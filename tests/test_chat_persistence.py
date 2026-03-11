"""Unit tests for chat session persistence helpers."""

from __future__ import annotations

import json

from spyder_ai_assistant.utils.chat_persistence import (
    CHAT_SESSION_STATE_VERSION,
    build_chat_session_history_rows,
    load_chat_session_state,
    merge_chat_session_history,
    remove_chat_session_from_history,
    save_chat_session_state,
)
from spyder_ai_assistant.utils.prompt_library import (
    DEFAULT_CHAT_PROMPT_PRESET,
)


def test_load_chat_session_state_returns_empty_for_missing_file(tmp_path):
    assert load_chat_session_state(tmp_path / "missing.json") == {}


def test_load_chat_session_state_normalizes_legacy_payload(tmp_path):
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

    assert state["version"] == CHAT_SESSION_STATE_VERSION
    assert state["active_index"] == 0
    assert len(state["sessions"]) == 1
    assert len(state["history"]) == 1

    session = state["sessions"][0]
    assert session["title"] == ""
    assert session["messages"] == [
        {"role": "user", "content": "Hello"},
        {"role": "assistant", "content": "123"},
    ]
    assert session["session_id"]
    assert session["created_at"]
    assert session["updated_at"]
    assert session["prompt_preset_id"] == DEFAULT_CHAT_PROMPT_PRESET
    assert session["temperature_override"] is None
    assert session["max_tokens_override"] is None

    assert state["history"][0]["session_id"] == session["session_id"]


def test_save_chat_session_state_writes_sessions_and_history(tmp_path):
    path = tmp_path / "nested" / "sessions.json"
    state = {
        "active_index": -5,
        "sessions": [
            {
                "session_id": "open-session",
                "title": "Session 1",
                "created_at": "2026-03-11T12:00:00Z",
                "updated_at": "2026-03-11T12:05:00Z",
                "prompt_preset_id": "debugging",
                "temperature_override": 0.2,
                "max_tokens_override": 512,
                "messages": [
                    {"role": "user", "content": "hello"},
                    {"role": "assistant", "content": "world"},
                    {"role": "tool", "content": "ignored"},
                ],
            }
        ],
        "history": [
            {
                "session_id": "archived-session",
                "title": "Archived",
                "created_at": "2026-03-10T09:00:00Z",
                "updated_at": "2026-03-10T09:30:00Z",
                "prompt_preset_id": "documentation",
                "temperature_override": None,
                "max_tokens_override": 2048,
                "messages": [
                    {"role": "user", "content": "alpha"},
                    {"role": "assistant", "content": "beta"},
                ],
            }
        ],
    }

    assert save_chat_session_state(path, state) is True

    payload = json.loads(path.read_text(encoding="utf-8"))
    assert payload["version"] == CHAT_SESSION_STATE_VERSION
    assert payload["active_index"] == 0
    assert [session["session_id"] for session in payload["sessions"]] == [
        "open-session"
    ]
    assert [session["session_id"] for session in payload["history"]] == [
        "open-session",
        "archived-session",
    ]
    assert payload["sessions"][0]["prompt_preset_id"] == "debugging"
    assert payload["sessions"][0]["temperature_override"] == 0.2
    assert payload["sessions"][0]["max_tokens_override"] == 512
    assert payload["history"][1]["prompt_preset_id"] == "documentation"
    assert payload["history"][1]["temperature_override"] is None
    assert payload["history"][1]["max_tokens_override"] == 2048


def test_merge_chat_session_history_preserves_closed_sessions():
    merged = merge_chat_session_history(
        open_sessions=[
            {
                "session_id": "open-session",
                "title": "Open",
                "created_at": "2026-03-11T12:00:00Z",
                "updated_at": "2026-03-11T12:10:00Z",
                "prompt_preset_id": "documentation",
                "temperature_override": 0.3,
                "max_tokens_override": 256,
                "messages": [{"role": "user", "content": "open"}],
            }
        ],
        history_sessions=[
            {
                "session_id": "closed-session",
                "title": "Closed",
                "created_at": "2026-03-10T08:00:00Z",
                "updated_at": "2026-03-10T08:30:00Z",
                "prompt_preset_id": "debugging",
                "temperature_override": None,
                "max_tokens_override": None,
                "messages": [{"role": "user", "content": "closed"}],
            }
        ],
    )

    assert [session["session_id"] for session in merged] == [
        "open-session",
        "closed-session",
    ]


