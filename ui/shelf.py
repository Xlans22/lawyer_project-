"""Client Shelf — the client roster and the add/edit dialog."""

from __future__ import annotations

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPushButton,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from core import database, storage


class ClientDialog(QDialog):
    """Create or edit a client record."""

    def __init__(self, parent: QWidget | None = None, record=None) -> None:
        super().__init__(parent)
        self.setWindowTitle(
            "إضافة عميل"      # إضافة عميل
            if record is None
            else "تعديل بيانات العميل"
        )
        self.setMinimumWidth(420)
        self._record = record

        layout = QVBoxLayout(self)
        form = QFormLayout()
        form.setSpacing(10)

        self.name = QLineEdit()
        self.name.setPlaceholderText("اسم العميل بالكامل")
        self.case_number = QLineEdit()
        self.case_number.setPlaceholderText("رقم القضية / الملف")
        self.national_id = QLineEdit()
        self.phone = QLineEdit()
        self.phone.setPlaceholderText("01xxxxxxxxx")
        self.notes = QTextEdit()
        self.notes.setFixedHeight(80)

        form.addRow("الاسم *", self.name)
        form.addRow("رقم القضية", self.case_number)
        form.addRow("الرقم القومي", self.national_id)
        form.addRow("الهاتف", self.phone)
        form.addRow("ملاحظات", self.notes)
        layout.addLayout(form)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self._on_accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

        if record is not None:
            self.name.setText(record["name"])
            self.case_number.setText(record["case_number"] or "")
            self.national_id.setText(record["national_id"] or "")
            self.phone.setText(record["phone"] or "")
            self.notes.setPlainText(record["notes"] or "")

    def _on_accept(self) -> None:
        if not self.name.text().strip():
            QMessageBox.warning(
                self, "بيانات ناقصة",
                "يرجى إدخال اسم العميل.",
            )
            return
        self.accept()

    def values(self) -> dict[str, str]:
        return {
            "name": self.name.text().strip(),
            "case_number": self.case_number.text().strip(),
            "national_id": self.national_id.text().strip(),
            "phone": self.phone.text().strip(),
            "notes": self.notes.toPlainText().strip(),
        }


class ClientShelf(QWidget):
    """Searchable client roster. Emits the selected client to the rest of the UI."""

    client_selected = pyqtSignal(int, str)   # (client_id, name)
    clients_changed = pyqtSignal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("ShelfPanel")
        self._current_id: int | None = None

        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(12)

        self.search = QLineEdit()
        self.search.setPlaceholderText("بحث بالاسم أو رقم القضية...")
        self.search.textChanged.connect(self.reload)
        layout.addWidget(self.search)

        self.list = QListWidget()
        self.list.currentItemChanged.connect(self._on_row_changed)
        layout.addWidget(self.list, 1)

        row = QHBoxLayout()
        row.setSpacing(8)
        self.btn_add = QPushButton("عميل جديد")
        self.btn_edit = QPushButton("تعديل")
        self.btn_edit.setProperty("variant", "ghost")
        self.btn_delete = QPushButton("حذف")
        self.btn_delete.setProperty("variant", "ghost")
        row.addWidget(self.btn_add)
        row.addWidget(self.btn_edit)
        row.addWidget(self.btn_delete)
        layout.addLayout(row)

        self.btn_add.clicked.connect(self._add)
        self.btn_edit.clicked.connect(self._edit)
        self.btn_delete.clicked.connect(self._delete)

        self.reload()

    # -- data --------------------------------------------------------------

    def reload(self) -> None:
        """Repopulate from the database, preserving the current selection."""
        self.list.blockSignals(True)
        self.list.clear()
        for row in database.list_clients(self.search.text()):
            item = QListWidgetItem(
                f"{row['name']}\n{row['case_number'] or 'بدون رقم قضية'}"
            )
            item.setData(Qt.ItemDataRole.UserRole, row["id"])
            self.list.addItem(item)
        self.list.blockSignals(False)

        if self._current_id is not None:
            self.select(self._current_id)
        elif self.list.count():
            self.list.setCurrentRow(0)

    def select(self, client_id: int) -> None:
        for i in range(self.list.count()):
            item = self.list.item(i)
            if item.data(Qt.ItemDataRole.UserRole) == client_id:
                self.list.setCurrentItem(item)
                return

    def current_client(self) -> tuple[int, str] | None:
        item = self.list.currentItem()
        if item is None:
            return None
        client_id = item.data(Qt.ItemDataRole.UserRole)
        record = database.get_client(client_id)
        if record is None:
            return None
        return client_id, record["name"]

    # -- events ------------------------------------------------------------

    def _on_row_changed(self, current, _previous) -> None:
        if current is None:
            return
        client_id = current.data(Qt.ItemDataRole.UserRole)
        record = database.get_client(client_id)
        if record is not None:
            self._current_id = client_id
            self.client_selected.emit(client_id, record["name"])

    def _add(self) -> None:
        dialog = ClientDialog(self)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        values = dialog.values()
        client_id = database.add_client(**values)
        storage.provision_client(client_id, values["name"])  # create dossier tree
        self.reload()
        self.select(client_id)
        self.clients_changed.emit()

    def _edit(self) -> None:
        current = self.current_client()
        if current is None:
            return
        client_id, old_name = current
        record = database.get_client(client_id)
        dialog = ClientDialog(self, record)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        values = dialog.values()
        database.update_client(client_id, **values)
        if values["name"] != old_name:
            # Keep the folder name in sync with the record.
            storage.relocate(client_id, old_name, values["name"])
        self.reload()
        self.clients_changed.emit()

    def _delete(self) -> None:
        current = self.current_client()
        if current is None:
            return
        client_id, name = current
        confirm = QMessageBox.question(
            self,
            "تأكيد الحذف",
            f"هل أنت متأكد من حذف العميل «{name}»؟\n\n"
            "سيتم حذف السجل والجلسات، "
            "ولن تُحذف الملفات من القرص.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if confirm != QMessageBox.StandardButton.Yes:
            return

        from core import rag

        database.delete_client(client_id)
        rag.delete_client_vectors(client_id)  # documents on disk are intentionally kept
        self._current_id = None
        self.reload()
        self.clients_changed.emit()
