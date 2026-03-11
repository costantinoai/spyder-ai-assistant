"""Persistence helpers for chat session state."""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path

from spyder.config.base import get_conf_path

logger = logging.getLogger(__name__)


CHAT_SESSION_STATE_VERSION = 1
GLOBAL_CHAT_STATE_FILENAME = "spyder-ai-assistant-chat-sessions.json"
PROJECT_CHAT_STATE_RELATIVE_PATH = (
    ".spyproject/ai-assistant/chat-sessions.json"
)


def get_chat_session_storage_path(project_path=None):
    """Return the storage path for chat sessions in the current scope."""
    if project_path:
        return Path(project_path) / PROJECT_CHAT_STATE_RELATIVE_PATH
    return Path(get_conf_path(GLOBAL_CHAT_STATE_FILENAME))


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
    active_index = payload.get("active_index", 0)
    if not isinstance(active_index, int):
        active_index = 0

    return {
        "version": CHAT_SESSION_STATE_VERSION,
        "active_index": max(0, active_index),
        "sessions": sessions,
    }


def save_chat_session_state(storage_path, state):
    """Write chat session state atomically to disk."""
    path = Path(storage_path)
    normalized_state = {
        "version": CHAT_SESSION_STATE_VERSION,
        "active_index": max(0, int((state or {}).get("active_index", 0))),
        "sessions": _normalize_sessions((state or {}).get("sessions", [])),
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

        messages = _normalize_messages(session.get("messages", []))
        title = session.get("title", "")
        if not isinstance(title, str):
            title = ""

        normalized.append({
            "title": title,
            "messages": messages,
        })

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
