"""Unit tests for chat exchange browsing and deletion helpers."""

from __future__ import annotations

from spyder_ai_assistant.utils.chat_exchanges import (
    build_chat_exchange_rows,
    delete_chat_exchange,
)


def test_build_chat_exchange_rows_pairs_user_and_assistant_messages():
    rows = build_chat_exchange_rows(
        [
            {"role": "user", "content": "first question"},
            {"role": "assistant", "content": "first answer"},
            {"role": "user", "content": "second question"},
            {"role": "assistant", "content": "second answer"},
        ]
    )

    assert [row["title"] for row in rows] == ["Turn 1", "Turn 2"]
    assert rows[0]["user_preview"] == "first question"
    assert rows[0]["status"] == "Answered"
    assert "AI:\nfirst answer" in rows[0]["preview"]


def test_build_chat_exchange_rows_keeps_pending_user_turns():
    rows = build_chat_exchange_rows(
        [
            {"role": "user", "content": "still waiting"},
        ]
    )

    assert len(rows) == 1
    assert rows[0]["status"] == "Pending"
    assert rows[0]["preview"] == "You:\nstill waiting"


def test_delete_chat_exchange_removes_middle_exchange_pair():
    updated, removed = delete_chat_exchange(
        [
            {"role": "user", "content": "first"},
            {"role": "assistant", "content": "answer one"},
            {"role": "user", "content": "second"},
            {"role": "assistant", "content": "answer two"},
            {"role": "user", "content": "third"},
            {"role": "assistant", "content": "answer three"},
        ],
        exchange_index=1,
    )

    assert removed is True
    assert updated == [
        {"role": "user", "content": "first"},
        {"role": "assistant", "content": "answer one"},
        {"role": "user", "content": "third"},
        {"role": "assistant", "content": "answer three"},
    ]


def test_delete_chat_exchange_rejects_missing_indices():
    messages = [
        {"role": "user", "content": "only"},
        {"role": "assistant", "content": "once"},
    ]

    updated, removed = delete_chat_exchange(messages, exchange_index=99)

    assert removed is False
    assert updated == messages
