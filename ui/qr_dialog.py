"""Direct Mobile Transfer dialog — QR code, bridge status, and controls."""

from __future__ import annotations

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QPixmap
from PyQt6.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from core import config
from ui import theme


class QrDialog(QDialog):
    """Shows the LAN URL as a scannable QR code and starts/stops the bridge."""

    bridge_toggled = pyqtSignal(bool)   # True = running
    token_rotated = pyqtSignal()

    def __init__(self, bridge, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.bridge = bridge
        self.setWindowTitle("النقل المباشر من الهاتف")
        self.setMinimumWidth(420)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 20, 20, 20)
        layout.setSpacing(12)

        heading = QLabel("امسح الرمز بالهاتف")
        heading.setProperty("role", "heading")
        heading.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(heading)

        caption = QLabel(
            "يجب أن يكون الهاتف "
            "متصلًا بنفس شبكة الواي فاي. "
            "لا تُرسل الصور إلى أي خادم خارجي."
        )
        caption.setProperty("role", "caption")
        caption.setWordWrap(True)
        caption.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(caption)

        # -- QR image -------------------------------------------------------
        self.qr_label = QLabel()
        self.qr_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.qr_label.setMinimumHeight(260)
        self.qr_label.setStyleSheet(
            f"background:{theme.T['white']};border:1px solid {theme.T['border']};"
            f"border-radius:10px;padding:14px;"
        )
        theme.apply_shadow(self.qr_label, radius=22, dy=4, alpha=60)
        layout.addWidget(self.qr_label)

        # -- URL + status ---------------------------------------------------
        self.url_label = QLabel()
        self.url_label.setProperty("role", "caption")
        self.url_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.url_label.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse
        )
        self.url_label.setWordWrap(True)
        layout.addWidget(self.url_label)

        self.status_label = QLabel()
        self.status_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.status_label.setWordWrap(True)
        layout.addWidget(self.status_label)

        # -- controls -------------------------------------------------------
        controls = QHBoxLayout()
        controls.setSpacing(8)

        self.btn_toggle = QPushButton("تشغيل الاتصال")
        self.btn_toggle.clicked.connect(self._toggle)

        self.btn_rotate = QPushButton("تجديد الرمز")
        self.btn_rotate.setProperty("variant", "ghost")
        self.btn_rotate.clicked.connect(self._rotate)

        self.btn_close = QPushButton("إغلاق")
        self.btn_close.setProperty("variant", "ghost")
        self.btn_close.clicked.connect(self.reject)

        controls.addWidget(self.btn_toggle)
        controls.addWidget(self.btn_rotate)
        controls.addStretch(1)
        controls.addWidget(self.btn_close)
        layout.addLayout(controls)

        self.refresh()

    # -- rendering ---------------------------------------------------------

    def refresh(self) -> None:
        """Redraw the QR and the status line from current bridge state."""
        if not self.bridge.running:
            self.qr_label.setPixmap(QPixmap())
            self.qr_label.setText(
                "الاتصال متوقف"
            )
            self.url_label.setText("")
            self.status_label.setText(
                f"المنفذ المحلي: {config.MOBILE_PORT}"
            )
            self.status_label.setStyleSheet(f"color:{theme.T['muted']};")
            self.btn_toggle.setText("تشغيل الاتصال")
            return

        url = self.bridge.url()
        pixmap = _qr_pixmap(url, size=240)
        if pixmap is not None:
            self.qr_label.setPixmap(pixmap)
        else:
            self.qr_label.setText(
                "Install the 'qrcode' package to display the code:\n"
                "pip install \"qrcode[pil]\""
            )

        self.url_label.setText(url)
        target = self.bridge.active_client
        if target is None:
            self.status_label.setText(
                "⚠ لم يتم اختيار عميل "
                "— ستُرفض الرفعات"
            )
            self.status_label.setStyleSheet(f"color:{theme.T['brass']};")
        else:
            self.status_label.setText(
                f"الملف المستقبل: «{target[1]}» "
                f"→ مجلد المستندات"
            )
            self.status_label.setStyleSheet(f"color:{theme.T['success']};")

        self.btn_toggle.setText("إيقاف الاتصال")

    # -- actions -----------------------------------------------------------

    def _toggle(self) -> None:
        if self.bridge.running:
            self.bridge.stop()
            self.bridge_toggled.emit(False)
        else:
            try:
                self.bridge.start()
            except Exception as exc:
                QMessageBox.critical(
                    self, "خطأ",
                    f"Could not start the bridge:\n{exc}\n\n"
                    "Make sure fastapi and uvicorn are installed.",
                )
                return
            self.bridge_toggled.emit(True)
        self.refresh()

    def _rotate(self) -> None:
        self.bridge.rotate_token()
        self.token_rotated.emit()
        self.refresh()

    def closeEvent(self, event) -> None:  # noqa: N802 - Qt override
        # Leave the bridge running on close — the lawyer may want to keep
        # transferring. It is stopped explicitly from the toggle or on app exit.
        super().closeEvent(event)


def _qr_pixmap(url: str, size: int = 240) -> QPixmap | None:
    """Render a URL as a QR code QPixmap, or None if qrcode is missing."""
    try:
        import qrcode
        from PIL.ImageQt import ImageQt
    except ImportError:
        return None

    qr = qrcode.QRCode(
        version=None,
        error_correction=qrcode.constants.ERROR_CORRECT_M,
        box_size=10,
        border=2,
    )
    qr.add_data(url)
    qr.make(fit=True)

    # Navy-on-white keeps the code scannable while matching the app palette.
    image = qr.make_image(
        fill_color=theme.T["navy"], back_color=theme.T["white"]
    ).convert("RGB")
    image = image.resize((size, size))

    return QPixmap.fromImage(ImageQt(image))
