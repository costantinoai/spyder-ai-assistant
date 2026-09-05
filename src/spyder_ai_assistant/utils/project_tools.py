"""Bounded, read-only project file and git access for the assistant.

One service backs three consumers so the boundary rules live in one place:

- the chat runtime-request protocol (the model asks for ``project.*`` or
  ``git.*`` tools mid-turn and receives an observation),
- the embedded MCP server (external agents call the same tools),
- one-click chat actions (for example "Review Changes").

Boundaries (GitHub issue #2 asked for project-wide access *with* limits):

- every path is resolved inside the project root (the active Spyder
  project, else the directory of the current file); ``..`` escapes and
  symlinks that leave the root are rejected,
- the same skip list as the project tree (``.git``, virtualenvs, caches,
  build output) applies to listing and search,
- files larger than ``MAX_FILE_BYTES`` or that look binary are not read,
  and returned text is capped at ``MAX_READ_CHARS``,
- git commands run with a timeout and their output is capped.

Nothing here writes to disk or to git.
"""

from __future__ import annotations

import fnmatch
import logging
import os
import re
import subprocess
from typing import Any, Callable, Optional

from spyder_ai_assistant.utils.context import SKIP_DIRS

logger = logging.getLogger(__name__)

# Tool names understood by the chat protocol, the MCP server and the plugin
# dispatcher. Kept as a tuple so the system prompt can list them verbatim.
PROJECT_TOOL_NAMES = (
    "project.list_files",
    "project.read_file",
    "project.search",
    "git.status",
    "git.diff",
    "git.log",
)

MAX_READ_CHARS = 20_000
MAX_FILE_BYTES = 2_000_000
MAX_LIST_ENTRIES = 300
MAX_SEARCH_RESULTS = 50
MAX_SEARCH_FILES = 2_000
MAX_GIT_CHARS = 20_000
MAX_GIT_LOG_COUNT = 50
GIT_TIMEOUT_S = 8

_BINARY_SNIFF_BYTES = 4096


def dispatch_chat_tool_request(request, runtime_executor, project_executor):
    """Route one chat tool request to the runtime or the project executor.

    The chat turn controller only knows "an executor"; this keeps the two
    tool families separate without teaching the controller about either.
    """
    tool = str((request or {}).get("tool", "") or "")
    if tool.startswith(("project.", "git.")):
        return project_executor(request)
    return runtime_executor(request)


