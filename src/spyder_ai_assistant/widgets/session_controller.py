"""Chat session/history helpers extracted from ``chat_widget.py``."""

from __future__ import annotations

import logging

from spyder_ai_assistant.utils.chat_exchanges import (
    build_chat_exchange_rows,
    delete_chat_exchange,
)
from spyder_ai_assistant.utils.chat_persistence import (
    current_timestamp,
    build_chat_session_history_rows,
    make_chat_session_record,
    merge_chat_session_history,
    remove_chat_session_from_history,
)

logger = logging.getLogger(__name__)


class ChatSessionStore:
    """Track chat sessions by their display widget instead of tab index."""

    def __init__(self):
        self._by_widget = {}

    def add(self, session):
        """Register a new session by its display widget."""
        self._by_widget[session.display] = session

    def get_for_widget(self, widget):
        """Return the session bound to a display widget, if any."""
        return self._by_widget.get(widget)

    def get_for_index(self, tab_widget, index):
        """Return the session currently shown at a tab index."""
        return self.get_for_widget(tab_widget.widget(index))

    def remove_for_widget(self, widget):
        """Forget the session associated with a display widget."""
        return self._by_widget.pop(widget, None)

    def index_of(self, tab_widget, session):
        """Return the current tab index for a session, or -1."""
        for index in range(tab_widget.count()):
            if tab_widget.widget(index) is session.display:
                return index
        return -1

    def ordered_sessions(self, tab_widget):
        """Return sessions in the current visible tab order."""
        sessions = []
        for index in range(tab_widget.count()):
            session = self.get_for_index(tab_widget, index)
            if session is not None:
                sessions.append(session)
        return sessions


class ChatSession:
    """State for one chat conversation tab."""

    _counter = 0

    def __init__(
        self,
        parent=None,
        title=None,
        messages=None,
        session_id=None,
        created_at=None,
        updated_at=None,
        prompt_preset_id=None,
        temperature_override=None,
        max_tokens_override=None,
        display_factory=None,
    ):
        ChatSession._counter += 1
        default_title = title or f"Chat {ChatSession._counter}"
        record = make_chat_session_record(
            title=default_title,
            messages=messages or [],
            session_id=session_id,
            created_at=created_at,
            updated_at=updated_at,
            prompt_preset_id=prompt_preset_id,
            temperature_override=temperature_override,
            max_tokens_override=max_tokens_override,
        )
        if display_factory is None:
            from spyder_ai_assistant.widgets.chat_display import ChatDisplay

            display_factory = ChatDisplay
        self.display = display_factory(parent)
        self.session_id = record["session_id"]
        self.title = record["title"]
        self.messages = record["messages"]
        self.created_at = record["created_at"]
        self.updated_at = record["updated_at"]
        self.prompt_preset_id = record["prompt_preset_id"]
        self.temperature_override = record["temperature_override"]
        self.max_tokens_override = record["max_tokens_override"]

    def touch(self):
        """Refresh the session updated timestamp after a state change."""
        self.updated_at = current_timestamp()

    def to_state(self):
        """Return one persisted session payload."""
        return make_chat_session_record(
            title=self.title,
            messages=self.messages,
            session_id=self.session_id,
            created_at=self.created_at,
            updated_at=self.updated_at,
            prompt_preset_id=self.prompt_preset_id,
            temperature_override=self.temperature_override,
            max_tokens_override=self.max_tokens_override,
        )


