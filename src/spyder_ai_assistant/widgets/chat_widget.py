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
from datetime import datetime

from qtpy.QtCore import Qt, Signal, QThread
from spyder.utils.icon_manager import ima
from qtpy.QtWidgets import (
    QVBoxLayout, QHBoxLayout, QSplitter, QPushButton, QComboBox, QLabel,
    QFileDialog, QMenu, QSizePolicy, QTabWidget, QToolButton,
)

from spyder.api.widgets.main_widget import PluginMainWidget
from spyder.config.gui import is_dark_interface

from spyder_ai_assistant.backend.worker import ChatWorker
from spyder_ai_assistant.utils.assistant_settings import (
    ASSISTANT_APPEARANCE_KEYS,
    AssistantSettings,
)
from spyder_ai_assistant.utils.chat_inference import (
    describe_chat_inference_source,
    format_chat_temperature,
    make_chat_inference_record,
    resolve_chat_inference_options,
)
from spyder_ai_assistant.utils.context import build_system_context_block
from spyder_ai_assistant.utils.provider_profiles import (
    PROVIDER_KIND_OPENAI_COMPATIBLE,
    resolve_preferred_profile,
    serialize_provider_profiles,
)
from spyder_ai_assistant.widgets.model_selection import (
    format_model_tooltip,
    populate_model_combo,
    select_model,
)
from spyder_ai_assistant.utils.prompt_library import (
    build_chat_prompt_preset_block,
    get_chat_prompt_preset,
    normalize_chat_prompt_preset,
)
from spyder_ai_assistant.utils.runtime_bridge import (
    build_runtime_bridge_instructions,
)
from spyder_ai_assistant.utils.chat_workflows import (
    DEBUG_ACTION_LABELS,
    build_debug_prompt,
    build_export_markdown,
)
from spyder_ai_assistant.widgets.assistant_settings_dialog import (
    AssistantSettingsDialog,
)
from spyder_ai_assistant.widgets.chat_input import ChatInput
from spyder_ai_assistant.widgets.chat_settings_dialog import ChatSettingsDialog
from spyder_ai_assistant.widgets.provider_profiles_dialog import (
    ProviderProfilesDialog,
)
from spyder_ai_assistant.widgets.session_controller import (
    ChatSession,
    SessionController,
)
from spyder_ai_assistant.widgets.turn_controller import TurnController

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# ChatWidget — the main dockable pane
# ---------------------------------------------------------------------------

# Panel width (px) below which the action row shows icons only. Above it the
# labels fit next to the icons; below it they would force a minimum dock
# width wider than Spyder's own side panes.
COMPACT_ACTION_ROW_WIDTH = 520


