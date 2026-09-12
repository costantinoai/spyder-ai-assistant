"""Chat display widget for rendering conversation messages.

Renders user messages and assistant responses in a scrollable QTextEdit.
Supports real-time streaming (tokens appended as they arrive) and basic
markdown formatting including fenced code blocks and inline code.

The markdown-to-HTML pipeline itself lives in ``MarkdownRenderer``
(``utils/markdown_render.py``): it touches no QTextEdit API, so a second
transcript view can produce exactly the same HTML. This widget keeps the
bubble, thinking-block and placeholder layout and delegates every
markdown call to its renderer.

Thinking/reasoning support: Models like Qwen3 emit <think>...</think>
blocks before the actual answer. These are detected during streaming and
rendered in a dimmed section separate from the main response.
The thinking block shows the model's reasoning process (like Cursor's
"Show reasoning" feature).

Code blocks in assistant messages include apply links that emit signals
when clicked, allowing the plugin to open a preview dialog before
mutating the active editor.

HTML structure note: Qt's QTextEdit HTML renderer cannot properly contain
block-level elements (<pre>, <table>) inside <div> tags — they break out
of the parent container. To work around this, ALL message containers use
<table> cells with inline styles. This is the only reliable way to keep
code blocks visually contained within their message bubble.

Scroll behavior: During streaming, auto-scroll only occurs if the user
was already at or near the bottom of the display. If the user scrolled
up to read earlier content, auto-scroll is suppressed until they click
the "scroll to bottom" button or manually scroll back to the bottom.
This prevents the common annoyance of being snapped away from content
the user is actively reading.

Streaming render optimization: Completed messages are stored as pre-built
HTML (`_html_content`). During streaming, only the active message's
markdown is re-rendered each chunk — the completed portion is concatenated
without re-processing. This avoids the O(n*m) cost of re-rendering all
n messages on every one of m streaming tokens.
"""

import logging
import time

from qtpy.QtGui import QTextCursor, QTextFrameFormat
from qtpy.QtCore import Qt, QTimer, Signal, QSize, QEvent
from qtpy.QtWidgets import QApplication, QTextEdit, QToolButton

from spyder_ai_assistant.utils.chat_themes import (
    get_theme_colors,
    parse_color_overrides,
)
from spyder_ai_assistant.utils.markdown_render import (
    MarkdownRenderer,
    close_list_stack,
    escape_html,
    is_list_item,
    is_table_row,
    is_table_separator,
    parse_table_cells,
    trim_before_block,
    unescape_html,
)

logger = logging.getLogger(__name__)

# Pixel threshold for "near bottom" detection. If the scrollbar is
# within this many pixels of the maximum, we consider the user to
# be "at the bottom" and auto-scroll will continue during streaming.
_SCROLL_BOTTOM_THRESHOLD_PX = 30

# Streaming re-renders are coalesced to this cadence (about 30 fps). Tokens
# arrive far faster than the eye can follow, and every render re-parses the
# streaming markdown, so rendering each token is pure waste.
_STREAM_RENDER_INTERVAL_S = 0.033

# Offered in an empty transcript: (link label, prompt). Clicking one
# prefills the input instead of sending, so the user can edit it first --
# a starter that fired immediately would spend a model call on a guess.
_STARTER_ACTIONS = (
    ("Explain the current file", "Explain what the current file does."),
    (
        "Review it for bugs",
        "Review the current file and explain the riskiest problem you find.",
    ),
    ("Add docstrings", "Add docstrings to the functions in the current file."),
)


