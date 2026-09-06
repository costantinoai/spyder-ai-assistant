"""Per-tab chat inference settings dialog."""

from __future__ import annotations

from qtpy.QtCore import Qt
from qtpy.QtWidgets import (
    QComboBox,
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QFormLayout,
    QGroupBox,
    QLabel,
    QPushButton,
    QSpinBox,
    QVBoxLayout,
)

from spyder_ai_assistant.utils.prompt_library import (
    get_chat_prompt_preset,
    list_chat_prompt_presets,
    normalize_chat_prompt_preset,
)
from spyder_ai_assistant.utils.chat_inference import (
    format_chat_temperature,
    make_chat_inference_record,
)


class ChatSettingsDialog(QDialog):
    """Edit inference overrides for one chat tab."""

    def __init__(self, session_title, defaults, overrides=None, parent=None,
                 prompt_preset_id=None):
        super().__init__(parent)
        self.setWindowTitle("Chat Settings")
        self.resize(440, 320)
        self._prompt_preset_id = normalize_chat_prompt_preset(prompt_preset_id)

        self._defaults = dict(defaults or {})
        self._overrides = dict(overrides or {})
        temperature_value = self._overrides.get("temperature_override")
        if temperature_value is None:
            temperature_value = self._defaults.get("temperature", 0.5)
        max_tokens_value = self._overrides.get("max_tokens_override")
        if max_tokens_value is None:
            max_tokens_value = self._defaults.get("num_predict", 1024)

        layout = QVBoxLayout(self)

        intro = QLabel(
            "These settings apply only to the active chat tab. "
            "Leave an option unchecked to keep using the global preference."
        )
        intro.setWordWrap(True)
        layout.addWidget(intro)

        if session_title:
            session_label = QLabel(f"Tab: {session_title}")
            layout.addWidget(session_label)

        # Chat mode: a preset instruction block prepended to the system
        # prompt for this tab (coding, debugging, review, ...). Lives here
        # rather than in the toolbar: changed rarely, per tab.
        mode_group = QGroupBox("Chat mode", self)
        mode_form = QFormLayout(mode_group)
        self.mode_combo = QComboBox(mode_group)
        for preset in list_chat_prompt_presets():
            self.mode_combo.addItem(preset["label"], preset["id"])
            self.mode_combo.setItemData(
                self.mode_combo.count() - 1, preset["description"], Qt.ToolTipRole
            )
        self.mode_combo.setCurrentIndex(max(0, self.mode_combo.findData(self._prompt_preset_id)))
        self.mode_description = QLabel(mode_group)
        self.mode_description.setWordWrap(True)
        self.mode_combo.currentIndexChanged.connect(self._refresh_mode_description)
        self._refresh_mode_description()
        mode_form.addRow("Mode", self.mode_combo)
        mode_form.addRow("", self.mode_description)
        layout.addWidget(mode_group)

        group = QGroupBox("Inference overrides", self)
        form = QFormLayout(group)

        self.temperature_checkbox = QCheckBox("Override temperature", group)
        self.temperature_spin = QDoubleSpinBox(group)
        self.temperature_spin.setDecimals(1)
        self.temperature_spin.setRange(0.0, 2.0)
        self.temperature_spin.setSingleStep(0.1)
        self.temperature_spin.setValue(float(temperature_value))
        self.temperature_checkbox.setChecked(
            self._overrides.get("temperature_override") is not None
        )
        self.temperature_spin.setEnabled(self.temperature_checkbox.isChecked())
        self.temperature_checkbox.toggled.connect(self.temperature_spin.setEnabled)
        self.temperature_default_label = QLabel(
            f"Global default: {format_chat_temperature(self._defaults.get('temperature', 0.5))}"
        )

        self.max_tokens_checkbox = QCheckBox("Override max tokens", group)
        self.max_tokens_spin = QSpinBox(group)
        self.max_tokens_spin.setRange(64, 8192)
        self.max_tokens_spin.setSingleStep(64)
        self.max_tokens_spin.setValue(int(max_tokens_value))
        self.max_tokens_checkbox.setChecked(
            self._overrides.get("max_tokens_override") is not None
        )
        self.max_tokens_spin.setEnabled(self.max_tokens_checkbox.isChecked())
        self.max_tokens_checkbox.toggled.connect(self.max_tokens_spin.setEnabled)
        self.max_tokens_default_label = QLabel(
            f"Global default: {int(self._defaults.get('num_predict', 1024))}"
        )

        form.addRow(self.temperature_checkbox, self.temperature_spin)
        form.addRow("", self.temperature_default_label)
        form.addRow(self.max_tokens_checkbox, self.max_tokens_spin)
        form.addRow("", self.max_tokens_default_label)
        layout.addWidget(group)

        self.button_box = QDialogButtonBox(
            QDialogButtonBox.Cancel | QDialogButtonBox.Save,
            parent=self,
        )
        self.reset_btn = QPushButton("Use Global Defaults", self)
        self.button_box.addButton(self.reset_btn, QDialogButtonBox.ResetRole)
        self.button_box.accepted.connect(self.accept)
        self.button_box.rejected.connect(self.reject)
        self.reset_btn.clicked.connect(self.reset_to_defaults)
        layout.addWidget(self.button_box)

    def _refresh_mode_description(self):
        """Explain what the selected chat mode adds to the system prompt."""
        preset = get_chat_prompt_preset(self.mode_combo.currentData())
        self.mode_description.setText(preset["description"])

    def selected_prompt_preset_id(self):
        """Return the chat mode chosen in the dialog."""
        return normalize_chat_prompt_preset(self.mode_combo.currentData())

    def reset_to_defaults(self):
        """Clear all overrides and show the global defaults again."""
        self.mode_combo.setCurrentIndex(max(0, self.mode_combo.findData(normalize_chat_prompt_preset(None))))
        self.temperature_checkbox.setChecked(False)
        self.temperature_spin.setValue(self._defaults.get("temperature", 0.5))
        self.max_tokens_checkbox.setChecked(False)
        self.max_tokens_spin.setValue(self._defaults.get("num_predict", 1024))

    def selected_overrides(self):
        """Return the normalized overrides chosen in the dialog."""
        return make_chat_inference_record(
            temperature_override=(
                self.temperature_spin.value()
                if self.temperature_checkbox.isChecked() else None
            ),
            max_tokens_override=(
                self.max_tokens_spin.value()
                if self.max_tokens_checkbox.isChecked() else None
            ),
        )
