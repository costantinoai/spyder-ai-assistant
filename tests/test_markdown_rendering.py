"""Unit tests for the extended markdown renderer in ChatDisplay.

Tests each markdown element type individually and in combination,
ensuring that code blocks and inline code are never affected by
other markdown transformations.

The ChatDisplay widget requires a QApplication instance, so we
create one for the test session. The QT_QPA_PLATFORM=offscreen
environment variable should be set when running headless.
"""

from __future__ import annotations

import os
import sys

# Ensure offscreen rendering for headless test environments
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from qtpy.QtWidgets import QApplication

# Create QApplication once for the entire test module.
# Qt requires exactly one QApplication per process; creating it
# at module scope ensures it exists before any widget is instantiated.
_app = QApplication.instance() or QApplication(sys.argv)

from spyder_ai_assistant.widgets.chat_display import ChatDisplay


def _make_display():
    """Create a fresh ChatDisplay instance for testing.

    Returns a new widget each time so tests are isolated from
    each other's state (code block tracking, HTML content, etc.).
    """
    return ChatDisplay()


# ===================================================================
# Headings
# ===================================================================

class TestHeadings:
    """Test markdown heading rendering (# through ####)."""

    def test_h1_renders_as_bold_with_large_font(self):
        """H1 should render with font-size:1.4em and bold weight."""
        d = _make_display()
        result = d._render_markdown("# Main Title")
        assert "font-size:1.4em" in result
        assert "font-weight:bold" in result
        assert "Main Title" in result

    def test_h2_renders_with_medium_font(self):
        """H2 should render with font-size:1.2em."""
        d = _make_display()
        result = d._render_markdown("## Section")
        assert "font-size:1.2em" in result
        assert "Section" in result

    def test_h3_renders_with_small_heading_font(self):
        """H3 should render with font-size:1.1em."""
        d = _make_display()
        result = d._render_markdown("### Subsection")
        assert "font-size:1.1em" in result

    def test_h4_renders_with_normal_font_but_bold(self):
        """H4 should render with font-size:1.0em and bold weight."""
        d = _make_display()
        result = d._render_markdown("#### Detail")
        assert "font-size:1.0em" in result
        assert "font-weight:bold" in result

    def test_heading_with_inline_bold(self):
        """Bold text inside a heading should still be rendered."""
        d = _make_display()
        result = d._render_markdown("## A **bold** heading")
        assert "<b>bold</b>" in result

    def test_hash_in_non_heading_context_is_not_a_heading(self):
        """A # not at line start or without space is not a heading."""
        d = _make_display()
        result = d._render_markdown("This is not #a heading")
        # Should not contain heading markup
        assert "font-size:1.4em" not in result
        assert "font-size:1.2em" not in result


# ===================================================================
# Bold, Italic, Strikethrough
# ===================================================================

class TestInlineFormatting:
    """Test inline formatting: bold, italic, strikethrough."""

    def test_bold_double_asterisks(self):
        """**text** should render as <b>text</b>."""
        d = _make_display()
        result = d._render_markdown("This is **bold** text")
        assert "<b>bold</b>" in result

    def test_italic_single_asterisk(self):
        """*text* should render as <i>text</i>."""
        d = _make_display()
        result = d._render_markdown("This is *italic* text")
        assert "<i>italic</i>" in result

    def test_italic_underscore(self):
        """_text_ should render as <i>text</i>."""
        d = _make_display()
        result = d._render_markdown("This is _italic_ text")
        assert "<i>italic</i>" in result

    def test_underscore_in_snake_case_is_not_italic(self):
        """Underscores in snake_case_names should NOT be italicized."""
        d = _make_display()
        result = d._render_markdown("Use my_var_name here")
        # The underscores are part of an identifier; they should not
        # create <i> tags
        assert "<i>" not in result

    def test_bold_and_italic_combined(self):
        """Bold and italic can appear together on the same line."""
        d = _make_display()
        result = d._render_markdown("**bold** and *italic*")
        assert "<b>bold</b>" in result
        assert "<i>italic</i>" in result

    def test_strikethrough(self):
        """~~text~~ should render as <s>text</s>.

        Qt's QTextEdit supports <s> for strikethrough but NOT <del>.
        """
        d = _make_display()
        result = d._render_markdown("This is ~~deleted~~ text")
        assert "<s>deleted</s>" in result

    def test_bold_italic_nested_order(self):
        """Bold wrapping italic should both render correctly."""
        d = _make_display()
        result = d._render_markdown("**bold *and italic* here**")
        assert "<b>" in result
        assert "<i>" in result


