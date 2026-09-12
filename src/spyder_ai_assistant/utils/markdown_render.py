"""Markdown to HTML rendering for chat transcripts.

Converts the markdown that LLMs emit into the narrow HTML subset Qt's
rich text engine understands: fenced code blocks (syntax-highlighted with
Pygments), inline code, bold/italic/strikethrough, headings, nested
ordered and unordered lists, blockquotes, horizontal rules, GFM pipe
tables and bare URLs.

HTML structure note: Qt's rich text renderer cannot properly contain
block-level elements (<pre>, <table>) inside <div> tags — they break out
of the parent container. To work around this, ALL containers here use
<table> cells with inline styles. This is the only reliable way to keep
code blocks visually contained within their message bubble.

The renderer holds only plain configuration (a theme colour dict, code
font, Pygments styles) and pure text state, so any widget that shows a
transcript can reuse it without owning a QTextEdit. ``ChatDisplay``
delegates its whole markdown pipeline here.
"""

import re
from collections import OrderedDict

from qtpy.QtGui import QColor

# Highlighted code is cached per (language, style, code). While a response
# streams, every already-closed code block in it is re-rendered about 30
# times a second even though it can no longer change; highlighting measured
# 0.26 ms per block, so a message with a dozen closed blocks spent several
# milliseconds per render re-deriving identical HTML. The cap keeps a long
# session from holding every snippet it ever displayed.
_HIGHLIGHT_CACHE_MAX = 256


# --- Pure text helpers ---
# Module-level because they depend on nothing but their argument: the
# renderer, the widget and any future transcript view share one copy.


def escape_html(text):
    """Escape HTML special characters to prevent rendering issues."""
    return (
        text
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )


def unescape_html(text):
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


def trim_before_block(output_lines):
    """Remove the line breaks sitting just above an opening block element.

    Text lines end with ``<br>`` and blank markdown lines add a
    standalone ``<br>`` spacer. Above a block element (code block, list,
    table, blockquote, heading, rule) Qt renders both as empty lines:
    the block already starts on its own line and carries its own
    margin, so they only add a large gap. Call this right before
    appending the block's opening HTML.
    """
    # Blank markdown lines before the block: drop their spacers.
    while output_lines and output_lines[-1] == "<br>":
        output_lines.pop()
    # The text line above the block: drop its own trailing line break.
    if output_lines and output_lines[-1].endswith("<br>"):
        output_lines[-1] = output_lines[-1][:-len("<br>")]


def is_list_item(stripped_line):
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


def is_table_row(stripped_line):
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


def is_table_separator(stripped_line):
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


def parse_table_cells(stripped_line):
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


def close_list_stack(indent_stack):
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


