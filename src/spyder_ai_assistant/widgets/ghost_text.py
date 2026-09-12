"""Ghost text (inline completion) for Spyder code editors.

Renders AI-generated code suggestions as grayed-out text directly in
the editor document, VS Code Copilot / Cursor style. The user presses
Tab to accept the suggestion or keeps typing to dismiss it.

Architecture:
  Instead of painting an overlay on top of the editor (which can't push
  existing content down for multi-line completions), ghost text is
  inserted as actual document content with a gray QTextCharFormat. This
  means multi-line suggestions naturally push existing lines down, just
  like in VS Code and Cursor.

  Insertion and removal use editor.blockSignals() to prevent:
  - textChanged from re-triggering completions or marking the file dirty
  - cursorPositionChanged from auto-clearing the ghost text prematurely

  Removal uses document.undo() which cleanly reverses the insertion in
  one step. Acceptance undoes the gray text, then re-inserts as normal
  text (so it becomes a real edit in the undo stack).

Components:
- GhostTextManager: Manages the full lifecycle — show, clear, accept.
- _GhostEventFilter: Intercepts Tab/Escape/other keys on the editor.
- _CompletionPopupWatcher: Tracks Spyder's native completion popup so
  ghost text stays out of the way when the LSP menu is visible.

Usage (from plugin.py):
    manager = GhostTextManager(codeeditor)
    manager.show_suggestion("return a + b\\n\\ndef bar():\\n    pass")
    # User presses Tab → text is inserted as real code
    # User types anything else → ghost text disappears
"""

from spyder_ai_assistant.utils.assistant_settings import (
    ASSISTANT_CONF_DEFAULTS,
    ASSISTANT_OPTION_RANGES,
    DEFAULT_NATIVE_POPUP_POLICY,
    NATIVE_POPUP_POLICIES,
    NATIVE_POPUP_POLICY_AI_FIRST,
    NATIVE_POPUP_POLICY_NATIVE_FIRST,
)
from spyder_ai_assistant.utils.coerce import bounded_int
from spyder_ai_assistant.utils.text_positions import python_index, utf16_length

import logging
import time

from qtpy.QtCore import Qt, QEvent, QObject, QTimer
from qtpy.QtGui import QColor, QPalette, QTextCharFormat, QTextCursor
from qtpy.QtWidgets import QTextEdit

logger = logging.getLogger(__name__)
IDLE_COMPLETION_DELAY_MS = 1000
MANUAL_REQUEST_DEDUP_WINDOW_S = 0.5
POST_ACCEPT_COMPLETION_DELAY_MS = 75


class _GhostEventFilter(QObject):
    """Event filter for intercepting key events on the editor.

    Separated from GhostTextManager to properly subclass QObject
    (required for installEventFilter). Routes events to the manager.
    """

    def __init__(self, manager, editor):
        super().__init__(editor)
        self._manager = manager
        self._editor = editor
        self._manual_shortcut_override_active = False

    @staticmethod
    def _is_manual_completion_shortcut(event):
        """Return True when the event matches Ctrl+Shift+Space."""
        modifiers = event.modifiers()
        required = Qt.ControlModifier | Qt.ShiftModifier
        disallowed = Qt.AltModifier | Qt.MetaModifier
        return (
            event.key() == Qt.Key_Space
            and (modifiers & required) == required
            and not (modifiers & disallowed)
        )

    def eventFilter(self, obj, event):
        """Intercept key events for ghost text acceptance/dismissal."""
        if event.type() == QEvent.Wheel:
            self._manager.pause_for_scroll()
            return False
        if event.type() == QEvent.FocusOut:
            self._manager.on_editor_focus_out(event)
            return False
        if event.type() == QEvent.FocusIn:
            self._manager.resume_suggestions("focus_regained", only_if="focus_loss")
            return False
        if event.type() == QEvent.MouseButtonPress:
            self._manager.clear(reason="mouse")
            return False
        if event.type() not in (QEvent.ShortcutOverride, QEvent.KeyPress):
            return False
        if event.type() == QEvent.KeyPress:
            # Any key at the cursor means the user is back to editing here,
            # so a scroll/focus pause no longer applies (this also covers
            # Spyder's own Ctrl+Space, which never reaches request_completion).
            self._manager.resume_suggestions("keypress")

        if self._is_manual_completion_shortcut(event):
            if event.type() == QEvent.ShortcutOverride:
                self._manual_shortcut_override_active = True
                logger.info(
                    "Editor-level manual AI completion shortcut override intercepted on %s",
                    obj.__class__.__name__,
                )
                self._manager.request_completion()
                return True

            if self._manual_shortcut_override_active:
                self._manual_shortcut_override_active = False
                return True

            logger.info(
                "Editor-level manual AI completion keypress intercepted on %s",
                obj.__class__.__name__,
            )
            self._manager.request_completion()
            return True

        key = event.key()
        modifiers = event.modifiers()

        if not self._manager.has_suggestion():
            return False

        if event.type() == QEvent.ShortcutOverride:
            # Reserve only ghost acceptance/dismissal keys here. Mutating the
            # document during ShortcutOverride would handle a printable key
            # twice when Qt subsequently delivers KeyPress.
            if key in (Qt.Key_Tab, Qt.Key_Escape) or (
                key == Qt.Key_Right and modifiers & Qt.AltModifier
            ):
                event.accept()
                return True
            if modifiers & (Qt.ControlModifier | Qt.MetaModifier):
                # Native completion, Save and other Spyder shortcuts must see
                # the real document, with temporary ghost text removed.
                self._manager.clear(reason="native_shortcut")
            return False

        logger.debug(
            "Ghost keypress intercepted: key=%r text=%r target=%s",
            key,
            event.text(),
            obj.__class__.__name__,
        )

        if (
            key == Qt.Key_Right
            and modifiers == Qt.AltModifier
            and self._manager.accept_next_word()
        ):
            return True

        if (
            key == Qt.Key_Right
            and modifiers == (Qt.AltModifier | Qt.ShiftModifier)
            and self._manager.accept_next_line()
        ):
            return True

        if key == Qt.Key_Tab and not (modifiers & Qt.ControlModifier):
            # Tab pressed with ghost text visible → accept the suggestion.
            # The ghost text becomes real code in the document.
            self._manager.accept()
            return True  # Consume the event (don't let editor indent)

        elif key == Qt.Key_Escape:
            # Escape pressed → dismiss the ghost text
            self._manager.clear(reason="escape")
            return True  # Consume the event

        elif key == Qt.Key_Backspace:
            # Backspace explicitly dismisses the current suggestion, but the
            # editor should still handle the key normally.
            self._manager.clear(reason="backspace")
            return False

        else:
            if self._manager.try_accept_typed_text(event):
                return True

            # Any other key → clear ghost text first, then let the editor
            # process the key normally. The user is typing new code, so
            # the suggestion is stale.
            self._manager.clear(reason="typing")
            return False  # Let the event propagate to the editor