# ===================================================================
# Lists
# ===================================================================

class TestLists:
    """Test unordered and ordered list rendering."""

    def test_unordered_list_dash(self):
        """Lines starting with '- ' should render as <ul><li>."""
        d = _make_display()
        result = d._render_markdown("- Item one\n- Item two\n- Item three")
        assert "<ul" in result
        assert "<li>Item one</li>" in result
        assert "<li>Item two</li>" in result
        assert "<li>Item three</li>" in result
        assert "</ul>" in result

    def test_unordered_list_asterisk(self):
        """Lines starting with '* ' should also render as list items."""
        d = _make_display()
        result = d._render_markdown("* First\n* Second")
        assert "<ul" in result
        assert "<li>First</li>" in result

    def test_unordered_list_plus(self):
        """Lines starting with '+ ' should render as list items."""
        d = _make_display()
        result = d._render_markdown("+ Alpha\n+ Beta")
        assert "<ul" in result
        assert "<li>Alpha</li>" in result

    def test_ordered_list(self):
        """Lines starting with '1. ' etc should render as <ol><li>."""
        d = _make_display()
        result = d._render_markdown("1. First step\n2. Second step\n3. Third step")
        assert "<ol" in result
        assert "<li>First step</li>" in result
        assert "<li>Second step</li>" in result
        assert "</ol>" in result

    def test_nested_unordered_list(self):
        """Indented list items should create nested <ul> elements."""
        d = _make_display()
        text = "- Parent\n  - Child\n  - Child 2\n- Parent 2"
        result = d._render_markdown(text)
        # Should have at least two <ul> tags (one for nesting)
        assert result.count("<ul") >= 2
        assert "<li>Child</li>" in result

    def test_list_items_get_inline_formatting(self):
        """List items should support bold/italic within them."""
        d = _make_display()
        result = d._render_markdown("- A **bold** item\n- An *italic* item")
        assert "<b>bold</b>" in result
        assert "<i>italic</i>" in result

    def test_list_closes_on_non_list_line(self):
        """A list should close when followed by non-list content."""
        d = _make_display()
        result = d._render_markdown("- Item\n\nParagraph after")
        assert "</ul>" in result
        assert "Paragraph after" in result


# ===================================================================
# Blockquotes
# ===================================================================

class TestBlockquotes:
    """Test blockquote rendering with > prefix."""

    def test_single_line_blockquote(self):
        """A line starting with '> ' should render in a styled table."""
        d = _make_display()
        result = d._render_markdown("> This is quoted")
        assert "border-left:3px solid" in result
        assert "This is quoted" in result

    def test_multi_line_blockquote(self):
        """Consecutive > lines should be part of the same blockquote."""
        d = _make_display()
        result = d._render_markdown("> Line one\n> Line two")
        assert "Line one" in result
        assert "Line two" in result
        # Should only have one opening table tag
        assert result.count("</td></tr></table>") == 1

    def test_blockquote_with_inline_formatting(self):
        """Blockquote content should support inline markdown."""
        d = _make_display()
        result = d._render_markdown("> This is **important**")
        assert "<b>important</b>" in result

    def test_blockquote_closes_before_normal_text(self):
        """Blockquote should close when non-quoted text follows."""
        d = _make_display()
        result = d._render_markdown("> Quote\n\nNot quoted")
        assert "</td></tr></table>" in result
        assert "Not quoted" in result


# ===================================================================
# Horizontal Rules
# ===================================================================

