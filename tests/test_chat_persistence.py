"""Unit tests for chat session persistence helpers."""

from __future__ import annotations

import json

from spyder_ai_assistant.utils.chat_persistence import (
    CHAT_SESSION_STATE_VERSION,
    load_chat_session_state,
    save_chat_session_state,
)


def test_load_chat_session_state_returns_empty_for_missing_file(tmp_path):
    assert load_chat_session_state(tmp_path / "missing.json") == {}


def test_load_chat_session_state_normalizes_invalid_payload(tmp_path):
    path = tmp_path / "sessions.json"
    path.write_text(
        json.dumps(
            {
                "active_index": "bad",
                "sessions": [
                    {
                        "title": 42,
                        "messages": [
                            {"role": "user", "content": "Hello"},
                            {"role": "assistant", "content": 123},
                            {"role": "system", "content": "ignored"},
                            "bad-item",
                        ],
                    },
                    "bad-session",
                ],
            }
        ),
        encoding="utf-8",
    )

    state = load_chat_session_state(path)

    assert state == {
        "version": CHAT_SESSION_STATE_VERSION,
        "active_index": 0,
        "sessions": [
            {
                "title": "",
                "messages": [
                    {"role": "user", "content": "Hello"},
                    {"role": "assistant", "content": "123"},
                ],
            }
        ],
    }


def test_save_chat_session_state_writes_normalized_json(tmp_path):
    path = tmp_path / "nested" / "sessions.json"
    state = {
        "active_index": -5,
        "sessions": [
            {
                "title": "Session 1",
                "messages": [
                    {"role": "user", "content": "hello"},
                    {"role": "assistant", "content": "world"},
                    {"role": "tool", "content": "ignored"},
                ],
            }
        ],
    }

    assert save_chat_session_state(path, state) is True

    payload = json.loads(path.read_text(encoding="utf-8"))
    assert payload == {
        "version": CHAT_SESSION_STATE_VERSION,
        "active_index": 0,
        "sessions": [
            {
                "title": "Session 1",
                "messages": [
                    {"role": "user", "content": "hello"},
                    {"role": "assistant", "content": "world"},
                ],
            }
        ],
    }
