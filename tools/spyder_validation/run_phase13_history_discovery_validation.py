"""Live Phase 13 validation for history search and session discovery."""

from __future__ import annotations

import json
import shutil
import traceback

from qtpy.QtCore import QTimer
from qtpy.QtWidgets import QApplication

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


CONFIG_DIR = artifact_path("configs", "phase13-history-discovery")
PROJECT_DIR = artifact_path("fixtures", "phase13-history-project")
RESULT_PATH = artifact_path("results", "phase13-history-discovery-validation.json")
STATE_PATH = PROJECT_DIR / ".spyproject/ai-assistant/chat-sessions.json"


def ensure_project_open(window):
    """Open or create the project used for Phase 13 history validation."""
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


def seed_session(widget, *, title, prompt_preset_id, user_text, assistant_text,
                 created_at, updated_at):
    """Create one deterministic chat session with explicit metadata."""
    session = ChatSession(
        parent=widget._tab_widget,
        title=title,
        messages=[
            {"role": "user", "content": user_text},
            {"role": "assistant", "content": assistant_text},
        ],
        prompt_preset_id=prompt_preset_id,
        created_at=created_at,
        updated_at=updated_at,
    )
    widget._add_session(session, notify=False)
    session.display.rebuild_from_messages(session.messages)
    return session


def _find_history_dialog():
    """Return the visible history dialog, if any."""
    app = QApplication.instance()
    for widget in app.topLevelWidgets():
        if isinstance(widget, SessionHistoryDialog) and widget.isVisible():
            return widget
    return None


def run_validation(window):
    """Exercise the searchable/sortable session history browser."""
    results = {
        "phase": "13",
        "errors": [],
        "project_path": str(PROJECT_DIR),
        "state_path": str(STATE_PATH),
    }

    try:
        ensure_project_open(window)
        plugin = get_ai_plugin(window)
        widget = get_chat_widget(window)
        print("[phase13] resetting chat state and seeding sessions", flush=True)

        widget._clear_all_tabs()
        widget._history_sessions = []
        analysis = seed_session(
            widget,
            title="Array Debug",
            prompt_preset_id="analysis",
            user_text="Check the array summary",
            assistant_text="The array spans 0 to 5.",
            created_at="2026-03-10T09:00:00Z",
            updated_at="2026-03-10T09:10:00Z",
        )
        review = seed_session(
            widget,
            title="Review Findings",
            prompt_preset_id="review",
            user_text="Review this function",
            assistant_text="There is one missing edge-case test.",
            created_at="2026-03-11T09:00:00Z",
            updated_at="2026-03-11T09:20:00Z",
        )
        docs = seed_session(
            widget,
            title="Doc Notes",
            prompt_preset_id="documentation",
            user_text="Document the public API",
            assistant_text="Add parameter and return sections.",
            created_at="2026-03-12T09:00:00Z",
            updated_at="2026-03-12T09:30:00Z",
        )
        widget._tab_widget.setCurrentIndex(
            widget._sessions.index_of(widget._tab_widget, review)
        )
        widget._notify_session_state_changed("phase13-history-seed")
        plugin._flush_chat_session_state()

        # Leave one tab open and move the others into saved history only.
        docs_index = widget._sessions.index_of(widget._tab_widget, docs)
        widget._close_tab(docs_index)
        analysis_index = widget._sessions.index_of(widget._tab_widget, analysis)
        widget._close_tab(analysis_index)
        plugin._flush_chat_session_state()
        print("[phase13] opening sessions browser from the compact toolbar", flush=True)

        interaction = {
            "dialog_seen": False,
            "initial_rows": [],
            "review_rows": [],
            "saved_rows": [],
            "saved_title_sort": [],
            "analysis_search_rows": [],
            "menu_actions": [],
            "button_text": widget.session_btn.text(),
        }

        def drive_dialog():
            dialog = _find_history_dialog()
            if dialog is None:
                QTimer.singleShot(50, drive_dialog)
                return

            interaction["dialog_seen"] = True
            print("[phase13] history dialog opened", flush=True)
            interaction["menu_actions"] = [
                action.text() for action in dialog.parent().session_btn.menu().actions()
            ]
            interaction["initial_rows"] = dialog.visible_rows()

            print("[phase13] applying title search for 'review'", flush=True)
            dialog.set_search_text("review")
            QApplication.instance().processEvents()
            interaction["review_rows"] = dialog.visible_rows()

            print("[phase13] filtering saved-only rows", flush=True)
            dialog.set_search_text("")
            dialog.set_status_filter("saved")
            QApplication.instance().processEvents()
            interaction["saved_rows"] = dialog.visible_rows()

            print("[phase13] sorting saved rows by title", flush=True)
            dialog.set_sort_key("title_asc")
            QApplication.instance().processEvents()
            interaction["saved_title_sort"] = [
                row.get("title", "") for row in dialog.visible_rows()
            ]

            print("[phase13] searching by prompt mode label", flush=True)
            dialog.set_status_filter("all")
            dialog.set_search_text("analysis")
            QApplication.instance().processEvents()
            interaction["analysis_search_rows"] = dialog.visible_rows()

            print("[phase13] reopening Doc Notes from the filtered list", flush=True)
            dialog.set_search_text("doc")
            QApplication.instance().processEvents()
            if not dialog.select_session_id(docs.session_id):
                results["errors"].append("Failed to select Doc Notes row in filtered history browser")
                dialog.reject()
                return
            dialog.open_btn.click()

        QTimer.singleShot(0, drive_dialog)
        widget.session_btn.click()

        reopened = wait_for(
            lambda: (
                widget._active_session is not None
                and widget._active_session.session_id == docs.session_id
            ),
            timeout_ms=5000,
            step_ms=50,
        )
        if not reopened:
            raise RuntimeError("Filtered history open action did not restore Doc Notes")

        if not interaction["dialog_seen"]:
            raise RuntimeError("History discovery dialog did not open")

        results["interaction"] = interaction
        results["active_session"] = widget._active_session.to_state()
        results["serialized_state"] = widget.serialize_session_state()
        results["persisted_state"] = json.loads(STATE_PATH.read_text(encoding="utf-8"))
        results["checks"] = {
            "session_button_text": interaction["button_text"] == "Sessions",
            "review_search_hit": [
                row.get("title") for row in interaction["review_rows"]
            ] == ["Review Findings"],
            "saved_filter_titles": [
                row.get("title") for row in interaction["saved_rows"]
            ] == ["Doc Notes", "Array Debug"],
            "saved_title_sort": interaction["saved_title_sort"] == [
                "Array Debug",
                "Doc Notes",
            ],
            "analysis_mode_search_hit": [
                row.get("title") for row in interaction["analysis_search_rows"]
            ] == ["Array Debug"],
            "reopened_doc_session": widget._active_session.session_id == docs.session_id,
        }
        if not all(results["checks"].values()):
            raise RuntimeError("One or more Phase 13 history discovery checks failed")
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
    """Launch Spyder and validate Phase 13 history discovery."""
    return run_spyder_validation(
        config_dir=CONFIG_DIR,
        filter_log=(
            "spyder_ai_assistant,spyder.plugins.projects,"
            "spyder.app.mainwindow"
        ),
        run_validation=run_validation,
        attr_name="_phase13_history_discovery_validation_scheduled",
        delay_ms=3500,
    )


if __name__ == "__main__":
    raise SystemExit(main())
