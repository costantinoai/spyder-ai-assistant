"""Protocol helpers for on-demand Spyder runtime inspection.

The chat model does not receive live kernel state by default. Instead, the
system prompt advertises a small read-only protocol that the frontend can
intercept and satisfy against the active Spyder IPython console.
"""

from __future__ import annotations

import json
import re

from spyder_ai_assistant.utils.runtime_context import (
    format_runtime_shell,
    format_runtime_variable,
)

RUNTIME_REQUEST_TAG = "spyder-runtime-request"
MAX_RUNTIME_TOOL_CALLS_PER_TURN = 4
RUNTIME_TOOL_NAMES = (
    "runtime.status",
    "runtime.list_shells",
    "runtime.get_latest_error",
    "runtime.get_console_tail",
    "runtime.list_variables",
    "runtime.inspect_variable",
    "runtime.inspect_variables",
)

_REQUEST_RE = re.compile(
    rf"^\s*<{RUNTIME_REQUEST_TAG}>\s*(\{{.*\}})\s*</{RUNTIME_REQUEST_TAG}>\s*$",
    re.DOTALL,
)
_TAG_RE = re.compile(rf"</?{RUNTIME_REQUEST_TAG}>")


def build_runtime_bridge_instructions():
    """Return the internal system-prompt instructions for runtime access."""
    tool_names = "\n".join(f"- {tool_name}" for tool_name in RUNTIME_TOOL_NAMES)
    example = (
        f"<{RUNTIME_REQUEST_TAG}>\n"
        '{"tool":"runtime.inspect_variable","args":{"name":"df"}}\n'
        f"</{RUNTIME_REQUEST_TAG}>"
    )
    return (
        "Live Spyder runtime access is available when needed.\n"
        "Do not assume current variable values, console output, or kernel "
        "state unless you inspect them.\n"
        "If the user asks about the current kernel, current variables, "
        "current console output, the latest traceback/error, what just ran, "
        "or asks you to debug live state, you MUST inspect runtime data "
        "before answering.\n"
        "Use runtime inspection only when the user's request depends on the "
        "current IPython session, recent errors, or live variable state.\n"
        "If you need runtime data, reply with ONLY one request block in this "
        "exact format:\n"
        f"{example}\n"
        "Allowed read-only tools:\n"
        f"{tool_names}\n"
        "Tool selection guidance:\n"
        "- Use `runtime.inspect_variable` for a named variable.\n"
        "- Use `runtime.list_variables` when you need to discover what exists.\n"
        "- Use `runtime.list_shells` when multiple Spyder consoles may matter.\n"
        "- Use `runtime.get_latest_error` for the current traceback/error.\n"
        "- Use `runtime.get_console_tail` for recent console output.\n"
        "- Use `runtime.status` when availability or freshness is the question.\n"
        "Rules:\n"
        "1. A runtime request must be the entire assistant message.\n"
        "2. Request only one tool at a time.\n"
        "3. Prefer the smallest tool that answers the question.\n"
        "4. After a runtime observation arrives, either answer normally or "
        "request one more tool if necessary.\n"
        "5. Do not ask for runtime data when file/project context is enough.\n"
        "6. Never invent runtime results.\n"
        "7. Do not answer questions about current runtime state from memory, "
        "guesswork, or file context alone."
    )


def parse_runtime_request(text):
    """Parse one runtime request block from a model response.

    Returns ``None`` when the response is a normal assistant answer.
    Returns a dict with ``valid`` plus either ``tool``/``args`` or ``error``
    when the response appears to be a runtime request block.
    """
    stripped = (text or "").strip()
    if not stripped:
        return None

    if not _TAG_RE.search(stripped):
        return None

    match = _REQUEST_RE.fullmatch(stripped)
    if not match:
        return {
            "valid": False,
            "error": (
                "Runtime requests must be the only content in the assistant "
                "message and must use valid JSON inside the request block."
            ),
            "raw_text": stripped,
        }

    try:
        payload = json.loads(match.group(1))
    except json.JSONDecodeError as error:
        return {
            "valid": False,
            "error": f"Invalid runtime request JSON: {error}",
            "raw_text": stripped,
        }

    tool = payload.get("tool")
    args = payload.get("args", {})

    if tool not in RUNTIME_TOOL_NAMES:
        return {
            "valid": False,
            "error": (
                f"Unsupported runtime tool: {tool!r}. "
                f"Allowed tools: {', '.join(RUNTIME_TOOL_NAMES)}"
            ),
            "raw_text": stripped,
        }

    if args is None:
        args = {}
    if not isinstance(args, dict):
        return {
            "valid": False,
            "error": "Runtime request args must be a JSON object.",
            "raw_text": stripped,
        }

    return {
        "valid": True,
        "tool": tool,
        "args": args,
        "raw_text": stripped,
    }