def test_merge_chat_session_history_updates_matching_open_session():
    merged = merge_chat_session_history(
        open_sessions=[
            {
                "session_id": "shared-session",
                "title": "Updated title",
                "created_at": "2026-03-11T12:00:00Z",
                "updated_at": "2026-03-11T12:10:00Z",
                "prompt_preset_id": "documentation",
                "temperature_override": 0.4,
                "max_tokens_override": 256,
                "messages": [
                    {"role": "user", "content": "new question"},
                    {"role": "assistant", "content": "new answer"},
                ],
            }
        ],
        history_sessions=[
            {
                "session_id": "shared-session",
                "title": "Old title",
                "created_at": "2026-03-11T12:00:00Z",
                "updated_at": "2026-03-11T12:05:00Z",
                "prompt_preset_id": "coding",
                "temperature_override": None,
                "max_tokens_override": 1024,
                "messages": [{"role": "user", "content": "old question"}],
            }
        ],
    )

    assert len(merged) == 1
    assert merged[0]["title"] == "Updated title"
    assert merged[0]["messages"][-1]["content"] == "new answer"
    assert merged[0]["prompt_preset_id"] == "documentation"
    assert merged[0]["temperature_override"] == 0.4
    assert merged[0]["max_tokens_override"] == 256


def test_remove_chat_session_from_history_removes_matching_id():
    history, removed = remove_chat_session_from_history(
        [
            {
                "session_id": "keep-me",
                "title": "Keep",
                "created_at": "2026-03-10T08:00:00Z",
                "updated_at": "2026-03-10T08:30:00Z",
                "prompt_preset_id": "coding",
                "messages": [{"role": "user", "content": "keep"}],
            },
            {
                "session_id": "delete-me",
                "title": "Delete",
                "created_at": "2026-03-11T08:00:00Z",
                "updated_at": "2026-03-11T08:30:00Z",
                "prompt_preset_id": "debugging",
                "messages": [{"role": "user", "content": "delete"}],
            },
        ],
        "delete-me",
    )

    assert removed is True
    assert [session["session_id"] for session in history] == ["keep-me"]


def test_build_chat_session_history_rows_marks_open_sessions():
    rows = build_chat_session_history_rows(
        [
            {
                "session_id": "open-session",
                "title": "Open",
                "created_at": "2026-03-11T12:00:00Z",
                "updated_at": "2026-03-11T12:10:00Z",
                "prompt_preset_id": "debugging",
                "messages": [{"role": "user", "content": "open preview"}],
            },
            {
                "session_id": "empty-session",
                "title": "Empty",
                "created_at": "2026-03-11T12:00:00Z",
                "updated_at": "2026-03-11T12:10:00Z",
                "prompt_preset_id": "documentation",
                "messages": [],
            },
        ],
        open_session_ids={"open-session"},
    )

    assert len(rows) == 1
    assert rows[0]["session_id"] == "open-session"
    assert rows[0]["is_open"] is True
    assert rows[0]["preview"] == "open preview"


def test_build_chat_session_history_rows_sorts_newest_first():
    rows = build_chat_session_history_rows(
        [
            {
                "session_id": "older-session",
                "title": "Older",
                "created_at": "2026-03-10T12:00:00Z",
                "updated_at": "2026-03-10T12:10:00Z",
                "prompt_preset_id": "debugging",
                "messages": [{"role": "user", "content": "older preview"}],
            },
            {
                "session_id": "newer-session",
                "title": "Newer",
                "created_at": "2026-03-11T12:00:00Z",
                "updated_at": "2026-03-11T12:10:00Z",
                "prompt_preset_id": "documentation",
                "messages": [{"role": "user", "content": "newer preview"}],
            },
        ],
    )

    assert [row["session_id"] for row in rows] == [
        "newer-session",
        "older-session",
    ]


def test_load_chat_session_state_normalizes_invalid_prompt_preset(tmp_path):
    path = tmp_path / "sessions.json"
    path.write_text(
        json.dumps(
            {
                "active_index": 0,
                "sessions": [
                    {
                        "title": "Preset",
                        "prompt_preset_id": "not-a-real-preset",
                        "messages": [
                            {"role": "user", "content": "Hello"},
                        ],
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    state = load_chat_session_state(path)

    assert state["sessions"][0]["prompt_preset_id"] == DEFAULT_CHAT_PROMPT_PRESET
