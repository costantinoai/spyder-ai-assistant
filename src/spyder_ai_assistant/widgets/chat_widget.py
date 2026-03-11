"""Main chat widget for the AI Chat plugin.

This is the PluginMainWidget that provides the dockable chat pane in
Spyder. Supports multiple chat sessions as tabs, each with its own
conversation history and display. All sessions share the same background
worker, model selector, and input area.

Architecture:
    UI (main thread) ──signals──> OllamaWorker (background QThread)
    OllamaWorker ──signals──> UI (main thread)

Multi-tab design:
    - ChatSession: holds a ChatDisplay + messages list for one conversation
    - QTabWidget: manages multiple ChatSessions with closable tabs
    - The input, buttons, worker, and toolbar are shared across all tabs
    - Streaming always targets the tab that initiated the request
"""

import logging
import os
from dataclasses import dataclass
from datetime import datetime

from qtpy.QtCore import Qt, Signal, QThread
from qtpy.QtWidgets import (
    QVBoxLayout, QHBoxLayout, QSplitter, QPushButton, QComboBox, QLabel,
    QFileDialog, QTabWidget, QToolButton,
)

from spyder.api.widgets.main_widget import PluginMainWidget

from spyder_ai_assistant.backend.worker import OllamaWorker
from spyder_ai_assistant.utils.context import build_system_context_block
from spyder_ai_assistant.utils.runtime_bridge import (
    MAX_RUNTIME_TOOL_CALLS_PER_TURN,
    build_runtime_bridge_instructions,
    format_runtime_observation,
    parse_runtime_request,
)
from spyder_ai_assistant.widgets.chat_input import ChatInput
from spyder_ai_assistant.widgets.chat_display import ChatDisplay

logger = logging.getLogger(__name__)


def _normalize_chat_temperature(value):
    """Normalize stored chat temperature values to Ollama's expected range.

    The preferences UI historically exposed "temperature x10" values
    (e.g. 5 meaning 0.5), while the runtime config used decimal floats.
    Phase 1 keeps backward compatibility by accepting either format.
    """
    try:
        numeric = float(value)
    except (TypeError, ValueError):
        return 0.5

    if numeric > 2.0:
        numeric = numeric / 10.0

    return max(0.0, min(numeric, 2.0))


class ChatSessionStore:
    """Track chat sessions by their display widget instead of tab index."""

    def __init__(self):
        self._by_widget = {}

    def add(self, session):
        """Register a new session by its display widget."""
        self._by_widget[session.display] = session

    def get_for_widget(self, widget):
        """Return the session bound to a display widget, if any."""
        return self._by_widget.get(widget)

    def get_for_index(self, tab_widget, index):
        """Return the session currently shown at a tab index."""
        return self.get_for_widget(tab_widget.widget(index))

    def remove_for_widget(self, widget):
        """Forget the session associated with a display widget."""
        return self._by_widget.pop(widget, None)

    def index_of(self, tab_widget, session):
        """Return the current tab index for a session, or -1."""
        for index in range(tab_widget.count()):
            if tab_widget.widget(index) is session.display:
                return index
        return -1


# ---------------------------------------------------------------------------
# ChatSession — one conversation tab
# ---------------------------------------------------------------------------

class ChatSession:
    """State for a single chat conversation tab.

    Bundles a ChatDisplay widget with its conversation history. Each tab
    in the QTabWidget owns one ChatSession. The widget handles the display;
    the messages list is the authoritative conversation state for API calls.

    Attributes:
        display: The ChatDisplay widget for this tab.
        messages: List of role/content dicts (user + assistant messages).
        title: Short label for the tab (auto-generated or user-set).
    """

    # Counter for auto-naming new tabs ("Chat 1", "Chat 2", ...)
    _counter = 0

    def __init__(self, parent=None):
        ChatSession._counter += 1
        self.display = ChatDisplay(parent)
        self.messages = []
        self.title = f"Chat {ChatSession._counter}"


@dataclass
class ChatTurnState:
    """Internal per-turn state used for runtime inspection loops."""

    session: ChatSession
    request_messages: list
    tool_calls: int = 0


# ---------------------------------------------------------------------------
# ChatWidget — the main dockable pane
# ---------------------------------------------------------------------------

