"""Live chat workflow validation in a real Spyder session."""

from __future__ import annotations

import json
import traceback
from pathlib import Path

from qtpy.QtGui import QTextCursor
from qtpy.QtWidgets import QApplication

from spyder_ai_assistant.utils.chat_workflows import build_export_markdown
from spyder_ai_assistant.utils.code_apply import (
    APPLY_MODE_INSERT,
    APPLY_MODE_REPLACE,
)
from spyder_ai_assistant.utils.prompt_library import get_chat_prompt_preset
from tools.spyder_validation.common import (
    DEFAULT_CHAT_MODEL,
    apply_chat_code_via_dialog,
    artifact_path,
    finalize,
    get_ai_plugin,
    get_chat_widget,
    get_current_shell,
    get_editor_plugin,
    get_runtime_service,
    record_validation_result,
    run_spyder_validation,
    select_model,
    set_input_text,
    wait_for,
    wait_for_assistant_turn,
    write_file,
    write_json,
)


CONFIG_DIR = artifact_path("configs", "chat-workflow-validation")
RESULT_PATH = artifact_path("results", "chat-workflow-validation.json")
EXPORT_PATH = artifact_path("results", "chat-workflow-export.md")
TEST_FILE = artifact_path("fixtures", "chat_workflow_sample.py")
CHAT_MODEL = DEFAULT_CHAT_MODEL
CONSOLE_MARKER = "SPYDER_AI_ASSISTANT_CONSOLE_MARKER"


def open_validation_file(window, results):
    """Open the chat workflow fixture file."""
    editor_plugin = get_editor_plugin(window)
    write_file(TEST_FILE, "alpha = 1\nbeta = 2\n")
    editor_plugin.load_edit(str(TEST_FILE))

    editor = wait_for(lambda: editor_plugin.get_current_editor(), timeout_ms=8000)
    if editor is None:
        raise RuntimeError("Failed to open validation file")

    results["editor"] = {
        "filename": str(TEST_FILE),
        "opened": True,
    }
    return editor


def wait_for_runtime_ready(window):
    """Wait until the runtime service reaches a ready state."""
    widget = get_chat_widget(window)
    service = get_runtime_service(window)
    wait_for(lambda: "Kernel: " in widget.runtime_label.text(), timeout_ms=10000)
    return wait_for(
        lambda: (
            widget.runtime_label.text() == "Kernel: ready"
            and service.get_current_context().get("status") == "ready"
        ),
        timeout_ms=30000,
        step_ms=100,
    )


def execute_code(window, code, predicate=None, timeout_ms=15000):
    """Execute one code snippet in the active IPython console."""
    shell = get_current_shell(window)
    shell.execute(code)
    if predicate is None:
        return True
    return wait_for(predicate, timeout_ms=timeout_ms, step_ms=100)


def send_debug_action(widget, action, user_text="", timeout_ms=180000):
    """Trigger one debug quick action and return the assistant answer."""
    session = widget._active_session
    previous_count = len(session.messages)
    set_input_text(widget, user_text)
    widget.trigger_debug_action(action)
    completed = wait_for_assistant_turn(
        widget,
        session,
        previous_count,
        timeout_ms=timeout_ms,
    )
    if not completed:
        raise RuntimeError(f"Timed out waiting for {action} response")
    return session.messages[-1]["content"]


def send_prompt(widget, prompt, timeout_ms=120000):
    """Send one normal prompt and return the assistant answer."""
    session = widget._active_session
    previous_count = len(session.messages)
    set_input_text(widget, prompt)
    widget.send_btn.click()
    completed = wait_for_assistant_turn(
        widget,
        session,
        previous_count,
        timeout_ms=timeout_ms,
    )
    if not completed:
        raise RuntimeError("Timed out waiting for prompt response")
    return session.messages[-1]["content"]


def select_text(editor, text):
    """Select one exact text fragment in the editor."""
    full_text = editor.toPlainText()
    start = full_text.index(text)
    end = start + len(text)
    cursor = editor.textCursor()
    cursor.setPosition(start)
    cursor.setPosition(end, QTextCursor.KeepAnchor)
    editor.setTextCursor(cursor)
    editor.setFocus()
    QApplication.instance().processEvents()


def move_cursor_to_end(editor):
    """Place the editor cursor at the end of the document."""
    cursor = editor.textCursor()
    cursor.setPosition(len(editor.toPlainText()))
    editor.setTextCursor(cursor)
    editor.setFocus()
    QApplication.instance().processEvents()


