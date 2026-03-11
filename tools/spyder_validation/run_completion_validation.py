"""Live completion-provider validation in a real Spyder session."""

from __future__ import annotations

import json
import time
import traceback
from pathlib import Path

from qtpy.QtCore import QEvent, Qt
from qtpy.QtGui import QKeyEvent
from qtpy.QtWidgets import QApplication

from spyder_ai_assistant.backend.client import OllamaClient
from tools.spyder_validation.common import (
    artifact_path,
    finalize,
    get_ai_plugin,
    get_editor_plugin,
    get_provider,
    record_validation_result,
    run_spyder_validation,
    wait_for,
    write_json,
    write_file,
)


CONFIG_DIR = artifact_path("configs", "completion-validation")
RESULT_PATH = artifact_path("results", "completion-validation.json")
TEST_FILE = artifact_path("fixtures", "completion_validation_sample.py")
HELPER_FILE = artifact_path("fixtures", "helpers_context.py")
REAL_COMPLETION_MODEL = "huihui_ai/qwen3-abliterated:30b-a3b-instruct-2507-q3_K_M"
REAL_OLLAMA_HOST = "http://localhost:11434"
INVALID_OLLAMA_HOST = "http://127.0.0.1:9"


FAKE_CALLS = []
ORIGINAL_GENERATE_COMPLETION = OllamaClient.generate_completion


def fake_generate_completion(self, model, prefix, suffix="", system=None,
                             options=None, single_line=False):
    """Return deterministic completions for UI behavior checks."""
    normalized_prefix = prefix.replace("\r\n", "\n")
    normalized_suffix = (suffix or "").replace("\r\n", "\n")
    call = {
        "model": model,
        "prefix_tail": normalized_prefix[-120:],
        "suffix_head": normalized_suffix[:80],
        "single_line": bool(single_line),
        "has_path_marker": normalized_prefix.startswith("# Path: "),
        "has_related_context": "related context" in normalized_prefix,
        "has_avoid_block": "avoid repeating these exact completions" in normalized_prefix,
    }
    FAKE_CALLS.append(call)
    time.sleep(0.35)

    if normalized_prefix.endswith("answer_value = "):
        return "42"
    if normalized_prefix.endswith("numbers_list = "):
        return "[1, 2, 3]"
    if normalized_prefix.endswith("result = func("):
        return ")"
    if normalized_prefix.endswith("result = a + "):
        return "b"
    if normalized_prefix.endswith("result = value + "):
        return "other_value"
    if normalized_prefix.endswith("cache_value = "):
        return "cached_item"
    if normalized_prefix.endswith("combined = compute_"):
        if "compute_total" in normalized_prefix and "helpers_context.py" in normalized_prefix:
            return "total(values)"
        return "fallback_total(values)"
    if normalized_prefix.endswith("cycle_value = "):
        if "avoid: primary_value" in normalized_prefix:
            return "secondary_value"
        return "primary_value"
    if normalized_prefix.endswith("values = [1, 2"):
        return ", 3]"
    if normalized_prefix.endswith("repeat_value = "):
        return "value value value value"
    if normalized_prefix.endswith("if value > 10:\n    "):
        return "return value * 2\n    log_value(value)\n"
    return ""


def send_key(editor, key, text="", modifiers=Qt.NoModifier):
    """Send one synthetic key press and release to the editor."""
    target = editor
    viewport = getattr(editor, "viewport", None)
    if callable(viewport):
        viewport = viewport()
    if viewport is not None:
        target = viewport

    QApplication.sendEvent(
        target,
        QKeyEvent(QEvent.KeyPress, key, modifiers, text),
    )
    QApplication.sendEvent(
        target,
        QKeyEvent(QEvent.KeyRelease, key, modifiers, text),
    )
    QApplication.instance().processEvents()


def build_key_event(key, text="", modifiers=Qt.NoModifier):
    """Build a synthetic key-press event for event-filter checks."""
    return QKeyEvent(QEvent.KeyPress, key, modifiers, text)


