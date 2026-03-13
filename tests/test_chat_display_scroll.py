"""Unit tests for ChatDisplay smart scroll behavior and scroll-to-bottom button.

Tests cover:
- Auto-scroll during streaming only fires when user is at/near bottom
- User scrolling away suppresses auto-scroll
- Scroll-to-bottom button visibility tracks user scroll position
- Clicking scroll-to-bottom re-enables auto-scroll
- Non-streaming operations (user message, error) always scroll to bottom
- finish_assistant_message does NOT force scroll
- clear_conversation resets scroll state
"""

from __future__ import annotations

import os
import sys

import pytest

# Force offscreen rendering for headless CI environments
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from qtpy.QtWidgets import QApplication

# Ensure a QApplication exists before any widget is created.
# In a test suite, multiple test files may try to create one,
# so we check first to avoid "QApplication already exists" errors.
app = QApplication.instance()
if app is None:
    app = QApplication(sys.argv)

from spyder_ai_assistant.widgets.chat_display import (
    ChatDisplay,
    _SCROLL_BOTTOM_THRESHOLD_PX,
)


@pytest.fixture
def display():
    """Create a ChatDisplay widget for testing.

    Uses a fixed size to ensure the scrollbar is predictable.
    The widget must be shown (even offscreen) for scroll geometry
    to be computed correctly by Qt.
    """
    w = ChatDisplay()
    # Set a small fixed size so that adding messages creates scrollable
    # content and the scrollbar becomes active.
    w.resize(400, 200)
    w.show()
    # Process events so Qt computes the initial layout and scrollbar range
    app.processEvents()
    return w


def _add_many_messages(display, count=15):
    """Helper: add enough messages to make the display scrollable.

    Creates alternating user/assistant messages with enough text
    to push the document beyond the viewport height.
    """
    for i in range(count):
        if i % 2 == 0:
            display.append_user_message(f"User message #{i} " + "lorem " * 20)
        else:
            display.append_assistant_message(
                f"Assistant message #{i} " + "ipsum " * 30
            )
    app.processEvents()


def _scroll_to_top(display):
    """Helper: scroll the display to the very top (simulating user scroll up)."""
    display.verticalScrollBar().setValue(0)
    app.processEvents()


def _scroll_to_near_bottom(display):
    """Helper: scroll to within the threshold of the bottom."""
    sb = display.verticalScrollBar()
    # Position just inside the threshold
    target = max(0, sb.maximum() - _SCROLL_BOTTOM_THRESHOLD_PX + 5)
    sb.setValue(target)
    app.processEvents()