def run_apply_action_checks(window, results):
    """Verify previewed insert and replace actions through the real dialog."""
    editor = get_editor_plugin(window).get_current_editor()
    if editor is None:
        raise RuntimeError("No editor available for apply checks")

    widget = get_chat_widget(window)
    session = widget._active_session
    session.display.clear_conversation()

    editor.set_text("alpha = 1\nbeta = 2\n")
    editor.document().setModified(False)
    move_cursor_to_end(editor)
    insert_dialog = apply_chat_code_via_dialog(
        widget,
        "gamma = 3\n",
        mode=APPLY_MODE_INSERT,
        accept=True,
    )
    after_insert = editor.toPlainText()

    editor.set_text("alpha = 1\nbeta = 2\n")
    editor.document().setModified(False)
    select_text(editor, "beta = 2")
    replace_dialog = apply_chat_code_via_dialog(
        widget,
        "beta = 99",
        mode=APPLY_MODE_REPLACE,
        accept=True,
    )
    after_replace = editor.toPlainText()

    results["apply_actions"] = {
        "insert_text": after_insert,
        "replace_text": after_replace,
        "insert_diff": insert_dialog["diff_text"],
        "replace_diff": replace_dialog["diff_text"],
        "insert_ok": "alpha = 1\nbeta = 2\ngamma = 3\n" == after_insert,
        "replace_ok": "alpha = 1\nbeta = 99\n" == after_replace,
    }


def run_runtime_debug_checks(window, results):
    """Exercise the shipped runtime-aware chat workflows."""
    widget = get_chat_widget(window)
    service = get_runtime_service(window)

    results["toolbar"] = {
        "context_label": widget.context_label.text(),
        "runtime_label": widget.runtime_label.text(),
        "runtime_tooltip": widget.runtime_label.toolTip(),
        "debug_controls": [
            widget.debug_menu_btn.text(),
            widget.regenerate_btn.text(),
            widget.session_btn.text(),
            widget.chat_settings_btn.text(),
        ],
        "debug_actions": [
            widget._debug_actions["explain_error"].text(),
            widget._debug_actions["fix_traceback"].text(),
            widget._debug_actions["use_variables"].text(),
            widget._debug_actions["use_console"].text(),
        ],
    }

    runtime_ready = wait_for_runtime_ready(window)
    if not runtime_ready:
        raise RuntimeError("Runtime service did not reach ready state")

    zero_division_ready = execute_code(
        window,
        "1/0",
        predicate=lambda: "ZeroDivisionError" in (
            service.get_current_context().get("latest_error") or ""
        ),
        timeout_ms=20000,
    )
    if not zero_division_ready:
        raise RuntimeError("Failed to capture ZeroDivisionError in runtime context")

    variables_ready = execute_code(
        window,
        "values = [1, 2, 3]\nstate_name = 'phase4'",
        predicate=lambda: any(
            variable.get("name") == "values"
            for variable in service.get_current_context().get("variables", [])
        ),
        timeout_ms=20000,
    )
    if not variables_ready:
        raise RuntimeError("Runtime variables did not refresh after assignment")

    marker_ready = execute_code(
        window,
        f"print('{CONSOLE_MARKER}')",
        predicate=lambda: CONSOLE_MARKER in (
            service.get_current_context().get("console_output") or ""
        ),
        timeout_ms=20000,
    )
    if not marker_ready:
        raise RuntimeError("Console marker did not appear in runtime context")

    explain_answer = send_debug_action(widget, "explain_error", user_text="Explain briefly.")
    fix_answer = send_debug_action(
        widget,
        "fix_traceback",
        user_text="Show a concrete corrected line or pattern.",
    )
    variables_answer = send_debug_action(
        widget,
        "use_variables",
        user_text="Focus on values and report its length and contents.",
    )
    console_answer = send_debug_action(
        widget,
        "use_console",
        user_text="What marker was just printed?",
    )

    results["runtime_debug"] = {
        "explain_error": explain_answer,
        "fix_traceback": fix_answer,
        "use_variables": variables_answer,
        "use_console": console_answer,
        "latest_error": service.get_current_context().get("latest_error"),
        "runtime_tooltip_after_runtime_checks": widget.runtime_label.toolTip(),
        "explain_mentions_division": "division" in explain_answer.lower(),
        "variables_mentions_values": "values" in variables_answer.lower(),
        "variables_mentions_length": (
            "len=3" in variables_answer.lower()
            or "length: 3" in variables_answer.lower()
            or "length is 3" in variables_answer.lower()
            or "length 3" in variables_answer.lower()
            or "[1, 2, 3]" in variables_answer
        ),
        "console_mentions_marker": CONSOLE_MARKER.lower() in console_answer.lower(),
        "fix_traceback_non_empty": bool(fix_answer.strip()),
    }