class SessionController:
    """Own chat session, history, and tab-state behavior."""

    def __init__(
        self,
        tab_widget,
        *,
        appearance_applier,
        generating_session_getter,
        session_factory=ChatSession,
        session_initializer=None,
        history_dialog_factory=None,
        exchange_delete_dialog_factory=None,
    ):
        self._tab_widget = tab_widget
        self._appearance_applier = appearance_applier
        self._generating_session_getter = generating_session_getter
        self._session_factory = session_factory
        self._session_initializer = session_initializer
        self._history_dialog_factory = history_dialog_factory
        self._exchange_delete_dialog_factory = exchange_delete_dialog_factory
        self._sessions = ChatSessionStore()
        self._history_sessions = []
        self.session_state_changed_callback = None
        self.session_scope_provider = None

    @property
    def active_session(self):
        """Return the session for the currently visible tab."""
        return self._sessions.get_for_widget(self._tab_widget.currentWidget())

    @property
    def history_sessions(self):
        """Return the cached saved-session history."""
        return list(self._history_sessions)

    @history_sessions.setter
    def history_sessions(self, sessions):
        """Replace the cached saved-session history."""
        self._history_sessions = list(sessions or [])

    def ordered_sessions(self):
        """Return the currently open sessions in tab order."""
        return self._sessions.ordered_sessions(self._tab_widget)

    def add_session(self, session, notify=True):
        """Insert one chat session into the tab widget."""
        if callable(self._session_initializer):
            self._session_initializer(session)
        self._appearance_applier(session.display)
        index = self._tab_widget.addTab(session.display, session.title)
        self._sessions.add(session)
        self._tab_widget.setCurrentIndex(index)
        logger.debug("New chat tab: %s (index %d)", session.title, index)
        if notify:
            self.notify_session_state_changed("tab-add")
        return session

    def add_new_tab(self, notify=True):
        """Create a new chat session tab and switch to it.

        The title is the lowest unused "Chat N" among the open tabs, so a
        fresh panel always starts at "Chat 1" even though sessions are
        also constructed (and the class counter bumped) during restores.
        """
        session = self._session_factory(
            parent=self._tab_widget,
            title=self._next_default_title(),
        )
        return self.add_session(session, notify=notify)

    def _next_default_title(self):
        """Return the first "Chat N" title not used by an open tab."""
        open_titles = {
            self._tab_widget.tabText(index)
            for index in range(self._tab_widget.count())
        }
        number = 1
        while f"Chat {number}" in open_titles:
            number += 1
        return f"Chat {number}"

    def close_tab(self, index):
        """Close one chat tab, keeping at least one available."""
        session = self._sessions.get_for_index(self._tab_widget, index)
        if session is None or session is self._generating_session_getter():
            return
        if self._tab_widget.count() <= 1:
            if session and session.messages:
                self._history_sessions = merge_chat_session_history(
                    [session.to_state()],
                    self._history_sessions,
                )
            self.clear_all_tabs()
            self.add_new_tab(notify=False)
            self.notify_session_state_changed("tab-clear")
            return

        if session and session.messages:
            self._history_sessions = merge_chat_session_history(
                [session.to_state()],
                self._history_sessions,
            )

        widget = self._tab_widget.widget(index)
        self._tab_widget.removeTab(index)
        self._sessions.remove_for_widget(widget)
        widget.deleteLater()
        self.notify_session_state_changed("tab-close")

    def clear_all_tabs(self):
        """Remove all tabs and forget their tracked sessions."""
        while self._tab_widget.count():
            widget = self._tab_widget.widget(0)
            self._tab_widget.removeTab(0)
            self._sessions.remove_for_widget(widget)
            widget.deleteLater()

    def refresh_session_title(self, session):
        """Keep the tab title aligned with the first visible user message."""
        title = "Chat"
        for message in session.messages:
            if message.get("role") != "user":
                continue
            short = message.get("content", "")[:30].strip()
            if len(message.get("content", "")) > 30:
                short += "..."
            title = short.replace("\n", " ") or "Chat"
            break

        if session.title == title:
            return

        session.title = title
        index = self._sessions.index_of(self._tab_widget, session)
        if index >= 0:
            self._tab_widget.setTabText(index, title)

    def serialize_open_sessions(self):
        """Return the current visible tabs as persisted session records."""
        return [session.to_state() for session in self.ordered_sessions()]

    def notify_session_state_changed(self, reason):
        """Notify the plugin layer that persisted session state changed."""
        self._history_sessions = merge_chat_session_history(
            self.serialize_open_sessions(),
            self._history_sessions,
        )
        callback = self.session_state_changed_callback
        if callback is None:
            return

        logger.debug("Chat session state changed: %s", reason)
        callback()

    def find_session_by_id(self, session_id):
        """Return the currently open session with one persisted id."""
        for session in self.ordered_sessions():
            if session.session_id == session_id:
                return session
        return None

    def session_scope_info(self):
        """Return metadata for the current chat history scope."""
        if self.session_scope_provider is None:
            return {"scope_label": "Global", "storage_path": ""}
        try:
            return dict(self.session_scope_provider() or {})
        except Exception:
            logger.exception("Failed to query chat session scope info")
            return {"scope_label": "Global", "storage_path": ""}

    def serialize_session_state(self):
        """Return the current chat sessions as a persisted payload."""
        sessions = self.serialize_open_sessions()
        history = merge_chat_session_history(sessions, self._history_sessions)
        self._history_sessions = list(history)
        return {
            "active_index": max(0, self._tab_widget.currentIndex()),
            "sessions": sessions,
            "history": history,
        }

    def restore_session_state(self, state):
        """Restore tabs and messages from persisted state."""
        if self._generating_session_getter() is not None:
            logger.warning(
                "Skipping chat session restore while a response is generating"
            )
            return False

        sessions = []
        history = []
        if isinstance(state, dict):
            sessions = state.get("sessions", [])
            history = state.get("history", [])

        self.clear_all_tabs()
        self._history_sessions = merge_chat_session_history(sessions, history)
        if not sessions:
            self.add_new_tab(notify=False)
            return True

        for session_state in sessions:
            if not isinstance(session_state, dict):
                continue
            session = self._session_factory(
                parent=self._tab_widget,
                title=session_state.get("title", ""),
                messages=session_state.get("messages", []),
                session_id=session_state.get("session_id"),
                created_at=session_state.get("created_at"),
                updated_at=session_state.get("updated_at"),
                prompt_preset_id=session_state.get("prompt_preset_id"),
                temperature_override=session_state.get("temperature_override"),
                max_tokens_override=session_state.get("max_tokens_override"),
            )
            self.add_session(session, notify=False)
            session.display.rebuild_from_messages(session.messages)

        if self._tab_widget.count() == 0:
            self.add_new_tab(notify=False)
            return True

        active_index = 0
        if isinstance(state, dict):
            active_index = state.get("active_index", 0)
        if not isinstance(active_index, int):
            active_index = 0
        active_index = max(0, min(active_index, self._tab_widget.count() - 1))
        self._tab_widget.setCurrentIndex(active_index)
        return True

    def create_history_browser_dialog(self):
        """Build the modal history browser for the current persistence scope."""
        open_session_ids = {
            session.session_id
            for session in self.ordered_sessions()
        }
        rows = build_chat_session_history_rows(
            self._history_sessions,
            open_session_ids=open_session_ids,
        )
        logger.info(
            "Built chat history browser with %d saved session(s)",
            len(rows),
        )
        dialog_factory = self._history_dialog_factory
        if dialog_factory is None:
            from spyder_ai_assistant.widgets.session_history_dialog import (
                SessionHistoryDialog,
            )

            dialog_factory = SessionHistoryDialog
        return dialog_factory(
            rows=rows,
            scope_info=self.session_scope_info(),
            parent=self._tab_widget.parent(),
        )

    def open_history_browser(self):
        """Open the saved-session history browser and apply one action."""
        dialog = self.create_history_browser_dialog()
        logger.info(
            "Opened chat history browser for %s scope",
            self.session_scope_info().get("scope_label", "unknown"),
        )
        if dialog.exec_() != dialog.Accepted:
            return

        session_id = dialog.selected_session_id()
        action = dialog.selected_action()
        if not session_id or not action:
            return

        logger.info(
            "History browser selected action '%s' for session %s",
            action,
            session_id,
        )

        if action == "open":
            self.open_session_from_history(session_id, duplicate=False)
        elif action == "duplicate":
            self.open_session_from_history(session_id, duplicate=True)
        elif action == "delete":
            self.delete_session_from_history(session_id)

    def history_session_by_id(self, session_id):
        """Return one saved history record by id."""
        for session_state in self._history_sessions:
            if session_state.get("session_id") == session_id:
                return session_state
        return None

    def open_session_from_history(self, session_id, duplicate=False):
        """Reopen or duplicate one saved history session into the tab widget."""
        session_state = self.history_session_by_id(session_id)
        if session_state is None:
            session = self.active_session
            if session:
                session.display.append_error("Saved chat session no longer exists.")
            return False

        if not duplicate:
            existing = self.find_session_by_id(session_id)
            if existing is not None:
                index = self._sessions.index_of(self._tab_widget, existing)
                if index >= 0:
                    self._tab_widget.setCurrentIndex(index)
                logger.info(
                    "Focused already-open chat session from history: %s",
                    session_id,
                )
                return True

        title = session_state.get("title", "")
        if duplicate and title:
            title = f"{title} (copy)"

        session = self._session_factory(
            parent=self._tab_widget,
            title=title,
            messages=session_state.get("messages", []),
            session_id=None if duplicate else session_state.get("session_id"),
            created_at=None if duplicate else session_state.get("created_at"),
            updated_at=None if duplicate else session_state.get("updated_at"),
            prompt_preset_id=session_state.get("prompt_preset_id"),
            temperature_override=session_state.get("temperature_override"),
            max_tokens_override=session_state.get("max_tokens_override"),
        )
        self.add_session(session, notify=True)
        session.display.rebuild_from_messages(session.messages)
        if duplicate:
            logger.info(
                "Duplicated chat session from history: %s -> %s",
                session_id,
                session.session_id,
            )
        else:
            logger.info("Reopened chat session from history: %s", session_id)
        return True

    def delete_session_from_history(self, session_id):
        """Delete one saved history session and close any matching open tab."""
        open_session = self.find_session_by_id(session_id)
        if open_session is self._generating_session_getter():
            session = self.active_session
            if session:
                session.display.append_error(
                    "Stop the active response before deleting this session."
                )
            return False

        updated_history, removed = remove_chat_session_from_history(
            self._history_sessions,
            session_id,
        )
        if not removed:
            session = self.active_session
            if session:
                session.display.append_error("Saved chat session no longer exists.")
            return False

        self._history_sessions = updated_history

        if open_session is not None:
            index = self._sessions.index_of(self._tab_widget, open_session)
            if index >= 0:
                widget = self._tab_widget.widget(index)
                self._tab_widget.removeTab(index)
                self._sessions.remove_for_widget(widget)
                widget.deleteLater()
            if self._tab_widget.count() == 0:
                self.add_new_tab(notify=False)

        logger.info("Deleted chat session from history: %s", session_id)
        self.notify_session_state_changed("history-delete")
        return True

    def create_exchange_delete_dialog(self, session=None):
        """Build the delete-exchange browser for the active session."""
        session = session or self.active_session
        rows = build_chat_exchange_rows(getattr(session, "messages", []))
        logger.info(
            "Built exchange delete browser with %d exchange(s) for session %s",
            len(rows),
            getattr(session, "session_id", "<unknown>"),
        )
        dialog_factory = self._exchange_delete_dialog_factory
        if dialog_factory is None:
            from spyder_ai_assistant.widgets.exchange_delete_dialog import (
                ExchangeDeleteDialog,
            )

            dialog_factory = ExchangeDeleteDialog
        return dialog_factory(
            rows=rows,
            session_title=getattr(session, "title", ""),
            parent=self._tab_widget.parent(),
        )

    def open_exchange_delete_dialog(self):
        """Open the delete-exchange browser for the active chat tab."""
        session = self.active_session
        if session is None or not session.messages:
            if session:
                session.display.append_error("No exchanges are available to delete.")
            return False
        if session is self._generating_session_getter():
            session.display.append_error(
                "Stop the active response before deleting an exchange."
            )
            return False

        dialog = self.create_exchange_delete_dialog(session)
        logger.info(
            "Opened exchange delete browser for session %s",
            session.session_id,
        )
        if dialog.exec_() != dialog.Accepted:
            return False

        exchange_index = dialog.selected_exchange_index()
        if exchange_index is None:
            return False
        return self.delete_exchange_from_session(session, exchange_index)

    def delete_exchange_from_session(self, session, exchange_index):
        """Delete one selected exchange from a chat session."""
        updated_messages, removed = delete_chat_exchange(
            session.messages,
            exchange_index,
        )
        if not removed:
            session.display.append_error("The selected exchange no longer exists.")
            return False

        session.messages = updated_messages
        session.touch()
        session.display.rebuild_from_messages(session.messages)
        self.refresh_session_title(session)
        logger.info(
            "Deleted exchange %d from session %s",
            exchange_index + 1,
            session.session_id,
        )
        self.notify_session_state_changed("exchange-delete")
        return True