class TestHorizontalRules:
    """Test horizontal rule rendering."""

    def test_triple_dash_is_hr(self):
        """--- should render as <hr>."""
        d = _make_display()
        result = d._render_markdown("---")
        assert "<hr" in result

    def test_triple_asterisk_is_hr(self):
        """*** should render as <hr>."""
        d = _make_display()
        result = d._render_markdown("***")
        assert "<hr" in result

    def test_triple_underscore_is_hr(self):
        """___ should render as <hr>."""
        d = _make_display()
        result = d._render_markdown("___")
        assert "<hr" in result

    def test_long_dash_is_hr(self):
        """More than three dashes should also render as <hr>."""
        d = _make_display()
        result = d._render_markdown("-----")
        assert "<hr" in result

    def test_two_dashes_is_not_hr(self):
        """Only two dashes should NOT be treated as <hr>."""
        d = _make_display()
        result = d._render_markdown("--")
        assert "<hr" not in result


# ===================================================================
# Tables
# ===================================================================

class TestTables:
    """Test GFM pipe table rendering."""

    def test_simple_table(self):
        """A basic two-column table should render with <table>, <th>, <td>."""
        d = _make_display()
        text = "| Name | Value |\n|------|-------|\n| foo  | 42    |"
        result = d._render_markdown(text)
        assert "<table" in result
        assert "<th" in result
        assert "<td" in result
        assert "Name" in result
        assert "foo" in result
        assert "42" in result

    def test_table_separator_is_not_rendered(self):
        """The |---|---| separator row should not produce visible output."""
        d = _make_display()
        text = "| A | B |\n|---|---|\n| 1 | 2 |"
        result = d._render_markdown(text)
        # The separator should be skipped, so we should not see "---"
        # rendered as cell content
        assert ">-" not in result or "border" in result

    def test_table_closes_after_last_row(self):
        """Table should be properly closed with </table>."""
        d = _make_display()
        text = "| H |\n|---|\n| D |"
        result = d._render_markdown(text)
        assert "</table>" in result

    def test_table_cells_get_inline_formatting(self):
        """Table cell contents should support bold/italic."""
        d = _make_display()
        text = "| **Bold** | *Italic* |\n|------|------|\n| data | data |"
        result = d._render_markdown(text)
        assert "<b>Bold</b>" in result
        assert "<i>Italic</i>" in result


# ===================================================================
# Bare URLs
# ===================================================================

class TestBareUrls:
    """Test auto-linking of bare HTTP(S) URLs."""

    def test_https_url_becomes_link(self):
        """https:// URLs should be wrapped in <a> tags."""
        d = _make_display()
        result = d._render_markdown("Visit https://example.com for info")
        assert "<a href=" in result
        assert "https://example.com" in result

    def test_http_url_becomes_link(self):
        """http:// URLs should also be linked."""
        d = _make_display()
        result = d._render_markdown("See http://example.org")
        assert "<a href=" in result

    def test_url_in_code_block_is_not_linked(self):
        """URLs inside fenced code blocks should NOT be auto-linked."""
        d = _make_display()
        text = "```\nhttps://example.com\n```"
        result = d._render_markdown(text)
        # The URL is inside a code block (protected), so it should
        # not have an <a> tag wrapping it
        assert '<a href="https://example.com"' not in result

    def test_url_in_inline_code_is_not_linked(self):
        """URLs inside `backticks` should NOT be auto-linked."""
        d = _make_display()
        result = d._render_markdown("Use `https://api.example.com` endpoint")
        # The inline code is protected, so URL should not be linked
        assert 'href="https://api.example.com"' not in result


# ===================================================================
# Code Blocks (existing functionality, regression tests)
# ===================================================================