class TestSmartAutoScroll:
    """Tests for the smart auto-scroll behavior during streaming."""

    def test_auto_scroll_when_at_bottom(self, display):
        """Auto-scroll should fire during streaming when user is at bottom."""
        _add_many_messages(display)
        sb = display.verticalScrollBar()

        # Verify we start at bottom after adding messages
        assert sb.value() >= sb.maximum() - _SCROLL_BOTTOM_THRESHOLD_PX

        # Start streaming — should continue auto-scrolling
        display.start_assistant_message()
        for i in range(10):
            display.append_chunk(f"Token {i} " + "word " * 10 + "\n")
            app.processEvents()

        # Should still be at bottom after streaming tokens
        assert sb.value() >= sb.maximum() - _SCROLL_BOTTOM_THRESHOLD_PX

    def test_no_auto_scroll_when_user_scrolled_away(self, display):
        """Auto-scroll should NOT fire when user has scrolled up."""
        _add_many_messages(display)

        # User scrolls to the top
        _scroll_to_top(display)
        sb = display.verticalScrollBar()
        top_value = sb.value()

        # Verify user_scrolled_away is set
        assert display._user_scrolled_away is True

        # Start streaming — should NOT scroll back to bottom
        display.start_assistant_message()
        for i in range(5):
            display.append_chunk(f"Token {i} ")
            app.processEvents()

        # The scroll position should not have jumped to the bottom.
        # It may have changed slightly due to document reflow, but
        # should not be at the maximum.
        assert sb.value() < sb.maximum() - _SCROLL_BOTTOM_THRESHOLD_PX

    def test_scroll_away_detected_via_scrollbar(self, display):
        """Scrolling away from bottom should set _user_scrolled_away flag."""
        _add_many_messages(display)

        # Initially at bottom
        assert display._user_scrolled_away is False

        # Scroll to top (simulating user scrolling up)
        _scroll_to_top(display)

        assert display._user_scrolled_away is True

    def test_scroll_back_to_bottom_clears_flag(self, display):
        """Scrolling back to bottom should clear _user_scrolled_away flag."""
        _add_many_messages(display)

        # Scroll away
        _scroll_to_top(display)
        assert display._user_scrolled_away is True

        # Scroll back to bottom
        sb = display.verticalScrollBar()
        sb.setValue(sb.maximum())
        app.processEvents()

        assert display._user_scrolled_away is False

    def test_near_bottom_counts_as_at_bottom(self, display):
        """Being within the threshold of bottom should count as 'at bottom'."""
        _add_many_messages(display)

        # Scroll to near-bottom (within threshold)
        _scroll_to_near_bottom(display)

        # Should be considered "at bottom" — flag should be clear
        assert display._user_scrolled_away is False

    def test_user_message_always_scrolls_to_bottom(self, display):
        """Sending a user message should always scroll to bottom."""
        _add_many_messages(display)

        # Scroll away
        _scroll_to_top(display)
        assert display._user_scrolled_away is True

        # Send a new user message — should force scroll to bottom.
        # Multiple processEvents calls are needed because:
        # 1. setHtml() triggers async layout that may increase scrollbar max
        # 2. QTimer.singleShot(0) callbacks need an event loop tick to fire
        # 3. Qt offscreen rendering may take extra passes to settle
        display.append_user_message("New message from user")
        app.processEvents()
        app.processEvents()

        # The logical scroll state should be correct: user is NOT scrolled
        # away, and the scroll button is hidden. The exact scrollbar pixel
        # position may be slightly off in offscreen mode due to Qt's async
        # document layout, so we use a generous threshold (100px) for the
        # position check. The important behavioral check is _user_scrolled_away.
        assert display._user_scrolled_away is False
        assert not display._scroll_btn.isVisible()
        sb = display.verticalScrollBar()
        assert sb.value() >= sb.maximum() - 100

    def test_finish_does_not_force_scroll(self, display):
        """finish_assistant_message should NOT force scroll to bottom."""
        _add_many_messages(display)

        # Start streaming, then scroll away during streaming
        display.start_assistant_message()
        display.append_chunk("Some response text " * 20)
        app.processEvents()

        _scroll_to_top(display)
        assert display._user_scrolled_away is True

        # Finish the message — should NOT snap to bottom
        display.finish_assistant_message()
        app.processEvents()

        sb = display.verticalScrollBar()
        # User should still be scrolled away (not forced to bottom)
        assert sb.value() < sb.maximum() - _SCROLL_BOTTOM_THRESHOLD_PX

    def test_clear_resets_scroll_state(self, display):
        """clear_conversation should reset all scroll tracking state."""
        _add_many_messages(display)
        _scroll_to_top(display)

        assert display._user_scrolled_away is True

        display.clear_conversation()
        app.processEvents()

        assert display._user_scrolled_away is False


