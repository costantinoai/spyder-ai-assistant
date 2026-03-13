"""Chat display widget for rendering conversation messages.

Renders user messages and assistant responses in a scrollable QTextEdit.
Supports real-time streaming (tokens appended as they arrive) and basic
markdown formatting including fenced code blocks and inline code.

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
import re

from qtpy.QtCore import Qt, QTimer, Signal, QSize
from qtpy.QtWidgets import QApplication, QTextEdit, QToolButton

from spyder_ai_assistant.utils.chat_themes import (
    get_theme_colors,
    parse_color_overrides,
)

logger = logging.getLogger(__name__)

# Pixel threshold for "near bottom" detection. If the scrollbar is
# within this many pixels of the maximum, we consider the user to
# be "at the bottom" and auto-scroll will continue during streaming.
_SCROLL_BOTTOM_THRESHOLD_PX = 30


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

    # Theme color presets for light and dark Spyder themes.
    # Selected at runtime based on the widget's background luminance.
    # Theme color presets — designed for clear visual distinction between
    # user messages (accent color, right-aligned feel) and AI responses
    # (neutral, left-aligned). Colors are inspired by modern chat UIs
    # (Slack, Discord, Cursor) while respecting Spyder's theme.
    _LIGHT_THEME = {
        "user_bg": "#d4e6f9", "user_text": "#1a1a1a",
        "user_label": "#2962a1",
        "assistant_bg": "#f0f0f0", "assistant_text": "#1a1a1a",
        "assistant_label": "#4a4a4a",
        "error_bg": "#fde8e8", "error_text": "#b71c1c",
        "error_label": "#c62828",
        "code_block_bg": "#282c34", "code_block_text": "#abb2bf",
        "inline_code_bg": "#e8e8e8", "inline_code_text": "#1a1a1a",
        "link_color": "#1565c0",
        # Thinking blocks: dimmed, italic, with a subtle left border
        "thinking_bg": "#f5f5f5", "thinking_text": "#888888",
        "thinking_border": "#cccccc",
        # Scroll-to-bottom button styling
        "scroll_btn_bg": "rgba(0, 0, 0, 150)",
        "scroll_btn_text": "#ffffff",
        # Blockquote: subtle left border and light background
        "blockquote_bg": "#f5f5f0", "blockquote_border": "#c0c0c0",
        "blockquote_text": "#555555",
        # Table: light grid lines for data tables
        "table_border": "#cccccc", "table_header_bg": "#e0e0e0",
        # Horizontal rule
        "hr_color": "#cccccc",
    }
    _DARK_THEME = {
        "user_bg": "#1e3a5f", "user_text": "#e8e8e8",
        "user_label": "#7ab3ef",
        "assistant_bg": "#252525", "assistant_text": "#e0e0e0",
        "assistant_label": "#999999",
        "error_bg": "#3d1515", "error_text": "#ff8a80",
        "error_label": "#ff6b6b",
        "code_block_bg": "#1a1d23", "code_block_text": "#abb2bf",
        "inline_code_bg": "#383838", "inline_code_text": "#e0e0e0",
        "link_color": "#64b5f6",
        # Thinking blocks: dimmed, italic, with a subtle left border
        "thinking_bg": "#1a1a1a", "thinking_text": "#777777",
        "thinking_border": "#444444",
        # Scroll-to-bottom button styling
        "scroll_btn_bg": "rgba(255, 255, 255, 150)",
        "scroll_btn_text": "#000000",
        # Blockquote: subtle left border on dark background
        "blockquote_bg": "#2a2a2a", "blockquote_border": "#555555",
        "blockquote_text": "#aaaaaa",
        # Table: darker grid lines for data tables
        "table_border": "#444444", "table_header_bg": "#333333",
        # Horizontal rule
        "hr_color": "#555555",
    }

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setReadOnly(True)
        self.setAcceptRichText(True)

        # Detect dark vs light theme from the widget's background color.
        # If the background luminance is below 128, we're on a dark theme.
        bg = self.palette().color(self.backgroundRole())
        is_dark = bg.lightness() < 128
        self._is_dark = is_dark

        # Theme state: preset name + per-color overrides.
        # Resolved into self._theme via get_theme_colors().
        self._theme_preset = "default"
        self._theme_color_overrides = {}
        self._theme = get_theme_colors("default", is_dark)

        # Configurable appearance values — initialized to defaults that
        # match the previously-hardcoded values. Updated at runtime via
        # update_appearance() when the user changes settings.
        self._font_family = "sans-serif"
        self._font_size = 10        # pt
        self._line_height = 1.5
        self._code_font_family = "Courier New"
        self._code_font_size = 9    # pt
        self._pygments_style_dark = "monokai"
        self._pygments_style_light = "default"
        self._bubble_padding = 12   # px (used as cellpadding)
        self._bubble_border_radius = 8  # px
        self._bubble_spacing = 4    # px (vertical margin between bubbles)

        # Buffer for accumulating streaming tokens from the LLM
        self._streaming_buffer = ""
        # Whether an assistant response is currently being streamed
        self._is_streaming = False
        # All finalized message HTML (persists across streaming cycles).
        # This is the "stable" portion that does not need re-rendering
        # on each streaming token — only the streaming bubble changes.
        self._html_content = ""

        # Extracted code blocks from assistant messages, indexed by
        # position. Used to look up code when user clicks code-apply
        # links (which reference blocks by index).
        self._code_blocks = []

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
        # Re-render is done by reconstructing _html_content from the
        # message list maintained by ChatSession. The session calls
        # reload_messages() which clears and re-appends each message.
        # We emit a signal so the owning session can drive the reload.
        # For now, just refresh the current HTML (layout values are
        # read live from instance attributes, so a setHtml re-render
        # with the same content picks up the new values).
        if self._is_streaming:
            # During streaming, the next token append will use the new
            # values. Don't interrupt the stream.
            return
        # Re-set the same HTML to force Qt to re-render with new styles.
        # This works because _wrap_message reads from instance attributes.
        self.setHtml(self._html_content)

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

    def _do_scroll_to_bottom(self):
        """Perform the actual scroll-to-bottom operation.

        Forces the document layout to complete before reading the
        scrollbar maximum, because setHtml() triggers asynchronous
        layout and the maximum may not yet reflect the full document
        height. Without adjustSize(), we might scroll to a stale
        maximum and end up short of the true bottom.

        Sets the programmatic scroll guard to prevent the scrollbar
        valueChanged handler from misinterpreting this as a user action.
        The guard is cleared via QTimer.singleShot(0) rather than
        synchronously, because Qt may deliver the valueChanged signal
        on the next event loop tick after setValue() returns — clearing
        the flag immediately would leave a timing window where the
        deferred signal sees _programmatic_scroll=False.
        """
        self._programmatic_scroll = True
        # Force layout completion so scrollbar.maximum() is accurate.
        self.document().adjustSize()
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
            f'<tr><td style="background-color:{bg}; color:{text_color};'
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
        escaped = self._escape_html(text)
        # Preserve newlines in the user's message
        escaped = escaped.replace("\n", "<br>")
        self._html_content += self._wrap_message(
            self._theme["user_bg"], self._theme["user_text"],
            "YOU", escaped,
            label_color=self._theme["user_label"],
        )
        self._set_document_html(self._html_content)
        # User just sent a message — always scroll to bottom regardless
        # of previous scroll position, and reset scroll-away state.
        self._user_scrolled_away = False
        self._scroll_btn.hide()
        # Force the document layout to complete before scrolling.
        # Without this, setHtml() triggers asynchronous layout and
        # the scrollbar maximum may increase after we set the value,
        # leaving us slightly above the true bottom.
        self.document().adjustSize()
        self._do_scroll_to_bottom()

    def append_assistant_message(self, text):
        """Add a finalized assistant message to the display."""
        rendered = self._render_markdown(text or "", track_code_blocks=True)
        self._html_content += self._wrap_message(
            self._theme["assistant_bg"], self._theme["assistant_text"],
            "AI", rendered,
            label_color=self._theme["assistant_label"],
        )
        self._set_document_html(self._html_content)
        self._scroll_to_bottom()

    def start_assistant_message(self):
        """Begin a new assistant response. Call before streaming chunks.

        Resets the streaming buffer and enables streaming mode. The
        scroll-away state is NOT reset here — if the user was reading
        earlier content, they should continue undisturbed.
        """
        self._streaming_buffer = ""
        self._is_streaming = True

    def append_chunk(self, text):
        """Append a streaming token to the current assistant response.

        Accumulates text in a buffer and re-renders the current message
        on each chunk. Detects <think>...</think> blocks and renders them
        separately from the main response in a dimmed style.

        Rendering optimization: Only the streaming message's markdown is
        re-rendered on each chunk. The completed messages in _html_content
        are pre-built HTML that is simply concatenated — no re-processing
        of earlier messages occurs. This keeps per-chunk cost proportional
        to the streaming buffer size, not the total conversation length.

        Args:
            text: The next token from the LLM.
        """
        if not self._is_streaming:
            return

        self._streaming_buffer += text

        # Parse the buffer to separate thinking from response content.
        # This runs on every chunk because <think> tags can arrive
        # split across multiple tokens.
        thinking, response, thinking_done = self._parse_thinking(
            self._streaming_buffer
        )

        # Build the streaming HTML: thinking block (if any) + response.
        # Only this portion is re-rendered each chunk; _html_content
        # is stable pre-built HTML from completed messages.
        streaming_html = ""

        if thinking:
            # Render thinking in a dimmed block with "Thinking..." label
            label = "Thinking..." if not thinking_done else "Thought"
            streaming_html += self._wrap_thinking(thinking, label)

        if response:
            # Render the main response normally
            rendered = self._render_markdown(response)
            streaming_html += self._wrap_message(
                self._theme["assistant_bg"], self._theme["assistant_text"],
                "AI", rendered,
                label_color=self._theme["assistant_label"],
            )
        elif not thinking:
            # No thinking and no response yet — show empty AI bubble
            streaming_html += self._wrap_message(
                self._theme["assistant_bg"], self._theme["assistant_text"],
                "AI", "",
                label_color=self._theme["assistant_label"],
            )

        # Combine stable completed HTML with the streaming portion.
        # _html_content is pre-built and doesn't need re-rendering.
        self._set_document_html(self._html_content + streaming_html)
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

        # Parse thinking vs response for the final version
        thinking, response, _ = self._parse_thinking(self._streaming_buffer)

        # Add thinking block to permanent HTML (if present)
        if thinking:
            self._html_content += self._wrap_thinking(thinking, "Thought")

        # Render the response with code block tracking enabled.
        # This stores code blocks in self._code_blocks and adds
        # code-apply links below each code block.
        # If no response (model only produced thinking), show empty.
        response_text = response or ""
        rendered = self._render_markdown(
            response_text, track_code_blocks=True
        )
        self._html_content += self._wrap_message(
            self._theme["assistant_bg"], self._theme["assistant_text"],
            "AI", rendered,
            label_color=self._theme["assistant_label"],
        )

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

        self._set_document_html(self._html_content)

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

        self._streaming_buffer = ""
        self._is_streaming = False
        self._set_document_html(self._html_content)
        self._scroll_to_bottom()

    def append_error(self, message):
        """Display an error message in the chat.

        Args:
            message: The error description (will be HTML-escaped).
        """
        escaped = self._escape_html(message)
        self._html_content += self._wrap_message(
            self._theme["error_bg"], self._theme["error_text"],
            "ERROR", escaped,
            label_color=self._theme["error_label"],
        )
        self._set_document_html(self._html_content)
        self._scroll_to_bottom()

    def clear_conversation(self):
        """Remove all messages and reset the display to empty."""
        self._html_content = ""
        self._streaming_buffer = ""
        self._is_streaming = False
        self._code_blocks.clear()
        # Reset scroll state on clear — fresh conversation
        self._user_scrolled_away = False
        self._scroll_btn.hide()
        self._set_document_html(self._html_content)

    def rebuild_from_messages(self, messages):
        """Re-render the full conversation from authoritative history."""
        self.clear_conversation()
        for message in messages:
            role = message.get("role")
            content = message.get("content", "")
            if role == "user":
                self.append_user_message(content)
            elif role == "assistant":
                self.append_assistant_message(content)

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
        self._programmatic_scroll = True
        self.setHtml(html)
        # Clear the guard via QTimer.singleShot(0) to ensure it
        # persists through any deferred valueChanged signals that Qt
        # delivers on the next event loop tick after setHtml().
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

    # --- Private rendering helpers ---

    def _render_markdown(self, text, track_code_blocks=False):
        """Convert markdown to HTML for chat display.

        Supports a comprehensive set of markdown elements commonly
        produced by LLMs:
        - Fenced code blocks (``` with optional language) via Pygments
        - Inline code (`code`)
        - Bold (**text**), italic (*text* / _text_), strikethrough (~~text~~)
        - Headings (# H1 through #### H4)
        - Unordered lists (-, *, +) with nesting
        - Ordered lists (1., 2., etc.) with nesting
        - Blockquotes (> text)
        - Horizontal rules (---, ***, ___)
        - GFM pipe tables (| col | col |)
        - Bare URLs (https://... outside code)

        Processing is done in two major phases:
        1. **Extract protected blocks**: Fenced code blocks and inline code
           are extracted first and replaced with placeholders, so their
           contents are never affected by markdown transformations.
        2. **Block-level processing**: Line-by-line state machine that
           handles headings, lists, blockquotes, horizontal rules, tables.
        3. **Inline processing**: Bold, italic, strikethrough, bare URLs.
        4. **Restore protected blocks**: Placeholders are replaced with
           the original rendered HTML.

        Args:
            text: Raw text from the LLM (may contain markdown).
            track_code_blocks: If True, store code blocks in
                self._code_blocks and add code-apply links.
                Used only for finalized messages (not during streaming)
                to avoid index instability while chunks arrive.

        Returns:
            HTML string suitable for QTextEdit rendering.
        """
        # ----------------------------------------------------------------
        # Phase 1: Escape HTML and extract protected blocks
        # ----------------------------------------------------------------

        # Escape HTML entities to prevent injection/rendering issues.
        # This must happen before any regex matching so angle brackets
        # in user text don't create spurious HTML elements.
        text = self._escape_html(text)

        # Protected blocks: code blocks and inline code whose contents
        # must not be transformed by later markdown rules. We replace
        # them with unique placeholder tokens and restore them at the end.
        protected_blocks = []

        # --- Fenced code blocks (```language\n...\n```) ---
        # Processed first so code block contents are protected from
        # later transformations. Code blocks use <pre> with inline styles.
        # Since the parent message is a <table> cell, <pre> inside <td>
        # renders correctly (see lessons.md Qt HTML constraints).
        cb_bg = self._theme["code_block_bg"]
        cb_text = self._theme["code_block_text"]
        link_color = self._theme["link_color"]

        def _replace_code_block(match):
            """Replace a fenced code block with a placeholder.

            Renders the code block to HTML (with Pygments highlighting if
            a language hint is provided) and stores it in protected_blocks.
            Returns a placeholder string that will be swapped back in
            after all other markdown processing is complete.
            """
            lang = match.group(1) or ""
            code = match.group(2)

            # Unescape HTML entities so Pygments sees the original code.
            # The code was escaped above; Pygments needs raw text
            # and will produce its own properly-escaped HTML output.
            raw_code = self._unescape_html(code)

            # Syntax-highlight with Pygments if a language is specified.
            # Falls back to plain <pre> for unknown languages or no hint.
            highlighted = self._highlight_code(raw_code, lang)

            # Code font from configurable appearance settings
            cff = self._code_font_family
            cfs = self._code_font_size

            if highlighted:
                # Pygments output is highlighted <span> elements.
                # Wrap in our styled <pre> for consistent appearance.
                lang_label = (
                    f'<span style="color:#888; font-size:0.85em;">'
                    f'{lang}</span><br>'
                    if lang else ""
                )
                block_html = (
                    f'<pre style="background-color:{cb_bg};'
                    f' font-family:{cff},monospace; font-size:{cfs}pt;'
                    f' padding:8px 12px; white-space:pre-wrap;'
                    f' word-wrap:break-word;">'
                    f'{lang_label}{highlighted}</pre>'
                )
            else:
                # Fallback: plain monochrome code block (no highlighting)
                lang_label = (
                    f'<span style="color:#888; font-size:0.85em;">'
                    f'{lang}</span><br>'
                    if lang else ""
                )
                block_html = (
                    f'<pre style="background-color:{cb_bg}; color:{cb_text};'
                    f' font-family:{cff},monospace; font-size:{cfs}pt;'
                    f' padding:8px 12px; white-space:pre-wrap;'
                    f' word-wrap:break-word;">'
                    f'{lang_label}{code}</pre>'
                )

            if track_code_blocks:
                # Store the raw code for code-apply actions and "Copy"
                # actions. Uses the unescaped version so insertions clean.
                index = len(self._code_blocks)
                self._code_blocks.append(raw_code)
                # Action links below the code block: Copy + Apply preview
                block_html += (
                    f'<a href="copy://{index}" style="color:{link_color};'
                    f' font-size:0.85em; text-decoration:none;">'
                    f'Copy</a>'
                    f'&nbsp;&nbsp;'
                    f'<a href="apply://{index}" style="color:{link_color};'
                    f' font-size:0.85em; text-decoration:none;">'
                    f'Apply...</a>'
                )

            # Store the rendered HTML and return a unique placeholder.
            # The placeholder uses a pattern that cannot appear in normal
            # text (HTML-escaped angle brackets + unique index).
            placeholder = f"\x00CODEBLOCK{len(protected_blocks)}\x00"
            protected_blocks.append(block_html)
            return placeholder

        text = re.sub(
            r"```(\w+)?\n(.*?)```",
            _replace_code_block,
            text,
            flags=re.DOTALL,
        )

        # --- Partial (unclosed) fenced code blocks ---
        # During streaming, the model may have sent the opening ```
        # but not yet the closing ```.  Without this pass the code
        # content is processed by inline markdown rules (bold, italic,
        # headings), causing spurious formatting that flickers until
        # the closing fence arrives.  We detect an opening fence
        # followed by content all the way to the end of the string
        # and protect it with the same placeholder mechanism.
        def _replace_partial_code_block(match):
            """Replace a partial (unclosed) fenced code block with a placeholder.

            Same rendering logic as complete code blocks, but applied to
            content that runs from an opening fence to end-of-string.
            """
            lang = match.group(1) or ""
            code = match.group(2)

            # Unescape HTML entities so Pygments sees the original code.
            raw_code = self._unescape_html(code)

            # Syntax-highlight with Pygments if a language is specified.
            highlighted = self._highlight_code(raw_code, lang)

            # Code font from configurable appearance settings
            cff = self._code_font_family
            cfs = self._code_font_size

            if highlighted:
                lang_label = (
                    f'<span style="color:#888; font-size:0.85em;">'
                    f'{lang}</span><br>'
                    if lang else ""
                )
                block_html = (
                    f'<pre style="background-color:{cb_bg};'
                    f' font-family:{cff},monospace; font-size:{cfs}pt;'
                    f' padding:8px 12px; white-space:pre-wrap;'
                    f' word-wrap:break-word;">'
                    f'{lang_label}{highlighted}</pre>'
                )
            else:
                lang_label = (
                    f'<span style="color:#888; font-size:0.85em;">'
                    f'{lang}</span><br>'
                    if lang else ""
                )
                block_html = (
                    f'<pre style="background-color:{cb_bg}; color:{cb_text};'
                    f' font-family:{cff},monospace; font-size:{cfs}pt;'
                    f' padding:8px 12px; white-space:pre-wrap;'
                    f' word-wrap:break-word;">'
                    f'{lang_label}{code}</pre>'
                )

            placeholder = f"\x00CODEBLOCK{len(protected_blocks)}\x00"
            protected_blocks.append(block_html)
            return placeholder

        text = re.sub(
            r"```(\w+)?\n(.+)$",
            _replace_partial_code_block,
            text,
            flags=re.DOTALL,
        )

        # --- Inline code (`code`) ---
        # Extract inline code before block-level processing so that
        # markdown syntax inside backticks is not interpreted.
        ic_bg = self._theme["inline_code_bg"]
        ic_text = self._theme["inline_code_text"]

        def _replace_inline_code(match):
            """Replace inline code with a placeholder.

            Renders the inline code span with monospace styling and
            stores it in protected_blocks so its contents are not
            affected by bold/italic/heading transformations.
            """
            code_content = match.group(1)
            code_html = (
                f'<code style="background-color:{ic_bg}; color:{ic_text};'
                f' padding:1px 4px;'
                f' font-family:{self._code_font_family},monospace;'
                f' font-size:{self._code_font_size}pt;">'
                f'{code_content}</code>'
            )
            placeholder = f"\x00INLINECODE{len(protected_blocks)}\x00"
            protected_blocks.append(code_html)
            return placeholder

        text = re.sub(r"`([^`]+)`", _replace_inline_code, text)

        # ----------------------------------------------------------------
        # Phase 2: Block-level processing (line-by-line state machine)
        # ----------------------------------------------------------------
        # Process each line to detect block-level markdown elements:
        # headings, lists, blockquotes, horizontal rules, and tables.
        # Lines that don't match any block pattern pass through as-is
        # and receive inline formatting in Phase 3.

        lines = text.split("\n")
        output_lines = []

        # State tracking for multi-line block elements.
        # Lists and tables span multiple consecutive lines, so we need
        # to track when we're inside one to emit proper open/close tags.
        in_blockquote = False  # Currently inside a blockquote sequence
        in_table = False       # Currently inside a GFM pipe table
        table_row_index = 0    # Row counter within current table

        # Nesting stack for lists. Each entry is a (indent_level, tag_type)
        # tuple where tag_type is "ul" or "ol". This allows correct
        # closing of mixed ordered/unordered nesting — when de-indenting,
        # we emit the correct </ul> or </ol> based on what was opened.
        # "Are we in a list" is derived from len(list_indent_stack) > 0.
        list_indent_stack = []  # Stack of (indent_level, "ul"|"ol") tuples

        # Theme colors for block elements
        bq_bg = self._theme["blockquote_bg"]
        bq_border = self._theme["blockquote_border"]
        bq_text = self._theme["blockquote_text"]
        tbl_border = self._theme["table_border"]
        tbl_header_bg = self._theme["table_header_bg"]
        hr_color = self._theme["hr_color"]

        for line_idx, line in enumerate(lines):
            stripped = line.strip()

            # --- Close open block elements if the line doesn't continue them ---

            # Close blockquote if the current line is not a blockquote line
            if in_blockquote and not stripped.startswith("&gt; ") and stripped != "&gt;":
                output_lines.append("</td></tr></table>")
                in_blockquote = False

            # Close table if the current line is not a table row
            if in_table and not self._is_table_row(stripped):
                output_lines.append("</table>")
                in_table = False
                table_row_index = 0

            # Close lists if the current line is not a list item and not blank
            # (blank lines inside lists are allowed for spacing)
            if list_indent_stack and stripped and not self._is_list_item(stripped):
                # Close all nested list levels
                output_lines.append(self._close_list_stack(list_indent_stack))
                list_indent_stack = []

            # --- Skip blank lines (emit <br> for paragraph spacing) ---
            if not stripped:
                # Blank line closes lists (if we're in one)
                if list_indent_stack:
                    output_lines.append(self._close_list_stack(list_indent_stack))
                    list_indent_stack = []
                # Only add spacing if we have prior content (avoid leading <br>)
                if output_lines:
                    output_lines.append("<br>")
                continue

            # --- Check for protected code block placeholders ---
            # Code block placeholders should pass through untouched
            if "\x00CODEBLOCK" in stripped:
                output_lines.append(stripped)
                continue

            # --- Horizontal rules: ---, ***, ___ (3+ characters) ---
            # Must be checked before list detection because "---" could
            # be confused with a list item starting with "-".
            if re.match(r"^(-{3,}|\*{3,}|_{3,})$", stripped):
                output_lines.append(
                    f'<hr style="border:none; border-top:1px solid'
                    f' {hr_color}; margin:8px 0;">'
                )
                continue

            # --- Headings: # H1, ## H2, ### H3, #### H4 ---
            # Render as bold text with scaled font size for visual hierarchy.
            # We use inline font-size styling because Qt's QTextEdit does
            # not reliably support <h1>-<h6> tags with proper sizing.
            heading_match = re.match(r"^(#{1,4})\s+(.+)$", stripped)
            if heading_match:
                level = len(heading_match.group(1))
                heading_text = heading_match.group(2)
                # Apply inline formatting to heading text content
                heading_text = self._apply_inline_formatting(heading_text)
                # Scale font size: H1=1.4em, H2=1.2em, H3=1.1em, H4=1.0em
                sizes = {1: "1.4em", 2: "1.2em", 3: "1.1em", 4: "1.0em"}
                font_size = sizes.get(level, "1.0em")
                output_lines.append(
                    f'<p style="margin-top:12px; margin-bottom:4px;'
                    f' font-size:{font_size}; font-weight:bold;">'
                    f'{heading_text}</p>'
                )
                continue

            # --- Blockquotes: lines starting with > ---
            # Uses a table with a colored left border cell to simulate
            # the classic blockquote appearance. We use <table> because
            # Qt's HTML renderer supports borders on table cells reliably
            # (unlike border-left on div/p elements).
            if stripped.startswith("&gt; ") or stripped == "&gt;":
                # Extract the quoted text (after "> ")
                quote_content = stripped[5:] if stripped.startswith("&gt; ") else ""
                # Apply inline formatting to quote content
                quote_content = self._apply_inline_formatting(quote_content)

                if not in_blockquote:
                    # Open a new blockquote table container
                    in_blockquote = True
                    output_lines.append(
                        f'<table cellpadding="4" cellspacing="0"'
                        f' style="margin:4px 0;">'
                        f'<tr><td style="background-color:{bq_bg};'
                        f' color:{bq_text};'
                        f' border-left:3px solid {bq_border};'
                        f' padding:4px 8px; font-style:italic;">'
                        f'{quote_content}'
                    )
                else:
                    # Continue the existing blockquote with a line break
                    output_lines.append(f"<br>{quote_content}")
                continue

            # --- GFM pipe tables: | col1 | col2 | ---
            # Detected by lines containing pipe characters. The second
            # row in a GFM table is always the separator (|---|---|)
            # which we skip. First row = header, rest = data rows.
            if self._is_table_row(stripped):
                # Parse cells from the pipe-delimited line
                cells = self._parse_table_cells(stripped)

                if not in_table:
                    # First row of a new table — this is the header row
                    in_table = True
                    table_row_index = 0
                    output_lines.append(
                        f'<table cellpadding="6" cellspacing="0"'
                        f' style="margin:4px 0; border-collapse:collapse;">'
                    )

                if table_row_index == 1 and self._is_table_separator(stripped):
                    # Second row is the separator (|---|---|) — skip it
                    # but still count it for row indexing
                    table_row_index += 1
                    continue

                # Render cells: header row (index 0) uses <th>, data uses <td>
                is_header = (table_row_index == 0)
                cell_tag = "th" if is_header else "td"
                cell_bg = tbl_header_bg if is_header else "transparent"

                row_html = "<tr>"
                for cell in cells:
                    # Apply inline formatting to each cell's content
                    cell_content = self._apply_inline_formatting(cell.strip())
                    row_html += (
                        f'<{cell_tag} style="border:1px solid {tbl_border};'
                        f' padding:4px 8px; background-color:{cell_bg};">'
                        f'{cell_content}</{cell_tag}>'
                    )
                row_html += "</tr>"
                output_lines.append(row_html)
                table_row_index += 1
                continue

            # --- Ordered lists: lines starting with "1. ", "2. ", etc. ---
            # We detect the pattern digit(s) + dot + space at the start.
            ol_match = re.match(r"^(\s*)(\d+)\.\s+(.+)$", line)
            if ol_match:
                indent_str = ol_match.group(1)
                item_text = ol_match.group(3)
                item_text = self._apply_inline_formatting(item_text)
                indent_level = len(indent_str) // 2  # 2 spaces per level

                if not list_indent_stack:
                    # Start a new ordered list (no list currently open)
                    list_indent_stack = [(indent_level, "ol")]
                    output_lines.append(
                        '<ol style="margin:4px 0; padding-left:24px;">'
                    )
                elif indent_level > list_indent_stack[-1][0]:
                    # Deeper nesting — open a nested <ol>
                    list_indent_stack.append((indent_level, "ol"))
                    output_lines.append(
                        '<ol style="margin:2px 0; padding-left:20px;">'
                    )
                elif indent_level < list_indent_stack[-1][0]:
                    # De-indent — close nested lists back to this level,
                    # using the correct closing tag for each level
                    while (list_indent_stack
                           and list_indent_stack[-1][0] > indent_level):
                        _, closing_tag = list_indent_stack.pop()
                        output_lines.append(f"</{closing_tag}>")

                output_lines.append(f"<li>{item_text}</li>")
                continue

            # --- Unordered lists: lines starting with "- ", "* ", "+ " ---
            ul_match = re.match(r"^(\s*)[-*+]\s+(.+)$", line)
            if ul_match:
                indent_str = ul_match.group(1)
                item_text = ul_match.group(2)
                item_text = self._apply_inline_formatting(item_text)
                indent_level = len(indent_str) // 2  # 2 spaces per level

                if not list_indent_stack:
                    # Start a new unordered list (no list currently open)
                    list_indent_stack = [(indent_level, "ul")]
                    output_lines.append(
                        '<ul style="margin:4px 0; padding-left:24px;">'
                    )
                elif indent_level > list_indent_stack[-1][0]:
                    # Deeper nesting — open a nested <ul>
                    list_indent_stack.append((indent_level, "ul"))
                    output_lines.append(
                        '<ul style="margin:2px 0; padding-left:20px;">'
                    )
                elif indent_level < list_indent_stack[-1][0]:
                    # De-indent — close nested lists back to this level,
                    # using the correct closing tag for each level
                    while (list_indent_stack
                           and list_indent_stack[-1][0] > indent_level):
                        _, closing_tag = list_indent_stack.pop()
                        output_lines.append(f"</{closing_tag}>")

                output_lines.append(f"<li>{item_text}</li>")
                continue

            # --- Default: regular text line ---
            # Apply inline formatting and append as a paragraph-like line.
            formatted = self._apply_inline_formatting(stripped)
            output_lines.append(formatted + "<br>")

        # --- Close any open block elements at end of text ---
        if in_blockquote:
            output_lines.append("</td></tr></table>")
        if in_table:
            output_lines.append("</table>")
        if list_indent_stack:
            output_lines.append(
                self._close_list_stack(list_indent_stack)
            )

        # Join all processed lines into the final HTML string
        text = "\n".join(output_lines)

        # ----------------------------------------------------------------
        # Phase 3: Restore protected blocks
        # ----------------------------------------------------------------
        # Replace placeholder tokens with their rendered HTML. This must
        # happen last so that code block and inline code contents are
        # never touched by block-level or inline markdown rules.
        for i, block_html in enumerate(protected_blocks):
            # Code block placeholders
            text = text.replace(f"\x00CODEBLOCK{i}\x00", block_html)
            # Inline code placeholders
            text = text.replace(f"\x00INLINECODE{i}\x00", block_html)

        return text

    # --- Markdown rendering helper methods ---
    # These are extracted from _render_markdown to keep the main method
    # readable while handling the complexity of each element type.

    def _apply_inline_formatting(self, text):
        """Apply inline markdown formatting to a text fragment.

        Handles bold, italic, strikethrough, and bare URLs. This is
        called on individual lines or cell contents after block-level
        processing, so it never sees fenced code blocks.

        Order of operations matters:
        1. Bold (**text**) — must come before italic to avoid conflict
        2. Italic (*text* and _text_) — single asterisk/underscore
        3. Strikethrough (~~text~~)
        4. Bare URLs (https://... or http://...)

        Inline code has already been extracted to placeholders, so
        backtick patterns will not appear in the input.

        Args:
            text: A text fragment (single line or table cell content).

        Returns:
            The text with inline markdown converted to HTML tags.
        """
        # Bold: **text** -> <b>text</b>
        # Uses non-greedy match to handle multiple bold spans on one line
        text = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", text)

        # Italic: *text* -> <i>text</i>
        # Must come after bold so **bold** is not misinterpreted.
        # Uses negative lookbehind/lookahead to avoid matching inside
        # words like "file_name_here" (underscores) or already-consumed
        # asterisks from bold. The pattern requires non-asterisk chars
        # adjacent to the delimiters to prevent false matches.
        text = re.sub(r"(?<!\*)\*([^*]+?)\*(?!\*)", r"<i>\1</i>", text)

        # Italic with underscores: _text_ -> <i>text</i>
        # Requires word boundary or start/end to avoid matching snake_case
        # variable names. The lookbehind/lookahead ensure underscores
        # are at word boundaries, not mid-identifier.
        text = re.sub(
            r"(?<!\w)_([^_]+?)_(?!\w)", r"<i>\1</i>", text
        )

        # Strikethrough: ~~text~~ -> <s>text</s>
        # Note: Qt's QTextEdit supports <s> for strikethrough but NOT <del>.
        # The <del> tag is ignored by Qt's HTML subset, while <s> renders
        # the expected line-through text decoration.
        text = re.sub(r"~~(.+?)~~", r"<s>\1</s>", text)

        # Bare URLs: convert http:// and https:// URLs to clickable links.
        # Only matches URLs that are not already inside HTML tags (the
        # code block placeholders use \x00 delimiters, not angle brackets).
        # The pattern matches non-whitespace, non-null sequences after
        # the protocol prefix. We use a negative lookahead to stop before
        # HTML entity sequences that represent angle brackets or quotes
        # (since the text has been HTML-escaped). Regular `&amp;` in
        # query strings is allowed to pass through.
        link_color = self._theme["link_color"]
        text = re.sub(
            r'(https?://(?:(?!&lt;|&gt;|&quot;|\s|\x00).)+)',
            f'<a href="\\1" style="color:{link_color};">\\1</a>',
            text,
        )

        return text

    def _is_list_item(self, stripped_line):
        """Check if a stripped line is a list item (ordered or unordered).

        Args:
            stripped_line: A line with leading/trailing whitespace removed.

        Returns:
            True if the line matches unordered (- / * / +) or ordered
            (digit.) list item syntax.
        """
        # Unordered: starts with -, *, or + followed by space
        if re.match(r"^[-*+]\s+", stripped_line):
            return True
        # Ordered: starts with digit(s) + dot + space
        if re.match(r"^\d+\.\s+", stripped_line):
            return True
        return False

    def _is_table_row(self, stripped_line):
        """Check if a stripped line looks like a GFM pipe table row.

        A table row starts and ends with pipe characters and contains
        at least one internal pipe. This distinguishes table rows from
        lines that merely contain a pipe character in prose.

        Args:
            stripped_line: A line with leading/trailing whitespace removed.

        Returns:
            True if the line matches GFM table row syntax.
        """
        # Must start with | and end with |, with content in between
        return (
            stripped_line.startswith("|")
            and stripped_line.endswith("|")
            and len(stripped_line) > 2
        )

    def _is_table_separator(self, stripped_line):
        """Check if a line is a GFM table separator row.

        The separator row appears after the header and contains only
        pipes, dashes, colons (for alignment), and spaces.
        Example: |---|:---:|---:|

        Args:
            stripped_line: A line with leading/trailing whitespace removed.

        Returns:
            True if the line is a table separator (should be skipped
            during rendering).
        """
        # After HTML escaping, the line only contains |, -, :, spaces
        return bool(re.match(r"^\|[\s\-:| ]+\|$", stripped_line))

    def _parse_table_cells(self, stripped_line):
        """Extract cell contents from a GFM pipe table row.

        Splits on pipe characters and removes the empty first/last
        elements that result from leading/trailing pipes.

        Args:
            stripped_line: A pipe-delimited table row (e.g., "| A | B |").

        Returns:
            List of cell content strings (whitespace-trimmed).
        """
        # Split on | and remove empty strings from leading/trailing pipes
        parts = stripped_line.split("|")
        # First and last elements are empty because the line starts/ends
        # with |. Slice them off.
        return [p for p in parts[1:-1]]

    def _close_list_stack(self, indent_stack):
        """Generate closing tags for all open list nesting levels.

        When a list ends (blank line or non-list content), we need to
        close all nested <ul>/<ol> tags that were opened for indentation.
        Each entry in the stack is a (indent_level, tag_type) tuple, so
        mixed ordered/unordered nesting closes with the correct tag.

        Args:
            indent_stack: Stack of (indent_level, tag_type) tuples
                currently open, where tag_type is "ul" or "ol".

        Returns:
            HTML string with the appropriate closing tags in reverse
            order (innermost list closed first).
        """
        # Close each nesting level with its own tag type, innermost first
        closing_tags = []
        for _, tag_type in reversed(indent_stack):
            closing_tags.append(f"</{tag_type}>")
        return "".join(closing_tags)

    def _highlight_code(self, code, language=""):
        """Syntax-highlight code using Pygments with inline styles.

        Uses inline styles (noclasses=True) because QTextEdit does not
        support CSS class-based styling. The Pygments style is chosen
        based on the current theme (dark/light).

        Args:
            code: Raw code text (not HTML-escaped).
            language: Language hint from the markdown fence (e.g., "python").
                If empty, returns None to signal fallback to plain rendering.

        Returns:
            Highlighted HTML string (inline-styled spans), or None if
            highlighting is not possible (no language hint or unknown language).
        """
        if not language:
            return None

        try:
            from pygments import highlight
            from pygments.lexers import get_lexer_by_name
            from pygments.formatters import HtmlFormatter

            lexer = get_lexer_by_name(language, stripall=True)

            # Pick a Pygments style based on user configuration.
            # Falls back to the current theme-appropriate default.
            style = (self._pygments_style_dark if self._is_dark
                     else self._pygments_style_light)

            # nowrap=True gives us just the highlighted <span> elements
            # without wrapping <div>/<pre> — we provide our own <pre> wrapper
            # for consistent styling with our message bubbles.
            formatter = HtmlFormatter(
                style=style, noclasses=True, nowrap=True
            )
            return highlight(code, lexer, formatter)
        except Exception:
            # Unknown language or Pygments error — fall back to plain text
            return None

    def _escape_html(self, text):
        """Escape HTML special characters to prevent rendering issues."""
        return (
            text
            .replace("&", "&amp;")
            .replace("<", "&lt;")
            .replace(">", "&gt;")
            .replace('"', "&quot;")
        )

    def _unescape_html(self, text):
        """Reverse HTML escaping to recover the original text.

        Used to store raw code from code blocks (which were HTML-escaped
        during markdown rendering) so that code-apply actions insert
        the original code, not HTML entities.
        """
        return (
            text
            .replace("&quot;", '"')
            .replace("&gt;", ">")
            .replace("&lt;", "<")
            .replace("&amp;", "&")
        )

    def mousePressEvent(self, event):
        """Handle clicks on code block action links.

        Detects clicks on custom URLs embedded below code blocks:
        - apply://<index> -> emit sig_apply_code_requested
        - copy://<index> -> copy code to system clipboard

        Falls through to default behavior for all other clicks.
        """
        anchor = self.anchorAt(event.pos())
        if not anchor:
            super().mousePressEvent(event)
            return

        # Parse the action and code block index from the URL
        if anchor.startswith("apply://"):
            prefix = "apply://"
        elif anchor.startswith("copy://"):
            prefix = "copy://"
        else:
            super().mousePressEvent(event)
            return

        try:
            index = int(anchor[len(prefix):])
            if not (0 <= index < len(self._code_blocks)):
                return
        except (ValueError, IndexError):
            return

        code = self._code_blocks[index]

        if prefix == "apply://":
            self.sig_apply_code_requested.emit(code)
        elif prefix == "copy://":
            # Copy code to the system clipboard
            clipboard = QApplication.clipboard()
            clipboard.setText(code)
