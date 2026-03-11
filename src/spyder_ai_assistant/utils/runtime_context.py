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
    "working_directory",
    "last_refreshed_at",
    "console_output",
    "latest_error",
    "variables",
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
        "working_directory": "",
        "last_refreshed_at": "",
        "console_output": "",
        "latest_error": "",
        "variables": [],
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

    def __init__(self, parent=None):
        super().__init__(parent)
        self._ipython_console_plugin = None
        self._variable_explorer_plugin = None
        self._current_shell_id = None
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
        self._tracked_shell_ids.clear()
        self._snapshots.clear()
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
        shellwidget = self._safe_get_current_shellwidget()
        if shellwidget is None:
            return make_empty_runtime_context(
                status="unavailable",
                detail="No active IPython console is available.",
            )

        self._track_shellwidget(shellwidget)
        self._switch_current_shellwidget(shellwidget)
        self._refresh_console_snapshot(shellwidget, reason="context-request")
        return self._build_public_context(shellwidget)

    def execute_request(self, request):
        """Execute one read-only runtime inspection request."""
        tool = (request or {}).get("tool", "")
        args = (request or {}).get("args") or {}
        logger.info("Executing runtime request: %s", tool)

        shellwidget = self._safe_get_current_shellwidget()
        if shellwidget is None:
            return {
                "ok": False,
                "tool": tool,
                "source": "unavailable",
                "shell_status": "unavailable",
                "shell_detail": "No active IPython console is available.",
                "working_directory": "",
                "last_refreshed_at": "",
                "payload": {},
                "query_note": "",
                "error": "No active IPython console is available.",
            }

        runtime_context = self.get_current_context()

        if tool == "runtime.status":
            return self._build_status_result(tool, runtime_context)
        if tool == "runtime.get_latest_error":
            return self._build_latest_error_result(tool, runtime_context)
        if tool == "runtime.get_console_tail":
            return self._build_console_result(tool, runtime_context, args)
        if tool == "runtime.list_variables":
            return self._build_list_variables_result(
                tool, shellwidget, runtime_context, args
            )
        if tool == "runtime.inspect_variable":
            name = str(args.get("name", "")).strip()
            names = [name] if name else []
            return self._build_inspect_variables_result(
                tool, shellwidget, runtime_context, names
            )
        if tool == "runtime.inspect_variables":
            raw_names = args.get("names", [])
            if isinstance(raw_names, str):
                raw_names = [raw_names]
            names = [
                str(name).strip()
                for name in raw_names[:MAX_RUNTIME_REQUEST_NAMES]
                if str(name).strip()
            ]
            return self._build_inspect_variables_result(
                tool, shellwidget, runtime_context, names
            )

        return {
            "ok": False,
            "tool": tool,
            "source": "unavailable",
            "shell_status": runtime_context.get("status", "unavailable"),
            "shell_detail": runtime_context.get("status_detail", ""),
            "working_directory": runtime_context.get("working_directory", ""),
            "last_refreshed_at": runtime_context.get("last_refreshed_at", ""),
            "payload": {},
            "query_note": "",
            "error": f"Unsupported runtime tool: {tool}",
        }

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
        if self._current_shell_id == shell_id:
            self._current_shell_id = None
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
        current_shell = shellwidget
        if current_shell is None and self._current_shell_id is not None:
            snapshot = self._snapshots.get(self._current_shell_id)
            current_shell = snapshot.get("_shellwidget") if snapshot else None

        self.sig_current_context_changed.emit(
            self._build_public_context(current_shell)
        )

    def _safe_get_current_shellwidget(self):
        if self._ipython_console_plugin is None:
            return None
        try:
            return self._ipython_console_plugin.get_current_shellwidget()
        except Exception:
            return None

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

    def _build_status_result(self, tool, runtime_context):
        return {
            "ok": True,
            "tool": tool,
            "source": "snapshot",
            "shell_status": runtime_context.get("status", "unavailable"),
            "shell_detail": runtime_context.get("status_detail", ""),
            "working_directory": runtime_context.get("working_directory", ""),
            "last_refreshed_at": runtime_context.get("last_refreshed_at", ""),
            "payload": {
                "stale": runtime_context.get("stale", False),
            },
            "query_note": "",
            "error": "",
        }

    def _build_latest_error_result(self, tool, runtime_context):
        latest_error = runtime_context.get("latest_error", "")
        return {
            "ok": bool(latest_error),
            "tool": tool,
            "source": "snapshot",
            "shell_status": runtime_context.get("status", "unavailable"),
            "shell_detail": runtime_context.get("status_detail", ""),
            "working_directory": runtime_context.get("working_directory", ""),
            "last_refreshed_at": runtime_context.get("last_refreshed_at", ""),
            "payload": {"latest_error": latest_error},
            "query_note": "",
            "error": "" if latest_error else "No latest error is available.",
        }

    def _build_console_result(self, tool, runtime_context, args):
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
        return {
            "ok": bool(console_output),
            "tool": tool,
            "source": "snapshot",
            "shell_status": runtime_context.get("status", "unavailable"),
            "shell_detail": runtime_context.get("status_detail", ""),
            "working_directory": runtime_context.get("working_directory", ""),
            "last_refreshed_at": runtime_context.get("last_refreshed_at", ""),
            "payload": {"console_output": console_output},
            "query_note": "",
            "error": "" if console_output else "No recent console output is available.",
        }

    def _build_list_variables_result(self, tool, shellwidget, runtime_context, args):
        limit = _bounded_int(
            args.get("limit"),
            default=MAX_RUNTIME_REQUEST_VARIABLES,
            minimum=1,
            maximum=MAX_RUNTIME_VARIABLES,
        )
        namespace_view, var_properties, source, query_note, query_error = (
            self._query_namespace_state(shellwidget, tool)
        )
        variables = build_runtime_variable_summaries(
            namespace_view,
            var_properties,
        )[:limit]
        return {
            "ok": bool(variables),
            "tool": tool,
            "source": source,
            "shell_status": runtime_context.get("status", "unavailable"),
            "shell_detail": runtime_context.get("status_detail", ""),
            "working_directory": runtime_context.get("working_directory", ""),
            "last_refreshed_at": runtime_context.get("last_refreshed_at", ""),
            "payload": {
                "count": len(variables),
                "variables": variables,
            },
            "query_note": query_note,
            "error": query_error if not variables else "",
        }

    def _build_inspect_variables_result(self, tool, shellwidget, runtime_context, names):
        if not names:
            return {
                "ok": False,
                "tool": tool,
                "source": "unavailable",
                "shell_status": runtime_context.get("status", "unavailable"),
                "shell_detail": runtime_context.get("status_detail", ""),
                "working_directory": runtime_context.get("working_directory", ""),
                "last_refreshed_at": runtime_context.get("last_refreshed_at", ""),
                "payload": {},
                "query_note": "",
                "error": "No variable name was provided.",
            }

        namespace_view, var_properties, source, query_note, query_error = (
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
                variable = self._attach_live_preview(shellwidget, variable)
            found.append(variable)

        error = ""
        if not found:
            if missing:
                error = f"Variables not found: {', '.join(missing)}"
            elif query_error:
                error = query_error
            else:
                error = "No matching variables were found."

        return {
            "ok": bool(found),
            "tool": tool,
            "source": source,
            "shell_status": runtime_context.get("status", "unavailable"),
            "shell_detail": runtime_context.get("status_detail", ""),
            "working_directory": runtime_context.get("working_directory", ""),
            "last_refreshed_at": runtime_context.get("last_refreshed_at", ""),
            "payload": {
                "variables": found,
                "missing": missing,
            },
            "query_note": query_note,
            "error": error,
        }

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

    def _attach_live_preview(self, shellwidget, variable):
        kind = variable.get("kind")
        if kind not in {"scalar", "list", "tuple", "set", "dict"}:
            return variable

        length = variable.get("length")
        if length not in ("", None):
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

        live_preview = _summarize_live_value(value)
        if live_preview:
            updated = dict(variable)
            updated["preview"] = live_preview
            return updated
        return variable


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
    if preview:
        label = "value" if kind == "scalar" else "preview"
        parts.append(f"{label}={preview}")
    return "; ".join(parts)


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
