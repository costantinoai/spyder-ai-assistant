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

import logging

from qtpy.QtCore import Qt, QEvent, QObject
from qtpy.QtGui import QColor, QTextCharFormat, QTextCursor
from qtpy.QtWidgets import QTextEdit

logger = logging.getLogger(__name__)


class _GhostEventFilter(QObject):
    """Event filter for intercepting key events on the editor.

    Separated from GhostTextManager to properly subclass QObject
    (required for installEventFilter). Routes events to the manager.
    """

    def __init__(self, manager, editor):
        super().__init__(editor)
        self._manager = manager
        self._editor = editor

    def eventFilter(self, obj, event):
        """Intercept key events for ghost text acceptance/dismissal."""
        if event.type() != QEvent.KeyPress:
            return False

        if not self._manager.has_suggestion():
            return False

        key = event.key()
        logger.debug(
            "Ghost keypress intercepted: key=%r text=%r target=%s",
            key,
            event.text(),
            obj.__class__.__name__,
        )

        modifiers = event.modifiers()

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

    The native LSP popup should take priority over inline ghost text.
    This watcher keeps the manager informed about popup visibility so
    ghost suggestions are cleared or suppressed when needed.
    """

    def __init__(self, manager, completion_widget):
        super().__init__(completion_widget)
        self._manager = manager

    def eventFilter(self, obj, event):
        """Track completion popup visibility changes."""
        if event.type() == QEvent.Show:
            if self._manager.has_suggestion():
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

    def __init__(self, editor, lifecycle_callback=None):
        self._editor = editor
        self._lifecycle_callback = lifecycle_callback
        self._ghost_active = False
        self._ghost_text = ""
        self._target = None
        # Document positions of the inserted ghost text, used for
        # applying extra selections (gray overlay) on the range.
        self._ghost_start = -1
        self._ghost_end = -1

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
        # Clear any previous ghost text first
        if self._ghost_active:
            self.clear(reason="replaced", record_event=False)

        if not text:
            return False

        if self._completion_popup_visible():
            self._hide_completion_popup()

        if not self._matches_target(target):
            logger.debug("Ghost text skipped because the editor target moved")
            return False

        self._ghost_text = text
        self._target = target or {}
        cursor = self._editor.textCursor()
        original_pos = cursor.position()

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
            cursor.beginEditBlock()
            cursor.insertText(text)
            cursor.endEditBlock()

            self._ghost_start = original_pos
            self._ghost_end = original_pos + len(text)

            # Restore cursor to original position — the user should see
            # their cursor at the same spot, with the gray text after it.
            cursor.setPosition(original_pos)
            self._editor.setTextCursor(cursor)
        finally:
            self._editor.blockSignals(False)
            # Restore the modified flag so ghost text doesn't make the
            # file tab show a "dirty" indicator.
            doc.setModified(was_modified)

        self._ghost_active = True

        # Apply gray color via extra selections. This renders ON TOP of
        # the syntax highlighter's formatting, so ghost text is always
        # gray regardless of syntax colors.
        self._apply_ghost_extra_selection()

        logger.debug("Ghost text shown: %d chars", len(text))
        self._emit_lifecycle_event(
            "shown",
            chars=len(text),
            target=dict(self._target or {}),
        )
        return True

    def clear(self, reason="unknown", record_event=True):
        """Remove the ghost text from the document.

        Uses document.undo() to cleanly reverse the insertion in one step.
        Editor signals are blocked during removal to prevent side effects.
        Also removes the gray extra selection.
        """
        if not self._ghost_active:
            return

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
        self._target = None
        self._ghost_start = -1
        self._ghost_end = -1
        logger.debug("Ghost text cleared")

        # Remove our ghost extra selection. After undo the text is gone,
        # so the selection is invalid. Rebuild without it.
        self._remove_ghost_extra_selection()

        if record_event:
            self._emit_lifecycle_event("dismissed", reason=reason)

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

        # Remove the gray ghost text (silently via undo)
        self.clear(reason="accepted", record_event=False)

        # Re-insert as normal text — this is a real edit that enters
        # the undo stack and triggers textChanged/dirty indicators.
        self._editor.insert_text(text)
        logger.debug("Ghost text accepted: %d chars", len(text))
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

        self.clear(reason="accepted", record_event=False)
        self._editor.insert_text(text)

        if remainder:
            cursor = self._editor.textCursor()
            self.show_suggestion(
                remainder,
                target={
                    "offset": cursor.position(),
                    "line": cursor.blockNumber(),
                    "column": cursor.columnNumber(),
                },
            )
            logger.debug(
                "Ghost text advanced by typed prefix: %r (%d chars remain)",
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
            self._emit_lifecycle_event(
                "accepted",
                method="full",
                chars=len(text),
                remaining_chars=0,
            )

        return True

    def request_completion(self):
        """Manually trigger a completion request.

        Called when the user presses the completion shortcut (e.g.,
        Ctrl+Shift+Space). Triggers the editor's built-in completion
        mechanism which routes through the AI completion provider.
        """
        try:
            self._editor.do_completion()
        except Exception as e:
            logger.debug("Manual completion trigger failed: %s", e)

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

        # Gray foreground — dimmed but readable against dark backgrounds.
        # Also use italic to visually distinguish from real code.
        fmt = QTextCharFormat()
        fmt.setForeground(QColor(110, 110, 110))
        fmt.setFontItalic(True)
        sel.format = fmt

        # Append our selection to the editor's existing extra selections
        # (don't replace them — Spyder uses extra selections for current
        # line highlight, matching brackets, etc.)
        existing = list(self._editor.extraSelections())
        existing.append(sel)
        self._editor.setExtraSelections(existing)

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

    def on_completion_popup_visibility_changed(self, visible):
        """Track popup visibility and clear ghost text if the menu opens."""
        return

    def _matches_target(self, target):
        """Return True if the editor is still at the target cursor position."""
        if not target:
            return True

        try:
            cursor = self._editor.textCursor()
            return (
                cursor.position() == int(target.get("offset", -1))
                and cursor.blockNumber() == int(target.get("line", -1))
                and cursor.columnNumber() == int(target.get("column", -1))
            )
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

        try:
            self._editor.cursorPositionChanged.disconnect(
                self._on_cursor_moved
            )
        except (RuntimeError, TypeError):
            pass  # Already disconnected or editor destroyed

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

        self.clear(reason="accepted", record_event=False)
        self._editor.insert_text(accepted_text)

        if remainder:
            cursor = self._editor.textCursor()
            self.show_suggestion(
                remainder,
                target={
                    "offset": cursor.position(),
                    "line": cursor.blockNumber(),
                    "column": cursor.columnNumber(),
                },
            )

        logger.debug(
            "Ghost text partially accepted by %s: %d chars accepted, %d remain",
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

        try:
            self._lifecycle_callback(event_name, payload)
        except Exception as error:  # pragma: no cover - defensive UI guard
            logger.debug("Ghost lifecycle callback failed: %s", error)

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
