"""Create project-scoped chat state in a real Spyder session."""

from __future__ import annotations

import json
import traceback

from tools.spyder_validation.common import (
    DEFAULT_CHAT_MODEL,
    artifact_path,
    finalize,
    get_ai_plugin,
    get_chat_widget,
    get_projects_plugin,
    record_validation_result,
    run_spyder_validation,
    select_model,
    send_prompt,
    wait_for,
    write_json,
)


CONFIG_DIR = artifact_path("configs", "chat-persistence")
PROJECT_DIR = artifact_path("fixtures", "chat-project")
RESULT_PATH = artifact_path("results", "chat-persistence-setup.json")
CHAT_MODEL = DEFAULT_CHAT_MODEL
STATE_PATH = PROJECT_DIR / ".spyproject/ai-assistant/chat-sessions.json"


def ensure_project_open(window):
    """Open or create the project used for persistence validation."""
    projects = get_projects_plugin(window)
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
    """Write two chat sessions and flush them to project storage."""
    results = {
        "errors": [],
        "requested_model": CHAT_MODEL,
        "project_path": str(PROJECT_DIR),
        "state_path": str(STATE_PATH),
    }

    try:
        ensure_project_open(window)
        widget = get_chat_widget(window)
        select_model(widget, CHAT_MODEL)

        first_answer = send_prompt(
            widget,
            "Reply with exactly PERSISTENCE_ALPHA and nothing else.",
        )

        widget._add_new_tab()
        second_answer = send_prompt(
            widget,
            "Reply with exactly PERSISTENCE_BETA and nothing else.",
        )

        get_ai_plugin(window)._flush_chat_session_state()
        persisted = json.loads(STATE_PATH.read_text(encoding="utf-8"))

        results["first_answer"] = first_answer
        results["second_answer"] = second_answer
        results["serialized_state"] = widget.serialize_session_state()
        results["persisted_state"] = persisted
        results["state_file_exists"] = STATE_PATH.exists()
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
    """Launch Spyder and create project-scoped chat persistence state."""
    return run_spyder_validation(
        config_dir=CONFIG_DIR,
        filter_log=(
            "spyder_ai_assistant,spyder.plugins.projects,"
            "spyder.app.mainwindow"
        ),
        run_validation=run_validation,
        attr_name="_chat_persistence_setup_scheduled",
        delay_ms=3500,
    )


if __name__ == "__main__":
    raise SystemExit(main())
