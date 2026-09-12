"""Global assistant settings dialog opened from the chat pane."""

from __future__ import annotations

from qtpy.QtCore import Qt, Signal
from qtpy.QtGui import QColor, QFont, QPixmap, QIcon
from qtpy.QtWidgets import QApplication
from qtpy.QtWidgets import (
    QCheckBox,
    QColorDialog,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QFontComboBox,
    QFormLayout,
    QFrame,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPlainTextEdit,
    QPushButton,
    QScrollArea,
    QSpinBox,
    QTabWidget,
    QTextEdit,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from spyder_ai_assistant.mcp.launch import (
    MCP_CLIENT_CLAUDE,
    MCP_CLIENT_CODEX,
    MCP_CLIENT_OPENCODE,
    get_mcp_client_label,
)
from spyder_ai_assistant.mcp.settings import (
    DEFAULT_MCP_HOST,
    DEFAULT_MCP_PORT,
    DEFAULT_MCP_SERVER_NAME,
    build_client_setup_snippets,
    build_mcp_endpoint_url,
    normalize_mcp_host,
    normalize_mcp_port,
)
from spyder_ai_assistant.utils.assistant_settings import (
    ASSISTANT_OPTION_RANGES,
    DEFAULT_NATIVE_POPUP_POLICY,
    NATIVE_POPUP_POLICIES,
    NATIVE_POPUP_POLICY_DESCRIPTIONS,
    NATIVE_POPUP_POLICY_LABELS,
    AssistantSettings,
)
from spyder_ai_assistant.utils.chat_inference import normalize_chat_temperature
from spyder_ai_assistant.utils.constants import DEFAULT_OLLAMA_HOST
from spyder_ai_assistant.utils.chat_themes import (
    EXPOSED_COLOR_KEYS,
    get_preset_names,
    get_theme_colors,
    parse_color_overrides,
    serialize_color_overrides,
)
from spyder_ai_assistant.widgets.model_selection import (
    populate_model_combo,
    provider_key,
    select_model,
)


class ColorSwatchButton(QToolButton):
    """A small button that shows a color swatch and opens a color picker."""

    color_changed = Signal(str, str)  # (color_key, hex_color)

    def __init__(self, color_key, label, initial_color="#000000", parent=None):
        super().__init__(parent)
        self._color_key = color_key
        self._label = label
        self._color = initial_color
        self._is_overridden = False
        self.setFixedSize(28, 28)
        self.setToolTip(f"{label}: click to change")
        self._update_icon()
        self.clicked.connect(self._pick_color)

    def _update_icon(self):
        """Redraw the swatch icon with the current color."""
        pixmap = QPixmap(24, 24)
        pixmap.fill(QColor(self._color))
        self.setIcon(QIcon(pixmap))
        self.setIconSize(pixmap.size())

    def set_color(self, hex_color, is_override=False):
        """Set the displayed color and override state."""
        self._color = hex_color
        self._is_overridden = is_override
        self._update_icon()
        # Visual cue: bold border when overridden
        if is_override:
            self.setStyleSheet(
                "QToolButton { border: 2px solid #ff8800; border-radius: 4px; }"
            )
        else:
            self.setStyleSheet("")

    def _pick_color(self):
        """Open a color dialog and emit the chosen color."""
        current = QColor(self._color)
        chosen = QColorDialog.getColor(current, self, f"Choose {self._label}")
        if chosen.isValid():
            hex_color = chosen.name()
            self._color = hex_color
            self._is_overridden = True
            self._update_icon()
            self.set_color(hex_color, is_override=True)
            self.color_changed.emit(self._color_key, hex_color)

    @property
    def color_key(self):
        return self._color_key

    @property
    def is_overridden(self):
        return self._is_overridden


class AssistantSettingsDialog(QDialog):
    """Edit global assistant and completion settings in one place."""

    manage_profiles_requested = Signal()
    refresh_models_requested = Signal()

    def __init__(
        self,
        *,
        models=None,
        settings=None,
        mcp_status=None,
        mcp_client_launcher=None,
        parent=None,
    ):
        super().__init__(parent)
        self.setWindowTitle("Assistant Settings")
        # Tall enough that the common tabs need no scrolling; the scroll
        # areas below only take over on dense tabs or small screens.
        self.resize(820, 900)

        self._models = [dict(model) for model in (models or []) if isinstance(model, dict)]
        self._settings = AssistantSettings.from_mapping(settings).to_conf_dict()
        self._mcp_status = dict(mcp_status or {})
        self._mcp_client_launcher = mcp_client_launcher
        self._completion_model_payloads = []

        layout = QVBoxLayout(self)

        intro = QLabel(
            "Defaults for every chat tab. Per-tab values live in "
            "Settings > Tab settings, provider endpoints in Provider Profiles."
        )
        intro.setWordWrap(True)
        layout.addWidget(intro)

        tabs = QTabWidget(self)
        layout.addWidget(tabs)

        # Tabs are grouped by the task the user came to do. All four are
        # built before the populate/load calls below, so every widget the
        # settings are pushed into already exists.
        tabs.addTab(self._as_scrollable(self._build_chat_tab()), "Chat")
        tabs.addTab(
            self._as_scrollable(self._build_completions_tab()), "Completions"
        )
        tabs.addTab(
            self._as_scrollable(self._build_appearance_tab()), "Appearance"
        )
        tabs.addTab(self._as_scrollable(self._build_advanced_tab()), "Advanced")

        self.button_box = QDialogButtonBox(
            QDialogButtonBox.Cancel | QDialogButtonBox.Save,
            parent=self,
        )
        self.button_box.accepted.connect(self.accept)
        self.button_box.rejected.connect(self.reject)
        layout.addWidget(self.button_box)

        self._populate_model_combos()
        self._load_settings()
        self.mcp_host_edit.textChanged.connect(self._refresh_mcp_preview)
        self.mcp_port_spin.valueChanged.connect(self._refresh_mcp_preview)

    def _as_scrollable(self, page):
        """Return ``page`` wrapped in a vertical-only scroll area.

        Without this the dialog's minimum height grows to the tallest tab.
        The Advanced tab needs 950 px, so the dialog opened 1080 px tall and
        still squeezed that page: the OpenCode buttons were drawn over the
        JSON box and the MCP status note lost a line. Scrolling keeps the
        dialog at its intended 760x720 and lets a page exceed the window.
        """
        area = QScrollArea(self)
        area.setWidget(page)
        area.setWidgetResizable(True)
        area.setFrameShape(QFrame.NoFrame)
        # Every group wraps its own text, so a horizontal bar would only
        # hide content that could have been laid out narrower.
        area.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        return area

    def _build_chat_tab(self):
        """Return the Chat tab: everything that shapes a chat reply.

        Model choice, the defaults every chat tab starts from, and the
        prompt templates live together so a chat change needs one tab.
        """
        chat_tab = QWidget(self)
        layout = QVBoxLayout(chat_tab)
        layout.addWidget(self._build_chat_model_group(chat_tab))
        layout.addWidget(self._build_chat_defaults_group(chat_tab))
        layout.addWidget(self._build_system_prompt_group(chat_tab))
        layout.addWidget(self._build_editor_action_prompts_group(chat_tab))
        layout.addStretch(1)
        return chat_tab

    def _build_completions_tab(self):
        """Return the Completions tab: everything about ghost text.

        Model, generation defaults, the two delays, who owns the popup and
        the keyboard shortcuts were previously spread over three tabs.
        """
        completions_tab = QWidget(self)
        layout = QVBoxLayout(completions_tab)
        layout.addWidget(self._build_completion_model_group(completions_tab))
        layout.addWidget(self._build_completion_defaults_group(completions_tab))
        layout.addWidget(self._build_ghost_text_group(completions_tab))
        layout.addWidget(self._build_native_popup_group(completions_tab))
        layout.addWidget(self._build_shortcuts_group(completions_tab))
        layout.addStretch(1)
        return completions_tab

    def _build_appearance_tab(self):
        """Return the Appearance tab: theme, fonts, and bubble geometry."""
        appearance_tab = QWidget(self)
        layout = QVBoxLayout(appearance_tab)
        layout.addWidget(self._build_color_theme_group(appearance_tab))
        layout.addWidget(self._build_chat_font_group(appearance_tab))
        layout.addWidget(self._build_code_blocks_group(appearance_tab))
        layout.addWidget(self._build_message_bubbles_group(appearance_tab))
        layout.addStretch(1)
        return appearance_tab

    def _build_advanced_tab(self):
        """Return the Advanced tab: endpoints, access, and the MCP server.

        Set-once plumbing that neither chatting nor completing needs day to
        day, kept out of the way of the two task tabs.
        """
        advanced_tab = QWidget(self)
        layout = QVBoxLayout(advanced_tab)
        layout.addWidget(self._build_local_endpoint_group(advanced_tab))
        layout.addWidget(self._build_providers_group(advanced_tab))
        layout.addWidget(self._build_project_access_group(advanced_tab))
        layout.addWidget(self._build_mcp_server_group(advanced_tab))
        layout.addWidget(self._build_client_setup_group(advanced_tab))
        layout.addStretch(1)
        return advanced_tab

    def _build_chat_model_group(self, parent):
        """Return the Chat model group: which model answers in the chat.

        The chat model also fixes which provider the completion model may
        come from, so its selection signal rebuilds that other list.
        """
        models_group = QGroupBox("Chat model", parent)
        models_form = QFormLayout(models_group)
        self.chat_model_combo = QComboBox(models_group)
        self.chat_model_combo.currentIndexChanged.connect(
            self._refresh_completion_model_options
        )
        models_form.addRow("Default chat model", self.chat_model_combo)
        return models_group

    def _build_completion_model_group(self, parent):
        """Return the Completion model group: the ghost-text model.

        Populated by ``_refresh_completion_model_options`` from the chat
        model's provider, which is why no options are added here.
        """
        completion_model_group = QGroupBox("Completion model", parent)
        completion_model_form = QFormLayout(completion_model_group)
        self.completion_model_combo = QComboBox(completion_model_group)
        completion_model_form.addRow(
            "Default completion model", self.completion_model_combo
        )
        return completion_model_group

    def _build_local_endpoint_group(self, parent):
        """Return the Local endpoint group: the local Ollama host."""
        local_group = QGroupBox("Local endpoint", parent)
        local_form = QFormLayout(local_group)
        self.ollama_host_edit = QLineEdit(local_group)
        self.ollama_host_edit.setPlaceholderText(DEFAULT_OLLAMA_HOST)
        # A mistyped endpoint used to fail silently: models simply never
        # appeared. Say so while the field is being edited instead.
        self.ollama_host_note_label = QLabel(local_group)
        self.ollama_host_note_label.setWordWrap(True)
        self.ollama_host_note_label.setVisible(False)
        self.ollama_host_edit.textChanged.connect(
            self._refresh_ollama_host_note
        )
        local_form.addRow("Ollama host", self.ollama_host_edit)
        local_form.addRow("", self.ollama_host_note_label)
        return local_group

    def _build_providers_group(self, parent):
        """Return the Providers group: where model dropdowns come from."""
        provider_group = QGroupBox("Providers", parent)
        provider_layout = QVBoxLayout(provider_group)
        provider_note = QLabel(
            "OpenAI-compatible endpoints are managed through Provider Profiles. "
            "Chat and completion model dropdowns are populated from the "
            "recognized models returned by the local Ollama endpoint and "
            "any enabled provider profiles."
        )
        provider_note.setWordWrap(True)
        provider_layout.addWidget(provider_note)
        provider_button_row = QHBoxLayout()
        self.refresh_models_btn = QPushButton("Refresh Models", provider_group)
        self.refresh_models_btn.clicked.connect(self.refresh_models_requested.emit)
        self.manage_profiles_btn = QPushButton("Provider Profiles...", provider_group)
        self.manage_profiles_btn.clicked.connect(self.manage_profiles_requested.emit)
        provider_button_row.addWidget(self.refresh_models_btn)
        provider_button_row.addWidget(self.manage_profiles_btn)
        provider_button_row.addStretch()
        provider_layout.addLayout(provider_button_row)
        return provider_group

    def _build_chat_defaults_group(self, parent):
        """Return the Chat defaults group: temperature and token budget.

        The title names "all tabs" because these are the values a new chat
        tab starts from, not the setting of whichever tab is open.
        """
        chat_group = QGroupBox("Chat defaults (all tabs)", parent)
        chat_form = QFormLayout(chat_group)
        self.chat_temperature_spin = QDoubleSpinBox(chat_group)
        self.chat_temperature_spin.setDecimals(1)
        self.chat_temperature_spin.setRange(*ASSISTANT_OPTION_RANGES["chat_temperature"])
        self.chat_temperature_spin.setSingleStep(0.1)
        self.chat_max_tokens_spin = QSpinBox(chat_group)
        self.chat_max_tokens_spin.setRange(*ASSISTANT_OPTION_RANGES["max_tokens"])
        self.chat_max_tokens_spin.setSingleStep(64)
        chat_form.addRow("Temperature", self.chat_temperature_spin)
        chat_form.addRow("Max tokens", self.chat_max_tokens_spin)
        return chat_group

    def _build_completion_defaults_group(self, parent):
        """Return the Completion defaults group: ghost-text generation."""
        completion_group = QGroupBox("Completion defaults", parent)
        completion_form = QFormLayout(completion_group)
        self.completions_enabled_checkbox = QCheckBox(
            "Enable AI ghost-text completions",
            completion_group,
        )
        self.completion_temperature_spin = QDoubleSpinBox(completion_group)
        self.completion_temperature_spin.setDecimals(2)
        self.completion_temperature_spin.setRange(
            *ASSISTANT_OPTION_RANGES["completion_temperature"]
        )
        self.completion_temperature_spin.setSingleStep(0.05)
        self.completion_max_tokens_spin = QSpinBox(completion_group)
        self.completion_max_tokens_spin.setRange(
            *ASSISTANT_OPTION_RANGES["completion_max_tokens"]
        )
        self.completion_max_tokens_spin.setSingleStep(16)
        self.debounce_spin = QSpinBox(completion_group)
        self.debounce_spin.setRange(*ASSISTANT_OPTION_RANGES["debounce_ms"])
        self.debounce_spin.setSingleStep(50)
        completion_form.addRow(self.completions_enabled_checkbox)
        completion_form.addRow("Temperature", self.completion_temperature_spin)
        completion_form.addRow("Max tokens", self.completion_max_tokens_spin)
        completion_form.addRow("Debounce (ms)", self.debounce_spin)
        return completion_group

    def _build_shortcuts_group(self, parent):
        """Return the Keyboard shortcuts group for completion commands."""
        shortcuts_group = QGroupBox("Keyboard shortcuts", parent)
        shortcuts_form = QFormLayout(shortcuts_group)
        self.completion_shortcut_edit = QLineEdit(shortcuts_group)
        self.accept_word_shortcut_edit = QLineEdit(shortcuts_group)
        self.accept_line_shortcut_edit = QLineEdit(shortcuts_group)
        shortcuts_form.addRow("Trigger completion", self.completion_shortcut_edit)
        shortcuts_form.addRow("Accept next word", self.accept_word_shortcut_edit)
        shortcuts_form.addRow("Accept next line", self.accept_line_shortcut_edit)
        # The note sits inside the group so it stays attached to the three
        # shortcut rows it qualifies, wherever the group is composed.
        shortcuts_note = QLabel(
            "Shortcut changes take effect after restarting Spyder."
        )
        shortcuts_note.setWordWrap(True)
        shortcuts_form.addRow(shortcuts_note)
        return shortcuts_group

    def _build_color_theme_group(self, parent):
        """Return the Color theme group: preset plus per-color overrides."""
        # Color theme preset and per-color overrides
        theme_group = QGroupBox("Color theme", parent)
        theme_layout = QVBoxLayout(theme_group)
        theme_form = QFormLayout()
        self.theme_preset_combo = QComboBox(theme_group)
        for name in get_preset_names():
            self.theme_preset_combo.addItem(name.capitalize(), name)
        self.theme_preset_combo.currentIndexChanged.connect(
            self._on_theme_preset_changed
        )
        theme_form.addRow("Theme preset", self.theme_preset_combo)
        theme_layout.addLayout(theme_form)

        # Color swatch grid for the most important colors
        color_label = QLabel("Color overrides (click to customize):")
        theme_layout.addWidget(color_label)
        self._color_swatches = {}
        self._color_overrides = {}
        color_grid = QHBoxLayout()
        # Build swatch buttons in a wrapping flow: label above, swatch below
        for color_key, label in EXPOSED_COLOR_KEYS:
            col_layout = QVBoxLayout()
            col_layout.setSpacing(2)
            swatch_label = QLabel(label)
            swatch_label.setWordWrap(True)
            swatch_label.setFixedWidth(90)
            swatch_label.setStyleSheet("font-size: 8pt;")
            swatch = ColorSwatchButton(color_key, label, parent=theme_group)
            swatch.color_changed.connect(self._on_color_override_changed)
            col_layout.addWidget(swatch_label, alignment=Qt.AlignCenter)
            col_layout.addWidget(swatch, alignment=Qt.AlignCenter)
            color_grid.addLayout(col_layout)
            self._color_swatches[color_key] = swatch
        theme_layout.addLayout(color_grid)

        # Reset overrides button
        reset_row = QHBoxLayout()
        self.reset_colors_btn = QPushButton("Reset all color overrides", theme_group)
        self.reset_colors_btn.clicked.connect(self._reset_all_color_overrides)
        reset_row.addWidget(self.reset_colors_btn)
        reset_row.addStretch()
        theme_layout.addLayout(reset_row)
        return theme_group

    def _build_chat_font_group(self, parent):
        """Return the Chat font group: family, size, and line height."""
        # Chat font settings
        chat_font_group = QGroupBox("Chat font", parent)
        chat_font_form = QFormLayout(chat_font_group)
        self.chat_font_combo = QFontComboBox(chat_font_group)
        self.chat_font_size_spin = QSpinBox(chat_font_group)
        self.chat_font_size_spin.setRange(*ASSISTANT_OPTION_RANGES["chat_font_size"])
        self.chat_font_size_spin.setSuffix(" pt")
        self.chat_line_height_spin = QDoubleSpinBox(chat_font_group)
        self.chat_line_height_spin.setRange(
            *ASSISTANT_OPTION_RANGES["chat_line_height"]
        )
        self.chat_line_height_spin.setSingleStep(0.1)
        self.chat_line_height_spin.setDecimals(1)
        chat_font_form.addRow("Font family", self.chat_font_combo)
        chat_font_form.addRow("Font size", self.chat_font_size_spin)
        chat_font_form.addRow("Line height", self.chat_line_height_spin)
        return chat_font_group

    def _build_code_blocks_group(self, parent):
        """Return the Code blocks group: code font and syntax themes."""
        # Code block font settings
        code_font_group = QGroupBox("Code blocks", parent)
        code_font_form = QFormLayout(code_font_group)
        self.code_font_combo = QFontComboBox(code_font_group)
        self.code_font_size_spin = QSpinBox(code_font_group)
        self.code_font_size_spin.setRange(*ASSISTANT_OPTION_RANGES["code_font_size"])
        self.code_font_size_spin.setSuffix(" pt")
        self.pygments_dark_combo = QComboBox(code_font_group)
        self.pygments_light_combo = QComboBox(code_font_group)
        self._populate_pygments_combos()
        code_font_form.addRow("Code font", self.code_font_combo)
        code_font_form.addRow("Code font size", self.code_font_size_spin)
        code_font_form.addRow("Syntax theme (dark)", self.pygments_dark_combo)
        code_font_form.addRow("Syntax theme (light)", self.pygments_light_combo)
        return code_font_group

    def _build_message_bubbles_group(self, parent):
        """Return the Message bubbles group: padding, radius, spacing."""
        # Message bubble geometry
        bubble_group = QGroupBox("Message bubbles", parent)
        bubble_form = QFormLayout(bubble_group)
        self.bubble_padding_spin = QSpinBox(bubble_group)
        self.bubble_padding_spin.setRange(*ASSISTANT_OPTION_RANGES["bubble_padding"])
        self.bubble_padding_spin.setSuffix(" px")
        self.bubble_radius_spin = QSpinBox(bubble_group)
        self.bubble_radius_spin.setRange(
            *ASSISTANT_OPTION_RANGES["bubble_border_radius"]
        )
        self.bubble_radius_spin.setSuffix(" px")
        self.bubble_spacing_spin = QSpinBox(bubble_group)
        self.bubble_spacing_spin.setRange(*ASSISTANT_OPTION_RANGES["bubble_spacing"])
        self.bubble_spacing_spin.setSuffix(" px")
        bubble_form.addRow("Padding", self.bubble_padding_spin)
        bubble_form.addRow("Border radius", self.bubble_radius_spin)
        bubble_form.addRow("Spacing", self.bubble_spacing_spin)
        return bubble_group

    def _build_ghost_text_group(self, parent):
        """Return the Ghost text timing group: the two suggestion delays."""
        ghost_group = QGroupBox("Ghost text timing", parent)
        ghost_form = QFormLayout(ghost_group)
        self.idle_delay_spin = QSpinBox(ghost_group)
        self.idle_delay_spin.setRange(
            *ASSISTANT_OPTION_RANGES["idle_completion_delay_ms"]
        )
        self.idle_delay_spin.setSingleStep(100)
        self.idle_delay_spin.setSuffix(" ms")
        self.post_accept_delay_spin = QSpinBox(ghost_group)
        self.post_accept_delay_spin.setRange(
            *ASSISTANT_OPTION_RANGES["post_accept_completion_delay_ms"]
        )
        self.post_accept_delay_spin.setSingleStep(25)
        self.post_accept_delay_spin.setSuffix(" ms")
        self.manual_only_checkbox = QCheckBox(
            "Only suggest when I ask (Ctrl+Shift+Space)", ghost_group
        )
        self.manual_only_checkbox.setToolTip(
            "Stop suggesting as you type. The completion shortcut still "
            "works, and is then the only way to request a suggestion."
        )
        self.manual_only_checkbox.toggled.connect(
            self._refresh_ghost_timing_enabled
        )
        ghost_form.addRow(self.manual_only_checkbox)
        ghost_form.addRow("Idle completion delay", self.idle_delay_spin)
        ghost_form.addRow("Post-accept delay", self.post_accept_delay_spin)
        # The note sits inside the group so the two delays are explained
        # exactly where they are edited.
        behavior_note = QLabel(
            "Idle delay: how long after you stop typing before ghost text "
            "appears. Post-accept delay: pause after accepting a suggestion "
            "before requesting the next one."
        )
        behavior_note.setWordWrap(True)
        ghost_form.addRow(behavior_note)
        self._refresh_ghost_timing_enabled()
        return ghost_group

    def _refresh_ghost_timing_enabled(self, *_args):
        """Grey out the two delays while suggestions are manual only.

        Neither timer runs in that mode, so leaving them editable would
        invite the user to tune something that has no effect.
        """
        automatic = not self.manual_only_checkbox.isChecked()
        self.idle_delay_spin.setEnabled(automatic)
        self.post_accept_delay_spin.setEnabled(automatic)

    def _build_native_popup_group(self, parent):
        """Return the group choosing who owns Spyder's completion popup."""
        # Ownership between ghost text and Spyder's own automatic popup
        # (pylsp etc.). The description below the combo explains the
        # selected policy so the user does not have to guess.
        popup_group = QGroupBox("Spyder's automatic completion popup", parent)
        popup_layout = QVBoxLayout(popup_group)
        self.native_popup_policy_combo = QComboBox(popup_group)
        for policy in NATIVE_POPUP_POLICIES:
            self.native_popup_policy_combo.addItem(
                NATIVE_POPUP_POLICY_LABELS[policy], policy
            )
        self.native_popup_policy_combo.currentIndexChanged.connect(
            self._refresh_native_popup_policy_description
        )
        popup_layout.addWidget(self.native_popup_policy_combo)
        self.native_popup_policy_description = QLabel(popup_group)
        self.native_popup_policy_description.setWordWrap(True)
        popup_layout.addWidget(self.native_popup_policy_description)
        return popup_group

    def _build_project_access_group(self, parent):
        """Return the Project access group: opt-in read-only file access."""
        access_group = QGroupBox("Project access", parent)
        access_form = QFormLayout(access_group)
        self.project_tools_checkbox = QCheckBox(
            "Let the assistant read project files and git history on request",
            access_group,
        )
        self.project_tools_checkbox.setToolTip(
            "Read-only. Limited to files under the active project (or the "
            "current file's folder), with size caps; also exposed as MCP tools."
        )
        access_form.addRow(self.project_tools_checkbox)
        return access_group

    def _build_mcp_server_group(self, parent):
        """Return the Embedded MCP server group: host, port, and status."""
        mcp_server_group = QGroupBox("Embedded MCP server", parent)
        mcp_server_form = QFormLayout(mcp_server_group)
        self.mcp_enabled_checkbox = QCheckBox(
            "Start the local Spyder MCP server automatically",
            mcp_server_group,
        )
        self.mcp_host_edit = QLineEdit(mcp_server_group)
        self.mcp_host_edit.setPlaceholderText(DEFAULT_MCP_HOST)
        # The listen host is a bare host or IP, never a URL; a pasted
        # "http://..." silently fell back to the default on save.
        self.mcp_host_note_label = QLabel(mcp_server_group)
        self.mcp_host_note_label.setWordWrap(True)
        self.mcp_host_note_label.setVisible(False)
        self.mcp_host_edit.textChanged.connect(self._refresh_mcp_host_note)
        self.mcp_port_spin = QSpinBox(mcp_server_group)
        self.mcp_port_spin.setRange(*ASSISTANT_OPTION_RANGES["mcp_port"])
        self.mcp_port_spin.setValue(DEFAULT_MCP_PORT)
        self.mcp_endpoint_edit = QLineEdit(mcp_server_group)
        self.mcp_endpoint_edit.setReadOnly(True)
        self.mcp_status_label = QLabel(mcp_server_group)
        self.mcp_status_label.setWordWrap(True)
        self.mcp_status_note_label = QLabel(
            "Status reflects the currently saved configuration. Preview "
            "commands below update live as you edit host or port values.",
            mcp_server_group,
        )
        self.mcp_status_note_label.setWordWrap(True)
        # Shown by _refresh_mcp_restart_warning once the edited endpoint
        # differs from the one the running server is serving.
        self.mcp_restart_warning_label = QLabel(mcp_server_group)
        self.mcp_restart_warning_label.setWordWrap(True)
        self.mcp_restart_warning_label.setVisible(False)
        mcp_server_form.addRow(self.mcp_enabled_checkbox)
        mcp_server_form.addRow("Listen host", self.mcp_host_edit)
        mcp_server_form.addRow("", self.mcp_host_note_label)
        mcp_server_form.addRow("Listen port", self.mcp_port_spin)
        mcp_server_form.addRow("Endpoint URL", self.mcp_endpoint_edit)
        mcp_server_form.addRow("Current status", self.mcp_status_label)
        mcp_server_form.addRow("", self.mcp_status_note_label)
        mcp_server_form.addRow("", self.mcp_restart_warning_label)
        return mcp_server_group

    def _build_client_setup_group(self, parent):
        """Return the Client setup group: copy-ready client snippets."""
        clients_group = QGroupBox("Client setup", parent)
        clients_layout = QVBoxLayout(clients_group)
        clients_note = QLabel(
            "Use these copy-ready snippets to connect Claude Code, Codex, "
            "or OpenCode to the embedded Spyder MCP server."
        )
        clients_note.setWordWrap(True)
        clients_layout.addWidget(clients_note)

        claude_row = QHBoxLayout()
        self.claude_command_edit = QLineEdit(clients_group)
        self.claude_command_edit.setReadOnly(True)
        self.copy_claude_btn = QPushButton("Copy Claude", clients_group)
        self.copy_claude_btn.clicked.connect(
            lambda: self._copy_text(self.claude_command_edit.text())
        )
        self.launch_claude_btn = QPushButton("Launch Claude", clients_group)
        self.launch_claude_btn.clicked.connect(
            lambda: self._launch_client(MCP_CLIENT_CLAUDE)
        )
        claude_row.addWidget(self.claude_command_edit, stretch=1)
        claude_row.addWidget(self.copy_claude_btn)
        claude_row.addWidget(self.launch_claude_btn)
        clients_layout.addLayout(claude_row)

        codex_row = QHBoxLayout()
        self.codex_command_edit = QLineEdit(clients_group)
        self.codex_command_edit.setReadOnly(True)
        self.copy_codex_btn = QPushButton("Copy Codex", clients_group)
        self.copy_codex_btn.clicked.connect(
            lambda: self._copy_text(self.codex_command_edit.text())
        )
        self.launch_codex_btn = QPushButton("Launch Codex", clients_group)
        self.launch_codex_btn.clicked.connect(
            lambda: self._launch_client(MCP_CLIENT_CODEX)
        )
        codex_row.addWidget(self.codex_command_edit, stretch=1)
        codex_row.addWidget(self.copy_codex_btn)
        codex_row.addWidget(self.launch_codex_btn)
        clients_layout.addLayout(codex_row)

        self.opencode_config_edit = QPlainTextEdit(clients_group)
        self.opencode_config_edit.setReadOnly(True)
        self.opencode_config_edit.setLineWrapMode(QPlainTextEdit.NoWrap)
        self.opencode_config_edit.setMinimumHeight(180)
        clients_layout.addWidget(self.opencode_config_edit)

        opencode_row = QHBoxLayout()
        self.copy_opencode_btn = QPushButton("Copy OpenCode", clients_group)
        self.copy_opencode_btn.clicked.connect(
            lambda: self._copy_text(self.opencode_config_edit.toPlainText())
        )
        self.launch_opencode_btn = QPushButton("Launch OpenCode", clients_group)
        self.launch_opencode_btn.clicked.connect(
            lambda: self._launch_client(MCP_CLIENT_OPENCODE)
        )
        self.mcp_action_feedback = QLabel(clients_group)
        self.mcp_action_feedback.setWordWrap(True)
        opencode_row.addWidget(self.copy_opencode_btn)
        opencode_row.addWidget(self.launch_opencode_btn)
        opencode_row.addWidget(self.mcp_action_feedback, stretch=1)
        opencode_row.addStretch()
        clients_layout.addLayout(opencode_row)
        return clients_group

    def _build_system_prompt_group(self, parent):
        """Return the System prompt group: the standing chat instruction."""
        system_group = QGroupBox("System prompt", parent)
        system_layout = QVBoxLayout(system_group)
        self.system_prompt_edit = QTextEdit(system_group)
        system_layout.addWidget(self.system_prompt_edit)
        return system_group

    def _build_editor_action_prompts_group(self, parent):
        """Return the Editor action prompts group: one prompt per action."""
        actions_group = QGroupBox("Editor action prompts", parent)
        actions_form = QFormLayout(actions_group)
        self.prompt_explain_edit = QTextEdit(actions_group)
        self.prompt_fix_edit = QTextEdit(actions_group)
        self.prompt_docstring_edit = QTextEdit(actions_group)
        self.prompt_ask_edit = QTextEdit(actions_group)
        actions_form.addRow("Explain", self.prompt_explain_edit)
        actions_form.addRow("Fix", self.prompt_fix_edit)
        actions_form.addRow("Add docstring", self.prompt_docstring_edit)
        actions_form.addRow("Ask AI", self.prompt_ask_edit)
        return actions_group

    def _on_theme_preset_changed(self, index):
        """Update color swatches when the user picks a different preset."""
        del index
        self._refresh_color_swatches()

    def _on_color_override_changed(self, color_key, hex_color):
        """Store a per-color override when the user picks a color."""
        self._color_overrides[color_key] = hex_color

    def _reset_all_color_overrides(self):
        """Clear all color overrides and refresh swatches to preset colors."""
        self._color_overrides = {}
        self._refresh_color_swatches()

    def _refresh_color_swatches(self):
        """Update all color swatch buttons to show effective colors.

        The effective color is the preset color with any active override
        applied on top.
        """
        preset_name = self.theme_preset_combo.currentData() or "default"
        # Resolve colors for both dark and light — show dark variant
        # since that's most common (swatches are just a preview).
        colors = get_theme_colors(preset_name, is_dark=True)
        for key, swatch in self._color_swatches.items():
            if key in self._color_overrides:
                swatch.set_color(self._color_overrides[key], is_override=True)
            else:
                swatch.set_color(colors.get(key, "#000000"), is_override=False)

    def _populate_pygments_combos(self):
        """Fill the Pygments style combos with available styles."""
        try:
            from pygments.styles import get_all_styles
            styles = sorted(get_all_styles())
        except ImportError:
            # Pygments not installed — offer sensible defaults only
            styles = ["default", "monokai", "emacs", "friendly", "native"]
        for combo in (self.pygments_dark_combo, self.pygments_light_combo):
            combo.clear()
            for style in styles:
                combo.addItem(style)

    def _populate_model_combos(self):
        """Fill the chat-model combo from the latest discovered models."""
        populate_model_combo(self.chat_model_combo, self._models, show_provider=True)

    def _copy_text(self, text):
        """Copy text to the system clipboard and update the feedback label."""
        clipboard = QApplication.clipboard()
        clipboard.setText(str(text or ""))
        self._set_mcp_action_feedback("Copied to clipboard.")

    def _set_mcp_action_feedback(self, text):
        """Update the shared MCP client action feedback label."""
        self.mcp_action_feedback.setText(str(text or ""))

    def _current_mcp_host(self):
        """Return the normalized host currently shown in the dialog."""
        return normalize_mcp_host(self.mcp_host_edit.text())

    def _current_mcp_port(self):
        """Return the normalized port currently shown in the dialog."""
        return normalize_mcp_port(self.mcp_port_spin.value())

    def _refresh_mcp_preview(self):
        """Refresh the endpoint URL and copy-ready client setup snippets."""
        snippets = build_client_setup_snippets(
            host=self._current_mcp_host(),
            port=self._current_mcp_port(),
            name=DEFAULT_MCP_SERVER_NAME,
        )
        self.mcp_endpoint_edit.setText(snippets["url"])
        self.claude_command_edit.setText(snippets["claude_command"])
        self.codex_command_edit.setText(snippets["codex_command"])
        self.opencode_config_edit.setPlainText(snippets["opencode_config"])
        self._refresh_mcp_restart_warning(snippets["url"])

    def _refresh_ollama_host_note(self, *_args):
        """Flag an Ollama endpoint that is not a usable http(s) URL.

        Empty falls back to the default on save, which is fine and needs no
        warning; anything else must carry a scheme and a host.
        """
        text = self.ollama_host_edit.text().strip()
        if not text:
            problem = ""
        elif not text.startswith(("http://", "https://")):
            problem = "Include the scheme, for example http://localhost:11434"
        elif not text.split("//", 1)[1].strip(" /"):
            problem = "Add the host, for example http://localhost:11434"
        else:
            problem = ""
        self.ollama_host_note_label.setText(problem)
        self.ollama_host_note_label.setVisible(bool(problem))

    def _refresh_mcp_host_note(self, *_args):
        """Flag a listen host that is a URL or carries a port."""
        text = self.mcp_host_edit.text().strip()
        if not text:
            problem = ""
        elif "//" in text or text.startswith(("http:", "https:")):
            problem = "Use a bare host or IP here, for example 127.0.0.1"
        elif text.count(":") == 1:
            # One colon means "host:port"; several mean an IPv6 address.
            problem = "Set the port in the field below, not in the host"
        else:
            problem = ""
        self.mcp_host_note_label.setText(problem)
        self.mcp_host_note_label.setVisible(bool(problem))

    def _refresh_mcp_restart_warning(self, previewed_url):
        """Warn that saving will move the running server to a new endpoint.

        Saving MCP host or port changes restarts the embedded server, which
        drops any connected client. The warning appears only once the edited
        endpoint actually differs from the one the server is serving.
        """
        running_url = str((self._mcp_status or {}).get("endpoint_url", "") or "")
        moves_server = bool(running_url) and previewed_url != running_url
        self.mcp_restart_warning_label.setText(
            "Saving restarts the embedded server on this endpoint. "
            "Connected clients have to reconnect."
            if moves_server
            else ""
        )
        self.mcp_restart_warning_label.setVisible(moves_server)

    def _refresh_mcp_status_label(self):
        """Update the MCP status label for the currently saved server."""
        status = dict(self._mcp_status or {})
        enabled = bool(status.get("enabled", True))
        running = bool(status.get("running", False))
        error = str(status.get("error", "") or "").strip()
        endpoint_url = str(
            status.get(
                "endpoint_url",
                build_mcp_endpoint_url(
                    host=self._settings.get("mcp_host", DEFAULT_MCP_HOST),
                    port=self._settings.get("mcp_port", DEFAULT_MCP_PORT),
                ),
            ) or ""
        )

        if not enabled:
            text = "Disabled. The embedded Spyder MCP server will not start."
        elif running:
            text = f"Running on {endpoint_url}"
        elif error:
            text = f"Not running. {error}"
        else:
            text = f"Not running. Expected endpoint: {endpoint_url}"

        self.mcp_status_label.setText(text)

    def _can_launch_client(self):
        """Return whether the launch buttons match the running saved server."""
        status = dict(self._mcp_status or {})
        preview_url = self.mcp_endpoint_edit.text().strip()
        saved_url = str(status.get("endpoint_url", "") or "").strip()

        if not self.mcp_enabled_checkbox.isChecked():
            return (
                False,
                "Enable the embedded MCP server and save settings before "
                "launching a client.",
            )

        if bool(status.get("enabled", True)) != bool(self.mcp_enabled_checkbox.isChecked()):
            return (
                False,
                "Save MCP settings first so Spyder restarts the embedded "
                "server with this configuration.",
            )

        if preview_url != saved_url:
            return (
                False,
                "Save MCP settings first so the running embedded server "
                "matches the preview URL.",
            )

        if not bool(status.get("running", False)):
            return (
                False,
                "The embedded MCP server is not running. Save settings or "
                "restart Spyder first.",
            )

        return True, ""

    def _launch_client(self, client_id):
        """Launch one supported MCP-aware client through the plugin callback."""
        allowed, message = self._can_launch_client()
        if not allowed:
            self._set_mcp_action_feedback(message)
            return False

        launcher = self._mcp_client_launcher
        if not callable(launcher):
            self._set_mcp_action_feedback(
                "MCP client launch support is not available in this build."
            )
            return False

        client_label = get_mcp_client_label(client_id)
        try:
            result = launcher(client_id, self.mcp_endpoint_edit.text().strip())
        except Exception as error:
            self._set_mcp_action_feedback(
                f"Could not launch {client_label}: {error}"
            )
            return False

        self._set_mcp_action_feedback(
            str(result or f"Launching {client_label}.")
        )
        return True

    def replace_models(self, models):
        """Replace discovered models and rebuild both dropdowns."""
        current_chat = self.chat_model_combo.currentData() or {}
        current_completion = self.completion_model_combo.currentData() or {}
        self._models = [dict(model) for model in (models or []) if isinstance(model, dict)]
        self._populate_model_combos()
        self._select_chat_model(
            preferred_name=str(current_chat.get("name", "") or ""),
            preferred_provider_kind=str(
                current_chat.get("provider_kind", current_chat.get("provider_id", "")) or ""
            ),
            preferred_profile_id=str(current_chat.get("profile_id", "") or ""),
        )
        self._refresh_completion_model_options(
            preferred_name=str(current_completion.get("name", "") or ""),
        )

    def _load_settings(self):
        """Load the current config-backed settings into the dialog widgets.

        ``self._settings`` came from ``AssistantSettings.from_mapping`` in
        ``__init__``, so every value is already the right type, inside its
        bounds, and present. This method therefore only moves values into
        widgets: no defaults, no coercion, no clamping. The one exception is
        the chat temperature, which is stored as the legacy "x10" integer and
        decoded by its owning helper.
        """
        settings = self._settings

        self.ollama_host_edit.setText(settings["ollama_host"])
        self.mcp_enabled_checkbox.setChecked(settings["mcp_enabled"])
        self.mcp_host_edit.setText(settings["mcp_host"])
        self.mcp_port_spin.setValue(settings["mcp_port"])
        self.chat_temperature_spin.setValue(
            normalize_chat_temperature(settings["chat_temperature"])
        )
        self.chat_max_tokens_spin.setValue(settings["max_tokens"])
        self.completions_enabled_checkbox.setChecked(settings["completions_enabled"])
        self.manual_only_checkbox.setChecked(settings["completion_manual_only"])
        self._refresh_ghost_timing_enabled()
        self.project_tools_checkbox.setChecked(settings["project_tools_enabled"])
        self.completion_temperature_spin.setValue(settings["completion_temperature"])
        self.completion_max_tokens_spin.setValue(settings["completion_max_tokens"])
        self.debounce_spin.setValue(settings["debounce_ms"])
        self.completion_shortcut_edit.setText(settings["completion_shortcut"])
        self.accept_word_shortcut_edit.setText(
            settings["completion_accept_word_shortcut"]
        )
        self.accept_line_shortcut_edit.setText(
            settings["completion_accept_line_shortcut"]
        )
        self.system_prompt_edit.setPlainText(settings["chat_system_prompt"])
        self.prompt_explain_edit.setPlainText(settings["prompt_explain"])
        self.prompt_fix_edit.setPlainText(settings["prompt_fix"])
        self.prompt_docstring_edit.setPlainText(settings["prompt_docstring"])
        self.prompt_ask_edit.setPlainText(settings["prompt_ask"])

        # Theme preset and color overrides
        preset_index = self.theme_preset_combo.findData(settings["theme_preset"])
        if preset_index >= 0:
            self.theme_preset_combo.setCurrentIndex(preset_index)
        self._color_overrides = parse_color_overrides(
            settings["theme_color_overrides"]
        )
        self._refresh_color_swatches()

        # Appearance settings
        self.chat_font_combo.setCurrentFont(QFont(settings["chat_font_family"]))
        self.chat_font_size_spin.setValue(settings["chat_font_size"])
        self.chat_line_height_spin.setValue(settings["chat_line_height"])
        self.code_font_combo.setCurrentFont(QFont(settings["code_font_family"]))
        self.code_font_size_spin.setValue(settings["code_font_size"])
        # Select the configured Pygments style in each combo
        dark_index = self.pygments_dark_combo.findText(settings["pygments_style_dark"])
        if dark_index >= 0:
            self.pygments_dark_combo.setCurrentIndex(dark_index)
        light_index = self.pygments_light_combo.findText(
            settings["pygments_style_light"]
        )
        if light_index >= 0:
            self.pygments_light_combo.setCurrentIndex(light_index)
        self.bubble_padding_spin.setValue(settings["bubble_padding"])
        self.bubble_radius_spin.setValue(settings["bubble_border_radius"])
        self.bubble_spacing_spin.setValue(settings["bubble_spacing"])

        # Behavior settings
        self.idle_delay_spin.setValue(settings["idle_completion_delay_ms"])
        self.post_accept_delay_spin.setValue(
            settings["post_accept_completion_delay_ms"]
        )
        self._select_native_popup_policy(settings["native_popup_policy"])

        # Stored values are already valid, so these start clear; they react to
        # what the user types next.
        self._refresh_ollama_host_note()
        self._refresh_mcp_host_note()

        self._select_chat_model()
        self._refresh_completion_model_options()
        self._refresh_mcp_preview()
        self._refresh_mcp_status_label()

    def _select_native_popup_policy(self, policy):
        """Select ``policy`` in the popup combo (default when unknown)."""
        index = self.native_popup_policy_combo.findData(policy)
        if index < 0:
            index = self.native_popup_policy_combo.findData(DEFAULT_NATIVE_POPUP_POLICY)
        self.native_popup_policy_combo.setCurrentIndex(max(index, 0))
        self._refresh_native_popup_policy_description()

    def _refresh_native_popup_policy_description(self, *_args):
        """Explain the selected popup policy under the combo."""
        policy = self.native_popup_policy_combo.currentData()
        self.native_popup_policy_description.setText(
            NATIVE_POPUP_POLICY_DESCRIPTIONS.get(policy, "")
        )

    def selected_native_popup_policy(self):
        """Return the popup policy chosen in the Completions tab."""
        return self.native_popup_policy_combo.currentData() or DEFAULT_NATIVE_POPUP_POLICY

    def _select_chat_model(
        self,
        preferred_name=None,
        preferred_provider_kind=None,
        preferred_profile_id=None,
    ):
        """Select the configured chat model, or the first available entry."""
        select_model(
            self.chat_model_combo,
            name=str(
                preferred_name
                if preferred_name is not None
                else self._settings.get("chat_model", "") or ""
            ),
            provider_kind=str(
                preferred_provider_kind
                if preferred_provider_kind is not None
                else self._settings.get("chat_provider", "ollama") or "ollama"
            ),
            profile_id=str(
                preferred_profile_id
                if preferred_profile_id is not None
                else self._settings.get("chat_provider_profile_id", "") or ""
            ),
        )

    def _refresh_completion_model_options(self, preferred_name=None):
        """Rebuild the completion-model list for the selected provider."""
        chat_payload = self.chat_model_combo.currentData()
        allowed_key = provider_key(chat_payload or {})

        self._completion_model_payloads = [
            dict(payload)
            for payload in self._models
            if provider_key(payload) == allowed_key
        ]
        if not self._completion_model_payloads:
            self._completion_model_payloads = [dict(payload) for payload in self._models]

        selected_name = str(
            preferred_name
            if preferred_name is not None
            else self._settings.get("completion_model", "") or ""
        )
        populate_model_combo(
            self.completion_model_combo,
            self._completion_model_payloads,
            show_provider=True,
        )
        self.completion_model_combo.blockSignals(True)
        try:
            select_model(self.completion_model_combo, name=selected_name)
        finally:
            self.completion_model_combo.blockSignals(False)

    def selected_settings(self):
        """Return the normalized settings chosen in the dialog."""
        chat_payload = self.chat_model_combo.currentData() or {}
        completion_payload = self.completion_model_combo.currentData() or {}
        selected = dict(self._settings)
        selected.update({
            "ollama_host": self.ollama_host_edit.text().strip() or DEFAULT_OLLAMA_HOST,
            "mcp_enabled": bool(self.mcp_enabled_checkbox.isChecked()),
            "mcp_host": self._current_mcp_host(),
            "mcp_port": self._current_mcp_port(),
            "chat_provider": chat_payload.get(
                "provider_kind",
                self._settings.get("chat_provider", "ollama"),
            ),
            "chat_provider_profile_id": str(
                chat_payload.get("profile_id", "") or ""
            ),
            "chat_model": chat_payload.get(
                "name",
                self._settings.get("chat_model", ""),
            ),
            "completion_model": completion_payload.get(
                "name",
                self._settings.get("completion_model", ""),
            ),
            "chat_temperature": int(round(self.chat_temperature_spin.value() * 10)),
            "max_tokens": int(self.chat_max_tokens_spin.value()),
            "completions_enabled": bool(self.completions_enabled_checkbox.isChecked()),
            "completion_manual_only": bool(self.manual_only_checkbox.isChecked()),
            "project_tools_enabled": bool(self.project_tools_checkbox.isChecked()),
            "completion_temperature": float(self.completion_temperature_spin.value()),
            "completion_max_tokens": int(self.completion_max_tokens_spin.value()),
            "debounce_ms": int(self.debounce_spin.value()),
            "completion_shortcut": self.completion_shortcut_edit.text().strip(),
            "completion_accept_word_shortcut": self.accept_word_shortcut_edit.text().strip(),
            "completion_accept_line_shortcut": self.accept_line_shortcut_edit.text().strip(),
            "chat_system_prompt": self.system_prompt_edit.toPlainText(),
            "prompt_explain": self.prompt_explain_edit.toPlainText(),
            "prompt_fix": self.prompt_fix_edit.toPlainText(),
            "prompt_docstring": self.prompt_docstring_edit.toPlainText(),
            "prompt_ask": self.prompt_ask_edit.toPlainText(),
            # Theme
            "theme_preset": self.theme_preset_combo.currentData() or "default",
            "theme_color_overrides": serialize_color_overrides(
                self._color_overrides
            ),
            # Appearance
            "chat_font_family": self.chat_font_combo.currentFont().family(),
            "chat_font_size": int(self.chat_font_size_spin.value()),
            "chat_line_height": float(self.chat_line_height_spin.value()),
            "code_font_family": self.code_font_combo.currentFont().family(),
            "code_font_size": int(self.code_font_size_spin.value()),
            "pygments_style_dark": self.pygments_dark_combo.currentText(),
            "pygments_style_light": self.pygments_light_combo.currentText(),
            "bubble_padding": int(self.bubble_padding_spin.value()),
            "bubble_border_radius": int(self.bubble_radius_spin.value()),
            "bubble_spacing": int(self.bubble_spacing_spin.value()),
            # Behavior
            "idle_completion_delay_ms": int(self.idle_delay_spin.value()),
            "post_accept_completion_delay_ms": int(self.post_accept_delay_spin.value()),
            "native_popup_policy": self.selected_native_popup_policy(),
        })
        return AssistantSettings.from_mapping(selected).to_conf_dict()
