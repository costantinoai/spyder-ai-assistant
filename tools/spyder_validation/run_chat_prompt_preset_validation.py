"""Validate per-tab chat prompt presets in a real Spyder session."""

from __future__ import annotations

import json
import shutil
import traceback

from tools.spyder_validation.common import (
    artifact_path,
    finalize,
    get_ai_plugin,
    get_chat_widget,
    get_projects_plugin,
    record_validation_result,
    run_spyder_validation,
    select_prompt_preset,
    wait_for,
)


CONFIG_DIR = artifact_path("configs", "chat-prompt-preset")
PROJECT_DIR = artifact_path("fixtures", "chat-prompt-preset-project")
RESULT_PATH = artifact_path("results", "chat-prompt-preset-validation.json")
STATE_PATH = PROJECT_DIR / ".spyproject/ai-assistant/chat-sessions.json"


def ensure_project_open(window):
    """Open or create the project used for prompt-preset validation."""
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


def run_validation(window):
    """Exercise prompt preset selection, tab switching, and persistence."""
    results = {
        "errors": [],
        "project_path": str(PROJECT_DIR),
        "state_path": str(STATE_PATH),
    }

    try:
        ensure_project_open(window)
        plugin = get_ai_plugin(window)
        widget = get_chat_widget(window)
        print("[validation] prompt preset setup: resetting chat state", flush=True)

        widget._clear_all_tabs()
        widget._history_sessions = []
        first_session = widget._add_new_tab(notify=False)

        print("[validation] selecting review preset on first tab", flush=True)
        default_preset = widget._active_session.prompt_preset_id
        review_preset = select_prompt_preset(widget, "review")
        review_prompt = widget._build_system_prompt(widget._active_session)
        first_session = widget._active_session

        print("[validation] opening second tab and selecting data-analysis preset", flush=True)
        second_session = widget._add_new_tab()
        analysis_preset = select_prompt_preset(widget, "analysis")
        analysis_prompt = widget._build_system_prompt(second_session)

        print("[validation] switching tabs to verify toolbar preset sync", flush=True)
        widget._tab_widget.setCurrentIndex(0)
        wait_for(
            lambda: widget.prompt_preset_combo.currentData() == review_preset,
            timeout_ms=2000,
            step_ms=50,
        )
        first_combo_after_switch = widget.prompt_preset_combo.currentData()

        widget._tab_widget.setCurrentIndex(1)
        wait_for(
            lambda: widget.prompt_preset_combo.currentData() == analysis_preset,
            timeout_ms=2000,
            step_ms=50,
        )
        second_combo_after_switch = widget.prompt_preset_combo.currentData()

        print("[validation] flushing prompt preset state to project storage", flush=True)
        plugin._flush_chat_session_state()
        persisted = json.loads(STATE_PATH.read_text(encoding="utf-8"))
        serialized = widget.serialize_session_state()

        results["default_preset"] = default_preset
        results["first_session_id"] = first_session.session_id
        results["second_session_id"] = second_session.session_id
        results["first_preset"] = first_session.prompt_preset_id
        results["second_preset"] = second_session.prompt_preset_id
        results["first_combo_after_switch"] = first_combo_after_switch
        results["second_combo_after_switch"] = second_combo_after_switch
        results["first_prompt_contains_review"] = (
            "Active chat mode: Review." in review_prompt
        )
        results["second_prompt_contains_analysis"] = (
            "Active chat mode: Data Analysis." in analysis_prompt
        )
        results["serialized_state"] = serialized
        results["persisted_state"] = persisted
        results["persisted_presets"] = [
            session.get("prompt_preset_id")
            for session in persisted.get("sessions", [])
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
    """Launch Spyder and validate the prompt-preset workflow."""
    return run_spyder_validation(
        config_dir=CONFIG_DIR,
        filter_log=(
            "spyder_ai_assistant,spyder.plugins.projects,"
            "spyder.app.mainwindow"
        ),
        run_validation=run_validation,
        attr_name="_chat_prompt_preset_validation_scheduled",
        delay_ms=3500,
    )


if __name__ == "__main__":
    raise SystemExit(main())
