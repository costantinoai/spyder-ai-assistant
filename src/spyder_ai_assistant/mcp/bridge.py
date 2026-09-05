"""Qt main-thread bridge for Spyder MCP requests."""

from __future__ import annotations

import hashlib
import logging
import os
import threading
from dataclasses import dataclass, field
from typing import Any

from qtpy.QtCore import QObject, Qt, QThread, Signal, Slot
from qtpy.QtGui import QTextCursor

from spyder.api.plugins import Plugins

from spyder_ai_assistant.utils.code_apply import (
    build_code_apply_plan,
)

logger = logging.getLogger(__name__)
BRIDGE_REQUEST_TIMEOUT_SECS = 30.0


@dataclass
class _BridgeRequest:
    """One queued bridge invocation."""

    method_name: str
    kwargs: dict[str, Any] = field(default_factory=dict)
    result: Any = None
    error: Exception | None = None
    completed: threading.Event = field(default_factory=threading.Event)
    lock: Any = field(default_factory=threading.Lock)
    cancelled: bool = False
    started: bool = False


class SpyderMCPBridge(QObject):
    """Marshal Spyder state access back onto the Qt main thread."""

    sig_invoke = Signal(object)

    def __init__(self, plugin, runtime_context, context_service, project_tools,
                 parent=None):
        super().__init__(parent or plugin)
        self._plugin = plugin
        self._runtime_context = runtime_context
        self._project_tools = project_tools
        # Read handlers call the context service unconditionally, so it is a
        # required collaborator rather than an optional default.
        if context_service is None:
            raise ValueError("SpyderMCPBridge requires a context service")
        self._context_service = context_service
        self.moveToThread(plugin.thread())
        self.sig_invoke.connect(
            self._dispatch_request,
            Qt.QueuedConnection,
        )

    def invoke_main_thread(self, method_name, **kwargs):
        """Run one bridge operation on the Qt main thread."""
        request = _BridgeRequest(method_name=method_name, kwargs=dict(kwargs))
        if QThread.currentThread() is self.thread():
            self._dispatch_request(request)
        else:
            self.sig_invoke.emit(request)
            if not request.completed.wait(BRIDGE_REQUEST_TIMEOUT_SECS):
                with request.lock:
                    request.cancelled = True
                    started = request.started
                raise TimeoutError(
                    "Spyder did not finish the MCP request in time. "
                    + ("The operation already started; inspect state before retrying."
                       if started else "The queued operation was cancelled.")
                )

        if request.error is not None:
            raise request.error
        return request.result

    def get_current_file(self):
        """Return the active Spyder editor context."""
        return self.invoke_main_thread("get_current_file")

    def get_open_files(self):
        """Return summaries for other open Spyder editor tabs."""
        return self.invoke_main_thread("get_open_files")

    def get_project_tree(self):
        """Return the active Spyder project tree."""
        return self.invoke_main_thread("get_project_tree")

    def execute_project_request(self, tool_name, **args):
        """Run one read-only project/git tool on the main thread."""
        return self.invoke_main_thread(
            "execute_project_request", tool_name=tool_name, args=dict(args)
        )

    def execute_runtime_request(self, tool_name, **args):
        """Execute one runtime-context request on the main thread."""
        return self.invoke_main_thread(
            "execute_runtime_request",
            tool_name=tool_name,
            args=args,
        )

    def preview_file_edit(
        self,
        code,
        *,
        filename="",
        requested_mode="insert",
        cursor_position=None,
        selection_start=None,
        selection_end=None,
    ):
        """Preview one editor mutation without changing the document."""
        return self.invoke_main_thread(
            "preview_file_edit",
            code=code,
            filename=filename,
            requested_mode=requested_mode,
            cursor_position=cursor_position,
            selection_start=selection_start,
            selection_end=selection_end,
        )

    def apply_file_edit(
        self,
        code,
        *,
        filename="",
        requested_mode="insert",
        cursor_position=None,
        selection_start=None,
        selection_end=None,
        expected_document_sha256="",
        confirm=False,
        save=False,
    ):
        """Apply one explicit editor mutation on the main thread."""
        return self.invoke_main_thread(
            "apply_file_edit",
            code=code,
            filename=filename,
            requested_mode=requested_mode,
            cursor_position=cursor_position,
            selection_start=selection_start,
            selection_end=selection_end,
            expected_document_sha256=expected_document_sha256,
            confirm=confirm,
            save=save,
        )

    @Slot(object)
    def _dispatch_request(self, request):
        """Execute one queued request on the QObject thread."""
        with request.lock:
            if request.cancelled:
                request.completed.set()
                return
            request.started = True
        try:
            request.result = self._execute(request.method_name, **request.kwargs)
        except Exception as error:
            request.error = error
            logger.exception(
                "Spyder MCP bridge request failed: %s",
                request.method_name,
            )
        finally:
            request.completed.set()

    def _execute(self, method_name, **kwargs):
        handler = getattr(self, f"_handle_{method_name}", None)
        if handler is None:
            raise ValueError(f"Unsupported bridge method: {method_name}")
        return handler(**kwargs)

    def _handle_get_current_file(self):
        return self._context_service.get_current_file()

    def _handle_get_open_files(self):
        return self._context_service.get_open_files()

    def _handle_get_project_tree(self):
        return self._context_service.get_project_tree()

    def _handle_execute_project_request(self, tool_name, args):
        request = {"tool": str(tool_name or "").strip(), "args": dict(args or {})}
        return self._project_tools.execute_request(request)

    def _handle_execute_runtime_request(self, tool_name, args):
        request = {
            "tool": str(tool_name or "").strip(),
            "args": dict(args or {}),
        }
        return self._runtime_context.execute_request(request)

    def _handle_preview_file_edit(
        self,
        code,
        filename="",
        requested_mode="insert",
        cursor_position=None,
        selection_start=None,
        selection_end=None,
    ):
        editor_plugin = self._get_plugin(Plugins.Editor)
        target = self._resolve_editor_target(editor_plugin, filename=filename)
        if target["editor"] is None:
            return {
                "ok": False,
                "source": "unavailable",
                "filename": target["filename"],
                "error": target["error"],
            }

        document_text = self._read_editor_text(target["editor"])
        positions = self._resolve_editor_positions(
            target["editor"],
            cursor_position=cursor_position,
            selection_start=selection_start,
            selection_end=selection_end,
        )
        plan = build_code_apply_plan(
            document_text=document_text,
            code=code,
            cursor_position=positions["cursor_position"],
            selection_start=positions["selection_start"],
            selection_end=positions["selection_end"],
            requested_mode=requested_mode,
        )
        return self._serialize_edit_plan(
            target["filename"],
            document_text,
            plan,
            source="preview",
            ok=True,
            applied=False,
            save_requested=False,
            saved=False,
            error="",
        )

    def _handle_apply_file_edit(
        self,
        code,
        filename="",
        requested_mode="insert",
        cursor_position=None,
        selection_start=None,
        selection_end=None,
        expected_document_sha256="",
        confirm=False,
        save=False,
    ):
        if not bool(confirm):
            return {
                "ok": False,
                "source": "unavailable",
                "filename": str(filename or "").strip(),
                "error": (
                    "Explicit confirmation is required. Call preview_file_edit, "
                    "then call apply_file_edit with confirm=true and the "
                    "expected_document_sha256 returned by the preview."
                ),
            }

        positions, position_error = _required_apply_positions(
            cursor_position=cursor_position,
            selection_start=selection_start,
            selection_end=selection_end,
        )
        if position_error:
            return {
                "ok": False,
                "source": "unavailable",
                "filename": str(filename or "").strip(),
                "error": position_error,
            }

        editor_plugin = self._get_plugin(Plugins.Editor)
        target = self._resolve_editor_target(editor_plugin, filename=filename)
        if target["editor"] is None:
            return {
                "ok": False,
                "source": "unavailable",
                "filename": target["filename"],
                "error": target["error"],
            }

        document_text = self._read_editor_text(target["editor"])
        current_sha = self._hash_text(document_text)
        expected_sha = str(expected_document_sha256 or "").strip()
        plan = build_code_apply_plan(
            document_text=document_text,
            code=code,
            cursor_position=positions["cursor_position"],
            selection_start=positions["selection_start"],
            selection_end=positions["selection_end"],
            requested_mode=requested_mode,
        )

        if not expected_sha:
            return self._serialize_edit_plan(
                target["filename"],
                document_text,
                plan,
                source="conflict",
                ok=False,
                applied=False,
                save_requested=bool(save),
                saved=False,
                error=(
                    "expected_document_sha256 is required. Preview the edit "
                    "first and resend the returned hash when applying."
                ),
            )

        if expected_sha != current_sha:
            return self._serialize_edit_plan(
                target["filename"],
                document_text,
                plan,
                source="conflict",
                ok=False,
                applied=False,
                save_requested=bool(save),
                saved=False,
                error=(
                    "The target document changed after preview. Refresh the "
                    "preview and confirm the new diff before applying."
                ),
            )

        self._apply_code_into_editor(target["editor"], code, plan)
        updated_text = self._read_editor_text(target["editor"])
        saved = False
        save_error = ""
        if save:
            saved, save_error = self._save_editor_document(
                editor_plugin,
                target["filename"],
            )

        ok = not save or saved
        error = save_error if save and not saved else ""
        return self._serialize_edit_plan(
            target["filename"],
            updated_text,
            plan,
            source="applied",
            ok=ok,
            applied=True,
            save_requested=bool(save),
            saved=bool(saved),
            error=error,
            save_error=save_error,
        )

    def _get_plugin(self, plugin_name):
        """Return one optional Spyder plugin without raising."""
        try:
            return self._plugin.get_plugin(plugin_name, error=False)
        except TypeError:
            try:
                return self._plugin.get_plugin(plugin_name)
            except Exception:
                return None
        except Exception:
            return None

    def _resolve_editor_target(self, editor_plugin, filename=""):
        """Return the current or explicitly addressed Spyder editor."""
        if editor_plugin is None:
            return {
                "editor": None,
                "filename": str(filename or "").strip(),
                "error": "Spyder's Editor plugin is not available.",
            }

        requested_filename = str(filename or "").strip()
        if requested_filename:
            normalized_filename = os.path.abspath(requested_filename)
            editor = None
            try:
                editor = editor_plugin.get_codeeditor_for_filename(normalized_filename)
            except Exception:
                editor = None

            if editor is None and os.path.isfile(normalized_filename):
                try:
                    editor_plugin.load_edit(normalized_filename)
                except Exception:
                    editor = None
                else:
                    try:
                        editor = editor_plugin.get_codeeditor_for_filename(
                            normalized_filename
                        )
                    except Exception:
                        editor = None

            if editor is None:
                return {
                    "editor": None,
                    "filename": normalized_filename,
                    "error": (
                        "The requested file is not available in Spyder's "
                        "editor and could not be opened."
                    ),
                }

            return {
                "editor": editor,
                "filename": normalized_filename,
                "error": "",
            }

        editor = editor_plugin.get_current_editor()
        current_filename = ""
        with_filename = editor_plugin.get_current_filename()
        if with_filename:
            current_filename = str(with_filename)
        elif editor is not None:
            current_filename = str(getattr(editor, "filename", "") or "")
        return {
            "editor": editor,
            "filename": current_filename or "Untitled",
            "error": (
                ""
                if editor is not None
                else "No active Spyder editor is available."
            ),
        }

    @staticmethod
    def _read_editor_text(editor):
        """Return the current text content from one editor widget."""
        try:
            return editor.toPlainText() or ""
        except Exception:
            return ""

    @staticmethod
    def _resolve_editor_positions(
        editor,
        *,
        cursor_position=None,
        selection_start=None,
        selection_end=None,
    ):
        """Resolve explicit or current editor cursor/selection positions."""
        cursor = editor.textCursor()
        current_cursor = int(cursor.position())
        current_start = int(cursor.selectionStart())
        current_end = int(cursor.selectionEnd())
        return {
            "cursor_position": _optional_position(cursor_position, current_cursor),
            "selection_start": _optional_position(selection_start, current_start),
            "selection_end": _optional_position(selection_end, current_end),
        }

    def _serialize_edit_plan(
        self,
        filename,
        document_text,
        plan,
        *,
        source,
        ok,
        applied,
        save_requested,
        saved,
        error,
        save_error="",
    ):
        """Return one serialized MCP response for an editor preview/apply."""
        document_sha = self._hash_text(document_text)
        updated_text = plan.get("updated_text", document_text)
        updated_sha = self._hash_text(updated_text)
        return {
            "ok": bool(ok),
            "source": str(source or ""),
            "filename": str(filename or ""),
            "document_sha256": document_sha,
            "updated_document_sha256": updated_sha,
            "document_length": len(document_text or ""),
            "updated_document_length": len(updated_text or ""),
            "requested_mode": plan.get("requested_mode", ""),
            "effective_mode": plan.get("effective_mode", ""),
            "mode_label": plan.get("mode_label", ""),
            "has_selection": bool(plan.get("has_selection", False)),
            "cursor_position": int(plan.get("cursor_position", 0) or 0),
            "selection_start": int(plan.get("selection_start", 0) or 0),
            "selection_end": int(plan.get("selection_end", 0) or 0),
            "selection_preview": plan.get("selection_preview", ""),
            "code_preview": plan.get("code_preview", ""),
            "diff_text": plan.get("diff_text", ""),
            "line_delta": int(plan.get("line_delta", 0) or 0),
            "note": plan.get("note", ""),
            "applied": bool(applied),
            "save_requested": bool(save_requested),
            "saved": bool(saved),
            "save_error": str(save_error or ""),
            "error": str(error or ""),
        }

    def _save_editor_document(self, editor_plugin, filename):
        """Save one edited file through Spyder's editor stack."""
        normalized_filename = str(filename or "").strip()
        if not normalized_filename or not os.path.isfile(normalized_filename):
            return (
                False,
                "The edited document does not map to an existing file on disk, "
                "so Spyder cannot save it automatically.",
            )

        editor_widget = editor_plugin.get_widget() if editor_plugin is not None else None
        editorstacks = getattr(editor_widget, "editorstacks", []) or []
        for editorstack in editorstacks:
            try:
                index = editorstack.get_index_from_filename(normalized_filename)
            except Exception:
                index = None
            if index is None:
                continue
            try:
                saved = bool(editorstack.save(index=index, force=False))
            except Exception as error:
                return False, f"Spyder failed to save the edited file: {error}"
            if saved:
                return True, ""
            return False, "Spyder reported that the edited file could not be saved."

        return False, "Spyder could not find an editor stack for the edited file."

    @staticmethod
    def _apply_code_into_editor(editor, code, plan):
        """Apply one previewed code change directly to an editor widget."""
        cursor = editor.textCursor()
        cursor.beginEditBlock()
        try:
            if plan["effective_mode"] == "replace" and plan["has_selection"]:
                cursor.setPosition(plan["selection_start"])
                cursor.setPosition(
                    plan["selection_end"],
                    QTextCursor.KeepAnchor,
                )
                cursor.insertText(code)
            else:
                cursor.clearSelection()
                cursor.setPosition(plan["cursor_position"])
                editor.setTextCursor(cursor)
                cursor.insertText(code)
        finally:
            cursor.endEditBlock()
            editor.setTextCursor(cursor)

    @staticmethod
    def _hash_text(text):
        """Return one stable hash for compare-and-apply editor writes."""
        return hashlib.sha256(str(text or "").encode("utf-8")).hexdigest()


def _optional_position(value, fallback):
    """Return one explicit editor position or the provided fallback."""
    try:
        normalized = int(value)
    except (TypeError, ValueError):
        return int(fallback)
    if normalized < 0:
        return int(fallback)
    return normalized


def _required_apply_positions(
    *,
    cursor_position=None,
    selection_start=None,
    selection_end=None,
):
    """Return the previewed editor positions required for apply confirmation."""
    positions = {}
    for name, value in (
        ("cursor_position", cursor_position),
        ("selection_start", selection_start),
        ("selection_end", selection_end),
    ):
        try:
            normalized = int(value)
        except (TypeError, ValueError):
            normalized = -1
        if normalized < 0:
            return None, (
                "Explicit cursor_position, selection_start, and selection_end "
                "are required when applying an MCP editor mutation. Call "
                "preview_file_edit first, then resend the exact positions "
                "returned by the preview together with confirm=true and "
                "expected_document_sha256."
            )
        positions[name] = normalized
    return positions, ""
