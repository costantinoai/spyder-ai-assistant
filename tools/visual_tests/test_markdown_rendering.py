"""Visual verification script for the markdown renderer.

Creates a ChatDisplay widget, populates it with a user message and an
assistant message containing all supported markdown elements, then saves
screenshots for both light and dark themes.

Usage:
    QT_QPA_PLATFORM=offscreen PYTHONPATH=src python tools/visual_tests/test_markdown_rendering.py

Output:
    /tmp/phase14-slice-b-markdown.png       (light theme)
    /tmp/phase14-slice-b-markdown-dark.png  (dark theme)
"""

import os
import sys

# Ensure offscreen rendering for headless environments
os.environ["QT_QPA_PLATFORM"] = "offscreen"

# Add src to path for local imports
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "src"))

from qtpy.QtWidgets import QApplication
from qtpy.QtGui import QPalette, QColor
from qtpy.QtCore import Qt

from spyder_ai_assistant.widgets.chat_display import ChatDisplay


# The assistant message that exercises every markdown element type.
# This simulates a real LLM response containing a mix of all supported
# markdown features.
FULL_MARKDOWN_RESPONSE = """\
# Markdown Rendering Test

Here is a demonstration of **all supported** markdown features.

## Inline Formatting

You can use **bold**, *italic*, _also italic_, and ~~strikethrough~~ text.
Combine them: **bold and *italic* together**.

## Headings

### This is H3
#### This is H4

## Ordered List

1. First step: install the package
2. Second step: configure the **settings**
3. Third step: run `pytest` to verify

## Unordered List

- Item with *emphasis*
- Item with `inline code`
- Nested items:
  - Sub-item A
  - Sub-item B
- Back to top level

## Code Block

```python
def fibonacci(n):
    \"\"\"Calculate the nth Fibonacci number.\"\"\"
    if n <= 1:
        return n
    return fibonacci(n - 1) + fibonacci(n - 2)

# Example usage
print(fibonacci(10))  # Output: 55
```

## Blockquote

> This is a blockquote. It can contain **bold** and *italic* text.
> It spans multiple lines and has a distinctive left border.

## Horizontal Rule

---

## Table

| Feature | Status | Notes |
|---------|--------|-------|
| Headings | Done | H1-H4 supported |
| **Bold** | Done | Double asterisks |
| *Italic* | Done | Single asterisk or underscore |
| Lists | Done | Ordered and unordered |

## Bare URLs

Visit https://docs.python.org/3/ for Python documentation.
Also see https://spyder-ide.org for the Spyder IDE.

---

That's all the markdown features! Use `_render_markdown()` to render them.
"""


def create_themed_display(is_dark=False):
    """Create a ChatDisplay with explicit light or dark theme.

    Args:
        is_dark: If True, set a dark background to trigger dark theme
                 color selection in ChatDisplay.__init__.

    Returns:
        A ChatDisplay widget sized for screenshot capture.
    """
    # Create the widget first, then set palette before theme detection
    # occurs. Since ChatDisplay detects theme in __init__, we need to
    # set the palette on the application before creating the widget.
    app = QApplication.instance()

    if is_dark:
        # Set a dark palette so ChatDisplay detects dark theme
        palette = QPalette()
        palette.setColor(QPalette.Window, QColor(30, 30, 30))
        palette.setColor(QPalette.Base, QColor(30, 30, 30))
        palette.setColor(QPalette.WindowText, QColor(220, 220, 220))
        palette.setColor(QPalette.Text, QColor(220, 220, 220))
        app.setPalette(palette)
    else:
        # Set a light palette for light theme
        palette = QPalette()
        palette.setColor(QPalette.Window, QColor(245, 245, 245))
        palette.setColor(QPalette.Base, QColor(255, 255, 255))
        palette.setColor(QPalette.WindowText, QColor(30, 30, 30))
        palette.setColor(QPalette.Text, QColor(30, 30, 30))
        app.setPalette(palette)

    # Use a very tall widget to capture all content without scrolling.
    # The full markdown response with all elements needs ~2000px height.
    display = ChatDisplay()
    display.resize(800, 2400)
    display.show()
    return display


def main():
    """Generate markdown rendering screenshots for visual verification."""
    app = QApplication.instance() or QApplication(sys.argv)

    # --- Light theme screenshot ---
    print("Rendering light theme...")
    light_display = create_themed_display(is_dark=False)
    light_display.append_user_message("Show me all markdown features")
    light_display.append_assistant_message(FULL_MARKDOWN_RESPONSE)

    # Scroll to top so the screenshot shows the beginning of the content
    light_display.verticalScrollBar().setValue(0)

    # Process events so the widget fully renders before screenshot
    app.processEvents()

    light_path = "/tmp/phase14-slice-b-markdown.png"
    light_display.grab().save(light_path)
    print(f"Light theme saved to: {light_path}")

    # --- Dark theme screenshot ---
    print("Rendering dark theme...")
    dark_display = create_themed_display(is_dark=True)
    dark_display.append_user_message("Show me all markdown features")
    dark_display.append_assistant_message(FULL_MARKDOWN_RESPONSE)

    # Scroll to top
    dark_display.verticalScrollBar().setValue(0)

    app.processEvents()

    dark_path = "/tmp/phase14-slice-b-markdown-dark.png"
    dark_display.grab().save(dark_path)
    print(f"Dark theme saved to: {dark_path}")

    print("Done. Review the screenshots for visual correctness.")


if __name__ == "__main__":
    main()