def run_regenerate_check(window, results):
    """Verify regenerate replaces the last assistant answer."""
    widget = get_chat_widget(window)
    widget._add_new_tab(notify=False)
    answer_one = send_prompt(widget, "Reply with a single short sentence about arrays.")

    session = widget._active_session
    if len(session.messages) != 2:
        raise RuntimeError("Unexpected message count after initial prompt")

    widget.regenerate_btn.click()
    regenerated = wait_for(
        lambda: (
            not widget._generating
            and len(session.messages) == 2
            and session.messages[-1].get("role") == "assistant"
        ),
        timeout_ms=120000,
        step_ms=200,
    )
    if not regenerated:
        raise RuntimeError("Timed out waiting for regenerate response")

    results["regenerate"] = {
        "first_answer": answer_one,
        "second_answer": session.messages[-1]["content"],
        "message_count_after_regenerate": len(session.messages),
        "message_roles": [message.get("role") for message in session.messages],
        "regenerate_ok": len(session.messages) == 2,
    }


def run_export_check(window, results):
    """Verify export metadata from the active session."""
    widget = get_chat_widget(window)
    session = widget._active_session
    if session is None or not session.messages:
        raise RuntimeError("No active conversation is available for export")

    EXPORT_PATH.write_text(
        build_export_markdown(
            session.messages,
            model_name=widget._current_model,
            prompt_preset_label=get_chat_prompt_preset(
                session.prompt_preset_id
            )["label"],
            context_label=widget.context_label.text(),
            runtime_context=widget._runtime_context_snapshot,
        ),
        encoding="utf-8",
    )

    exported = EXPORT_PATH.read_text(encoding="utf-8")
    results["export"] = {
        "path": str(EXPORT_PATH),
        "headless_validation": True,
        "contains_model": "**Model:**" in exported,
        "contains_chat_mode": "**Chat mode:**" in exported,
        "contains_editor_context": "**Editor context:**" in exported,
        "contains_runtime_status": "**Runtime status:**" in exported,
        "contains_runtime_latest_error": "**Runtime latest error:**" in exported,
        "contains_runtime_variables": "**Runtime variables tracked:**" in exported,
        "preview": exported[:800],
    }


def run_validation(window):
    """Collect the end-to-end chat workflow validation results."""
    results = {
        "plugin_loaded": False,
        "errors": [],
        "artifacts": {
            "result": str(RESULT_PATH),
            "export": str(EXPORT_PATH),
        },
        "requested_model": CHAT_MODEL,
    }

    try:
        print("[validation] acquiring chat widget", flush=True)
        widget = get_chat_widget(window)
        results["plugin_loaded"] = get_ai_plugin(window) is not None
        print("[validation] selecting model", flush=True)
        select_model(widget, CHAT_MODEL)
        widget._clear_all_tabs()
        widget._add_new_tab(notify=False)
        results["chat_widget_loaded"] = True
        write_json(RESULT_PATH, results)
        print("[validation] opening editor fixture", flush=True)
        editor = open_validation_file(window, results)
        results["editor_loaded"] = editor is not None
        results["current_model"] = widget.model_combo.currentData()
        write_json(RESULT_PATH, results)
        print("[validation] apply actions", flush=True)
        run_apply_action_checks(window, results)
        write_json(RESULT_PATH, results)
        print("[validation] runtime debug checks", flush=True)
        run_runtime_debug_checks(window, results)
        write_json(RESULT_PATH, results)
        print("[validation] regenerate check", flush=True)
        run_regenerate_check(window, results)
        write_json(RESULT_PATH, results)
        print("[validation] export check", flush=True)
        run_export_check(window, results)
        write_json(RESULT_PATH, results)
        print("[validation] completed", flush=True)
    except Exception as exc:
        results["errors"].append({
            "type": type(exc).__name__,
            "message": str(exc),
            "traceback": traceback.format_exc(),
        })
    finally:
        print("[validation] finalizing", flush=True)
        record_validation_result(window, RESULT_PATH, results)
        finalize(window)


def main():
    """Launch Spyder and run the end-to-end chat workflow validation."""
    return run_spyder_validation(
        config_dir=CONFIG_DIR,
        filter_log=(
            "spyder_ai_assistant,spyder.plugins.projects,"
            "spyder.app.mainwindow"
        ),
        run_validation=run_validation,
        attr_name="_chat_workflow_validation_scheduled",
        delay_ms=3500,
    )


if __name__ == "__main__":
    raise SystemExit(main())
