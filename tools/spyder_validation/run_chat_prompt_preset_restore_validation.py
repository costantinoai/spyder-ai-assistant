"""Verify restored chat prompt presets after restarting Spyder."""

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


CONFIG_DIR = artifact_path("configs", "chat-prompt-preset")
PROJECT_DIR = artifact_path("fixtures", "chat-prompt-preset-project")
RESULT_PATH = artifact_path("results", "chat-prompt-preset-restore-validation.json")


def ensure_project_open(window):
    """Reopen the prompt-preset validation project."""
    projects = get_projects_plugin(window)
    if not PROJECT_DIR.exists():
        raise RuntimeError("Expected prompt-preset project does not exist")

    projects.open_project(str(PROJECT_DIR))
    opened = wait_for(
        lambda: projects.get_active_project_path() == str(PROJECT_DIR),
        timeout_ms=30000,
        step_ms=100,
    )
    if not opened:
        raise RuntimeError("Spyder project did not reopen")


def run_validation(window):
    """Verify restored prompt preset state across restart."""
    results = {
        "errors": [],
        "project_path": str(PROJECT_DIR),
    }

    try:
        ensure_project_open(window)
        widget = get_chat_widget(window)
        print("[validation] prompt preset restore: waiting for restored sessions", flush=True)
        restored = wait_for(
            lambda: len(widget.serialize_session_state().get("sessions", [])) >= 2,
            timeout_ms=30000,
            step_ms=100,
        )
        if not restored:
            raise RuntimeError("Restored prompt preset sessions did not appear")

        print("[validation] switching to first restored tab", flush=True)
        widget._tab_widget.setCurrentIndex(0)
        wait_for(
            lambda: widget.prompt_preset_combo.currentData() == "debugging",
            timeout_ms=2000,
            step_ms=50,
        )
        first_combo = widget.prompt_preset_combo.currentData()

        print("[validation] switching to second restored tab", flush=True)
        widget._tab_widget.setCurrentIndex(1)
        wait_for(
            lambda: widget.prompt_preset_combo.currentData() == "documentation",
            timeout_ms=2000,
            step_ms=50,
        )
        second_combo = widget.prompt_preset_combo.currentData()

        state = widget.serialize_session_state()
        results["restored_state"] = state
        results["first_combo"] = first_combo
        results["second_combo"] = second_combo
        results["restored_presets"] = [
            session.get("prompt_preset_id")
            for session in state.get("sessions", [])
        ]
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
    """Launch Spyder and verify restored prompt preset state."""
    return run_spyder_validation(
        config_dir=CONFIG_DIR,
        filter_log=(
            "spyder_ai_assistant,spyder.plugins.projects,"
            "spyder.app.mainwindow"
        ),
        run_validation=run_validation,
        attr_name="_chat_prompt_preset_restore_validation_scheduled",
        delay_ms=3500,
    )


if __name__ == "__main__":
    raise SystemExit(main())