class ProjectToolsService:
    """Read-only project file and git tools scoped to the project root."""

    def __init__(
        self,
        root_resolver: Callable[[], str],
        enabled_resolver: Optional[Callable[[], bool]] = None,
    ):
        # Returns the absolute root directory, or "" when nothing is open.
        self._root_resolver = root_resolver
        # The user can switch project access off in Assistant Settings.
        self._enabled_resolver = enabled_resolver or (lambda: True)

    # --- public entry points --------------------------------------------

    def resolve_root(self) -> str:
        """Return the absolute project root or "" when none is available."""
        try:
            root = str(self._root_resolver() or "").strip()
        except Exception as error:
            logger.debug("Project root resolver failed: %s", error)
            return ""
        if not root or not os.path.isdir(root):
            return ""
        return os.path.realpath(root)

    def execute_request(self, request) -> dict[str, Any]:
        """Execute one ``{"tool": ..., "args": {...}}`` request.

        Returns the same result shape as the runtime bridge so the chat
        observation formatter and MCP normaliser treat both alike.
        """
        tool = str((request or {}).get("tool", "") or "")
        args = (request or {}).get("args") or {}
        if not isinstance(args, dict):
            args = {}
        logger.info("Executing project tool request: tool=%s args=%s", tool, args)

        if not self._enabled_resolver():
            return self._error(
                tool,
                "Project file access is disabled in Assistant Settings "
                "(Behavior tab).",
            )
        root = self.resolve_root()
        if not root:
            return self._error(
                tool,
                "No project or file is open in Spyder, so there is no "
                "project root to read from.",
            )

        handlers = {
            "project.list_files": self._list_files,
            "project.read_file": self._read_file,
            "project.search": self._search,
            "git.status": self._git_status,
            "git.diff": self._git_diff,
            "git.log": self._git_log,
        }
        handler = handlers.get(tool)
        if handler is None:
            return self._error(tool, f"Unsupported project tool: {tool!r}")
        try:
            payload, note = handler(root, args)
        except ValueError as error:
            return self._error(tool, str(error), root=root)
        except Exception as error:  # pragma: no cover - defensive
            logger.exception("Project tool %s failed", tool)
            return self._error(tool, f"{tool} failed: {error}", root=root)
        result = {
            "ok": True,
            "tool": tool,
            "source": "project",
            "root": root,
            "payload": payload,
            "query_note": note,
            "error": "",
        }
        logger.info("Project tool %s completed (%s)", tool, note or "ok")
        return result

    # --- helpers shared by the handlers ----------------------------------

    @staticmethod
    def _error(tool, message, root=""):
        return {
            "ok": False,
            "tool": tool,
            "source": "project",
            "root": root,
            "payload": {},
            "query_note": "",
            "error": message,
        }

    @staticmethod
    def _resolve_path(root, relative):
        """Return the absolute path for ``relative`` inside ``root``.

        Raises ``ValueError`` for anything that escapes the root, including
        absolute paths elsewhere, ``..`` segments and symlinks pointing out.
        """
        candidate = str(relative or "").strip()
        joined = candidate if os.path.isabs(candidate) else os.path.join(root, candidate)
        resolved = os.path.realpath(joined)
        if resolved != root and not resolved.startswith(root + os.sep):
            raise ValueError(
                f"Path {candidate!r} is outside the project root; only files "
                "under the project can be accessed."
            )
        return resolved

    @staticmethod
    def _is_skipped_dir(name):
        return name in SKIP_DIRS or name.startswith(".")

    @staticmethod
    def _looks_binary(path):
        with open(path, "rb") as handle:
            chunk = handle.read(_BINARY_SNIFF_BYTES)
        return b"\x00" in chunk

    def _walk(self, root, start):
        """Yield (relative_path, absolute_path) for files under ``start``."""
        for dirpath, dirnames, filenames in os.walk(start):
            dirnames[:] = sorted(
                name for name in dirnames if not self._is_skipped_dir(name)
            )
            for filename in sorted(filenames):
                absolute = os.path.join(dirpath, filename)
                yield os.path.relpath(absolute, root), absolute

    # --- handlers ----------------------------------------------------------

    def _list_files(self, root, args):
        """List files below an optional subdirectory (bounded)."""
        subdir = self._resolve_path(root, args.get("subdir", ""))
        if not os.path.isdir(subdir):
            raise ValueError(f"Not a directory: {os.path.relpath(subdir, root)}")
        limit = _bounded_int(args.get("max_entries"), MAX_LIST_ENTRIES, MAX_LIST_ENTRIES)
        pattern = str(args.get("glob", "") or "").strip()
        files = []
        truncated = False
        for relative, _absolute in self._walk(root, subdir):
            if pattern and not fnmatch.fnmatch(relative, pattern):
                continue
            if len(files) >= limit:
                truncated = True
                break
            files.append(relative)
        note = f"{len(files)} file(s)" + (" (truncated)" if truncated else "")
        return {"files": files, "truncated": truncated}, note

    def _read_file(self, root, args):
        """Return the text of one project file (optionally a line range)."""
        relative = str(args.get("path", "") or "").strip()
        if not relative:
            raise ValueError("project.read_file needs a 'path' argument.")
        absolute = self._resolve_path(root, relative)
        if not os.path.isfile(absolute):
            raise ValueError(f"File not found in project: {relative}")
        size = os.path.getsize(absolute)
        if size > MAX_FILE_BYTES:
            raise ValueError(
                f"{relative} is {size} bytes; files over {MAX_FILE_BYTES} bytes "
                "are not read."
            )
        if self._looks_binary(absolute):
            raise ValueError(f"{relative} looks like a binary file.")
        with open(absolute, "r", encoding="utf-8", errors="replace") as handle:
            lines = handle.read().splitlines()
        total_lines = len(lines)
        start = _bounded_int(args.get("start_line"), 1, max(total_lines, 1))
        end = _bounded_int(args.get("end_line"), total_lines, max(total_lines, 1))
        if end < start:
            raise ValueError("end_line must not be smaller than start_line.")
        selected = lines[start - 1:end]
        text = "\n".join(selected)
        max_chars = _bounded_int(args.get("max_chars"), MAX_READ_CHARS, MAX_READ_CHARS)
        truncated = len(text) > max_chars
        if truncated:
            text = text[:max_chars]
        note = f"lines {start}-{min(end, total_lines)} of {total_lines}"
        if truncated:
            note += f", truncated to {max_chars} chars"
        return {
            "path": os.path.relpath(absolute, root),
            "start_line": start,
            "end_line": min(end, total_lines),
            "total_lines": total_lines,
            "truncated": truncated,
            "content": text,
        }, note

    def _search(self, root, args):
        """Search project text files for a pattern (regex, case-insensitive)."""
        pattern = str(args.get("pattern", "") or "")
        if not pattern.strip():
            raise ValueError("project.search needs a 'pattern' argument.")
        try:
            regex = re.compile(pattern, re.IGNORECASE)
        except re.error as error:
            raise ValueError(f"Invalid search pattern: {error}") from error
        file_glob = str(args.get("glob", "") or "").strip()
        limit = _bounded_int(args.get("max_results"), MAX_SEARCH_RESULTS, MAX_SEARCH_RESULTS)
        matches = []
        scanned = 0
        truncated = False
        for relative, absolute in self._walk(root, root):
            if file_glob and not fnmatch.fnmatch(relative, file_glob):
                continue
            scanned += 1
            if scanned > MAX_SEARCH_FILES:
                truncated = True
                break
            try:
                if os.path.getsize(absolute) > MAX_FILE_BYTES or self._looks_binary(absolute):
                    continue
                with open(absolute, "r", encoding="utf-8", errors="replace") as handle:
                    for number, line in enumerate(handle, start=1):
                        if regex.search(line):
                            matches.append({
                                "path": relative,
                                "line": number,
                                "text": line.rstrip("\n")[:300],
                            })
                            if len(matches) >= limit:
                                truncated = True
                                break
            except OSError:
                continue
            if len(matches) >= limit:
                break
        note = f"{len(matches)} match(es) in {scanned} file(s)"
        if truncated:
            note += " (truncated)"
        return {"matches": matches, "truncated": truncated}, note

    def _git_status(self, root, args):
        del args
        output = self._run_git(root, ["status", "--short", "--branch"])
        return {"status": output}, "git status"

    def _git_diff(self, root, args):
        command = ["diff", "--no-color"]
        if bool(args.get("staged", False)):
            command.append("--cached")
        path = str(args.get("path", "") or "").strip()
        if path:
            command.extend(["--", os.path.relpath(self._resolve_path(root, path), root)])
        max_chars = _bounded_int(args.get("max_chars"), MAX_GIT_CHARS, MAX_GIT_CHARS)
        output = self._run_git(root, command)
        truncated = len(output) > max_chars
        if truncated:
            output = output[:max_chars]
        note = "git diff" + (" (staged)" if "--cached" in command else "")
        if truncated:
            note += f", truncated to {max_chars} chars"
        return {"diff": output or "(no changes)", "truncated": truncated}, note

    def _git_log(self, root, args):
        count = _bounded_int(args.get("max_count"), 10, MAX_GIT_LOG_COUNT)
        command = ["log", "--no-color", f"--max-count={count}", "--date=short",
                   "--format=%h %ad %an%n    %s"]
        path = str(args.get("path", "") or "").strip()
        if path:
            command.extend(["--", os.path.relpath(self._resolve_path(root, path), root)])
        output = self._run_git(root, command)
        return {"log": output or "(no commits)"}, f"git log, last {count}"

    @staticmethod
    def _run_git(root, arguments):
        """Run one read-only git command in ``root`` and return its stdout."""
        try:
            completed = subprocess.run(
                ["git", "-C", root, *arguments],
                capture_output=True,
                text=True,
                timeout=GIT_TIMEOUT_S,
                check=False,
            )
        except FileNotFoundError as error:
            raise ValueError("git is not installed or not on PATH.") from error
        except subprocess.TimeoutExpired as error:
            raise ValueError(f"git timed out after {GIT_TIMEOUT_S}s.") from error
        if completed.returncode != 0:
            message = (completed.stderr or completed.stdout or "").strip()
            if "not a git repository" in message.lower():
                raise ValueError("The project root is not a git repository.")
            raise ValueError(f"git failed: {message or completed.returncode}")
        return completed.stdout.rstrip("\n")


def _bounded_int(value, default, maximum):
    """Coerce ``value`` to an int in ``[1, maximum]`` with a default."""
    try:
        number = int(value)
    except (TypeError, ValueError):
        number = int(default)
    return max(1, min(int(maximum), number))
