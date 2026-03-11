"""Validate restored state after per-exchange deletion."""

from __future__ import annotations

import json
import traceback

from tools.spyder_validation.common import (
    artifact_path,
    finalize,
    get_chat_widget,
    get_projects_plugin,
    record_validation_result,
    run_spyder_validation,
    wait_for,
)


CONFIG_DIR = artifact_path("configs", "chat-exchange-deletion")
PROJECT_DIR = artifact_path("fixtures", "chat-exchange-deletion-project")
RESULT_PATH = artifact_path(
    "results",
    "chat-exchange-deletion-restore-validation.json",
)
STATE_PATH = PROJECT_DIR / ".spyproject/ai-assistant/chat-sessions.json"


def ensure_project_open(window):
    """Reopen the existing project used for exchange-deletion validation."""
    projects = get_projects_plugin(window)
    if not (PROJECT_DIR / ".spyproject").exists():
        raise RuntimeError("Validation project does not exist")

    projects.open_project(str(PROJECT_DIR))
    opened = wait_for(
        lambda: projects.get_active_project_path() == str(PROJECT_DIR),
        timeout_ms=30000,
        step_ms=100,
    )
    if not opened:
        raise RuntimeError("Spyder project did not reopen")


def run_validation(window):
    """Verify restored sessions keep the exchange-deletion result."""
    results = {
        "errors": [],
        "project_path": str(PROJECT_DIR),
        "state_path": str(STATE_PATH),
    }

    try:
        ensure_project_open(window)
        widget = get_chat_widget(window)
        persisted = json.loads(STATE_PATH.read_text(encoding="utf-8"))
        expected_sessions = persisted.get("sessions", [])
        restored = wait_for(
            lambda: widget._tab_widget.count() == len(expected_sessions),
            timeout_ms=20000,
            step_ms=100,
        )
        if not restored:
            raise RuntimeError("Restored chat tab count did not match saved state")

        session = widget._active_session
        if session is None:
            raise RuntimeError("No active session after restore")

        restored_messages = list(session.messages)
        if any("SECOND" in message.get("content", "") for message in restored_messages):
            raise RuntimeError("Deleted exchange returned after restart")
        if [message.get("content") for message in restored_messages if message.get("role") == "user"] != [
            "Reply with the single word FIRST.",
            "Reply with the single word THIRD.",
        ]:
            raise RuntimeError("Restored user-turn order is incorrect after deletion")

        dialog = widget._create_exchange_delete_dialog(session)
        rows = [
            {
                "exchange_index": row.get("exchange_index"),
                "title": row.get("title"),
                "status": row.get("status"),
                "preview": row.get("preview"),
            }
            for row in dialog._rows
        ]
        dialog.close()
        print(
            "[validation] restored delete-dialog rows: "
            f"{[(row['exchange_index'], row['title']) for row in rows]}",
            flush=True,
        )
        if len(rows) != 2:
            raise RuntimeError("Restored delete dialog should show two remaining exchanges")

        results["persisted_sessions"] = expected_sessions
        results["restored_messages"] = restored_messages
        results["exchange_rows"] = rows
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
    """Launch Spyder and validate restored exchange-deletion state."""
    return run_spyder_validation(
        config_dir=CONFIG_DIR,
        filter_log=(
            "spyder_ai_assistant,spyder.plugins.projects,"
            "spyder.app.mainwindow"
        ),
        run_validation=run_validation,
        attr_name="_chat_exchange_deletion_restore_validation_scheduled",
        delay_ms=3500,
    )


if __name__ == "__main__":
    raise SystemExit(main())
