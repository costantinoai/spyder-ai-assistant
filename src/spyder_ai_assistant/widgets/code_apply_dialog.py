"""Preview and confirm one chat-generated code application."""

from __future__ import annotations

from qtpy.QtWidgets import (
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QGroupBox,
    QLabel,
    QPlainTextEdit,
    QVBoxLayout,
)

from spyder_ai_assistant.utils.code_apply import (
    APPLY_MODE_INSERT,
    APPLY_MODE_REPLACE,
    build_code_apply_plan,
)


class CodeApplyDialog(QDialog):
    """Preview one editor mutation before applying chat-generated code."""

    def __init__(
        self,
        *,
        filename,
        document_text,
        code,
        cursor_position,
        selection_start,
        selection_end,
        default_mode=APPLY_MODE_INSERT,
        parent=None,
    ):
        super().__init__(parent)
        self.setWindowTitle("Apply Code")
        self.resize(920, 620)

        self._filename = filename or "Untitled"
        self._document_text = document_text or ""
        self._code = code or ""
        self._cursor_position = int(cursor_position or 0)
        self._selection_start = int(selection_start or 0)
        self._selection_end = int(selection_end or 0)
        self._has_selection = self._selection_end > self._selection_start
        self._default_mode = (
            APPLY_MODE_REPLACE if self._has_selection else APPLY_MODE_INSERT
        )
        if default_mode in {APPLY_MODE_INSERT, APPLY_MODE_REPLACE}:
            self._default_mode = default_mode
        if self._default_mode == APPLY_MODE_REPLACE and not self._has_selection:
            self._default_mode = APPLY_MODE_INSERT

        layout = QVBoxLayout(self)

        header = QLabel(
            "Review the proposed editor change before mutating the file. "
            "The preview uses the current editor text, cursor, and selection."
        )
        header.setWordWrap(True)
        layout.addWidget(header)

        metadata_group = QGroupBox("Target", self)
        metadata_form = QFormLayout(metadata_group)
        self.file_label = QLabel(self._filename)
        self.selection_label = QLabel(
            "Selection detected" if self._has_selection else "No active selection"
        )
        self.mode_combo = QComboBox(self)
        self.mode_combo.addItem("Insert at cursor", APPLY_MODE_INSERT)
        if self._has_selection:
            self.mode_combo.addItem("Replace selection", APPLY_MODE_REPLACE)
        self.mode_combo.currentIndexChanged.connect(self._refresh_preview)
        metadata_form.addRow("File", self.file_label)
        metadata_form.addRow("Selection", self.selection_label)
        metadata_form.addRow("Mode", self.mode_combo)
        layout.addWidget(metadata_group)

        self.summary_label = QLabel("")
        self.summary_label.setWordWrap(True)
        layout.addWidget(self.summary_label)

        preview_title = QLabel("Unified diff preview")
        layout.addWidget(preview_title)

        self.diff_view = QPlainTextEdit(self)
        self.diff_view.setReadOnly(True)
        self.diff_view.setLineWrapMode(QPlainTextEdit.NoWrap)
        layout.addWidget(self.diff_view, stretch=1)

        self.button_box = QDialogButtonBox(
            QDialogButtonBox.Cancel | QDialogButtonBox.Ok,
            parent=self,
        )
        self.button_box.accepted.connect(self.accept)
        self.button_box.rejected.connect(self.reject)
        self.apply_button = self.button_box.button(QDialogButtonBox.Ok)
        layout.addWidget(self.button_box)

        self.mode_combo.setCurrentIndex(
            self.mode_combo.findData(self._default_mode)
        )
        self._refresh_preview()

    def selected_plan(self):
        """Return the currently displayed apply plan."""
        return build_code_apply_plan(
            document_text=self._document_text,
            code=self._code,
            cursor_position=self._cursor_position,
            selection_start=self._selection_start,
            selection_end=self._selection_end,
            requested_mode=self.mode_combo.currentData(),
        )

    def select_mode(self, mode):
        """Select one apply mode programmatically."""
        index = self.mode_combo.findData(mode)
        if index < 0:
            return False
        self.mode_combo.setCurrentIndex(index)
        self._refresh_preview()
        return True

    def _refresh_preview(self):
        """Rebuild the displayed diff when the apply mode changes."""
        plan = self.selected_plan()
        self.summary_label.setText(
            "\n".join(
                [
                    plan["note"],
                    f"Mode: {plan['mode_label']}",
                    f"Selection preview: {plan['selection_preview'] or '(none)'}",
                    f"Code preview: {plan['code_preview'] or '(empty)'}",
                ]
            )
        )
        self.diff_view.setPlainText(plan["diff_text"])
        self.apply_button.setText(plan["mode_label"])
