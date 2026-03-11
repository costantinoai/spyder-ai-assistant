# PyQt5 Patterns for Spyder Plugin Development

> Compiled: 2026-03-10
> Note: Spyder uses `qtpy` as an abstraction layer. Import from `qtpy` instead of `PyQt5` directly.

---

## 1. Import Convention (qtpy)

Spyder uses `qtpy` so the plugin works with both PyQt5 and PySide2:

```python
# DO this (Spyder convention):
from qtpy.QtWidgets import QWidget, QVBoxLayout, QTextEdit, QPushButton
from qtpy.QtCore import Qt, Signal, QThread, QObject, QMutex, QMutexLocker
from qtpy.QtGui import QFont, QTextCursor

# DON'T do this:
# from PyQt5.QtWidgets import ...
```

---

## 2. QThread Worker Pattern (for Ollama calls)

Never do blocking I/O on the main thread. Use a QThread + worker QObject.

```python
from qtpy.QtCore import QObject, QThread, Signal, QMutex, QMutexLocker

class OllamaWorker(QObject):
    """Worker that runs on a background QThread."""
    # Signals to communicate with main thread
    chunk_received = Signal(str)       # Streaming: each token
    response_ready = Signal(str)       # Complete response
    error_occurred = Signal(str)       # Error message
    status_changed = Signal(str)       # "generating", "idle", etc.

    def __init__(self):
        super().__init__()
        self._abort = False
        self._mutex = QMutex()

    def send_chat(self, model, messages, options=None):
        """Called via signal from main thread. Runs on worker thread."""
        from ollama import Client, ResponseError

        with QMutexLocker(self._mutex):
            self._abort = False

        self.status_changed.emit("generating")
        try:
            client = Client(host="http://localhost:11434")
            stream = client.chat(
                model=model,
                messages=messages,
                stream=True,
                options=options or {},
            )

            chunks = []
            for part in stream:
                with QMutexLocker(self._mutex):
                    if self._abort:
                        self.status_changed.emit("idle")
                        return

                content = part.message.content
                chunks.append(content)
                self.chunk_received.emit(content)

            full_response = "".join(chunks)
            self.response_ready.emit(full_response)

        except ResponseError as e:
            self.error_occurred.emit(f"Ollama error: {e.error}")
        except ConnectionError:
            self.error_occurred.emit("Cannot connect to Ollama. Is it running?")
        finally:
            self.status_changed.emit("idle")

    def abort(self):
        """Thread-safe abort request."""
        with QMutexLocker(self._mutex):
            self._abort = True


# Usage in the widget:
class ChatWidget(PluginMainWidget):
    # Signal to send work to the worker thread
    sig_send_chat = Signal(str, list, dict)  # model, messages, options

    def setup(self):
        # Create thread and worker
        self._thread = QThread()
        self._worker = OllamaWorker()
        self._worker.moveToThread(self._thread)

        # Connect signals
        self.sig_send_chat.connect(self._worker.send_chat)
        self._worker.chunk_received.connect(self._on_chunk)
        self._worker.response_ready.connect(self._on_response)
        self._worker.error_occurred.connect(self._on_error)

        self._thread.start()

    def _on_chunk(self, text):
        """Append streaming token to chat display."""
        self.chat_display.moveCursor(QTextCursor.End)
        self.chat_display.insertPlainText(text)

    def _on_response(self, full_text):
        """Handle complete response."""
        self.messages.append({"role": "assistant", "content": full_text})

    def _on_error(self, error_msg):
        """Show error in chat display."""
        self.chat_display.append(f"\n[Error] {error_msg}\n")

    def send_message(self, user_text):
        """Send a message to the LLM."""
        self.messages.append({"role": "user", "content": user_text})
        self.sig_send_chat.emit(self.model, self.messages, self.options)
```

---

## 3. Signals and Slots

```python
from qtpy.QtCore import Signal, QObject

class MyObject(QObject):
    # Define custom signals as class attributes
    data_ready = Signal(str)           # One string argument
    progress = Signal(int)             # One int argument
    completed = Signal()               # No arguments
    result = Signal(str, int, dict)    # Multiple arguments

    def do_work(self):
        self.progress.emit(50)
        self.data_ready.emit("result")
        self.completed.emit()

# Connect signals to slots (methods):
obj = MyObject()
obj.data_ready.connect(some_function)
obj.progress.connect(lambda val: print(f"{val}%"))

# Disconnect:
obj.data_ready.disconnect(some_function)
```

