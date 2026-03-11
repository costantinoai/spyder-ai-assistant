"""Dialog for deleting one exchange from the active chat session."""

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


class ExchangeDeleteDialog(QDialog):
    """Browse and delete one exchange from the active conversation."""

    def __init__(self, rows, session_title="", parent=None):
        super().__init__(parent)
        self.setWindowTitle("Delete Exchange")
        self.resize(860, 520)

        self._rows = list(rows or [])
        self._selected_exchange_index = None

        layout = QVBoxLayout(self)

        header = QLabel(
            "Delete one exchange from the active chat tab. "
            "The saved session, exports, and future regenerations will use "
            "the updated conversation."
        )
        header.setWordWrap(True)
        layout.addWidget(header)

        if session_title:
            title_label = QLabel(f"Tab: {session_title}")
            title_label.setTextInteractionFlags(Qt.TextSelectableByMouse)
            layout.addWidget(title_label)

        self.table = QTableWidget(0, 3, self)
        self.table.setHorizontalHeaderLabels(["Turn", "Preview", "Status"])
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SingleSelection)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.verticalHeader().setVisible(False)
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.itemSelectionChanged.connect(self._on_selection_changed)
        self.table.itemDoubleClicked.connect(lambda _item: self._confirm_delete())
        layout.addWidget(self.table)

        preview_title = QLabel("Selected exchange")
        layout.addWidget(preview_title)

        self.preview = QTextEdit(self)
        self.preview.setReadOnly(True)
        self.preview.setAcceptRichText(False)
        layout.addWidget(self.preview, stretch=1)

        button_layout = QHBoxLayout()
        self.delete_btn = QPushButton("Delete")
        self.close_btn = QPushButton("Close")
        self.close_btn.clicked.connect(self.reject)
        self.delete_btn.clicked.connect(self._confirm_delete)
        button_layout.addWidget(self.delete_btn)
        button_layout.addStretch()
        button_layout.addWidget(self.close_btn)
        layout.addLayout(button_layout)

        self._populate_rows()
        self._on_selection_changed()

    def selected_exchange_index(self):
        """Return the selected exchange index when the dialog closes."""
        return self._selected_exchange_index

    def select_exchange_index(self, exchange_index):
        """Select one exchange row programmatically by exchange index."""
        for row in range(self.table.rowCount()):
            item = self.table.item(row, 0)
            if item is not None and item.data(Qt.UserRole) == exchange_index:
                self.table.selectRow(row)
                self._on_selection_changed()
                return True
        return False

    def request_delete(self):
        """Accept the dialog programmatically as if Delete were clicked."""
        self._confirm_delete()

    def _populate_rows(self):
        """Fill the table with exchange rows."""
        self.table.setRowCount(len(self._rows))
        for row_index, row in enumerate(self._rows):
            turn_item = QTableWidgetItem(row.get("title", "Turn"))
            turn_item.setData(Qt.UserRole, row.get("exchange_index"))
            preview_item = QTableWidgetItem(row.get("user_preview", ""))
            preview_item.setToolTip(row.get("preview", ""))
            status_item = QTableWidgetItem(row.get("status", ""))
            for column, item in enumerate((turn_item, preview_item, status_item)):
                self.table.setItem(row_index, column, item)

        self.table.resizeColumnsToContents()
        if self.table.rowCount():
            self.table.selectRow(0)

    def _current_row(self):
        """Return the currently selected exchange row payload."""
        current_row = self.table.currentRow()
        if current_row < 0 or current_row >= len(self._rows):
            return None
        return self._rows[current_row]

    def _on_selection_changed(self):
        """Refresh preview and button state for the current exchange."""
        row = self._current_row()
        has_row = row is not None
        self.delete_btn.setEnabled(has_row)
        if not has_row:
            self.preview.setPlainText("No deletable exchanges are available in this tab.")
            return
        self.preview.setPlainText(row.get("preview", ""))

    def _confirm_delete(self):
        """Require confirmation before deleting a chat exchange."""
        row = self._current_row()
        if row is None:
            return

        reply = QMessageBox.question(
            self,
            "Delete Exchange",
            (
                f"Delete {row.get('title', 'this exchange')} from the active chat?\n\n"
                "This updates the saved session immediately."
            ),
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        if reply != QMessageBox.Yes:
            return

        self._selected_exchange_index = row.get("exchange_index")
        self.accept()
