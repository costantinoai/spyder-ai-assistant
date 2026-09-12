"""A transcript built from message widgets rather than one text document.

Qt's rich-text engine cannot round a corner: `border-radius` inside a
QTextDocument is parsed and discarded, which is why the old transcript shipped
a `bubble_border_radius` setting that did nothing at any value. Widgets have no
such limit, so each message becomes a `QFrame` carrying its own gradient,
hairline, radius, shadow, alignment and width, and the markdown pipeline keeps
rendering only the *content* inside it.

The content still goes through a `QTextBrowser`, so tables, lists, blockquotes
and highlighted code render exactly as before. Each is sized to its own
document, because a bubble must hug its text rather than take a fixed height.

Cost of this shape, measured before choosing it: 1,000 messages occupy about
31 MB and repaint in roughly 7 ms regardless of conversation length, since only
visible bubbles paint. A web engine cost 289 MB for a single view before any
messages existed.
"""

from __future__ import annotations

from qtpy.QtCore import Qt, Signal
from qtpy.QtGui import QColor, QGuiApplication
from qtpy.QtWidgets import (
    QFrame,
    QGraphicsDropShadowEffect,
    QHBoxLayout,
    QLabel,
    QScrollArea,
    QSizePolicy,
    QTextBrowser,
    QVBoxLayout,
    QWidget,
)


# How much of the pane one bubble may occupy. The remainder is the gutter that
# shows which side a turn belongs to; without it every turn looks alike. A
# maximum width alone does not achieve this: the frame still shrinks to its
# content, so the row carries stretch factors instead.
BUBBLE_WIDTH_SHARE = 78
GUTTER_SHARE = 22

ROLE_USER = "user"
ROLE_ASSISTANT = "assistant"
ROLE_NOTICE = "notice"

ROLE_LABELS = {ROLE_USER: "YOU", ROLE_ASSISTANT: "ASSISTANT"}


class StreamBuffer:
    """Holds back markup that is still arriving, so it is never painted raw.

    Two things flash on screen without this. A ``<think>`` tag split across
    chunks paints its prefix (``<th``) as literal text before vanishing when
    the rest lands, and a fence paints ``` and then ```python as prose until
    the block's body arrives. Both are one defect: text that only becomes
    meaningful when complete gets rendered while still partial.

    So withhold a trailing fragment that could still become markup, and release
    it as soon as it either completes or proves to be ordinary text.
    """

    #: Longest prefix worth withholding, the length of ``<think>``.
    MAX_HELD = 7

    def __init__(self):
        self._text = ""

    def add(self, chunk):
        """Absorb a chunk and return the text that is safe to render now."""
        self._text += chunk
        return self.safe_text()

    def safe_text(self):
        """Everything except a trailing fragment that may still be markup."""
        held = self._pending_length()
        return self._text[: len(self._text) - held] if held else self._text

    def full_text(self):
        """Everything received, including whatever is currently withheld."""
        return self._text

    def clear(self):
        self._text = ""

    def _pending_length(self):
        """How many trailing characters form an incomplete tag or fence."""
        text = self._text
        if not text:
            return 0

        # An unterminated tag: hold from '<' onward until '>' arrives. Only a
        # short run can still become a tag we care about, so a stray '<' in
        # prose is released rather than stalling the stream.
        angle = text.rfind("<")
        if angle != -1 and ">" not in text[angle:]:
            if len(text) - angle <= self.MAX_HELD:
                return len(text) - angle

        # An opening fence. The renderer draws an *unclosed* block as a partial
        # code card, but only once it has a body, so withholding until the
        # closing fence would keep a long block invisible while it streams.
        # Hold only while the opener itself is still meaningless as markup.
        if text.count("```") % 2 == 1:
            opener = text.rfind("```")
            newline = text.find("\n", opener)
            if newline == -1:
                # The info string is still arriving: ``` or ```pyth
                return len(text) - opener
            if newline == len(text) - 1:
                # Opener complete, body empty. This is the frame where the
                # reported flash happened, with ```python shown as prose.
                return len(text) - opener
            return 0

        # An inline code span on the current line, the same defect at a smaller
        # scale. A closing ``` also lands on a line, so skip lines carrying a
        # fence or its three backticks read as one unbalanced span.
        line_start = text.rfind("\n") + 1
        trailing = text[line_start:]
        if "```" in trailing:
            return 0
        if trailing.count("`") % 2 == 1:
            return len(text) - (line_start + trailing.rfind("`"))
        return 0


