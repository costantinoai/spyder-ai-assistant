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
    compatible_api_url,
    describe_api_key_problem,
    describe_base_url_problem,
    make_provider_profile,
)


class ProviderProfilesDialog(QDialog):
    """Manage named OpenAI-compatible chat profiles."""

    def __init__(self, profiles=None, diagnostics=None, parent=None,
                 connection_tester=None):
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
        # Called as tester(profile, on_result). The probe runs off the GUI
        # thread, so the result arrives later; this dialog is modal and
        # would otherwise freeze for the whole request.
        self._connection_tester = connection_tester
        # Last probe outcome per profile id, so switching rows shows the
        # result that belongs to the row rather than the last one tested.
        self._test_results = {}
        self._test_in_flight = ""
        # A probe can outlive the dialog; see _on_test_result.
        self._closed = False

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

        # Inline notes, shown only when the current value cannot work. A
        # malformed endpoint used to fail silently: the model list simply
        # came back empty with nothing said about why.
        self.base_url_note_label = QLabel(form_group)
        self.base_url_note_label.setWordWrap(True)
        self.base_url_note_label.setVisible(False)
        self.api_key_note_label = QLabel(form_group)
        self.api_key_note_label.setWordWrap(True)
        self.api_key_note_label.setVisible(False)

        # "Test connection" answers the question the diagnostics row cannot:
        # whether the endpoint being edited right now actually responds.
        self.test_btn = QPushButton("Test connection", form_group)
        self.test_btn.clicked.connect(self._test_connection)
        self.test_result_label = QLabel("", form_group)
        self.test_result_label.setWordWrap(True)
        self.test_result_label.setTextInteractionFlags(Qt.TextSelectableByMouse)
        test_row = QHBoxLayout()
        test_row.addWidget(self.test_btn)
        test_row.addStretch()

        form.addRow(self.enabled_checkbox)
        form.addRow("Name", self.name_edit)
        form.addRow("Base URL", self.base_url_edit)
        form.addRow("", self.base_url_note_label)
        form.addRow("API Key", self.api_key_edit)
        form.addRow("", self.api_key_note_label)
        form.addRow("Diagnostics", self.status_label)
        form.addRow("", test_row)
        form.addRow("Test result", self.test_result_label)
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
                self.test_result_label.setText("")
                self._refresh_validation_notes()
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
            self.test_result_label.setText(
                self._format_test_result(profile.get("profile_id", ""))
            )
        finally:
            self._updating_form = False
        # Outside the guard: the notes reflect the loaded values, and
        # _store_current_profile is what the guard exists to suppress.
        self._refresh_validation_notes()

    def done(self, result):
        """Record that the dialog is gone before Qt tears the widgets down."""
        self._closed = True
        super().done(result)

    def _refresh_validation_notes(self, *_args):
        """Show or hide the Base URL and API key notes for the current form."""
        base_url = self.base_url_edit.text().strip()
        url_problem = describe_base_url_problem(base_url)
        self.base_url_note_label.setText(url_problem)
        self.base_url_note_label.setVisible(bool(url_problem))

        key_problem = describe_api_key_problem(base_url, self.api_key_edit.text())
        self.api_key_note_label.setText(key_problem)
        self.api_key_note_label.setVisible(bool(key_problem))

        # Nothing to probe without an endpoint, nothing to probe with when
        # the dialog was opened without a tester, and no point probing a
        # URL we already know is malformed: the user would just read a
        # transport error instead of the note above.
        self.test_btn.setEnabled(
            bool(base_url)
            and not url_problem
            and callable(self._connection_tester)
            and not self._test_in_flight
        )

    def _test_connection(self):
        """Probe the selected profile's endpoint without blocking the dialog."""
        self._store_current_profile()
        row = self._current_row()
        if row < 0 or not callable(self._connection_tester):
            return
        profile = dict(self._profiles[row])
        if not str(profile.get("base_url", "") or "").strip():
            return

        profile_id = profile.get("profile_id", "")
        self._test_in_flight = profile_id
        self.test_btn.setEnabled(False)
        self.test_result_label.setText(
            f"Testing {compatible_api_url(profile['base_url'])}..."
        )
        try:
            self._connection_tester(profile, lambda result: self._on_test_result(profile_id, result))
        except RuntimeError:
            self._on_test_result(profile_id, {
                "ok": False, "endpoint": compatible_api_url(profile["base_url"]),
                "error": "Connection testing is busy or shutting down. Try again shortly.",
            })

    def _on_test_result(self, profile_id, result):
        """Store and render one probe outcome.

        The probe runs on a worker, so by now the dialog may be closed or
        the user may have selected another profile; the result is kept
        either way and rendered only when its row is the visible one.
        """
        if self._closed:
            return
        self._test_results[profile_id] = dict(result or {})
        if self._test_in_flight == profile_id:
            self._test_in_flight = ""
        self._refresh_validation_notes()
        row = self._current_row()
        if row >= 0 and self._profiles[row].get("profile_id", "") == profile_id:
            self.test_result_label.setText(self._format_test_result(profile_id))

    def _format_test_result(self, profile_id):
        """Return the display text for one stored probe outcome."""
        result = self._test_results.get(profile_id)
        if not result:
            return "Not tested yet."
        endpoint = result.get("endpoint", "")
        if result.get("ok"):
            count = result.get("model_count", 0)
            sample = ", ".join(result.get("models", []))
            text = f"Reached {endpoint}: {count} model(s)."
            return f"{text} {sample}" if sample else text
        return f"Failed against {endpoint or 'the endpoint'}: {result.get('error', '')}"

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
        self._refresh_validation_notes()
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
