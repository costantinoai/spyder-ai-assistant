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
import json
import logging
import os
import re
import subprocess
import sys
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Optional

from spyder_ai_assistant.utils.coerce import bounded_int
from spyder_ai_assistant.utils.bounded_process import run_bounded_command
from spyder_ai_assistant.utils.project_files import (
    SKIP_DIRS, MAX_WALK_ENTRIES, WalkBudget, resolve_project_path,
    walk_project_files,
)
from spyder_ai_assistant.utils.tool_protocol import (
    PROJECT_TOOL_NAMES,  # noqa: F401 -- re-exported for existing importers
    is_project_tool,
    TOOL_GIT_DIFF,
    TOOL_GIT_LOG,
    TOOL_GIT_STATUS,
    TOOL_PROJECT_LIST_FILES,
    TOOL_PROJECT_READ_FILE,
    TOOL_PROJECT_SEARCH,
)

logger = logging.getLogger(__name__)

# Tool names understood by the chat protocol, the MCP server and the plugin
# dispatcher. Kept as a tuple so the system prompt can list them verbatim.
# PROJECT_TOOL_NAMES is imported above from utils.tool_protocol, which owns
# the spellings, and re-exported here for the existing importers.

MAX_READ_CHARS = 20_000
MAX_FILE_BYTES = 2_000_000
MAX_LIST_ENTRIES = 300
MAX_SEARCH_RESULTS = 50
MAX_SEARCH_FILES = 2_000
MAX_SEARCH_BYTES = 256_000_000
MAX_SEARCH_PATTERN_CHARS = 2_000
SEARCH_TIMEOUT_S = 3.0
MAX_GIT_CHARS = 20_000
MAX_GIT_LOG_COUNT = 50
GIT_TIMEOUT_S = 8

_BINARY_SNIFF_BYTES = 4096


def dispatch_chat_tool_request(request, runtime_executor, project_executor):
    """Route one chat tool request to the runtime or the project executor.

    The chat turn controller only knows "an executor"; this keeps the two
    tool families separate without teaching the controller about either.
    """
    if is_project_tool((request or {}).get("tool", "")):
        return project_executor(request)
    return runtime_executor(request)