class ChatDisplay(QTextEdit):
    """Read-only text display for the AI chat conversation.

    Renders messages as styled HTML with basic markdown support.
    During streaming, tokens accumulate in a buffer and the current
    assistant message is re-rendered on each chunk.

    Smart scroll: Auto-scroll only happens during streaming when the
    user is already at or near the bottom. If they scroll up to read,
    a floating "scroll to bottom" button appears. Clicking it (or
    manually scrolling to the bottom) re-enables auto-scroll.

    Signals:
        sig_apply_code_requested(str): Emitted when the user clicks an
            "Apply..." link on a code block. The str argument is the raw
            code content to preview and optionally apply.

    Usage:
        display.append_user_message("Hello")
        display.start_assistant_message()
        display.append_chunk("Here is ")
        display.append_chunk("the answer")
        display.finish_assistant_message()
    """

    # Emitted when the user clicks "Apply..." on a code block.
    # Carries the raw code text to preview in the active editor.
    sig_apply_code_requested = Signal(str)
    # Emitted with a prompt when a starter link in the empty transcript is
    # clicked. The widget prefills the input; it does not send.
    sig_starter_action = Signal(str)

    sig_theme_changed = Signal()

    # Resolve all palettes through the shared schema.
    _LIGHT_THEME = get_theme_colors("default", False)
    _DARK_THEME = get_theme_colors("default", True)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setReadOnly(True)
        self.setAcceptRichText(True)
        # A read-only QTextEdit reaches links with the mouse only. Adding
        # LinksAccessibleByKeyboard lets Tab move between the transcript's
        # Copy and Apply actions; keyPressEvent activates the focused one.
        self.setTextInteractionFlags(
            Qt.TextSelectableByMouse
            | Qt.TextSelectableByKeyboard
            | Qt.LinksAccessibleByMouse
            | Qt.LinksAccessibleByKeyboard
        )
        self.setAccessibleName("Chat transcript")

        # Detect dark vs light theme from the widget's background color.
        # If the background luminance is below 128, we're on a dark theme.
        bg = self.palette().color(self.backgroundRole())
        is_dark = bg.lightness() < 128

        # Theme state: preset name + per-color overrides.
        # Resolved into self._theme via get_theme_colors().
        self._theme_preset = "default"
        self._theme_color_overrides = {}

        # The renderer owns everything only the markdown pipeline reads:
        # the resolved theme, the dark/light flag, the code font, the
        # Pygments styles, the tracked code blocks and the highlight
        # cache. The widget reaches all of it through the delegating
        # properties below, so there is a single copy of each value.
        # The code font and Pygments defaults now live in MarkdownRenderer
        # and still match the values this widget used to hardcode.
        self._renderer = MarkdownRenderer(
            get_theme_colors("default", is_dark), is_dark=is_dark,
        )

        # Configurable appearance values — initialized to defaults that
        # match the previously-hardcoded values. Updated at runtime via
        # update_appearance() when the user changes settings.
        self._font_family = "sans-serif"
        self._font_size = 10        # pt
        self._line_height = 1.5
        self._bubble_padding = 12   # px (used as cellpadding)
        self._bubble_border_radius = 8  # px
        self._bubble_spacing = 4    # px (vertical margin between bubbles)

        # Buffer for accumulating streaming tokens from the LLM
        self._streaming_buffer = ""
        # Whether an assistant response is currently being streamed
        self._is_streaming = False
        # The streaming bubble lives in its own frame at the end of the
        # document; only that frame is replaced per render, so the cost of
        # a chunk no longer grows with the length of the transcript.
        self._stream_frame = None
        self._stream_dirty = False
        self._last_stream_render = 0.0
        self._stream_render_timer = QTimer(self)
        self._stream_render_timer.setSingleShot(True)
        self._stream_render_timer.setInterval(int(_STREAM_RENDER_INTERVAL_S * 1000))
        self._stream_render_timer.timeout.connect(self._render_stream)
        # The transcript is read-only; an undo stack would only retain every
        # streamed revision in memory.
        self.document().setUndoRedoEnabled(False)
        # All finalized message HTML (persists across streaming cycles).
        # This is the "stable" portion that does not need re-rendering
        # on each streaming token — only the streaming bubble changes.
        self._html_content = ""
        self._render_messages = []

        self._batch_render = False
        # True while the empty-state guidance occupies the document. It is
        # never part of _html_content; see _render_placeholder.
        self._placeholder_showing = False

        # --- Smart auto-scroll state ---
        # Tracks whether the user has manually scrolled away from the
        # bottom during streaming. When True, auto-scroll is suppressed
        # so the user can read earlier content without being snapped back.
        self._user_scrolled_away = False

        # Guard flag to prevent the scrollbar valueChanged signal handler
        # from triggering when we programmatically scroll to bottom.
        # Without this, _on_scrollbar_moved would incorrectly detect our
        # own auto-scroll as a "user is at bottom" event during the wrong
        # phase of the update cycle.
        self._programmatic_scroll = False

        # --- Scroll-to-bottom button ---
        # Floating button overlaid on the bottom-right corner of the
        # display. Only visible when the user has scrolled away from
        # the bottom during or after streaming. Clicking it scrolls to
        # bottom and re-enables auto-scroll.
        self._scroll_btn = QToolButton(self)
        self._scroll_btn.setText("\u2193")  # Down arrow character
        self._scroll_btn.setToolTip("Scroll to bottom")
        self._scroll_btn.setFixedSize(QSize(32, 32))
        self._scroll_btn.hide()  # Hidden by default
        self._apply_scroll_button_style()
        self._scroll_btn.clicked.connect(self._on_scroll_to_bottom_clicked)

        # Connect to scrollbar changes to detect manual user scrolling.
        # This fires on both programmatic and user-initiated scrolls,
        # so we use the _programmatic_scroll guard to distinguish them.
        self.verticalScrollBar().valueChanged.connect(
            self._on_scrollbar_moved
        )

        # Initialize the document (empty chat)
        self.setHtml(self._html_content)
        self._render_placeholder()

    # --- Renderer-backed state ---
    # These keep their original names because the widget, the plugin and
    # the tests read *and write* them directly: a caller assigns
    # _pygments_style_dark or a whole new _theme, and a live theme edit
    # mutates the theme dict in place. Delegating rather than copying
    # means whatever is written here is what the next render uses.

    @property
    def _theme(self):
        """Resolved theme colors, shared by reference with the renderer."""
        return self._renderer.theme

    @_theme.setter
    def _theme(self, value):
        self._renderer.theme = value

    @property
    def _is_dark(self):
        """Whether the interface palette is dark."""
        return self._renderer.is_dark

    @_is_dark.setter
    def _is_dark(self, value):
        self._renderer.is_dark = value

    @property
    def _code_font_family(self):
        """Font family used for code blocks and inline code."""
        return self._renderer.code_font_family

    @_code_font_family.setter
    def _code_font_family(self, value):
        self._renderer.code_font_family = value

    @property
    def _code_font_size(self):
        """Point size used for code blocks and inline code."""
        return self._renderer.code_font_size

    @_code_font_size.setter
    def _code_font_size(self, value):
        self._renderer.code_font_size = value

    @property
    def _pygments_style_dark(self):
        """Pygments style used on dark code cards."""
        return self._renderer.pygments_style_dark

    @_pygments_style_dark.setter
    def _pygments_style_dark(self, value):
        self._renderer.pygments_style_dark = value

    @property
    def _pygments_style_light(self):
        """Pygments style used on light code cards."""
        return self._renderer.pygments_style_light

    @_pygments_style_light.setter
    def _pygments_style_light(self, value):
        self._renderer.pygments_style_light = value

    @property
    def _code_blocks(self):
        """Code extracted from assistant messages, in render order.

        The code-apply and copy links reference these by index.
        """
        return self._renderer.code_blocks

    @property
    def _highlight_cache(self):
        """The renderer's (language, style, code) -> HTML LRU cache."""
        return self._renderer.highlight_cache

    # --- Appearance configuration ---

    def update_appearance(self, **kwargs):
        """Update configurable appearance values and re-render.

        Accepts any subset of appearance keys. Only provided keys are
        updated; omitted keys keep their current value. After updating,
        the full chat HTML is rebuilt so changes are visible immediately.

        Supported keys:
            chat_font_family, chat_font_size, chat_line_height,
            code_font_family, code_font_size,
            pygments_style_dark, pygments_style_light,
            bubble_padding, bubble_border_radius, bubble_spacing,
            theme_preset, theme_color_overrides
        """
        # The code font and Pygments keys below are properties that write
        # straight through to the renderer, so the pipeline sees a setting
        # change with no extra bookkeeping. A style change cannot serve
        # stale colours either: the Pygments style is part of the highlight
        # cache key, so a restyled block misses the cache.
        attr_map = {
            "chat_font_family": "_font_family",
            "chat_font_size": "_font_size",
            "chat_line_height": "_line_height",
            "code_font_family": "_code_font_family",
            "code_font_size": "_code_font_size",
            "pygments_style_dark": "_pygments_style_dark",
            "pygments_style_light": "_pygments_style_light",
            "bubble_padding": "_bubble_padding",
            "bubble_border_radius": "_bubble_border_radius",
            "bubble_spacing": "_bubble_spacing",
        }
        changed = False
        for key, attr in attr_map.items():
            if key in kwargs and kwargs[key] != getattr(self, attr):
                setattr(self, attr, kwargs[key])
                changed = True

        # Handle theme preset and color overrides
        theme_changed = False
        if "is_dark" in kwargs and bool(kwargs["is_dark"]) != self._is_dark:
            self._is_dark = bool(kwargs["is_dark"])
            theme_changed = True
        if "theme_preset" in kwargs and kwargs["theme_preset"] != self._theme_preset:
            self._theme_preset = kwargs["theme_preset"]
            theme_changed = True
        if "theme_color_overrides" in kwargs:
            # Accept either a dict or a JSON string
            overrides = kwargs["theme_color_overrides"]
            if isinstance(overrides, str):
                overrides = parse_color_overrides(overrides)
            if overrides != self._theme_color_overrides:
                self._theme_color_overrides = overrides
                theme_changed = True
        if theme_changed:
            self._theme = get_theme_colors(
                self._theme_preset, self._is_dark, self._theme_color_overrides,
            )
            self._apply_scroll_button_style()
            changed = True

        if changed:
            self._full_rerender()

    def _full_rerender(self):
        """Rebuild the entire chat HTML from stored messages.

        Called after appearance settings change so the new fonts, sizes,
        and geometry are applied to all existing messages.
        """
        messages = list(self._render_messages)
        streaming, buffer = self._is_streaming, self._streaming_buffer
        position = self.verticalScrollBar().value()
        scrolled_away = self._user_scrolled_away
        self._batch_render = True
        try:
            self.clear_conversation()
            for role, text in messages:
                if role == "streamed":
                    self.start_assistant_message()
                    self._streaming_buffer = text
                    self.finish_assistant_message()
                else:
                    getattr(self, f"append_{role}")(text)
        finally:
            self._batch_render = False
        self._user_scrolled_away = scrolled_away
        if streaming:
            # The batched appends above deliberately skipped the document,
            # so load the rebuilt transcript before re-opening the frame.
            self._set_document_html(self._html_content)
            self.start_assistant_message()
            self.append_chunk(buffer)
            self._render_stream()
        else:
            self._set_document_html(self._html_content)
        if scrolled_away:
            self.verticalScrollBar().setValue(position)
        else:
            self._do_scroll_to_bottom()
        # Re-render the guidance in the new theme when there is no history.
        self._render_placeholder()

    def changeEvent(self, event):
        """Refresh existing bubbles when the application palette changes."""
        super().changeEvent(event)
        if event.type() != QEvent.PaletteChange or not hasattr(self, "_scroll_btn"):
            return
        is_dark = self.palette().color(self.backgroundRole()).lightness() < 128
        if is_dark == self._is_dark:
            return
        self._is_dark = is_dark
        self._theme = get_theme_colors(
            self._theme_preset, is_dark, self._theme_color_overrides,
        )
        self._apply_scroll_button_style()
        self._full_rerender()
        self.sig_theme_changed.emit()

    # --- Scroll-to-bottom button positioning and styling ---

    def _apply_scroll_button_style(self):
        """Apply theme-appropriate styling to the scroll-to-bottom button.

        Uses inline stylesheet since the button is a QWidget child, not
        HTML content. The button is semi-transparent to avoid obstructing
        chat content underneath.
        """
        t = self._theme
        self._scroll_btn.setStyleSheet(
            f"QToolButton {{"
            f"  background-color: {t['scroll_btn_bg']};"
            f"  color: {t['scroll_btn_text']};"
            f"  border: none;"
            f"  border-radius: 16px;"
            f"  font-size: 16px;"
            f"  font-weight: bold;"
            f"}}"
        )

    def resizeEvent(self, event):
        """Reposition the scroll-to-bottom button when the widget resizes.

        The button is always anchored to the bottom-right corner with
        a small margin, regardless of widget size.
        """
        super().resizeEvent(event)
        self._reposition_scroll_button()

    def _reposition_scroll_button(self):
        """Place the scroll-to-bottom button in the bottom-right corner.

        Accounts for the scrollbar width so the button doesn't overlap
        with the vertical scrollbar when it's visible.
        """
        margin = 12
        scrollbar_width = (
            self.verticalScrollBar().width()
            if self.verticalScrollBar().isVisible()
            else 0
        )
        x = self.width() - self._scroll_btn.width() - margin - scrollbar_width
        y = self.height() - self._scroll_btn.height() - margin
        self._scroll_btn.move(x, y)

    # --- Smart auto-scroll logic ---

    def _is_at_bottom(self):
        """Check if the scrollbar is at or near the bottom.

        Returns True if the current scroll position is within
        _SCROLL_BOTTOM_THRESHOLD_PX pixels of the maximum. This small
        tolerance accounts for sub-pixel rendering differences and
        makes the "at bottom" detection feel natural — the user doesn't
        need to be at the exact last pixel.
        """
        scrollbar = self.verticalScrollBar()
        # When the document fits within the viewport (no scrollbar needed),
        # maximum() is 0 and the user is trivially "at the bottom".
        if scrollbar.maximum() == 0:
            return True
        return scrollbar.value() >= scrollbar.maximum() - _SCROLL_BOTTOM_THRESHOLD_PX

    def _on_scrollbar_moved(self, value):
        """Handle scrollbar position changes to track user scroll intent.

        Called on every scrollbar value change (both programmatic and
        user-initiated). Uses the _programmatic_scroll guard to ignore
        changes caused by our own auto-scroll calls.

        When the user scrolls manually:
        - If they scrolled to the bottom: clear _user_scrolled_away,
          hide the scroll button, re-enable auto-scroll.
        - If they scrolled away from the bottom: set _user_scrolled_away,
          show the scroll button (if streaming is active).
        """
        # Ignore scrollbar changes caused by our own _scroll_to_bottom()
        # or setHtml() calls. These are not user actions.
        if self._programmatic_scroll:
            return

        if self._is_at_bottom():
            # User manually scrolled back to the bottom — re-enable
            # auto-scroll and hide the indicator button.
            self._user_scrolled_away = False
            self._scroll_btn.hide()
        else:
            # User scrolled away from the bottom — suppress auto-scroll
            # so they can read content without being interrupted.
            self._user_scrolled_away = True
            # Show the scroll button during streaming so the user
            # has a clear way to jump back to the latest content.
            # Also show it if there's content below the viewport
            # (even outside of streaming, for general convenience).
            self._scroll_btn.show()
            self._reposition_scroll_button()

    def _on_scroll_to_bottom_clicked(self):
        """Handle click on the floating "scroll to bottom" button.

        Scrolls to the bottom, re-enables auto-scroll, and hides the
        button. The user explicitly chose to see the latest content,
        so we resume auto-scrolling for subsequent streaming chunks.
        """
        self._user_scrolled_away = False
        self._scroll_btn.hide()
        self._do_scroll_to_bottom()

    def _ensure_layout_finished(self):
        """Complete any pending document layout without changing its width.

        ``QTextDocument.adjustSize()`` also forces layout, but it sets the
        document text width to its *ideal* width. Bubbles are 100%-width
        tables, so that ideal width exceeds the viewport and every later
        paint wraps at the wider size, producing a permanent horizontal
        scrollbar. Asking the layout for its size finishes the lazy layout
        while QTextEdit keeps the text width pinned to the viewport.
        """
        self.document().documentLayout().documentSize()

    def _do_scroll_to_bottom(self):
        """Perform the actual scroll-to-bottom operation.

        Forces the document layout to complete before reading the
        scrollbar maximum, because setHtml() triggers asynchronous
        layout and the maximum may not yet reflect the full document
        height. Without forcing layout, we might scroll to a stale
        maximum and end up short of the true bottom.

        Sets the programmatic scroll guard to prevent the scrollbar
        valueChanged handler from misinterpreting this as a user action.
        The guard is cleared via QTimer.singleShot(0) rather than
        synchronously, because Qt may deliver the valueChanged signal
        on the next event loop tick after setValue() returns — clearing
        the flag immediately would leave a timing window where the
        deferred signal sees _programmatic_scroll=False.
        """
        if self._batch_render:
            return
        self._programmatic_scroll = True
        # Force layout completion so scrollbar.maximum() is accurate.
        self._ensure_layout_finished()
        scrollbar = self.verticalScrollBar()
        scrollbar.setValue(scrollbar.maximum())
        QTimer.singleShot(0, self._clear_programmatic_scroll)

    def _scroll_to_bottom(self):
        """Conditionally scroll to bottom based on user scroll state.

        This is the main scroll entry point called after content updates.
        During streaming, it respects the user's scroll position:
        - If user was at or near the bottom -> auto-scroll to show new content
        - If user scrolled up -> do NOT scroll, show the scroll button instead

        For non-streaming updates (new user message, error, finalized
        assistant message), always scroll to bottom since the user
        initiated the action.
        """
        if self._is_streaming and self._user_scrolled_away:
            # User is reading earlier content — don't interrupt them.
            # Make sure the scroll button is visible so they can jump
            # back when ready.
            self._scroll_btn.show()
            self._reposition_scroll_button()
            return

        # Either not streaming (user action triggered this) or user
        # was already at the bottom — scroll to show latest content.
        self._do_scroll_to_bottom()

    # --- Message bubble HTML builders ---
    # All message containers use <table> cells with inline styles.
    # This is required because Qt's HTML renderer breaks block-level
    # elements (<pre>, nested <table>) out of <div> containers.
    # Tables-in-tables work correctly in Qt's renderer.

    def _wrap_message(self, bg, text_color, label, content,
                      label_color=None):
        """Wrap message content in a table-based bubble with inline styles.

        Uses a clean card-style design with:
        - Rounded corners and generous padding
        - Small colored role label above the content
        - Full-width layout (Qt HTML doesn't support max-width well)

        Args:
            bg: Background color hex string.
            text_color: Text color hex string.
            label: Role label (e.g., "You", "AI", "Error").
            content: Pre-rendered HTML content for the message body.
            label_color: Color for the role label. Defaults to text_color.

        Returns:
            HTML string for a complete message bubble.
        """
        lc = label_color or text_color
        # Use configurable appearance values (set via update_appearance)
        pad = self._bubble_padding
        sp = self._bubble_spacing
        ff = self._font_family
        fs = self._font_size
        lh = self._line_height
        br = self._bubble_border_radius
        # Label size is 80% of body font, capped between 7–12pt
        label_fs = max(7, min(12, int(round(fs * 0.8))))
        return (
            f'<table width="100%" cellpadding="{pad}" cellspacing="0"'
            f' style="margin-top:{sp}px; margin-bottom:{sp}px;">'
            f'<tr><td width="5" style="background-color:{lc};"></td>'
            f'<td style="background-color:{bg}; color:{text_color};'
            f' font-family:{ff}; font-size:{fs}pt;'
            f' border-radius:{br}px; line-height:{lh};">'
            f'<span style="font-size:{label_fs}pt; font-weight:bold;'
            f' color:{lc}; letter-spacing:0.5px;">'
            f'{label}</span><br>'
            f'{content}'
            f'</td></tr></table>'
        )

    def append_user_message(self, text):
        """Add a user message bubble to the display.

        Always scrolls to bottom and resets scroll-away state because
        the user just performed an action (sending a message), so they
        expect to see the conversation tip.

        Args:
            text: The user's message (plain text, will be HTML-escaped).
        """
        self._render_messages.append(("user_message", text))
        escaped = self._escape_html(text)
        # Preserve newlines in the user's message
        escaped = escaped.replace("\n", "<br>")
        bubble = self._wrap_message(
            self._theme["user_bg"], self._theme["user_text"],
            "YOU", escaped,
            label_color=self._theme["user_label"],
        )
        self._html_content += bubble
        self._append_document_html(bubble)
        # User just sent a message — always scroll to bottom regardless
        # of previous scroll position, and reset scroll-away state.
        self._user_scrolled_away = False
        self._scroll_btn.hide()
        # Force the document layout to complete before scrolling, so the
        # scrollbar maximum already reflects the whole document.
        self._ensure_layout_finished()
        self._do_scroll_to_bottom()

    def append_assistant_message(self, text):
        """Add a finalized assistant message to the display."""
        self._render_messages.append(("assistant_message", text))
        rendered = self._render_markdown(text or "", track_code_blocks=True)
        bubble = self._wrap_message(
            self._theme["assistant_bg"], self._theme["assistant_text"],
            "AI", rendered,
            label_color=self._theme["assistant_label"],
        )
        self._html_content += bubble
        self._append_document_html(bubble)
        self._scroll_to_bottom()

    def start_assistant_message(self):
        """Begin a new assistant response. Call before streaming chunks.

        Resets the streaming buffer, enables streaming mode and opens the
        streaming frame at the end of the stable transcript. The
        scroll-away state is NOT reset here — if the user was reading
        earlier content, they should continue undisturbed.
        """
        self._streaming_buffer = ""
        self._is_streaming = True
        self._stream_dirty = False
        if not self._batch_render:
            self._open_stream_frame()

    def append_chunk(self, text):
        """Append a streaming token to the current assistant response.

        Accumulates text in a buffer and schedules a re-render of the
        streaming frame. Renders happen at most every
        ``_STREAM_RENDER_INTERVAL_S``; a burst of tokens is drawn once.

        Args:
            text: The next token from the LLM.
        """
        if not self._is_streaming:
            return

        self._streaming_buffer += text
        self._stream_dirty = True
        if self._batch_render:
            return

        elapsed = time.monotonic() - self._last_stream_render
        if elapsed >= _STREAM_RENDER_INTERVAL_S:
            self._render_stream()
        elif not self._stream_render_timer.isActive():
            self._stream_render_timer.start()

    def _streaming_html(self):
        """Render the streaming buffer (thinking block + response bubble).

        Detects <think>...</think> blocks and renders them separately from
        the main response in a dimmed style. Runs on every render because
        <think> tags can arrive split across tokens.
        """
        thinking, response, thinking_done = self._parse_thinking(
            self._streaming_buffer
        )
        html = ""
        if thinking:
            label = "Thinking..." if not thinking_done else "Thought"
            html += self._wrap_thinking(thinking, label)
        if response or not thinking:
            # Response bubble, or an empty AI bubble while nothing arrived.
            rendered = self._render_markdown(response) if response else ""
            html += self._wrap_message(
                self._theme["assistant_bg"], self._theme["assistant_text"],
                "AI", rendered,
                label_color=self._theme["assistant_label"],
            )
        return html

    def _open_stream_frame(self):
        """Append an empty frame at the end to hold the live response.

        The document already contains the finished transcript, because each
        finished message is appended to it, so there is nothing to reload
        here. Reloading used to happen once per turn, which put the whole
        O(n) transcript cost back on every response.
        """
        if self._batch_render:
            return
        # A first response streams into an otherwise empty transcript.
        self._clear_placeholder()
        self._programmatic_scroll = True
        cursor = QTextCursor(self.document())
        cursor.movePosition(QTextCursor.End)
        frame_format = QTextFrameFormat()
        frame_format.setBorder(0)
        frame_format.setMargin(0)
        frame_format.setPadding(0)
        self._stream_frame = cursor.insertFrame(frame_format)
        QTimer.singleShot(0, self._clear_programmatic_scroll)

    def _reload_and_open_stream_frame(self):
        """Reload the transcript, then re-open the live frame below it.

        Needed only when something must appear *above* the live response:
        rebuilding from ``_html_content`` is the simplest correct way to
        reorder the document.
        """
        self._set_document_html(self._html_content)
        self._open_stream_frame()

    def _render_stream(self):
        """Replace the streaming frame with the current buffer's HTML."""
        self._stream_render_timer.stop()
        if not self._is_streaming or self._batch_render:
            return
        self._stream_dirty = False
        self._last_stream_render = time.monotonic()
        if self._stream_frame is None:
            self._open_stream_frame()
        cursor = self._stream_frame.firstCursorPosition()
        cursor.setPosition(
            self._stream_frame.lastCursorPosition().position(),
            QTextCursor.KeepAnchor,
        )
        cursor.beginEditBlock()
        cursor.removeSelectedText()
        cursor.insertHtml(self._streaming_html())
        cursor.endEditBlock()
        # Smart scroll: only auto-scroll if user is at/near bottom
        self._scroll_to_bottom()

    def finish_assistant_message(self):
        """Finalize the current assistant response.

        Commits the streaming buffer to the permanent HTML content
        and resets the streaming state. Thinking blocks are preserved
        in the final output. Code blocks are tracked and "Insert into
        editor" links are added.

        Does NOT force-scroll to bottom — the user stays where they
        are. The scroll-away state is reset so that the next streaming
        session starts with auto-scroll enabled (unless the user scrolls
        up again before it starts).
        """
        if not self._is_streaming:
            return

        if not self._streaming_buffer.strip():
            self.discard_assistant_message()
            return

        self._stream_render_timer.stop()
        self._render_messages.append(("streamed", self._streaming_buffer))
        # Parse thinking vs response for the final version
        thinking, response, _ = self._parse_thinking(self._streaming_buffer)

        # Build the finished HTML once: it is both appended to the
        # authoritative transcript and put into the document below.
        finalized_html = ""
        if thinking:
            finalized_html += self._wrap_thinking(thinking, "Thought")

        # Render the response with code block tracking enabled.
        # This stores code blocks in self._code_blocks and adds
        # code-apply links below each code block.
        # If no response (model only produced thinking), show empty.
        response_text = response or ""
        rendered = self._render_markdown(
            response_text, track_code_blocks=True
        )
        finalized_html += self._wrap_message(
            self._theme["assistant_bg"], self._theme["assistant_text"],
            "AI", rendered,
            label_color=self._theme["assistant_label"],
        )
        self._html_content += finalized_html

        # Set _is_streaming = False BEFORE calling _set_document_html,
        # because setHtml() may trigger a synchronous or deferred
        # valueChanged signal. If _is_streaming were still True at that
        # point, the scroll handler would misinterpret the HTML reset
        # as happening during streaming and incorrectly set
        # _user_scrolled_away = True.
        self._is_streaming = False
        self._streaming_buffer = ""

        # Reset scroll-away state unconditionally. The previous
        # streaming session's scroll state should not carry over to the
        # next session. If the user is currently scrolled away, the
        # scroll button will be shown based on the _is_at_bottom check
        # below, but the _user_scrolled_away flag starts fresh.
        self._user_scrolled_away = False

        self._commit_stream_frame(finalized_html)

        # After rendering, check if the user is at the bottom.
        # If not, show the scroll button so they can jump back.
        # If yes, hide it — everything is visible.
        if self._is_at_bottom():
            self._scroll_btn.hide()
        else:
            self._scroll_btn.show()
            self._reposition_scroll_button()

    def discard_assistant_message(self):
        """Drop the current streaming assistant message without saving it."""
        if not self._is_streaming:
            return

        self._stream_render_timer.stop()
        self._streaming_buffer = ""
        self._is_streaming = False
        self._set_document_html(self._html_content)
        self._scroll_to_bottom()

    def append_error(self, message):
        """Display an error message in the chat.

        Args:
            message: The error description (will be HTML-escaped).
        """
        self._append_notice("error", message)

    def append_warning(self, message):
        """Show a nonfatal operational warning."""
        self._append_notice("warning", message)

    def append_info(self, message):
        """Show an informational notice without error styling."""
        self._append_notice("info", message)

    def _append_notice(self, level, message):
        """Render all notice levels with the same escaping and layout."""
        self._render_messages.append((level, message))
        bubble = self._wrap_message(
            self._theme[f"{level}_bg"], self._theme[f"{level}_text"],
            level.upper(), self._escape_html(message),
            label_color=self._theme[f"{level}_label"],
        )
        self._html_content += bubble
        if self._batch_render:
            return
        if self._is_streaming:
            # The notice belongs before the streaming bubble, so this is
            # the one path that still reorders by reloading.
            self._reload_and_open_stream_frame()
            self._render_stream()
        else:
            self._append_document_html(bubble)
            self._scroll_to_bottom()

    def clear_conversation(self):
        """Remove all messages and reset the display to empty."""
        self._stream_render_timer.stop()
        self._html_content = ""
        self._streaming_buffer = ""
        self._is_streaming = False
        self._renderer.clear_code_blocks()
        self._render_messages.clear()
        self._renderer.clear_highlight_cache()
        # Reset scroll state on clear — fresh conversation
        self._user_scrolled_away = False
        self._scroll_btn.hide()
        self._set_document_html(self._html_content)
        # An emptied tab is a fresh tab: offer the guidance again.
        self._render_placeholder()

    def rebuild_from_messages(self, messages):
        """Re-render the full conversation from authoritative history."""
        # Restoring a long history should lay out the document once, instead
        # of laying out every growing prefix of the transcript.
        self._batch_render = True
        try:
            self.clear_conversation()
            for message in messages:
                role = message.get("role")
                content = message.get("content", "")
                if role == "user":
                    self.append_user_message(content)
                elif role == "assistant":
                    self.append_assistant_message(content)
        finally:
            self._batch_render = False
        self._set_document_html(self._html_content)
        self._do_scroll_to_bottom()

    # --- HTML document update ---

    def _set_document_html(self, html):
        """Set the document HTML with programmatic scroll guard.

        Wraps setHtml() with the _programmatic_scroll flag so that the
        scrollbar valueChanged handler does not misinterpret the scroll
        position reset that setHtml() causes as a user action.

        setHtml() resets the scroll position to the top, which would
        fire valueChanged and incorrectly set _user_scrolled_away=True
        (since the top is not at the bottom). The guard prevents this.

        Args:
            html: The full HTML content to render in the document.
        """
        if self._batch_render:
            return
        # setHtml replaces the whole document, so any streaming frame
        # created earlier no longer exists, and so does any placeholder.
        self._stream_frame = None
        self._placeholder_showing = False
        self._stream_render_timer.stop()
        position = self.verticalScrollBar().value()
        self._programmatic_scroll = True
        self.setHtml(html)
        self.verticalScrollBar().setValue(position)
        # Clear the guard via QTimer.singleShot(0) to ensure it
        # persists through any deferred valueChanged signals that Qt
        # delivers on the next event loop tick after setHtml().
        QTimer.singleShot(0, self._clear_programmatic_scroll)

    def _placeholder_html(self):
        """Return the empty-state guidance plus one link per starter."""
        # Body text, not the dimmed "thinking" colour: measured against the
        # presets, thinking_text falls to 1.69:1 on nord dark and 2.18:1 on
        # solarized light, so guidance drawn in it would be close to
        # invisible in exactly the themes a new user might be running.
        body = self._theme["assistant_text"]
        link_color = self._theme["link_color"]
        links = "".join(
            f'<div style="margin:3px 0;">'
            f'<a href="starter://{index}" style="color:{link_color};'
            f' font-size:{self._font_size}pt; text-decoration:underline;">'
            f'{self._escape_html(label)}</a></div>'
            for index, (label, _prompt) in enumerate(_STARTER_ACTIONS)
        )
        return (
            f'<table width="100%" cellpadding="{self._bubble_padding}"'
            f' cellspacing="0"><tr><td style="color:{body};'
            f' font-family:{self._font_family};'
            f' font-size:{self._font_size}pt;'
            f' line-height:{self._line_height};">'
            "Ask about the file you have open, an error in your console, or a "
            "variable you are looking at. The assistant reads your editor and "
            "consoles only when a question needs them."
            f'<div style="margin-top:10px;">{links}</div>'
            "</td></tr></table>"
        )

    def _render_placeholder(self):
        """Show the empty-state guidance while the transcript has no messages.

        Written straight to the document and deliberately *not* into
        ``_html_content``. That string is the authoritative transcript: a
        theme change re-renders every bubble from it and the session layer
        persists the conversation, so guidance text must never join it.
        """
        if self._batch_render or self._html_content or self._is_streaming:
            return
        self._placeholder_showing = True
        self._programmatic_scroll = True
        self.setHtml(self._placeholder_html())
        QTimer.singleShot(0, self._clear_programmatic_scroll)

    def _clear_placeholder(self):
        """Drop the guidance before the first real content is appended.

        Clears the document directly rather than going through ``setHtml``:
        there is no HTML to parse, and appending a message must not reload
        the document even once, which is the property the transcript's
        append path is built on.
        """
        if not self._placeholder_showing:
            return
        self._placeholder_showing = False
        self._programmatic_scroll = True
        self.document().clear()
        QTimer.singleShot(0, self._clear_programmatic_scroll)

    def _append_document_html(self, html):
        """Append one finished bubble without reloading the document.

        ``setHtml`` re-lays out the entire transcript, so appending message
        n cost O(n): measured over 150 exchanges, the first appends took
        6.5 ms each and the last took 101 ms each, 7.9 s in total. Building
        the HTML string is flat and nearly free (0.05 s for the same 150),
        so the reload was the whole cost. Inserting at the end keeps it
        flat.

        ``_html_content`` stays the authoritative copy of the transcript,
        because a font or theme change re-renders every bubble from it.
        """
        if self._batch_render:
            return
        # The guidance is document-only, so it has to go before content.
        self._clear_placeholder()
        # insertHtml moves the scrollbar; the guard keeps
        # _on_scrollbar_moved from reading that as the user scrolling.
        self._programmatic_scroll = True
        cursor = QTextCursor(self.document())
        cursor.movePosition(QTextCursor.End)
        cursor.insertHtml(html)
        QTimer.singleShot(0, self._clear_programmatic_scroll)

    def _commit_stream_frame(self, html):
        """Put the finished bubble in place of the live one.

        The streaming frame already sits at the end of the document, so the
        finished message replaces its contents and the turn ends without
        reloading the transcript.
        """
        if self._batch_render:
            return
        if self._stream_frame is None:
            self._append_document_html(html)
            return
        self._programmatic_scroll = True
        cursor = self._stream_frame.firstCursorPosition()
        cursor.setPosition(
            self._stream_frame.lastCursorPosition().position(),
            QTextCursor.KeepAnchor,
        )
        cursor.beginEditBlock()
        cursor.removeSelectedText()
        cursor.insertHtml(html)
        cursor.endEditBlock()
        self._stream_frame = None
        QTimer.singleShot(0, self._clear_programmatic_scroll)

    def _clear_programmatic_scroll(self):
        """Reset the programmatic scroll guard after event loop tick.

        Called via QTimer.singleShot(0) to ensure the guard persists
        through any deferred scrollbar valueChanged signals that Qt
        may deliver after setHtml() or setValue() returns.
        """
        self._programmatic_scroll = False

    # --- Thinking/reasoning parsing ---

    def _parse_thinking(self, text):
        """Split text into thinking and response portions.

        Detects <think>...</think> blocks emitted by reasoning models
        (Qwen3, DeepSeek, etc.). During streaming, the closing tag may
        not have arrived yet, so we handle both complete and incomplete
        thinking blocks.

        Args:
            text: The accumulated streaming buffer.

        Returns:
            Tuple of (thinking_text, response_text, thinking_complete):
            - thinking_text: Content inside <think>...</think>, or "" if none.
            - response_text: Content after </think>, or the full text if
              no thinking block was detected.
            - thinking_complete: True if </think> was found (thinking is done).
        """
        # Check if the text starts with a <think> tag (possibly with
        # leading whitespace/newlines that some models emit)
        stripped = text.lstrip()

        if not stripped.startswith("<think>"):
            # No thinking block — everything is response
            return "", text, False

        # Find where the thinking content starts (after <think>)
        think_start = text.index("<think>") + len("<think>")

        # Look for the closing </think> tag
        think_end = text.find("</think>", think_start)

        if think_end == -1:
            # Thinking is still in progress — no closing tag yet.
            # Everything after <think> is thinking content, no response yet.
            thinking_text = text[think_start:]
            return thinking_text, "", False

        # Thinking is complete — split into thinking and response
        thinking_text = text[think_start:think_end]
        # Response starts after </think> and any trailing whitespace/newlines
        response_text = text[think_end + len("</think>"):].lstrip("\n")
        return thinking_text, response_text, True

    def _wrap_thinking(self, thinking_text, label="Thinking..."):
        """Render a thinking block as a dimmed, styled HTML section.

        Uses a table cell with a left border to visually distinguish
        reasoning from the final answer (similar to Cursor's thinking UI).

        Args:
            thinking_text: The raw thinking content from the model.
            label: Header label ("Thinking..." during streaming, "Thought" when done).

        Returns:
            HTML string for the thinking block.
        """
        t = self._theme
        # Escape and render with basic markdown (thinking can contain code)
        escaped_thinking = self._render_markdown(thinking_text)

        # Thinking block font is slightly smaller than the main body
        think_fs = max(7, self._font_size - 1)
        think_label_fs = max(7, think_fs - 1)
        return (
            f'<table width="100%" cellpadding="8" cellspacing="0"'
            f' style="margin-top:6px; margin-bottom:2px;">'
            f'<tr><td style="background-color:{t["thinking_bg"]};'
            f' color:{t["thinking_text"]};'
            f' font-family:{self._font_family}; font-size:{think_fs}pt;'
            f' font-style:italic;'
            f' border-left:3px solid {t["thinking_border"]};'
            f' border-radius:4px;">'
            f'<b style="font-style:normal; font-size:{think_label_fs}pt;">'
            f'{label}</b><br>'
            f'{escaped_thinking}'
            f'</td></tr></table>'
        )

    # --- Markdown pipeline (delegated to MarkdownRenderer) ---
    # The pipeline lives in utils/markdown_render.py so that a second
    # transcript view can render identical HTML without a QTextEdit. The
    # methods below stay on the widget under their original names because
    # the widget's own rendering paths and its tests call them.

    def _render_markdown(self, text, track_code_blocks=False):
        """Convert markdown to HTML; see ``MarkdownRenderer.render``."""
        return self._renderer.render(text, track_code_blocks=track_code_blocks)

    def _highlight_code(self, code, language="", *, cache=True):
        """Highlight one snippet; see ``MarkdownRenderer.highlight_code``."""
        return self._renderer.highlight_code(code, language, cache=cache)

    def _code_block_html(self, lang, escaped_code, highlighted):
        """Return the styled ``<pre>`` block for one fenced code snippet."""
        return self._renderer.code_block_html(lang, escaped_code, highlighted)

    def _apply_inline_formatting(self, text):
        """Apply bold, italic, strikethrough and bare-URL formatting."""
        return self._renderer.apply_inline_formatting(text)

    def _code_card_is_dark(self):
        """Return True when the theme's code block background is dark."""
        return self._renderer.code_card_is_dark()

    @staticmethod
    def _trim_before_block(output_lines):
        """Drop the line breaks just above an opening block element."""
        return trim_before_block(output_lines)

    @staticmethod
    def _close_list_stack(indent_stack):
        """Return closing tags for all open list nesting levels."""
        return close_list_stack(indent_stack)

    @staticmethod
    def _is_list_item(stripped_line):
        """Check if a stripped line is a list item (ordered or unordered)."""
        return is_list_item(stripped_line)

    @staticmethod
    def _is_table_row(stripped_line):
        """Check if a stripped line looks like a GFM pipe table row."""
        return is_table_row(stripped_line)

    @staticmethod
    def _is_table_separator(stripped_line):
        """Check if a line is a GFM table separator row."""
        return is_table_separator(stripped_line)

    @staticmethod
    def _parse_table_cells(stripped_line):
        """Extract cell contents from a GFM pipe table row."""
        return parse_table_cells(stripped_line)

    @staticmethod
    def _escape_html(text):
        """Escape HTML special characters to prevent rendering issues."""
        return escape_html(text)

    @staticmethod
    def _unescape_html(text):
        """Reverse HTML escaping to recover the original text."""
        return unescape_html(text)

    def keyPressEvent(self, event):
        """Activate the focused link from the keyboard.

        The transcript's actions live in HTML anchors, which Qt reaches
        with Tab once LinksAccessibleByKeyboard is set, but which it never
        *activates* on its own: QTextEdit has no anchorClicked. Without
        this, Copy and Apply were mouse-only.
        """
        if event.key() in (Qt.Key_Return, Qt.Key_Enter, Qt.Key_Space):
            anchor = self.textCursor().charFormat().anchorHref()
            if anchor and self._activate_anchor(anchor):
                event.accept()
                return
        super().keyPressEvent(event)

    def mousePressEvent(self, event):
        """Handle clicks on the transcript's action links."""
        anchor = self.anchorAt(event.pos())
        if not anchor or not self._activate_anchor(anchor):
            super().mousePressEvent(event)

    def last_code_block(self):
        """Return the most recent tracked code block, or "" when there is none."""
        return self._code_blocks[-1] if self._code_blocks else ""

    def copy_last_code_block(self):
        """Copy the most recent code block; return whether one existed.

        Kept here rather than in the widget so every clipboard write for
        the transcript goes through one place.
        """
        code = self.last_code_block()
        if not code:
            return False
        QApplication.clipboard().setText(code)
        return True

    def _activate_anchor(self, anchor):
        """Run the action behind one anchor; return whether it was handled.

        Shared by the mouse and the keyboard so both routes cannot drift
        apart:
        - starter://<index> -> emit sig_starter_action with its prompt
        - apply://<index>   -> emit sig_apply_code_requested
        - copy://<index>    -> copy the code to the system clipboard
        """
        # Starter links carry a prompt for the input, not a code block.
        if anchor.startswith("starter://"):
            try:
                starter_index = int(anchor[len("starter://"):])
            except ValueError:
                return True
            if 0 <= starter_index < len(_STARTER_ACTIONS):
                self.sig_starter_action.emit(_STARTER_ACTIONS[starter_index][1])
            return True

        # Parse the action and code block index from the URL
        if anchor.startswith("apply://"):
            prefix = "apply://"
        elif anchor.startswith("copy://"):
            prefix = "copy://"
        else:
            # Not one of ours: let the caller fall through to Qt, which
            # handles ordinary links and text selection.
            return False

        # A malformed or stale index is still one of our anchors, so it
        # counts as handled: doing nothing is the intended outcome, and
        # reporting otherwise would make a click select text underneath
        # and a keypress reach the base class.
        try:
            index = int(anchor[len(prefix):])
            if not (0 <= index < len(self._code_blocks)):
                return True
        except (ValueError, IndexError):
            return True

        code = self._code_blocks[index]

        if prefix == "apply://":
            self.sig_apply_code_requested.emit(code)
        elif prefix == "copy://":
            # Copy code to the system clipboard
            clipboard = QApplication.clipboard()
            clipboard.setText(code)
        return True