def move_cursor_after(editor, marker):
    """Move the cursor immediately after one marker string."""
    text = editor.toPlainText()
    offset = text.index(marker) + len(marker)
    cursor = editor.textCursor()
    cursor.setPosition(offset)
    editor.setTextCursor(cursor)
    editor.setFocus()
    QApplication.instance().processEvents()
    return offset


def set_editor_text(editor, provider, filename, text):
    """Replace the editor contents and wait for provider tracking."""
    editor.set_text(text)
    editor.document().setModified(False)
    tracked = wait_for(
        lambda: provider._document_states.get(filename)
        and provider._document_states[filename].text == text,
        timeout_ms=4000,
    )
    if not tracked:
        raise RuntimeError(f"Provider did not track text update for {filename}")
    return text


def clear_fake_calls():
    """Reset the deterministic completion call log."""
    FAKE_CALLS[:] = []


def get_manager(ai_plugin, editor):
    """Return the ghost-text manager installed for the current editor."""
    manager = ai_plugin._ghost_managers.get(id(editor))
    if manager is None:
        raise RuntimeError("GhostTextManager not installed on current editor")
    return manager


def ensure_clean_ghost(manager):
    """Clear any currently displayed ghost suggestion."""
    if manager.has_suggestion():
        manager.clear()
    QApplication.instance().processEvents()


def open_validation_file(window, results):
    """Open the tracked completion validation file inside Spyder."""
    editor_plugin = get_editor_plugin(window)
    ai_plugin = get_ai_plugin(window)
    write_file(
        HELPER_FILE,
        "def compute_total(values):\n"
        "    return sum(values)\n",
    )
    write_file(TEST_FILE, "result = a + \n")
    editor_plugin.load_edit(str(HELPER_FILE))
    editor_plugin.load_edit(str(TEST_FILE))

    editor = wait_for(lambda: editor_plugin.get_current_editor(), timeout_ms=4000)
    if editor is None:
        raise RuntimeError("Failed to open validation file in Spyder editor")

    wait_for(
        lambda: get_provider(window)._document_states.get(str(TEST_FILE)) is not None,
        timeout_ms=4000,
    )
    wait_for(
        lambda: get_provider(window)._document_states.get(str(HELPER_FILE)) is not None,
        timeout_ms=4000,
    )
    wait_for(lambda: ai_plugin._ghost_managers.get(id(editor)) is not None, timeout_ms=4000)
    wait_for(lambda: hasattr(editor, "_ai_chat_completion_shortcut"), timeout_ms=4000)
    wait_for(
        lambda: hasattr(editor, "_ai_chat_completion_accept_word_shortcut"),
        timeout_ms=4000,
    )
    wait_for(
        lambda: hasattr(editor, "_ai_chat_completion_accept_line_shortcut"),
        timeout_ms=4000,
    )

    results["startup"] = {
        "validation_file": str(TEST_FILE),
        "helper_file": str(HELPER_FILE),
        "editor_opened": editor is not None,
        "ghost_manager_installed": ai_plugin._ghost_managers.get(id(editor)) is not None,
        "manual_shortcut_installed": hasattr(editor, "_ai_chat_completion_shortcut"),
        "accept_word_shortcut_installed": hasattr(
            editor, "_ai_chat_completion_accept_word_shortcut"
        ),
        "accept_line_shortcut_installed": hasattr(
            editor, "_ai_chat_completion_accept_line_shortcut"
        ),
    }
    return editor


