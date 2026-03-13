"""Unit tests for ghost-text interaction helpers."""

from __future__ import annotations

from qtpy.QtCore import QEvent, Qt
from qtpy.QtWidgets import QApplication, QPlainTextEdit, QWidget

from spyder_ai_assistant.widgets.ghost_text import GhostTextManager


_QT_APP = None


class _TestEditor(QPlainTextEdit):
    """Minimal editor stub for ghost-text behavior tests."""

    def __init__(self):
        super().__init__()
        self.completion_widget = QWidget()

    def insert_text(self, text):
        """Mirror Spyder's ``CodeEditor.insert_text`` helper."""
        cursor = self.textCursor()
        cursor.insertText(text)
        self.setTextCursor(cursor)

    def do_completion(self):
        """No-op completion hook used by the manager shortcut."""
        self.did_completion = True


def _app():
    """Return one QApplication for widget tests."""
    global _QT_APP
    _QT_APP = QApplication.instance() or QApplication([])
    return _QT_APP


def _move_cursor_to_end(editor):
    """Move the cursor to the end of the current document."""
    cursor = editor.textCursor()
    cursor.setPosition(len(editor.toPlainText()))
    editor.setTextCursor(cursor)


def test_accept_next_word_keeps_the_remaining_ghost_text():
    _app()
    events = []
    editor = _TestEditor()
    editor.setPlainText("result = ")
    _move_cursor_to_end(editor)
    manager = GhostTextManager(
        editor,
        lifecycle_callback=lambda event, payload: events.append((event, payload)),
    )

    assert manager.show_suggestion("value + other_value")
    assert manager.accept_next_word()

    assert editor.toPlainText() == "result = value + other_value"
    assert manager.has_suggestion()
    assert manager._ghost_text == "+ other_value"
    assert any(
        event == "accepted" and payload.get("method") == "word"
        for event, payload in events
    )


def test_accept_next_line_keeps_following_lines_as_ghost_text():
    _app()
    editor = _TestEditor()
    editor.setPlainText("if flag:\n    ")
    _move_cursor_to_end(editor)
    manager = GhostTextManager(editor)

    assert manager.show_suggestion("return value\n    log(value)\n")
    assert manager.accept_next_line()

    assert editor.toPlainText() == "if flag:\n    return value\n    log(value)\n"
    assert manager.has_suggestion()
    assert manager._ghost_text == "    log(value)\n"


def test_popup_visibility_suppresses_and_clears_ghost_text():
    _app()
    events = []
    editor = _TestEditor()
    editor.setPlainText("result = ")
    _move_cursor_to_end(editor)
    manager = GhostTextManager(
        editor,
        lifecycle_callback=lambda event, payload: events.append((event, payload)),
    )

    assert manager.show_suggestion("value")
    blocked = manager._popup_watcher.eventFilter(
        editor.completion_widget,
        QEvent(QEvent.Show),
    )
    assert blocked
    assert manager.has_suggestion()
    assert any(
        event == "suppressed" and payload.get("reason") == "native_popup"
        for event, payload in events
    )


def test_typing_through_one_matching_prefix_advances_the_ghost_text():
    _app()
    events = []
    editor = _TestEditor()
    editor.setPlainText("numbers = ")
    _move_cursor_to_end(editor)
    manager = GhostTextManager(
        editor,
        lifecycle_callback=lambda event, payload: events.append((event, payload)),
    )

    assert manager.show_suggestion("[1, 2, 3]")
    event = type(
        "KeyEvent",
        (),
        {
            "text": lambda self: "[",
            "modifiers": lambda self: Qt.NoModifier,
        },
    )()
    assert manager.try_accept_typed_text(event)

    assert editor.toPlainText() == "numbers = [1, 2, 3]"
    assert manager.has_suggestion()
    assert manager._ghost_text == "1, 2, 3]"
    assert any(
        event_name == "advanced" and payload.get("method") == "typed"
        for event_name, payload in events
    )


def test_request_completion_prefers_manual_ai_requester():
    _app()
    editor = _TestEditor()
    requests = []
    manager = GhostTextManager(
        editor,
        manual_completion_requester=lambda: requests.append("manual"),
    )

    manager.request_completion()

    assert requests == ["manual"]
    assert not getattr(editor, "did_completion", False)


def test_request_completion_falls_back_to_editor_completion():
    _app()
    editor = _TestEditor()
    manager = GhostTextManager(editor)

    manager.request_completion()

    assert editor.did_completion


def test_request_completion_falls_back_when_manual_requester_declines():
    _app()
    editor = _TestEditor()
    manager = GhostTextManager(
        editor,
        manual_completion_requester=lambda: False,
    )

    manager.request_completion()

    assert editor.did_completion


def test_idle_completion_requests_when_editor_is_focused():
    _app()
    editor = _TestEditor()
    requests = []
    manager = GhostTextManager(
        editor,
        manual_completion_requester=lambda: requests.append("manual"),
    )
    manager._editor_has_focus = lambda: True

    manager._request_idle_completion()

    assert requests == ["manual"]


def test_idle_completion_skips_when_ghost_text_is_visible():
    _app()
    editor = _TestEditor()
    requests = []
    manager = GhostTextManager(
        editor,
        manual_completion_requester=lambda: requests.append("manual"),
    )
    manager._editor_has_focus = lambda: True
    editor.setPlainText("result = ")
    _move_cursor_to_end(editor)
    assert manager.show_suggestion("value")

    manager._request_idle_completion()

    assert requests == []


def test_idle_completion_scheduler_stops_without_focus():
    _app()
    editor = _TestEditor()
    manager = GhostTextManager(editor)
    manager._editor_has_focus = lambda: False

    manager._schedule_idle_completion()

    assert not manager._idle_completion_timer.isActive()
