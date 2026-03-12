"""Main chat widget for the AI Chat plugin.

This is the PluginMainWidget that provides the dockable chat pane in
Spyder. Supports multiple chat sessions as tabs, each with its own
conversation history and display. All sessions share the same background
worker, model selector, and input area.

Architecture:
    UI (main thread) ──signals──> ChatWorker (background QThread)
    ChatWorker ──signals──> UI (main thread)

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
    QFileDialog, QMenu, QTabWidget, QToolButton,
)

from spyder.api.widgets.main_widget import PluginMainWidget

from spyder_ai_assistant.backend.worker import ChatWorker
from spyder_ai_assistant.utils.chat_exchanges import (
    build_chat_exchange_rows,
    delete_chat_exchange,
)
from spyder_ai_assistant.utils.chat_inference import (
    describe_chat_inference_source,
    format_chat_temperature,
    make_chat_inference_record,
    normalize_chat_max_tokens,
    normalize_chat_temperature,
    resolve_chat_inference_options,
)
from spyder_ai_assistant.utils.chat_persistence import (
    build_chat_session_history_rows,
    make_chat_session_record,
    merge_chat_session_history,
    remove_chat_session_from_history,
)
from spyder_ai_assistant.utils.context import build_system_context_block
from spyder_ai_assistant.utils.provider_profiles import (
    PROVIDER_KIND_OPENAI_COMPATIBLE,
    resolve_preferred_profile,
    serialize_provider_profiles,
    normalize_provider_profiles,
)
from spyder_ai_assistant.utils.prompt_library import (
    build_chat_prompt_preset_block,
    get_chat_prompt_preset,
    list_chat_prompt_presets,
    normalize_chat_prompt_preset,
)
from spyder_ai_assistant.utils.runtime_bridge import (
    MAX_RUNTIME_TOOL_CALLS_PER_TURN,
    build_runtime_bridge_instructions,
    format_runtime_observation,
    parse_runtime_request,
)
from spyder_ai_assistant.utils.chat_workflows import (
    DEBUG_ACTION_LABELS,
    build_debug_prompt,
    build_export_markdown,
)
from spyder_ai_assistant.widgets.chat_input import ChatInput
from spyder_ai_assistant.widgets.chat_settings_dialog import ChatSettingsDialog
from spyder_ai_assistant.widgets.exchange_delete_dialog import ExchangeDeleteDialog
from spyder_ai_assistant.widgets.provider_profiles_dialog import (
    ProviderProfilesDialog,
)
from spyder_ai_assistant.widgets.session_history_dialog import SessionHistoryDialog
from spyder_ai_assistant.widgets.chat_display import ChatDisplay

logger = logging.getLogger(__name__)


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

    def ordered_sessions(self, tab_widget):
        """Return sessions in the current visible tab order."""
        sessions = []
        for index in range(tab_widget.count()):
            session = self.get_for_index(tab_widget, index)
            if session is not None:
                sessions.append(session)
        return sessions


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

    def __init__(self, parent=None, title=None, messages=None, session_id=None,
                 created_at=None, updated_at=None, prompt_preset_id=None,
                 temperature_override=None, max_tokens_override=None):
        ChatSession._counter += 1
        default_title = title or f"Chat {ChatSession._counter}"
        record = make_chat_session_record(
            title=default_title,
            messages=messages or [],
            session_id=session_id,
            created_at=created_at,
            updated_at=updated_at,
            prompt_preset_id=prompt_preset_id,
            temperature_override=temperature_override,
            max_tokens_override=max_tokens_override,
        )
        self.display = ChatDisplay(parent)
        self.session_id = record["session_id"]
        self.title = record["title"]
        self.messages = record["messages"]
        self.created_at = record["created_at"]
        self.updated_at = record["updated_at"]
        self.prompt_preset_id = record["prompt_preset_id"]
        self.temperature_override = record["temperature_override"]
        self.max_tokens_override = record["max_tokens_override"]

    def touch(self):
        """Refresh the session updated timestamp after a state change."""
        self.updated_at = make_chat_session_record(
            title=self.title,
            messages=self.messages,
            session_id=self.session_id,
            created_at=self.created_at,
            prompt_preset_id=self.prompt_preset_id,
            temperature_override=self.temperature_override,
            max_tokens_override=self.max_tokens_override,
        )["updated_at"]

    def to_state(self):
        """Return one persisted session payload."""
        return make_chat_session_record(
            title=self.title,
            messages=self.messages,
            session_id=self.session_id,
            created_at=self.created_at,
            updated_at=self.updated_at,
            prompt_preset_id=self.prompt_preset_id,
            temperature_override=self.temperature_override,
            max_tokens_override=self.max_tokens_override,
        )


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
    - Compact runtime/session controls plus Stop/Send
    - Status indicator showing generation state and speed

    All chat-provider communication happens on a background QThread to keep
    the UI responsive during LLM inference.
    """

    # Signal to dispatch a chat request to the background worker.
    # Args: provider id (str), model name (str), messages list, options dict
    sig_send_chat = Signal(str, str, list, dict)

    # Signal to request model listing from the background worker
    sig_list_models = Signal()
    # Signal to update chat-provider settings on the worker thread
    sig_update_provider_settings = Signal(dict)

    # Emitted when the user clicks "Apply..." on a code block.
    # Bubbles up from ChatDisplay so the plugin can open the apply preview.
    sig_apply_code = Signal(str)

    # Enable the built-in loading spinner in the corner toolbar
    ENABLE_SPINNER = True

    def __init__(self, name, plugin, parent=None):
        super().__init__(name, plugin, parent)

        # Whether the LLM is currently generating a response
        self._generating = False

        # Currently selected provider-aware model entry from the combo box.
        self._current_provider = self.get_conf("chat_provider", default="ollama")
        self._current_provider_label = ""
        self._current_provider_kind = self._current_provider
        self._current_provider_profile_id = self.get_conf(
            "chat_provider_profile_id", default=""
        )
        self._current_model = ""

        # Callable that returns editor context dict (set by plugin).
        # When set, _send_message() enriches the system prompt with
        # the current file content and cursor position.
        self._context_provider = None
        # Callable that executes one runtime inspection request.
        self._runtime_request_executor = None
        # Callable that changes the explicit runtime target shell.
        self._runtime_target_handler = None

        # The session that initiated the current generation.
        # Streaming tokens and the final response are routed here,
        # even if the user switches tabs mid-generation.
        self._generating_session = None
        # Hidden per-turn state used when the model asks for runtime data.
        self._pending_turn = None
        # Cached public runtime snapshot for toolbar status and exports.
        self._runtime_context_snapshot = {}
        # Cached runtime shell-target records used by the toolbar selector.
        self._runtime_shells = []
        # Optional callback invoked when chat-session state changes and
        # should be persisted by the plugin layer.
        self._session_state_changed_callback = None
        # Callable returning the current persistence-scope metadata used by
        # the history browser dialog.
        self._session_scope_provider = None
        # Cached saved-session history for the current project/global scope.
        self._history_sessions = []
        # Latest provider diagnostics emitted by the worker after model refresh.
        self._provider_diagnostics = []

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

        self.prompt_preset_combo = QComboBox(self)
        self.prompt_preset_combo.setMinimumWidth(170)
        self.prompt_preset_combo.setToolTip("Select the active chat mode for this tab")
        self.prompt_preset_combo.ID = "ai_chat_prompt_preset_selector"
        self._populate_prompt_preset_combo()

        self.status_label = QLabel("Connecting...")
        self.status_label.ID = "ai_chat_status_label"

        # Context label: shows current file and cursor line (e.g. "main.py:42")
        self.context_label = QLabel("")
        self.context_label.ID = "ai_chat_context_label"
        self.context_label.setMinimumWidth(100)
        self.context_label.setToolTip("Current editor file and cursor position")

        # Runtime label: shows the active kernel state without dumping
        # console or variable content into the normal chat prompt path.
        self.runtime_label = QLabel("Kernel: unavailable")
        self.runtime_label.ID = "ai_chat_runtime_label"
        self.runtime_label.setMinimumWidth(130)
        self.runtime_label.setToolTip("Active IPython console runtime status")

        self.runtime_target_combo = QComboBox(self)
        self.runtime_target_combo.setMinimumWidth(190)
        self.runtime_target_combo.setToolTip(
            "Choose which Spyder IPython console the runtime bridge should inspect"
        )
        self.runtime_target_combo.ID = "ai_chat_runtime_target_selector"
        self.runtime_target_combo.addItem("Follow Active Console", "")

        toolbar = self.get_main_toolbar()
        self.add_item_to_toolbar(
            self.model_combo, toolbar=toolbar, section="main",
        )
        self.add_item_to_toolbar(
            self.prompt_preset_combo, toolbar=toolbar, section="preset",
        )
        self.add_item_to_toolbar(
            self.context_label, toolbar=toolbar, section="context",
        )
        self.add_item_to_toolbar(
            self.runtime_label, toolbar=toolbar, section="runtime",
        )
        self.add_item_to_toolbar(
            self.runtime_target_combo, toolbar=toolbar, section="runtime_target",
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
        self._new_tab_action = self.create_action(
            "ai_chat_new_tab",
            text="New Chat Tab",
            triggered=self._add_new_tab,
        )
        self._export_action = self.create_action(
            "ai_chat_export",
            text="Export Chat...",
            triggered=self._export_chat,
        )
        self._delete_exchange_action = self.create_action(
            "ai_chat_delete_exchange",
            text="Delete Exchange...",
            triggered=self._open_exchange_delete_dialog,
        )
        self._chat_settings_action = self.create_action(
            "ai_chat_tab_settings",
            text="Chat Settings...",
            triggered=self._open_chat_settings_dialog,
        )
        self._history_action = self.create_action(
            "ai_chat_history",
            text="Chat History...",
            triggered=self._open_history_browser,
        )
        self._provider_profiles_action = self.create_action(
            "ai_chat_provider_profiles",
            text="Provider Profiles...",
            triggered=self._open_provider_profiles_dialog,
        )
        options_menu = self.get_options_menu()
        self.add_item_to_menu(refresh_action, menu=options_menu)
        self.add_item_to_menu(self._new_tab_action, menu=options_menu)
        self.add_item_to_menu(self._provider_profiles_action, menu=options_menu)
        self.add_item_to_menu(self._chat_settings_action, menu=options_menu)
        self.add_item_to_menu(self._delete_exchange_action, menu=options_menu)
        self.add_item_to_menu(self._history_action, menu=options_menu)
        self.add_item_to_menu(self._export_action, menu=options_menu)

        # --- Tab widget for multiple chat sessions ---
        self._tab_widget = QTabWidget(self)
        self._tab_widget.setTabsClosable(True)
        self._tab_widget.setMovable(True)
        self._tab_widget.tabCloseRequested.connect(self._close_tab)
        self._tab_widget.tabBar().tabMoved.connect(
            lambda _from, _to: self._notify_session_state_changed("tab-move")
        )

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

        # Compact action row: keep the common actions visible while moving
        # the lower-frequency debug variants behind a small menu button.
        controls_layout = QHBoxLayout()
        self._debug_actions = {
            action: self.create_action(
                f"ai_chat_debug_{action}",
                text=DEBUG_ACTION_LABELS.get(action, action),
                triggered=lambda checked=False, action=action: self._send_debug_prompt(action),
            )
            for action in (
                "explain_error",
                "fix_traceback",
                "use_variables",
                "use_console",
            )
        }
        self.debug_menu_btn = QToolButton(self)
        self.debug_menu_btn.setText("Debug")
        self.debug_menu_btn.setPopupMode(QToolButton.InstantPopup)
        self.debug_menu_btn.setToolTip(
            "Runtime-aware debugging actions for the active chat tab"
        )
        debug_menu = QMenu(self.debug_menu_btn)
        for action in (
                "explain_error",
                "fix_traceback",
                "use_variables",
                "use_console"):
            debug_menu.addAction(self._debug_actions[action])
        self.debug_menu_btn.setMenu(debug_menu)

        self.regenerate_btn = QToolButton(self)
        self.regenerate_btn.setText("Regenerate")
        self.regenerate_btn.setToolTip(
            "Remove the last assistant answer on this tab and ask again"
        )
        self.chat_settings_btn = QToolButton(self)
        self.chat_settings_btn.setText("Settings")
        self.chat_settings_btn.setToolTip(
            "Adjust inference settings for the active chat tab"
        )
        self.session_btn = QToolButton(self)
        self.session_btn.setText("Sessions")
        self.session_btn.setPopupMode(QToolButton.MenuButtonPopup)
        self.session_btn.setToolTip(
            "Browse saved chats and open other session actions"
        )
        session_menu = QMenu(self.session_btn)
        session_menu.addAction(self._history_action)
        session_menu.addAction(self._new_tab_action)
        session_menu.addAction(self._provider_profiles_action)
        session_menu.addAction(self._delete_exchange_action)
        session_menu.addAction(self._export_action)
        self.session_btn.setMenu(session_menu)
        # Backward-compatible alias used by older harnesses.
        self.history_btn = self.session_btn

        self.stop_btn = QPushButton("Stop")
        self.send_btn = QPushButton("Send")
        self.stop_btn.setEnabled(False)
        controls_layout.addWidget(self.debug_menu_btn)
        controls_layout.addWidget(self.regenerate_btn)
        controls_layout.addWidget(self.session_btn)
        controls_layout.addWidget(self.chat_settings_btn)
        controls_layout.addStretch()
        controls_layout.addWidget(self.stop_btn)
        controls_layout.addWidget(self.send_btn)

        # Assemble the content layout (splitter + compact controls)
        content_layout = QVBoxLayout()
        content_layout.addWidget(splitter)
        content_layout.addLayout(controls_layout)

        self.setLayout(content_layout)

        # --- Background worker thread ---
        self._thread = QThread(None)
        self._worker = ChatWorker(settings=self._chat_provider_settings())
        self._worker.moveToThread(self._thread)

        # Main thread → worker: dispatch work requests via signals
        self.sig_send_chat.connect(self._worker.send_chat)
        self.sig_list_models.connect(self._worker.list_models)
        self.sig_update_provider_settings.connect(
            self._worker.update_settings
        )

        # Worker → main thread: receive results via signals
        self._worker.chunk_received.connect(self._on_chunk)
        self._worker.response_ready.connect(self._on_response)
        self._worker.models_listed.connect(self._on_models_listed)
        self._worker.provider_diagnostics_ready.connect(
            self._on_provider_diagnostics
        )
        self._worker.error_occurred.connect(self._on_error)
        self._worker.status_changed.connect(self._on_status_changed)

        # UI signal connections
        self.chat_input.submit_requested.connect(self._send_message)
        self.send_btn.clicked.connect(self._send_message)
        self.stop_btn.clicked.connect(self._stop_generation)
        self.chat_settings_btn.clicked.connect(self._open_chat_settings_dialog)
        self.session_btn.clicked.connect(self._open_history_browser)
        self.regenerate_btn.clicked.connect(self._regenerate_last_turn)
        self.model_combo.currentIndexChanged.connect(
            self._on_model_changed
        )
        self.runtime_target_combo.currentIndexChanged.connect(
            self._on_runtime_target_changed
        )
        self.prompt_preset_combo.currentIndexChanged.connect(
            self._on_prompt_preset_changed
        )
        self._tab_widget.currentChanged.connect(self._on_current_tab_changed)
        self._sync_session_controls()

        # Start the worker thread and fetch available models
        self._thread.start()
        self.sig_list_models.emit()

    def update_actions(self):
        """Called by Spyder when the widget gains/loses focus.

        Required by the PluginMainWidget interface. No focus-dependent
        actions to update.
        """
        pass

    def _populate_prompt_preset_combo(self):
        """Fill the shared preset selector with built-in prompt presets."""
        self.prompt_preset_combo.blockSignals(True)
        self.prompt_preset_combo.clear()
        for preset in list_chat_prompt_presets():
            self.prompt_preset_combo.addItem(preset["label"], preset["id"])
            index = self.prompt_preset_combo.count() - 1
            self.prompt_preset_combo.setItemData(
                index,
                preset["description"],
                Qt.ToolTipRole,
            )
        self.prompt_preset_combo.blockSignals(False)

    def _sync_prompt_preset_combo(self, session=None):
        """Reflect the active session preset in the shared combo box."""
        if session is None:
            session = self._active_session

        preset = get_chat_prompt_preset(
            getattr(session, "prompt_preset_id", None)
        )
        combo_index = 0
        for index in range(self.prompt_preset_combo.count()):
            if self.prompt_preset_combo.itemData(index) == preset["id"]:
                combo_index = index
                break

        self.prompt_preset_combo.blockSignals(True)
        self.prompt_preset_combo.setCurrentIndex(combo_index)
        self.prompt_preset_combo.setToolTip(
            f"{preset['label']}: {preset['description']}"
        )
        self.prompt_preset_combo.blockSignals(False)

    def _sync_chat_settings_button(self, session=None):
        """Reflect the active session inference overrides in the settings button."""
        if session is None:
            session = self._active_session

        if session is None:
            self.chat_settings_btn.setText("Settings")
            self.chat_settings_btn.setToolTip(
                "Adjust inference settings for the active chat tab"
            )
            return

        metadata = self._chat_option_metadata(session)
        has_override = (
            metadata["temperature_source"] == "override"
            or metadata["num_predict_source"] == "override"
        )
        self.chat_settings_btn.setText("Settings*" if has_override else "Settings")
        self.chat_settings_btn.setToolTip(
            self._build_chat_settings_tooltip(metadata)
        )

    def _sync_session_menu_button(self, session=None):
        """Refresh the session-menu tooltip from the active session/scope."""
        if session is None:
            session = self._active_session

        scope = self._session_scope_info()
        scope_label = scope.get("scope_label", "Global")
        active_title = session.title if session else "No active session"
        self.session_btn.setToolTip(
            "Open chat history and session actions.\n"
            f"Scope: {scope_label}\n"
            f"Active tab: {active_title}\n"
            f"Saved sessions: {len(self._history_sessions or [])}"
        )

    def _sync_session_controls(self, session=None):
        """Refresh the shared per-tab controls from the active session."""
        self._sync_prompt_preset_combo(session=session)
        self._sync_chat_settings_button(session=session)
        self._sync_session_menu_button(session=session)

    def _on_current_tab_changed(self, index):
        """Update shared tab-scoped controls when the active tab changes."""
        del index
        self._sync_session_controls()

    def _current_model_payload(self):
        """Return the current provider-aware model selection."""
        payload = self.model_combo.currentData()
        if isinstance(payload, dict):
            return dict(payload)
        return {}

    @staticmethod
    def _format_model_display(payload):
        """Return the provider-aware combo-box label for one model."""
        provider_label = payload.get("provider_label", "Provider")
        name = payload.get("name", "")
        parameter_size = payload.get("parameter_size", "")
        size_gb = payload.get("size_gb", 0) or 0

        details = []
        if parameter_size:
            details.append(str(parameter_size))
        if size_gb:
            details.append(f"{size_gb}GB")
        if details:
            return f"[{provider_label}] {name} ({', '.join(details)})"
        return f"[{provider_label}] {name}"

    @staticmethod
    def _format_model_tooltip(payload):
        """Return the detailed tooltip for one provider-aware model entry."""
        lines = [
            f"Provider: {payload.get('provider_label', 'unknown')}",
            f"Kind: {payload.get('provider_kind', payload.get('provider_id', 'unknown'))}",
            f"Model: {payload.get('name', '')}",
            f"Family: {payload.get('family', 'unknown') or 'unknown'}",
            (
                "Parameters: "
                f"{payload.get('parameter_size', 'unknown') or 'unknown'}"
            ),
            (
                "Quantization: "
                f"{payload.get('quantization', 'unknown') or 'unknown'}"
            ),
            f"Size: {payload.get('size_gb', 0) or 0} GB",
        ]
        if payload.get("endpoint"):
            lines.append(f"Endpoint: {payload.get('endpoint', '')}")
        return "\n".join(lines)

    def _current_model_export_name(self):
        """Return the provider-aware model label used in exports/logging."""
        payload = self._current_model_payload()
        if not payload:
            return self._current_model
        provider_label = payload.get("provider_label", "").strip()
        if provider_label:
            return f"{provider_label}: {payload.get('name', '')}"
        return payload.get("name", self._current_model)

    def trigger_debug_action(self, action):
        """Trigger one runtime-aware debug action programmatically."""
        debug_action = self._debug_actions.get(action)
        if debug_action is None:
            raise KeyError(f"Unknown debug action: {action}")
        debug_action.trigger()

    def _provider_profiles(self):
        """Return normalized provider profiles from config."""
        return normalize_provider_profiles(
            self.get_conf("provider_profiles", default="[]"),
            legacy_base_url=self.get_conf(
                "openai_compatible_base_url",
                default="",
            ),
            legacy_api_key=self.get_conf(
                "openai_compatible_api_key",
                default="",
            ),
        )

    def _build_provider_diagnostics_tooltip(self):
        """Render the latest provider diagnostics as a tooltip block."""
        if not self._provider_diagnostics:
            return "No provider diagnostics collected yet."

        lines = []
        for record in self._provider_diagnostics:
            label = record.get("provider_label", record.get("provider_id", "Provider"))
            status = record.get("status", "unknown")
            message = record.get("message", "")
            endpoint = record.get("endpoint", "")
            segment = f"{label} [{status}]"
            if endpoint:
                segment += f" {endpoint}"
            if message:
                segment += f" — {message}"
            lines.append(segment)
        return "\n".join(lines)

    def _sync_provider_status_label(self, models_available=None):
        """Refresh the status-label summary from provider diagnostics."""
        if models_available is None:
            models_available = self.model_combo.count() > 0
        diagnostics = list(self._provider_diagnostics)
        error_count = sum(
            1 for record in diagnostics if record.get("status") == "error"
        )
        if models_available:
            if error_count:
                self.status_label.setText(f"Ready ({error_count} provider issue)")
            else:
                self.status_label.setText("Ready")
        elif error_count:
            self.status_label.setText("Provider issue")
        else:
            self.status_label.setText("No models available")
        self.status_label.setToolTip(self._build_provider_diagnostics_tooltip())

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

    def _add_new_tab(self, notify=True):
        """Create a new chat session tab and switch to it."""
        session = ChatSession(parent=self._tab_widget)
        return self._add_session(session, notify=notify)

    def _close_tab(self, index):
        """Close a chat tab. Prevents closing the last tab.

        Args:
            index: Tab index to close.
        """
        # Don't close the last tab — always keep at least one
        if self._tab_widget.count() <= 1:
            session = self._sessions.get_for_index(self._tab_widget, index)
            if session and session.messages:
                self._history_sessions = merge_chat_session_history(
                    [session.to_state()],
                    self._history_sessions,
                )
            self._clear_all_tabs()
            self._add_new_tab(notify=False)
            self._notify_session_state_changed("tab-clear")
            return

        # Don't close a tab that's currently generating
        session = self._sessions.get_for_index(self._tab_widget, index)
        if session is self._generating_session:
            return

        if session and session.messages:
            self._history_sessions = merge_chat_session_history(
                [session.to_state()],
                self._history_sessions,
            )

        # Remove the tab and clean up the session
        widget = self._tab_widget.widget(index)
        self._tab_widget.removeTab(index)
        self._sessions.remove_for_widget(widget)
        widget.deleteLater()
        self._notify_session_state_changed("tab-close")

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
                "Chat model %s/%s returned an empty response",
                self._current_provider or "<provider>",
                self._current_model or "<model>",
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
            session.touch()
            self._refresh_session_title(session)
            self._notify_session_state_changed("assistant-response")

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
            payload = dict(m)
            display = self._format_model_display(payload)
            self.model_combo.addItem(display, payload)
            idx = self.model_combo.count() - 1
            self.model_combo.setItemData(
                idx,
                self._format_model_tooltip(payload),
                Qt.ToolTipRole,
            )
        self._select_default_model(previous)
        self.model_combo.blockSignals(False)

        self._on_model_changed(self.model_combo.currentIndex())
        self._sync_provider_status_label(models_available=bool(models))

    def _on_provider_diagnostics(self, diagnostics):
        """Store provider diagnostics emitted after a model refresh."""
        self._provider_diagnostics = list(diagnostics or [])
        self.status_label.setToolTip(self._build_provider_diagnostics_tooltip())

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
        self.status_label.setToolTip(self._build_provider_diagnostics_tooltip())

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
        self._send_prompt_text(self.chat_input.peek_text())

    def _send_debug_prompt(self, action):
        """Send a predefined runtime-aware debug prompt."""
        user_text = self.chat_input.peek_text()
        prompt = build_debug_prompt(
            action,
            user_text=user_text,
            context_label=self.context_label.text(),
        )
        logger.info("Dispatching debug quick action: %s", action)
        self._send_prompt_text(prompt)

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
        """Re-fetch the model list from every configured chat provider."""
        self.sig_list_models.emit()

    def _export_chat(self):
        """Export the active tab's conversation to a Markdown file."""
        session = self._active_session
        if session is None or not session.messages:
            if session:
                session.display.append_error("No messages to export.")
            return

        model_short = self._current_model.split("/")[-1].split(":")[0]
        if self._current_provider:
            model_short = f"{self._current_provider}-{model_short}"
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

        try:
            with open(filepath, "w", encoding="utf-8") as f:
                f.write(
                    build_export_markdown(
                        session.messages,
                        model_name=self._current_model_export_name(),
                        context_label=self.context_label.text(),
                        runtime_context=self._runtime_context_snapshot,
                        prompt_preset_label=get_chat_prompt_preset(
                            session.prompt_preset_id
                        )["label"],
                        inference_metadata=self._chat_option_metadata(session),
                    )
                )
            self.status_label.setText(
                f"Exported to {os.path.basename(filepath)}"
            )
            logger.info("Exported chat session to %s", filepath)
        except OSError as e:
            session.display.append_error(f"Export failed: {e}")

    def _on_model_changed(self, index):
        """Update the current model when the combo selection changes."""
        del index
        payload = self._current_model_payload()
        self._current_provider = payload.get(
            "provider_id",
            self.get_conf("chat_provider", default="ollama"),
        )
        self._current_provider_label = payload.get("provider_label", "")
        self._current_provider_kind = payload.get(
            "provider_kind",
            self.get_conf("chat_provider", default="ollama"),
        )
        self._current_provider_profile_id = payload.get("profile_id", "")
        self._current_model = payload.get("name", "")
        if payload:
            self.model_combo.setToolTip(self._format_model_tooltip(payload))
            self.set_conf("chat_model", self._current_model)
            self.set_conf("chat_provider_profile_id", self._current_provider_profile_id)
            preferred_kind = payload.get(
                "provider_kind",
                self.get_conf("chat_provider", default="ollama"),
            )
            if preferred_kind != self.get_conf("chat_provider", default="ollama"):
                self.set_conf("chat_provider", preferred_kind)

    def _on_prompt_preset_changed(self, index):
        """Persist the selected prompt preset on the active session."""
        del index
        session = self._active_session
        if session is None:
            return

        preset_id = normalize_chat_prompt_preset(
            self.prompt_preset_combo.currentData()
        )
        if session.prompt_preset_id == preset_id:
            self._sync_prompt_preset_combo(session)
            return

        session.prompt_preset_id = preset_id
        session.touch()
        preset = get_chat_prompt_preset(preset_id)
        logger.info(
            "Chat prompt preset set to %s for session %s",
            preset["label"],
            session.session_id,
        )
        self._sync_prompt_preset_combo(session)
        self._notify_session_state_changed("prompt-preset")

    def _chat_default_options(self):
        """Return the normalized global chat defaults from preferences."""
        return {
            "temperature": normalize_chat_temperature(
                self.get_conf("chat_temperature", default=0.5)
            ),
            "num_predict": normalize_chat_max_tokens(
                self.get_conf("max_tokens", default=1024)
            ),
        }

    def _chat_option_metadata(self, session=None):
        """Return resolved request options plus source metadata for one tab."""
        session = session or self._active_session
        defaults = self._chat_default_options()
        return resolve_chat_inference_options(
            default_temperature=defaults["temperature"],
            default_max_tokens=defaults["num_predict"],
            temperature_override=getattr(session, "temperature_override", None),
            max_tokens_override=getattr(session, "max_tokens_override", None),
        )

    def _build_chat_settings_tooltip(self, metadata):
        """Return the active tab settings summary shown in the UI."""
        return "\n".join(
            [
                "Adjust inference settings for the active chat tab.",
                (
                    "Temperature: "
                    f"{format_chat_temperature(metadata['temperature'])} "
                    f"({describe_chat_inference_source(metadata['temperature_source'])})"
                ),
                (
                    "Max tokens: "
                    f"{int(metadata['num_predict'])} "
                    f"({describe_chat_inference_source(metadata['num_predict_source'])})"
                ),
            ]
        )

    def _create_chat_settings_dialog(self, session=None):
        """Build the per-tab chat settings dialog for one session."""
        session = session or self._active_session
        overrides = make_chat_inference_record(
            temperature_override=getattr(session, "temperature_override", None),
            max_tokens_override=getattr(session, "max_tokens_override", None),
        )
        return ChatSettingsDialog(
            session_title=getattr(session, "title", ""),
            defaults=self._chat_default_options(),
            overrides=overrides,
            parent=self,
        )

    def _apply_chat_settings(self, session, overrides):
        """Persist one set of per-tab inference overrides."""
        normalized = make_chat_inference_record(
            temperature_override=(overrides or {}).get("temperature_override"),
            max_tokens_override=(overrides or {}).get("max_tokens_override"),
        )
        current = make_chat_inference_record(
            temperature_override=getattr(session, "temperature_override", None),
            max_tokens_override=getattr(session, "max_tokens_override", None),
        )
        if current == normalized:
            self._sync_chat_settings_button(session)
            return False

        session.temperature_override = normalized["temperature_override"]
        session.max_tokens_override = normalized["max_tokens_override"]
        session.touch()
        metadata = self._chat_option_metadata(session)
        logger.info(
            "Updated chat settings for session %s: temperature=%s (%s), "
            "max_tokens=%d (%s)",
            session.session_id,
            format_chat_temperature(metadata["temperature"]),
            describe_chat_inference_source(metadata["temperature_source"]),
            int(metadata["num_predict"]),
            describe_chat_inference_source(metadata["num_predict_source"]),
        )
        self._sync_chat_settings_button(session)
        self._notify_session_state_changed("chat-settings")
        return True

    def _open_chat_settings_dialog(self):
        """Open the per-tab chat settings dialog and save any accepted changes."""
        session = self._active_session
        if session is None:
            return False

        dialog = self._create_chat_settings_dialog(session)
        if dialog.exec_() != dialog.Accepted:
            self._sync_chat_settings_button(session)
            return False

        return self._apply_chat_settings(session, dialog.selected_overrides())

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

    def set_runtime_target_handler(self, handler):
        """Set the callable that changes the explicit runtime target shell."""
        self._runtime_target_handler = handler

    def set_session_state_changed_callback(self, callback):
        """Set the callback invoked when chat session state changes."""
        self._session_state_changed_callback = callback

    def set_session_scope_provider(self, provider):
        """Set the callable that returns the current history-browser scope."""
        self._session_scope_provider = provider

    def update_toolbar_context(self, context_str):
        """Update the toolbar context label with the current file info.

        Args:
            context_str: String like "main.py:42", or "" to clear.
        """
        self.context_label.setText(context_str)

    def update_runtime_context(self, runtime_context):
        """Update the runtime toolbar label from a public runtime snapshot."""
        self._runtime_context_snapshot = dict(runtime_context or {})
        status = self._runtime_context_snapshot.get("status", "unavailable") or "unavailable"
        detail = self._runtime_context_snapshot.get("status_detail", "")
        label = f"Kernel: {status}"
        if status == "errored":
            label = "Kernel: error"
        self.runtime_label.setText(label)
        self.runtime_label.setToolTip(
            self._build_runtime_tooltip(detail=detail)
        )
        logger.debug("Updated runtime toolbar status: %s", label)

    def update_runtime_shell_targets(self, shell_records, selected_shell_id=""):
        """Refresh the runtime-target combo from the runtime service."""
        self._runtime_shells = list(shell_records or [])
        selected_shell_id = str(selected_shell_id or "").strip()

        self.runtime_target_combo.blockSignals(True)
        self.runtime_target_combo.clear()
        self.runtime_target_combo.addItem("Follow Active Console", "")
        for record in self._runtime_shells:
            label = record.get("label", "Console")
            suffix = []
            if record.get("is_active"):
                suffix.append("active")
            if record.get("has_error"):
                suffix.append("error")
            if suffix:
                label = f"{label} ({', '.join(suffix)})"
            self.runtime_target_combo.addItem(label, record.get("shell_id", ""))
        target_index = 0
        for index in range(self.runtime_target_combo.count()):
            if self.runtime_target_combo.itemData(index) == selected_shell_id:
                target_index = index
                break
        self.runtime_target_combo.setCurrentIndex(target_index)
        self.runtime_target_combo.blockSignals(False)
        logger.debug(
            "Updated runtime shell targets with %d option(s); selected=%s",
            len(self._runtime_shells),
            selected_shell_id or "<follow-active>",
        )

    def _chat_provider_settings(self):
        """Return one snapshot of provider settings for the worker."""
        return {
            "ollama_host": self.get_conf(
                "ollama_host", default="http://localhost:11434"
            ),
            "provider_profiles": self._provider_profiles(),
            "openai_compatible_base_url": self.get_conf(
                "openai_compatible_base_url",
                default="",
            ),
            "openai_compatible_api_key": self.get_conf(
                "openai_compatible_api_key",
                default="",
            ),
        }

    def update_ollama_host(self, host):
        """Backward-compatible wrapper for chat-provider refreshes."""
        del host
        self.update_chat_provider_settings()

    def update_chat_provider_settings(self, settings=None):
        """Refresh the worker's provider settings and reload chat models."""
        self.status_label.setText("Connecting...")
        self.status_label.setToolTip(self._build_provider_diagnostics_tooltip())
        self.sig_update_provider_settings.emit(
            dict(settings or self._chat_provider_settings())
        )
        self.sig_list_models.emit()

    def _open_provider_profiles_dialog(self):
        """Open the provider-profile manager and save any accepted changes."""
        dialog = ProviderProfilesDialog(
            profiles=self._provider_profiles(),
            diagnostics=self._provider_diagnostics,
            parent=self,
        )
        if dialog.exec_() != dialog.Accepted:
            return False

        profiles = dialog.selected_profiles()
        previous_profile_id = self.get_conf(
            "chat_provider_profile_id",
            default="",
        )
        self.set_conf("provider_profiles", serialize_provider_profiles(profiles))
        # Once the profile manager is used, migrate away from the legacy
        # single-endpoint settings so deleted profiles do not reappear.
        self.set_conf("openai_compatible_base_url", "")
        self.set_conf("openai_compatible_api_key", "")
        if self.get_conf("chat_provider", default="ollama") == PROVIDER_KIND_OPENAI_COMPATIBLE:
            preferred = resolve_preferred_profile(
                profiles,
                previous_profile_id,
            )
            self.set_conf("chat_provider_profile_id", preferred.get("profile_id", ""))
            if (
                previous_profile_id
                and preferred.get("profile_id", "") != previous_profile_id
            ):
                logger.info(
                    "Provider profile selection fell back from %s to %s",
                    previous_profile_id,
                    preferred.get("profile_id", "<none>"),
                )
        logger.info("Saved %d provider profile(s)", len(profiles))
        self.update_chat_provider_settings()
        return True

    def send_with_prompt(self, prompt):
        """Inject a prompt into the input and send it immediately.

        Used by context menu actions (Explain, Fix, Add Docstring) to
        populate the chat input with a pre-built prompt containing
        the selected code and then trigger generation.

        Args:
            prompt: The full prompt text to send.
        """
        self._send_prompt_text(prompt)

    def serialize_session_state(self):
        """Return the current chat sessions as a persisted payload."""
        sessions = self._serialize_open_sessions()
        history = merge_chat_session_history(sessions, self._history_sessions)
        self._history_sessions = list(history)

        return {
            "active_index": max(0, self._tab_widget.currentIndex()),
            "sessions": sessions,
            "history": history,
        }

    def restore_session_state(self, state):
        """Restore tabs and messages from persisted state."""
        if self._generating:
            logger.warning(
                "Skipping chat session restore while a response is generating"
            )
            return False

        sessions = []
        history = []
        if isinstance(state, dict):
            sessions = state.get("sessions", [])
            history = state.get("history", [])

        self._clear_all_tabs()
        self._history_sessions = merge_chat_session_history(sessions, history)
        if not sessions:
            self._add_new_tab(notify=False)
            return True

        for session_state in sessions:
            if not isinstance(session_state, dict):
                continue
            session = ChatSession(
                parent=self._tab_widget,
                title=session_state.get("title", ""),
                messages=session_state.get("messages", []),
                session_id=session_state.get("session_id"),
                created_at=session_state.get("created_at"),
                updated_at=session_state.get("updated_at"),
                prompt_preset_id=session_state.get("prompt_preset_id"),
                temperature_override=session_state.get("temperature_override"),
                max_tokens_override=session_state.get("max_tokens_override"),
            )
            self._add_session(session, notify=False)
            session.display.rebuild_from_messages(session.messages)

        if self._tab_widget.count() == 0:
            self._add_new_tab(notify=False)
            return True

        active_index = 0
        if isinstance(state, dict):
            active_index = state.get("active_index", 0)
        if not isinstance(active_index, int):
            active_index = 0
        active_index = max(0, min(active_index, self._tab_widget.count() - 1))
        self._tab_widget.setCurrentIndex(active_index)
        return True

    # --- Internal helpers ---

    def _notify_session_state_changed(self, reason):
        """Notify the plugin layer that persisted session state changed."""
        self._history_sessions = merge_chat_session_history(
            self._serialize_open_sessions(),
            self._history_sessions,
        )
        callback = self._session_state_changed_callback
        if callback is None:
            return

        logger.debug("Chat session state changed: %s", reason)
        callback()

    def _add_session(self, session, notify=True):
        """Insert one chat session into the tab widget."""
        session.display.sig_apply_code_requested.connect(
            self.sig_apply_code
        )

        idx = self._tab_widget.addTab(session.display, session.title)
        self._sessions.add(session)
        self._tab_widget.setCurrentIndex(idx)
        logger.debug("New chat tab: %s (index %d)", session.title, idx)
        if notify:
            self._notify_session_state_changed("tab-add")
        return session

    def _serialize_open_sessions(self):
        """Return the current visible tabs as persisted session records."""
        sessions = []
        for session in self._sessions.ordered_sessions(self._tab_widget):
            sessions.append(session.to_state())
        return sessions

    def _clear_all_tabs(self):
        """Remove all tabs and forget their tracked sessions."""
        while self._tab_widget.count():
            widget = self._tab_widget.widget(0)
            self._tab_widget.removeTab(0)
            self._sessions.remove_for_widget(widget)
            widget.deleteLater()

    def _refresh_session_title(self, session):
        """Keep the tab title aligned with the first visible user message."""
        title = "Chat"
        for msg in session.messages:
            if msg.get("role") != "user":
                continue
            short = msg.get("content", "")[:30].strip()
            if len(msg.get("content", "")) > 30:
                short += "..."
            title = short.replace("\n", " ") or "Chat"
            break

        if session.title == title:
            return

        session.title = title
        index = self._sessions.index_of(self._tab_widget, session)
        if index >= 0:
            self._tab_widget.setTabText(index, title)

    def _find_session_by_id(self, session_id):
        """Return the currently open session with one persisted id."""
        for session in self._sessions.ordered_sessions(self._tab_widget):
            if session.session_id == session_id:
                return session
        return None

    def _session_scope_info(self):
        """Return metadata for the current chat history scope."""
        if self._session_scope_provider is None:
            return {"scope_label": "Global", "storage_path": ""}
        try:
            return dict(self._session_scope_provider() or {})
        except Exception:
            logger.exception("Failed to query chat session scope info")
            return {"scope_label": "Global", "storage_path": ""}

    def _create_history_browser_dialog(self):
        """Build the modal history browser for the current persistence scope."""
        open_session_ids = {
            session.session_id
            for session in self._sessions.ordered_sessions(self._tab_widget)
        }
        rows = build_chat_session_history_rows(
            self._history_sessions,
            open_session_ids=open_session_ids,
        )
        logger.info(
            "Built chat history browser with %d saved session(s)",
            len(rows),
        )
        return SessionHistoryDialog(
            rows=rows,
            scope_info=self._session_scope_info(),
            parent=self,
        )

    def _open_history_browser(self):
        """Open the saved-session history browser and apply one chosen action."""
        dialog = self._create_history_browser_dialog()
        logger.info(
            "Opened chat history browser for %s scope",
            self._session_scope_info().get("scope_label", "unknown"),
        )
        if dialog.exec_() != dialog.Accepted:
            return

        session_id = dialog.selected_session_id()
        action = dialog.selected_action()
        if not session_id or not action:
            return

        logger.info(
            "History browser selected action '%s' for session %s",
            action,
            session_id,
        )

        if action == "open":
            self._open_session_from_history(session_id, duplicate=False)
        elif action == "duplicate":
            self._open_session_from_history(session_id, duplicate=True)
        elif action == "delete":
            self._delete_session_from_history(session_id)

    def _history_session_by_id(self, session_id):
        """Return one saved history record by id."""
        for session_state in self._history_sessions:
            if session_state.get("session_id") == session_id:
                return session_state
        return None

    def _open_session_from_history(self, session_id, duplicate=False):
        """Reopen or duplicate one saved history session into the tab widget."""
        session_state = self._history_session_by_id(session_id)
        if session_state is None:
            if self.chat_display:
                self.chat_display.append_error("Saved chat session no longer exists.")
            return False

        if not duplicate:
            existing = self._find_session_by_id(session_id)
            if existing is not None:
                index = self._sessions.index_of(self._tab_widget, existing)
                if index >= 0:
                    self._tab_widget.setCurrentIndex(index)
                logger.info("Focused already-open chat session from history: %s", session_id)
                return True

        title = session_state.get("title", "")
        if duplicate and title:
            title = f"{title} (copy)"

        session = ChatSession(
            parent=self._tab_widget,
            title=title,
            messages=session_state.get("messages", []),
            session_id=None if duplicate else session_state.get("session_id"),
            created_at=None if duplicate else session_state.get("created_at"),
            updated_at=None if duplicate else session_state.get("updated_at"),
            prompt_preset_id=session_state.get("prompt_preset_id"),
            temperature_override=session_state.get("temperature_override"),
            max_tokens_override=session_state.get("max_tokens_override"),
        )
        self._add_session(session, notify=True)
        session.display.rebuild_from_messages(session.messages)
        if duplicate:
            logger.info(
                "Duplicated chat session from history: %s -> %s",
                session_id,
                session.session_id,
            )
        else:
            logger.info("Reopened chat session from history: %s", session_id)
        return True

    def _delete_session_from_history(self, session_id):
        """Delete one saved history session and close any matching open tab."""
        open_session = self._find_session_by_id(session_id)
        if open_session is self._generating_session:
            if self.chat_display:
                self.chat_display.append_error(
                    "Stop the active response before deleting this session."
                )
            return False

        updated_history, removed = remove_chat_session_from_history(
            self._history_sessions,
            session_id,
        )
        if not removed:
            if self.chat_display:
                self.chat_display.append_error("Saved chat session no longer exists.")
            return False

        self._history_sessions = updated_history

        if open_session is not None:
            index = self._sessions.index_of(self._tab_widget, open_session)
            if index >= 0:
                widget = self._tab_widget.widget(index)
                self._tab_widget.removeTab(index)
                self._sessions.remove_for_widget(widget)
                widget.deleteLater()
            if self._tab_widget.count() == 0:
                self._add_new_tab(notify=False)

        logger.info("Deleted chat session from history: %s", session_id)
        self._notify_session_state_changed("history-delete")
        return True

    def _create_exchange_delete_dialog(self, session=None):
        """Build the delete-exchange browser for the active session."""
        session = session or self._active_session
        rows = build_chat_exchange_rows(getattr(session, "messages", []))
        logger.info(
            "Built exchange delete browser with %d exchange(s) for session %s",
            len(rows),
            getattr(session, "session_id", "<unknown>"),
        )
        return ExchangeDeleteDialog(
            rows=rows,
            session_title=getattr(session, "title", ""),
            parent=self,
        )

    def _open_exchange_delete_dialog(self):
        """Open the delete-exchange browser for the active chat tab."""
        session = self._active_session
        if session is None or not session.messages:
            if session:
                session.display.append_error("No exchanges are available to delete.")
            return False
        if session is self._generating_session:
            session.display.append_error(
                "Stop the active response before deleting an exchange."
            )
            return False

        dialog = self._create_exchange_delete_dialog(session)
        logger.info(
            "Opened exchange delete browser for session %s",
            session.session_id,
        )
        if dialog.exec_() != dialog.Accepted:
            return False

        exchange_index = dialog.selected_exchange_index()
        if exchange_index is None:
            return False
        return self._delete_exchange_from_session(session, exchange_index)

    def _delete_exchange_from_session(self, session, exchange_index):
        """Delete one selected exchange from a chat session."""
        updated_messages, removed = delete_chat_exchange(
            session.messages,
            exchange_index,
        )
        if not removed:
            session.display.append_error("The selected exchange no longer exists.")
            return False

        session.messages = updated_messages
        session.touch()
        session.display.rebuild_from_messages(session.messages)
        self._refresh_session_title(session)
        logger.info(
            "Deleted exchange %d from session %s",
            exchange_index + 1,
            session.session_id,
        )
        self._notify_session_state_changed("exchange-delete")
        return True

    def _send_prompt_text(self, text):
        """Append one user prompt to the active session and dispatch it."""
        if self._generating:
            return False

        session = self._active_session
        if session is None:
            return False

        prompt_text = (text or "").strip()
        if not prompt_text:
            return False

        if not self._current_model:
            session.display.append_error(
                "No chat model selected. Check the configured providers, then "
                "use 'Refresh Models' from the options menu."
            )
            return False

        self.chat_input.clear_text()
        session.display.append_user_message(prompt_text)
        session.messages.append({"role": "user", "content": prompt_text})
        session.touch()
        self._refresh_session_title(session)
        self._notify_session_state_changed("user-message")
        return self._dispatch_messages(
            session,
            self._build_request_messages(session),
        )

    def _dispatch_messages(self, session, request_messages, tool_calls=0):
        """Start one assistant turn for a session using prepared messages."""
        if not self._current_model:
            session.display.append_error(
                "No chat model selected. Check the configured providers, then "
                "use 'Refresh Models' from the options menu."
            )
            return False

        self._generating_session = session
        self._pending_turn = ChatTurnState(
            session=session,
            request_messages=list(request_messages),
            tool_calls=tool_calls,
        )
        session.display.start_assistant_message()
        self._set_generating(True)
        options = self._chat_options(session)
        logger.info(
            "Dispatching chat request for session %s via %s/%s with options %s",
            session.session_id,
            self._current_provider or "<provider>",
            self._current_model or "<model>",
            options,
        )
        self.sig_send_chat.emit(
            self._current_provider,
            self._current_model,
            list(request_messages),
            options,
        )
        return True

    def _build_request_messages(self, session):
        """Build the full request payload for the current chat session."""
        return (
            [{"role": "system", "content": self._build_system_prompt(session)}]
            + session.messages
        )

    def _build_system_prompt(self, session):
        """Build the system prompt plus the current editor/project context."""
        system_prompt = self.get_conf(
            "chat_system_prompt",
            default=(
                "You are a helpful AI coding assistant working inside "
                "the Spyder IDE. Be concise and provide code examples "
                "when relevant."
            ),
        )
        preset_id = normalize_chat_prompt_preset(
            getattr(session, "prompt_preset_id", None)
        )
        system_prompt = (
            f"{system_prompt}\n\n"
            f"{build_chat_prompt_preset_block(preset_id)}\n\n"
            f"{build_runtime_bridge_instructions()}"
        )
        logger.debug(
            "Building chat system prompt with preset %s for session %s",
            preset_id,
            getattr(session, "session_id", "<unknown>"),
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

        return system_prompt

    def _chat_options(self, session=None):
        """Return the resolved chat generation options for one chat tab."""
        metadata = self._chat_option_metadata(session)
        return {
            "temperature": metadata["temperature"],
            "num_predict": metadata["num_predict"],
        }

    def _regenerate_last_turn(self):
        """Re-run the last user turn on the active session."""
        if self._generating:
            return

        session = self._active_session
        if session is None or not session.messages:
            if session:
                session.display.append_error("No conversation is available to regenerate.")
            return

        if session.messages and session.messages[-1].get("role") == "assistant":
            session.messages.pop()

        if not session.messages or session.messages[-1].get("role") != "user":
            session.display.append_error(
                "Regenerate needs a previous user message on this tab."
            )
            return

        session.display.rebuild_from_messages(session.messages)
        session.touch()
        logger.info("Regenerating the last assistant answer for the active chat tab")
        self._notify_session_state_changed("regenerate")
        self._dispatch_messages(
            session,
            self._build_request_messages(session),
        )

    def _build_runtime_tooltip(self, detail=""):
        """Build the runtime-status tooltip from the cached snapshot."""
        runtime_context = self._runtime_context_snapshot or {}
        lines = []
        status = runtime_context.get("status", "unavailable")
        lines.append(f"Status: {status}")
        shell_label = runtime_context.get("shell_label", "")
        if shell_label:
            lines.append(f"Inspecting: {shell_label}")
        target_label = runtime_context.get("target_shell_label", "")
        active_label = runtime_context.get("active_shell_label", "")
        if target_label and target_label != active_label:
            lines.append(f"Target: {target_label}")
        if active_label:
            lines.append(f"Active console: {active_label}")
        if detail:
            lines.append(f"Detail: {detail}")
        cwd = runtime_context.get("working_directory", "")
        if cwd:
            lines.append(f"CWD: {cwd}")
        refreshed = runtime_context.get("last_refreshed_at", "")
        if refreshed:
            lines.append(f"Last refreshed: {refreshed}")
        variables = runtime_context.get("variables") or []
        if variables:
            lines.append(f"Tracked variables: {len(variables)}")
        if runtime_context.get("latest_error"):
            lines.append("Latest error: available")
        return "\n".join(lines)

    def _on_runtime_target_changed(self, index):
        """Apply one explicit runtime target selection from the toolbar."""
        del index
        if self._runtime_target_handler is None:
            return
        shell_id = str(self.runtime_target_combo.currentData() or "").strip()
        logger.info(
            "Chat widget runtime target changed to %s",
            shell_id or "<follow-active>",
        )
        self._runtime_target_handler(shell_id)

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
        self.session_btn.setEnabled(not generating)
        self.chat_settings_btn.setEnabled(not generating)
        self.debug_menu_btn.setEnabled(not generating)
        self.regenerate_btn.setEnabled(not generating)
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
        if isinstance(previous, dict):
            for i in range(self.model_combo.count()):
                if self.model_combo.itemData(i) == previous:
                    self.model_combo.setCurrentIndex(i)
                    return

        default_provider = self.get_conf("chat_provider", default="ollama")
        default_profile_id = self.get_conf(
            "chat_provider_profile_id",
            default="",
        )
        default = self.get_conf(
            "chat_model", default="gpt-oss-20b-abliterated"
        )
        for i in range(self.model_combo.count()):
            payload = self.model_combo.itemData(i)
            if not isinstance(payload, dict):
                continue
            if payload.get("name") != default:
                continue
            provider_kind = payload.get("provider_kind", payload.get("provider_id", ""))
            if provider_kind != default_provider:
                continue
            if (
                provider_kind == PROVIDER_KIND_OPENAI_COMPATIBLE
                and default_profile_id
                and payload.get("profile_id") != default_profile_id
            ):
                continue
            self.model_combo.setCurrentIndex(i)
            return

        if default_provider == PROVIDER_KIND_OPENAI_COMPATIBLE and default_profile_id:
            for i in range(self.model_combo.count()):
                payload = self.model_combo.itemData(i)
                if not isinstance(payload, dict):
                    continue
                if payload.get("provider_kind") != PROVIDER_KIND_OPENAI_COMPATIBLE:
                    continue
                if payload.get("profile_id") != default_profile_id:
                    continue
                self.model_combo.setCurrentIndex(i)
                return

        for i in range(self.model_combo.count()):
            payload = self.model_combo.itemData(i)
            if isinstance(payload, dict) and payload.get("name") == default:
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
        return self._dispatch_messages(
            session,
            self._pending_turn.request_messages,
            tool_calls=self._pending_turn.tool_calls,
        )

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