def run_fake_completion_checks(window, results):
    """Run deterministic UI checks against fake completions."""
    provider = get_provider(window)
    editor_plugin = get_editor_plugin(window)
    ai_plugin = get_ai_plugin(window)
    editor = editor_plugin.get_current_editor()
    manager = get_manager(ai_plugin, editor)

    clear_fake_calls()
    provider.set_conf("completion_model", "fake/completion-model")
    provider.set_conf("ollama_host", REAL_OLLAMA_HOST)
    provider.on_host_changed(REAL_OLLAMA_HOST)
    provider._set_ready_status()
    OllamaClient.generate_completion = fake_generate_completion

    checks = {}

    set_editor_text(editor, provider, str(TEST_FILE), "abc")
    move_cursor_after(editor, "abc")
    before = len(FAKE_CALLS)
    editor.do_completion()
    wait_for(lambda: len(FAKE_CALLS) > before, timeout_ms=700)
    checks["min_context_skip"] = {
        "skipped_before_worker": len(FAKE_CALLS) == before,
        "ghost_visible": manager.has_suggestion(),
    }
    ensure_clean_ghost(manager)

    set_editor_text(editor, provider, str(TEST_FILE), "result = value + other\n")
    move_cursor_after(editor, "value")
    before = len(FAKE_CALLS)
    editor.do_completion()
    wait_for(lambda: len(FAKE_CALLS) > before, timeout_ms=700)
    checks["middle_of_line_skip"] = {
        "skipped_before_worker": len(FAKE_CALLS) == before,
        "ghost_visible": manager.has_suggestion(),
    }
    ensure_clean_ghost(manager)

    set_editor_text(editor, provider, str(TEST_FILE), "result = func()\n")
    move_cursor_after(editor, "result = func(")
    before = len(FAKE_CALLS)
    editor.do_completion()
    wait_for(lambda: len(FAKE_CALLS) >= before + 1, timeout_ms=2500)
    wait_for(lambda: provider._request_queue.active_req_id is None, timeout_ms=2500)
    checks["already_present_filter"] = {
        "worker_called": len(FAKE_CALLS) >= before + 1,
        "ghost_visible": manager.has_suggestion(),
        "filtered_text": editor.toPlainText(),
    }
    ensure_clean_ghost(manager)

    set_editor_text(editor, provider, str(TEST_FILE), "numbers_list = \n")
    move_cursor_after(editor, "numbers_list = ")
    before = len(FAKE_CALLS)
    editor.do_completion()
    wait_for(lambda: provider._request_queue.active_req_id is not None, timeout_ms=1500)
    editor.insert_text("x")
    wait_for(lambda: len(FAKE_CALLS) >= before + 1, timeout_ms=2500)
    wait_for(lambda: provider._request_queue.active_req_id is None, timeout_ms=2500)
    checks["stale_discard"] = {
        "worker_called": len(FAKE_CALLS) >= before + 1,
        "ghost_visible": manager.has_suggestion(),
        "text_after_typing": editor.toPlainText(),
    }
    ensure_clean_ghost(manager)

    set_editor_text(editor, provider, str(TEST_FILE), "result = a + \n")
    move_cursor_after(editor, "result = a + ")
    before = len(FAKE_CALLS)
    editor.do_completion()
    wait_for(lambda: manager.has_suggestion(), timeout_ms=2500)
    call = FAKE_CALLS[-1] if len(FAKE_CALLS) >= before + 1 else {}
    checks["single_line_completion"] = {
        "ghost_visible": manager.has_suggestion(),
        "ghost_text": manager._ghost_text,
        "single_line": call.get("single_line"),
        "has_path_marker": call.get("has_path_marker"),
    }
    send_key(editor, Qt.Key_Tab)
    checks["tab_accept"] = {
        "text_after_accept": editor.toPlainText(),
        "ghost_visible_after_accept": manager.has_suggestion(),
    }

    set_editor_text(editor, provider, str(TEST_FILE), "result = value + \n")
    move_cursor_after(editor, "result = value + ")
    editor.do_completion()
    wait_for(lambda: manager.has_suggestion(), timeout_ms=2500)
    accept_word_consumed = manager._event_filter.eventFilter(
        editor,
        build_key_event(Qt.Key_Right, modifiers=Qt.AltModifier),
    )
    QApplication.instance().processEvents()
    checks["partial_accept_word"] = {
        "event_consumed": accept_word_consumed,
        "text_after_accept_word": editor.toPlainText(),
        "ghost_visible_after_accept_word": manager.has_suggestion(),
        "remaining_ghost_text": manager._ghost_text,
    }
    send_key(editor, Qt.Key_Tab)
    checks["partial_accept_word_finish"] = {
        "final_text": editor.toPlainText(),
        "ghost_visible_after_finish": manager.has_suggestion(),
    }

    set_editor_text(editor, provider, str(TEST_FILE), "if value > 10:\n    ")
    move_cursor_after(editor, "if value > 10:\n    ")
    before = len(FAKE_CALLS)
    editor.do_completion()
    wait_for(lambda: manager.has_suggestion(), timeout_ms=2500)
    call = FAKE_CALLS[-1] if len(FAKE_CALLS) >= before + 1 else {}
    checks["multiline_completion"] = {
        "ghost_visible": manager.has_suggestion(),
        "ghost_text": manager._ghost_text,
        "single_line": call.get("single_line"),
    }
    accept_line_consumed = manager._event_filter.eventFilter(
        editor,
        build_key_event(
            Qt.Key_Right,
            modifiers=Qt.AltModifier | Qt.ShiftModifier,
        ),
    )
    QApplication.instance().processEvents()
    checks["partial_accept_line"] = {
        "event_consumed": accept_line_consumed,
        "text_after_accept_line": editor.toPlainText(),
        "ghost_visible_after_accept_line": manager.has_suggestion(),
        "remaining_ghost_text": manager._ghost_text,
    }
    escape_consumed = manager._event_filter.eventFilter(
        editor,
        build_key_event(Qt.Key_Escape),
    )
    QApplication.instance().processEvents()
    checks["escape_dismiss"] = {
        "event_consumed": escape_consumed,
        "ghost_visible_after_escape": manager.has_suggestion(),
    }

    set_editor_text(editor, provider, str(TEST_FILE), "numbers_list = \n")
    move_cursor_after(editor, "numbers_list = ")
    editor.do_completion()
    wait_for(lambda: manager.has_suggestion(), timeout_ms=2500)
    typed_consumed = manager._event_filter.eventFilter(
        editor,
        build_key_event(Qt.Key_BracketLeft, "["),
    )
    QApplication.instance().processEvents()
    checks["typed_prefix_acceptance"] = {
        "event_consumed": typed_consumed,
        "ghost_visible_after_typed_prefix": manager.has_suggestion(),
        "remaining_ghost_text": manager._ghost_text,
        "text_after_typed_prefix": editor.toPlainText(),
    }
    send_key(editor, Qt.Key_Tab)
    checks["typed_prefix_then_tab"] = {
        "final_text": editor.toPlainText(),
        "ghost_visible_after_tab": manager.has_suggestion(),
    }

    set_editor_text(editor, provider, str(TEST_FILE), "values = [1, 2]\n")
    move_cursor_after(editor, "values = [1, 2")
    before = len(FAKE_CALLS)
    editor.do_completion()
    wait_for(lambda: manager.has_suggestion(), timeout_ms=2500)
    checks["suffix_overlap_trim"] = {
        "worker_called": len(FAKE_CALLS) >= before + 1,
        "ghost_text": manager._ghost_text,
        "text_with_ghost": editor.toPlainText(),
    }
    ensure_clean_ghost(manager)

    set_editor_text(editor, provider, str(TEST_FILE), "repeat_value = \n")
    move_cursor_after(editor, "repeat_value = ")
    before = len(FAKE_CALLS)
    editor.do_completion()
    wait_for(lambda: len(FAKE_CALLS) >= before + 1, timeout_ms=2500)
    wait_for(lambda: provider._request_queue.active_req_id is None, timeout_ms=2500)
    checks["repetition_filter"] = {
        "worker_called": len(FAKE_CALLS) >= before + 1,
        "ghost_visible": manager.has_suggestion(),
    }
    ensure_clean_ghost(manager)

    set_editor_text(editor, provider, str(TEST_FILE), "cache_value = \n")
    move_cursor_after(editor, "cache_value = ")
    before = len(FAKE_CALLS)
    editor.do_completion()
    wait_for(lambda: manager.has_suggestion(), timeout_ms=2500)
    checks["cache_warm"] = {
        "worker_called": len(FAKE_CALLS) >= before + 1,
        "ghost_text": manager._ghost_text,
    }
    ensure_clean_ghost(manager)
    first_calls = len(FAKE_CALLS)
    editor.do_completion()
    cached = bool(wait_for(lambda: manager.has_suggestion(), timeout_ms=1000))
    checks["cache_hit"] = {
        "ghost_visible": cached,
        "worker_called_again": len(FAKE_CALLS) > first_calls,
        "ghost_text": manager._ghost_text if cached else "",
    }
    ensure_clean_ghost(manager)

    set_editor_text(editor, provider, str(TEST_FILE), "combined = compute_\n")
    move_cursor_after(editor, "combined = compute_")
    before = len(FAKE_CALLS)
    editor.do_completion()
    wait_for(lambda: manager.has_suggestion(), timeout_ms=2500)
    related_call = FAKE_CALLS[-1] if len(FAKE_CALLS) >= before + 1 else {}
    checks["neighbor_context"] = {
        "ghost_visible": manager.has_suggestion(),
        "ghost_text": manager._ghost_text,
        "has_related_context": related_call.get("has_related_context"),
        "prefix_tail": related_call.get("prefix_tail"),
    }
    ensure_clean_ghost(manager)

    set_editor_text(editor, provider, str(TEST_FILE), "cycle_value = \n")
    move_cursor_after(editor, "cycle_value = ")
    before = len(FAKE_CALLS)
    editor.do_completion()
    wait_for(lambda: manager.has_suggestion(), timeout_ms=2500)
    primary_text = manager._ghost_text
    first_cycle_calls = len(FAKE_CALLS)

    editor.do_completion()
    wait_for(
        lambda: manager.has_suggestion() and manager._ghost_text != primary_text,
        timeout_ms=3000,
    )
    secondary_text = manager._ghost_text
    second_cycle_calls = len(FAKE_CALLS)

    editor.do_completion()
    wait_for(
        lambda: manager.has_suggestion() and manager._ghost_text == primary_text,
        timeout_ms=1500,
    )
    checks["candidate_cycling"] = {
        "primary_text": primary_text,
        "secondary_text": secondary_text,
        "cycled_back_text": manager._ghost_text,
        "second_request_hit_worker": second_cycle_calls > first_cycle_calls,
        "third_request_hit_worker": len(FAKE_CALLS) > second_cycle_calls,
    }
    ensure_clean_ghost(manager)

    popup_widget = getattr(editor, "completion_widget", None)
    popup_suppression = {}
    if popup_widget is not None:
        set_editor_text(editor, provider, str(TEST_FILE), "result = a + \n")
        move_cursor_after(editor, "result = a + ")
        editor.do_completion()
        wait_for(lambda: manager.has_suggestion(), timeout_ms=2500)
        if hasattr(popup_widget, "clear"):
            popup_widget.clear()
            popup_widget.addItem("native-popup-item")
        blocked = manager._popup_watcher.eventFilter(
            popup_widget,
            QEvent(QEvent.Show),
        )
        popup_suppression = {
            "popup_blocked": bool(blocked),
            "ghost_visible_after_popup_show": manager.has_suggestion(),
        }
        if hasattr(popup_widget, "clear"):
            popup_widget.clear()
        QApplication.instance().processEvents()
        ensure_clean_ghost(manager)
    checks["popup_suppression"] = popup_suppression

    results["completion_metrics"] = provider.get_metrics_snapshot()

    results["fake_completion_checks"] = checks
    results["fake_calls"] = list(FAKE_CALLS)


