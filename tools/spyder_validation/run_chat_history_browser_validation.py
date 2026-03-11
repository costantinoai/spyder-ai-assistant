"""Validate the chat history browser in a real Spyder session."""

from __future__ import annotations

import json
import shutil
import traceback

from qtpy.QtCore import QTimer
from qtpy.QtWidgets import QApplication, QMessageBox

from spyder_ai_assistant.widgets.chat_widget import ChatSession
from spyder_ai_assistant.widgets.session_history_dialog import SessionHistoryDialog
from tools.spyder_validation.common import (
    artifact_path,
    finalize,
    get_ai_plugin,
    get_chat_widget,
    get_projects_plugin,
    record_validation_result,
    run_spyder_validation,
    wait_for,
)


CONFIG_DIR = artifact_path("configs", "chat-history-browser")
PROJECT_DIR = artifact_path("fixtures", "chat-history-project")
RESULT_PATH = artifact_path("results", "chat-history-browser-validation.json")
STATE_PATH = PROJECT_DIR / ".spyproject/ai-assistant/chat-sessions.json"


def ensure_project_open(window):
    """Open or create the project used for history-browser validation."""
    projects = get_projects_plugin(window)
    if PROJECT_DIR.exists():
        shutil.rmtree(PROJECT_DIR)
    PROJECT_DIR.mkdir(parents=True, exist_ok=True)
    if not (PROJECT_DIR / ".spyproject").exists():
        projects.create_project(str(PROJECT_DIR))
    else:
        projects.open_project(str(PROJECT_DIR))

    opened = wait_for(
        lambda: projects.get_active_project_path() == str(PROJECT_DIR),
        timeout_ms=30000,
        step_ms=100,
    )
    if not opened:
        raise RuntimeError("Spyder project did not open")


def seed_session(widget, title, user_text, assistant_text):
    """Create one deterministic chat session without depending on model I/O."""
    session = ChatSession(
        parent=widget._tab_widget,
        title=title,
        messages=[
            {"role": "user", "content": user_text},
            {"role": "assistant", "content": assistant_text},
        ],
    )
    widget._add_session(session, notify=False)
    session.display.rebuild_from_messages(session.messages)
    return session


def _extract_browser_rows(dialog):
    """Return the visible browser rows from the dialog table."""
    rows = []
    for row_index in range(dialog.table.rowCount()):
        rows.append({
            "title": dialog.table.item(row_index, 0).text(),
            "session_id": dialog.table.item(row_index, 0).data(0x0100),
            "status": dialog.table.item(row_index, 3).text(),
        })
    return rows


def _find_history_dialog():
    """Return the active history dialog, if it exists."""
    app = QApplication.instance()
    for widget in app.topLevelWidgets():
        if isinstance(widget, SessionHistoryDialog) and widget.isVisible():
            return widget
    return None


def run_history_action(widget, session_id, action):
    """Drive the modal history browser through the real button path."""
    results = {
        "action": action,
        "session_id": session_id,
        "dialog_seen": False,
        "rows": [],
    }

    original_question = QMessageBox.question
    if action == "delete":
        QMessageBox.question = lambda *args, **kwargs: QMessageBox.Yes

    def drive_dialog():
        dialog = _find_history_dialog()
        if dialog is None:
            QTimer.singleShot(50, drive_dialog)
            return

        results["dialog_seen"] = True
        results["rows"] = _extract_browser_rows(dialog)
        if not dialog.select_session_id(session_id):
            results["error"] = f"Session row not found in dialog: {session_id}"
            dialog.reject()
            return

        button = {
            "open": dialog.open_btn,
            "duplicate": dialog.duplicate_btn,
            "delete": dialog.delete_btn,
        }.get(action)
        if button is None:
            results["error"] = f"Unsupported action: {action}"
            dialog.reject()
            return

        button.click()

    try:
        QTimer.singleShot(0, drive_dialog)
        widget.history_btn.click()
    finally:
        QMessageBox.question = original_question

    if not results["dialog_seen"]:
        raise RuntimeError(f"History dialog did not open for action: {action}")
    if results.get("error"):
        raise RuntimeError(results["error"])

    return results


