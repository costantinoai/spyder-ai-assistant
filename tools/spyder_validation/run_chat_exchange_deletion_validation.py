"""Validate per-exchange deletion in a real Spyder session."""

from __future__ import annotations

import json
import shutil
import traceback

from tools.spyder_validation.common import (
    DEFAULT_CHAT_MODEL,
    artifact_path,
    delete_chat_exchange_via_dialog,
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


CONFIG_DIR = artifact_path("configs", "chat-exchange-deletion")
PROJECT_DIR = artifact_path("fixtures", "chat-exchange-deletion-project")
RESULT_PATH = artifact_path("results", "chat-exchange-deletion-validation.json")
STATE_PATH = PROJECT_DIR / ".spyproject/ai-assistant/chat-sessions.json"


def ensure_project_open(window):
    """Open or create the project used for exchange-deletion validation."""
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
    """Exercise exchange deletion and regenerate on the active chat tab."""
    results = {
        "errors": [],
        "project_path": str(PROJECT_DIR),
        "state_path": str(STATE_PATH),
    }

    try:
        ensure_project_open(window)
        plugin = get_ai_plugin(window)
        widget = get_chat_widget(window)
        print("[validation] exchange deletion: resetting chat state", flush=True)

        widget._clear_all_tabs()
        widget._history_sessions = []
        session = widget._add_new_tab(notify=False)

        print("[validation] selecting live chat model", flush=True)
        select_model(widget, DEFAULT_CHAT_MODEL)

        prompts = [
            ("FIRST", "Reply with the single word FIRST."),
            ("SECOND", "Reply with the single word SECOND."),
            ("THIRD", "Reply with the single word THIRD."),
        ]
        replies = []
        for label, prompt in prompts:
            print(f"[validation] sending {label} turn", flush=True)
            replies.append(send_prompt(widget, prompt, timeout_ms=150000))

        before_delete = list(session.messages)
        print(
            f"[validation] deleting exchange 2 from {len(before_delete)} messages",
            flush=True,
        )
        delete_chat_exchange_via_dialog(widget, 1)
        after_delete = list(session.messages)
        print(
            f"[validation] remaining messages after delete: {len(after_delete)}",
            flush=True,
        )

        if [message.get("content") for message in after_delete if message.get("role") == "user"] != [
            "Reply with the single word FIRST.",
            "Reply with the single word THIRD.",
        ]:
            raise RuntimeError("Deleted exchange did not remove the middle turn")

        if any("SECOND" in message.get("content", "") for message in after_delete):
            raise RuntimeError("Deleted exchange content still remains in the session")

        print("[validation] regenerating after exchange deletion", flush=True)
        widget.regenerate_btn.click()
        regenerated = wait_for(
            lambda: (
                not widget._generating
                and len(session.messages) == 4
                and session.messages[-2].get("role") == "user"
                and session.messages[-2].get("content") == "Reply with the single word THIRD."
                and session.messages[-1].get("role") == "assistant"
                and bool(session.messages[-1].get("content", "").strip())
            ),
            timeout_ms=150000,
            step_ms=200,
        )
        if not regenerated:
            raise RuntimeError("Regenerate did not complete cleanly after deletion")

        print("[validation] flushing exchange-deletion state to project storage", flush=True)
        plugin._flush_chat_session_state()
        persisted = json.loads(STATE_PATH.read_text(encoding="utf-8"))
        persisted_messages = persisted.get("sessions", [{}])[0].get("messages", [])
        if any("SECOND" in message.get("content", "") for message in persisted_messages):
            raise RuntimeError("Deleted exchange still exists in persisted state")

        results["replies"] = replies
        results["before_delete"] = before_delete
        results["after_delete"] = after_delete
        results["after_regenerate"] = list(session.messages)
        results["persisted_state"] = persisted
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
    """Launch Spyder and validate per-exchange deletion."""
    return run_spyder_validation(
        config_dir=CONFIG_DIR,
        filter_log=(
            "spyder_ai_assistant,spyder.plugins.projects,"
            "spyder.app.mainwindow"
        ),
        run_validation=run_validation,
        attr_name="_chat_exchange_deletion_validation_scheduled",
        delay_ms=3500,
    )


if __name__ == "__main__":
    raise SystemExit(main())