---

## 4. Layout Patterns

### Chat panel layout:

```python
from qtpy.QtWidgets import (
    QVBoxLayout, QHBoxLayout, QTextEdit, QPlainTextEdit,
    QPushButton, QSplitter, QWidget, QComboBox
)
from qtpy.QtCore import Qt

def build_chat_ui(self):
    """Build the chat panel layout."""
    layout = QVBoxLayout()

    # Model selector toolbar
    toolbar_layout = QHBoxLayout()
    self.model_combo = QComboBox()
    toolbar_layout.addWidget(QLabel("Model:"))
    toolbar_layout.addWidget(self.model_combo, stretch=1)
    layout.addLayout(toolbar_layout)

    # Chat display (read-only, rich text)
    self.chat_display = QTextEdit()
    self.chat_display.setReadOnly(True)
    self.chat_display.setFont(QFont("Monospace", 10))

    # Input area
    self.input_field = QPlainTextEdit()
    self.input_field.setMaximumHeight(100)
    self.input_field.setPlaceholderText("Type a message... (Shift+Enter for newline)")

    # Splitter between chat and input
    splitter = QSplitter(Qt.Vertical)
    splitter.addWidget(self.chat_display)
    splitter.addWidget(self.input_field)
    splitter.setStretchFactor(0, 4)  # Chat gets 80%
    splitter.setStretchFactor(1, 1)  # Input gets 20%
    layout.addWidget(splitter)

    # Buttons
    button_layout = QHBoxLayout()
    self.send_btn = QPushButton("Send")
    self.stop_btn = QPushButton("Stop")
    self.stop_btn.setEnabled(False)
    self.clear_btn = QPushButton("Clear")
    button_layout.addWidget(self.clear_btn)
    button_layout.addStretch()
    button_layout.addWidget(self.stop_btn)
    button_layout.addWidget(self.send_btn)
    layout.addLayout(button_layout)

    self.setLayout(layout)
```

---

## 5. Keyboard Shortcuts in Widgets

```python
from qtpy.QtWidgets import QShortcut
from qtpy.QtGui import QKeySequence

# In setup():
send_shortcut = QShortcut(QKeySequence("Ctrl+Return"), self.input_field)
send_shortcut.activated.connect(self.send_message)
```

Or override `keyPressEvent` on the input field:

```python
class ChatInput(QPlainTextEdit):
    submit_requested = Signal()

    def keyPressEvent(self, event):
        if event.key() in (Qt.Key_Return, Qt.Key_Enter):
            if event.modifiers() & Qt.ShiftModifier:
                # Shift+Enter: insert newline
                super().keyPressEvent(event)
            else:
                # Enter: submit
                self.submit_requested.emit()
        else:
            super().keyPressEvent(event)
```

---

## 6. Markdown Rendering

For rendering LLM responses with code blocks, use QTextEdit with HTML:

```python
def append_markdown(self, text):
    """Simple markdown-to-HTML for chat display."""
    import re
    # Code blocks
    text = re.sub(
        r'```(\w+)?\n(.*?)```',
        r'<pre style="background:#2d2d2d;color:#f8f8f2;padding:8px;border-radius:4px;">\2</pre>',
        text, flags=re.DOTALL
    )
    # Inline code
    text = re.sub(r'`([^`]+)`', r'<code style="background:#3d3d3d;padding:2px 4px;">\1</code>', text)
    # Bold
    text = re.sub(r'\*\*(.+?)\*\*', r'<b>\1</b>', text)
    # Newlines
    text = text.replace('\n', '<br>')

    self.chat_display.append(text)
```

For production, consider using a proper markdown library (`markdown` or `mistune`) to convert to HTML.

---

## 7. Timer-based Debouncing (for completions)

```python
from qtpy.QtCore import QTimer

class CompletionDebouncer:
    """Debounce completion requests to avoid flooding the LLM."""

    def __init__(self, delay_ms=500):
        self._timer = QTimer()
        self._timer.setSingleShot(True)
        self._timer.timeout.connect(self._fire)
        self._delay = delay_ms
        self._callback = None
        self._args = None

    def request(self, callback, *args):
        """Schedule a completion request. Resets timer on each call."""
        self._callback = callback
        self._args = args
        self._timer.start(self._delay)

    def cancel(self):
        self._timer.stop()

    def _fire(self):
        if self._callback:
            self._callback(*self._args)
```