class _CompletionPopupWatcher(QObject):
    """Event filter installed on the editor's completion popup widget.

    Decides, at the moment Spyder tries to show its completion popup,
    whether the popup or the inline ghost text owns the editor (see
    ``GhostTextManager.blocks_automatic_popup``), and keeps the manager
    informed about popup visibility so ghost suggestions are cleared or
    suppressed when the popup wins.
    """

    def __init__(self, manager, completion_widget):
        super().__init__(completion_widget)
        self._manager = manager

    def eventFilter(self, obj, event):
        """Track completion popup visibility changes."""
        if event.type() == QEvent.Show:
            automatic = bool(getattr(obj, "automatic", False))
            if automatic and self._manager.blocks_automatic_popup():
                # Swallow the popup before it takes focus; the ghost text (or
                # the pending AI request) keeps the editor.
                self._manager._emit_lifecycle_event(
                    "suppressed",
                    reason="native_popup",
                )
                obj.hide()
                return True
            self._manager.on_completion_popup_visibility_changed(True)
        elif event.type() in (QEvent.Hide, QEvent.Close):
            self._manager.on_completion_popup_visibility_changed(False)
        return False


class GhostTextManager:
    """Manages ghost text lifecycle for a single code editor.

    Ghost text is implemented by temporarily inserting gray-formatted text
    into the editor's QTextDocument. This approach (vs. a QPainter overlay)
    ensures that:
    - Multi-line suggestions push existing content down (like VS Code)
    - Text alignment is pixel-perfect (uses the editor's own layout engine)
    - Syntax highlighting, scrolling, and line numbers all work naturally

    The insertion is done with editor signals blocked so it doesn't trigger
    file-modified indicators, completion re-requests, or cursor-move handlers.
    Removal uses document.undo() for a clean one-step reversal.

    Handles:
    - Showing/clearing ghost text suggestions
    - Tab key acceptance (ghost text becomes real code)
    - Escape key dismissal
    - Auto-clearing when the user types or moves the cursor
    - Hiding ghost text while Spyder's native completion popup is visible

    Args:
        editor: The Spyder CodeEditor widget to manage.
    """

    def __init__(
        self,
        editor,
        lifecycle_callback=None,
        manual_completion_requester=None,
        idle_completion_delay_ms=IDLE_COMPLETION_DELAY_MS,
        post_accept_completion_delay_ms=POST_ACCEPT_COMPLETION_DELAY_MS,
        native_popup_policy=DEFAULT_NATIVE_POPUP_POLICY,
        ai_available=None,
        manual_only=False,
    ):
        self._editor = editor
        self._lifecycle_callback = lifecycle_callback
        self._manual_completion_requester = manual_completion_requester
        # Suggestions only when the user asks for them: both automatic
        # paths (idle and post-accept) stop scheduling, while
        # request_completion keeps working for the shortcut.
        self._manual_only = bool(manual_only)
        # Ownership rule between ghost text and Spyder's automatic popup
        # (``NATIVE_POPUP_POLICIES``); ``ai_available`` tells whether the AI
        # side can actually deliver (completions on, model loaded), so the
        # "AI first" policy never hides native popups while the AI is down.
        self._native_popup_policy = DEFAULT_NATIVE_POPUP_POLICY
        self._ai_available = ai_available
        self.set_native_popup_policy(native_popup_policy)
        self._ghost_active = False
        self._ghost_text = ""
        self._target = None
        self._pause_reason = ""
        self._insert_offset = -1
        self._display_cursor_offset = -1
        # Document positions of the inserted ghost text, used for
        # applying extra selections (gray overlay) on the range.
        self._ghost_start = -1
        self._ghost_end = -1
        self._idle_completion_delay_ms = bounded_int(
            idle_completion_delay_ms,
            ASSISTANT_CONF_DEFAULTS["idle_completion_delay_ms"],
            *ASSISTANT_OPTION_RANGES["idle_completion_delay_ms"],
        )
        self._last_manual_request_at = 0.0
        self._last_manual_request_state = None
        self._idle_completion_timer = QTimer(editor)
        self._idle_completion_timer.setSingleShot(True)
        self._idle_completion_timer.setInterval(self._idle_completion_delay_ms)
        self._idle_completion_timer.timeout.connect(
            self._request_idle_completion
        )
        self._post_accept_completion_timer = QTimer(editor)
        self._post_accept_completion_timer.setSingleShot(True)
        self._post_accept_completion_delay_ms = bounded_int(
            post_accept_completion_delay_ms,
            ASSISTANT_CONF_DEFAULTS["post_accept_completion_delay_ms"],
            *ASSISTANT_OPTION_RANGES["post_accept_completion_delay_ms"],
        )
        self._post_accept_completion_timer.setInterval(
            self._post_accept_completion_delay_ms
        )
        self._post_accept_reason = "accepted"
        self._post_accept_pending = False
        self._post_accept_completion_timer.timeout.connect(
            self._request_post_accept_completion
        )

        # Event filter for key interception (Tab/Escape/any key).
        # Must be a proper QObject subclass for installEventFilter.
        self._event_filter = _GhostEventFilter(self, editor)
        editor.installEventFilter(self._event_filter)
        self._event_targets = [editor]
        viewport = getattr(editor, "viewport", None)
        if callable(viewport):
            viewport = viewport()
        if viewport is not None and viewport is not editor:
            viewport.installEventFilter(self._event_filter)
            self._event_targets.append(viewport)

        # Event filter on the completion popup so the native LSP menu
        # can suppress inline ghost text while it is visible.
        self._popup_watcher = None
        if hasattr(editor, "completion_widget"):
            self._popup_watcher = _CompletionPopupWatcher(
                self, editor.completion_widget
            )
            editor.completion_widget.installEventFilter(self._popup_watcher)

        # Auto-clear ghost text when the user clicks elsewhere or uses
        # arrow keys. This handler doesn't fire during our own insertions
        # because we block editor signals during those operations.
        editor.cursorPositionChanged.connect(self._on_cursor_moved)
        editor.textChanged.connect(self._schedule_idle_completion)
        editor.verticalScrollBar().sliderPressed.connect(self.pause_for_scroll)

    def set_native_popup_policy(self, policy):
        """Choose how ghost text shares the editor with Spyder's popup.

        Unknown values fall back to the default policy so a stale config
        value can never disable both completion sources.
        """
        policy = str(policy or "").strip().lower()
        if policy not in NATIVE_POPUP_POLICIES:
            policy = DEFAULT_NATIVE_POPUP_POLICY
        self._native_popup_policy = policy

    @property
    def native_popup_policy(self):
        """The active popup ownership policy (see ``NATIVE_POPUP_POLICIES``)."""
        return self._native_popup_policy

    def _ai_can_deliver(self):
        """True when the AI side is expected to produce suggestions."""
        if self._ai_available is None:
            return True
        try:
            return bool(self._ai_available())
        except Exception:
            return False

    def blocks_automatic_popup(self):
        """True when an *automatic* native popup must stay hidden right now.

        A visible ghost always keeps its place against an automatic popup.
        Under the "AI first" policy the automatic popup also stays hidden
        while a suggestion may still arrive, as long as the AI can deliver;
        an explicit Ctrl+Space popup is never blocked (the caller checks
        ``automatic``).
        """
        if self._ghost_active:
            return True
        return (
            self._native_popup_policy == NATIVE_POPUP_POLICY_AI_FIRST
            and self._ai_can_deliver()
        )

    def _ghost_replaces_automatic_popup(self):
        """True when an arriving ghost may close an automatic native popup."""
        return self._native_popup_policy != NATIVE_POPUP_POLICY_NATIVE_FIRST

    def update_timing(self, idle_ms=None, post_accept_ms=None):
        """Update completion delay timers from config.

        Called when the user changes ghost text timing settings.
        Only updates values that are explicitly provided.
        """
        if idle_ms is not None:
            self._idle_completion_delay_ms = bounded_int(
                idle_ms, ASSISTANT_CONF_DEFAULTS["idle_completion_delay_ms"],
                *ASSISTANT_OPTION_RANGES["idle_completion_delay_ms"],
            )
            self._idle_completion_timer.setInterval(
                self._idle_completion_delay_ms
            )
        if post_accept_ms is not None:
            self._post_accept_completion_delay_ms = bounded_int(
                post_accept_ms, ASSISTANT_CONF_DEFAULTS["post_accept_completion_delay_ms"],
                *ASSISTANT_OPTION_RANGES["post_accept_completion_delay_ms"],
            )
            self._post_accept_completion_timer.setInterval(
                self._post_accept_completion_delay_ms
            )

    def show_suggestion(self, text, target=None):
        """Display a ghost text suggestion at the current cursor position.

        Inserts the text into the document, then applies a gray extra
        selection over it. The extra selection renders ON TOP of syntax
        highlighting, ensuring the ghost text always appears gray
        regardless of what the highlighter does.

        The cursor is restored to its original position so the user sees
        the suggestion appearing after their cursor.

        Args:
            text: The completion text to show. Can be multi-line.
            target: Optional target metadata dict containing the cursor
                position the suggestion was generated for.
        """
        # Responses already in flight must not pull the viewport back to the
        # cursor after the user starts reading elsewhere in the file.
        if self._pause_reason:
            self._log_suppressed(f"suggestions paused ({self._pause_reason})", target)
            return False

        # Clear any previous ghost text first
        if self._ghost_active:
            self.clear(reason="replaced", record_event=False)

        if not text:
            self._log_suppressed("empty suggestion", target)
            return False

        self._idle_completion_timer.stop()

        # An explicit (Ctrl+Space) popup always owns the completion UI. An
        # automatic one only does under the "native first" policy; otherwise
        # the arriving ghost closes it and takes over.
        replaces_automatic = self._ghost_replaces_automatic_popup()
        if self._completion_popup_visible(manual_only=replaces_automatic):
            self._log_suppressed("native completion popup is visible", target)
            return False
        if replaces_automatic and self._completion_popup_visible():
            logger.info("Ghost text replaces the automatic native completion popup")
            self._hide_completion_popup()

        if not self._matches_target(target):
            self._log_suppressed("editor cursor no longer matches the target", target)
            return False

        self._ghost_text = text
        self._target = target or {}
        cursor = self._editor.textCursor()
        original_pos = cursor.position()
        insert_offset = int(self._target.get("insert_offset", original_pos))
        insert_offset = max(0, min(insert_offset, self._editor.document().characterCount() - 1))
        restore_pos = original_pos + utf16_length(text) if insert_offset <= original_pos else original_pos
        insert_cursor = QTextCursor(self._editor.document())
        insert_cursor.setPosition(insert_offset)
        ghost_format = self._build_ghost_text_format()

        # Block EDITOR signals (not document signals) during insertion:
        # - textChanged won't fire (no completion re-trigger, no dirty flag)
        # - cursorPositionChanged won't fire (no premature auto-clear)
        # The document's internal layout signals still flow to the editor's
        # viewport, so the display updates correctly.
        doc = self._editor.document()
        was_modified = doc.isModified()
        self._editor.blockSignals(True)
        try:
            # Insert as one atomic undo step so document.undo() removes
            # the entire ghost text in one call.
            insert_cursor.beginEditBlock()
            insert_cursor.insertText(text, ghost_format)
            insert_cursor.endEditBlock()

            self._ghost_start = insert_offset
            self._ghost_end = insert_offset + utf16_length(text)
            self._insert_offset = insert_offset
            self._display_cursor_offset = original_pos

            # Restore cursor to original position — the user should see
            # their cursor at the same logical spot, even when the ghost
            # text is inserted earlier on a continuation line.
            cursor.setPosition(restore_pos)
            self._editor.setTextCursor(cursor)
        finally:
            self._editor.blockSignals(False)
            # Restore the modified flag so ghost text doesn't make the
            # file tab show a "dirty" indicator.
            doc.setModified(was_modified)

        self._ghost_active = True

        # Apply one overlay selection on top of the inserted formatting so
        # the ghost style survives highlighter refreshes and stays visible.
        self._apply_ghost_extra_selection()

        logger.debug("Ghost text shown: %d chars", len(text))
        logger.info(
            "Ghost text shown: chars=%d target=%s insert_offset=%s display_offset=%s preview=%r",
            len(text),
            self._target,
            self._insert_offset,
            self._display_cursor_offset,
            text[:100],
        )
        self._emit_lifecycle_event(
            "shown",
            chars=len(text),
            target=dict(self._target or {}),
        )
        return True

    def pause_for_scroll(self):
        """Dismiss ghost text and pause automatic suggestions while reading."""
        self.pause_suggestions("scroll")

    def on_editor_focus_out(self, event):
        """Pause suggestions when focus really leaves the editor.

        Spyder's native completion popup takes keyboard focus while it is
        shown, which also fires FocusOut on the editor. That hand-off is
        managed by the popup watcher, so it must not pause ghost text:
        otherwise every native popup silently disabled AI suggestions until
        the next edit. Only pause for genuine focus loss (another widget,
        another window).
        """
        try:
            popup_focus = event.reason() == Qt.PopupFocusReason
        except Exception:
            popup_focus = False
        if popup_focus or self._completion_popup_visible():
            logger.info("Editor focus moved to the native completion popup; ghost text not paused")
            return
        self.pause_suggestions("focus_loss")

    def resume_suggestions(self, trigger, only_if=""):
        """Lift a scroll/focus pause because the user is active here again.

        Args:
            trigger: What resumed suggestions (logging only).
            only_if: When given, resume only if the pause had this reason
                (focus regained must not cancel a deliberate scroll pause).
        """
        if not self._pause_reason:
            return
        if only_if and self._pause_reason != only_if:
            return
        logger.info(
            "AI suggestions resumed after %s (was paused: %s)",
            trigger,
            self._pause_reason,
        )
        self._pause_reason = ""

    def pause_suggestions(self, reason):
        """Suspend pending suggestions while the user navigates elsewhere."""
        self._pause_reason = reason
        self._idle_completion_timer.stop()
        self._post_accept_completion_timer.stop()
        self._post_accept_pending = False
        scrollbar = self._editor.verticalScrollBar()
        position = scrollbar.value()
        self.clear(reason=reason)
        scrollbar.setValue(position)

    def clear(self, reason="unknown", record_event=True):
        """Remove the ghost text from the document.

        Uses document.undo() to cleanly reverse the insertion in one step.
        Editor signals are blocked during removal to prevent side effects.
        Also removes the gray extra selection.
        """
        if not self._ghost_active:
            return

        self._idle_completion_timer.stop()
        doc = self._editor.document()
        was_modified = doc.isModified()
        self._editor.blockSignals(True)
        try:
            # Undo reverses our beginEditBlock/endEditBlock insertion
            # in one step, restoring the document to its pre-ghost state.
            doc.undo()
        finally:
            self._editor.blockSignals(False)
            doc.setModified(was_modified)

        self._ghost_active = False
        self._ghost_text = ""
        # Keep the target for the lifecycle event below: the provider uses
        # it to forget shown candidates / remember an explicit dismissal at
        # this spot, which needs the spot, not just the reason.
        dismissed_target = dict(self._target) if self._target else None
        self._target = None
        display_cursor_offset = self._display_cursor_offset
        self._ghost_start = -1
        self._ghost_end = -1
        self._insert_offset = -1
        self._display_cursor_offset = -1
        if reason in {"escape", "backspace"}:
            self._last_manual_request_at = 0.0
            self._last_manual_request_state = None
            logger.info(
                "Reset manual AI completion dedup state after explicit %s dismissal",
                reason,
            )
        logger.info(
            "Ghost text cleared: reason=%s insert_offset=%s display_offset=%s",
            reason,
            self._insert_offset,
            display_cursor_offset,
        )

        if display_cursor_offset >= 0:
            try:
                cursor = self._editor.textCursor()
                cursor.setPosition(
                    max(0, min(display_cursor_offset, self._editor.document().characterCount() - 1))
                )
                self._editor.setTextCursor(cursor)
            except Exception:
                pass

        # Remove our ghost extra selection. After undo the text is gone,
        # so the selection is invalid. Rebuild without it.
        self._remove_ghost_extra_selection()

        if record_event:
            self._emit_lifecycle_event(
                "dismissed", reason=reason, target=dismissed_target
            )

    def document_state_without_ghost(self):
        """Return ``(text, cursor_position)`` as the user sees the document.

        Ghost text is temporary document content; anything that syncs the
        editor to a provider or reads the "real" cursor must exclude it.
        Positions are Qt (UTF-16) offsets like the editor's own.
        """
        text = self._editor.toPlainText()
        position = self._editor.textCursor().position()
        if not self._ghost_active or self._ghost_start < 0:
            return text, position
        start, end = self._ghost_start, self._ghost_end
        py_start = python_index(text, start)
        py_end = python_index(text, end)
        text = text[:py_start] + text[py_end:]
        if position >= end:
            position -= end - start
        elif position > start:
            position = start
        return text, position

    def has_suggestion(self):
        """Return True if ghost text is currently visible."""
        return self._ghost_active

    def accept(self):
        """Accept the ghost text — make it permanent real code.

        Removes the gray ghost text via undo, then re-inserts the same
        text as normal (un-formatted) text. This way the accepted text
        appears as a real edit in the undo stack.
        """
        if not self._ghost_active:
            return

        text = self._ghost_text
        insert_offset = self._insert_offset
        display_offset = self._display_cursor_offset

        # Remove the gray ghost text (silently via undo)
        self.clear(reason="accepted", record_event=False)

        # Re-insert as normal text — this is a real edit that enters
        # the undo stack and triggers textChanged/dirty indicators.
        self._insert_real_text(text, insert_offset, display_offset)
        logger.info(
            "Ghost text accepted fully: chars=%d insert_offset=%s display_offset=%s",
            len(text),
            insert_offset,
            display_offset,
        )
        self._schedule_post_accept_completion("accepted")
        self._emit_lifecycle_event(
            "accepted",
            method="full",
            chars=len(text),
            remaining_chars=0,
        )

    def accept_next_word(self):
        """Accept one leading word-like segment from the ghost text."""
        if not self._ghost_active:
            return False

        return self._accept_prefix(
            self._next_word_segment(self._ghost_text),
            method="word",
        )

    def accept_next_line(self):
        """Accept one leading line from the ghost text."""
        if not self._ghost_active:
            return False

        return self._accept_prefix(
            self._next_line_segment(self._ghost_text),
            method="line",
        )

    def try_accept_typed_text(self, event):
        """Consume a keypress that matches the next ghost-text prefix.

        This lets the user keep typing through a suggestion without making
        the rest of the ghost text disappear immediately.
        """
        if not self._ghost_active:
            return False

        text = event.text() or ""
        modifiers = event.modifiers()
        if (
            not text
            or not text.isprintable()
            or modifiers & (Qt.ControlModifier | Qt.AltModifier | Qt.MetaModifier)
        ):
            return False

        if not self._ghost_text.startswith(text):
            return False

        full_text = self._ghost_text
        remainder = full_text[len(text):]
        insert_offset = self._insert_offset
        display_offset = self._display_cursor_offset

        self.clear(reason="accepted", record_event=False)
        self._insert_real_text(text, insert_offset, display_offset)

        if remainder:
            cursor = self._editor.textCursor()
            self.show_suggestion(
                remainder,
                target={
                    "offset": cursor.position(),
                    "line": cursor.blockNumber(),
                    "column": cursor.columnNumber(),
                    "insert_offset": (
                        insert_offset + utf16_length(text)
                        if isinstance(insert_offset, int) and insert_offset >= 0
                        else cursor.position()
                    ),
                },
            )
            logger.debug(
                "Ghost text advanced by typed prefix: %r (%d chars remain)",
                text,
                len(remainder),
            )
            logger.info(
                "Ghost text advanced by typed prefix %r (%d chars remain)",
                text,
                len(remainder),
            )
            self._emit_lifecycle_event(
                "advanced",
                method="typed",
                chars=len(text),
                remaining_chars=len(remainder),
            )
        else:
            logger.debug("Ghost text fully accepted by typing: %r", text)
            logger.info("Ghost text fully accepted by typing: %r", text)
            self._schedule_post_accept_completion("typed_accept")
            self._emit_lifecycle_event(
                "accepted",
                method="full",
                chars=len(text),
                remaining_chars=0,
            )

        return True

    def request_completion(self, source="manual"):
        """Manually trigger a completion request.

        Called when the user presses the completion shortcut (e.g.,
        Ctrl+Shift+Space). Prefer the plugin-provided AI-only request
        path so the native Spyder popup does not steal focus from ghost
        text. Fall back to the editor's built-in completion hook if the
        plugin did not provide an explicit requester.
        """
        self._idle_completion_timer.stop()
        self._post_accept_completion_timer.stop()
        self._post_accept_pending = False
        self._request_is_manual = source not in {"idle", "post_accept"}
        if self._manual_only and not self._request_is_manual:
            return
        if source not in {"idle", "post_accept"}:
            self.resume_suggestions(f"{source} request")
            self._hide_completion_popup()
        elif self._pause_reason or self._completion_popup_visible():
            return
        if source not in {"idle", "post_accept"}:
            now = time.monotonic()
            cursor = self._editor.textCursor()
            request_state = (
                int(cursor.position()),
                int(cursor.selectionStart()),
                int(cursor.selectionEnd()),
                len(self._editor.toPlainText() or ""),
            )
            if (
                self._last_manual_request_state == request_state
                and (now - self._last_manual_request_at) < MANUAL_REQUEST_DEDUP_WINDOW_S
            ):
                logger.info(
                    "Ignoring duplicate %s AI completion request within %.2fs window",
                    source,
                    MANUAL_REQUEST_DEDUP_WINDOW_S,
                )
                return
            self._last_manual_request_at = now
            self._last_manual_request_state = request_state
        try:
            if self._manual_completion_requester is not None:
                logger.info(
                    "Requesting %s AI completion through plugin path",
                    source,
                )
                handled = self._manual_completion_requester()
                if handled is not False:
                    logger.info(
                        "%s AI completion request was handled by plugin path",
                        source.capitalize(),
                    )
                    return
                logger.info(
                    "%s AI completion requester returned False; falling back to editor completion",
                    source.capitalize(),
                )
            else:
                logger.info(
                    "No %s AI completion requester is installed; falling back to editor completion",
                    source,
                )
            self._editor.do_completion()
        except Exception as e:
            logger.debug("%s completion trigger failed: %s", source, e)

    def _apply_ghost_extra_selection(self):
        """Apply gray foreground to ghost text via extra selections.

        Extra selections render ON TOP of syntax highlighting, so the
        gray color overrides whatever the highlighter sets. This is the
        standard Qt mechanism for decorations that must survive re-highlighting.
        """
        if self._ghost_start < 0 or self._ghost_end < 0:
            return

        sel = QTextEdit.ExtraSelection()

        # Create a cursor spanning the ghost text range
        ghost_cursor = QTextCursor(self._editor.document())
        ghost_cursor.setPosition(self._ghost_start)
        ghost_cursor.setPosition(self._ghost_end, QTextCursor.KeepAnchor)
        sel.cursor = ghost_cursor

        sel.format = self._build_ghost_text_format()

        # Append our selection to the editor's existing extra selections
        # (don't replace them — Spyder uses extra selections for current
        # line highlight, matching brackets, etc.)
        existing = list(self._editor.extraSelections())
        existing.append(sel)
        self._editor.setExtraSelections(existing)

    def _build_ghost_palette(self):
        """Return theme-aware colors that clearly mark inline ghost text."""
        palette = self._editor.palette()
        base_color = palette.color(QPalette.Base)
        if not base_color.isValid():
            base_color = QColor(30, 34, 39)

        dark_theme = base_color.lightness() < 128
        if dark_theme:
            ghost_foreground = QColor(214, 205, 176)
            ghost_background = QColor(120, 99, 43, 192)
            underline = QColor(237, 224, 181)
        else:
            ghost_foreground = QColor(126, 102, 56)
            ghost_background = QColor(244, 230, 187, 168)
            underline = QColor(148, 122, 71)
        return ghost_foreground, ghost_background, underline

    def _build_ghost_text_format(self):
        """Return one text format reused for inserted and overlay ghost text."""
        foreground, background, underline = self._build_ghost_palette()
        fmt = QTextCharFormat()
        fmt.setForeground(foreground)
        fmt.setBackground(background)
        fmt.setUnderlineStyle(QTextCharFormat.DotLine)
        fmt.setUnderlineColor(underline)
        fmt.setFontItalic(True)
        return fmt

    def _remove_ghost_extra_selection(self):
        """Remove ghost extra selections from the editor.

        After undo, the ghost text range is gone. We rebuild the extra
        selections list without our entry. Spyder will refresh its own
        extra selections on the next editor update.
        """
        try:
            # Filter out any selections with our ghost format (italic + gray).
            # This is a heuristic — we check for italic since Spyder's own
            # extra selections don't typically use italic.
            existing = self._editor.extraSelections()
            filtered = [
                s for s in existing
                if not s.format.fontItalic()
                or s.format.foreground().color() != QColor(110, 110, 110)
            ]
            self._editor.setExtraSelections(filtered)
        except (RuntimeError, AttributeError):
            pass  # Editor may be destroyed

    def _on_cursor_moved(self):
        """Auto-clear ghost text when the cursor moves.

        Any cursor movement while ghost text is active means the user
        clicked elsewhere or used arrow keys — the suggestion is no
        longer relevant. Note: this handler doesn't fire during our
        own insertions because we block editor signals.
        """
        if self._ghost_active:
            self.clear(reason="cursor_move")
            return
        self._schedule_idle_completion()

    def _editor_has_focus(self):
        """Return True when the editor or one of its children has focus."""
        try:
            if self._editor.hasFocus():
                return True
            focus_widget = self._editor.focusWidget()
            return bool(
                focus_widget is not None
                and (
                    focus_widget is self._editor
                    or self._editor.isAncestorOf(focus_widget)
                )
            )
        except Exception:
            return False

    def set_manual_only(self, manual_only):
        """Turn "suggest only when asked" on or off for this editor.

        Any pending automatic request is dropped straight away, so turning
        the mode on never lets one last suggestion through.
        """
        self._manual_only = bool(manual_only)
        if self._manual_only:
            self._idle_completion_timer.stop()
            self._post_accept_completion_timer.stop()
            self._post_accept_pending = False
        logger.info(
            "Manual-only AI completions are now %s for this editor",
            "on" if self._manual_only else "off",
        )

    @property
    def manual_only(self):
        """Return whether suggestions are only produced on request."""
        return self._manual_only

    def _schedule_idle_completion(self):
        """Schedule one AI completion after a short pause."""
        self.resume_suggestions("edit")
        if self._manual_only:
            # Typing must not queue anything in this mode; the shortcut is
            # the only way to ask for a suggestion.
            return
        if self._ghost_active:
            logger.info(
                "Idle AI completion scheduling skipped because ghost text is already visible"
            )
            return
        if self._post_accept_pending:
            logger.info(
                "Idle AI completion scheduling skipped because a post-accept request is already pending (reason=%s)",
                self._post_accept_reason,
            )
            return
        if not self._editor_has_focus():
            self._idle_completion_timer.stop()
            logger.info(
                "Idle AI completion scheduling skipped because the editor does not have focus"
            )
            return
        self._idle_completion_timer.start()
        logger.info(
            "Scheduled idle AI completion after %dms",
            self._idle_completion_delay_ms,
        )

    def _request_idle_completion(self):
        """Trigger one completion when the editor has been idle long enough."""
        if self._ghost_active or not self._editor_has_focus():
            logger.info(
                "Idle AI completion request skipped: ghost_active=%s editor_has_focus=%s",
                self._ghost_active,
                self._editor_has_focus(),
            )
            return
        logger.info(
            "Requesting idle AI completion after %dms pause",
            self._idle_completion_delay_ms,
        )
        self.request_completion(source="idle")

    def _schedule_post_accept_completion(self, reason):
        """Schedule one immediate completion after accepting ghost text."""
        self._post_accept_reason = str(reason or "accepted")
        if self._manual_only:
            # Accepting a suggestion is not a request for the next one.
            self._post_accept_pending = False
            return
        if not self._editor_has_focus():
            self._post_accept_pending = False
            logger.info(
                "Post-accept AI completion scheduling skipped because the editor does not have focus (reason=%s)",
                self._post_accept_reason,
            )
            return
        self._post_accept_pending = True
        self._post_accept_completion_timer.start()
        logger.info(
            "Scheduled post-accept AI completion after %dms (reason=%s)",
            self._post_accept_completion_delay_ms,
            self._post_accept_reason,
        )

    def _request_post_accept_completion(self):
        """Trigger one follow-up completion after a full accept."""
        self._post_accept_pending = False
        if self._ghost_active or not self._editor_has_focus():
            logger.info(
                "Post-accept AI completion request skipped: ghost_active=%s editor_has_focus=%s reason=%s",
                self._ghost_active,
                self._editor_has_focus(),
                self._post_accept_reason,
            )
            return
        logger.info(
            "Requesting post-accept AI completion after %dms (reason=%s)",
            self._post_accept_completion_delay_ms,
            self._post_accept_reason,
        )
        self.request_completion(source="post_accept")
        # The backup idle timer below would re-arm the automatic path, so
        # it is skipped in manual-only mode along with everything else.
        if not self._manual_only and not self._ghost_active and self._editor_has_focus():
            self._idle_completion_timer.start()
            logger.info(
                "Scheduled backup idle AI completion after post-accept request (%dms)",
                self._idle_completion_delay_ms,
            )

    def on_completion_popup_visibility_changed(self, visible):
        """Track popup visibility and clear ghost text if the menu opens."""
        if visible:
            self._idle_completion_timer.stop()
            self._post_accept_completion_timer.stop()
            self._post_accept_pending = False
            self.clear(reason="native_popup")

    def _log_suppressed(self, reason, target):
        """Log one suppressed suggestion with the concrete reason.

        Every early exit in ``show_suggestion`` used to be silent, which made
        live logs ambiguous (the plugin could only guess "target moved").
        Naming the reason keeps validation evidence unambiguous.
        """
        try:
            cursor_pos = self._editor.textCursor().position()
        except Exception:
            cursor_pos = -1
        logger.info(
            "Ghost text suppressed: %s (cursor=%s target=%s)",
            reason,
            cursor_pos,
            target,
        )

    def _matches_target(self, target):
        """Return True if the editor is still at the target cursor position.

        A target created while a previous ghost was visible carries the
        cursor position *after* that ghost (``offset``) and the logical
        insertion spot (``insert_offset``). Once the old ghost is replaced
        the cursor is back at the insertion spot, so either position counts
        as "still there". Line/column are implied by the position.
        """
        if not target:
            return True

        try:
            position = self._editor.textCursor().position()
            expected = {int(target.get("offset", -1))}
            if target.get("insert_offset") is not None:
                expected.add(int(target["insert_offset"]))
            return position in expected
        except Exception:
            return False

    def cleanup(self):
        """Remove ghost text, event filters, and disconnect signals.

        Call this when the editor is being destroyed or the plugin
        is shutting down.
        """
        # Remove any active ghost text from the document
        if self._ghost_active:
            self.clear(reason="cleanup", record_event=False)
        self._idle_completion_timer.stop()
        self._post_accept_completion_timer.stop()
        self._post_accept_pending = False

        try:
            self._editor.cursorPositionChanged.disconnect(
                self._on_cursor_moved
            )
        except (RuntimeError, TypeError):
            pass  # Already disconnected or editor destroyed

        try:
            self._editor.textChanged.disconnect(self._schedule_idle_completion)
            self._editor.verticalScrollBar().sliderPressed.disconnect(
                self.pause_for_scroll
            )
        except (RuntimeError, TypeError):
            pass

        for target in self._event_targets:
            try:
                target.removeEventFilter(self._event_filter)
            except (RuntimeError, TypeError):
                pass

        # Remove the completion popup watcher
        if self._popup_watcher is not None:
            try:
                self._editor.completion_widget.removeEventFilter(
                    self._popup_watcher
                )
            except (RuntimeError, TypeError, AttributeError):
                pass

    def _accept_prefix(self, accepted_text, method):
        """Accept a prefix of the ghost text and keep the remainder visible."""
        if not accepted_text or not self._ghost_text.startswith(accepted_text):
            return False

        full_text = self._ghost_text
        remainder = full_text[len(accepted_text):]
        insert_offset = self._insert_offset
        display_offset = self._display_cursor_offset

        self.clear(reason="accepted", record_event=False)
        self._insert_real_text(accepted_text, insert_offset, display_offset)
        new_display_offset = display_offset
        if insert_offset >= 0 and display_offset >= 0 and insert_offset <= display_offset:
            new_display_offset = display_offset + utf16_length(accepted_text)
        elif display_offset < 0:
            new_display_offset = self._editor.textCursor().position()

        if remainder:
            cursor = self._editor.textCursor()
            self.show_suggestion(
                remainder,
                target={
                    "offset": new_display_offset,
                    "line": cursor.blockNumber(),
                    "column": cursor.columnNumber(),
                    "insert_offset": (
                        insert_offset + utf16_length(accepted_text)
                        if insert_offset >= 0
                        else cursor.position()
                    ),
                },
            )
        else:
            self._schedule_post_accept_completion(f"partial_{method}")

        logger.debug(
            "Ghost text partially accepted by %s: %d chars accepted, %d remain",
            method,
            len(accepted_text),
            len(remainder),
        )
        logger.info(
            "Ghost text partially accepted by %s: accepted=%d remaining=%d",
            method,
            len(accepted_text),
            len(remainder),
        )
        self._emit_lifecycle_event(
            "accepted",
            method=method,
            chars=len(accepted_text),
            remaining_chars=len(remainder),
        )
        return True

    @staticmethod
    def _next_word_segment(text):
        """Return the next word-like prefix from one ghost suggestion."""
        if not text:
            return ""

        length = len(text)
        index = 0

        while index < length and text[index].isspace():
            index += 1

        if index == length:
            return text

        if text[index].isalnum() or text[index] == "_":
            while index < length and (
                text[index].isalnum() or text[index] == "_"
            ):
                index += 1
        else:
            while index < length and (
                not text[index].isalnum()
                and text[index] != "_"
                and not text[index].isspace()
            ):
                index += 1

        while index < length and text[index].isspace() and text[index] != "\n":
            index += 1

        return text[:index]

    @staticmethod
    def _next_line_segment(text):
        """Return the next line prefix from one ghost suggestion."""
        if not text:
            return ""

        newline_index = text.find("\n")
        if newline_index == -1:
            return text
        return text[:newline_index + 1]

    def _emit_lifecycle_event(self, event_name, **payload):
        """Send one lifecycle event to the optional observer callback."""
        if self._lifecycle_callback is None:
            return

        if "target" not in payload and self._target is not None:
            payload["target"] = dict(self._target)
        # Exact ghost bounds (Qt offsets) let the provider map cursor
        # positions reported while the ghost is in the document back to
        # ghost-free coordinates; ``ghost_visible`` is the state *after*
        # this event.
        payload["ghost_visible"] = bool(self._ghost_active)
        payload["ghost_start"] = int(self._ghost_start)
        payload["ghost_end"] = int(self._ghost_end)

        try:
            self._lifecycle_callback(event_name, payload)
        except Exception as error:  # pragma: no cover - defensive UI guard
            logger.debug("Ghost lifecycle callback failed: %s", error)

    def _insert_real_text(self, text, insert_offset, display_offset):
        """Insert accepted text at the ghost anchor and restore the cursor."""
        cursor = self._editor.textCursor()
        insert_at = (
            insert_offset
            if isinstance(insert_offset, int) and insert_offset >= 0
            else cursor.position()
        )
        insert_at = max(0, min(insert_at, self._editor.document().characterCount() - 1))
        logger.info(
            "Inserting accepted ghost text: chars=%d insert_offset=%s display_offset=%s",
            len(text),
            insert_at,
            display_offset,
        )
        insert_cursor = QTextCursor(self._editor.document())
        insert_cursor.setPosition(insert_at)
        insert_cursor.insertText(text, QTextCharFormat())

        restore_offset = (
            display_offset
            if isinstance(display_offset, int) and display_offset >= 0
            else cursor.position()
        )
        if insert_at <= restore_offset:
            restore_offset += utf16_length(text)
        cursor = self._editor.textCursor()
        cursor.setPosition(max(0, min(restore_offset, self._editor.document().characterCount() - 1)))
        self._editor.setTextCursor(cursor)

    def _completion_popup_visible(self, manual_only=False):
        """Return True when the editor's native completion popup is visible."""
        popup_widget = getattr(self._editor, "completion_widget", None)
        if popup_widget is None:
            return False

        try:
            if not popup_widget.isVisible():
                return False
            if hasattr(popup_widget, "is_empty") and popup_widget.is_empty():
                return False
            if manual_only and bool(getattr(popup_widget, "automatic", False)):
                return False
            return True
        except (RuntimeError, AttributeError):
            return False

    def _hide_completion_popup(self):
        """Hide Spyder's native completion popup when ghost text takes over."""
        try:
            if hasattr(self._editor, "hide_completion_widget"):
                self._editor.hide_completion_widget()
            elif hasattr(self._editor, "completion_widget"):
                self._editor.completion_widget.hide()
        except (RuntimeError, AttributeError):
            pass
