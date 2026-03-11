"""Verify project-scoped chat restore in a real Spyder session."""

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
    write_json,
)


CONFIG_DIR = artifact_path("configs", "chat-persistence")
PROJECT_DIR = artifact_path("fixtures", "chat-project")
RESULT_PATH = artifact_path("results", "chat-persistence-verify.json")
STATE_PATH = PROJECT_DIR / ".spyproject/ai-assistant/chat-sessions.json"


def ensure_project_open(window):
    """Reopen the existing persistence test project."""
    projects = get_projects_plugin(window)
    if not PROJECT_DIR.exists():
        raise RuntimeError("Expected persistence project directory does not exist")

    projects.open_project(str(PROJECT_DIR))
    opened = wait_for(
        lambda: projects.get_active_project_path() == str(PROJECT_DIR),
        timeout_ms=30000,
        step_ms=100,
    )
    if not opened:
        raise RuntimeError("Spyder project did not reopen")


def run_validation(window):
    """Verify that the persisted chat sessions restore correctly."""
    results = {
        "errors": [],
        "project_path": str(PROJECT_DIR),
        "state_path": str(STATE_PATH),
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
            raise RuntimeError("Persisted chat sessions did not restore")

        state = widget.serialize_session_state()
        results["restored_state"] = state
        results["active_index"] = widget._tab_widget.currentIndex()
        results["tab_titles"] = [
            widget._tab_widget.tabText(index)
            for index in range(widget._tab_widget.count())
        ]
        results["state_file_exists"] = STATE_PATH.exists()
        results["state_file_preview"] = STATE_PATH.read_text(encoding="utf-8")[:800]
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
    """Launch Spyder and verify project-scoped chat restoration."""
    return run_spyder_validation(
        config_dir=CONFIG_DIR,
        filter_log=(
            "spyder_ai_assistant,spyder.plugins.projects,"
            "spyder.app.mainwindow"
        ),
        run_validation=run_validation,
        attr_name="_chat_persistence_verify_scheduled",
        delay_ms=3500,
    )


if __name__ == "__main__":
    raise SystemExit(main())
