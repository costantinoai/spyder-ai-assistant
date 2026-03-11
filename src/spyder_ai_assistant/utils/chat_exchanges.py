"""Helpers for browsing and deleting chat exchanges within one session."""

from __future__ import annotations


def build_chat_exchange_rows(messages):
    """Return browser-friendly rows for the visible exchange list."""
    rows = []
    for exchange_index, exchange in enumerate(_build_chat_exchanges(messages)):
        user_content = exchange.get("user_content", "")
        assistant_content = exchange.get("assistant_content", "")
        preview_parts = []
        if user_content:
            preview_parts.append(f"You:\n{user_content}")
        if assistant_content:
            preview_parts.append(f"\nAI:\n{assistant_content}")
        preview = "\n".join(part for part in preview_parts if part)
        status = "Answered" if assistant_content else "Pending"
        rows.append(
            {
                "exchange_index": exchange_index,
                "title": f"Turn {exchange_index + 1}",
                "user_preview": _preview_text(user_content or assistant_content),
                "status": status,
                "preview": preview.strip(),
                "start_index": exchange["start_index"],
                "end_index": exchange["end_index"],
            }
        )
    return rows


def delete_chat_exchange(messages, exchange_index):
    """Delete one exchange from a visible chat transcript."""
    normalized = list(messages or [])
    rows = build_chat_exchange_rows(normalized)
    if not isinstance(exchange_index, int):
        return normalized, False
    if exchange_index < 0 or exchange_index >= len(rows):
        return normalized, False

    row = rows[exchange_index]
    start_index = row["start_index"]
    end_index = row["end_index"]
    updated = normalized[:start_index] + normalized[end_index:]
    return updated, True


def _build_chat_exchanges(messages):
    """Return normalized exchange ranges from a visible message list."""
    exchanges = []
    normalized = [
        message for message in list(messages or [])
        if isinstance(message, dict)
        and message.get("role") in {"user", "assistant"}
    ]
    index = 0
    while index < len(normalized):
        start_index = index
        message = normalized[index]
        user_content = ""
        assistant_content = ""

        if message.get("role") == "user":
            user_content = message.get("content", "")
            index += 1
            if (
                index < len(normalized)
                and normalized[index].get("role") == "assistant"
            ):
                assistant_content = normalized[index].get("content", "")
                index += 1
        else:
            assistant_content = message.get("content", "")
            index += 1

        exchanges.append(
            {
                "start_index": start_index,
                "end_index": index,
                "user_content": _normalize_text(user_content),
                "assistant_content": _normalize_text(assistant_content),
            }
        )

    return exchanges


def _normalize_text(value):
    """Return one safe message body string."""
    if not isinstance(value, str):
        return str(value or "")
    return value


def _preview_text(value, max_length=72):
    """Return one short single-line preview for a message."""
    preview = " ".join(_normalize_text(value).split())
    if len(preview) > max_length:
        return f"{preview[:max_length - 3]}..."
    return preview