class TestCodeBlocksRegression:
    """Ensure code block rendering is unchanged by new markdown features."""

    def test_fenced_code_block_renders_pre(self):
        """Fenced code blocks should still render as <pre> elements."""
        d = _make_display()
        result = d._render_markdown("```python\nprint('hello')\n```")
        assert "<pre" in result
        assert "print" in result

    def test_code_block_contents_not_formatted(self):
        """Markdown syntax inside code blocks should be literal."""
        d = _make_display()
        text = "```\n# Not a heading\n**not bold**\n- not a list\n```"
        result = d._render_markdown(text)
        # The code block should contain the raw text, not markdown HTML
        assert "font-size:1.4em" not in result  # no heading styling
        # The code block content should be inside <pre>
        assert "<pre" in result

    def test_inline_code_not_affected_by_bold(self):
        """Bold syntax inside inline code should be literal."""
        d = _make_display()
        result = d._render_markdown("Use `**not bold**` here")
        assert "<code" in result
        # The ** should be literal inside the code span, not <b> tags
        assert "**not bold**" in result

    def test_code_block_tracking(self):
        """track_code_blocks should still store code and add links."""
        d = _make_display()
        result = d._render_markdown(
            "```python\nx = 1\n```", track_code_blocks=True
        )
        assert len(d._code_blocks) == 1
        assert "x = 1" in d._code_blocks[0]
        assert "Copy" in result
        assert "Apply" in result


# ===================================================================
# Inline Code Regression
# ===================================================================

class TestInlineCodeRegression:
    """Ensure inline code rendering works with new markdown features."""

    def test_inline_code_renders_with_styling(self):
        """Inline code should have monospace font and background color."""
        d = _make_display()
        result = d._render_markdown("Call `my_function()` here")
        assert "<code" in result
        assert "my_function()" in result
        assert "Courier New" in result

    def test_inline_code_headings_dont_conflict(self):
        """Inline code inside heading text should not break heading."""
        d = _make_display()
        result = d._render_markdown("## Using `config.py`")
        assert "font-size:1.2em" in result
        assert "<code" in result


# ===================================================================
# Combined / Integration Tests
# ===================================================================

class TestCombinedMarkdown:
    """Test complex markdown with multiple element types."""

    def test_heading_followed_by_list(self):
        """A heading followed by a list should both render correctly."""
        d = _make_display()
        text = "## Steps\n\n1. First\n2. Second\n3. Third"
        result = d._render_markdown(text)
        assert "font-size:1.2em" in result  # heading
        assert "<ol" in result  # ordered list
        assert "<li>First</li>" in result

    def test_paragraph_code_block_paragraph(self):
        """Text before and after a code block should render normally."""
        d = _make_display()
        text = "Before code:\n\n```python\nprint(1)\n```\n\nAfter code."
        result = d._render_markdown(text)
        assert "Before code:" in result
        assert "<pre" in result
        assert "After code." in result

    def test_mixed_block_elements(self):
        """Multiple block element types should coexist."""
        d = _make_display()
        text = (
            "# Title\n\n"
            "Some **bold** text.\n\n"
            "- Item 1\n"
            "- Item 2\n\n"
            "> A quote\n\n"
            "---\n\n"
            "| A | B |\n"
            "|---|---|\n"
            "| 1 | 2 |"
        )
        result = d._render_markdown(text)
        assert "font-size:1.4em" in result  # heading
        assert "<b>bold</b>" in result
        assert "<ul" in result
        assert "<li>Item 1</li>" in result
        assert "border-left:3px solid" in result  # blockquote
        assert "<hr" in result
        assert "<table" in result
        assert "<th" in result

    def test_full_assistant_response_simulation(self):
        """Simulate a real LLM response with multiple markdown elements."""
        d = _make_display()
        text = (
            "## Overview\n\n"
            "Here's a quick *summary* of the changes:\n\n"
            "1. Added **new feature**\n"
            "2. Fixed ~~old bug~~\n\n"
            "### Code Example\n\n"
            "```python\ndef hello():\n    print('world')\n```\n\n"
            "> Note: This is a breaking change.\n\n"
            "See https://docs.example.com for more info.\n\n"
            "---\n\n"
            "| Parameter | Default |\n"
            "|-----------|--------|\n"
            "| timeout   | 30s    |\n"
            "| retries   | 3      |"
        )
        result = d._render_markdown(text)
        # Verify all element types are present
        assert "font-size:1.2em" in result  # H2
        assert "font-size:1.1em" in result  # H3
        assert "<i>summary</i>" in result
        assert "<ol" in result
        assert "<b>new feature</b>" in result
        assert "<s>old bug</s>" in result
        assert "<pre" in result  # code block
        assert "border-left:3px solid" in result  # blockquote
        assert "<a href=" in result  # bare URL
        assert "<hr" in result
        assert "<table" in result


