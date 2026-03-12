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
"""

import logging
import re

from qtpy.QtCore import Signal
from qtpy.QtWidgets import QApplication, QTextEdit

logger = logging.getLogger(__name__)


class ChatDisplay(QTextEdit):
    """Read-only text display for the AI chat conversation.

    Renders messages as styled HTML with basic markdown support.
    During streaming, tokens accumulate in a buffer and the current
    assistant message is re-rendered on each chunk.

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
    }

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setReadOnly(True)
        self.setAcceptRichText(True)

        # Detect dark vs light theme from the widget's background color.
        # If the background luminance is below 128, we're on a dark theme.
        bg = self.palette().color(self.backgroundRole())
        is_dark = bg.lightness() < 128
        self._theme = self._DARK_THEME if is_dark else self._LIGHT_THEME

        # Buffer for accumulating streaming tokens from the LLM
        self._streaming_buffer = ""
        # Whether an assistant response is currently being streamed
        self._is_streaming = False
        # All finalized message HTML (persists across streaming cycles)
        self._html_content = ""

        # Extracted code blocks from assistant messages, indexed by
        # position. Used to look up code when user clicks code-apply
        # links (which reference blocks by index).
        self._code_blocks = []

        # Initialize the document (empty chat)
        self.setHtml(self._html_content)

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
        return (
            f'<table width="100%" cellpadding="12" cellspacing="0"'
            f' style="margin-top:4px; margin-bottom:4px;">'
            f'<tr><td style="background-color:{bg}; color:{text_color};'
            f' font-family:sans-serif; font-size:10pt;'
            f' border-radius:8px; line-height:1.5;">'
            f'<span style="font-size:8pt; font-weight:bold;'
            f' color:{lc}; letter-spacing:0.5px;">'
            f'{label}</span><br>'
            f'{content}'
            f'</td></tr></table>'
        )

    def append_user_message(self, text):
        """Add a user message bubble to the display.

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
        self.setHtml(self._html_content)
        self._scroll_to_bottom()

    def append_assistant_message(self, text):
        """Add a finalized assistant message to the display."""
        rendered = self._render_markdown(text or "", track_code_blocks=True)
        self._html_content += self._wrap_message(
            self._theme["assistant_bg"], self._theme["assistant_text"],
            "AI", rendered,
            label_color=self._theme["assistant_label"],
        )
        self.setHtml(self._html_content)
        self._scroll_to_bottom()

    def start_assistant_message(self):
        """Begin a new assistant response. Call before streaming chunks."""
        self._streaming_buffer = ""
        self._is_streaming = True

    def append_chunk(self, text):
        """Append a streaming token to the current assistant response.

        Accumulates text in a buffer and re-renders the current message
        on each chunk. Detects <think>...</think> blocks and renders them
        separately from the main response in a dimmed style.

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

        # Build the streaming HTML: thinking block (if any) + response
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

        self.setHtml(self._html_content + streaming_html)
        self._scroll_to_bottom()

    def finish_assistant_message(self):
        """Finalize the current assistant response.

        Commits the streaming buffer to the permanent HTML content
        and resets the streaming state. Thinking blocks are preserved
        in the final output. Code blocks are tracked and "Insert into
        editor" links are added.
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

        self.setHtml(self._html_content)
        self._streaming_buffer = ""
        self._is_streaming = False
        self._scroll_to_bottom()

    def discard_assistant_message(self):
        """Drop the current streaming assistant message without saving it."""
        if not self._is_streaming:
            return

        self._streaming_buffer = ""
        self._is_streaming = False
        self.setHtml(self._html_content)
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
        self.setHtml(self._html_content)
        self._scroll_to_bottom()

    def clear_conversation(self):
        """Remove all messages and reset the display to empty."""
        self._html_content = ""
        self._streaming_buffer = ""
        self._is_streaming = False
        self._code_blocks.clear()
        self.setHtml(self._html_content)

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

        return (
            f'<table width="100%" cellpadding="8" cellspacing="0"'
            f' style="margin-top:6px; margin-bottom:2px;">'
            f'<tr><td style="background-color:{t["thinking_bg"]};'
            f' color:{t["thinking_text"]};'
            f' font-family:sans-serif; font-size:9pt;'
            f' font-style:italic;'
            f' border-left:3px solid {t["thinking_border"]};'
            f' border-radius:4px;">'
            f'<b style="font-style:normal; font-size:8pt;">'
            f'{label}</b><br>'
            f'{escaped_thinking}'
            f'</td></tr></table>'
        )

    # --- Private rendering helpers ---

    def _render_markdown(self, text, track_code_blocks=False):
        """Convert basic markdown to HTML for chat display.

        Handles fenced code blocks, inline code, and bold text.
        This is intentionally minimal — a full markdown library
        (e.g., mistune) can replace this in Phase 4.

        Processing order matters: code blocks are handled first so
        their contents aren't affected by inline code or bold rules.

        Args:
            text: Raw text from the LLM (may contain markdown).
            track_code_blocks: If True, store code blocks in
                self._code_blocks and add code-apply links.
                Used only for finalized messages (not during streaming)
                to avoid index instability while chunks arrive.

        Returns:
            HTML string suitable for QTextEdit rendering.
        """
        # Step 1: Escape HTML entities to prevent injection/rendering issues
        text = self._escape_html(text)

        # Step 2: Fenced code blocks (```language\n...\n```)
        # Processed first so code block contents are protected from
        # later transformations (inline code, bold, newline conversion).
        # Code blocks use <pre> with inline styles. Since the parent
        # message is a <table> cell, <pre> inside <td> renders correctly.
        cb_bg = self._theme["code_block_bg"]
        cb_text = self._theme["code_block_text"]
        link_color = self._theme["link_color"]

        def _replace_code_block(match):
            lang = match.group(1) or ""
            code = match.group(2)

            # Unescape HTML entities so Pygments sees the original code.
            # The code was escaped in Step 1; Pygments needs raw text
            # and will produce its own properly-escaped HTML output.
            raw_code = self._unescape_html(code)

            # Syntax-highlight with Pygments if a language is specified.
            # Falls back to plain <pre> for unknown languages or no hint.
            highlighted = self._highlight_code(raw_code, lang)

            if highlighted:
                # Pygments output is a <div><pre>...</pre></div> block.
                # Wrap it in our styled container for consistent appearance.
                lang_label = (
                    f'<span style="color:#888; font-size:0.85em;">'
                    f'{lang}</span><br>'
                    if lang else ""
                )
                block_html = (
                    f'<pre style="background-color:{cb_bg};'
                    f' font-family:Courier New,monospace; font-size:9pt;'
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
                    f' font-family:Courier New,monospace; font-size:9pt;'
                    f' padding:8px 12px; white-space:pre-wrap;'
                    f' word-wrap:break-word;">'
                    f'{lang_label}{code}</pre>'
                )

            if track_code_blocks:
                # Store the raw code for code-apply actions and "Copy"
                # actions. Uses the unescaped version so insertions are clean.
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

            return block_html

        text = re.sub(
            r"```(\w+)?\n(.*?)```",
            _replace_code_block,
            text,
            flags=re.DOTALL,
        )

        # Step 3: Inline code (`code`)
        ic_bg = self._theme["inline_code_bg"]
        ic_text = self._theme["inline_code_text"]
        text = re.sub(
            r"`([^`]+)`",
            f'<code style="background-color:{ic_bg}; color:{ic_text};'
            f' padding:1px 4px; font-family:Courier New,monospace;'
            f' font-size:9pt;">\\1</code>',
            text,
        )

        # Step 4: Bold (**text**)
        text = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", text)

        # Step 5: Convert newlines to <br>, but NOT inside <pre> blocks.
        # Split on pre blocks, only transform the non-pre segments.
        parts = re.split(r"(<pre.*?</pre>)", text, flags=re.DOTALL)
        for i, part in enumerate(parts):
            if not part.startswith("<pre"):
                parts[i] = part.replace("\n", "<br>")
        text = "".join(parts)

        return text

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

            # Pick a Pygments style that matches the Spyder theme.
            # Dark theme → monokai, light theme → default.
            bg = self.palette().color(self.backgroundRole())
            style = "monokai" if bg.lightness() < 128 else "default"

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
        - apply://<index> → emit sig_apply_code_requested
        - copy://<index> → copy code to system clipboard

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

    def _scroll_to_bottom(self):
        """Scroll the display to show the latest content."""
        scrollbar = self.verticalScrollBar()
        scrollbar.setValue(scrollbar.maximum())
