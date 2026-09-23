"""Settings view — dependency health, paths, and privacy posture."""

from __future__ import annotations

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from core import config, ocr, rag
from ui import theme
from ui.workers import FunctionWorker


class SettingsView(QWidget):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.Shape.NoFrame)

        body = QWidget()
        body.setObjectName("SettingsPanel")
        layout = QVBoxLayout(body)
        layout.setContentsMargins(18, 18, 18, 18)
        layout.setSpacing(14)

        title = QLabel("الإعدادات")
        title.setProperty("role", "heading")
        layout.addWidget(title)

        # -- diagnostics ----------------------------------------------------
        health_box = QGroupBox("حالة النظام")
        health_layout = QVBoxLayout(health_box)

        self.ocr_state = QLabel("لم يتم الفحص بعد")
        self.ocr_state.setWordWrap(True)
        self.ai_state = QLabel("لم يتم الفحص بعد")
        self.ai_state.setWordWrap(True)

        for label, widget in (("OCR / Tesseract", self.ocr_state), ("AI / Ollama", self.ai_state)):
            row = QHBoxLayout()
            caption = QLabel(label)
            caption.setFixedWidth(150)
            caption.setProperty("role", "muted")
            row.addWidget(caption)
            row.addWidget(widget, 1)
            health_layout.addLayout(row)

        self.btn_check = QPushButton("فحص النظام")
        self.btn_check.clicked.connect(self.run_checks)
        health_layout.addWidget(self.btn_check)
        layout.addWidget(health_box)

        # -- configuration --------------------------------------------------
        config_box = QGroupBox("الإعدادات الحالية")
        form = QFormLayout(config_box)
        for label, value in [
            ("مجلد البيانات", str(config.DATA_DIR)),
            ("قاعدة البيانات", str(config.DB_PATH)),
            ("مخزن المتجهات", str(config.VECTOR_DIR)),
            ("نموذج اللغة", config.LLM_MODEL),
            ("نموذج التشفير", config.EMBED_MODEL),
            ("عنوان Ollama", config.OLLAMA_BASE_URL),
            ("لغة التعرف الضوئي", config.OCR_LANG),
            ("دقة المسح", f"{config.OCR_DPI} DPI"),
            ("منفذ الهاتف", str(config.MOBILE_PORT)),
            ("اتجاه الواجهة", "RTL" if config.UI_RTL else "LTR"),
        ]:
            value_label = QLabel(value)
            value_label.setProperty("role", "caption")
            value_label.setTextInteractionFlags(
                Qt.TextInteractionFlag.TextSelectableByMouse
            )
            form.addRow(label, value_label)
        layout.addWidget(config_box)

        # -- privacy --------------------------------------------------------
        privacy_box = QGroupBox("الخصوصية")
        privacy_layout = QVBoxLayout(privacy_box)
        privacy = QLabel(
            "جميع العمليات تتم "
            "على هذا الجهاز. "
            "لا تخرج نصوص الملفات "
            "إلى أي خادم خارجي مطلقًا.\n\n"
            "الاتصالات الوحيدة: "
            "Ollama على الجهاز المحلي، "
            "وجسر الهاتف عند تشغيله.\n\n"
            "تنبيه: مجلدا البيانات "
            "والمتجهات غير مشفرين "
            "على القرص. استخدم "
            "BitLocker أو VeraCrypt لحماية الملفات."
        )
        privacy.setWordWrap(True)
        privacy_layout.addWidget(privacy)
        layout.addWidget(privacy_box)

        layout.addStretch(1)
        scroll.setWidget(body)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(scroll)

    # -- checks ------------------------------------------------------------

    def run_checks(self) -> None:
        self.ocr_state.setText("جارٍ الفحص...")
        self.ai_state.setText("جارٍ الفحص...")
        self.btn_check.setEnabled(False)

        self._worker = FunctionWorker(self._probe_all, parent=self)
        self._worker.finished_ok.connect(self._on_probed)
        self._worker.failed.connect(self._on_probe_failed)
        self._worker.start()

    @staticmethod
    def _probe_all() -> tuple[tuple[bool, str], tuple[bool, str]]:
        return ocr.probe(), rag.probe()

    def _on_probed(self, result) -> None:
        (ocr_ok, ocr_msg), (ai_ok, ai_msg) = result
        self._paint(self.ocr_state, ocr_ok, ocr_msg)
        self._paint(self.ai_state, ai_ok, ai_msg)
        self.btn_check.setEnabled(True)

    def _on_probe_failed(self, message: str) -> None:
        self._paint(self.ocr_state, False, message)
        self._paint(self.ai_state, False, message)
        self.btn_check.setEnabled(True)

    @staticmethod
    def _paint(label: QLabel, healthy: bool, message: str) -> None:
        mark = "✓" if healthy else "✗"
        colour = theme.T["success"] if healthy else theme.T["danger"]
        label.setText(f"{mark}  {message}")
        label.setStyleSheet(f"color:{colour};")
