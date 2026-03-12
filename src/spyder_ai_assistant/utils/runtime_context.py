"""Runtime-context collection and formatting helpers.

This module keeps live kernel context out of the chat send hot path.
It listens to Spyder console lifecycle signals, caches one runtime snapshot
per shellwidget, and exposes a lightweight read API for prompt enrichment.

Runtime snapshots include:
- shell availability and freshness state
- recent console output
- latest extracted traceback/error block
- structured variable summaries built from Spyder kernel state
"""

from __future__ import annotations

import copy
import logging
import re
from datetime import datetime

from qtpy.QtCore import QObject, Signal

from spyder.config.base import CHECK_ALL, EXCLUDED_NAMES

logger = logging.getLogger(__name__)


# --- Runtime-context budgets -------------------------------------------------

# Keep runtime context bounded independently from editor/project context so it
# can be inserted safely into the system prompt.
MAX_RUNTIME_CONTEXT_CHARS = 8_000
MAX_RUNTIME_ERROR_CHARS = 2_500
MAX_RUNTIME_CONSOLE_LINES = 60
MAX_RUNTIME_CONSOLE_CHARS = 3_000
MAX_RUNTIME_VARIABLES = 20
MAX_RUNTIME_VARIABLES_CHARS = 3_500
MAX_RUNTIME_PREVIEW_CHARS = 160
MAX_RUNTIME_REQUEST_VARIABLES = 12
MAX_RUNTIME_REQUEST_NAMES = 5
MAX_RUNTIME_REQUEST_TIMEOUT = 2
MAX_RUNTIME_SHELLS = 12


# --- Spyder namespace-view defaults -----------------------------------------

DEFAULT_NAMESPACE_VIEW_SETTINGS = {
    "check_all": CHECK_ALL,
    "excluded_names": list(EXCLUDED_NAMES),
    "exclude_private": True,
    "exclude_uppercase": False,
    "exclude_capitalized": False,
    "exclude_unsupported": False,
    "exclude_callables_and_modules": True,
    "minmax": False,
    "show_callable_attributes": True,
    "show_special_attributes": False,
    "filter_on": True,
}


TRACEBACK_START_RE = re.compile(r"^\s*Traceback \(most recent call last\):")
TRACEBACK_FILE_FRAME_RE = re.compile(
    r'^\s*File "(.+)", line (\d+), in (.+)$'
)
TRACEBACK_CELL_FRAME_RE = re.compile(
    r"^\s*Cell In\[(\d+)\], line (\d+)(?:, in (.+))?$"
)
PROMPT_LINE_RE = re.compile(r"^\s*(?:In \[\d+\]:|Out\[\d+\]:|>>>|\.\.\.:)")
ERROR_LINE_RE = re.compile(
    r"^\s*(?:[A-Za-z_][\w.]*(?:Error|Exception)|"
    r"KeyboardInterrupt|SystemExit|GeneratorExit|MemoryError)(?::|\b)"
)
ERROR_DIVIDER_RE = re.compile(r"^\s*-{5,}\s*$")
TRACEBACK_TITLE_RE = re.compile(
    r"^\s*[A-Za-z_][\w.]*\s+Traceback \(most recent call last\)"
)
ANSI_ESCAPE_RE = re.compile(r"\x1b\[[0-9;?]*[ -/]*[@-~]")

SCALAR_PYTHON_TYPES = {
    "int", "float", "complex", "bool", "str", "bytes", "NoneType",
}
PUBLIC_RUNTIME_KEYS = {
    "status",
    "status_detail",
    "shell_id",
    "shell_label",
    "working_directory",
    "last_refreshed_at",
    "console_output",
    "latest_error",
    "variables",
    "active_shell_id",
    "active_shell_label",
    "target_shell_id",
    "target_shell_label",
    "available_shells",
    "stale",
    "collection_error",
}


def build_namespace_view_settings(variable_explorer_plugin=None):
    """Build namespace-view settings with optional Variable Explorer overrides."""
    settings = copy.deepcopy(DEFAULT_NAMESPACE_VIEW_SETTINGS)
    if variable_explorer_plugin is None:
        return settings

    for option in DEFAULT_NAMESPACE_VIEW_SETTINGS:
        try:
            value = variable_explorer_plugin.get_conf(option)
        except Exception as error:
            logger.debug(
                "Failed to read Variable Explorer setting %s: %s",
                option,
                error,
            )
            continue

        if value is None:
            continue
        if option == "excluded_names":
            settings[option] = list(value)
        else:
            settings[option] = value

    return settings


def make_empty_runtime_context(status="unavailable", detail=""):
    """Create a new runtime-context dictionary with predictable keys."""
    return {
        "status": status,
        "status_detail": detail,
        "shell_id": "",
        "shell_label": "",
        "working_directory": "",
        "last_refreshed_at": "",
        "console_output": "",
        "latest_error": "",
        "variables": [],
        "active_shell_id": "",
        "active_shell_label": "",
        "target_shell_id": "",
        "target_shell_label": "",
        "available_shells": [],
        "stale": False,
        "collection_error": "",
    }


def clone_runtime_context(runtime_context):
    """Return a deep copy of a cached runtime context snapshot."""
    if not runtime_context:
        return make_empty_runtime_context()

    public_snapshot = {
        key: runtime_context.get(key)
        for key in PUBLIC_RUNTIME_KEYS
    }
    return copy.deepcopy(public_snapshot)


def build_runtime_context_blocks(runtime_context):
    """Build bounded prompt sections for cached runtime context."""
    if not runtime_context:
        return []

    sections = []
    remaining = MAX_RUNTIME_CONTEXT_CHARS

    for section in (
        _build_runtime_status_block(runtime_context),
        _build_runtime_error_block(runtime_context),
        _build_runtime_variables_block(runtime_context),
        _build_runtime_console_block(runtime_context),
    ):
        if not section:
            continue

        if len(section) <= remaining:
            sections.append(section)
            remaining -= len(section) + 2
            continue

        if remaining <= 80:
            sections.append("[Runtime Context]\n... (additional runtime context omitted)")
            break

        clipped = _clip_runtime_section(section, remaining)
        sections.append(clipped)
        break

    return sections


