"""Unit tests for chat widget Phase 1 helpers and send flow."""

import unittest
from types import SimpleNamespace

from spyder_ai_chat.widgets.chat_widget import (
    ChatSessionStore,
    ChatWidget,
    _normalize_chat_temperature,
)


class _FakeInput:
    """Minimal chat input stub."""

    def __init__(self, text):
        self._text = text
        self.clear_calls = 0

    def peek_text(self):
        return self._text.strip()

    def clear_text(self):
        self.clear_calls += 1
        self._text = ""

    def setPlainText(self, text):
        self._text = text


class _FakeDisplay:
    """Minimal display stub for send-flow assertions."""

    def __init__(self):
        self.errors = []
        self.user_messages = []
        self.started = 0

    def append_error(self, message):
        self.errors.append(message)

    def append_user_message(self, message):
        self.user_messages.append(message)

    def start_assistant_message(self):
        self.started += 1


class _FakeSignal:
    """Signal stub that records emitted payloads."""

    def __init__(self):
        self.calls = []

    def emit(self, *args):
        self.calls.append(args)


class _FakeTabWidget:
    """Tiny tab widget shim for ChatSessionStore tests."""

    def __init__(self, widgets):
        self._widgets = list(widgets)

    def count(self):
        return len(self._widgets)

    def widget(self, index):
        return self._widgets[index]

    def reorder(self, widgets):
        self._widgets = list(widgets)


class ChatWidgetHelperTests(unittest.TestCase):
    """Cover pure helper logic introduced for Phase 1."""

    def test_normalize_chat_temperature_accepts_old_and_new_formats(self):
        self.assertEqual(_normalize_chat_temperature(5), 0.5)
        self.assertEqual(_normalize_chat_temperature(0.7), 0.7)
        self.assertEqual(_normalize_chat_temperature("12"), 1.2)
        self.assertEqual(_normalize_chat_temperature("bad"), 0.5)

    def test_session_store_tracks_widgets_across_reordering(self):
        display_a = object()
        display_b = object()
        session_a = SimpleNamespace(display=display_a)
        session_b = SimpleNamespace(display=display_b)
        store = ChatSessionStore()
        store.add(session_a)
        store.add(session_b)

        tab_widget = _FakeTabWidget([display_a, display_b])
        self.assertIs(store.get_for_index(tab_widget, 0), session_a)
        self.assertEqual(store.index_of(tab_widget, session_b), 1)

        tab_widget.reorder([display_b, display_a])
        self.assertIs(store.get_for_index(tab_widget, 0), session_b)
        self.assertEqual(store.index_of(tab_widget, session_a), 1)


class ChatSendFlowTests(unittest.TestCase):
    """Cover Phase 1 send-flow correctness without full Qt widget setup."""

    def _make_conf(self):
        values = {
            "chat_system_prompt": "system prompt",
            "chat_temperature": 5,
            "max_tokens": 256,
        }

        def get_conf(name, default=None):
            return values.get(name, default)

        return get_conf

    def test_send_does_not_clear_text_while_generation_is_running(self):
        fake_widget = SimpleNamespace(
            chat_input=_FakeInput("hello"),
            _generating=True,
            _active_session=None,
        )

        ChatWidget._send_message(fake_widget)

        self.assertEqual(fake_widget.chat_input.clear_calls, 0)
        self.assertEqual(fake_widget.chat_input.peek_text(), "hello")

    def test_send_does_not_clear_text_when_model_is_missing(self):
        session = SimpleNamespace(display=_FakeDisplay(), messages=[])
        fake_widget = SimpleNamespace(
            chat_input=_FakeInput("hello"),
            _generating=False,
            _active_session=session,
            _current_model="",
        )

        ChatWidget._send_message(fake_widget)

        self.assertEqual(fake_widget.chat_input.clear_calls, 0)
        self.assertEqual(fake_widget.chat_input.peek_text(), "hello")
        self.assertEqual(len(session.display.errors), 1)

    def test_send_clears_text_only_after_validation_and_normalizes_temperature(self):
        session = SimpleNamespace(display=_FakeDisplay(), messages=[])
        signal = _FakeSignal()
        generating_flags = []
        fake_widget = SimpleNamespace(
            chat_input=_FakeInput("print('ok')"),
            _generating=False,
            _active_session=session,
            _current_model="demo-model",
            _context_provider=None,
            _generating_session=None,
            sig_send_chat=signal,
            get_conf=self._make_conf(),
            _set_generating=generating_flags.append,
        )

        ChatWidget._send_message(fake_widget)

        self.assertEqual(fake_widget.chat_input.clear_calls, 1)
        self.assertEqual(session.display.user_messages, ["print('ok')"])
        self.assertEqual(session.display.started, 1)
        self.assertEqual(generating_flags, [True])
        model, messages, options = signal.calls[0]
        self.assertEqual(model, "demo-model")
        self.assertEqual(messages[0]["role"], "system")
        self.assertEqual(options["temperature"], 0.5)
        self.assertEqual(options["num_predict"], 256)


if __name__ == "__main__":
    unittest.main()