class MessageBubble(QFrame):
    """One message: a rounded, gradient surface wrapping rendered content."""

    def __init__(self, role, tokens, label=None, parent=None):
        super().__init__(parent)
        self._role = role
        self._tokens = tokens
        self.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Maximum)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(14, 10, 14, 12)
        layout.setSpacing(6)

        self._label = QLabel(label or "", self)
        self._label.setVisible(bool(label))
        layout.addWidget(self._label)

        self._body = QTextBrowser(self)
        self._body.setFrameShape(QFrame.NoFrame)
        self._body.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self._body.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self._body.setOpenExternalLinks(False)
        self._body.setOpenLinks(False)
        self._body.setTextInteractionFlags(
            Qt.TextSelectableByMouse
            | Qt.TextSelectableByKeyboard
            | Qt.LinksAccessibleByMouse
            | Qt.LinksAccessibleByKeyboard
        )
        layout.addWidget(self._body)

        if tokens is not None:
            self.apply_tokens(tokens)

    # --- content ---------------------------------------------------------
    @property
    def anchor_clicked(self):
        """The body's anchor signal, so a list can route actions centrally."""
        return self._body.anchorClicked

    def set_html(self, html):
        """Replace the bubble's content and resize it to the new document."""
        self._body.setHtml(html)
        self._resize_to_document()

    def html(self):
        return self._body.toHtml()

    def plain_text(self):
        return self._body.toPlainText()

    def _resize_to_document(self):
        """Height the body to its content; a bubble must hug its own text."""
        document = self._body.document()
        width = self._body.width() or max(160, self.width() - 28) or 420
        document.setTextWidth(width)
        self._body.setFixedHeight(int(document.size().height()) + 2)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._resize_to_document()

    # --- appearance ------------------------------------------------------
    def apply_tokens(self, tokens):
        """Restyle from the current design tokens."""
        self._tokens = tokens
        if tokens is None:
            return
        surface = tokens.user if self._role == ROLE_USER else tokens.assistant
        # The corner nearest the speaker is tightened, which is what makes a
        # bubble read as coming *from* a side rather than floating.
        tight = (
            "border-bottom-right-radius: 5px;"
            if self._role == ROLE_USER
            else "border-bottom-left-radius: 5px;"
        )
        self.setStyleSheet(
            "MessageBubble {"
            f" background: {surface.gradient()};"
            f" border: 1px solid {surface.border};"
            f" border-radius: {tokens.radius}px; {tight} }}"
        )
        shadow = QGraphicsDropShadowEffect(self)
        shadow.setBlurRadius(22)
        shadow.setXOffset(0)
        shadow.setYOffset(6)
        shadow.setColor(QColor(0, 0, 0, 150 if tokens.is_dark else 60))
        self.setGraphicsEffect(shadow)

        label_color = tokens.dim if self._role == ROLE_USER else tokens.accent
        self._label.setStyleSheet(
            f"color: {label_color}; font-family: '{tokens.ui_font}';"
            f" font-size: {tokens.label_size}px; letter-spacing: 1.3px;"
            " border: none; background: transparent;"
        )
        self._body.setStyleSheet(
            "QTextBrowser { background: transparent; border: none;"
            f" color: {tokens.text}; font-family: '{tokens.ui_font}';"
            f" font-size: {tokens.font_size}px; }}"
        )
        self._resize_to_document()