class ChatWidget(PluginMainWidget):
    """Main widget for the AI Chat dockable pane.

    Provides a tabbed chat interface with:
    - Multiple chat sessions (tabs) with independent conversation histories
    - Model selection dropdown in the toolbar
    - Shared text input with Enter-to-send behavior
    - Stop/New/Export/Send controls
    - Status indicator showing generation state and speed

    All Ollama communication happens on a background QThread to keep
    the UI responsive during LLM inference.
    """

    # Signal to dispatch a chat request to the background worker.
    # Args: model name (str), messages list, options dict
    sig_send_chat = Signal(str, list, dict)

    # Signal to request model listing from the background worker
    sig_list_models = Signal()
    # Signal to update the Ollama host on the worker thread
    sig_update_host = Signal(str)

    # Emitted when the user clicks "Insert into editor" on a code block.
    # Bubbles up from ChatDisplay so the plugin can handle insertion.
    sig_insert_code = Signal(str)

    # Enable the built-in loading spinner in the corner toolbar
    ENABLE_SPINNER = True

    def __init__(self, name, plugin, parent=None):
        super().__init__(name, plugin, parent)

        # Whether the LLM is currently generating a response
        self._generating = False

        # Currently selected model identifier (from combo box data)
        self._current_model = ""

        # Callable that returns editor context dict (set by plugin).
        # When set, _send_message() enriches the system prompt with
        # the current file content and cursor position.
        self._context_provider = None
        # Callable that executes one runtime inspection request.
        self._runtime_request_executor = None

        # The session that initiated the current generation.
        # Streaming tokens and the final response are routed here,
        # even if the user switches tabs mid-generation.
        self._generating_session = None
        # Hidden per-turn state used when the model asks for runtime data.
        self._pending_turn = None

    # --- PluginMainWidget interface ---

    def get_title(self):
        """Widget title shown in the pane header and View > Panes menu."""
        return "AI Chat"

    def setup(self):
        """Build the UI and start the background worker thread.

        Called once during widget initialization by Spyder's plugin
        infrastructure. Creates all child widgets, sets up the toolbar,
        connects signals between the UI and the background worker, and
        starts the worker thread.
        """
        # --- Model selector in the main toolbar ---
        self.model_combo = QComboBox(self)
        self.model_combo.setMinimumWidth(200)
        self.model_combo.setToolTip("Select the AI model for chat")
        self.model_combo.ID = "ai_chat_model_selector"

        self.status_label = QLabel("Connecting...")
        self.status_label.ID = "ai_chat_status_label"

        # Context label: shows current file and cursor line (e.g. "main.py:42")
        self.context_label = QLabel("")
        self.context_label.ID = "ai_chat_context_label"
        self.context_label.setMinimumWidth(100)
        self.context_label.setToolTip("Current editor file and cursor position")

        toolbar = self.get_main_toolbar()
        self.add_item_to_toolbar(
            self.model_combo, toolbar=toolbar, section="main",
        )
        self.add_item_to_toolbar(
            self.context_label, toolbar=toolbar, section="context",
        )
        self.add_item_to_toolbar(
            self.status_label, toolbar=toolbar, section="status",
        )

        # --- Options menu actions (hamburger menu in corner toolbar) ---
        refresh_action = self.create_action(
            "ai_chat_refresh_models",
            text="Refresh Models",
            triggered=self._refresh_models,
        )
        new_tab_action = self.create_action(
            "ai_chat_new_tab",
            text="New Chat Tab",
            triggered=self._add_new_tab,
        )
        export_action = self.create_action(
            "ai_chat_export",
            text="Export Chat...",
            triggered=self._export_chat,
        )
        options_menu = self.get_options_menu()
        self.add_item_to_menu(refresh_action, menu=options_menu)
        self.add_item_to_menu(new_tab_action, menu=options_menu)
        self.add_item_to_menu(export_action, menu=options_menu)

        # --- Tab widget for multiple chat sessions ---
        self._tab_widget = QTabWidget(self)
        self._tab_widget.setTabsClosable(True)
        self._tab_widget.setMovable(True)
        self._tab_widget.tabCloseRequested.connect(self._close_tab)

        # "+" button in the tab bar corner to create new tabs
        add_tab_btn = QToolButton(self)
        add_tab_btn.setText("+")
        add_tab_btn.setToolTip("New chat session")
        add_tab_btn.clicked.connect(self._add_new_tab)
        self._tab_widget.setCornerWidget(add_tab_btn, Qt.TopRightCorner)

        # Track sessions by their display widget so tab moves do not
        # corrupt the mapping between visible tabs and conversations.
        self._sessions = ChatSessionStore()

        # Create the first tab
        self._add_new_tab()

        # Chat input: text field with Enter-to-send, Shift+Enter for newline
        self.chat_input = ChatInput(self)

        # Vertical splitter between tabs and input so the user can
        # resize the input area by dragging the divider
        splitter = QSplitter(Qt.Vertical)
        splitter.addWidget(self._tab_widget)
        splitter.addWidget(self.chat_input)
        splitter.setStretchFactor(0, 4)  # Tabs get ~80% of space
        splitter.setStretchFactor(1, 1)  # Input gets ~20% of space

        # Button bar: [New] [Export] ----stretch---- [Stop] [Send]
        button_layout = QHBoxLayout()
        self.new_btn = QPushButton("New")
        self.new_btn.setToolTip("Open a new chat tab")
        self.export_btn = QPushButton("Export")
        self.export_btn.setToolTip("Export the current conversation to a file")
        self.stop_btn = QPushButton("Stop")
        self.send_btn = QPushButton("Send")
        self.stop_btn.setEnabled(False)
        button_layout.addWidget(self.new_btn)
        button_layout.addWidget(self.export_btn)
        button_layout.addStretch()
        button_layout.addWidget(self.stop_btn)
        button_layout.addWidget(self.send_btn)

        # Assemble the content layout (splitter + buttons)
        content_layout = QVBoxLayout()
        content_layout.addWidget(splitter)
        content_layout.addLayout(button_layout)

        self.setLayout(content_layout)

        # --- Background worker thread ---
        host = self.get_conf(
            "ollama_host", default="http://localhost:11434"
        )
        self._thread = QThread(None)
        self._worker = OllamaWorker(host=host)
        self._worker.moveToThread(self._thread)

        # Main thread → worker: dispatch work requests via signals
        self.sig_send_chat.connect(self._worker.send_chat)
        self.sig_list_models.connect(self._worker.list_models)
        self.sig_update_host.connect(self._worker.update_host)

        # Worker → main thread: receive results via signals
        self._worker.chunk_received.connect(self._on_chunk)
        self._worker.response_ready.connect(self._on_response)
        self._worker.models_listed.connect(self._on_models_listed)
        self._worker.error_occurred.connect(self._on_error)
        self._worker.status_changed.connect(self._on_status_changed)

        # UI signal connections
        self.chat_input.submit_requested.connect(self._send_message)
        self.send_btn.clicked.connect(self._send_message)
        self.stop_btn.clicked.connect(self._stop_generation)
        self.new_btn.clicked.connect(self._add_new_tab)
        self.export_btn.clicked.connect(self._export_chat)
        self.model_combo.currentIndexChanged.connect(
            self._on_model_changed
        )

        # Start the worker thread and fetch available models
        self._thread.start()
        self.sig_list_models.emit()

    def update_actions(self):
        """Called by Spyder when the widget gains/loses focus.

        Required by the PluginMainWidget interface. No focus-dependent
        actions to update.
        """
        pass

    # --- Tab management ---

    @property
    def _active_session(self):
        """The ChatSession for the currently visible tab."""
        return self._sessions.get_for_widget(self._tab_widget.currentWidget())

    @property
    def chat_display(self):
        """The ChatDisplay of the active tab.

        Convenience property for backward compatibility with code that
        accesses self.chat_display directly (e.g., plugin error messages).
        """
        session = self._active_session
        return session.display if session else None

    def _add_new_tab(self):
        """Create a new chat session tab and switch to it."""
        session = ChatSession(parent=self._tab_widget)

        # Connect the "Insert into editor" signal from this tab's display
        session.display.sig_insert_code_requested.connect(
            self.sig_insert_code
        )

        idx = self._tab_widget.addTab(session.display, session.title)
        self._sessions.add(session)
        self._tab_widget.setCurrentIndex(idx)
        logger.debug("New chat tab: %s (index %d)", session.title, idx)

    def _close_tab(self, index):
        """Close a chat tab. Prevents closing the last tab.

        Args:
            index: Tab index to close.
        """
        # Don't close the last tab — always keep at least one
        if self._tab_widget.count() <= 1:
            # Instead of closing, just clear the conversation
            session = self._sessions.get_for_index(self._tab_widget, index)
            if session:
                session.messages.clear()
                session.display.clear_conversation()
            return

        # Don't close a tab that's currently generating
        session = self._sessions.get_for_index(self._tab_widget, index)
        if session is self._generating_session:
            return

        # Remove the tab and clean up the session
        widget = self._tab_widget.widget(index)
        self._tab_widget.removeTab(index)
        self._sessions.remove_for_widget(widget)

    # --- Worker signal handlers (called on main thread) ---

    def _on_chunk(self, text):
        """Append a streaming token to the generating session's display.

        Routes the token to the session that started the request,
        not necessarily the currently visible tab. This allows the user
        to switch tabs while a response is streaming.
        """
        session = self._generating_session
        if session:
            session.display.append_chunk(text)

    def _on_response(self, full_text, metrics):
        """Handle a completed LLM response.

        Finalizes the streaming display, saves the response to the
        generating session's history, and updates status with speed.
        Strips <think>...</think> blocks from the saved history.
        """
        session = self._generating_session
        clean_text = self._strip_thinking(full_text)
        runtime_request = parse_runtime_request(clean_text)

        if session and runtime_request is not None:
            if self._handle_runtime_request(session, runtime_request):
                return

        if session and not clean_text.strip():
            logger.warning(
                "Chat model %s returned an empty response",
                self._current_model or "<unknown>",
            )
            session.display.finish_assistant_message()
            session.display.append_error(
                "The selected chat model returned an empty response. "
                "Try another chat model."
            )
            self._pending_turn = None
            self._set_generating(False)
            self.status_label.setText("Empty response")
            return

        if session:
            session.display.finish_assistant_message()
            session.messages.append({
                "role": "assistant", "content": clean_text
            })
            self._maybe_rename_tab(session)

        self._pending_turn = None
        self._set_generating(False)

        # Display generation speed if metrics are available
        eval_count = metrics.get("eval_count", 0)
        eval_duration = metrics.get("eval_duration", 0)
        if eval_count and eval_duration:
            tokens_per_sec = eval_count / (eval_duration / 1e9)
            self.status_label.setText(
                f"Ready ({tokens_per_sec:.1f} tok/s)"
            )
        else:
            self.status_label.setText("Ready")

    def _on_models_listed(self, models):
        """Populate the model dropdown with available models."""
        previous = self.model_combo.currentData()

        self.model_combo.blockSignals(True)
        self.model_combo.clear()
        for m in models:
            # Show parameter count and VRAM size for quick reference
            size_gb = m.get("size_gb", 0)
            display = f"{m['name']} ({m['parameter_size']}, {size_gb}GB)"
            self.model_combo.addItem(display, m["name"])

            # Tooltip with full model details
            idx = self.model_combo.count() - 1
            tooltip = (
                f"Model: {m['name']}\n"
                f"Family: {m.get('family', 'unknown')}\n"
                f"Parameters: {m['parameter_size']}\n"
                f"Quantization: {m.get('quantization', 'unknown')}\n"
                f"Size: {size_gb} GB"
            )
            self.model_combo.setItemData(idx, tooltip, Qt.ToolTipRole)
        self._select_default_model(previous)
        self.model_combo.blockSignals(False)

        self._current_model = self.model_combo.currentData() or ""

        if models:
            self.status_label.setText("Ready")
        else:
            self.status_label.setText("No models available")

    def _on_error(self, message):
        """Handle an error from the worker.

        Routes the error to the generating session's display.
        """
        session = self._generating_session
        if session:
            session.display.finish_assistant_message()
            session.display.append_error(message)
        self._pending_turn = None
        self._set_generating(False)
        self.status_label.setText("Error")

    def _on_status_changed(self, status):
        """Update the status label for active worker states."""
        labels = {
            "generating": "Generating...",
            "loading_models": "Loading models...",
        }
        label = labels.get(status)
        if label:
            self.status_label.setText(label)

    # --- UI action handlers ---

    def _send_message(self):
        """Send the current input text to the LLM.

        Dispatches the message on the active tab's conversation.
        The response will be routed back to this tab's display
        even if the user switches tabs mid-generation.
        """
        if self._generating:
            return

        session = self._active_session
        if session is None:
            return

        text = self.chat_input.peek_text()
        if not text:
            return

        if not self._current_model:
            session.display.append_error(
                "No model selected. Is Ollama running? "
                "Try 'Refresh Models' from the options menu."
            )
            return

        self.chat_input.clear_text()

        # Add user message to the active tab's display and history
        session.display.append_user_message(text)
        session.messages.append({"role": "user", "content": text})

        # Build system prompt with editor/project context
        system_prompt = self.get_conf(
            "chat_system_prompt",
            default=(
                "You are a helpful AI coding assistant working inside "
                "the Spyder IDE. Be concise and provide code examples "
                "when relevant."
            ),
        )
        system_prompt = (
            f"{system_prompt}\n\n"
            f"{build_runtime_bridge_instructions()}"
        )

        if self._context_provider:
            full_context = self._context_provider()
            context_block = build_system_context_block(
                context=full_context.get("context", {}),
                open_files=full_context.get("open_files"),
                project=full_context.get("project"),
                console=full_context.get("console"),
            )
            if context_block:
                system_prompt = f"{system_prompt}\n\n{context_block}"

        messages = (
            [{"role": "system", "content": system_prompt}]
            + session.messages
        )

        options = {
            "temperature": _normalize_chat_temperature(
                self.get_conf("chat_temperature", default=0.5)
            ),
            "num_predict": self.get_conf("max_tokens", default=1024),
        }

        # Lock this session as the generation target. Streaming tokens
        # and the final response will be routed here regardless of
        # which tab is visible.
        self._generating_session = session
        self._pending_turn = ChatTurnState(
            session=session,
            request_messages=list(messages),
        )
        session.display.start_assistant_message()
        self._set_generating(True)

        self.sig_send_chat.emit(self._current_model, messages, options)

    def _stop_generation(self):
        """Abort the current LLM generation."""
        self._worker.abort()
        session = self._generating_session
        if session:
            session.display.finish_assistant_message()
        self._pending_turn = None
        self._set_generating(False)
        self.status_label.setText("Stopped")

    def _refresh_models(self):
        """Re-fetch the model list from Ollama."""
        self.sig_list_models.emit()

    def _export_chat(self):
        """Export the active tab's conversation to a Markdown file."""
        session = self._active_session
        if session is None or not session.messages:
            if session:
                session.display.append_error("No messages to export.")
            return

        model_short = self._current_model.split("/")[-1].split(":")[0]
        default_name = (
            f"ai-chat-{model_short}-"
            f"{datetime.now().strftime('%Y%m%d-%H%M%S')}.md"
        )

        filepath, _ = QFileDialog.getSaveFileName(
            self,
            "Export Chat",
            default_name,
            "Markdown (*.md);;Text (*.txt);;All Files (*)",
        )
        if not filepath:
            return

        lines = [f"# AI Chat Export — {datetime.now().strftime('%Y-%m-%d %H:%M')}\n"]
        lines.append(f"**Model:** {self._current_model}\n")

        for msg in session.messages:
            role = msg["role"].capitalize()
            content = msg["content"]
            if role == "User":
                lines.append(f"## You\n\n{content}\n")
            elif role == "Assistant":
                lines.append(f"## AI\n\n{content}\n")

        try:
            with open(filepath, "w", encoding="utf-8") as f:
                f.write("\n".join(lines))
            self.status_label.setText(
                f"Exported to {os.path.basename(filepath)}"
            )
        except OSError as e:
            session.display.append_error(f"Export failed: {e}")

    def _on_model_changed(self, index):
        """Update the current model when the combo selection changes."""
        self._current_model = self.model_combo.currentData() or ""

    # --- Public API (called by plugin) ---

    def set_context_provider(self, provider):
        """Set the callable that provides editor context.

        Args:
            provider: Callable returning a dict with editor context, or
                empty dict if no editor is active.
        """
        self._context_provider = provider

    def set_runtime_request_executor(self, executor):
        """Set the callable that executes one runtime inspection request."""
        self._runtime_request_executor = executor

    def update_toolbar_context(self, context_str):
        """Update the toolbar context label with the current file info.

        Args:
            context_str: String like "main.py:42", or "" to clear.
        """
        self.context_label.setText(context_str)

    def update_ollama_host(self, host):
        """Update the chat worker host and refresh the model list."""
        self.status_label.setText("Connecting...")
        self.sig_update_host.emit(host)
        self.sig_list_models.emit()

    def send_with_prompt(self, prompt):
        """Inject a prompt into the input and send it immediately.

        Used by context menu actions (Explain, Fix, Add Docstring) to
        populate the chat input with a pre-built prompt containing
        the selected code and then trigger generation.

        Args:
            prompt: The full prompt text to send.
        """
        self.chat_input.setPlainText(prompt)
        self._send_message()

    # --- Internal helpers ---

    def _maybe_rename_tab(self, session):
        """Auto-rename a tab based on the first user message.

        Uses the first ~30 characters of the first user message as the
        tab title, which is more descriptive than "Chat N". Only renames
        if the tab still has its default auto-generated title.

        Args:
            session: The ChatSession to potentially rename.
        """
        # Only rename if still using the default title
        if not session.title.startswith("Chat "):
            return

        # Find the first user message for a descriptive title
        for msg in session.messages:
            if msg["role"] == "user":
                # Truncate to ~30 chars for the tab label
                short = msg["content"][:30].strip()
                if len(msg["content"]) > 30:
                    short += "..."
                # Replace newlines with spaces for the tab label
                short = short.replace("\n", " ")
                session.title = short

                # Update the tab label in the QTabWidget
                index = self._sessions.index_of(self._tab_widget, session)
                if index >= 0:
                    self._tab_widget.setTabText(index, short)
                break

    @staticmethod
    def _strip_thinking(text):
        """Remove <think>...</think> blocks from text.

        Used to clean the assistant response before saving it to
        conversation history. Thinking tokens are displayed in the UI
        but shouldn't be sent back to the model in future turns.

        Args:
            text: Raw assistant response that may contain thinking blocks.

        Returns:
            Text with thinking blocks removed and leading whitespace stripped.
        """
        import re
        cleaned = re.sub(r"<think>.*?</think>", "", text, flags=re.DOTALL)
        return cleaned.lstrip("\n")

    def _set_generating(self, generating):
        """Update UI controls for generation-in-progress state."""
        self._generating = generating
        self.send_btn.setEnabled(not generating)
        self.stop_btn.setEnabled(generating)
        self.chat_input.setEnabled(not generating)
        if generating:
            self.start_spinner()
        else:
            self.stop_spinner()
            self._generating_session = None
            self._pending_turn = None

    def _select_default_model(self, previous=""):
        """Select the best model in the combo box.

        Priority: previous selection > configured default > first available.
        """
        if previous:
            for i in range(self.model_combo.count()):
                if self.model_combo.itemData(i) == previous:
                    self.model_combo.setCurrentIndex(i)
                    return

        default = self.get_conf(
            "chat_model", default="gpt-oss-20b-abliterated"
        )
        for i in range(self.model_combo.count()):
            if self.model_combo.itemData(i) == default:
                self.model_combo.setCurrentIndex(i)
                return

        if self.model_combo.count() > 0:
            self.model_combo.setCurrentIndex(0)

    def _handle_runtime_request(self, session, runtime_request):
        """Execute an internal runtime request and continue the turn."""
        session.display.discard_assistant_message()

        if self._pending_turn is None or self._pending_turn.session is not session:
            logger.warning("Missing pending turn state for runtime request")
            return False

        logger.info(
            "Intercepted runtime request from model: %s",
            runtime_request.get("tool", "runtime.unknown"),
        )

        if not runtime_request.get("valid"):
            logger.warning(
                "Rejected malformed runtime request: %s",
                runtime_request.get("error", "unknown error"),
            )
            return self._continue_after_runtime_observation(
                session,
                runtime_request,
                {
                    "ok": False,
                    "tool": "runtime.invalid_request",
                    "source": "unavailable",
                    "shell_status": "unavailable",
                    "shell_detail": "",
                    "working_directory": "",
                    "last_refreshed_at": "",
                    "payload": {},
                    "query_note": "",
                    "error": runtime_request.get(
                        "error", "Malformed runtime request."
                    ),
                },
            )

        if self._runtime_request_executor is None:
            logger.warning(
                "Runtime request executor is unavailable for tool %s",
                runtime_request["tool"],
            )
            return self._continue_after_runtime_observation(
                session,
                runtime_request,
                {
                    "ok": False,
                    "tool": runtime_request["tool"],
                    "source": "unavailable",
                    "shell_status": "unavailable",
                    "shell_detail": "",
                    "working_directory": "",
                    "last_refreshed_at": "",
                    "payload": {},
                    "query_note": "",
                    "error": "Runtime inspection is not currently available.",
                },
            )

        if self._pending_turn.tool_calls >= MAX_RUNTIME_TOOL_CALLS_PER_TURN:
            logger.warning(
                "Runtime request limit reached for this turn (%d)",
                MAX_RUNTIME_TOOL_CALLS_PER_TURN,
            )
            return self._continue_after_runtime_observation(
                session,
                runtime_request,
                {
                    "ok": False,
                    "tool": runtime_request["tool"],
                    "source": "unavailable",
                    "shell_status": "unavailable",
                    "shell_detail": "",
                    "working_directory": "",
                    "last_refreshed_at": "",
                    "payload": {},
                    "query_note": "",
                    "error": (
                        "Runtime inspection limit reached for this turn. "
                        "Answer with the available information."
                    ),
                },
            )

        self.status_label.setText("Inspecting runtime...")
        try:
            result = self._runtime_request_executor(runtime_request)
        except Exception as error:
            logger.exception(
                "Runtime request executor crashed for tool %s",
                runtime_request["tool"],
            )
            result = {
                "ok": False,
                "tool": runtime_request["tool"],
                "source": "unavailable",
                "shell_status": "unavailable",
                "shell_detail": "",
                "working_directory": "",
                "last_refreshed_at": "",
                "payload": {},
                "query_note": "",
                "error": f"Runtime inspection failed: {error}",
            }
        logger.info(
            "Runtime request %s completed (ok=%s, source=%s)",
            runtime_request["tool"],
            result.get("ok"),
            result.get("source", ""),
        )
        self._pending_turn.tool_calls += 1
        return self._continue_after_runtime_observation(
            session,
            runtime_request,
            result,
        )

    def _continue_after_runtime_observation(self, session, runtime_request, result):
        """Append a hidden runtime observation and continue the same turn."""
        if self._pending_turn is None or self._pending_turn.session is not session:
            return False

        observation = format_runtime_observation(runtime_request, result)
        logger.info(
            "Continuing chat turn after runtime observation for %s (tool call %d/%d)",
            runtime_request.get("tool", "runtime.unknown"),
            self._pending_turn.tool_calls,
            MAX_RUNTIME_TOOL_CALLS_PER_TURN,
        )
        self._pending_turn.request_messages.extend([
            {
                "role": "assistant",
                "content": runtime_request.get("raw_text", ""),
            },
            {
                "role": "user",
                "content": observation,
            },
        ])
        session.display.start_assistant_message()
        options = {
            "temperature": _normalize_chat_temperature(
                self.get_conf("chat_temperature", default=0.5)
            ),
            "num_predict": self.get_conf("max_tokens", default=1024),
        }
        self.sig_send_chat.emit(
            self._current_model,
            list(self._pending_turn.request_messages),
            options,
        )
        return True

    # --- Cleanup ---

    def cleanup_worker(self):
        """Stop the worker thread gracefully.

        Called by the plugin during Spyder shutdown.
        """
        if self._thread.isRunning():
            self._worker.abort()
            self._thread.quit()
            if not self._thread.wait(5000):
                logger.warning(
                    "Worker thread did not exit cleanly, terminating"
                )
                self._thread.terminate()
                self._thread.wait(1000)
