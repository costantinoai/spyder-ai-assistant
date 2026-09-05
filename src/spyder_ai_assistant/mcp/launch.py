"""Helpers for launching MCP-aware external CLIs from Spyder."""

from __future__ import annotations

import json
import os
import stat
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path

from qtpy.QtCore import QStandardPaths

from spyder.utils.programs import run_python_script_in_terminal

from spyder_ai_assistant.mcp.settings import (
    DEFAULT_MCP_SERVER_NAME,
    build_claude_mcp_config,
    build_codex_mcp_override,
    build_opencode_mcp_payload,
)

MCP_CLIENT_CLAUDE = "claude"
MCP_CLIENT_CODEX = "codex"
MCP_CLIENT_OPENCODE = "opencode"
SUPPORTED_MCP_CLIENTS = (
    MCP_CLIENT_CLAUDE,
    MCP_CLIENT_CODEX,
    MCP_CLIENT_OPENCODE,
)

_CLIENT_LABELS = {
    MCP_CLIENT_CLAUDE: "Claude Code",
    MCP_CLIENT_CODEX: "Codex",
    MCP_CLIENT_OPENCODE: "OpenCode",
}
_CLIENT_EXECUTABLES = {
    MCP_CLIENT_CLAUDE: "claude",
    MCP_CLIENT_CODEX: "codex",
    MCP_CLIENT_OPENCODE: "opencode",
}
_LINUX_TERMINAL_CANDIDATES = (
    ("x-terminal-emulator", ["-e"]),
    ("gnome-terminal", ["--"]),
    ("konsole", ["-e"]),
    ("xfce4-terminal", ["-x"]),
    ("xterm", ["-e"]),
)


@dataclass(frozen=True)
class MCPClientLaunchSpec:
    """Concrete launch plan for one MCP-aware external CLI."""

    client_id: str
    display_name: str
    executable_path: str
    working_dir: str
    endpoint_url: str
    launcher_path: str
    command_preview: str
    config_path: str = ""


def get_mcp_client_label(client_id):
    """Return the human-readable label for one supported client."""
    return _CLIENT_LABELS.get(str(client_id or "").strip(), "MCP client")


def find_mcp_client_executable(client_id):
    """Return the resolved executable path for one supported client."""
    executable_name = _CLIENT_EXECUTABLES.get(str(client_id or "").strip(), "")
    if not executable_name:
        return ""
    return QStandardPaths.findExecutable(executable_name)


def _normalize_working_dir(working_dir):
    """Return one existing working directory for launched clients."""
    candidate = str(working_dir or "").strip()
    if candidate:
        candidate = os.path.abspath(candidate)
        if os.path.isdir(candidate):
            return candidate
    return os.getcwd()


def _write_json(path, payload):
    """Write one JSON payload to disk."""
    Path(path).write_text(
        json.dumps(payload, indent=2) + "\n",
        encoding="utf-8",
    )


def _write_launcher_script(path, *, args, env_updates, working_dir):
    """Write the small Python shim executed inside the external terminal."""
    script = f"""#!/usr/bin/env python3
import os
import subprocess
import sys

ARGS = {json.dumps(list(args))}
ENV_UPDATES = {json.dumps(dict(env_updates or {}))}
WORKDIR = {json.dumps(str(working_dir))}


def main():
    env = os.environ.copy()
    env.update(ENV_UPDATES)
    os.chdir(WORKDIR)
    try:
        return subprocess.call(ARGS, cwd=WORKDIR, env=env)
    except FileNotFoundError as error:
        print(f"Executable not found: {{error.filename or ARGS[0]}}", file=sys.stderr)
        print("Verify the CLI is installed and available to Spyder's environment.", file=sys.stderr)
        try:
            input("Press Enter to close...")
        except EOFError:
            pass
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
"""
    launcher_path = Path(path)
    launcher_path.write_text(script, encoding="utf-8")
    launcher_path.chmod(
        launcher_path.stat().st_mode
        | stat.S_IRUSR
        | stat.S_IWUSR
        | stat.S_IXUSR
    )


