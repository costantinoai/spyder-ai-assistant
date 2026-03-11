"""Verify restored history-browser state after restarting Spyder."""

from __future__ import annotations

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


CONFIG_DIR = artifact_path("configs", "chat-history-browser")
PROJECT_DIR = artifact_path("fixtures", "chat-history-project")
RESULT_PATH = artifact_path("results", "chat-history-browser-restore-validation.json")


def ensure_project_open(window):
    """Reopen the existing history-browser validation project."""
    projects = get_projects_plugin(window)
    if not PROJECT_DIR.exists():
        raise RuntimeError("Expected history-browser project does not exist")

    projects.open_project(str(PROJECT_DIR))
    opened = wait_for(
        lambda: projects.get_active_project_path() == str(PROJECT_DIR),
        timeout_ms=30000,
        step_ms=100,
    )
    if not opened:
        raise RuntimeError("Spyder project did not reopen")


def run_validation(window):
    """Verify that the history-browser end state restores across restart."""
    results = {
        "errors": [],
        "project_path": str(PROJECT_DIR),
    }

    try:
        ensure_project_open(window)
        widget = get_chat_widget(window)
        restored = wait_for(
            lambda: len(widget.serialize_session_state().get("sessions", [])) >= 2,
            timeout_ms=30000,
            step_ms=100,
        )
        if not restored:
            raise RuntimeError("Restored sessions did not appear after restart")

        state = widget.serialize_session_state()
        session_ids = [session.get("session_id") for session in state.get("sessions", [])]
        history_ids = [session.get("session_id") for session in state.get("history", [])]
        titles = [session.get("title") for session in state.get("sessions", [])]

        results["restored_state"] = state
        results["session_ids"] = session_ids
        results["history_ids"] = history_ids
        results["titles"] = titles
        results["has_duplicate_copy"] = any("(copy)" in (title or "") for title in titles)
        results["history_matches_open_sessions"] = set(session_ids) == set(history_ids)
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
    """Launch Spyder and verify restored history-browser state."""
    return run_spyder_validation(
        config_dir=CONFIG_DIR,
        filter_log=(
            "spyder_ai_assistant,spyder.plugins.projects,"
            "spyder.app.mainwindow"
        ),
        run_validation=run_validation,
        attr_name="_chat_history_browser_restore_validation_scheduled",
        delay_ms=3500,
    )


if __name__ == "__main__":
    raise SystemExit(main())
