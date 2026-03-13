"""Dialog for managing named chat-provider profiles."""

from __future__ import annotations

from qtpy.QtCore import Qt
from qtpy.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
)

from spyder_ai_assistant.utils.provider_profiles import (
    PROVIDER_KIND_OPENAI_COMPATIBLE,
    make_provider_profile,
)


class ProviderProfilesDialog(QDialog):
    """Manage named OpenAI-compatible chat profiles."""

    def __init__(self, profiles=None, diagnostics=None, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Provider Profiles")
        self.resize(980, 620)

        self._profiles = [dict(profile) for profile in (profiles or [])]
        self._diagnostics = {
            record.get("profile_id", ""): dict(record)
            for record in (diagnostics or [])
            if record.get("profile_id")
        }
        self._updating_form = False

        layout = QVBoxLayout(self)

        intro = QLabel(
            "Manage named OpenAI-compatible endpoints here. "
            "These profiles are used by the shared chat and completion "
            "model selectors."
        )
        intro.setWordWrap(True)
        layout.addWidget(intro)

        self.table = QTableWidget(0, 4, self)
        self.table.setHorizontalHeaderLabels(
            ["Profile", "Endpoint", "Enabled", "Status"]
        )
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SingleSelection)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.verticalHeader().setVisible(False)
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.itemSelectionChanged.connect(self._on_selection_changed)
        layout.addWidget(self.table)

        button_row = QHBoxLayout()
        self.new_btn = QPushButton("New")
        self.duplicate_btn = QPushButton("Duplicate")
        self.delete_btn = QPushButton("Delete")
        self.new_btn.clicked.connect(self._create_profile)
        self.duplicate_btn.clicked.connect(self._duplicate_profile)
        self.delete_btn.clicked.connect(self._delete_profile)
        button_row.addWidget(self.new_btn)
        button_row.addWidget(self.duplicate_btn)
        button_row.addWidget(self.delete_btn)
        button_row.addStretch()
        layout.addLayout(button_row)

        form_group = QGroupBox("Selected profile", self)
        form = QFormLayout(form_group)
        self.enabled_checkbox = QCheckBox("Enabled", self)
        self.enabled_checkbox.toggled.connect(self._store_current_profile)
        self.name_edit = QLineEdit(self)
        self.name_edit.textChanged.connect(self._store_current_profile)
        self.base_url_edit = QLineEdit(self)
        self.base_url_edit.textChanged.connect(self._store_current_profile)
        self.api_key_edit = QLineEdit(self)
        self.api_key_edit.setEchoMode(QLineEdit.Password)
        self.api_key_edit.textChanged.connect(self._store_current_profile)
        self.status_label = QLabel("")
        self.status_label.setWordWrap(True)
        self.status_label.setTextInteractionFlags(Qt.TextSelectableByMouse)

        form.addRow(self.enabled_checkbox)
        form.addRow("Name", self.name_edit)
        form.addRow("Base URL", self.base_url_edit)
        form.addRow("API Key", self.api_key_edit)
        form.addRow("Diagnostics", self.status_label)
        layout.addWidget(form_group)

        self.button_box = QDialogButtonBox(
            QDialogButtonBox.Cancel | QDialogButtonBox.Save,
            parent=self,
        )
        self.button_box.accepted.connect(self.accept)
        self.button_box.rejected.connect(self.reject)
        layout.addWidget(self.button_box)

        self._populate_rows()
        if self._profiles:
            self.table.selectRow(0)
        self._on_selection_changed()

    def selected_profiles(self):
        """Return the normalized profile list when the dialog closes."""
        self._store_current_profile()
        return [dict(profile) for profile in self._profiles]

    def replace_profiles(self, profiles):
        """Replace the entire profile list programmatically."""
        self._profiles = [dict(profile) for profile in (profiles or [])]
        self._populate_rows()
        if self._profiles:
            self.table.selectRow(0)
        self._on_selection_changed()

    def add_profile(self, *, label, base_url, api_key="", enabled=True):
        """Create one profile programmatically and select it."""
        profile = make_provider_profile(
            label=label,
            provider_kind=PROVIDER_KIND_OPENAI_COMPATIBLE,
            base_url=base_url,
            api_key=api_key,
            enabled=enabled,
        )
        self._profiles.append(profile)
        self._populate_rows()
        self.table.selectRow(len(self._profiles) - 1)
        self._on_selection_changed()
        return profile["profile_id"]

    def select_profile_by_label(self, label):
        """Select one profile row by label."""
        for row_index, profile in enumerate(self._profiles):
            if profile.get("label") == label:
                self.table.selectRow(row_index)
                self._on_selection_changed()
                return True
        return False

    def update_selected_profile(self, *, label=None, base_url=None, api_key=None,
                                enabled=None):
        """Update the currently selected profile programmatically."""
        row = self._current_row()
        if row < 0:
            return False
        if label is not None:
            self.name_edit.setText(label)
        if base_url is not None:
            self.base_url_edit.setText(base_url)
        if api_key is not None:
            self.api_key_edit.setText(api_key)
        if enabled is not None:
            self.enabled_checkbox.setChecked(bool(enabled))
        self._store_current_profile()
        return True

    def _populate_rows(self):
        """Refresh the table from the current in-memory profile list."""
        self.table.setRowCount(len(self._profiles))
        for row_index, profile in enumerate(self._profiles):
            diagnostic = self._diagnostics.get(profile.get("profile_id", ""), {})
            items = [
                QTableWidgetItem(profile.get("label", "")),
                QTableWidgetItem(profile.get("base_url", "")),
                QTableWidgetItem("Yes" if profile.get("enabled") else "No"),
                QTableWidgetItem(diagnostic.get("status", "Not checked")),
            ]
            for item in items:
                item.setFlags(item.flags() & ~Qt.ItemIsEditable)
            for column, item in enumerate(items):
                self.table.setItem(row_index, column, item)
        self.table.resizeColumnsToContents()

    def _current_row(self):
        """Return the current table row index."""
        row = self.table.currentRow()
        if row < 0 or row >= len(self._profiles):
            return -1
        return row

    def _on_selection_changed(self):
        """Load the selected profile into the edit form."""
        row = self._current_row()
        has_profile = row >= 0
        self.duplicate_btn.setEnabled(has_profile)
        self.delete_btn.setEnabled(has_profile)
        self._updating_form = True
        try:
            if not has_profile:
                self.enabled_checkbox.setChecked(False)
                self.name_edit.clear()
                self.base_url_edit.clear()
                self.api_key_edit.clear()
                self.status_label.setText("No profile selected.")
                return
            profile = self._profiles[row]
            diagnostic = self._diagnostics.get(profile.get("profile_id", ""), {})
            self.enabled_checkbox.setChecked(bool(profile.get("enabled", True)))
            self.name_edit.setText(profile.get("label", ""))
            self.base_url_edit.setText(profile.get("base_url", ""))
            self.api_key_edit.setText(profile.get("api_key", ""))
            if diagnostic:
                self.status_label.setText(
                    "\n".join(
                        [
                            f"Status: {diagnostic.get('status', 'unknown')}",
                            f"Endpoint: {diagnostic.get('endpoint', '')}",
                            f"Message: {diagnostic.get('message', '')}",
                        ]
                    )
                )
            else:
                self.status_label.setText("No diagnostics collected yet.")
        finally:
            self._updating_form = False

    def _store_current_profile(self):
        """Persist the current edit form back into the selected profile."""
        if self._updating_form:
            return
        row = self._current_row()
        if row < 0:
            return
        profile = self._profiles[row]
        profile["enabled"] = self.enabled_checkbox.isChecked()
        profile["label"] = self.name_edit.text().strip() or "Compatible endpoint"
        profile["base_url"] = self.base_url_edit.text().strip()
        profile["api_key"] = self.api_key_edit.text()
        self._populate_rows()
        self.table.selectRow(row)

    def _create_profile(self):
        """Append one blank compatible profile."""
        self._store_current_profile()
        self._profiles.append(
            make_provider_profile(
                label="New compatible endpoint",
                provider_kind=PROVIDER_KIND_OPENAI_COMPATIBLE,
            )
        )
        self._populate_rows()
        self.table.selectRow(len(self._profiles) - 1)
        self._on_selection_changed()

    def _duplicate_profile(self):
        """Duplicate the selected profile with a fresh id."""
        row = self._current_row()
        if row < 0:
            return
        source = self._profiles[row]
        self._profiles.append(
            make_provider_profile(
                label=f"{source.get('label', 'Compatible endpoint')} Copy",
                provider_kind=source.get(
                    "provider_kind",
                    PROVIDER_KIND_OPENAI_COMPATIBLE,
                ),
                base_url=source.get("base_url", ""),
                api_key=source.get("api_key", ""),
                enabled=source.get("enabled", True),
            )
        )
        self._populate_rows()
        self.table.selectRow(len(self._profiles) - 1)
        self._on_selection_changed()

    def _delete_profile(self):
        """Remove the selected profile."""
        self._store_current_profile()
        row = self._current_row()
        if row < 0:
            return
        self._profiles.pop(row)
        self._populate_rows()
        if self._profiles:
            self.table.selectRow(max(0, min(row, len(self._profiles) - 1)))
        self._on_selection_changed()