def run_validation(window):
    """Exercise history browsing, reopening, duplication, and deletion."""
    results = {
        "errors": [],
        "project_path": str(PROJECT_DIR),
        "state_path": str(STATE_PATH),
    }

    try:
        ensure_project_open(window)
        plugin = get_ai_plugin(window)
        widget = get_chat_widget(window)

        widget._clear_all_tabs()
        widget._history_sessions = []
        alpha = seed_session(
            widget,
            "History Alpha",
            "Summarize alpha session",
            "Alpha session answer",
        )
        beta = seed_session(
            widget,
            "History Beta",
            "Summarize beta session",
            "Beta session answer",
        )
        widget._tab_widget.setCurrentIndex(0)
        widget._notify_session_state_changed("history-browser-seed")
        plugin._flush_chat_session_state()

        beta_index = widget._sessions.index_of(widget._tab_widget, beta)
        widget._close_tab(beta_index)
        plugin._flush_chat_session_state()

        open_results = run_history_action(widget, beta.session_id, "open")
        state_after_open = widget.serialize_session_state()

        reopened = widget._active_session
        if reopened is None or reopened.session_id != beta.session_id:
            raise RuntimeError("Reopened session did not become the active tab")

        duplicate_results = run_history_action(
            widget,
            alpha.session_id,
            "duplicate",
        )
        duplicated = wait_for(
            lambda: any(
                (session.get("title") or "").endswith(" (copy)")
                for session in widget.serialize_session_state().get("sessions", [])
            ),
            timeout_ms=5000,
            step_ms=50,
        )
        if not duplicated:
            raise RuntimeError("Duplicated session did not appear in the open tabs")
        state_after_duplicate = widget.serialize_session_state()

        duplicate = widget._active_session
        if duplicate is None or duplicate.session_id == alpha.session_id:
            raise RuntimeError("Duplicated session did not get a fresh session id")

        delete_results = run_history_action(widget, beta.session_id, "delete")
        deleted = wait_for(
            lambda: (
                beta.session_id not in {
                    session.get("session_id")
                    for session in widget.serialize_session_state().get("history", [])
                }
                and beta.session_id not in {
                    session.get("session_id")
                    for session in widget.serialize_session_state().get("sessions", [])
                }
            ),
            timeout_ms=5000,
            step_ms=50,
        )
        if not deleted:
            raise RuntimeError("Deleted history session still appears in widget state")

        plugin._flush_chat_session_state()
        persisted = json.loads(STATE_PATH.read_text(encoding="utf-8"))
        serialized = widget.serialize_session_state()

        results["browser_rows"] = open_results["rows"]
        results["open_results"] = open_results
        results["duplicate_results"] = duplicate_results
        results["delete_results"] = delete_results
        results["state_after_open"] = state_after_open
        results["state_after_duplicate"] = state_after_duplicate
        results["alpha_session_id"] = alpha.session_id
        results["beta_session_id"] = beta.session_id
        results["duplicate_session_id"] = duplicate.session_id
        results["serialized_state"] = serialized
        results["persisted_state"] = persisted
        results["reopened_matches_beta"] = reopened.session_id == beta.session_id
        results["duplicate_has_new_id"] = duplicate.session_id != alpha.session_id
        results["deleted_beta_from_history"] = beta.session_id not in {
            session.get("session_id")
            for session in serialized.get("history", [])
        }
        results["deleted_beta_closed_tab"] = beta.session_id not in {
            session.get("session_id")
            for session in serialized.get("sessions", [])
        }
        results["scope_label"] = widget._session_scope_info().get("scope_label")
    except Exception as exc:
        results["errors"].append({
            "type": type(exc).__name__,
            "message": str(exc),
            "traceback": traceback.format_exc(),
        })
    finally:
        record_validation_result(window, RESULT_PATH, results)
        finalize(window)


def main():
    """Launch Spyder and validate the history-browser workflow."""
    return run_spyder_validation(
        config_dir=CONFIG_DIR,
        filter_log=(
            "spyder_ai_assistant,spyder.plugins.projects,"
            "spyder.app.mainwindow"
        ),
        run_validation=run_validation,
        attr_name="_chat_history_browser_validation_scheduled",
        delay_ms=3500,
    )


if __name__ == "__main__":
    raise SystemExit(main())
