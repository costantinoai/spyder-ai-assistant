"""Visual verification script for Phase 14 Slice A scroll behavior.

Creates a ChatDisplay widget, populates it with a mix of user and
assistant messages (including code blocks), simulates streaming a
long response, and saves a screenshot.

Usage:
    QT_QPA_PLATFORM=offscreen PYTHONPATH=src python tools/visual_tests/test_scroll_behavior.py

Output:
    /tmp/phase14-slice-a-scroll.png

What to verify in the screenshot:
- The display shows messages with proper styling
- Code blocks are rendered with syntax highlighting
- The scroll-to-bottom button is visible (since we scroll up mid-stream)
- The scroll position is NOT at the bottom (smart scroll preserved position)
"""

import os
import sys

# Force offscreen rendering for headless environments
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

# Add src to path for imports
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "src"))

from qtpy.QtWidgets import QApplication

app = QApplication.instance()
if app is None:
    app = QApplication(sys.argv)

from spyder_ai_assistant.widgets.chat_display import ChatDisplay


def main():
    """Run the visual scroll behavior test."""
    display = ChatDisplay()
    display.resize(600, 500)
    display.show()
    app.processEvents()

    # --- Step 1: Add ~15 messages (mix of user and assistant) ---
    sample_messages = [
        ("user", "Hello! Can you help me with Python?"),
        ("assistant", "Of course! I'd be happy to help you with Python. "
         "What would you like to know?"),
        ("user", "How do I read a CSV file?"),
        ("assistant", "You can use `pandas` to read CSV files:\n\n"
         "```python\nimport pandas as pd\n\n"
         "df = pd.read_csv('data.csv')\nprint(df.head())\n```\n\n"
         "This will load the CSV into a DataFrame."),
        ("user", "What about writing to a file?"),
        ("assistant", "Here's how to write data:\n\n"
         "```python\ndf.to_csv('output.csv', index=False)\n```\n\n"
         "The `index=False` prevents writing row numbers."),
        ("user", "Can you show me error handling?"),
        ("assistant", "Here's a robust pattern:\n\n"
         "```python\ntry:\n    df = pd.read_csv('data.csv')\n"
         "except FileNotFoundError:\n    print('File not found!')\n"
         "except pd.errors.EmptyDataError:\n    print('File is empty!')\n"
         "```"),
        ("user", "How about list comprehensions?"),
        ("assistant", "List comprehensions are **powerful** and concise:\n\n"
         "```python\n# Basic\nsquares = [x**2 for x in range(10)]\n\n"
         "# With filter\nevens = [x for x in range(20) if x % 2 == 0]\n\n"
         "# Nested\nmatrix = [[i*j for j in range(5)] for i in range(5)]\n```"),
        ("user", "What about decorators?"),
        ("assistant", "Decorators wrap functions to add behavior:\n\n"
         "```python\nimport functools\n\ndef timer(func):\n"
         "    @functools.wraps(func)\n"
         "    def wrapper(*args, **kwargs):\n"
         "        import time\n"
         "        start = time.time()\n"
         "        result = func(*args, **kwargs)\n"
         "        elapsed = time.time() - start\n"
         "        print(f'{func.__name__} took {elapsed:.2f}s')\n"
         "        return result\n"
         "    return wrapper\n\n"
         "@timer\ndef slow_function():\n"
         "    import time\n    time.sleep(1)\n```"),
        ("user", "Show me async/await"),
        ("assistant", "Here's an async example:\n\n"
         "```python\nimport asyncio\n\nasync def fetch_data(url):\n"
         "    # Simulate network request\n"
         "    await asyncio.sleep(1)\n    return f'Data from {url}'\n\n"
         "async def main():\n    urls = ['url1', 'url2', 'url3']\n"
         "    tasks = [fetch_data(u) for u in urls]\n"
         "    results = await asyncio.gather(*tasks)\n"
         "    for r in results:\n        print(r)\n\n"
         "asyncio.run(main())\n```"),
        ("user", "One more — how about context managers?"),
    ]

    for role, content in sample_messages:
        if role == "user":
            display.append_user_message(content)
        else:
            display.append_assistant_message(content)
        app.processEvents()

    # --- Step 2: Start streaming a long response ---
    display.start_assistant_message()
    app.processEvents()

    # Stream the first few tokens
    streaming_tokens = [
        "Context managers ",
        "are great for ",
        "resource management.\n\n",
        "Here's how to ",
        "create one:\n\n",
        "```python\n",
        "from contextlib import contextmanager\n\n",
        "@contextmanager\n",
        "def managed_resource(name):\n",
        "    print(f'Acquiring {name}')\n",
        "    try:\n",
        "        yield name\n",
        "    finally:\n",
        "        print(f'Releasing {name}')\n",
        "```\n\n",
        "You can also use the ",
        "`__enter__` and ",
        "`__exit__` protocol ",
        "directly by defining a class.",
    ]

    # Stream half the tokens, then scroll up, then stream the rest.
    # This demonstrates the smart scroll behavior.
    half = len(streaming_tokens) // 2
    for token in streaming_tokens[:half]:
        display.append_chunk(token)
        app.processEvents()

    # --- Step 3: Simulate user scrolling up mid-stream ---
    display.verticalScrollBar().setValue(0)
    app.processEvents()

    # Continue streaming — auto-scroll should be suppressed
    for token in streaming_tokens[half:]:
        display.append_chunk(token)
        app.processEvents()

    # --- Step 4: Save screenshot ---
    # The screenshot should show:
    # - Messages at the top of the display (user scrolled there)
    # - The scroll-to-bottom button visible (since user is scrolled away)
    # - Auto-scroll was NOT applied for the second half of tokens
    output_path = "/tmp/phase14-slice-a-scroll.png"
    pixmap = display.grab()
    pixmap.save(output_path)
    print(f"Screenshot saved to {output_path}")

    # Print diagnostic info
    sb = display.verticalScrollBar()
    print(f"Scrollbar value: {sb.value()}")
    print(f"Scrollbar maximum: {sb.maximum()}")
    print(f"User scrolled away: {display._user_scrolled_away}")
    print(f"Scroll button visible: {display._scroll_btn.isVisible()}")
    print(f"Is streaming: {display._is_streaming}")


if __name__ == "__main__":
    main()