def run_actual_model_smoke(window, results):
    """Run one real-model completion smoke test."""
    provider = get_provider(window)
    editor_plugin = get_editor_plugin(window)
    ai_plugin = get_ai_plugin(window)
    editor = editor_plugin.get_current_editor()
    manager = get_manager(ai_plugin, editor)

    OllamaClient.generate_completion = ORIGINAL_GENERATE_COMPLETION
    provider.set_conf("ollama_host", REAL_OLLAMA_HOST)
    provider.on_host_changed(REAL_OLLAMA_HOST)
    provider.set_conf("completion_model", REAL_COMPLETION_MODEL)
    provider._set_ready_status()

    set_editor_text(editor, provider, str(TEST_FILE), "result = a + \n")
    move_cursor_after(editor, "result = a + ")
    ensure_clean_ghost(manager)
    editor.do_completion()
    got_ghost = bool(wait_for(lambda: manager.has_suggestion(), timeout_ms=20000))
    results["actual_model_smoke"] = {
        "model": REAL_COMPLETION_MODEL,
        "ghost_visible": got_ghost,
        "ghost_text": manager._ghost_text if got_ghost else "",
    }
    ensure_clean_ghost(manager)


def run_offline_recovery(window, results):
    """Verify the provider degrades cleanly and then recovers."""
    provider = get_provider(window)
    editor_plugin = get_editor_plugin(window)
    ai_plugin = get_ai_plugin(window)
    editor = editor_plugin.get_current_editor()
    manager = get_manager(ai_plugin, editor)

    OllamaClient.generate_completion = ORIGINAL_GENERATE_COMPLETION
    provider.set_conf("ollama_host", INVALID_OLLAMA_HOST)
    provider.on_host_changed(INVALID_OLLAMA_HOST)
    provider.set_conf("completion_model", REAL_COMPLETION_MODEL)

    set_editor_text(editor, provider, str(TEST_FILE), "result = a + \n")
    move_cursor_after(editor, "result = a + ")
    ensure_clean_ghost(manager)
    editor.do_completion()
    wait_for(lambda: provider._request_queue.active_req_id is None, timeout_ms=12000)
    offline_status = {
        "ghost_visible": manager.has_suggestion(),
        "current_host": provider.get_conf("ollama_host"),
    }

    provider.set_conf("ollama_host", REAL_OLLAMA_HOST)
    provider.on_host_changed(REAL_OLLAMA_HOST)
    OllamaClient.generate_completion = fake_generate_completion
    provider.set_conf("completion_model", "fake/completion-model")
    provider._set_ready_status()

    clear_fake_calls()
    set_editor_text(editor, provider, str(TEST_FILE), "result = a + \n")
    move_cursor_after(editor, "result = a + ")
    editor.do_completion()
    recovered = bool(wait_for(lambda: manager.has_suggestion(), timeout_ms=3000))
    offline_status["recovered_after_host_restore"] = recovered
    offline_status["recovered_ghost_text"] = manager._ghost_text if recovered else ""
    results["offline_recovery"] = offline_status
    ensure_clean_ghost(manager)


def run_validation(window):
    """Collect the completion validation results for one live run."""
    results = {
        "plugin_loaded": False,
        "provider_started": False,
        "errors": [],
        "artifacts": {
            "result": str(RESULT_PATH),
        },
    }

    try:
        print("[validation] acquiring completion provider", flush=True)
        provider = get_provider(window)
        results["plugin_loaded"] = get_ai_plugin(window) is not None
        results["provider_started"] = provider._started
        print("[validation] opening editor fixtures", flush=True)
        open_validation_file(window, results)
        print("[validation] running deterministic completion checks", flush=True)
        run_fake_completion_checks(window, results)
        print("[validation] running real completion smoke", flush=True)
        run_actual_model_smoke(window, results)
        print("[validation] running offline recovery", flush=True)
        run_offline_recovery(window, results)
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
    """Launch Spyder and run the completion validation harness."""
    return run_spyder_validation(
        config_dir=CONFIG_DIR,
        filter_log=(
            "spyder_ai_assistant,spyder.app.mainwindow,"
            "spyder.plugins.completion,spyder.plugins.editor"
        ),
        run_validation=run_validation,
        attr_name="_completion_validation_scheduled",
        delay_ms=2500,
    )


if __name__ == "__main__":
    raise SystemExit(main())