@dataclass
class PreparedToolRequest:
    """One project tool request after its GUI-thread preparation.

    Splitting a request in two is what lets the work run off the GUI
    thread: resolving the project root reads Spyder's active project and
    editor, while the file and git access that follows is plain I/O.

    ``result`` is already filled in when the request cannot run at all
    (project access disabled, no project root, unknown tool). Otherwise
    ``root`` holds the resolved project root and the request is ready to
    run on any thread.
    """

    tool: str
    args: dict = field(default_factory=dict)
    root: str = ""
    result: Optional[dict] = None


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

    def _handlers(self):
        """Return the tool name to handler mapping.

        One table, consulted by ``prepare_request`` to reject an unknown
        tool before any thread is involved and by ``run_prepared`` to do
        the work.
        """
        return {
            TOOL_PROJECT_LIST_FILES: self._list_files,
            TOOL_PROJECT_READ_FILE: self._read_file,
            TOOL_PROJECT_SEARCH: self._search,
            TOOL_GIT_STATUS: self._git_status,
            TOOL_GIT_DIFF: self._git_diff,
            TOOL_GIT_LOG: self._git_log,
        }

    def prepare_request(self, request) -> PreparedToolRequest:
        """Resolve one request against Spyder state, without running it.

        This half reads the active project and the current editor, so it
        must run on the GUI thread. The work itself does not, which is why
        it lives in ``run_prepared``.
        """
        tool = str((request or {}).get("tool", "") or "")
        args = (request or {}).get("args") or {}
        if not isinstance(args, dict):
            args = {}
        # Copied so the worker thread reads a snapshot the caller cannot
        # mutate underneath it.
        args = dict(args)

        if not self._enabled_resolver():
            return PreparedToolRequest(tool, args, result=self.error_result(
                tool,
                "Project file access is disabled in Assistant Settings "
                "(Advanced tab).",
            ))
        root = self.resolve_root()
        if not root:
            return PreparedToolRequest(tool, args, result=self.error_result(
                tool,
                "No project or file is open in Spyder, so there is no "
                "project root to read from.",
            ))
        if tool not in self._handlers():
            return PreparedToolRequest(tool, args, root=root, result=self.error_result(
                tool, f"Unsupported project tool: {tool!r}", root=root,
            ))
        return PreparedToolRequest(tool, args, root=root)

    def run_prepared(self, prepared: PreparedToolRequest) -> dict[str, Any]:
        """Execute one prepared request. Safe to call off the GUI thread.

        Touches only the filesystem and git, never Qt or Spyder state, so
        the chat turn loop (via a worker) and the MCP server (on its own
        request thread) can both run this while the IDE stays responsive.

        Returns the same result shape as the runtime bridge so the chat
        observation formatter and MCP normaliser treat both alike.
        """
        if prepared.result is not None:
            return prepared.result

        tool = prepared.tool
        logger.info(
            "Executing project tool request: tool=%s args=%s",
            tool,
            prepared.args,
        )
        handler = self._handlers()[tool]
        try:
            payload, note = handler(prepared.root, prepared.args)
        except ValueError as error:
            return self.error_result(tool, str(error), root=prepared.root)
        except Exception as error:  # pragma: no cover - defensive
            logger.exception("Project tool %s failed", tool)
            return self.error_result(
                tool, f"{tool} failed: {error}", root=prepared.root,
            )
        logger.info("Project tool %s completed (%s)", tool, note or "ok")
        return {
            "ok": True,
            "tool": tool,
            "source": "project",
            "root": prepared.root,
            "payload": payload,
            "query_note": note,
            "error": "",
        }

    def execute_request(self, request) -> dict[str, Any]:
        """Execute one ``{"tool": ..., "args": {...}}`` request inline.

        The synchronous entry point, kept for callers that are already off
        the GUI thread or do not care (tests, the one-click chat actions).
        """
        return self.run_prepared(self.prepare_request(request))

    # --- helpers shared by the handlers ----------------------------------

    @staticmethod
    def error_result(tool, message, root=""):
        """Return the failure envelope for one project tool call.

        Public because a caller that runs the work on a worker thread has
        to build this itself when the worker raises.
        """
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
        return resolve_project_path(root, relative)

    @staticmethod
    def _is_skipped_dir(name):
        return name in SKIP_DIRS or name.startswith(".")

    @staticmethod
    def _looks_binary(path):
        with open(path, "rb") as handle:
            chunk = handle.read(_BINARY_SNIFF_BYTES)
        return b"\x00" in chunk

    def _walk(self, root, start, budget=None):
        """Yield (relative_path, absolute_path) for files under ``start``."""
        yield from walk_project_files(root, start, budget or WalkBudget())

    # --- handlers ----------------------------------------------------------

    def _list_files(self, root, args):
        """List files below an optional subdirectory (bounded)."""
        subdir = self._resolve_path(root, args.get("subdir", ""))
        if not os.path.isdir(subdir):
            raise ValueError(f"Not a directory: {os.path.relpath(subdir, root)}")
        limit = bounded_int(args.get("max_entries"), MAX_LIST_ENTRIES, 1, MAX_LIST_ENTRIES)
        pattern = str(args.get("glob", "") or "").strip()
        files = []
        truncated = False
        budget = WalkBudget(max_entries=MAX_WALK_ENTRIES)
        for relative, _absolute in self._walk(root, subdir, budget):
            if pattern and not fnmatch.fnmatch(relative, pattern):
                continue
            if len(files) >= limit:
                truncated = True
                break
            files.append(relative)
        truncated = truncated or bool(budget.reason)
        note = f"{len(files)} file(s)" + (" (truncated)" if truncated else "")
        return {"files": files, "truncated": truncated}, note

    def _read_file(self, root, args):
        """Return the text of one project file (optionally a line range)."""
        relative = str(args.get("path", "") or "").strip()
        if not relative:
            raise ValueError(f"{TOOL_PROJECT_READ_FILE} needs a 'path' argument.")
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
        with open(absolute, "rb") as handle:
            data = handle.read(MAX_FILE_BYTES + 1)
        if len(data) > MAX_FILE_BYTES:
            raise ValueError(f"{relative} exceeds {MAX_FILE_BYTES} bytes; it is not read.")
        lines = data.decode("utf-8", errors="replace").splitlines()
        total_lines = len(lines)
        start = bounded_int(args.get("start_line"), 1, 1, max(total_lines, 1))
        end = bounded_int(args.get("end_line"), total_lines, 1, max(total_lines, 1))
        if end < start:
            raise ValueError("end_line must not be smaller than start_line.")
        selected = lines[start - 1:end]
        text = "\n".join(selected)
        max_chars = bounded_int(args.get("max_chars"), MAX_READ_CHARS, 1, MAX_READ_CHARS)
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
        """Search in a process so regex and I/O can be stopped independently."""
        request = {
            "root": root, "args": args,
            "limits": {
                "max_files": MAX_SEARCH_FILES, "max_bytes": MAX_SEARCH_BYTES,
                "max_entries": MAX_WALK_ENTRIES, "timeout_s": SEARCH_TIMEOUT_S,
            },
        }
        timed_out = False
        environment = dict(os.environ)
        package_root = os.path.dirname(os.path.dirname(os.path.dirname(__file__)))
        environment["PYTHONPATH"] = os.pathsep.join(filter(None, (
            os.path.abspath(package_root), environment.get("PYTHONPATH", ""),
        )))
        try:
            completed = subprocess.run(
                [sys.executable, "-m", "spyder_ai_assistant.utils.project_search"],
                input=json.dumps(request), capture_output=True, text=True,
                encoding="utf-8", timeout=SEARCH_TIMEOUT_S, check=False,
                env=environment,
                creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
            )
            output = completed.stdout
            if completed.returncode:
                logger.debug("Project search process failed: %s", completed.stderr[-2000:])
                raise ValueError("The project search process failed.")
        except subprocess.TimeoutExpired as error:
            output = error.stdout or b""
            timed_out = True
        if isinstance(output, bytes):
            output = output.decode("utf-8", errors="replace")
        matches = []
        for line in output.splitlines():
            try:
                record = json.loads(line)
            except ValueError:
                continue  # A final record may have been interrupted by timeout.
            if "error" in record:
                raise ValueError(record["error"])
            if "match" in record:
                matches.append(record["match"])
            if "payload" in record:
                return record["payload"], record["note"]
        if not timed_out:
            raise ValueError("The project search process returned no result.")
        return {"matches": matches, "truncated": True}, (
            f"{len(matches)} match(es); search stopped after {SEARCH_TIMEOUT_S:g}s (truncated)"
        )

    def _search_files(self, root, args, *, on_match=None, max_files=MAX_SEARCH_FILES,
                      max_bytes=MAX_SEARCH_BYTES, max_entries=MAX_WALK_ENTRIES,
                      timeout_s=SEARCH_TIMEOUT_S):
        """Search project text files for a pattern (regex, case-insensitive)."""
        pattern = str(args.get("pattern", "") or "")
        if not pattern.strip():
            raise ValueError(f"{TOOL_PROJECT_SEARCH} needs a 'pattern' argument.")
        if len(pattern) > MAX_SEARCH_PATTERN_CHARS:
            raise ValueError(f"Search patterns are limited to {MAX_SEARCH_PATTERN_CHARS} characters.")
        try:
            regex = re.compile(pattern, re.IGNORECASE)
        except re.error as error:
            raise ValueError(f"Invalid search pattern: {error}") from error
        file_glob = str(args.get("glob", "") or "").strip()
        limit = bounded_int(args.get("max_results"), MAX_SEARCH_RESULTS, 1, MAX_SEARCH_RESULTS)
        matches = []
        scanned = 0
        used_bytes = 0
        truncated = False
        budget = WalkBudget(max_entries=max_entries, timeout_s=timeout_s)
        for relative, absolute in self._walk(root, root, budget):
            if file_glob and not fnmatch.fnmatch(relative, file_glob):
                continue
            if scanned >= max_files or used_bytes >= max_bytes:
                truncated = True
                break
            scanned += 1
            try:
                if os.path.getsize(absolute) > MAX_FILE_BYTES:
                    continue
                allowance = min(MAX_FILE_BYTES, max_bytes - used_bytes)
                with open(absolute, "rb") as handle:
                    data = handle.read(allowance + 1)
                used_bytes += min(len(data), allowance)
                if len(data) > allowance:
                    truncated = True
                    if allowance == MAX_FILE_BYTES:
                        continue  # The file grew past the file-size limit.
                data = data[:allowance]
                if b"\x00" in data[:_BINARY_SNIFF_BYTES]:
                    continue
                for number, line in enumerate(data.decode("utf-8", errors="replace").splitlines(), start=1):
                    if time.monotonic() >= budget.deadline:
                        budget.reason = "time budget"
                        break
                    if regex.search(line):
                        match = {
                            "path": relative,
                            "line": number,
                            "text": line[:300],
                        }
                        matches.append(match)
                        if on_match is not None:
                            on_match(match)
                        if len(matches) >= limit:
                            truncated = True
                            break
            except OSError:
                continue
            if len(matches) >= limit or budget.reason:
                break
        truncated = truncated or bool(budget.reason)
        note = f"{len(matches)} match(es) in {scanned} file(s)"
        if truncated:
            note += " (truncated)"
        return {"matches": matches, "truncated": truncated}, note

    def _git_status(self, root, args):
        del args
        output, truncated = self._run_git(root, ["status", "--short", "--branch"])
        return {"status": output, "truncated": truncated}, "git status" + (" (truncated)" if truncated else "")

    def _git_diff(self, root, args):
        command = ["diff", "--no-color", "--no-ext-diff", "--no-textconv"]
        if bool(args.get("staged", False)):
            command.append("--cached")
        path = str(args.get("path", "") or "").strip()
        if path:
            command.extend(["--", os.path.relpath(self._resolve_path(root, path), root)])
        max_chars = bounded_int(args.get("max_chars"), MAX_GIT_CHARS, 1, MAX_GIT_CHARS)
        output, truncated = self._run_git(root, command, max_chars=max_chars)
        note = "git diff" + (" (staged)" if "--cached" in command else "")
        if truncated:
            note += f", truncated to {max_chars} chars"
        return {"diff": output or "(no changes)", "truncated": truncated}, note

    def _git_log(self, root, args):
        count = bounded_int(args.get("max_count"), 10, 1, MAX_GIT_LOG_COUNT)
        command = ["log", "--no-color", f"--max-count={count}", "--date=short",
                   "--format=%h %ad %an%n    %s"]
        path = str(args.get("path", "") or "").strip()
        if path:
            command.extend(["--", os.path.relpath(self._resolve_path(root, path), root)])
        output, truncated = self._run_git(root, command)
        return {"log": output or "(no commits)", "truncated": truncated}, f"git log, last {count}" + (" (truncated)" if truncated else "")

    @staticmethod
    def _run_git(root, arguments, *, max_chars=MAX_GIT_CHARS):
        """Run one read-only git command in ``root`` and return its stdout."""
        try:
            environment = dict(os.environ, GIT_TERMINAL_PROMPT="0", GIT_OPTIONAL_LOCKS="0")
            code, data, byte_limited = run_bounded_command(
                ["git", "--no-pager", "-c", "core.fsmonitor=false", "-C", root, *arguments],
                max_bytes=(max_chars + 1) * 4, timeout=GIT_TIMEOUT_S,
                env=environment,
            )
        except FileNotFoundError as error:
            raise ValueError("git is not installed or not on PATH.") from error
        except subprocess.TimeoutExpired as error:
            raise ValueError(f"git timed out after {GIT_TIMEOUT_S}s.") from error
        output = data.decode("utf-8", errors="replace").rstrip("\n")
        truncated = byte_limited or len(output) > max_chars
        if code != 0 and not byte_limited:
            message = output.strip()
            if "not a git repository" in message.lower():
                raise ValueError("The project root is not a git repository.")
            raise ValueError(f"git failed: {message[:max_chars] or code}")
        return output[:max_chars], truncated