class MessageRow(QWidget):
    """A bubble plus its gutter, which is what aligns a turn to one side."""

    def __init__(self, bubble, role, parent=None):
        super().__init__(parent)
        self.bubble = bubble
        self.role = role
        self.setStyleSheet("background: transparent;")
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        if role == ROLE_USER:
            layout.addStretch(GUTTER_SHARE)
            layout.addWidget(bubble, BUBBLE_WIDTH_SHARE)
        else:
            layout.addWidget(bubble, BUBBLE_WIDTH_SHARE)
            layout.addStretch(GUTTER_SHARE)


class MessageList(QScrollArea):
    """The transcript: a scrolling column of message bubbles.

    Exposes the same surface the single-document transcript did, so a session
    can be handed either one.
    """

    sig_apply_code_requested = Signal(str)
    sig_starter_action = Signal(str)

    def __init__(self, parent=None, renderer=None, tokens=None):
        super().__init__(parent)
        self.setObjectName("aiChatTranscript")
        self.setWidgetResizable(True)
        self.setFrameShape(QFrame.NoFrame)
        self.setAccessibleName("Chat transcript")

        self._renderer = renderer
        self._tokens = tokens
        self._rows = []
        self._stream = StreamBuffer()
        self._stream_bubble = None

        self._canvas = QWidget()
        self._canvas.setStyleSheet("background: transparent;")
        self._column = QVBoxLayout(self._canvas)
        self._column.setContentsMargins(14, 14, 14, 14)
        self._column.setSpacing(12)
        self._column.addStretch(1)
        self.setWidget(self._canvas)

    # --- wiring ----------------------------------------------------------
    def set_renderer(self, renderer):
        self._renderer = renderer

    def apply_tokens(self, tokens):
        """Push new design tokens to every bubble."""
        self._tokens = tokens
        for row in self._rows:
            row.bubble.apply_tokens(tokens)

    def update_appearance(self, **kwargs):
        """Accept the appearance contract; colour comes from the tokens.

        The renderer owns fonts and syntax styles, so forward what it
        understands and ignore the rest rather than duplicating its keys here.
        """
        if self._renderer is not None and hasattr(self._renderer, "update"):
            forwarded = {
                key: kwargs[key]
                for key in (
                    "code_font_family",
                    "code_font_size",
                    "pygments_style_dark",
                    "pygments_style_light",
                )
                if key in kwargs
            }
            if forwarded:
                self._renderer.update(**forwarded)
        tokens = kwargs.get("tokens")
        if tokens is not None:
            self.apply_tokens(tokens)

    # --- building blocks -------------------------------------------------
    def _render(self, text, track_code_blocks=False):
        if self._renderer is None:
            return text
        return self._renderer.render(text, track_code_blocks=track_code_blocks)

    def _add_bubble(self, role, html, label=None):
        bubble = MessageBubble(role, self._tokens, label=label, parent=self._canvas)
        bubble.set_html(html)
        bubble.anchor_clicked.connect(self._on_anchor)
        row = MessageRow(bubble, role, parent=self._canvas)
        # Insert before the trailing stretch so rows stay at the top.
        self._column.insertWidget(self._column.count() - 1, row)
        self._rows.append(row)
        self._scroll_to_bottom()
        return bubble

    def _scroll_to_bottom(self):
        bar = self.verticalScrollBar()
        bar.setValue(bar.maximum())

    # --- the transcript contract -----------------------------------------
    def append_user_message(self, text):
        self._add_bubble(ROLE_USER, self._render(text), label=ROLE_LABELS[ROLE_USER])

    def append_assistant_message(self, text):
        self._add_bubble(
            ROLE_ASSISTANT,
            self._render(text, track_code_blocks=True),
            label=ROLE_LABELS[ROLE_ASSISTANT],
        )

    def start_assistant_message(self):
        """Open an empty bubble that the stream will fill."""
        self._stream.clear()
        self._stream_bubble = self._add_bubble(
            ROLE_ASSISTANT, "", label=ROLE_LABELS[ROLE_ASSISTANT]
        )

    def append_chunk(self, text):
        """Add streamed text, rendering only what is safe to show yet."""
        if self._stream_bubble is None:
            self.start_assistant_message()
        safe = self._stream.add(text)
        self._stream_bubble.set_html(self._render(safe))
        self._scroll_to_bottom()

    def finish_assistant_message(self):
        """Commit the stream, including anything the buffer was holding."""
        if self._stream_bubble is None:
            return
        final = self._stream.full_text()
        if not final.strip():
            # An empty reply leaves no bubble behind, rather than an empty one.
            self.discard_assistant_message()
            return
        self._stream_bubble.set_html(self._render(final, track_code_blocks=True))
        self._stream_bubble = None
        self._stream.clear()
        self._scroll_to_bottom()

    def discard_assistant_message(self):
        """Drop the in-progress bubble without leaving a gap."""
        if self._stream_bubble is None:
            return
        for row in list(self._rows):
            if row.bubble is self._stream_bubble:
                self._column.removeWidget(row)
                row.setParent(None)
                row.deleteLater()
                self._rows.remove(row)
                break
        self._stream_bubble = None
        self._stream.clear()

    def _append_notice(self, level, message):
        bubble = self._add_bubble(
            ROLE_NOTICE, self._render(message), label=level.upper()
        )
        return bubble

    def append_error(self, message):
        return self._append_notice("error", message)

    def append_warning(self, message):
        return self._append_notice("warning", message)

    def append_info(self, message):
        return self._append_notice("info", message)

    def clear_conversation(self):
        """Remove every bubble and forget the stream."""
        for row in list(self._rows):
            self._column.removeWidget(row)
            row.setParent(None)
            row.deleteLater()
        self._rows.clear()
        self._stream_bubble = None
        self._stream.clear()
        if self._renderer is not None and hasattr(self._renderer, "clear_code_blocks"):
            self._renderer.clear_code_blocks()

    def rebuild_from_messages(self, messages):
        """Re-render a whole conversation from authoritative history."""
        self.clear_conversation()
        for message in messages or []:
            role = message.get("role")
            content = message.get("content", "")
            if role == "user":
                self.append_user_message(content)
            elif role == "assistant":
                self.append_assistant_message(content)
        self._scroll_to_bottom()

    # --- code actions -----------------------------------------------------
    def last_code_block(self):
        """The most recent code block, or None when the chat has none."""
        blocks = getattr(self._renderer, "code_blocks", None) or []
        return blocks[-1] if blocks else None

    def copy_last_code_block(self):
        """Copy the most recent code block; True when there was one."""
        code = self.last_code_block()
        if not code:
            return False
        QGuiApplication.clipboard().setText(code)
        return True

    def _on_anchor(self, url):
        """One route for every action link, however it was activated."""
        anchor = url.toString() if hasattr(url, "toString") else str(url)
        blocks = getattr(self._renderer, "code_blocks", None) or []

        if anchor.startswith("copy://"):
            index = self._anchor_index(anchor, len("copy://"))
            if index is not None and 0 <= index < len(blocks):
                QGuiApplication.clipboard().setText(blocks[index])
            return True
        if anchor.startswith("apply://"):
            index = self._anchor_index(anchor, len("apply://"))
            if index is not None and 0 <= index < len(blocks):
                self.sig_apply_code_requested.emit(blocks[index])
            return True
        if anchor.startswith("starter://"):
            self.sig_starter_action.emit(anchor[len("starter://"):])
            return True
        return False

    @staticmethod
    def _anchor_index(anchor, prefix_length):
        """Parse the index out of an action anchor, or None when malformed."""
        try:
            return int(anchor[prefix_length:])
        except (TypeError, ValueError):
            return None