class TestScrollToBottomButton:
    """Tests for the floating scroll-to-bottom button."""

    def test_button_hidden_initially(self, display):
        """The scroll button should be hidden when the display is empty."""
        assert display._scroll_btn.isHidden()

    def test_button_hidden_when_at_bottom(self, display):
        """The scroll button should be hidden when at the bottom."""
        _add_many_messages(display)
        # After adding messages, we're at bottom — button should be hidden
        assert display._scroll_btn.isHidden()

    def test_button_shown_when_scrolled_away(self, display):
        """The scroll button should appear when the user scrolls up."""
        _add_many_messages(display)
        _scroll_to_top(display)

        assert display._scroll_btn.isVisible()

    def test_button_hidden_after_manual_scroll_to_bottom(self, display):
        """Button should hide when user manually scrolls back to bottom."""
        _add_many_messages(display)
        _scroll_to_top(display)
        assert display._scroll_btn.isVisible()

        # Scroll back to bottom
        sb = display.verticalScrollBar()
        sb.setValue(sb.maximum())
        app.processEvents()

        assert display._scroll_btn.isHidden()

    def test_button_click_scrolls_to_bottom(self, display):
        """Clicking the scroll button should scroll to bottom."""
        _add_many_messages(display)
        _scroll_to_top(display)
        assert display._user_scrolled_away is True

        # Click the button
        display._on_scroll_to_bottom_clicked()
        app.processEvents()

        sb = display.verticalScrollBar()
        assert sb.value() >= sb.maximum() - _SCROLL_BOTTOM_THRESHOLD_PX
        assert display._user_scrolled_away is False
        assert display._scroll_btn.isHidden()

    def test_button_shown_during_streaming_when_scrolled_away(self, display):
        """Scroll button should be visible during streaming if scrolled up."""
        _add_many_messages(display)

        # Start streaming, then scroll away
        display.start_assistant_message()
        display.append_chunk("Beginning of response...")
        app.processEvents()

        _scroll_to_top(display)
        assert display._scroll_btn.isVisible()

        # Continue streaming — button should remain visible
        display.append_chunk(" more tokens...")
        app.processEvents()
        assert display._scroll_btn.isVisible()

    def test_button_hidden_after_clear(self, display):
        """Scroll button should be hidden after clearing the conversation."""
        _add_many_messages(display)
        _scroll_to_top(display)
        assert display._scroll_btn.isVisible()

        display.clear_conversation()
        app.processEvents()

        assert display._scroll_btn.isHidden()

    def test_button_hidden_after_user_message_resets(self, display):
        """Scroll button hides when user sends a new message."""
        _add_many_messages(display)
        _scroll_to_top(display)
        assert display._scroll_btn.isVisible()

        display.append_user_message("New question")
        app.processEvents()

        assert display._scroll_btn.isHidden()


class TestProgrammaticScrollGuard:
    """Tests for the programmatic scroll guard mechanism."""

    def test_set_document_html_does_not_trigger_scroll_away(self, display):
        """_set_document_html should not set _user_scrolled_away."""
        _add_many_messages(display)

        # Reset to a clean state at bottom
        assert display._user_scrolled_away is False

        # Call _set_document_html directly — this is what setHtml()
        # calls internally and would normally reset scroll to top,
        # which the guard should prevent from setting the flag.
        display._set_document_html(display._html_content)
        app.processEvents()

        # The guard should have prevented the flag from being set
        assert display._user_scrolled_away is False


class TestStreamingRenderOptimization:
    """Tests verifying that streaming does not re-render completed messages.

    The key optimization: _html_content is pre-built HTML for completed
    messages. During streaming, only the streaming buffer is re-rendered
    via _render_markdown(). The completed HTML is simply concatenated.
    """

    def test_html_content_stable_during_streaming(self, display):
        """_html_content should not change during streaming."""
        display.append_user_message("Hello")
        app.processEvents()
        completed_html = display._html_content

        display.start_assistant_message()
        for i in range(5):
            display.append_chunk(f"token{i} ")
            app.processEvents()
            # _html_content should remain the same — only the streaming
            # portion (appended to it in the setHtml call) changes.
            assert display._html_content == completed_html

    def test_html_content_updated_on_finish(self, display):
        """_html_content should grow when streaming finishes."""
        display.append_user_message("Hello")
        app.processEvents()
        before = display._html_content

        display.start_assistant_message()
        display.append_chunk("The answer is 42")
        display.finish_assistant_message()
        app.processEvents()

        # The finished message should now be part of _html_content
        assert len(display._html_content) > len(before)
        assert "42" in display._html_content