def prepare_mcp_client_launch(
    client_id,
    endpoint_url,
    *,
    working_dir=None,
    server_name=DEFAULT_MCP_SERVER_NAME,
):
    """Generate one launchable external-client session plan."""
    normalized_client_id = str(client_id or "").strip()
    if normalized_client_id not in SUPPORTED_MCP_CLIENTS:
        raise ValueError(f"Unsupported MCP client: {client_id}")

    executable_path = find_mcp_client_executable(normalized_client_id)
    if not executable_path:
        raise RuntimeError(
            f"{get_mcp_client_label(normalized_client_id)} is not installed "
            "or is not visible to Spyder's environment."
        )

    resolved_working_dir = _normalize_working_dir(working_dir)
    launch_dir = Path(
        tempfile.mkdtemp(prefix=f"spyder-ai-mcp-{normalized_client_id}-")
    )
    launcher_path = launch_dir / "launch_client.py"
    config_path = ""
    env_updates = {}

    if normalized_client_id == MCP_CLIENT_CLAUDE:
        config_path = str(launch_dir / "claude-mcp.json")
        _write_json(
            config_path,
            build_claude_mcp_config(endpoint_url, name=server_name),
        )
        args = [
            executable_path,
            "--mcp-config",
            config_path,
            "--strict-mcp-config",
        ]
        command_preview = (
            f"{os.path.basename(executable_path)} --mcp-config {config_path} "
            "--strict-mcp-config"
        )
    elif normalized_client_id == MCP_CLIENT_CODEX:
        override = build_codex_mcp_override(endpoint_url, name=server_name)
        args = [executable_path, "-c", override]
        command_preview = (
            f"{os.path.basename(executable_path)} -c {override}"
        )
    else:
        config_path = str(launch_dir / "opencode.json")
        _write_json(
            config_path,
            build_opencode_mcp_payload(endpoint_url, name=server_name),
        )
        env_updates["OPENCODE_CONFIG"] = config_path
        args = [executable_path, resolved_working_dir]
        command_preview = (
            f"OPENCODE_CONFIG={config_path} "
            f"{os.path.basename(executable_path)} {resolved_working_dir}"
        )

    _write_launcher_script(
        launcher_path,
        args=args,
        env_updates=env_updates,
        working_dir=resolved_working_dir,
    )
    return MCPClientLaunchSpec(
        client_id=normalized_client_id,
        display_name=get_mcp_client_label(normalized_client_id),
        executable_path=executable_path,
        working_dir=resolved_working_dir,
        endpoint_url=str(endpoint_url or ""),
        launcher_path=str(launcher_path),
        command_preview=command_preview,
        config_path=config_path,
    )


def _find_linux_terminal():
    """Return one Linux terminal command plus its execute arguments."""
    for executable, execute_args in _LINUX_TERMINAL_CANDIDATES:
        resolved = QStandardPaths.findExecutable(executable)
        if resolved:
            return resolved, list(execute_args)
    return "", []


def _open_python_launcher_in_terminal(spec):
    """Open the generated Python launcher in an external system terminal."""
    if os.name == "nt":
        cmd = (
            f'start cmd.exe /K "{sys.executable}" "{spec.launcher_path}"'
        )
        subprocess.Popen(cmd, cwd=spec.working_dir, shell=True)
        return

    if sys.platform.startswith("linux"):
        terminal_path, execute_args = _find_linux_terminal()
        if not terminal_path:
            raise RuntimeError(
                "No supported external terminal was found. Install "
                "gnome-terminal, xterm, or another compatible terminal."
            )
        subprocess.Popen(
            [terminal_path, *execute_args, sys.executable, spec.launcher_path],
            cwd=spec.working_dir,
        )
        return

    run_python_script_in_terminal(
        spec.launcher_path,
        spec.working_dir,
        args="",
        interact=False,
        debug=False,
        python_args="",
        executable=sys.executable,
    )


def launch_mcp_client_in_terminal(
    client_id,
    endpoint_url,
    *,
    working_dir=None,
    server_name=DEFAULT_MCP_SERVER_NAME,
):
    """Prepare and launch one MCP-aware external CLI in a new terminal."""
    spec = prepare_mcp_client_launch(
        client_id,
        endpoint_url,
        working_dir=working_dir,
        server_name=server_name,
    )
    _open_python_launcher_in_terminal(spec)
    return spec
