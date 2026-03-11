"""Persistence helpers for chat session state.

Phase 6 expands the storage model from "currently open tabs only" to a split
between:

- the tabs that should be restored on startup
- the broader saved session history that powers the history browser

The saved-state helpers keep that schema backward compatible with older files
that only stored ``sessions`` and ``active_index``.
"""

from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from spyder.config.base import get_conf_path

logger = logging.getLogger(__name__)


CHAT_SESSION_STATE_VERSION = 2
GLOBAL_CHAT_STATE_FILENAME = "spyder-ai-assistant-chat-sessions.json"
PROJECT_CHAT_STATE_RELATIVE_PATH = (
    ".spyproject/ai-assistant/chat-sessions.json"
)


def get_chat_session_storage_path(project_path=None):
    """Return the storage path for chat sessions in the current scope."""
    if project_path:
        return Path(project_path) / PROJECT_CHAT_STATE_RELATIVE_PATH
    return Path(get_conf_path(GLOBAL_CHAT_STATE_FILENAME))


def make_chat_session_record(title="", messages=None, session_id=None,
                             created_at=None, updated_at=None):
    """Return one normalized persisted chat session record."""
    normalized_messages = _normalize_messages(messages or [])
    normalized_title = title if isinstance(title, str) else ""

    created = _normalize_timestamp(created_at)
    updated = _normalize_timestamp(updated_at, default=created)

    return {
        "session_id": _normalize_session_id(session_id),
        "title": normalized_title,
        "messages": normalized_messages,
        "created_at": created,
        "updated_at": updated,
    }


def merge_chat_session_history(open_sessions, history_sessions):
    """Merge the current open sessions into persisted history.

    ``open_sessions`` represent the tabs that should restore on startup.
    ``history_sessions`` represent the broader saved session archive. Closed
    sessions should remain in ``history_sessions`` unless the user deletes
    them intentionally from the history browser.
    """
    merged = {}
    order = []

    for session in _normalize_sessions(history_sessions):
        session_id = session["session_id"]
        merged[session_id] = session
        order.append(session_id)

    for session in _normalize_sessions(open_sessions):
        if not session["messages"]:
            continue
        session_id = session["session_id"]
        merged[session_id] = session
        if session_id not in order:
            order.append(session_id)

    sessions = [merged[session_id] for session_id in order if session_id in merged]
    sessions.sort(key=lambda item: item["updated_at"], reverse=True)
    return sessions


def remove_chat_session_from_history(history_sessions, session_id):
    """Remove one session from a normalized history list."""
    normalized = _normalize_sessions(history_sessions)
    remaining = [
        session for session in normalized
        if session["session_id"] != session_id
    ]
    return remaining, len(remaining) != len(normalized)


def build_chat_session_history_rows(history_sessions, open_session_ids=None):
    """Return browser-friendly session rows from persisted history."""
    open_ids = set(open_session_ids or [])
    rows = []

    for session in merge_chat_session_history([], history_sessions):
        if not session["messages"]:
            continue

        preview = _build_session_preview(session["messages"])
        rows.append({
            "session_id": session["session_id"],
            "title": session["title"] or preview or "Untitled chat",
            "preview": preview,
            "message_count": len(session["messages"]),
            "updated_at": session["updated_at"],
            "updated_label": _format_timestamp_label(session["updated_at"]),
            "is_open": session["session_id"] in open_ids,
        })

    return rows


def load_chat_session_state(storage_path):
    """Load normalized chat session state from disk."""
    path = Path(storage_path)
    if not path.is_file():
        return {}

    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        logger.warning("Failed to load chat session state from %s: %s", path, error)
        return {}

    if not isinstance(payload, dict):
        logger.warning("Ignoring malformed chat session state at %s", path)
        return {}

    sessions = _normalize_sessions(payload.get("sessions", []))
    history = merge_chat_session_history(
        sessions,
        payload.get("history", sessions),
    )
    active_index = payload.get("active_index", 0)
    if not isinstance(active_index, int):
        active_index = 0

    return {
        "version": CHAT_SESSION_STATE_VERSION,
        "active_index": max(0, active_index),
        "sessions": sessions,
        "history": history,
    }


def save_chat_session_state(storage_path, state):
    """Write chat session state atomically to disk."""
    path = Path(storage_path)
    state = state or {}
    sessions = _normalize_sessions(state.get("sessions", []))

    if "history" in state:
        history_source = state.get("history", [])
    else:
        history_source = load_chat_session_state(path).get("history", [])

    normalized_state = {
        "version": CHAT_SESSION_STATE_VERSION,
        "active_index": max(0, int(state.get("active_index", 0))),
        "sessions": sessions,
        "history": merge_chat_session_history(sessions, history_source),
    }

    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        temp_path = path.with_name(f"{path.name}.tmp")
        temp_path.write_text(
            json.dumps(normalized_state, indent=2),
            encoding="utf-8",
        )
        os.replace(temp_path, path)
    except OSError as error:
        logger.warning("Failed to save chat session state to %s: %s", path, error)
        return False

    return True


def _normalize_sessions(sessions):
    """Return a normalized list of persisted chat sessions."""
    if not isinstance(sessions, list):
        return []

    normalized = []
    for session in sessions:
        if not isinstance(session, dict):
            continue

        normalized.append(
            make_chat_session_record(
                title=session.get("title", ""),
                messages=session.get("messages", []),
                session_id=session.get("session_id"),
                created_at=session.get("created_at"),
                updated_at=session.get("updated_at"),
            )
        )

    return normalized


def _normalize_messages(messages):
    """Return a normalized list of persisted chat messages."""
    if not isinstance(messages, list):
        return []

    normalized = []
    for message in messages:
        if not isinstance(message, dict):
            continue

        role = message.get("role")
        content = message.get("content", "")
        if role not in {"user", "assistant"}:
            continue
        if not isinstance(content, str):
            content = str(content)

        normalized.append({
            "role": role,
            "content": content,
        })

    return normalized


def _normalize_session_id(value):
    """Return one stable session id string."""
    if isinstance(value, str) and value.strip():
        return value.strip()
    return uuid4().hex


def _normalize_timestamp(value, default=None):
    """Normalize timestamps to one UTC ISO-8601 format."""
    if isinstance(value, str) and value.strip():
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            parsed = None
        if parsed is not None:
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=timezone.utc)
            else:
                parsed = parsed.astimezone(timezone.utc)
            return parsed.isoformat().replace("+00:00", "Z")

    if default is not None:
        return default

    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _build_session_preview(messages):
    """Return a short human-readable preview for a session."""
    preview = ""
    for message in messages:
        if message.get("role") == "user":
            preview = message.get("content", "")
            break

    if not preview and messages:
        preview = messages[0].get("content", "")

    preview = " ".join(preview.split())
    if len(preview) > 120:
        preview = f"{preview[:117]}..."
    return preview


def _format_timestamp_label(value):
    """Return one compact label for the history browser."""
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except Exception:
        return value or ""
    return parsed.astimezone().strftime("%Y-%m-%d %H:%M")