def action_row_button_style(panel_width):
    """Return the tool-button style for the action row at ``panel_width``."""
    if int(panel_width) < COMPACT_ACTION_ROW_WIDTH:
        return Qt.ToolButtonIconOnly
    return Qt.ToolButtonTextBesideIcon


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
        # Callable that changes the explicit runtime target shell.
        self._runtime_target_handler = None
        # Extracted controllers for session/history and turn lifecycle.
        self._session_ctrl = None
        self._turn_ctrl = None
        # Cached public runtime snapshot for toolbar status and exports.
        self._runtime_context_snapshot = {}
        # Cached runtime shell-target records used by the toolbar selector.
        self._runtime_shells = []
        # Latest provider diagnostics emitted by the worker after model refresh.
        self._provider_diagnostics = []
        # Latest provider-aware model payloads emitted by the worker.
        self._available_model_payloads = []
        # Currently open global assistant settings dialog, if any.
        self._assistant_settings_dialog = None
        # Callable returning the embedded MCP server status snapshot.
        self._mcp_server_status_provider = None
        # Callable launching one external MCP-aware client terminal.
        self._mcp_client_launcher = None

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
        self.model_combo.setMinimumWidth(140)
        self.model_combo.setSizeAdjustPolicy(QComboBox.AdjustToMinimumContentsLengthWithIcon)
        # Long enough for a typical "name (size)" label without truncation
        # while still fitting a narrow dock next to the mode selector.
        self.model_combo.setMinimumContentsLength(24)
        self.model_combo.setAccessibleName("Chat model")
        self.model_combo.addItem("Loading models…", None)
        self.model_combo.setEnabled(False)
        self.model_combo.setToolTip("Select the AI model for chat")
        self.model_combo.ID = "ai_chat_model_selector"

        self.status_label = QLabel("Connecting...")
        self.status_label.ID = "ai_chat_status_label"

        # Context label: shows current file and cursor line (e.g. "main.py:42")
        self.context_label = QLabel("")
        self.context_label.ID = "ai_chat_context_label"
        self.context_label.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        self.context_label.setToolTip("Current editor file and cursor position")

        # Runtime label: shows the active kernel state without dumping
        # console or variable content into the normal chat prompt path.
        self.runtime_label = QLabel("Kernel: unavailable")
        self.runtime_label.ID = "ai_chat_runtime_label"
        self.runtime_label.setToolTip("Active IPython console runtime status")

        self.runtime_target_combo = QComboBox(self)
        self.runtime_target_combo.setMinimumWidth(120)
        self.runtime_target_combo.setSizeAdjustPolicy(QComboBox.AdjustToMinimumContentsLengthWithIcon)
        self.runtime_target_combo.setMinimumContentsLength(12)
        self.runtime_target_combo.hide()
        self.runtime_target_combo.setToolTip(
            "Choose which Spyder IPython console the runtime bridge should inspect"
        )
        self.runtime_target_combo.ID = "ai_chat_runtime_target_selector"
        self.runtime_target_combo.addItem("Follow Active Console", "")

        toolbar = self.get_main_toolbar()
        self.add_item_to_toolbar(
            self.model_combo, toolbar=toolbar, section="main",
        )
        # Secondary context belongs below the model selector so a narrow dock
        # does not hide all controls in the toolbar overflow menu.
        context_row = QHBoxLayout()
        context_row.addWidget(self.context_label, 1)
        context_row.addWidget(self.runtime_label)
        context_row.addWidget(self.runtime_target_combo)

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
            text="Tab settings...",
            triggered=self._open_chat_settings_dialog,
        )
        self._assistant_settings_action = self.create_action(
            "ai_chat_assistant_settings",
            text="Assistant Settings...",
            triggered=self._open_assistant_settings_dialog,
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
        self.add_item_to_menu(self._assistant_settings_action, menu=options_menu)
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

        self._session_ctrl = SessionController(
            self._tab_widget,
            appearance_applier=self._apply_current_appearance,
            generating_session_getter=lambda: self._generating_session,
            session_initializer=self._initialize_session,
        )

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
            for action in DEBUG_ACTION_LABELS
        }
        self.debug_menu_btn = QToolButton(self)
        self.debug_menu_btn.setText("Debug")
        self.debug_menu_btn.setIcon(ima.icon("bug"))
        self.debug_menu_btn.setPopupMode(QToolButton.InstantPopup)
        self.debug_menu_btn.setToolTip(
            "Runtime-aware debugging actions for the active chat tab"
        )
        debug_menu = QMenu(self.debug_menu_btn)
        for action in DEBUG_ACTION_LABELS:
            debug_menu.addAction(self._debug_actions[action])
        self.debug_menu_btn.setMenu(debug_menu)

        self.regenerate_btn = QToolButton(self)
        self.regenerate_btn.setText("Regenerate")
        self.regenerate_btn.setIcon(ima.icon("restart"))
        self.regenerate_btn.setToolTip(
            "Remove the last assistant answer on this tab and ask again"
        )
        self.chat_settings_btn = QToolButton(self)
        self.chat_settings_btn.setText("Settings")
        self.chat_settings_btn.setIcon(ima.icon("configure"))
        self.chat_settings_btn.setToolTip(
            "Settings for the assistant and this chat tab"
        )
        # The whole button opens the menu. A split button that opened a
        # different dialog on click than its arrow did hid the per-tab
        # settings behind the arrow (GitHub issue #4).
        self.chat_settings_btn.setPopupMode(QToolButton.InstantPopup)
        settings_menu = QMenu(self.chat_settings_btn)
        settings_menu.addAction(self._assistant_settings_action)
        settings_menu.addAction(self._chat_settings_action)
        settings_menu.addAction(self._provider_profiles_action)
        settings_menu.addSeparator()
        settings_menu.addAction(refresh_action)
        self.chat_settings_btn.setMenu(settings_menu)
        self.session_btn = QToolButton(self)
        self.session_btn.setText("Sessions")
        self.session_btn.setIcon(ima.icon("history"))
        self.session_btn.setPopupMode(QToolButton.MenuButtonPopup)
        self.session_btn.setToolTip(
            "Browse saved chats and open other session actions"
        )
        session_menu = QMenu(self.session_btn)
        session_menu.addAction(self._history_action)
        session_menu.addAction(self._new_tab_action)
        session_menu.addAction(self._delete_exchange_action)
        session_menu.addAction(self._export_action)
        self.session_btn.setMenu(session_menu)
        # Backward-compatible alias used by older harnesses.
        self.history_btn = self.session_btn

        self.stop_btn = QPushButton("Stop")
        self.send_btn = QPushButton("Send")
        self.stop_btn.setEnabled(False)
        self.stop_btn.hide()
        self.stop_btn.setToolTip("Stop the current response")
        self.send_btn.setToolTip("Send message (Enter)")
        for button in (self.send_btn, self.stop_btn):
            button.setMinimumHeight(30)
        self.send_btn.setEnabled(False)
        self._action_row_buttons = (
            self.debug_menu_btn,
            self.regenerate_btn,
            self.session_btn,
            self.chat_settings_btn,
        )
        for button in self._action_row_buttons:
            # An explicit minimum (icon-only size) replaces the label-based
            # minimum size hint, so the dock can shrink below the labelled
            # width; resizeEvent then drops the labels before anything clips.
            button.setMinimumWidth(28)
        self._apply_action_row_style(self.width())
        controls_layout.addWidget(self.debug_menu_btn)
        controls_layout.addWidget(self.regenerate_btn)
        controls_layout.addWidget(self.session_btn)
        controls_layout.addWidget(self.chat_settings_btn)
        controls_layout.addStretch()
        controls_layout.addWidget(self.stop_btn)
        controls_layout.addWidget(self.send_btn)

        # Assemble the content layout (splitter + compact controls)
        content_layout = QVBoxLayout()
        content_layout.setContentsMargins(6, 4, 6, 4)
        content_layout.setSpacing(6)
        content_layout.addLayout(context_row)
        content_layout.addWidget(splitter)
        content_layout.addLayout(controls_layout)
        footer = QHBoxLayout()
        input_hint = QLabel("Enter to send · Shift+Enter for a new line")
        hint_font = input_hint.font()
        hint_font.setPointSizeF(max(8, hint_font.pointSizeF() - 1))
        input_hint.setFont(hint_font)
        footer.addWidget(input_hint)
        footer.addStretch()
        footer.addWidget(self.status_label)
        content_layout.addLayout(footer)

        self.setLayout(content_layout)

        # --- Background worker thread ---
        self._thread = QThread(None)
        self._worker = ChatWorker(settings=self._chat_provider_settings())
        self._worker.moveToThread(self._thread)
        self._turn_ctrl = TurnController(
            send_chat_emitter=self.sig_send_chat.emit,
            worker_abort=self._worker.abort,
        )
        self._turn_ctrl.context_provider = self._context_provider
        self._turn_ctrl.runtime_status_notifier = self.status_label.setText

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
        self.chat_input.textChanged.connect(self._sync_send_controls)
        self.chat_input.submit_requested.connect(self._send_message)
        self.send_btn.clicked.connect(self._send_message)
        self.stop_btn.clicked.connect(self._stop_generation)
        self.session_btn.clicked.connect(self._open_history_browser)
        self.regenerate_btn.clicked.connect(self._regenerate_last_turn)
        self.model_combo.currentIndexChanged.connect(
            self._on_model_changed
        )
        self.runtime_target_combo.currentIndexChanged.connect(
            self._on_runtime_target_changed
        )
        self._tab_widget.currentChanged.connect(self._on_current_tab_changed)
        self._sync_session_controls()

        # Start the worker thread and fetch available models
        self._thread.start()
        self.sig_list_models.emit()

    def resizeEvent(self, event):
        """Switch the action row between labelled and icon-only buttons."""
        super().resizeEvent(event)
        self._apply_action_row_style(event.size().width())

    def _apply_action_row_style(self, panel_width):
        """Apply the responsive tool-button style to the action row."""
        style = action_row_button_style(panel_width)
        for button in getattr(self, "_action_row_buttons", ()):
            if button.toolButtonStyle() != style:
                button.setToolButtonStyle(style)

    def update_actions(self):
        """Called by Spyder when the widget gains/loses focus.

        Required by the PluginMainWidget interface. No focus-dependent
        actions to update.
        """
        pass

    def _sync_chat_settings_button(self, session=None):
        """Reflect assistant settings entrypoint and active tab overrides."""
        if session is None:
            session = self._active_session

        if session is None:
            self.chat_settings_btn.setText("Settings")
            self.chat_settings_btn.setToolTip(
                "Settings for the assistant and this chat tab"
            )
            return

        metadata = self._chat_option_metadata(session)
        mode = get_chat_prompt_preset(getattr(session, "prompt_preset_id", None))
        has_override = (
            metadata["temperature_source"] == "override"
            or metadata["num_predict_source"] == "override"
            or mode["id"] != normalize_chat_prompt_preset(None)
        )
        self.chat_settings_btn.setText("Settings*" if has_override else "Settings")
        self.chat_settings_btn.setToolTip(
            "\n".join(
                [
                    "Settings for the assistant and this chat tab.",
                    "A * marks a tab whose settings differ from the defaults.",
                    self._build_chat_settings_tooltip(metadata),
                ]
            )
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
            f"Saved sessions: {len(self._session_ctrl.history_sessions)}"
        )

    def _sync_session_controls(self, session=None):
        """Refresh the shared per-tab controls from the active session."""
        self._sync_chat_settings_button(session=session)
        self._sync_session_menu_button(session=session)
        if hasattr(self, "send_btn"):
            self._sync_send_controls()

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
        return self._assistant_settings().provider_profiles_list()

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
            # The combo always holds one row (a placeholder while loading or
            # when discovery found nothing), so count() is not a signal of
            # availability. Use the discovered payloads instead.
            models_available = bool(self._available_model_payloads)
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
        if self._session_ctrl is None:
            return None
        return self._session_ctrl.active_session

    @property
    def _generating(self):
        """Return whether one assistant response is in flight."""
        if self._turn_ctrl is None:
            return False
        return self._turn_ctrl.generating

    @property
    def _generating_session(self):
        """Return the session receiving streamed assistant output."""
        if self._turn_ctrl is None:
            return None
        return self._turn_ctrl.generating_session

    @property
    def _pending_turn(self):
        """Return the current hidden runtime-loop state."""
        if self._turn_ctrl is None:
            return None
        return self._turn_ctrl.pending_turn

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
        self._session_ctrl.close_tab(index)

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
        action, payload = self._turn_ctrl.process_response(full_text, session)

        if action == "error":
            self._on_error(payload)
            return

        if action == "runtime_continue":
            self._dispatch_messages(
                session,
                payload,
                tool_calls=self._turn_ctrl.pending_tool_calls,
            )
            return

        if session and action == "empty":
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
            self._turn_ctrl.finish_turn()
            self._set_generating(False)
            self.status_label.setText("Empty response")
            return

        if session:
            session.display.finish_assistant_message()
            session.messages.append({
                "role": "assistant", "content": payload
            })
            session.touch()
            self._refresh_session_title(session)
            self._notify_session_state_changed("assistant-response")

        self._turn_ctrl.finish_turn()
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
        self._available_model_payloads = [
            dict(model) for model in (models or []) if isinstance(model, dict)
        ]
        previous = self.model_combo.currentData()

        populate_model_combo(
            self.model_combo,
            self._available_model_payloads,
            placeholder="No models — open Settings",
        )
        self.model_combo.setEnabled(bool(self._available_model_payloads))
        self.model_combo.blockSignals(True)
        try:
            self._select_default_model(previous)
        finally:
            self.model_combo.blockSignals(False)

        self._on_model_changed(self.model_combo.currentIndex())
        self._sync_send_controls()
        self._sync_provider_status_label(models_available=bool(models))
        if self._assistant_settings_dialog is not None:
            self._assistant_settings_dialog.replace_models(
                self._discovered_models_snapshot()
            )

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
        self._turn_ctrl.finish_turn()
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
        session = self._generating_session
        self._turn_ctrl.abort_generation()
        if session:
            session.display.finish_assistant_message()
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
                session.display.append_info("No messages to export.")
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
            self.model_combo.setToolTip(format_model_tooltip(payload))
            preferred_kind = payload.get(
                "provider_kind",
                self.get_conf("chat_provider", default="ollama"),
            )
            if (
                self.get_conf("chat_provider_profile_id", default="")
                != self._current_provider_profile_id
            ):
                self.set_conf(
                    "chat_provider_profile_id",
                    self._current_provider_profile_id,
                )
            if preferred_kind != self.get_conf("chat_provider", default="ollama"):
                self.set_conf("chat_provider", preferred_kind)
            if self.get_conf("chat_model", default="") != self._current_model:
                self.set_conf("chat_model", self._current_model)
        # Send/Regenerate depend on a real model being selected, so every
        # selection change (manual or programmatic) re-evaluates them.
        self._sync_send_controls()

    # --- Appearance config keys that map to ChatDisplay.update_appearance ---
    _APPEARANCE_KEYS = ASSISTANT_APPEARANCE_KEYS

    def _initialize_session(self, session):
        """Attach widget-owned signal wiring to one chat session."""
        session.display.sig_apply_code_requested.connect(self.sig_apply_code)

    def _apply_current_appearance(self, display):
        """Apply all current appearance config values to one ChatDisplay."""
        kwargs = {"is_dark": is_dark_interface()}
        for key in self._APPEARANCE_KEYS:
            try:
                kwargs[key] = self.get_conf(key)
            except Exception:
                pass  # key not in config yet — use display default
        if kwargs:
            display.update_appearance(**kwargs)

    def update_all_display_appearance(self, **kwargs):
        """Push appearance settings to all active ChatDisplay widgets.

        Called by the plugin's @on_conf_change handlers when the user
        changes appearance settings (font, code font, bubble geometry, etc.).
        Iterates all chat sessions and calls update_appearance on each display.
        """
        for session in self._session_ctrl.ordered_sessions():
            session.display.update_appearance(**kwargs)

    def sync_model_selection_from_conf(self):
        """Apply the configured provider/model preference without relisting."""
        if not self._available_model_payloads:
            # Only the placeholder row exists: nothing to select yet, so
            # push the new settings to the worker and let discovery relist.
            self.update_chat_provider_settings()
            return False

        self.model_combo.blockSignals(True)
        try:
            self._select_default_model("")
        finally:
            self.model_combo.blockSignals(False)
        self._on_model_changed(self.model_combo.currentIndex())
        return True

    def _chat_default_options(self):
        """Return the normalized global chat defaults from preferences."""
        return self._assistant_settings().chat_default_options()

    def _discovered_models_snapshot(self):
        """Return the latest provider-aware model payloads for settings UI."""
        if self._available_model_payloads:
            return [dict(model) for model in self._available_model_payloads]

        models = []
        for index in range(self.model_combo.count()):
            payload = self.model_combo.itemData(index)
            if isinstance(payload, dict):
                models.append(dict(payload))
        return models

    def _assistant_settings_snapshot(self):
        """Return the global assistant settings exposed from the pane."""
        return self._assistant_settings().to_conf_dict()

    def _assistant_settings(self):
        """Return the current assistant settings as one normalized snapshot."""
        return AssistantSettings.from_conf(self.get_conf)

    def _create_assistant_settings_dialog(self):
        """Build the global assistant settings dialog from current state."""
        dialog = AssistantSettingsDialog(
            models=self._discovered_models_snapshot(),
            settings=self._assistant_settings_snapshot(),
            mcp_status=self._mcp_server_status_snapshot(),
            mcp_client_launcher=self._mcp_client_launcher,
            parent=self,
        )
        dialog.manage_profiles_requested.connect(
            self._open_provider_profiles_dialog
        )
        dialog.manage_profiles_requested.connect(
            lambda: dialog.replace_models(self._discovered_models_snapshot())
        )
        dialog.refresh_models_requested.connect(self._refresh_models)
        dialog.refresh_models_requested.connect(
            lambda: dialog.replace_models(self._discovered_models_snapshot())
        )
        return dialog

    def _apply_assistant_settings(self, settings):
        """Persist the global assistant settings exposed from the pane."""
        current = self._assistant_settings()
        updated = AssistantSettings.from_mapping(settings)
        current_dict = current.to_conf_dict()
        updated_dict = updated.to_conf_dict()
        # Provider profiles are edited and persisted by their own nested
        # dialog.  Preserve its latest values if that dialog was used while
        # this assistant-settings snapshot was already open.
        for key in (
            "provider_profiles",
            "openai_compatible_base_url",
            "openai_compatible_api_key",
        ):
            updated_dict[key] = current_dict[key]
        updated = AssistantSettings.from_mapping(updated_dict)
        changed_provider = False
        changed_model = False
        changed_any = False

        for key, value in updated_dict.items():
            if current_dict.get(key) == value:
                continue
            self.set_conf(key, value)
            changed_any = True
            if key in {"ollama_host", "chat_provider", "chat_provider_profile_id"}:
                changed_provider = True
            if key in {"chat_model", "completion_model"}:
                changed_model = True

        if not changed_any:
            return False

        logger.info(
            "Saved assistant settings: provider=%s profile=%s chat_model=%s completion_model=%s",
            updated.chat_provider,
            updated.chat_provider_profile_id or "<none>",
            updated.chat_model,
            updated.completion_model,
        )

        if changed_provider:
            self.update_chat_provider_settings()
        elif changed_model:
            self.sync_model_selection_from_conf()

        return True

    def _open_assistant_settings_dialog(self):
        """Open the global assistant settings dialog from the pane."""
        dialog = self._create_assistant_settings_dialog()
        self._assistant_settings_dialog = dialog
        try:
            if dialog.exec_() != dialog.Accepted:
                self._sync_chat_settings_button(self._active_session)
                return False
            return self._apply_assistant_settings(dialog.selected_settings())
        finally:
            if self._assistant_settings_dialog is dialog:
                self._assistant_settings_dialog = None

    def _chat_temperature_conf_value(self):
        """Return one safe config-backed chat temperature source value."""
        return self._assistant_settings().chat_temperature

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
                "Tab overrides:",
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
            prompt_preset_id=getattr(session, "prompt_preset_id", None),
            parent=self,
        )

    def set_prompt_preset(self, preset_id, session=None):
        """Set the chat mode (prompt preset) of one tab; returns True on change."""
        session = session or self._active_session
        if session is None:
            return False
        normalized = normalize_chat_prompt_preset(preset_id)
        if session.prompt_preset_id == normalized:
            return False
        session.prompt_preset_id = normalized
        session.touch()
        logger.info(
            "Chat prompt preset set to %s for session %s",
            get_chat_prompt_preset(normalized)["label"],
            session.session_id,
        )
        self._sync_chat_settings_button(session)
        self._notify_session_state_changed("prompt-preset")
        return True

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

        changed = self.set_prompt_preset(dialog.selected_prompt_preset_id(), session)
        return self._apply_chat_settings(session, dialog.selected_overrides()) or changed

    # --- Public API (called by plugin) ---

    def set_context_provider(self, provider):
        """Set the callable that provides editor context.

        Args:
            provider: Callable returning a dict with editor context, or
                empty dict if no editor is active.
        """
        self._context_provider = provider
        if self._turn_ctrl is not None:
            self._turn_ctrl.context_provider = provider

    def set_runtime_request_executor(self, executor):
        """Set the callable that executes one runtime inspection request."""
        self._turn_ctrl.runtime_request_executor = executor

    def set_runtime_target_handler(self, handler):
        """Set the callable that changes the explicit runtime target shell."""
        self._runtime_target_handler = handler

    def set_session_state_changed_callback(self, callback):
        """Set the callback invoked when chat session state changes."""
        self._session_ctrl.session_state_changed_callback = callback

    def set_session_scope_provider(self, provider):
        """Set the callable that returns the current history-browser scope."""
        self._session_ctrl.session_scope_provider = provider

    def set_mcp_server_status_provider(self, provider):
        """Set the callable that returns the embedded MCP server status."""
        self._mcp_server_status_provider = provider

    def set_mcp_client_launcher(self, launcher):
        """Set the callable that launches one external MCP-aware client."""
        self._mcp_client_launcher = launcher

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

    def _mcp_server_status_snapshot(self):
        """Return the latest available MCP server status from the plugin."""
        provider = self._mcp_server_status_provider
        if not callable(provider):
            return {}
        try:
            return dict(provider() or {})
        except Exception:
            logger.exception("Failed to fetch embedded MCP server status")
            return {}

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
        self.runtime_target_combo.setVisible(len(self._runtime_shells) > 1)
        logger.debug(
            "Updated runtime shell targets with %d option(s); selected=%s",
            len(self._runtime_shells),
            selected_shell_id or "<follow-active>",
        )

    def _chat_provider_settings(self):
        """Return one snapshot of provider settings for the worker."""
        return self._assistant_settings().chat_provider_settings()

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
        return self._session_ctrl.serialize_session_state()

    def clear_all_tabs(self):
        """Close every chat tab and forget its session (used before a restore)."""
        self._session_ctrl.clear_all_tabs()

    def restore_session_state(self, state):
        """Restore tabs and messages from persisted state."""
        return self._session_ctrl.restore_session_state(state)

    # --- Internal helpers ---

    def _notify_session_state_changed(self, reason):
        """Notify the plugin layer that persisted session state changed."""
        self._session_ctrl.notify_session_state_changed(reason)

    def _add_session(self, session, notify=True):
        """Insert one chat session into the tab widget."""
        return self._session_ctrl.add_session(session, notify=notify)

    def _refresh_session_title(self, session):
        """Keep the tab title aligned with the first visible user message."""
        self._session_ctrl.refresh_session_title(session)

    def _session_scope_info(self):
        """Return metadata for the current chat history scope."""
        return self._session_ctrl.session_scope_info()

    def _open_history_browser(self):
        """Open the saved-session history browser and apply one chosen action."""
        return self._session_ctrl.open_history_browser()

    def _open_exchange_delete_dialog(self):
        """Open the delete-exchange browser for the active chat tab."""
        return self._session_ctrl.open_exchange_delete_dialog()

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
                "use 'Refresh Models' from Settings."
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
                "use 'Refresh Models' from Settings."
            )
            return False

        session.display.start_assistant_message()
        options = self._chat_options(session)
        logger.info(
            "Dispatching chat request for session %s via %s/%s with options %s",
            session.session_id,
            self._current_provider or "<provider>",
            self._current_model or "<model>",
            options,
        )
        dispatched = self._turn_ctrl.dispatch_messages(
            session,
            request_messages,
            self._current_provider,
            self._current_model,
            options,
            tool_calls=tool_calls,
        )
        if dispatched:
            self._set_generating(True)
        return dispatched

    def _build_request_messages(self, session):
        """Build the full request payload for the current chat session."""
        return self._turn_ctrl.build_request_messages(
            session,
            self._build_system_prompt(session),
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
            f"{build_runtime_bridge_instructions(include_project_tools=bool(self.get_conf('project_tools_enabled', default=True)))}"
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
        request_messages = self._turn_ctrl.regenerate_last_turn(
            session,
            self._build_system_prompt(session) if session is not None else "",
        )
        if request_messages is None:
            return
        logger.info("Regenerating the last assistant answer for the active chat tab")
        self._notify_session_state_changed("regenerate")
        self._dispatch_messages(
            session,
            request_messages,
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

    def _set_generating(self, generating):
        """Update UI controls for generation-in-progress state."""
        self.send_btn.setVisible(not generating)
        self.stop_btn.setVisible(generating)
        self.stop_btn.setEnabled(generating)
        self.chat_input.setEnabled(not generating)
        self.session_btn.setEnabled(not generating)
        self.chat_settings_btn.setEnabled(not generating)
        self.debug_menu_btn.setEnabled(not generating)
        self._sync_send_controls()
        if generating:
            self.start_spinner()
        else:
            self.stop_spinner()

    def _sync_send_controls(self):
        """Keep action availability aligned with the active session and input."""
        ready = not self._generating and bool(self._current_model)
        self.send_btn.setEnabled(ready and bool(self.chat_input.peek_text()))
        session = self._active_session
        self.regenerate_btn.setEnabled(ready and bool(
            session and any(m.get("role") == "user" for m in session.messages)
        ))

    def _select_default_model(self, previous=""):
        """Select the best model row: previous selection, then configured
        default (provider/profile aware), then first available."""
        settings = self._assistant_settings()
        select_model(
            self.model_combo,
            name=settings.chat_model,
            provider_kind=settings.chat_provider,
            profile_id=settings.chat_provider_profile_id,
            previous=previous if isinstance(previous, dict) else None,
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
