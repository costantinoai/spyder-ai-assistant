"""Focused use-console smoke test in a real Spyder session."""

from __future__ import annotations

import traceback

from tools.spyder_validation.common import (
    DEFAULT_CHAT_MODEL,
    artifact_path,
    finalize,
    get_ai_plugin,
    get_chat_widget,
    get_current_shell,
    record_validation_result,
    run_spyder_validation,
    select_model,
    set_input_text,
    wait_for,
    wait_for_assistant_turn,
    write_json,
)


CONFIG_DIR = artifact_path("configs", "chat-use-console-smoke")
RESULT_PATH = artifact_path("results", "chat-use-console-smoke.json")
CHAT_MODEL = DEFAULT_CHAT_MODEL
MARKER = "SPYDER_AI_ASSISTANT_FRESH_CONSOLE_MARKER"


def run_validation(window):
    """Verify that the use-console flow inspects the live console tail."""
    results = {"errors": [], "marker": MARKER}

    try:
        widget = get_chat_widget(window)
        select_model(widget, CHAT_MODEL)

        shell = get_current_shell(window)
        shell.execute(f"print('{MARKER}')")
        wait_for(
            lambda: MARKER in (
                get_ai_plugin(window)._runtime_context.get_current_context().get("console_output") or ""
            ),
            timeout_ms=20000,
            step_ms=100,
        )

        session = widget._active_session
        previous_count = len(session.messages)
        set_input_text(widget, "What marker was just printed?")
        widget.trigger_debug_action("use_console")
        completed = wait_for_assistant_turn(
            widget,
            session,
            previous_count,
            timeout_ms=120000,
        )
        if not completed:
            raise RuntimeError("Timed out waiting for use-console response")

        answer = session.messages[-1]["content"]
        results["answer"] = answer
        results["mentions_marker"] = MARKER.lower() in answer.lower()
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
    """Launch Spyder and run the focused use-console smoke test."""
    return run_spyder_validation(
        config_dir=CONFIG_DIR,
        filter_log="spyder_ai_assistant,spyder.app.mainwindow",
        run_validation=run_validation,
        attr_name="_chat_use_console_smoke_scheduled",
        delay_ms=3500,
    )


if __name__ == "__main__":
    raise SystemExit(main())
