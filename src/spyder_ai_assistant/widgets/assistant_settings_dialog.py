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
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPlainTextEdit,
    QPushButton,
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
        self.resize(760, 720)

        self._models = [dict(model) for model in (models or []) if isinstance(model, dict)]
        self._settings = AssistantSettings.from_mapping(settings).to_conf_dict()
        self._mcp_status = dict(mcp_status or {})
        self._mcp_client_launcher = mcp_client_launcher
        self._completion_model_payloads = []

        layout = QVBoxLayout(self)

        intro = QLabel(
            "Configure models, generation, shortcuts, appearance, behavior, "
            "prompt templates, and the embedded MCP server here. "
            "Provider endpoints are managed through Provider Profiles."
        )
        intro.setWordWrap(True)
        layout.addWidget(intro)

        tabs = QTabWidget(self)
        layout.addWidget(tabs)

        models_tab = QWidget(self)
        models_layout = QVBoxLayout(models_tab)

        models_group = QGroupBox("Models", models_tab)
        models_form = QFormLayout(models_group)
        self.chat_model_combo = QComboBox(models_group)
        self.chat_model_combo.currentIndexChanged.connect(
            self._refresh_completion_model_options
        )
        self.completion_model_combo = QComboBox(models_group)
        models_form.addRow("Default chat model", self.chat_model_combo)
        models_form.addRow("Default completion model", self.completion_model_combo)
        models_layout.addWidget(models_group)

        local_group = QGroupBox("Local endpoint", models_tab)
        local_form = QFormLayout(local_group)
        self.ollama_host_edit = QLineEdit(local_group)
        self.ollama_host_edit.setPlaceholderText(DEFAULT_OLLAMA_HOST)
        local_form.addRow("Ollama host", self.ollama_host_edit)
        models_layout.addWidget(local_group)

        provider_group = QGroupBox("Providers", models_tab)
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
        models_layout.addWidget(provider_group)
        models_layout.addStretch(1)
        tabs.addTab(models_tab, "Models")

        generation_tab = QWidget(self)
        generation_layout = QVBoxLayout(generation_tab)

        chat_group = QGroupBox("Chat defaults", generation_tab)
        chat_form = QFormLayout(chat_group)
        self.chat_temperature_spin = QDoubleSpinBox(chat_group)
        self.chat_temperature_spin.setDecimals(1)
        self.chat_temperature_spin.setRange(0.0, 2.0)
        self.chat_temperature_spin.setSingleStep(0.1)
        self.chat_max_tokens_spin = QSpinBox(chat_group)
        self.chat_max_tokens_spin.setRange(64, 8192)
        self.chat_max_tokens_spin.setSingleStep(64)
        chat_form.addRow("Temperature", self.chat_temperature_spin)
        chat_form.addRow("Max tokens", self.chat_max_tokens_spin)
        generation_layout.addWidget(chat_group)

        completion_group = QGroupBox("Completion defaults", generation_tab)
        completion_form = QFormLayout(completion_group)
        self.completions_enabled_checkbox = QCheckBox(
            "Enable AI ghost-text completions",
            completion_group,
        )
        self.completion_temperature_spin = QDoubleSpinBox(completion_group)
        self.completion_temperature_spin.setDecimals(2)
        self.completion_temperature_spin.setRange(0.0, 2.0)
        self.completion_temperature_spin.setSingleStep(0.05)
        self.completion_max_tokens_spin = QSpinBox(completion_group)
        self.completion_max_tokens_spin.setRange(16, 4096)
        self.completion_max_tokens_spin.setSingleStep(16)
        self.debounce_spin = QSpinBox(completion_group)
        self.debounce_spin.setRange(0, 5000)
        self.debounce_spin.setSingleStep(50)
        completion_form.addRow(self.completions_enabled_checkbox)
        completion_form.addRow("Temperature", self.completion_temperature_spin)
        completion_form.addRow("Max tokens", self.completion_max_tokens_spin)
        completion_form.addRow("Debounce (ms)", self.debounce_spin)
        generation_layout.addWidget(completion_group)
        generation_layout.addStretch(1)
        tabs.addTab(generation_tab, "Generation")

        shortcuts_tab = QWidget(self)
        shortcuts_layout = QVBoxLayout(shortcuts_tab)
        shortcuts_group = QGroupBox("Keyboard shortcuts", shortcuts_tab)
        shortcuts_form = QFormLayout(shortcuts_group)
        self.completion_shortcut_edit = QLineEdit(shortcuts_group)
        self.accept_word_shortcut_edit = QLineEdit(shortcuts_group)
        self.accept_line_shortcut_edit = QLineEdit(shortcuts_group)
        shortcuts_form.addRow("Trigger completion", self.completion_shortcut_edit)
        shortcuts_form.addRow("Accept next word", self.accept_word_shortcut_edit)
        shortcuts_form.addRow("Accept next line", self.accept_line_shortcut_edit)
        shortcuts_layout.addWidget(shortcuts_group)
        shortcuts_note = QLabel(
            "Shortcut changes take effect after restarting Spyder."
        )
        shortcuts_note.setWordWrap(True)
        shortcuts_layout.addWidget(shortcuts_note)
        shortcuts_layout.addStretch(1)
        tabs.addTab(shortcuts_tab, "Shortcuts")

        # --- Appearance tab ---
        appearance_tab = QWidget(self)
        appearance_layout = QVBoxLayout(appearance_tab)

        # Color theme preset and per-color overrides
        theme_group = QGroupBox("Color theme", appearance_tab)
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
        appearance_layout.addWidget(theme_group)

        # Chat font settings
        chat_font_group = QGroupBox("Chat font", appearance_tab)
        chat_font_form = QFormLayout(chat_font_group)
        self.chat_font_combo = QFontComboBox(chat_font_group)
        self.chat_font_size_spin = QSpinBox(chat_font_group)
        self.chat_font_size_spin.setRange(6, 24)
        self.chat_font_size_spin.setSuffix(" pt")
        self.chat_line_height_spin = QDoubleSpinBox(chat_font_group)
        self.chat_line_height_spin.setRange(1.0, 3.0)
        self.chat_line_height_spin.setSingleStep(0.1)
        self.chat_line_height_spin.setDecimals(1)
        chat_font_form.addRow("Font family", self.chat_font_combo)
        chat_font_form.addRow("Font size", self.chat_font_size_spin)
        chat_font_form.addRow("Line height", self.chat_line_height_spin)
        appearance_layout.addWidget(chat_font_group)

        # Code block font settings
        code_font_group = QGroupBox("Code blocks", appearance_tab)
        code_font_form = QFormLayout(code_font_group)
        self.code_font_combo = QFontComboBox(code_font_group)
        self.code_font_size_spin = QSpinBox(code_font_group)
        self.code_font_size_spin.setRange(6, 24)
        self.code_font_size_spin.setSuffix(" pt")
        self.pygments_dark_combo = QComboBox(code_font_group)
        self.pygments_light_combo = QComboBox(code_font_group)
        self._populate_pygments_combos()
        code_font_form.addRow("Code font", self.code_font_combo)
        code_font_form.addRow("Code font size", self.code_font_size_spin)
        code_font_form.addRow("Syntax theme (dark)", self.pygments_dark_combo)
        code_font_form.addRow("Syntax theme (light)", self.pygments_light_combo)
        appearance_layout.addWidget(code_font_group)

        # Message bubble geometry
        bubble_group = QGroupBox("Message bubbles", appearance_tab)
        bubble_form = QFormLayout(bubble_group)
        self.bubble_padding_spin = QSpinBox(bubble_group)
        self.bubble_padding_spin.setRange(4, 32)
        self.bubble_padding_spin.setSuffix(" px")
        self.bubble_radius_spin = QSpinBox(bubble_group)
        self.bubble_radius_spin.setRange(0, 24)
        self.bubble_radius_spin.setSuffix(" px")
        self.bubble_spacing_spin = QSpinBox(bubble_group)
        self.bubble_spacing_spin.setRange(0, 16)
        self.bubble_spacing_spin.setSuffix(" px")
        bubble_form.addRow("Padding", self.bubble_padding_spin)
        bubble_form.addRow("Border radius", self.bubble_radius_spin)
        bubble_form.addRow("Spacing", self.bubble_spacing_spin)
        appearance_layout.addWidget(bubble_group)
        appearance_layout.addStretch(1)
        tabs.addTab(appearance_tab, "Appearance")

        # --- Behavior tab ---
        behavior_tab = QWidget(self)
        behavior_layout = QVBoxLayout(behavior_tab)

        ghost_group = QGroupBox("Ghost text timing", behavior_tab)
        ghost_form = QFormLayout(ghost_group)
        self.idle_delay_spin = QSpinBox(ghost_group)
        self.idle_delay_spin.setRange(100, 5000)
        self.idle_delay_spin.setSingleStep(100)
        self.idle_delay_spin.setSuffix(" ms")
        self.post_accept_delay_spin = QSpinBox(ghost_group)
        self.post_accept_delay_spin.setRange(0, 1000)
        self.post_accept_delay_spin.setSingleStep(25)
        self.post_accept_delay_spin.setSuffix(" ms")
        ghost_form.addRow("Idle completion delay", self.idle_delay_spin)
        ghost_form.addRow("Post-accept delay", self.post_accept_delay_spin)
        behavior_layout.addWidget(ghost_group)

        # Ownership between ghost text and Spyder's own automatic popup
        # (pylsp etc.). The description below the combo explains the
        # selected policy so the user does not have to guess.
        popup_group = QGroupBox("Spyder's automatic completion popup", behavior_tab)
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
        behavior_layout.addWidget(popup_group)

        access_group = QGroupBox("Project access", behavior_tab)
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
        behavior_layout.addWidget(access_group)
        behavior_note = QLabel(
            "Idle delay: how long after you stop typing before ghost text "
            "appears. Post-accept delay: pause after accepting a suggestion "
            "before requesting the next one."
        )
        behavior_note.setWordWrap(True)
        behavior_layout.addWidget(behavior_note)
        behavior_layout.addStretch(1)
        tabs.addTab(behavior_tab, "Behavior")

        mcp_tab = QWidget(self)
        mcp_layout = QVBoxLayout(mcp_tab)

        mcp_server_group = QGroupBox("Embedded MCP server", mcp_tab)
        mcp_server_form = QFormLayout(mcp_server_group)
        self.mcp_enabled_checkbox = QCheckBox(
            "Start the local Spyder MCP server automatically",
            mcp_server_group,
        )
        self.mcp_host_edit = QLineEdit(mcp_server_group)
        self.mcp_host_edit.setPlaceholderText(DEFAULT_MCP_HOST)
        self.mcp_port_spin = QSpinBox(mcp_server_group)
        self.mcp_port_spin.setRange(1, 65535)
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
        mcp_server_form.addRow(self.mcp_enabled_checkbox)
        mcp_server_form.addRow("Listen host", self.mcp_host_edit)
        mcp_server_form.addRow("Listen port", self.mcp_port_spin)
        mcp_server_form.addRow("Endpoint URL", self.mcp_endpoint_edit)
        mcp_server_form.addRow("Current status", self.mcp_status_label)
        mcp_server_form.addRow("", self.mcp_status_note_label)
        mcp_layout.addWidget(mcp_server_group)

        clients_group = QGroupBox("Client setup", mcp_tab)
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
        mcp_layout.addWidget(clients_group)
        mcp_layout.addStretch(1)
        tabs.addTab(mcp_tab, "MCP")

        prompts_tab = QWidget(self)
        prompts_layout = QVBoxLayout(prompts_tab)

        system_group = QGroupBox("System prompt", prompts_tab)
        system_layout = QVBoxLayout(system_group)
        self.system_prompt_edit = QTextEdit(system_group)
        system_layout.addWidget(self.system_prompt_edit)
        prompts_layout.addWidget(system_group)

        actions_group = QGroupBox("Editor action prompts", prompts_tab)
        actions_form = QFormLayout(actions_group)
        self.prompt_explain_edit = QTextEdit(actions_group)
        self.prompt_fix_edit = QTextEdit(actions_group)
        self.prompt_docstring_edit = QTextEdit(actions_group)
        self.prompt_ask_edit = QTextEdit(actions_group)
        actions_form.addRow("Explain", self.prompt_explain_edit)
        actions_form.addRow("Fix", self.prompt_fix_edit)
        actions_form.addRow("Add docstring", self.prompt_docstring_edit)
        actions_form.addRow("Ask AI", self.prompt_ask_edit)
        prompts_layout.addWidget(actions_group)
        tabs.addTab(prompts_tab, "Prompts")

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
        """Load the current config-backed settings into the dialog widgets."""
        # One owner for the legacy x10 encoding and the valid range.
        chat_temperature = normalize_chat_temperature(
            self._settings.get("chat_temperature", 0.5)
        )

        self.ollama_host_edit.setText(
            str(self._settings.get("ollama_host", DEFAULT_OLLAMA_HOST) or "")
        )
        self.mcp_enabled_checkbox.setChecked(
            bool(self._settings.get("mcp_enabled", True))
        )
        self.mcp_host_edit.setText(
            str(self._settings.get("mcp_host", DEFAULT_MCP_HOST) or DEFAULT_MCP_HOST)
        )
        self.mcp_port_spin.setValue(
            normalize_mcp_port(self._settings.get("mcp_port", DEFAULT_MCP_PORT))
        )
        self.chat_temperature_spin.setValue(chat_temperature)
        self.chat_max_tokens_spin.setValue(
            int(self._settings.get("max_tokens", 1024) or 1024)
        )
        self.completions_enabled_checkbox.setChecked(
            bool(self._settings.get("completions_enabled", True))
        )
        self.project_tools_checkbox.setChecked(
            bool(self._settings.get("project_tools_enabled", True))
        )
        self.completion_temperature_spin.setValue(
            float(self._settings.get("completion_temperature", 0.15) or 0.15)
        )
        self.completion_max_tokens_spin.setValue(
            int(self._settings.get("completion_max_tokens", 256) or 256)
        )
        self.debounce_spin.setValue(
            int(self._settings.get("debounce_ms", 300) or 300)
        )
        self.completion_shortcut_edit.setText(
            str(self._settings.get("completion_shortcut", "Ctrl+Shift+Space") or "")
        )
        self.accept_word_shortcut_edit.setText(
            str(self._settings.get("completion_accept_word_shortcut", "Alt+Right") or "")
        )
        self.accept_line_shortcut_edit.setText(
            str(self._settings.get("completion_accept_line_shortcut", "Alt+Shift+Right") or "")
        )
        self.system_prompt_edit.setPlainText(
            str(self._settings.get("chat_system_prompt", "") or "")
        )
        self.prompt_explain_edit.setPlainText(
            str(self._settings.get("prompt_explain", "") or "")
        )
        self.prompt_fix_edit.setPlainText(
            str(self._settings.get("prompt_fix", "") or "")
        )
        self.prompt_docstring_edit.setPlainText(
            str(self._settings.get("prompt_docstring", "") or "")
        )
        self.prompt_ask_edit.setPlainText(
            str(self._settings.get("prompt_ask", "") or "")
        )

        # Theme preset and color overrides
        preset = str(self._settings.get("theme_preset", "default") or "default")
        idx = self.theme_preset_combo.findData(preset)
        if idx >= 0:
            self.theme_preset_combo.setCurrentIndex(idx)
        overrides_raw = self._settings.get("theme_color_overrides", "{}")
        self._color_overrides = parse_color_overrides(
            overrides_raw if isinstance(overrides_raw, str) else "{}"
        )
        self._refresh_color_swatches()

        # Appearance settings
        chat_font = str(self._settings.get("chat_font_family", "sans-serif") or "sans-serif")
        self.chat_font_combo.setCurrentFont(
            QFont(chat_font)
        )
        self.chat_font_size_spin.setValue(
            int(self._settings.get("chat_font_size", 10) or 10)
        )
        self.chat_line_height_spin.setValue(
            float(self._settings.get("chat_line_height", 1.5) or 1.5)
        )
        code_font = str(self._settings.get("code_font_family", "Courier New") or "Courier New")
        self.code_font_combo.setCurrentFont(
            QFont(code_font)
        )
        self.code_font_size_spin.setValue(
            int(self._settings.get("code_font_size", 9) or 9)
        )
        # Select the configured Pygments style in each combo
        dark_style = str(self._settings.get("pygments_style_dark", "monokai") or "monokai")
        light_style = str(self._settings.get("pygments_style_light", "default") or "default")
        idx = self.pygments_dark_combo.findText(dark_style)
        if idx >= 0:
            self.pygments_dark_combo.setCurrentIndex(idx)
        idx = self.pygments_light_combo.findText(light_style)
        if idx >= 0:
            self.pygments_light_combo.setCurrentIndex(idx)
        self.bubble_padding_spin.setValue(
            int(self._settings.get("bubble_padding", 12) or 12)
        )
        self.bubble_radius_spin.setValue(
            int(self._settings.get("bubble_border_radius", 8) or 8)
        )
        self.bubble_spacing_spin.setValue(
            int(self._settings.get("bubble_spacing", 4) or 4)
        )

        # Behavior settings
        self.idle_delay_spin.setValue(
            int(self._settings.get("idle_completion_delay_ms", 1000) or 1000)
        )
        self.post_accept_delay_spin.setValue(
            int(self._settings.get("post_accept_completion_delay_ms", 75) or 75)
        )
        self._select_native_popup_policy(
            self._settings.get("native_popup_policy", DEFAULT_NATIVE_POPUP_POLICY)
        )

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
        """Return the popup policy chosen in the Behavior tab."""
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
