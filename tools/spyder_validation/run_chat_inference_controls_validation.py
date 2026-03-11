"""Validate per-tab chat inference controls in a real Spyder session."""

from __future__ import annotations

import json
import shutil
import traceback

from tools.spyder_validation.common import (
    DEFAULT_CHAT_MODEL,
    apply_chat_settings,
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
)


CONFIG_DIR = artifact_path("configs", "chat-inference-controls")
PROJECT_DIR = artifact_path("fixtures", "chat-inference-controls-project")
RESULT_PATH = artifact_path("results", "chat-inference-controls-validation.json")
STATE_PATH = PROJECT_DIR / ".spyproject/ai-assistant/chat-sessions.json"


def ensure_project_open(window):
    """Open or create the project used for inference-control validation."""
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
    """Exercise per-tab settings, reset behavior, and request dispatch."""
    results = {
        "errors": [],
        "project_path": str(PROJECT_DIR),
        "state_path": str(STATE_PATH),
    }

    try:
        ensure_project_open(window)
        plugin = get_ai_plugin(window)
        widget = get_chat_widget(window)
        print("[validation] inference controls: resetting chat state", flush=True)

        widget._clear_all_tabs()
        widget._history_sessions = []
        first_session = widget._add_new_tab(notify=False)
        global_defaults_before = widget._chat_default_options()

        print("[validation] selecting live chat model", flush=True)
        select_model(widget, DEFAULT_CHAT_MODEL)

        print("[validation] applying overrides to first tab via settings dialog", flush=True)
        apply_chat_settings(widget, temperature_override=0.2, max_tokens_override=128)
        first_tooltip = widget.chat_settings_btn.toolTip()
        first_button_text = widget.chat_settings_btn.text()
        first_options = widget._chat_options(first_session)
        print(
            f"[validation] first tab options: {first_options} | {first_button_text}",
            flush=True,
        )

        print("[validation] sending first prompt with overridden settings", flush=True)
        first_reply = send_prompt(
            widget,
            "Reply with the single word FIRST.",
            timeout_ms=150000,
        )

        print("[validation] opening second tab and exercising reset to global defaults", flush=True)
        second_session = widget._add_new_tab()
        apply_chat_settings(widget, temperature_override=0.9, max_tokens_override=256)
        second_custom_options = widget._chat_options(second_session)
        print(
            f"[validation] second tab custom options before reset: {second_custom_options}",
            flush=True,
        )
        apply_chat_settings(widget, use_defaults=True)
        second_tooltip = widget.chat_settings_btn.toolTip()
        second_button_text = widget.chat_settings_btn.text()
        second_options = widget._chat_options(second_session)
        print(
            f"[validation] second tab options after reset: {second_options} | {second_button_text}",
            flush=True,
        )

        print("[validation] sending second prompt with restored global defaults", flush=True)
        second_reply = send_prompt(
            widget,
            "Reply with the single word SECOND.",
            timeout_ms=150000,
        )

        print("[validation] switching tabs to verify settings summary sync", flush=True)
        widget._tab_widget.setCurrentIndex(0)
        wait_for(
            lambda: widget.chat_settings_btn.text() == first_button_text,
            timeout_ms=2000,
            step_ms=50,
        )
        first_tooltip_after_switch = widget.chat_settings_btn.toolTip()

        widget._tab_widget.setCurrentIndex(1)
        wait_for(
            lambda: widget.chat_settings_btn.text() == second_button_text,
            timeout_ms=2000,
            step_ms=50,
        )
        second_tooltip_after_switch = widget.chat_settings_btn.toolTip()

        print("[validation] flushing inference-control state to project storage", flush=True)
        plugin._flush_chat_session_state()
        persisted = json.loads(STATE_PATH.read_text(encoding="utf-8"))
        serialized = widget.serialize_session_state()
        persisted_by_id = {
            session.get("session_id"): session
            for session in persisted.get("sessions", [])
        }

        if first_options != {"temperature": 0.2, "num_predict": 128}:
            raise RuntimeError(f"First tab options did not persist: {first_options}")
        if second_custom_options != {"temperature": 0.9, "num_predict": 256}:
            raise RuntimeError(
                f"Second tab custom options did not apply: {second_custom_options}"
            )
        if second_options != global_defaults_before:
            raise RuntimeError(
                f"Second tab did not reset to global defaults: {second_options}"
            )
        if widget._chat_default_options() != global_defaults_before:
            raise RuntimeError("Global chat defaults changed during per-tab edits")
        if first_button_text != "Settings*":
            raise RuntimeError(f"First tab button text did not mark override: {first_button_text}")
        if second_button_text != "Settings":
            raise RuntimeError(f"Second tab button text did not reset: {second_button_text}")
        if "Temperature: 0.2 (tab override)" not in first_tooltip:
            raise RuntimeError("First tab tooltip did not describe its override")
        if "Max tokens: 128 (tab override)" not in first_tooltip:
            raise RuntimeError("First tab tooltip did not include max-token override")
        if first_tooltip_after_switch != first_tooltip:
            raise RuntimeError("First tab tooltip did not resync after tab switch")
        if second_tooltip_after_switch != second_tooltip:
            raise RuntimeError("Second tab tooltip did not resync after tab switch")

        first_persisted = persisted_by_id.get(first_session.session_id, {})
        second_persisted = persisted_by_id.get(second_session.session_id, {})
        if first_persisted.get("temperature_override") != 0.2:
            raise RuntimeError("First tab temperature override was not saved")
        if first_persisted.get("max_tokens_override") != 128:
            raise RuntimeError("First tab max-token override was not saved")
        if second_persisted.get("temperature_override") is not None:
            raise RuntimeError("Second tab reset should clear temperature override")
        if second_persisted.get("max_tokens_override") is not None:
            raise RuntimeError("Second tab reset should clear max-token override")
        print(
            "[validation] persisted overrides: "
            f"{first_session.session_id} -> "
            f"({first_persisted.get('temperature_override')}, "
            f"{first_persisted.get('max_tokens_override')}), "
            f"{second_session.session_id} -> "
            f"({second_persisted.get('temperature_override')}, "
            f"{second_persisted.get('max_tokens_override')})",
            flush=True,
        )

        results["global_defaults_before"] = global_defaults_before
        results["global_defaults_after"] = widget._chat_default_options()
        results["first_session_id"] = first_session.session_id
        results["second_session_id"] = second_session.session_id
        results["first_button_text"] = first_button_text
        results["second_button_text"] = second_button_text
        results["first_tooltip"] = first_tooltip
        results["second_tooltip"] = second_tooltip
        results["first_tooltip_after_switch"] = first_tooltip_after_switch
        results["second_tooltip_after_switch"] = second_tooltip_after_switch
        results["first_options"] = first_options
        results["second_custom_options"] = second_custom_options
        results["second_options"] = second_options
        results["first_reply"] = first_reply
        results["second_reply"] = second_reply
        results["serialized_state"] = serialized
        results["persisted_state"] = persisted
        results["persisted_overrides"] = [
            {
                "session_id": session.get("session_id"),
                "temperature_override": session.get("temperature_override"),
                "max_tokens_override": session.get("max_tokens_override"),
            }
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
    """Launch Spyder and validate the per-tab inference control workflow."""
    return run_spyder_validation(
        config_dir=CONFIG_DIR,
        filter_log=(
            "spyder_ai_assistant,spyder.plugins.projects,"
            "spyder.app.mainwindow"
        ),
        run_validation=run_validation,
        attr_name="_chat_inference_controls_validation_scheduled",
        delay_ms=3500,
    )


if __name__ == "__main__":
    raise SystemExit(main())
