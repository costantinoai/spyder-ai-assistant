"""Live Phase 11 validation for the apply-preview workflow."""

from __future__ import annotations

import traceback

from qtpy.QtGui import QTextCursor
from qtpy.QtWidgets import QApplication

from spyder_ai_assistant.utils.code_apply import (
    APPLY_MODE_INSERT,
    APPLY_MODE_REPLACE,
)
from tools.spyder_validation.common import (
    apply_chat_code_via_dialog,
    artifact_path,
    finalize,
    get_chat_widget,
    get_editor_plugin,
    record_validation_result,
    run_spyder_validation,
    write_file,
    write_json,
)


CONFIG_DIR = artifact_path("configs", "phase11-apply-preview-validation")
RESULT_PATH = artifact_path("results", "phase11-apply-preview-validation.json")
TEST_FILE = artifact_path("fixtures", "phase11_apply_preview_sample.py")


def _open_validation_file(window):
    editor_plugin = get_editor_plugin(window)
    write_file(TEST_FILE, "alpha = 1\nbeta = 2\n")
    editor_plugin.load_edit(str(TEST_FILE))
    editor = editor_plugin.get_current_editor()
    if editor is None:
        raise RuntimeError("Failed to open the Phase 11 validation file")
    return editor


def _move_cursor_to_end(editor):
    cursor = editor.textCursor()
    cursor.setPosition(len(editor.toPlainText()))
    editor.setTextCursor(cursor)
    editor.setFocus()
    QApplication.instance().processEvents()


def _select_text(editor, text):
    full_text = editor.toPlainText()
    start = full_text.index(text)
    end = start + len(text)
    cursor = editor.textCursor()
    cursor.setPosition(start)
    cursor.setPosition(end, QTextCursor.KeepAnchor)
    editor.setTextCursor(cursor)
    editor.setFocus()
    QApplication.instance().processEvents()


def run_validation(window):
    """Validate the real apply-preview dialog against a live editor."""
    results = {"phase": "11", "errors": []}

    try:
        widget = get_chat_widget(window)
        editor = _open_validation_file(window)

        results["controls"] = [
            widget.debug_menu_btn.text(),
            widget.regenerate_btn.text(),
            widget.history_btn.text(),
            widget.chat_settings_btn.text(),
            widget.more_btn.text(),
            widget.stop_btn.text(),
            widget.send_btn.text(),
        ]

        print("[phase11] validating insert preview cancel", flush=True)
        _move_cursor_to_end(editor)
        insert_cancel = apply_chat_code_via_dialog(
            widget,
            "gamma = 3\n",
            mode=APPLY_MODE_INSERT,
            accept=False,
        )
        after_insert_cancel = editor.toPlainText()
        print("[phase11] insert cancel diff:", insert_cancel["diff_text"], flush=True)

        print("[phase11] validating insert preview apply + undo", flush=True)
        _move_cursor_to_end(editor)
        insert_apply = apply_chat_code_via_dialog(
            widget,
            "gamma = 3\n",
            mode=APPLY_MODE_INSERT,
            accept=True,
        )
        after_insert_apply = editor.toPlainText()
        print("[phase11] insert apply diff:", insert_apply["diff_text"], flush=True)
        print("[phase11] undoing insert apply", flush=True)
        editor.document().undo()
        QApplication.instance().processEvents()
        after_insert_undo = editor.toPlainText()
        print("[phase11] insert undo result captured", flush=True)

        print("[phase11] validating replace preview cancel", flush=True)
        editor.set_text("alpha = 1\nbeta = 2\n")
        editor.document().setModified(False)
        _select_text(editor, "beta = 2")
        replace_cancel = apply_chat_code_via_dialog(
            widget,
            "beta = 99",
            mode=APPLY_MODE_REPLACE,
            accept=False,
        )
        after_replace_cancel = editor.toPlainText()
        print("[phase11] replace cancel diff:", replace_cancel["diff_text"], flush=True)

        print("[phase11] validating replace preview apply + undo", flush=True)
        _select_text(editor, "beta = 2")
        replace_apply = apply_chat_code_via_dialog(
            widget,
            "beta = 99",
            mode=APPLY_MODE_REPLACE,
            accept=True,
        )
        after_replace_apply = editor.toPlainText()
        print("[phase11] replace apply diff:", replace_apply["diff_text"], flush=True)
        print("[phase11] undoing replace apply", flush=True)
        editor.document().undo()
        QApplication.instance().processEvents()
        after_replace_undo = editor.toPlainText()
        print("[phase11] replace undo result captured", flush=True)

        results["insert"] = {
            "cancel_summary": insert_cancel["summary"],
            "cancel_diff": insert_cancel["diff_text"],
            "after_cancel": after_insert_cancel,
            "apply_summary": insert_apply["summary"],
            "apply_diff": insert_apply["diff_text"],
            "after_apply": after_insert_apply,
            "after_undo": after_insert_undo,
        }
        results["replace"] = {
            "cancel_summary": replace_cancel["summary"],
            "cancel_diff": replace_cancel["diff_text"],
            "after_cancel": after_replace_cancel,
            "apply_summary": replace_apply["summary"],
            "apply_diff": replace_apply["diff_text"],
            "after_apply": after_replace_apply,
            "after_undo": after_replace_undo,
        }

        if after_insert_cancel != "alpha = 1\nbeta = 2\n":
            raise RuntimeError("Insert-preview cancel still mutated the editor")
        if after_insert_apply != "alpha = 1\nbeta = 2\ngamma = 3\n":
            raise RuntimeError("Insert-preview apply did not insert the code")
        if after_insert_undo != "alpha = 1\nbeta = 2\n":
            raise RuntimeError("Insert-preview apply was not grouped into one undo step")
        if after_replace_cancel != "alpha = 1\nbeta = 2\n":
            raise RuntimeError("Replace-preview cancel still mutated the editor")
        if after_replace_apply != "alpha = 1\nbeta = 99\n":
            raise RuntimeError("Replace-preview apply did not replace the selection")
        if after_replace_undo != "alpha = 1\nbeta = 2\n":
            raise RuntimeError("Replace-preview apply was not grouped into one undo step")
    except Exception as error:  # pragma: no cover - live guard
        results["errors"].append(str(error))
        results["traceback"] = traceback.format_exc()

    record_validation_result(window, RESULT_PATH, results)
    write_json(RESULT_PATH, results)
    finalize(window)


if __name__ == "__main__":
    raise SystemExit(
        run_spyder_validation(
            CONFIG_DIR,
            filter_log="spyder_ai_assistant",
            run_validation=run_validation,
            attr_name="_phase11_apply_preview_validation_ran",
        )
    )