class RuntimeContextService(QObject):
    """Cache live runtime snapshots for Spyder shellwidgets."""

    # Emitted when the current shell's public runtime snapshot changes.
    # Carries a copy of the public runtime context so UI layers can update
    # status indicators without reaching into internal snapshot storage.
    sig_current_context_changed = Signal(dict)
    # Emitted when the available shell targets or the selected target change.
    sig_shell_targets_changed = Signal(list, str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._ipython_console_plugin = None
        self._variable_explorer_plugin = None
        self._current_shell_id = None
        self._selected_shell_id = ""
        self._tracked_shell_ids = set()
        self._snapshots = {}

    # --- Public API ---------------------------------------------------------

    def bind_ipython_console(self, ipython_console_plugin):
        """Bind to the IPython Console plugin and begin tracking shells."""
        if ipython_console_plugin is None:
            self.unbind_ipython_console()
            return

        if self._ipython_console_plugin is ipython_console_plugin:
            current_shell = self._safe_get_current_shellwidget()
            self._switch_current_shellwidget(current_shell)
            return

        self.unbind_ipython_console()
        self._ipython_console_plugin = ipython_console_plugin

        ipython_console_plugin.sig_shellwidget_created.connect(
            self._on_shellwidget_created
        )
        ipython_console_plugin.sig_shellwidget_deleted.connect(
            self._on_shellwidget_deleted
        )
        ipython_console_plugin.sig_shellwidget_changed.connect(
            self._on_shellwidget_changed
        )
        ipython_console_plugin.sig_shellwidget_errored.connect(
            self._on_shellwidget_errored
        )

        logger.info("Runtime context service bound to IPython Console plugin")

        current_shell = self._safe_get_current_shellwidget()
        if current_shell is not None:
            self._track_shellwidget(current_shell)
        self._switch_current_shellwidget(current_shell)

    def unbind_ipython_console(self):
        """Disconnect from the IPython Console plugin and tracked shells."""
        plugin = self._ipython_console_plugin
        if plugin is not None:
            try:
                plugin.sig_shellwidget_created.disconnect(self._on_shellwidget_created)
                plugin.sig_shellwidget_deleted.disconnect(self._on_shellwidget_deleted)
                plugin.sig_shellwidget_changed.disconnect(self._on_shellwidget_changed)
                plugin.sig_shellwidget_errored.disconnect(self._on_shellwidget_errored)
            except Exception:
                pass

        for shell_id in list(self._tracked_shell_ids):
            snapshot = self._snapshots.get(shell_id)
            shellwidget = snapshot.get("_shellwidget") if snapshot else None
            if shellwidget is not None:
                self._disconnect_shellwidget(shellwidget)

        self._ipython_console_plugin = None
        self._current_shell_id = None
        self._selected_shell_id = ""
        self._tracked_shell_ids.clear()
        self._snapshots.clear()
        self.sig_shell_targets_changed.emit([], "")
        self.sig_current_context_changed.emit(
            make_empty_runtime_context(
                status="unavailable",
                detail="No active IPython console is available.",
            )
        )

    def cleanup(self):
        """Release all signal connections owned by this service."""
        self.unbind_ipython_console()
        self._variable_explorer_plugin = None

    def set_variable_explorer_plugin(self, variable_explorer_plugin):
        """Store the Variable Explorer plugin for settings reuse."""
        self._variable_explorer_plugin = variable_explorer_plugin
        if variable_explorer_plugin is None:
            logger.info(
                "Runtime context service using default namespace view settings"
            )
        else:
            logger.info(
                "Runtime context service using Variable Explorer namespace settings"
            )
        if self._tracked_shell_ids:
            logger.info(
                "Runtime context service updating namespace settings for tracked shells"
            )
            for shell_id in list(self._tracked_shell_ids):
                snapshot = self._snapshots.get(shell_id, {})
                shellwidget = snapshot.get("_shellwidget")
                if shellwidget is not None:
                    self._ensure_namespace_settings(shellwidget, force=True)

    def get_current_context(self):
        """Return a copy of the current shell's runtime snapshot."""
        shellwidget = self._resolve_effective_shellwidget()
        if shellwidget is None:
            return make_empty_runtime_context(
                status="unavailable",
                detail="No runtime target is available.",
            )

        self._track_shellwidget(shellwidget)
        self._refresh_console_snapshot(shellwidget, reason="context-request")
        return self._build_public_context(shellwidget)

    def set_target_shell_id(self, shell_id):
        """Select one explicit runtime target shell or follow the active shell."""
        normalized = str(shell_id or "").strip()
        if normalized and normalized not in self._snapshots:
            logger.warning(
                "Ignoring unknown runtime target shell id: %s",
                normalized,
            )
            normalized = ""

        if normalized == self._selected_shell_id:
            return

        self._selected_shell_id = normalized
        logger.info(
            "Runtime context target shell set to %s",
            normalized or "<follow-active>",
        )
        self._emit_shell_targets_changed()
        self._emit_current_context_changed()

    def get_shell_targets(self):
        """Return the serialized runtime shell-target choices."""
        return self._build_shell_records(), self._selected_shell_id

    def execute_request(self, request):
        """Execute one read-only runtime inspection request."""
        tool = (request or {}).get("tool", "")
        args = (request or {}).get("args") or {}
        logger.info(
            "Executing runtime request: tool=%s requested_shell=%s selected_shell=%s active_shell=%s",
            tool,
            str(args.get("shell_id", "")).strip() or "<default>",
            self._selected_shell_id or "<follow-active>",
            self._current_shell_id or "<none>",
        )

        shellwidget, runtime_context, shell_note, shell_error = (
            self._resolve_request_shellwidget(args)
        )
        if shellwidget is None:
            result = {
                "ok": False,
                "tool": tool,
                "source": "unavailable",
                "shell_status": "unavailable",
                "shell_detail": shell_error or "No active IPython console is available.",
                "shell_id": "",
                "shell_label": "",
                "active_shell_id": self._current_shell_id or "",
                "active_shell_label": self._label_for_shell_id(self._current_shell_id),
                "target_shell_id": self._effective_target_shell_id(args),
                "target_shell_label": self._label_for_shell_id(
                    self._effective_target_shell_id(args)
                ),
                "working_directory": "",
                "last_refreshed_at": "",
                "payload": {},
                "query_note": shell_note,
                "error": shell_error or "No active IPython console is available.",
            }
            self._log_request_result(result)
            return result

        if tool == "runtime.list_shells":
            result = self._build_list_shells_result(tool, runtime_context, shell_note)
        elif tool == "runtime.status":
            result = self._build_status_result(tool, runtime_context, shell_note)
        elif tool == "runtime.get_latest_error":
            result = self._build_latest_error_result(tool, runtime_context, shell_note)
        elif tool == "runtime.get_console_tail":
            result = self._build_console_result(tool, runtime_context, args, shell_note)
        elif tool == "runtime.list_variables":
            result = self._build_list_variables_result(
                tool, shellwidget, runtime_context, args, shell_note
            )
        elif tool == "runtime.inspect_variable":
            name = str(args.get("name", "")).strip()
            names = [name] if name else []
            result = self._build_inspect_variables_result(
                tool, shellwidget, runtime_context, names, shell_note
            )
        elif tool == "runtime.inspect_variables":
            raw_names = args.get("names", [])
            if isinstance(raw_names, str):
                raw_names = [raw_names]
            names = [
                str(name).strip()
                for name in raw_names[:MAX_RUNTIME_REQUEST_NAMES]
                if str(name).strip()
            ]
            result = self._build_inspect_variables_result(
                tool, shellwidget, runtime_context, names, shell_note
            )
        else:
            result = self._build_result_base(
                tool,
                runtime_context,
                source="unavailable",
                query_note=shell_note,
                error=f"Unsupported runtime tool: {tool}",
            )
            result["ok"] = False
        self._log_request_result(result)
        return result

    # --- Qt signal handlers -------------------------------------------------

    def _on_shellwidget_created(self, shellwidget):
        self._track_shellwidget(shellwidget)
        self._switch_current_shellwidget(shellwidget)

    def _on_shellwidget_deleted(self, shellwidget):
        shell_id = self._shell_id(shellwidget)
        logger.info("Runtime context shell deleted: %s", shell_id)
        self._disconnect_shellwidget(shellwidget)
        self._tracked_shell_ids.discard(shell_id)
        self._snapshots.pop(shell_id, None)
        if self._selected_shell_id == shell_id:
            self._selected_shell_id = ""
        if self._current_shell_id == shell_id:
            self._current_shell_id = None
        self._emit_shell_targets_changed()
        self._emit_current_context_changed()

    def _on_shellwidget_changed(self, shellwidget):
        self._track_shellwidget(shellwidget)
        self._switch_current_shellwidget(shellwidget)

    def _on_shellwidget_errored(self, shellwidget):
        snapshot = self._get_or_create_snapshot(shellwidget)
        snapshot["status"] = "errored"
        snapshot["status_detail"] = "The current shellwidget failed to start."
        snapshot["collection_error"] = "Shellwidget startup failed."
        snapshot["stale"] = True
        logger.warning("Runtime context shell errored: %s", snapshot["shell_id"])
        self._emit_current_context_changed(shellwidget)

    def _on_prompt_ready(self):
        shellwidget = self.sender()
        if shellwidget is None:
            return
        self._ensure_namespace_settings(shellwidget)
        self._refresh_console_snapshot(shellwidget, reason="prompt-ready")

    def _on_kernel_state_arrived(self, state):
        shellwidget = self.sender()
        if shellwidget is None:
            return

        snapshot = self._get_or_create_snapshot(shellwidget)
        self._ensure_namespace_settings(shellwidget)
        snapshot["_namespace_view"] = state.get("namespace_view", {}) or {}
        snapshot["_var_properties"] = state.get("var_properties", {}) or {}
        snapshot["variables"] = build_runtime_variable_summaries(
            snapshot["_namespace_view"],
            snapshot["_var_properties"],
        )
        snapshot["last_refreshed_at"] = _timestamp_now()
        snapshot["collection_error"] = ""
        if snapshot["status"] != "errored":
            snapshot["status"] = "ready"
        snapshot["stale"] = self._is_shell_busy(shellwidget)
        logger.info(
            "Runtime context kernel state updated for %s with %d variables",
            snapshot["shell_id"],
            len(snapshot["variables"]),
        )
        self._emit_current_context_changed(shellwidget)

    # --- Shell tracking -----------------------------------------------------

    def _track_shellwidget(self, shellwidget):
        if shellwidget is None:
            return

        shell_id = self._shell_id(shellwidget)
        snapshot = self._get_or_create_snapshot(shellwidget)
        if shell_id in self._tracked_shell_ids:
            self._refresh_console_snapshot(shellwidget, reason="track-existing")
            return

        self._tracked_shell_ids.add(shell_id)
        shellwidget.sig_prompt_ready.connect(self._on_prompt_ready)
        shellwidget.sig_kernel_state_arrived.connect(self._on_kernel_state_arrived)

        logger.info("Runtime context tracking shell %s", shell_id)
        self._ensure_namespace_settings(shellwidget)
        self._refresh_console_snapshot(shellwidget, reason="track-new")
        self._emit_shell_targets_changed()

    def _disconnect_shellwidget(self, shellwidget):
        try:
            shellwidget.sig_prompt_ready.disconnect(self._on_prompt_ready)
        except Exception:
            pass
        try:
            shellwidget.sig_kernel_state_arrived.disconnect(self._on_kernel_state_arrived)
        except Exception:
            pass

    def _switch_current_shellwidget(self, shellwidget):
        if shellwidget is None:
            self._current_shell_id = None
            self._emit_current_context_changed()
            return

        shell_id = self._shell_id(shellwidget)
        shell_changed = shell_id != self._current_shell_id
        self._current_shell_id = shell_id
        snapshot = self._get_or_create_snapshot(shellwidget)
        snapshot["stale"] = self._is_shell_busy(shellwidget)
        self._refresh_console_snapshot(shellwidget, reason="shell-change")
        if shell_changed:
            logger.info("Runtime context current shell set to %s", shell_id)
            self._emit_shell_targets_changed()

    # --- Snapshot updates ---------------------------------------------------

    def _ensure_namespace_settings(self, shellwidget, force=False):
        if shellwidget is None or not getattr(shellwidget, "spyder_kernel_ready", False):
            return

        settings = build_namespace_view_settings(self._variable_explorer_plugin)
        snapshot = self._get_or_create_snapshot(shellwidget)
        previous = snapshot.get("_namespace_view_settings")
        if not force and previous == settings:
            return

        snapshot["_namespace_view_settings"] = copy.deepcopy(settings)
        try:
            shellwidget.set_kernel_configuration("namespace_view_settings", settings)
            logger.info(
                "Runtime context seeded namespace view settings for %s",
                snapshot["shell_id"],
            )
        except Exception as error:
            snapshot["collection_error"] = f"Failed to seed namespace settings: {error}"
            logger.warning(
                "Failed to seed namespace view settings for %s: %s",
                snapshot["shell_id"],
                error,
            )

    def _refresh_console_snapshot(self, shellwidget, reason):
        snapshot = self._get_or_create_snapshot(shellwidget)
        console_text = ""
        try:
            control = getattr(shellwidget, "_control", None)
            if control is not None:
                console_text = control.toPlainText() or ""
        except Exception as error:
            snapshot["collection_error"] = f"Failed to read console output: {error}"
            logger.warning(
                "Failed to read console output for %s during %s: %s",
                snapshot["shell_id"],
                reason,
                error,
            )
            return

        console_summary = summarize_console_text(console_text)
        snapshot["console_output"] = console_summary["console_output"]
        snapshot["latest_error"] = console_summary["latest_error"]
        snapshot["working_directory"] = _safe_get_cwd(shellwidget)
        snapshot["last_refreshed_at"] = _timestamp_now()
        snapshot["stale"] = self._is_shell_busy(shellwidget)

        if snapshot["status"] != "errored":
            if not getattr(shellwidget, "spyder_kernel_ready", False):
                snapshot["status"] = "starting"
                snapshot["status_detail"] = "The current kernel is not ready yet."
            elif snapshot["stale"]:
                snapshot["status"] = "busy"
                snapshot["status_detail"] = (
                    "Using the last cached runtime snapshot while the kernel is busy."
                )
            else:
                snapshot["status"] = "ready"
                snapshot["status_detail"] = ""

        logger.debug(
            "Runtime context refreshed console snapshot for %s (%s)",
            snapshot["shell_id"],
            reason,
        )
        self._emit_current_context_changed(shellwidget)

    # --- Internal helpers ---------------------------------------------------

    def _get_or_create_snapshot(self, shellwidget):
        shell_id = self._shell_id(shellwidget)
        snapshot = self._snapshots.get(shell_id)
        if snapshot is None:
            snapshot = make_empty_runtime_context(
                status="ready" if getattr(shellwidget, "spyder_kernel_ready", False) else "starting",
                detail="",
            )
            snapshot["shell_id"] = shell_id
            snapshot["shell_label"] = ""
            snapshot["working_directory"] = _safe_get_cwd(shellwidget)
            snapshot["_shellwidget"] = shellwidget
            snapshot["_namespace_view"] = {}
            snapshot["_var_properties"] = {}
            snapshot["_namespace_view_settings"] = None
            self._snapshots[shell_id] = snapshot
        else:
            snapshot["_shellwidget"] = shellwidget
        return snapshot

    def _build_public_context(self, shellwidget):
        """Return the normalized public runtime context for a shellwidget."""
        if shellwidget is None:
            return make_empty_runtime_context(
                status="unavailable",
                detail="No active IPython console is available.",
            )

        snapshot = self._snapshots.get(self._shell_id(shellwidget))
        runtime_context = clone_runtime_context(snapshot)
        effective_target_id = self._effective_target_shell_id()
        runtime_context["active_shell_id"] = self._current_shell_id or ""
        runtime_context["active_shell_label"] = self._label_for_shell_id(
            self._current_shell_id
        )
        runtime_context["target_shell_id"] = effective_target_id
        runtime_context["target_shell_label"] = self._label_for_shell_id(
            effective_target_id
        )
        runtime_context["available_shells"] = self._build_shell_records()
        runtime_context["shell_label"] = self._label_for_shell_id(
            runtime_context.get("shell_id")
        )

        if self._is_shell_busy(shellwidget):
            runtime_context["status"] = "busy"
            runtime_context["stale"] = True
            runtime_context["status_detail"] = (
                "Using the last cached runtime snapshot while the kernel is busy."
            )
        elif not getattr(shellwidget, "spyder_kernel_ready", False):
            runtime_context["status"] = "starting"
            runtime_context["status_detail"] = (
                "The current kernel is not ready yet."
            )
        elif runtime_context["status"] != "errored":
            runtime_context["status"] = "ready"

        return runtime_context

    def _emit_current_context_changed(self, shellwidget=None):
        """Emit the current shell's public runtime context for UI consumers."""
        current_shell = shellwidget or self._resolve_effective_shellwidget()

        self.sig_current_context_changed.emit(
            self._build_public_context(current_shell)
        )

    def _emit_shell_targets_changed(self):
        """Emit the current shell-target options for UI consumers."""
        self.sig_shell_targets_changed.emit(
            self._build_shell_records(),
            self._selected_shell_id,
        )

    def _safe_get_current_shellwidget(self):
        if self._ipython_console_plugin is None:
            return None
        try:
            return self._ipython_console_plugin.get_current_shellwidget()
        except Exception:
            return None

    def _resolve_effective_shellwidget(self):
        """Return the shellwidget the chat runtime should currently target."""
        if self._selected_shell_id:
            snapshot = self._snapshots.get(self._selected_shell_id)
            shellwidget = snapshot.get("_shellwidget") if snapshot else None
            if shellwidget is not None:
                return shellwidget
        return self._safe_get_current_shellwidget()

    def _resolve_request_shellwidget(self, args):
        """Resolve the shellwidget for one runtime request plus context."""
        requested_shell_id = str((args or {}).get("shell_id", "")).strip()
        if requested_shell_id:
            snapshot = self._snapshots.get(requested_shell_id)
            shellwidget = snapshot.get("_shellwidget") if snapshot else None
            if shellwidget is None:
                return None, make_empty_runtime_context(), "", (
                    f"Requested shell id is not available: {requested_shell_id}"
                )
            self._track_shellwidget(shellwidget)
            self._refresh_console_snapshot(shellwidget, reason="request-target")
            return (
                shellwidget,
                self._build_public_context(shellwidget),
                "Using the explicitly requested console target.",
                "",
            )

        shellwidget = self._resolve_effective_shellwidget()
        if shellwidget is None:
            return None, make_empty_runtime_context(), "", (
                "No active IPython console is available."
            )

        self._track_shellwidget(shellwidget)
        self._refresh_console_snapshot(shellwidget, reason="request-default")
        if self._selected_shell_id:
            query_note = "Using the pinned console target from the chat toolbar."
        else:
            query_note = "Using the current active Spyder IPython console."
        return shellwidget, self._build_public_context(shellwidget), query_note, ""

    def _effective_target_shell_id(self, args=None):
        """Return the effective shell id for runtime work."""
        requested_shell_id = str(((args or {}).get("shell_id", ""))).strip()
        if requested_shell_id:
            return requested_shell_id
        if self._selected_shell_id:
            return self._selected_shell_id
        return self._current_shell_id or ""

    @staticmethod
    def _shell_id(shellwidget):
        return hex(id(shellwidget))

    @staticmethod
    def _is_shell_busy(shellwidget):
        if shellwidget is None:
            return False
        try:
            return bool(
                getattr(shellwidget, "_executing", False)
                or shellwidget.is_waiting_pdb_input()
            )
        except Exception:
            return bool(getattr(shellwidget, "_executing", False))

    def _build_result_base(self, tool, runtime_context, source, query_note="", error=""):
        """Return common result metadata for one runtime tool."""
        return {
            "tool": tool,
            "source": source,
            "shell_status": runtime_context.get("status", "unavailable"),
            "shell_detail": runtime_context.get("status_detail", ""),
            "shell_id": runtime_context.get("shell_id", ""),
            "shell_label": runtime_context.get("shell_label", ""),
            "active_shell_id": runtime_context.get("active_shell_id", ""),
            "active_shell_label": runtime_context.get("active_shell_label", ""),
            "target_shell_id": runtime_context.get("target_shell_id", ""),
            "target_shell_label": runtime_context.get("target_shell_label", ""),
            "working_directory": runtime_context.get("working_directory", ""),
            "last_refreshed_at": runtime_context.get("last_refreshed_at", ""),
            "payload": {},
            "query_note": query_note,
            "error": error,
        }

    def _build_list_shells_result(self, tool, runtime_context, query_note):
        """Return the available shell targets for multi-console workflows."""
        shells = self._build_shell_records()
        result = self._build_result_base(
            tool,
            runtime_context,
            source="snapshot",
            query_note=query_note,
        )
        result["ok"] = bool(shells)
        result["payload"] = {
            "count": len(shells),
            "shells": shells,
        }
        if not shells:
            result["error"] = "No tracked Spyder IPython consoles are available."
        return result

    def _build_status_result(self, tool, runtime_context, query_note):
        result = self._build_result_base(
            tool,
            runtime_context,
            source="snapshot",
            query_note=query_note,
        )
        result["ok"] = True
        result["payload"] = {
            "stale": runtime_context.get("stale", False),
        }
        return result

    def _build_latest_error_result(self, tool, runtime_context, query_note):
        latest_error = runtime_context.get("latest_error", "")
        result = self._build_result_base(
            tool,
            runtime_context,
            source="snapshot",
            query_note=query_note,
            error="" if latest_error else "No latest error is available.",
        )
        result["ok"] = bool(latest_error)
        result["payload"] = {
            "latest_error": latest_error,
            "summary": summarize_traceback_text(latest_error),
        }
        return result

    def _build_console_result(self, tool, runtime_context, args, query_note):
        max_chars = _bounded_int(
            args.get("max_chars"),
            default=MAX_RUNTIME_CONSOLE_CHARS,
            minimum=200,
            maximum=MAX_RUNTIME_CONSOLE_CHARS,
        )
        console_output = _clip_tail_text(
            runtime_context.get("console_output", ""),
            max_chars,
        )
        result = self._build_result_base(
            tool,
            runtime_context,
            source="snapshot",
            query_note=query_note,
            error="" if console_output else "No recent console output is available.",
        )
        result["ok"] = bool(console_output)
        result["payload"] = {"console_output": console_output}
        return result

    def _build_list_variables_result(self, tool, shellwidget, runtime_context, args, query_note):
        limit = _bounded_int(
            args.get("limit"),
            default=MAX_RUNTIME_REQUEST_VARIABLES,
            minimum=1,
            maximum=MAX_RUNTIME_VARIABLES,
        )
        namespace_view, var_properties, source, namespace_note, query_error = (
            self._query_namespace_state(shellwidget, tool)
        )
        variables = build_runtime_variable_summaries(
            namespace_view,
            var_properties,
        )[:limit]
        result = self._build_result_base(
            tool,
            runtime_context,
            source=source,
            query_note=namespace_note or query_note,
            error=query_error if not variables else "",
        )
        result["ok"] = bool(variables)
        result["payload"] = {
            "count": len(variables),
            "variables": variables,
        }
        return result

    def _build_inspect_variables_result(self, tool, shellwidget, runtime_context, names, query_note):
        if not names:
            result = self._build_result_base(
                tool,
                runtime_context,
                source="unavailable",
                error="No variable name was provided.",
            )
            result["ok"] = False
            return result

        namespace_view, var_properties, source, namespace_note, query_error = (
            self._query_namespace_state(shellwidget, tool)
        )

        found = []
        missing = []
        for name in names[:MAX_RUNTIME_REQUEST_NAMES]:
            info = namespace_view.get(name)
            if info is None:
                missing.append(name)
                continue

            variable = _build_variable_summary(
                name,
                info,
                var_properties.get(name, {}),
            )
            if source == "live":
                variable = self._attach_live_details(shellwidget, variable)
            found.append(variable)

        error = ""
        if not found:
            if missing:
                error = f"Variables not found: {', '.join(missing)}"
            elif query_error:
                error = query_error
            else:
                error = "No matching variables were found."

        result = self._build_result_base(
            tool,
            runtime_context,
            source=source,
            query_note=namespace_note or query_note,
            error=error,
        )
        result["ok"] = bool(found)
        result["payload"] = {
            "variables": found,
            "missing": missing,
        }
        return result

    def _query_namespace_state(self, shellwidget, tool):
        snapshot = self._get_or_create_snapshot(shellwidget)
        cached_namespace = snapshot.get("_namespace_view", {}) or {}
        cached_properties = snapshot.get("_var_properties", {}) or {}

        if self._is_shell_busy(shellwidget):
            logger.info(
                "Runtime request %s using cached namespace because the kernel is busy",
                tool,
            )
            return (
                cached_namespace,
                cached_properties,
                "cached",
                "Using the last cached namespace snapshot because the kernel is busy.",
                "",
            )

        if not getattr(shellwidget, "spyder_kernel_ready", False):
            logger.info(
                "Runtime request %s using cached namespace because the kernel is not ready",
                tool,
            )
            return (
                cached_namespace,
                cached_properties,
                "cached",
                "Using the last cached namespace snapshot because the kernel is still starting.",
                "",
            )

        self._ensure_namespace_settings(shellwidget)
        try:
            kernel_client = shellwidget.call_kernel(
                blocking=True,
                timeout=MAX_RUNTIME_REQUEST_TIMEOUT,
            )
            namespace_view = kernel_client.get_namespace_view() or {}
            var_properties = kernel_client.get_var_properties() or {}
        except Exception as error:
            logger.warning(
                "Runtime request %s failed to refresh namespace state: %s",
                tool,
                error,
            )
            if cached_namespace:
                return (
                    cached_namespace,
                    cached_properties,
                    "cached",
                    "Using the last cached namespace snapshot because the live query failed.",
                    str(error),
                )
            return {}, {}, "cached", "", str(error)

        snapshot["_namespace_view"] = namespace_view
        snapshot["_var_properties"] = var_properties
        snapshot["variables"] = build_runtime_variable_summaries(
            namespace_view,
            var_properties,
        )
        snapshot["last_refreshed_at"] = _timestamp_now()
        snapshot["stale"] = False
        if snapshot["status"] != "errored":
            snapshot["status"] = "ready"
            snapshot["status_detail"] = ""
        logger.info(
            "Runtime request %s refreshed live namespace state for %s",
            tool,
            snapshot["shell_id"],
        )
        return namespace_view, var_properties, "live", "", ""

    def _attach_live_details(self, shellwidget, variable):
        kind = variable.get("kind")
        if kind not in {
                "scalar", "list", "tuple", "set", "dict",
                "array", "image", "dataframe", "series"}:
            return variable

        length = variable.get("length")
        if kind in {"list", "tuple", "set", "dict"} and length not in ("", None):
            try:
                if int(length) > 20:
                    return variable
            except (TypeError, ValueError):
                pass

        try:
            kernel_client = shellwidget.call_kernel(
                blocking=True,
                timeout=MAX_RUNTIME_REQUEST_TIMEOUT,
            )
            value = kernel_client.get_value(variable["name"], encoded=False)
        except Exception as error:
            logger.debug(
                "Runtime inspect failed to fetch live value for %s: %s",
                variable.get("name"),
                error,
            )
            return variable

        live_summary = summarize_runtime_value(
            value,
            kind=kind,
            fallback_type=variable.get("type", ""),
        )
        if live_summary:
            updated = dict(variable)
            updated.update(live_summary)
            return updated
        return variable

    def _build_shell_records(self):
        """Return normalized records for tracked Spyder IPython consoles."""
        records = []
        seen = set()
        clients = self._safe_get_clients()

        for index, client in enumerate(clients[:MAX_RUNTIME_SHELLS], start=1):
            shellwidget = self._client_shellwidget(client)
            if shellwidget is None:
                continue
            shell_id = self._shell_id(shellwidget)
            snapshot = self._get_or_create_snapshot(shellwidget)
            label = self._label_for_client(client, fallback_index=index)
            snapshot["shell_label"] = label
            records.append(
                self._build_shell_record(snapshot, label)
            )
            seen.add(shell_id)

        for shell_id in list(self._tracked_shell_ids):
            if shell_id in seen:
                continue
            snapshot = self._snapshots.get(shell_id)
            if not snapshot:
                continue
            label = snapshot.get("shell_label") or f"Console {len(records) + 1}"
            snapshot["shell_label"] = label
            records.append(self._build_shell_record(snapshot, label))

        return records

    def _build_shell_record(self, snapshot, label):
        """Return one serialized shell-target record."""
        shell_id = snapshot.get("shell_id", "")
        return {
            "shell_id": shell_id,
            "label": label,
            "status": snapshot.get("status", "unavailable"),
            "status_detail": snapshot.get("status_detail", ""),
            "working_directory": snapshot.get("working_directory", ""),
            "last_refreshed_at": snapshot.get("last_refreshed_at", ""),
            "has_error": bool(snapshot.get("latest_error")),
            "is_active": shell_id == (self._current_shell_id or ""),
            "is_target": shell_id == self._effective_target_shell_id(),
        }

    def _safe_get_clients(self):
        """Return IPython console clients when the plugin is available."""
        if self._ipython_console_plugin is None:
            return []
        try:
            return list(self._ipython_console_plugin.get_clients() or [])
        except Exception:
            return []

    @staticmethod
    def _client_shellwidget(client):
        """Return the shellwidget for one IPython console client."""
        for attribute in ("shellwidget", "_shellwidget"):
            try:
                shellwidget = getattr(client, attribute, None)
            except Exception:
                shellwidget = None
            if shellwidget is not None:
                return shellwidget
        return None

    @staticmethod
    def _label_for_client(client, fallback_index=1):
        """Return a user-facing label for one IPython console client."""
        try:
            label = client.get_name()
        except Exception:
            label = ""
        label = str(label or "").strip()
        return label or f"Console {fallback_index}"

    def _label_for_shell_id(self, shell_id):
        """Return the best known label for one shell id."""
        if not shell_id:
            return ""
        for record in self._build_shell_records():
            if record.get("shell_id") == shell_id:
                return record.get("label", "")
        snapshot = self._snapshots.get(shell_id, {})
        return snapshot.get("shell_label", "")

    def _log_request_result(self, result):
        """Log one runtime request result with shell-target metadata."""
        logger.info(
            "Runtime request completed: tool=%s ok=%s source=%s shell=%s active=%s target=%s error=%s",
            result.get("tool", ""),
            result.get("ok", False),
            result.get("source", ""),
            result.get("shell_label", "") or result.get("shell_id", "<none>"),
            result.get("active_shell_label", "") or result.get("active_shell_id", "<none>"),
            result.get("target_shell_label", "") or result.get("target_shell_id", "<none>"),
            result.get("error", ""),
        )


def summarize_console_text(console_text):
    """Split visible console text into recent output and latest error."""
    normalized = _normalize_console_text(console_text)
    if not normalized:
        return {"console_output": "", "latest_error": ""}

    lines = normalized.split("\n")
    error_lines = _extract_latest_error_lines(lines)
    latest_error = _clip_tail_text("\n".join(error_lines), MAX_RUNTIME_ERROR_CHARS)

    recent_lines = lines[-MAX_RUNTIME_CONSOLE_LINES:]
    if error_lines:
        recent_lines = _remove_contiguous_subsequence(recent_lines, error_lines)
    recent_text = _clip_tail_text(
        _strip_surrounding_blank_lines(recent_lines),
        MAX_RUNTIME_CONSOLE_CHARS,
    )

    return {
        "console_output": recent_text,
        "latest_error": latest_error,
    }


def build_runtime_variable_summaries(namespace_view, var_properties):
    """Build structured summaries from Spyder namespace-view state."""
    if not namespace_view:
        return []

    summaries = []
    for name, info in list(namespace_view.items())[:MAX_RUNTIME_VARIABLES]:
        properties = var_properties.get(name, {}) if var_properties else {}
        summaries.append(_build_variable_summary(name, info, properties))
    return summaries


def format_runtime_variable(variable):
    """Render one structured variable summary into a compact prompt line."""
    name = variable.get("name", "?")
    kind = variable.get("kind", "object")
    type_name = variable.get("type", "")
    shape = variable.get("shape", "")
    dtype = variable.get("dtype", "")
    length = variable.get("length", "")
    size = variable.get("size", "")
    columns = variable.get("columns", "")
    dtypes = variable.get("dtypes", "")
    value_range = variable.get("range", "")
    channels = variable.get("channels", "")
    preview = variable.get("preview", "")

    parts = [f"{name} [{kind}]"]
    if type_name:
        parts.append(f"type={type_name}")
    if shape:
        parts.append(f"shape={shape}")
    elif length not in ("", None):
        parts.append(f"len={length}")
    elif size not in ("", None):
        parts.append(f"size={size}")
    if dtype:
        parts.append(f"dtype={dtype}")
    if columns:
        parts.append(f"columns={columns}")
    if dtypes:
        parts.append(f"dtypes={dtypes}")
    if channels:
        parts.append(f"channels={channels}")
    if value_range:
        parts.append(f"range={value_range}")
    if preview:
        label = "value" if kind == "scalar" else "preview"
        parts.append(f"{label}={preview}")
    return "; ".join(parts)


def format_runtime_shell(shell_record):
    """Render one console-target record into a compact prompt line."""
    label = shell_record.get("label", "Console")
    shell_id = shell_record.get("shell_id", "")
    status = shell_record.get("status", "unavailable")
    cwd = shell_record.get("working_directory", "")

    flags = []
    if shell_record.get("is_active"):
        flags.append("active")
    if shell_record.get("is_target"):
        flags.append("target")
    if shell_record.get("has_error"):
        flags.append("error")

    parts = [label]
    if shell_id:
        parts.append(f"id={shell_id}")
    parts.append(f"status={status}")
    if cwd:
        parts.append(f"cwd={cwd}")
    if flags:
        parts.append(f"flags={','.join(flags)}")
    return "; ".join(parts)


def summarize_traceback_text(latest_error):
    """Return a compact structured summary of one traceback block."""
    text = (latest_error or "").strip()
    if not text:
        return {}

    lines = text.splitlines()
    frames = []
    for index, line in enumerate(lines):
        stripped = line.strip()
        file_match = TRACEBACK_FILE_FRAME_RE.match(stripped)
        if file_match:
            frames.append({
                "file": file_match.group(1),
                "line": int(file_match.group(2)),
                "function": file_match.group(3),
                "code": _extract_traceback_code_line(lines, index),
            })
            continue

        cell_match = TRACEBACK_CELL_FRAME_RE.match(stripped)
        if cell_match:
            cell_number = cell_match.group(1)
            function = cell_match.group(3) or f"In[{cell_number}]"
            frames.append({
                "file": f"Cell In[{cell_number}]",
                "line": int(cell_match.group(2)),
                "function": function,
                "code": _extract_traceback_code_line(lines, index),
            })

    exception_type = ""
    exception_message = ""
    for line in reversed(lines):
        stripped = line.strip()
        if (
            not stripped
            or TRACEBACK_FILE_FRAME_RE.match(stripped)
            or TRACEBACK_CELL_FRAME_RE.match(stripped)
        ):
            continue
        if ":" in stripped:
            maybe_type, maybe_message = stripped.split(":", 1)
            maybe_type = maybe_type.strip()
            if ERROR_LINE_RE.match(maybe_type):
                exception_type = maybe_type
                exception_message = maybe_message.strip()
                break
        if ERROR_LINE_RE.match(stripped):
            exception_type = stripped
            break

    return {
        "exception_type": exception_type,
        "exception_message": exception_message,
        "frame_count": len(frames),
        "frames": frames[-3:],
    }


def summarize_runtime_value(value, kind="", fallback_type=""):
    """Return richer runtime-variable details from one live kernel value."""
    if _is_pandas_dataframe(value):
        shape = getattr(value, "shape", ())
        columns = list(getattr(value, "columns", [])[:6])
        dtypes_items = list(getattr(value, "dtypes", []).items())[:4]
        return {
            "kind": "dataframe",
            "type": fallback_type or type(value).__name__,
            "shape": str(tuple(shape)) if shape else "",
            "length": getattr(value, "shape", [None])[0] if shape else "",
            "columns": _clip_preview(", ".join(str(column) for column in columns)),
            "dtypes": _clip_preview(
                ", ".join(f"{name}:{dtype}" for name, dtype in dtypes_items)
            ),
            "preview": _clip_preview(
                repr(value.head(3).to_dict(orient="records"))
            ),
        }

    if _is_pandas_series(value):
        shape = getattr(value, "shape", ())
        dtype = getattr(value, "dtype", "")
        return {
            "kind": "series",
            "type": fallback_type or type(value).__name__,
            "shape": str(tuple(shape)) if shape else "",
            "length": len(value),
            "dtype": str(dtype) if dtype else "",
            "preview": _clip_preview(repr(value.head(5).tolist())),
        }

    if _is_array_like(value):
        array_kind = "image" if kind == "image" else "array"
        shape = getattr(value, "shape", ())
        dtype = getattr(value, "dtype", "")
        summary = {
            "kind": array_kind,
            "type": fallback_type or type(value).__name__,
            "shape": str(tuple(shape)) if shape else "",
            "dtype": str(dtype) if dtype else "",
            "preview": _clip_preview(repr(_array_preview(value))),
            "range": _array_range(value),
        }
        channels = _array_channels(shape)
        if channels:
            summary["channels"] = channels
        return summary

    if kind in {"array", "image"}:
        sequence_summary = _summarize_sequence_array(
            value,
            kind=kind,
            fallback_type=fallback_type,
        )
        if sequence_summary:
            return sequence_summary

    if isinstance(value, dict):
        return {
            "kind": "dict",
            "type": fallback_type or type(value).__name__,
            "length": len(value),
            "preview": _clip_preview(repr(dict(list(value.items())[:5]))),
        }

    if isinstance(value, (list, tuple, set)):
        return {
            "kind": kind or type(value).__name__,
            "type": fallback_type or type(value).__name__,
            "length": len(value),
            "preview": _summarize_live_value(value),
        }

    return {
        "type": fallback_type or type(value).__name__,
        "preview": _summarize_live_value(value),
    }


def _build_runtime_status_block(runtime_context):
    has_runtime_content = any(
        (
            runtime_context.get("latest_error"),
            runtime_context.get("console_output"),
            runtime_context.get("variables"),
        )
    )
    if not has_runtime_content and runtime_context.get("status") == "ready":
        return ""

    lines = [f"status: {runtime_context.get('status', 'unavailable')}"]
    working_directory = runtime_context.get("working_directory")
    if working_directory:
        lines.append(f"cwd: {working_directory}")
    last_refreshed = runtime_context.get("last_refreshed_at")
    if last_refreshed:
        lines.append(f"last refreshed: {last_refreshed}")
    detail = runtime_context.get("status_detail")
    if detail:
        lines.append(f"note: {detail}")
    collection_error = runtime_context.get("collection_error")
    if collection_error:
        lines.append(f"collection issue: {collection_error}")

    return (
        "[Runtime Context]\n"
        "--- runtime status ---\n"
        + "\n".join(lines)
        + "\n--- end runtime status ---"
    )


def _build_runtime_error_block(runtime_context):
    latest_error = runtime_context.get("latest_error", "")
    if not latest_error:
        return ""
    return (
        "[IPython Console — latest error]\n"
        "--- latest error ---\n"
        f"{latest_error}\n"
        "--- end latest error ---"
    )


def _build_runtime_variables_block(runtime_context):
    variables = runtime_context.get("variables", [])
    if not variables:
        return ""

    rendered = []
    used_chars = 0
    for variable in variables:
        line = format_runtime_variable(variable)
        if used_chars + len(line) > MAX_RUNTIME_VARIABLES_CHARS:
            rendered.append("... (remaining variables omitted)")
            break
        rendered.append(line)
        used_chars += len(line) + 1

    return (
        "[Namespace Variables]\n"
        "--- variables ---\n"
        + "\n".join(rendered)
        + "\n--- end variables ---"
    )


def _build_runtime_console_block(runtime_context):
    console_output = runtime_context.get("console_output", "")
    if not console_output:
        return ""
    return (
        "[IPython Console — recent output]\n"
        "--- console output ---\n"
        f"{console_output}\n"
        "--- end console output ---"
    )


def _build_variable_summary(name, info, properties):
    preview = _clip_preview(info.get("view", ""))
    variable = {
        "name": name,
        "kind": _infer_variable_kind(info, properties),
        "type": info.get("type") or info.get("python_type") or "object",
        "size": _stringify_metric(info.get("size")),
        "length": properties.get("len"),
        "shape": _stringify_metric(properties.get("array_shape")),
        "ndim": properties.get("array_ndim"),
        "dtype": _normalize_dtype(info.get("numpy_type")),
        "preview": preview,
        "columns": "",
        "dtypes": "",
        "range": "",
        "channels": "",
    }

    if variable["kind"] == "dataframe" and preview.startswith("Column names: "):
        variable["columns"] = _clip_preview(preview.split(": ", 1)[1])
        variable["preview"] = ""

    return variable


def _infer_variable_kind(info, properties):
    if properties.get("is_data_frame"):
        return "dataframe"
    if properties.get("is_series"):
        return "series"
    if properties.get("is_image"):
        return "image"
    if properties.get("is_array"):
        return "array"
    if properties.get("is_dict"):
        return "dict"
    if properties.get("is_list"):
        return "list"
    if properties.get("is_set"):
        return "set"

    python_type = info.get("python_type", "")
    if python_type == "tuple":
        return "tuple"
    if python_type in SCALAR_PYTHON_TYPES:
        return "scalar"
    return "object"


def _normalize_console_text(text):
    text = ANSI_ESCAPE_RE.sub("", text or "")
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    return text.rstrip()


def _extract_latest_error_lines(lines):
    if not lines:
        return []

    for index in range(len(lines) - 1, -1, -1):
        if TRACEBACK_START_RE.match(lines[index]):
            end = _find_prompt_after(lines, index)
            candidate = _strip_surrounding_blank_lines(lines[index:end])
            return _trim_leading_error_lines(candidate)

    for index in range(len(lines) - 1, -1, -1):
        if ERROR_LINE_RE.match(lines[index].strip()):
            start = _find_prompt_before(lines, index) + 1
            end = _find_prompt_after(lines, index)
            candidate = _strip_surrounding_blank_lines(lines[start:end])
            return _trim_leading_error_lines(candidate)

    return []


def _find_prompt_before(lines, index):
    for current in range(index - 1, -1, -1):
        if PROMPT_LINE_RE.match(lines[current]):
            return current
    return -1


def _find_prompt_after(lines, index):
    for current in range(index + 1, len(lines)):
        if PROMPT_LINE_RE.match(lines[current]):
            return current
    return len(lines)


def _remove_contiguous_subsequence(lines, subsequence):
    if not subsequence or len(subsequence) > len(lines):
        return list(lines)

    for start in range(len(lines) - len(subsequence), -1, -1):
        if lines[start:start + len(subsequence)] == subsequence:
            remaining = lines[:start] + lines[start + len(subsequence):]
            return _strip_surrounding_blank_lines(remaining)
    return list(lines)


def _trim_leading_error_lines(lines):
    for index, line in enumerate(lines):
        if (
            TRACEBACK_START_RE.match(line)
            or ERROR_DIVIDER_RE.match(line)
            or TRACEBACK_TITLE_RE.match(line)
            or ERROR_LINE_RE.match(line)
        ):
            return _strip_surrounding_blank_lines(lines[index:])
    return _strip_surrounding_blank_lines(lines)


def _strip_surrounding_blank_lines(lines):
    if isinstance(lines, str):
        lines = lines.split("\n")

    start = 0
    end = len(lines)
    while start < end and not str(lines[start]).strip():
        start += 1
    while end > start and not str(lines[end - 1]).strip():
        end -= 1
    return list(lines[start:end])


def _clip_tail_text(text_or_lines, max_chars):
    if isinstance(text_or_lines, list):
        text = "\n".join(text_or_lines).strip()
    else:
        text = (text_or_lines or "").strip()

    if len(text) <= max_chars:
        return text
    return "... (truncated)\n" + text[-max_chars:]


def _clip_runtime_section(section, max_chars):
    if len(section) <= max_chars:
        return section
    clipped = section[:max_chars].rstrip()
    if not clipped.endswith("... (truncated)"):
        clipped += "\n... (truncated)"
    return clipped


def _clip_preview(preview):
    preview = " ".join(str(preview or "").split())
    if len(preview) <= MAX_RUNTIME_PREVIEW_CHARS:
        return preview
    return preview[:MAX_RUNTIME_PREVIEW_CHARS].rstrip() + "..."


def _extract_traceback_code_line(lines, index):
    """Return the best code line associated with one traceback frame."""
    for next_index in range(index + 1, min(index + 4, len(lines))):
        stripped = lines[next_index].strip()
        if not stripped:
            continue
        if (
            TRACEBACK_FILE_FRAME_RE.match(stripped)
            or TRACEBACK_CELL_FRAME_RE.match(stripped)
            or TRACEBACK_START_RE.match(stripped)
            or ERROR_LINE_RE.match(stripped)
        ):
            break
        return _clean_traceback_code_line(stripped)
    return ""


def _clean_traceback_code_line(line):
    """Normalize one traceback code line for prompt use."""
    had_arrow = bool(re.match(r"^\s*-+>\s*", line))
    cleaned = re.sub(r"^\s*-+>\s*", "", line.strip())
    if had_arrow or re.match(r"^\d+\s{2,}", cleaned):
        cleaned = re.sub(r"^\d+\s+", "", cleaned)
    return cleaned.strip()


def _summarize_live_value(value):
    if isinstance(value, str):
        return _clip_preview(repr(value))
    if isinstance(value, bytes):
        return _clip_preview(repr(value))
    if isinstance(value, dict):
        items = list(value.items())[:5]
        preview = "{" + ", ".join(
            f"{repr(key)}: {repr(item)}" for key, item in items
        )
        if len(value) > len(items):
            preview += ", ..."
        preview += "}"
        return _clip_preview(preview)
    if isinstance(value, (list, tuple)):
        items = [repr(item) for item in value[:5]]
        preview = ", ".join(items)
        if len(value) > len(items):
            preview += ", ..."
        opening, closing = ("[", "]") if isinstance(value, list) else ("(", ")")
        return _clip_preview(f"{opening}{preview}{closing}")
    if isinstance(value, set):
        items = [repr(item) for item in list(value)[:5]]
        preview = ", ".join(items)
        if len(value) > len(items):
            preview += ", ..."
        return _clip_preview("{" + preview + "}")
    return _clip_preview(repr(value))


def _is_pandas_dataframe(value):
    value_type = type(value)
    return (
        value_type.__name__ == "DataFrame"
        and value_type.__module__.split(".", 1)[0] == "pandas"
    )


def _is_pandas_series(value):
    value_type = type(value)
    return (
        value_type.__name__ == "Series"
        and value_type.__module__.split(".", 1)[0] == "pandas"
    )


def _is_array_like(value):
    return hasattr(value, "shape") and hasattr(value, "dtype") and hasattr(value, "size")


def _array_preview(value):
    try:
        flattened = value.ravel()[:6]
        if hasattr(flattened, "tolist"):
            return flattened.tolist()
        return list(flattened)
    except Exception:
        return repr(value)


def _array_range(value):
    try:
        if int(getattr(value, "size", 0) or 0) == 0:
            return ""
        minimum = value.min()
        maximum = value.max()
        return f"{_stringify_scalar(minimum)}..{_stringify_scalar(maximum)}"
    except Exception:
        return ""


def _array_channels(shape):
    if not shape or len(shape) != 3:
        return ""
    last_dim = shape[-1]
    if last_dim in {1, 3, 4}:
        return str(last_dim)
    return ""


def _summarize_sequence_array(value, kind, fallback_type):
    """Summarize list-backed array data when Spyder returns decoded containers."""
    shape = _sequence_shape(value)
    if not shape:
        return {}

    flattened = _flatten_sequence_scalars(value, limit=64)
    if not flattened:
        return {}

    preview = flattened[:6]
    summary = {
        "kind": "image" if kind == "image" else "array",
        "type": fallback_type or type(value).__name__,
        "shape": str(shape),
        "preview": _clip_preview(repr(preview)),
    }

    dtype = _infer_sequence_dtype(flattened, fallback_type)
    if dtype:
        summary["dtype"] = dtype

    value_range = _sequence_range(flattened)
    if value_range:
        summary["range"] = value_range

    channels = _array_channels(shape)
    if channels:
        summary["channels"] = channels

    return summary


def _sequence_shape(value):
    """Return a tuple shape for a rectangular nested sequence."""
    if not isinstance(value, (list, tuple)):
        return ()
    if not value:
        return (0,)

    child_shapes = []
    for item in value:
        if isinstance(item, (list, tuple)):
            child_shape = _sequence_shape(item)
            if not child_shape:
                return ()
            child_shapes.append(child_shape)
        else:
            child_shapes.append(())

    first_shape = child_shapes[0]
    if any(shape != first_shape for shape in child_shapes[1:]):
        return ()
    return (len(value),) + first_shape


def _flatten_sequence_scalars(value, limit=64):
    """Flatten nested sequences into scalar preview values."""
    flattened = []

    def _visit(item):
        if len(flattened) >= limit:
            return
        if isinstance(item, (list, tuple)):
            for child in item:
                _visit(child)
                if len(flattened) >= limit:
                    return
            return
        flattened.append(item)

    _visit(value)
    return flattened


def _infer_sequence_dtype(values, fallback_type=""):
    """Infer a readable dtype for nested-sequence array summaries."""
    lowered = str(fallback_type or "").lower()
    if " of " in lowered:
        return fallback_type.split(" of ", 1)[1].strip()
    if "dtype=" in lowered:
        return fallback_type.split("dtype=", 1)[1].strip(" )")

    if not values:
        return ""
    if all(isinstance(item, bool) for item in values):
        return "bool"
    if all(isinstance(item, int) and not isinstance(item, bool) for item in values):
        return "int"
    if all(isinstance(item, (int, float)) and not isinstance(item, bool) for item in values):
        return "float"
    if all(isinstance(item, complex) for item in values):
        return "complex"
    if all(isinstance(item, str) for item in values):
        return "str"
    return ""


def _sequence_range(values):
    """Return min/max for numeric flattened sequence values."""
    numeric = [
        item for item in values
        if isinstance(item, (int, float)) and not isinstance(item, bool)
    ]
    if not numeric:
        return ""
    return f"{_stringify_scalar(min(numeric))}..{_stringify_scalar(max(numeric))}"


def _stringify_scalar(value):
    if hasattr(value, "item"):
        try:
            value = value.item()
        except Exception:
            pass
    return str(value)


def _bounded_int(value, default, minimum, maximum):
    try:
        numeric = int(value)
    except (TypeError, ValueError):
        numeric = default
    return max(minimum, min(numeric, maximum))


def _normalize_dtype(dtype):
    if not dtype or dtype == "Unknown":
        return ""
    return str(dtype)


def _stringify_metric(value):
    if value in ("", None):
        return ""
    return str(value)


def _safe_get_cwd(shellwidget):
    if shellwidget is None or not hasattr(shellwidget, "get_cwd"):
        return ""
    try:
        return shellwidget.get_cwd() or ""
    except Exception:
        return ""


def _timestamp_now():
    return datetime.now().isoformat(timespec="seconds")
