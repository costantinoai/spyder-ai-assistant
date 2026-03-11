"""Session history browser dialog for the chat pane."""

from __future__ import annotations

from qtpy.QtCore import Qt
from qtpy.QtWidgets import (
    QAbstractItemView,
    QDialog,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QTextEdit,
    QVBoxLayout,
)


class SessionHistoryDialog(QDialog):
    """Browse, reopen, duplicate, or delete saved chat sessions."""

    def __init__(self, rows, scope_info, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Chat History")
        self.resize(860, 520)

        self._rows = list(rows or [])
        self._selected_action = ""
        self._selected_session_id = ""

        scope_label = (scope_info or {}).get("scope_label", "Global")
        storage_path = (scope_info or {}).get("storage_path", "")

        layout = QVBoxLayout(self)

        header_label = QLabel(f"Scope: {scope_label}")
        header_label.setTextInteractionFlags(Qt.TextSelectableByMouse)
        layout.addWidget(header_label)

        if storage_path:
            path_label = QLabel(storage_path)
            path_label.setTextInteractionFlags(Qt.TextSelectableByMouse)
            path_label.setStyleSheet("color: #666;")
            layout.addWidget(path_label)

        self.table = QTableWidget(0, 4, self)
        self.table.setHorizontalHeaderLabels(
            ["Title", "Updated", "Messages", "Status"]
        )
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SingleSelection)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.verticalHeader().setVisible(False)
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.itemSelectionChanged.connect(self._on_selection_changed)
        self.table.itemDoubleClicked.connect(lambda _item: self._accept_action("open"))
        layout.addWidget(self.table)

        preview_title = QLabel("Preview")
        layout.addWidget(preview_title)

        self.preview = QTextEdit(self)
        self.preview.setReadOnly(True)
        self.preview.setAcceptRichText(False)
        layout.addWidget(self.preview, stretch=1)

        button_layout = QHBoxLayout()
        self.open_btn = QPushButton("Open")
        self.duplicate_btn = QPushButton("Duplicate")
        self.delete_btn = QPushButton("Delete")
        self.close_btn = QPushButton("Close")
        self.close_btn.clicked.connect(self.reject)
        self.open_btn.clicked.connect(lambda: self._accept_action("open"))
        self.duplicate_btn.clicked.connect(lambda: self._accept_action("duplicate"))
        self.delete_btn.clicked.connect(self._confirm_delete)
        for button in (
                self.open_btn,
                self.duplicate_btn,
                self.delete_btn):
            button_layout.addWidget(button)
        button_layout.addStretch()
        button_layout.addWidget(self.close_btn)
        layout.addLayout(button_layout)

        self._populate_rows()
        self._on_selection_changed()

    def selected_action(self):
        """Return the action chosen when the dialog closed."""
        return self._selected_action

    def selected_session_id(self):
        """Return the selected session id when the dialog closed."""
        return self._selected_session_id

    def select_session_id(self, session_id):
        """Select one session row programmatically by id."""
        for row in range(self.table.rowCount()):
            item = self.table.item(row, 0)
            if item is not None and item.data(Qt.UserRole) == session_id:
                self.table.selectRow(row)
                self._on_selection_changed()
                return True
        return False

    def request_action(self, action):
        """Accept the dialog programmatically with one action."""
        self._accept_action(action)

    def _populate_rows(self):
        """Fill the table with session history rows."""
        self.table.setRowCount(len(self._rows))
        for row_index, row in enumerate(self._rows):
            title_item = QTableWidgetItem(row.get("title", "Untitled chat"))
            title_item.setData(Qt.UserRole, row.get("session_id", ""))
            title_item.setToolTip(row.get("preview", ""))
            updated_item = QTableWidgetItem(row.get("updated_label", ""))
            messages_item = QTableWidgetItem(str(row.get("message_count", 0)))
            status_item = QTableWidgetItem("Open" if row.get("is_open") else "Saved")
            for column, item in enumerate(
                    (title_item, updated_item, messages_item, status_item)):
                self.table.setItem(row_index, column, item)

        self.table.resizeColumnsToContents()
        if self.table.rowCount():
            self.table.selectRow(0)

    def _current_row(self):
        """Return the currently selected history row payload."""
        current_row = self.table.currentRow()
        if current_row < 0 or current_row >= len(self._rows):
            return None
        return self._rows[current_row]

    def _on_selection_changed(self):
        """Refresh the preview and button state from the current row."""
        row = self._current_row()
        has_row = row is not None
        self.open_btn.setEnabled(has_row)
        self.duplicate_btn.setEnabled(has_row)
        self.delete_btn.setEnabled(has_row)
        if not has_row:
            self.preview.setPlainText("No saved chat sessions are available in this scope.")
            return
        self.preview.setPlainText(row.get("preview", ""))

    def _accept_action(self, action):
        """Close the dialog and report one selected action."""
        row = self._current_row()
        if row is None:
            return
        self._selected_action = action
        self._selected_session_id = row.get("session_id", "")
        self.accept()

    def _confirm_delete(self):
        """Require confirmation before deleting a saved session."""
        row = self._current_row()
        if row is None:
            return

        title = row.get("title", "Untitled chat")
        reply = QMessageBox.question(
            self,
            "Delete Chat Session",
            (
                f"Delete the saved chat session '{title}'?\n\n"
                "If this session is currently open, its tab will be closed too."
            ),
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        if reply == QMessageBox.Yes:
            self._accept_action("delete")
