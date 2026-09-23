"""Navigation rail. Switches the centre panel between the app's main views."""

from __future__ import annotations

from PyQt6.QtCore import pyqtSignal
from PyQt6.QtWidgets import (
    QButtonGroup,
    QLabel,
    QPushButton,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

# (arabic label, latin sub-label)
NAV_ITEMS = [
    ("دفتر العملاء", "Client Shelf"),
    ("مفكرة الجلسات", "Calendar"),
    ("الإعدادات", "Settings"),
]


class Sidebar(QWidget):
    navigate = pyqtSignal(int)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("Sidebar")
        self.setFixedWidth(220)
        self.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Expanding)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        logo = QLabel("Lawyer Agent")
        logo.setObjectName("SidebarLogo")
        layout.addWidget(logo)

        sub = QLabel("مكتب المحاماة")
        sub.setObjectName("SidebarSub")
        layout.addWidget(sub)

        self._group = QButtonGroup(self)
        self._group.setExclusive(True)

        for index, (arabic, latin) in enumerate(NAV_ITEMS):
            button = QPushButton(f"{arabic}\n{latin}")
            button.setObjectName("NavButton")
            button.setCheckable(True)
            button.setCursor(button.cursor())
            self._group.addButton(button, index)
            layout.addWidget(button)
            if index == 0:
                button.setChecked(True)

        self._group.idClicked.connect(self.navigate.emit)

        layout.addStretch(1)

        self.status = QLabel("جاهز")
        self.status.setObjectName("SidebarSub")
        self.status.setWordWrap(True)
        layout.addWidget(self.status)

    def set_status(self, text: str) -> None:
        self.status.setText(text)

    def select(self, index: int) -> None:
        button = self._group.button(index)
        if button is not None:
            button.setChecked(True)
            self.navigate.emit(index)