# ===================================================================
# Edge Cases
# ===================================================================

class TestEdgeCases:
    """Test edge cases and boundary conditions."""

    def test_empty_string(self):
        """Empty input should produce empty output."""
        d = _make_display()
        result = d._render_markdown("")
        # Should be empty or just whitespace/br tags
        assert "<b>" not in result
        assert "<ul" not in result

    def test_only_newlines(self):
        """Input with only newlines should not crash."""
        d = _make_display()
        result = d._render_markdown("\n\n\n")
        # Should produce <br> tags at most
        assert result is not None

    def test_heading_without_space_is_not_heading(self):
        """'#text' without space after # is not a heading."""
        d = _make_display()
        result = d._render_markdown("#notheading")
        assert "font-weight:bold" not in result

    def test_code_block_with_markdown_inside(self):
        """Markdown syntax inside code blocks must remain literal."""
        d = _make_display()
        text = "```\n## heading\n**bold**\n> quote\n| table |\n```"
        result = d._render_markdown(text)
        # None of the markdown elements should be rendered as HTML
        # inside the code block
        assert "font-size:1.2em" not in result

    def test_multiple_code_blocks(self):
        """Multiple code blocks should each get their own placeholder."""
        d = _make_display()
        text = "```python\na = 1\n```\n\nText\n\n```python\nb = 2\n```"
        result = d._render_markdown(text, track_code_blocks=True)
        assert len(d._code_blocks) == 2
        assert "a = 1" in d._code_blocks[0]
        assert "b = 2" in d._code_blocks[1]

    def test_table_followed_by_text(self):
        """Table should close properly before subsequent text."""
        d = _make_display()
        text = "| A |\n|---|\n| 1 |\n\nAfter table"
        result = d._render_markdown(text)
        assert "</table>" in result
        assert "After table" in result

    def test_blockquote_followed_by_heading(self):
        """Blockquote should close before a heading."""
        d = _make_display()
        text = "> Some quote\n\n## Next Section"
        result = d._render_markdown(text)
        assert "</td></tr></table>" in result
        assert "font-size:1.2em" in result

    def test_list_with_inline_code(self):
        """List items containing inline code should render both."""
        d = _make_display()
        result = d._render_markdown("- Use `pip install` command\n- Run `pytest`")
        assert "<ul" in result
        assert "<code" in result

    def test_consecutive_headings(self):
        """Multiple headings in a row should each render correctly."""
        d = _make_display()
        text = "# Title\n## Subtitle\n### Detail"
        result = d._render_markdown(text)
        assert "font-size:1.4em" in result
        assert "font-size:1.2em" in result
        assert "font-size:1.1em" in result


# ===================================================================
# Mixed List Nesting (Bug fix regression tests)
# ===================================================================

