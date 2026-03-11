"""Custom text input widget for the chat panel.

Provides a QPlainTextEdit that sends on Enter and inserts newlines
on Shift+Enter, matching common chat application behavior. Auto-resizes
vertically as the user types more lines (up to a maximum height).
"""

from qtpy.QtCore import Qt, Signal
from qtpy.QtWidgets import QPlainTextEdit


# Height limits for the auto-resizing input area.
# MIN = single line + padding, MAX = ~6 lines before scrolling kicks in.
_MIN_HEIGHT = 36
_MAX_HEIGHT = 150


class ChatInput(QPlainTextEdit):
    """Text input field for the AI chat panel.

    Auto-resizes vertically to fit the content (up to _MAX_HEIGHT).
    Emits submit_requested when the user presses Enter (without Shift).
    Shift+Enter inserts a newline for multi-line input.

    Signals:
        submit_requested: Emitted when the user presses Enter to send.
    """

    submit_requested = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setPlaceholderText(
            "Type a message... (Shift+Enter for newline)"
        )
        self.setMaximumHeight(_MAX_HEIGHT)
        self.setMinimumHeight(_MIN_HEIGHT)

        # Auto-resize when content changes. textChanged fires on every
        # edit (typing, paste, clear), so the height stays in sync.
        self.textChanged.connect(self._auto_resize)

    def _auto_resize(self):
        """Adjust height to fit the current content.

        Calculates the ideal height from the document's line count and
        the font's line spacing, clamped to [_MIN_HEIGHT, _MAX_HEIGHT].
        This gives a smooth grow/shrink as the user types or deletes lines.
        """
        # Document height = content height in pixels
        doc_height = int(self.document().size().height())
        # Add margins (top + bottom frame + internal padding)
        margins = self.contentsMargins()
        target = doc_height + margins.top() + margins.bottom() + 8

        # Clamp to allowed range
        target = max(_MIN_HEIGHT, min(target, _MAX_HEIGHT))
        self.setFixedHeight(target)

    def keyPressEvent(self, event):
        """Handle Enter to submit, Shift+Enter for newline."""
        if event.key() in (Qt.Key_Return, Qt.Key_Enter):
            if event.modifiers() & Qt.ShiftModifier:
                # Shift+Enter: insert a newline for multi-line messages
                super().keyPressEvent(event)
            else:
                # Enter without modifier: submit the message
                self.submit_requested.emit()
        else:
            super().keyPressEvent(event)

    def get_text_and_clear(self):
        """Return the current input text and clear the field.

        Convenience method used by the chat widget when sending a
        message. Strips leading/trailing whitespace and resets the
        input height back to minimum.

        Returns:
            The input text, stripped of whitespace.
        """
        text = self.toPlainText().strip()
        self.clear_text()
        return text

    def peek_text(self):
        """Return the current stripped text without mutating the widget."""
        return self.toPlainText().strip()

    def clear_text(self):
        """Clear the input field and reset its compact height."""
        self.clear()
        self.setFixedHeight(_MIN_HEIGHT)
