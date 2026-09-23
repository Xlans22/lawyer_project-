"""
Main window: navigation rail | centre workspace | AI chat.

Threading note — the mobile bridge and the session notifier both run outside the
Qt main thread and report in through QObject signals. Qt queues those emissions
automatically, so UI updates always happen on the main thread. Never touch a
widget directly from those callbacks.
"""

from __future__ import annotations

from PyQt6.QtCore import QObject, Qt, pyqtSignal
from PyQt6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QSizePolicy,
    QSplitter,
    QStackedWidget,
    QStatusBar,
    QToolBar,
    QVBoxLayout,
    QWidget,
)

from core import config, database, notifier
from mobile.server import MobileBridge
from ui import theme
from ui.calendar import CalendarView
from ui.chat import ChatPanel
from ui.documents import DocumentPanel
from ui.qr_dialog import QrDialog
from ui.settings import SettingsView
from ui.shelf import ClientShelf


class BridgeSignals(QObject):
    """Qt-side relay for events raised on the mobile-bridge thread."""

    photo_received = pyqtSignal(str, str)   # (client_name, filename)
    upload_failed = pyqtSignal(str)


class NotifierSignals(QObject):
    """Qt-side relay for the session-notifier thread."""

    reminded = pyqtSignal(str, str)


class MainWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("Lawyer Agent — مكتب المحاماة")
        self.resize(1440, 880)
        self.setMinimumSize(1100, 700)

        self._active_client: tuple[int, str] | None = None

        # -- cross-thread relays -------------------------------------------
        self.bridge_signals = BridgeSignals()
        self.bridge_signals.photo_received.connect(self._on_photo_received)

        self.notifier_signals = NotifierSignals()
        self.notifier_signals.reminded.connect(self._on_reminded)

        # -- mobile bridge --------------------------------------------------
        self.bridge = MobileBridge(on_upload=self._bridge_upload_callback)

        # -- central layout -------------------------------------------------
        central = QWidget()
        root = QHBoxLayout(central)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(14)

        from ui.sidebar import Sidebar

        self.sidebar = Sidebar()
        self.sidebar.navigate.connect(self._on_navigate)
        root.addWidget(self.sidebar)

        self.stack = QStackedWidget()
        self.stack.addWidget(self._build_shelf_view())   # index 0
        self.calendar = CalendarView()
        self.stack.addWidget(self.calendar)              # index 1
        self.settings = SettingsView()
        self.stack.addWidget(self.settings)              # index 2
        root.addWidget(self.stack, 4)

        self.chat = ChatPanel()
        self.chat.status.connect(self._set_status)
        root.addWidget(self.chat, 2)

        self.setCentralWidget(central)

        # -- floating-card shadows -----------------------------------------
        # Lift each top-level content card off the dark canvas. The shelf and
        # documents cards get theirs inside _build_shelf_view; here we lift the
        # calendar, settings, and chat cards. Qt allows one effect per widget.
        theme.apply_shadow(self.calendar)
        theme.apply_shadow(self.settings)
        theme.apply_shadow(self.chat)

        # -- chrome ---------------------------------------------------------
        self._build_toolbar()
        self.setStatusBar(QStatusBar())
        self._set_status("جاهز")

        # -- background services -------------------------------------------
        self.notifier = notifier.SessionNotifier(on_event=self._notifier_callback)
        self.notifier.start()

        self._check_ai_on_startup()

    # ------------------------------------------------------------------
    # construction
    # ------------------------------------------------------------------

    def _build_shelf_view(self) -> QWidget:
        """Client Shelf: roster on one side, dossier contents on the other."""
        splitter = QSplitter(Qt.Orientation.Horizontal)

        self.shelf = ClientShelf()
        self.shelf.client_selected.connect(self._on_client_selected)
        self.shelf.clients_changed.connect(self._on_clients_changed)
        splitter.addWidget(self.shelf)

        self.documents = DocumentPanel()
        self.documents.status.connect(self._set_status)
        self.documents.documents_changed.connect(self._on_documents_changed)
        splitter.addWidget(self.documents)

        splitter.setStretchFactor(0, 1)
        splitter.setStretchFactor(1, 2)
        splitter.setSizes([340, 700])

        # Lift the roster and dossier cards off the dark canvas; the 14px
        # transparent splitter handle becomes a dark gutter between them.
        theme.apply_shadow(self.shelf)
        theme.apply_shadow(self.documents)
        return splitter

    def _build_toolbar(self) -> None:
        bar = QToolBar("main")
        bar.setMovable(False)
        self.addToolBar(bar)

        # Push the action buttons to the far side of the bar.
        spacer = QWidget()
        spacer.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred
        )
        bar.addWidget(spacer)

        self.btn_mobile = QPushButton("نقل من الهاتف")
        self.btn_mobile.setToolTip("Direct Mobile Transfer over the local network")
        self.btn_mobile.clicked.connect(self._open_qr)
        bar.addWidget(self.btn_mobile)

        self.btn_refresh = QPushButton("تحديث")
        self.btn_refresh.setProperty("variant", "ghost")
        self.btn_refresh.clicked.connect(self._refresh_all)
        bar.addWidget(self.btn_refresh)

        # The toolbar and its buttons are styled globally in styles.qss
        # (QToolBar / QToolBar QPushButton), so they read correctly on the dark
        # chrome bar without an inline stylesheet here.

    # ------------------------------------------------------------------
    # navigation
    # ------------------------------------------------------------------

    def _on_navigate(self, index: int) -> None:
        self.stack.setCurrentIndex(index)
        if index == 1:
            self.calendar.reload()
        elif index == 2:
            self.settings.run_checks()

    # ------------------------------------------------------------------
    # selection wiring
    # ------------------------------------------------------------------

    def _on_client_selected(self, client_id: int, name: str) -> None:
        self._active_client = (client_id, name)
        self.documents.set_client(client_id, name)
        self.chat.set_client(client_id, name)
        # Route incoming mobile photos into this client's dossier.
        self.bridge.active_client = (client_id, name)
        self._set_status(f"العميل الحالي: {name}")

    def _on_clients_changed(self) -> None:
        if self.shelf.current_client() is None:
            self._active_client = None
            self.documents.clear()
            self.chat.clear_scope()
            self.bridge.active_client = None

    def _on_documents_changed(self) -> None:
        pass  # the panel reloads itself; hook for future dashboard updates

    def _refresh_all(self) -> None:
        self.shelf.reload()
        if self._active_client is not None:
            self.documents.reload()
            self.chat.set_client(*self._active_client)
        self.calendar.reload()
        self._set_status("تم التحديث")

    # ------------------------------------------------------------------
    # mobile bridge
    # ------------------------------------------------------------------

    def _bridge_upload_callback(self, client_name: str, filename: str) -> None:
        """Called on the uvicorn thread — emit a signal, touch no widgets."""
        self.bridge_signals.photo_received.emit(client_name, filename)

    def _on_photo_received(self, client_name: str, filename: str) -> None:
        """Runs on the Qt main thread."""
        self._set_status(f"تم استلام صورة: {filename}")
        if self._active_client is not None and self._active_client[1] == client_name:
            self.documents.reload()
        notifier.notify_now(
            "تم استلام مستند جديد",
            f"{client_name} — {filename}",
        )

    def _open_qr(self) -> None:
        if self.bridge.active_client is None:
            QMessageBox.information(
                self,
                "اختر عميلًا أولًا",
                "يرجى اختيار عميل من "
                "دفتر العملاء قبل "
                "تشغيل النقل المباشر، "
                "ليتم حفط الصور في ملفه.",
            )
            return
        dialog = QrDialog(self.bridge, self)
        dialog.bridge_toggled.connect(self._on_bridge_toggled)
        dialog.exec()
        self._on_bridge_toggled(self.bridge.running)

    def _on_bridge_toggled(self, running: bool) -> None:
        self._set_status(
            "جسر الهاتف: مشغّل"
            if running
            else "جسر الهاتف: متوقف"
        )
        self.btn_mobile.setText(
            "نقل من الهاتف ●"
            if running
            else "نقل من الهاتف"
        )

    # ------------------------------------------------------------------
    # notifier
    # ------------------------------------------------------------------

    def _notifier_callback(self, title: str, message: str) -> None:
        """Called on the notifier thread — emit, don't touch widgets."""
        self.notifier_signals.reminded.emit(title, message)

    def _on_reminded(self, title: str, message: str) -> None:
        self.calendar.reload()
        self._set_status(f"تذكير: {title} — {message}")

    # ------------------------------------------------------------------
    # startup checks
    # ------------------------------------------------------------------

    def _check_ai_on_startup(self) -> None:
        """Warn early if Ollama is down, without blocking the window."""
        from ui.workers import FunctionWorker

        worker = FunctionWorker(rag_probe, parent=self)
        worker.finished_ok.connect(self._on_ai_probe)
        self._ai_probe = worker
        worker.start()

    def _on_ai_probe(self, result) -> None:
        healthy, message = result
        self.sidebar.set_status(
            "المساعد جاهز"
            if healthy
            else "المساعد غير جاهز"
        )
        if not healthy:
            self._set_status(message)

    # ------------------------------------------------------------------
    # shutdown
    # ------------------------------------------------------------------

    def _set_status(self, text: str) -> None:
        self.statusBar().showMessage(text)

    def closeEvent(self, event) -> None:  # noqa: N802 - Qt override
        """Stop the background threads so the process exits cleanly."""
        self.notifier.stop()
        self.bridge.stop()
        super().closeEvent(event)


def rag_probe() -> tuple[bool, str]:
    from core import rag

    return rag.probe()
