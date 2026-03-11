"""Validate restored per-tab chat inference controls after restarting Spyder."""

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


CONFIG_DIR = artifact_path("configs", "chat-inference-controls")
PROJECT_DIR = artifact_path("fixtures", "chat-inference-controls-project")
RESULT_PATH = artifact_path(
    "results",
    "chat-inference-controls-restore-validation.json",
)
STATE_PATH = PROJECT_DIR / ".spyproject/ai-assistant/chat-sessions.json"


def ensure_project_open(window):
    """Reopen the existing project used for inference-control validation."""
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
    """Verify restored sessions keep their per-tab inference state."""
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
            lambda: (
                widget._tab_widget.count() == len(expected_sessions)
                and len(widget._serialize_open_sessions()) == len(expected_sessions)
            ),
            timeout_ms=20000,
            step_ms=100,
        )
        if not restored:
            raise RuntimeError("Restored chat tab count did not match saved state")

        restored_state = widget.serialize_session_state()
        restored_sessions = restored_state.get("sessions", [])
        expected_by_id = {
            session.get("session_id"): session for session in expected_sessions
        }

        restored_rows = []
        for index, session in enumerate(widget._sessions.ordered_sessions(widget._tab_widget)):
            widget._tab_widget.setCurrentIndex(index)
            wait_for(
                lambda: widget._active_session is session,
                timeout_ms=2000,
                step_ms=50,
            )
            state = session.to_state()
            expected = expected_by_id.get(session.session_id, {})
            restored_rows.append(
                {
                    "session_id": session.session_id,
                    "title": session.title,
                    "button_text": widget.chat_settings_btn.text(),
                    "tooltip": widget.chat_settings_btn.toolTip(),
                    "resolved_options": widget._chat_options(session),
                    "persisted_temperature_override": expected.get("temperature_override"),
                    "persisted_max_tokens_override": expected.get("max_tokens_override"),
                    "restored_temperature_override": state.get("temperature_override"),
                    "restored_max_tokens_override": state.get("max_tokens_override"),
                }
            )
            print(
                "[validation] restored session "
                f"{session.session_id}: {widget._chat_options(session)} | "
                f"{widget.chat_settings_btn.text()}",
                flush=True,
            )

        restored_by_id = {
            row["session_id"]: row for row in restored_rows
        }
        first_session = expected_sessions[0]
        second_session = expected_sessions[1]
        first_row = restored_by_id.get(first_session.get("session_id"), {})
        second_row = restored_by_id.get(second_session.get("session_id"), {})

        if first_row.get("restored_temperature_override") != first_session.get("temperature_override"):
            raise RuntimeError("First restored temperature override did not match saved state")
        if first_row.get("restored_max_tokens_override") != first_session.get("max_tokens_override"):
            raise RuntimeError("First restored max-token override did not match saved state")
        if second_row.get("restored_temperature_override") != second_session.get("temperature_override"):
            raise RuntimeError("Second restored temperature override did not match saved state")
        if second_row.get("restored_max_tokens_override") != second_session.get("max_tokens_override"):
            raise RuntimeError("Second restored max-token override did not match saved state")

        if first_session.get("temperature_override") is not None:
            expected_first_options = {
                "temperature": first_session.get("temperature_override"),
                "num_predict": first_session.get("max_tokens_override"),
            }
            if first_row.get("resolved_options") != expected_first_options:
                raise RuntimeError("First restored resolved options were incorrect")
            if first_row.get("button_text") != "Settings*":
                raise RuntimeError("First restored tab should show overridden settings")

        if second_session.get("temperature_override") is None and second_session.get("max_tokens_override") is None:
            if second_row.get("button_text") != "Settings":
                raise RuntimeError("Second restored tab should use global defaults")
        print("[validation] restore verification passed", flush=True)

        results["persisted_sessions"] = expected_sessions
        results["restored_state"] = restored_state
        results["restored_rows"] = restored_rows
        results["active_index"] = widget._tab_widget.currentIndex()
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
    """Launch Spyder and validate restored chat inference controls."""
    return run_spyder_validation(
        config_dir=CONFIG_DIR,
        filter_log=(
            "spyder_ai_assistant,spyder.plugins.projects,"
            "spyder.app.mainwindow"
        ),
        run_validation=run_validation,
        attr_name="_chat_inference_controls_restore_validation_scheduled",
        delay_ms=3500,
    )


if __name__ == "__main__":
    raise SystemExit(main())
