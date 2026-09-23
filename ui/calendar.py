"""Calendar view — مفكرة الجلسات: upcoming court sessions and deadlines."""

from __future__ import annotations

from datetime import date, datetime, timedelta

from PyQt6.QtCore import QDateTime, Qt, pyqtSignal
from PyQt6.QtWidgets import (
    QComboBox,
    QDateTimeEdit,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from core import database


class SessionDialog(QDialog):
    """Schedule a session or a filing deadline for a client."""

    def __init__(self, parent: QWidget | None = None, client_id: int | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("إضافة جلسة")
        self.setMinimumWidth(420)

        layout = QVBoxLayout(self)
        form = QFormLayout()
        form.setSpacing(10)

        self.client = QComboBox()
        self._clients = database.list_clients()
        for row in self._clients:
            self.client.addItem(row["name"], row["id"])
        if client_id is not None:
            index = self.client.findData(client_id)
            if index >= 0:
                self.client.setCurrentIndex(index)

        self.kind = QComboBox()
        self.kind.addItem("جلسة محكمة", "session")
        self.kind.addItem("موعد نهائي", "deadline")

        self.when = QDateTimeEdit()
        self.when.setCalendarPopup(True)
        self.when.setDateTime(QDateTime.currentDateTime().addDays(1))
        self.when.setDisplayFormat("yyyy-MM-dd  HH:mm")

        self.title = QLineEdit()
        self.title.setPlaceholderText("مثال: مرافعة افتتاحية")
        self.court = QLineEdit()
        self.court.setPlaceholderText("مثال: محكمة الجمهورية")
        self.notes = QLineEdit()

        form.addRow("العميل", self.client)
        form.addRow("النوع", self.kind)
        form.addRow("التاريخ والوقت", self.when)
        form.addRow("العنوان", self.title)
        form.addRow("المحكمة", self.court)
        form.addRow("ملاحظات", self.notes)
        layout.addLayout(form)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def values(self) -> dict:
        return {
            "client_id": self.client.currentData(),
            "kind": self.kind.currentData(),
            "session_date": self.when.dateTime().toString("yyyy-MM-dd HH:mm"),
            "title": self.title.text().strip(),
            "court": self.court.text().strip(),
            "notes": self.notes.text().strip(),
        }


class CalendarView(QWidget):
    """Upcoming-sessions table with the add/delete controls."""

    sessions_changed = pyqtSignal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("CalendarPanel")

        layout = QVBoxLayout(self)
        layout.setContentsMargins(18, 18, 18, 18)
        layout.setSpacing(12)

        header = QHBoxLayout()
        title = QLabel("مفكرة الجلسات")
        title.setProperty("role", "heading")
        header.addWidget(title)
        header.addStretch(1)

        self.range = QComboBox()
        self.range.addItem("الأسبوع القادم", 7)
        self.range.addItem("الشهر القادم", 30)
        self.range.addItem("ثلاثة أشهر", 90)
        self.range.currentIndexChanged.connect(self.reload)
        header.addWidget(self.range)

        self.btn_add = QPushButton("جلسة جديدة")
        self.btn_add.clicked.connect(self._add)
        header.addWidget(self.btn_add)

        self.btn_delete = QPushButton("حذف")
        self.btn_delete.setProperty("variant", "ghost")
        self.btn_delete.clicked.connect(self._delete)
        header.addWidget(self.btn_delete)

        layout.addLayout(header)

        self.table = QTableWidget(0, 5)
        self.table.setHorizontalHeaderLabels(
            [
                "التاريخ",
                "العميل",
                "العنوان",
                "المحكمة",
                "النوع",
            ]
        )
        self.table.setSelectionBehavior(
            QTableWidget.SelectionBehavior.SelectRows
        )
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.table.verticalHeader().setVisible(False)
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.setColumnWidth(0, 150)
        self.table.setColumnWidth(1, 170)
        layout.addWidget(self.table, 1)

        self.reload()

    def reload(self) -> None:
        horizon = self.range.currentData() or 7
        today = date.today()
        rows = database.sessions_between(today - timedelta(days=1), today + timedelta(days=horizon))

        self.table.setRowCount(0)
        now = datetime.now()
        for row in rows:
            r = self.table.rowCount()
            self.table.insertRow(r)

            try:
                when = datetime.fromisoformat(row["session_date"])
                overdue = when < now
                label = when.strftime("%Y-%m-%d  %H:%M")
            except ValueError:
                overdue = False
                label = row["session_date"]

            kind = "جلسة" if row["kind"] == "session" else "موعد"
            cells = [label, row["client_name"], row["title"] or "—",
                     row["court"] or "—", kind]

            for col, text in enumerate(cells):
                item = QTableWidgetItem(text)
                if overdue:
                    item.setForeground(Qt.GlobalColor.darkRed)
                if col == 0:
                    item.setData(Qt.ItemDataRole.UserRole, row["id"])
                self.table.setItem(r, col, item)

    def _add(self) -> None:
        dialog = SessionDialog(self)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        values = dialog.values()
        if values["client_id"] is None:
            QMessageBox.information(
                self, "لا يوجد عملاء",
                "أضف عميلًا أولًا من دفتر العملاء.",
            )
            return
        database.add_session(**values)
        self.reload()
        self.sessions_changed.emit()

    def _delete(self) -> None:
        row = self.table.currentRow()
        if row < 0:
            return
        item = self.table.item(row, 0)
        if item is None:
            return
        session_id = item.data(Qt.ItemDataRole.UserRole)
        if session_id is None:
            return
        confirm = QMessageBox.question(
            self, "تأكيد",
            "هل تريد حذف هذه الجلسة؟",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if confirm == QMessageBox.StandardButton.Yes:
            database.delete_session(session_id)
            self.reload()
            self.sessions_changed.emit()