class MarkdownRenderer:
    """Render markdown to the HTML a Qt rich-text view can display.

    Owns the appearance inputs the pipeline needs (theme colours, code
    font, Pygments styles), the code blocks extracted from the messages
    it rendered, and the highlight cache. It has no widget state, so a
    transcript widget creates one and forwards its own markdown calls.

    The theme dict is held by reference: a caller that mutates its theme
    in place (as a live theme edit does) is seen by the next render.

    Usage:
        renderer = MarkdownRenderer(theme)
        html = renderer.render("**hi**")
        renderer.update(theme=new_theme)
    """

    def __init__(self, theme, *, code_font_family="Courier New",
                 code_font_size=9, pygments_style_dark="monokai",
                 pygments_style_light="default", is_dark=False,
                 highlight_cache_max=_HIGHLIGHT_CACHE_MAX):
        """Store the appearance configuration the pipeline renders with.

        Args:
            theme: Colour dict from ``chat_themes.get_theme_colors``.
            code_font_family: Font family for code blocks and inline code.
            code_font_size: Point size for code blocks and inline code.
            pygments_style_dark: Pygments style used on dark code cards.
            pygments_style_light: Pygments style used on light code cards.
            is_dark: Dark/light fallback used only when the theme's code
                card colour cannot be parsed; see ``code_card_is_dark``.
            highlight_cache_max: Cap for the highlight LRU cache.
        """
        self.theme = theme
        self.code_font_family = code_font_family
        self.code_font_size = code_font_size
        self.pygments_style_dark = pygments_style_dark
        self.pygments_style_light = pygments_style_light
        self.is_dark = is_dark

        # Code blocks extracted from rendered messages, in the order they
        # were rendered. Apply/Copy links reference blocks by this index.
        self._code_blocks = []
        # (language, style, code) -> highlighted HTML or None. See
        # _HIGHLIGHT_CACHE_MAX for why this exists.
        self._highlight_cache = OrderedDict()
        self._highlight_cache_max = highlight_cache_max

    # --- Configuration ---

    def update(self, *, theme=None, code_font_family=None,
               code_font_size=None, pygments_style_dark=None,
               pygments_style_light=None, is_dark=None):
        """Apply the appearance values that were provided, leaving the rest.

        ``None`` means "not provided" for every key, which is what makes
        this safe to call with a subset of the settings a user changed.

        Cached highlighting survives a style change because the Pygments
        style is part of the cache key, so a restyled block misses the
        cache and is re-highlighted rather than served stale.
        """
        if theme is not None:
            self.theme = theme
        if code_font_family is not None:
            self.code_font_family = code_font_family
        if code_font_size is not None:
            self.code_font_size = code_font_size
        if pygments_style_dark is not None:
            self.pygments_style_dark = pygments_style_dark
        if pygments_style_light is not None:
            self.pygments_style_light = pygments_style_light
        if is_dark is not None:
            self.is_dark = is_dark

    # --- Tracked state ---

    @property
    def code_blocks(self):
        """Raw code from every tracked block, in render order."""
        return self._code_blocks

    @property
    def highlight_cache(self):
        """The (language, style, code) -> HTML LRU cache."""
        return self._highlight_cache

    def clear_code_blocks(self):
        """Forget the tracked code blocks (the transcript was cleared)."""
        self._code_blocks.clear()

    def clear_highlight_cache(self):
        """Drop cached highlighting (the transcript was cleared)."""
        self._highlight_cache.clear()

    # --- Rendering ---

    def render(self, text, track_code_blocks=False):
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
        text = escape_html(text)

        # Protected blocks: code blocks and inline code whose contents
        # must not be transformed by later markdown rules. We replace
        # them with unique placeholder tokens and restore them at the end.
        protected_blocks = []

        # --- Fenced code blocks (```language\n...\n```) ---
        # Processed first so code block contents are protected from
        # later transformations. Code blocks use <pre> with inline styles.
        # Since the parent message is a <table> cell, <pre> inside <td>
        # renders correctly (see lessons.md Qt HTML constraints).
        link_color = self.theme["link_color"]

        def _protect_fenced_block(match, with_actions, complete=True):
            """Replace one fenced code block with a placeholder.

            Renders the block to HTML (with Pygments highlighting if a
            language hint is given) and stores it in protected_blocks, so
            later markdown rules cannot touch its contents. Returns the
            placeholder that is swapped back in at the end.

            ``with_actions`` adds the Copy/Apply links and registers the
            code for the code-apply path. Only complete blocks get them:
            a block still being streamed is not something to apply yet.
            """
            lang = match.group(1) or ""
            code = match.group(2)

            # Unescape HTML entities so Pygments sees the original code.
            # The code was escaped above; Pygments needs raw text
            # and will produce its own properly-escaped HTML output.
            raw_code = unescape_html(code)

            # Syntax-highlight with Pygments if a language is specified.
            # Falls back to plain <pre> for unknown languages or no hint.
            highlighted = self.highlight_code(raw_code, lang, cache=complete)

            block_html = self.code_block_html(lang, code, highlighted)

            if with_actions:
                # Store the raw code for code-apply actions and "Copy"
                # actions. Uses the unescaped version so insertions clean.
                index = len(self._code_blocks)
                self._code_blocks.append(raw_code)
                # Action links below the code block: Copy + Apply preview
                block_html += (
                    f'<a href="copy://{index}" style="color:{link_color};'
                    f' font-size:10pt; font-weight:600; text-decoration:underline;">'
                    f'Copy</a>'
                    f'&nbsp;&nbsp;│&nbsp;&nbsp;'
                    f'<a href="apply://{index}" style="color:{link_color};'
                    f' font-size:10pt; font-weight:600; text-decoration:underline;">'
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
            lambda match: _protect_fenced_block(match, track_code_blocks),
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
        # Same renderer as above, without the Copy/Apply actions.
        text = re.sub(
            r"```(\w+)?\n(.+)$",
            lambda match: _protect_fenced_block(match, False, complete=False),
            text,
            flags=re.DOTALL,
        )

        # --- Inline code (`code`) ---
        # Extract inline code before block-level processing so that
        # markdown syntax inside backticks is not interpreted.
        ic_bg = self.theme["inline_code_bg"]
        ic_text = self.theme["inline_code_text"]

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
                f' font-family:{self.code_font_family},monospace;'
                f' font-size:{self.code_font_size}pt;">'
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
        bq_bg = self.theme["blockquote_bg"]
        bq_border = self.theme["blockquote_border"]
        bq_text = self.theme["blockquote_text"]
        tbl_border = self.theme["table_border"]
        tbl_header_bg = self.theme["table_header_bg"]
        hr_color = self.theme["hr_color"]

        for line_idx, line in enumerate(lines):
            stripped = line.strip()

            # --- Close open block elements if the line doesn't continue them ---

            # Close blockquote if the current line is not a blockquote line
            if in_blockquote and not stripped.startswith("&gt; ") and stripped != "&gt;":
                output_lines.append("</td></tr></table>")
                in_blockquote = False

            # Close table if the current line is not a table row
            if in_table and not is_table_row(stripped):
                output_lines.append("</table>")
                in_table = False
                table_row_index = 0

            # Close lists if the current line is not a list item and not blank
            # (blank lines inside lists are allowed for spacing)
            if list_indent_stack and stripped and not is_list_item(stripped):
                # Close all nested list levels
                output_lines.append(close_list_stack(list_indent_stack))
                list_indent_stack = []

            # --- Skip blank lines (emit <br> for paragraph spacing) ---
            if not stripped:
                # Blank line closes lists (if we're in one)
                if list_indent_stack:
                    output_lines.append(close_list_stack(list_indent_stack))
                    list_indent_stack = []
                # Only add spacing if we have prior content (avoid leading <br>)
                if output_lines:
                    output_lines.append("<br>")
                continue

            # --- Check for protected code block placeholders ---
            # Code block placeholders should pass through untouched. A line
            # that starts with one opens a <pre> block once restored.
            if "\x00CODEBLOCK" in stripped:
                if stripped.startswith("\x00CODEBLOCK"):
                    trim_before_block(output_lines)
                output_lines.append(stripped)
                continue

            # --- Horizontal rules: ---, ***, ___ (3+ characters) ---
            # Must be checked before list detection because "---" could
            # be confused with a list item starting with "-".
            if re.match(r"^(-{3,}|\*{3,}|_{3,})$", stripped):
                trim_before_block(output_lines)
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
                heading_text = self.apply_inline_formatting(heading_text)
                # Scale font size: H1=1.4em, H2=1.2em, H3=1.1em, H4=1.0em
                sizes = {1: "1.4em", 2: "1.2em", 3: "1.1em", 4: "1.0em"}
                font_size = sizes.get(level, "1.0em")
                trim_before_block(output_lines)
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
                quote_content = self.apply_inline_formatting(quote_content)

                if not in_blockquote:
                    # Open a new blockquote table container
                    in_blockquote = True
                    trim_before_block(output_lines)
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
            if is_table_row(stripped):
                # Parse cells from the pipe-delimited line
                cells = parse_table_cells(stripped)

                if not in_table:
                    # First row of a new table — this is the header row
                    in_table = True
                    table_row_index = 0
                    trim_before_block(output_lines)
                    output_lines.append(
                        '<table cellpadding="6" cellspacing="0"'
                        ' style="margin:4px 0; border-collapse:collapse;">'
                    )

                if table_row_index == 1 and is_table_separator(stripped):
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
                    cell_content = self.apply_inline_formatting(cell.strip())
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
                item_text = self.apply_inline_formatting(item_text)
                indent_level = len(indent_str) // 2  # 2 spaces per level

                if not list_indent_stack:
                    # Start a new ordered list (no list currently open)
                    list_indent_stack = [(indent_level, "ol")]
                    trim_before_block(output_lines)
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
                item_text = self.apply_inline_formatting(item_text)
                indent_level = len(indent_str) // 2  # 2 spaces per level

                if not list_indent_stack:
                    # Start a new unordered list (no list currently open)
                    list_indent_stack = [(indent_level, "ul")]
                    trim_before_block(output_lines)
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
            formatted = self.apply_inline_formatting(stripped)
            output_lines.append(formatted + "<br>")

        # --- Close any open block elements at end of text ---
        if in_blockquote:
            output_lines.append("</td></tr></table>")
        if in_table:
            output_lines.append("</table>")
        if list_indent_stack:
            output_lines.append(
                close_list_stack(list_indent_stack)
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

    # --- Rendering helper methods ---
    # These are extracted from render() to keep the main method readable
    # while handling the complexity of each element type.

    def code_block_html(self, lang, escaped_code, highlighted):
        """Return the styled ``<pre>`` block for one fenced code snippet.

        Shared by complete and partial (still streaming) fences so both
        render identically. ``highlighted`` is Pygments output when a
        language was recognised; otherwise the already-escaped source is
        shown in the plain code colour.

        Args:
            lang: Language hint from the fence (may be empty).
            escaped_code: HTML-escaped source, used when not highlighted.
            highlighted: Pygments HTML or ``None``.
        """
        t = self.theme
        lang_label = (
            f'<span style="color:{t["lang_label"]}; font-size:0.85em;">'
            f'{lang}</span><br>'
            if lang else ""
        )
        body = highlighted if highlighted else escaped_code
        # Plain blocks need an explicit text colour; Pygments spans carry
        # their own colours.
        color = "" if highlighted else f' color:{t["code_block_text"]};'
        # Explicit vertical margin: Qt's default <pre> margin is larger than
        # the spacing used around lists and tables in the transcript.
        return (
            f'<pre style="background-color:{t["code_block_bg"]};{color}'
            f' margin:6px 0;'
            f' font-family:{self.code_font_family},monospace;'
            f' font-size:{self.code_font_size}pt;'
            f' padding:8px 12px; white-space:pre-wrap;'
            f' word-wrap:break-word;">'
            f'{lang_label}{body}</pre>'
        )

    def apply_inline_formatting(self, text):
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
        link_color = self.theme["link_color"]
        text = re.sub(
            r'(https?://(?:(?!&lt;|&gt;|&quot;|\s|\x00).)+)',
            f'<a href="\\1" style="color:{link_color};">\\1</a>',
            text,
        )

        return text

    def code_card_is_dark(self):
        """Return True when the theme's code block background is dark."""
        color = QColor(self.theme.get("code_block_bg", ""))
        if not color.isValid():
            return self.is_dark
        return color.lightness() < 128

    def highlight_code(self, code, language="", *, cache=True):
        """Syntax-highlight code using Pygments with inline styles.

        Uses inline styles (noclasses=True) because QTextEdit does not
        support CSS class-based styling. The Pygments style is chosen
        based on the current theme (dark/light).

        Args:
            code: Raw code text (not HTML-escaped).
            language: Language hint from the markdown fence (e.g., "python").
                If empty, returns None to signal fallback to plain rendering.
            cache: False for a fence that is still streaming — its content
                changes with every token, so caching it would only fill the
                cache with snippets that are never looked up again.

        Returns:
            Highlighted HTML string (inline-styled spans), or None if
            highlighting is not possible (no language hint or unknown language).
        """
        if not language:
            return None

        # Token colours must match the code card, not the interface:
        # several light presets deliberately keep dark code cards, and a
        # light Pygments style on a dark card is unreadable. The style is
        # part of the cache key, so a theme change cannot serve stale
        # colours.
        style = (self.pygments_style_dark if self.code_card_is_dark()
                 else self.pygments_style_light)
        key = (language, style, code)
        if cache and key in self._highlight_cache:
            self._highlight_cache.move_to_end(key)
            return self._highlight_cache[key]

        try:
            from pygments import highlight
            from pygments.lexers import get_lexer_by_name
            from pygments.formatters import HtmlFormatter

            lexer = get_lexer_by_name(language, stripall=True)

            # nowrap=True gives us just the highlighted <span> elements
            # without wrapping <div>/<pre> — we provide our own <pre> wrapper
            # for consistent styling with our message bubbles.
            formatter = HtmlFormatter(
                style=style, noclasses=True, nowrap=True
            )
            result = highlight(code, lexer, formatter)
        except Exception:
            # Unknown language or Pygments error — fall back to plain text.
            # Cached too, so an unknown language is not retried every render.
            result = None

        if cache:
            self._highlight_cache[key] = result
            if len(self._highlight_cache) > self._highlight_cache_max:
                self._highlight_cache.popitem(last=False)
        return result