def format_runtime_observation(request, result):
    """Format one runtime-tool result as a hidden follow-up user message."""
    tool = result.get("tool") or request.get("tool", "runtime.unknown")
    lines = [
        "[Spyder Runtime Observation]",
        f"tool: {tool}",
        f"ok: {str(bool(result.get('ok', False))).lower()}",
    ]

    source = result.get("source")
    if source:
        lines.append(f"source: {source}")

    shell_status = result.get("shell_status")
    if shell_status:
        lines.append(f"shell status: {shell_status}")

    shell_label = result.get("shell_label")
    if shell_label:
        lines.append(f"shell: {shell_label}")

    shell_id = result.get("shell_id")
    if shell_id:
        lines.append(f"shell id: {shell_id}")

    active_shell_label = result.get("active_shell_label")
    if active_shell_label:
        lines.append(f"active shell: {active_shell_label}")

    target_shell_label = result.get("target_shell_label")
    if target_shell_label:
        lines.append(f"target shell: {target_shell_label}")

    shell_detail = result.get("shell_detail")
    if shell_detail:
        lines.append(f"shell detail: {shell_detail}")

    working_directory = result.get("working_directory")
    if working_directory:
        lines.append(f"cwd: {working_directory}")

    last_refreshed = result.get("last_refreshed_at")
    if last_refreshed:
        lines.append(f"last refreshed: {last_refreshed}")

    query_note = result.get("query_note")
    if query_note:
        lines.append(f"note: {query_note}")

    error = result.get("error")
    if error:
        lines.append(f"error: {error}")

    payload = result.get("payload") or {}
    payload_lines = _format_payload(tool, payload)
    if payload_lines:
        lines.append("[data]")
        lines.extend(payload_lines)
        lines.append("[end data]")

    lines.append(
        "Continue helping the user. If more live runtime data is required, "
        "request one more runtime tool. Otherwise answer normally."
    )
    return "\n".join(lines)


def _format_payload(tool, payload):
    if tool == "runtime.status":
        return _format_simple_mapping(payload, ("stale",))

    if tool == "runtime.list_shells":
        return _format_shells_payload(payload)

    if tool == "runtime.get_latest_error":
        latest_error = payload.get("latest_error", "")
        summary = payload.get("summary") or {}
        lines = []
        exception_type = summary.get("exception_type", "")
        exception_message = summary.get("exception_message", "")
        if exception_type:
            if exception_message:
                lines.append(f"exception: {exception_type}: {exception_message}")
            else:
                lines.append(f"exception: {exception_type}")
        for frame in summary.get("frames", [])[:3]:
            lines.append(
                "frame: "
                f"{frame.get('file', '?')}:{frame.get('line', '?')} "
                f"in {frame.get('function', '?')}"
            )
            if frame.get("code"):
                lines.append(f"code: {frame['code']}")
        if not latest_error:
            lines.append("No latest error is available.")
            return lines
        lines.append(latest_error)
        return lines

    if tool == "runtime.get_console_tail":
        console_output = payload.get("console_output", "")
        if not console_output:
            return ["No recent console output is available."]
        return [console_output]

    if tool == "runtime.list_variables":
        return _format_variables_payload(payload)

    if tool in {"runtime.inspect_variable", "runtime.inspect_variables"}:
        return _format_inspect_payload(payload)

    return _format_simple_mapping(payload, ())


def _format_variables_payload(payload):
    variables = payload.get("variables", [])
    if not variables:
        return ["No variables are currently available."]

    lines = [format_runtime_variable(variable) for variable in variables]
    total_count = payload.get("count")
    if total_count not in ("", None):
        lines.insert(0, f"count: {total_count}")
    return lines


def _format_shells_payload(payload):
    shells = payload.get("shells", [])
    if not shells:
        return ["No Spyder IPython consoles are currently tracked."]

    lines = [format_runtime_shell(shell) for shell in shells]
    total_count = payload.get("count")
    if total_count not in ("", None):
        lines.insert(0, f"count: {total_count}")
    return lines


def _format_inspect_payload(payload):
    lines = []
    variables = payload.get("variables", [])
    if variables:
        lines.extend(format_runtime_variable(variable) for variable in variables)

    missing = payload.get("missing", [])
    if missing:
        lines.append(f"missing: {', '.join(missing)}")

    if not lines:
        return ["No matching variables were found."]
    return lines


def _format_simple_mapping(payload, boolean_keys):
    lines = []
    for key, value in payload.items():
        if value in ("", None, []):
            continue
        if key in boolean_keys:
            value = str(bool(value)).lower()
        lines.append(f"{key}: {value}")
    return lines