class TestMixedListNesting:
    """Test correct HTML when ordered and unordered lists are nested.

    Bug: When an unordered list contained a nested ordered list (or
    vice versa), both in_ul and in_ol became True simultaneously.
    The de-indent path hardcoded </ul> or </ol> regardless of what
    type was actually opened at that indent level.
    """

    def test_ul_with_nested_ol(self):
        """An unordered list containing a nested ordered list should
        produce <ul> wrapping <ol>, with correct closing tags."""
        d = _make_display()
        text = "- Item A\n  1. Sub one\n  2. Sub two\n- Item B"
        result = d._render_markdown(text)
        # Outer list is unordered
        assert "<ul" in result
        # Inner list is ordered
        assert "<ol" in result
        # Both closing tags must be present
        assert "</ul>" in result
        assert "</ol>" in result
        # Items should be present
        assert "<li>Item A</li>" in result
        assert "<li>Sub one</li>" in result
        assert "<li>Sub two</li>" in result
        assert "<li>Item B</li>" in result
        # The </ol> must appear before </ul> (inner closed first)
        ol_close_pos = result.index("</ol>")
        ul_close_pos = result.rindex("</ul>")
        assert ol_close_pos < ul_close_pos

    def test_ol_with_nested_ul(self):
        """An ordered list containing a nested unordered list should
        produce <ol> wrapping <ul>, with correct closing tags."""
        d = _make_display()
        text = "1. Step one\n  - Detail A\n  - Detail B\n2. Step two"
        result = d._render_markdown(text)
        # Outer list is ordered
        assert "<ol" in result
        # Inner list is unordered
        assert "<ul" in result
        # Both closing tags must be present
        assert "</ol>" in result
        assert "</ul>" in result
        assert "<li>Step one</li>" in result
        assert "<li>Detail A</li>" in result
        assert "<li>Step two</li>" in result
        # The </ul> must appear before </ol> (inner closed first)
        ul_close_pos = result.index("</ul>")
        ol_close_pos = result.rindex("</ol>")
        assert ul_close_pos < ol_close_pos

    def test_triple_mixed_nesting(self):
        """Three levels of mixed nesting: ul > ol > ul."""
        d = _make_display()
        text = (
            "- Top level\n"
            "  1. Numbered child\n"
            "    - Deep bullet\n"
            "  2. Another numbered\n"
            "- Back to top"
        )
        result = d._render_markdown(text)
        # All three list types should be present
        assert result.count("<ul") >= 2  # outer + deep
        assert "<ol" in result           # middle level
        assert "<li>Top level</li>" in result
        assert "<li>Numbered child</li>" in result
        assert "<li>Deep bullet</li>" in result
        assert "<li>Back to top</li>" in result


# ===================================================================
# Partial (Streaming) Code Blocks (Bug fix regression tests)
# ===================================================================

class TestPartialCodeBlocks:
    """Test that unclosed fenced code blocks during streaming are
    protected from markdown processing.

    Bug: During streaming, when a model had sent the opening ```
    but not yet the closing ```, the content was processed by inline
    markdown rules (bold, italic, headings), causing spurious
    formatting that flickered until the closing fence arrived.
    """

    def test_partial_code_block_rendered_as_pre(self):
        """An unclosed code block should render as <pre>, not as
        markdown-formatted text."""
        d = _make_display()
        # Simulate streaming: opening fence + content, no closing fence
        text = "```python\ndef hello():\n    print('world')"
        result = d._render_markdown(text)
        assert "<pre" in result
        # The code content should be present inside the pre block
        assert "hello" in result
        assert "print" in result

    def test_partial_code_block_not_markdown_processed(self):
        """Markdown syntax inside a partial code block must NOT be
        interpreted as formatting."""
        d = _make_display()
        # Content that would be bold/italic/heading if not protected
        text = "```\n# Not a heading\n**not bold**\n- not a list"
        result = d._render_markdown(text)
        # Should be inside <pre>, not formatted
        assert "<pre" in result
        # No heading styling should appear
        assert "font-size:1.4em" not in result
        # No bold tags from the ** content
        assert "<b>not bold</b>" not in result

    def test_partial_code_block_with_language_gets_highlighting(self):
        """A partial code block with a language tag should still get
        Pygments syntax highlighting."""
        d = _make_display()
        text = "```python\nx = 42"
        result = d._render_markdown(text)
        assert "<pre" in result
        # Language label should be present
        assert "python" in result

    def test_complete_block_unaffected_by_partial_pass(self):
        """A complete (closed) code block should still render normally.
        The partial-block pass must not interfere."""
        d = _make_display()
        text = "```python\nprint(1)\n```\n\nDone."
        result = d._render_markdown(text)
        assert "<pre" in result
        assert "Done." in result
