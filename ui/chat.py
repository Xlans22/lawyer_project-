"""Right-hand panel — the local AI assistant, scoped to the active client.

Answers now carry **clickable source transparency**: under every AI reply the
panel lists the exact document excerpts the retrieval step fed to the model,
each headed by its filename rendered as a link. Clicking the link opens the
original PDF/image in the operating system's default viewer, so a lawyer can
verify an answer against its source in one click instead of hunting the dossier
by hand.

Link handling is deliberate: the transcript is a QTextBrowser with automatic
link opening turned OFF (``setOpenLinks(False)``). We own the ``anchorClicked``
signal and open the file ourselves via ``QDesktopServices.openUrl`` — otherwise
the browser would try to render the file *into the transcript*. A clicked path
is opened only when it resolves inside the client data tree and still exists.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

from PyQt6.QtCore import Qt, QUrl, pyqtSignal
from PyQt6.QtGui import QDesktopServices, QKeyEvent
from PyQt6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QPlainTextEdit,
    QPushButton,
    QTextBrowser,
    QVBoxLayout,
    QWidget,
)

from core import config, rag
from ui import theme
from ui.workers import FunctionWorker

# A retrieval chunk can run to ~1000 chars. Show a generous, verifiable slice
# inline but cap it so a long chunk can't flood the transcript — the clickable
# link opens the full document.
SOURCE_EXCERPT_LIMIT = 600


class ChatInput(QPlainTextEdit):
    """Enter sends; Shift+Enter inserts a newline."""

    submitted = pyqtSignal()

    def keyPressEvent(self, event: QKeyEvent) -> None:  # noqa: N802 - Qt override
        if event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            if not event.modifiers() & Qt.KeyboardModifier.ShiftModifier:
                self.submitted.emit()
                return
        super().keyPressEvent(event)


class ChatPanel(QWidget):
    status = pyqtSignal(str)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("ChatPanel")
        self.setMinimumWidth(340)
        self._client: tuple[int, str] | None = None
        self._worker: FunctionWorker | None = None
        self._busy = False

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        header = QLabel("المساعد القانوني")
        header.setObjectName("ChatHeader")
        layout.addWidget(header)

        self.scope = QLabel("اختر عميلًا لبدء المحادثة")
        self.scope.setProperty("role", "caption")
        self.scope.setWordWrap(True)
        self.scope.setContentsMargins(14, 8, 14, 0)
        layout.addWidget(self.scope)

        self.transcript = QTextBrowser()
        self.transcript.setObjectName("ChatTranscript")
        # We open source links ourselves (see _on_source_clicked); without this
        # the browser tries to load the clicked file into itself.
        self.transcript.setOpenLinks(False)
        self.transcript.setOpenExternalLinks(False)
        self.transcript.anchorClicked.connect(self._on_source_clicked)
        self.transcript.setContentsMargins(14, 8, 14, 8)
        layout.addWidget(self.transcript, 1)

        composer = QHBoxLayout()
        composer.setContentsMargins(14, 8, 14, 14)
        composer.setSpacing(8)

        self.input = ChatInput()
        self.input.setObjectName("ChatInput")
        self.input.setPlaceholderText("اكتب سؤالك هنا...")
        self.input.setFixedHeight(88)
        self.input.submitted.connect(self._send)
        composer.addWidget(self.input, 1)

        self.btn_send = QPushButton("إرسال")
        self.btn_send.setFixedHeight(44)
        self.btn_send.clicked.connect(self._send)
        composer.addWidget(self.btn_send, 0, Qt.AlignmentFlag.AlignBottom)

        layout.addLayout(composer)

        self._set_enabled(False)
        self._greet()

    # -- public API --------------------------------------------------------

    def set_client(self, client_id: int, name: str) -> None:
        self._client = (client_id, name)
        self.scope.setText(
            f"النطاق: ملف «{name}» فقط"
        )
        self._set_enabled(True)
        self.transcript.clear()
        self._system_line(
            f"المحادثة الآن "
            f"محصورة في ملف «{name}». "
            "لن تستخدم الإجابات ملفات عملاء آخرين."
        )

    def clear_scope(self) -> None:
        self._client = None
        self.scope.setText("اختر عميلًا لبدء المحادثة")
        self._set_enabled(False)

    # -- transcript helpers ------------------------------------------------

    def _greet(self) -> None:
        self._system_line(
            "مرحبًا. يعمل هذا المساعد "
            "بالكامل على هذا الجهاز "
            "بدون الاتصال بأي خادم خارجي."
        )

    def _system_line(self, text: str) -> None:
        self.transcript.append(
            f'<p style="color:{theme.T["muted"]};font-size:9.5pt;margin:6px 0;">{text}</p>'
        )

    def _user_block(self, text: str) -> None:
        safe = _html_escape(text).replace("\n", "<br>")
        self.transcript.append(
            f'<div style="margin:12px 0 4px 0;">'
            f'{theme.badge("أنت", "navy")}'
            f'<div style="margin-top:6px;color:{theme.T["ink"]};">{safe}</div></div>'
        )

    def _answer_block(self, text: str, sources: list[dict]) -> None:
        safe = _html_escape(text).replace("\n", "<br>")
        self.transcript.append(
            f'<div style="margin:4px 0 14px 0;">'
            f'{theme.badge("المساعد", "brass")}'
            f'<div style="margin-top:6px;color:{theme.T["ink"]};'
            f'background:#FFFFFF;border:1px solid {theme.T["border"]};'
            f'border-radius:8px;padding:10px;">{safe}</div>'
            f'{self._sources_html(sources)}</div>'
        )

    # -- source transparency ----------------------------------------------

    def _sources_html(self, sources: list[dict]) -> str:
        """Render the 'المصادر' (Sources) section shown beneath an answer."""
        if not sources:
            return ""
        rows = "".join(self._source_row_html(src) for src in sources)
        return (
            f'<div style="margin-top:12px;">'
            f'<div style="color:{theme.T["brass"]};font-weight:700;'
            f'font-size:10pt;margin-bottom:4px;">المصادر</div>'
            f'{rows}</div>'
        )

    def _source_row_html(self, src: dict) -> str:
        """One source card: clickable filename + category + excerpt quotes."""
        filename = src.get("filename") or "؟"
        path = src.get("path")
        category = src.get("category") or ""
        label = config.category_label(category) if category else ""

        safe_name = _html_escape(filename)
        if path:
            href = _html_escape(QUrl.fromLocalFile(path).toString())
            name_html = (
                f'<a href="{href}" style="color:{theme.T["navy"]};'
                f'font-weight:700;text-decoration:none;">📄 {safe_name}</a>'
            )
        else:
            # Not found on disk — name it, but not as a (dead) link.
            name_html = (
                f'<span style="color:{theme.T["muted"]};font-weight:700;">'
                f'📄 {safe_name}</span>'
            )
        label_html = (
            f'<span style="color:{theme.T["muted"]};font-size:8.5pt;">'
            f'&nbsp;•&nbsp;{_html_escape(label)}</span>'
            if label else ""
        )
        excerpts_html = "".join(
            f'<div style="color:{theme.T["ink"]};font-size:9pt;margin-top:5px;'
            f'padding:6px 10px;background:{theme.T["parchment"]};'
            f'border-right:3px solid {theme.T["brass_light"]};'
            f'border-radius:4px;">«{_html_escape(_condense(ex))}»</div>'
            for ex in src.get("excerpts", [])
            if ex and ex.strip()
        )
        return (
            f'<div style="margin:6px 0;padding:9px 11px;background:#FFFFFF;'
            f'border:1px solid {theme.T["border"]};border-radius:8px;">'
            f'{name_html}{label_html}{excerpts_html}</div>'
        )

    def _on_source_clicked(self, url: QUrl) -> None:
        """Open a clicked source in the OS default viewer (main-thread slot).

        The URL comes from HTML we built, but we still refuse to hand an
        arbitrary path to the OS: it must be a local file resolving inside
        ``config.CLIENTS_DIR`` and it must still exist.
        """
        if not url.isLocalFile():
            return
        path = Path(url.toLocalFile())
        if not _within_data_dir(path):
            self.status.emit("تعذّر فتح المصدر: مسار غير مسموح")
            return
        if not path.exists():
            self._system_line("تعذّر فتح الملف: لم يعد موجودًا على القرص.")
            self.status.emit("الملف غير موجود")
            return

        if QDesktopServices.openUrl(QUrl.fromLocalFile(str(path))):
            self.status.emit(f"تم فتح المصدر: {path.name}")
            return

        # Fallback if the desktop integration declines (rare).
        try:
            if sys.platform == "win32":
                os.startfile(str(path))  # type: ignore[attr-defined]
            elif sys.platform == "darwin":
                subprocess.Popen(["open", str(path)])
            else:
                subprocess.Popen(["xdg-open", str(path)])
            self.status.emit(f"تم فتح المصدر: {path.name}")
        except Exception as exc:  # noqa: BLE001 - report, never crash the UI
            self._system_line(f"تعذّر فتح الملف: {_html_escape(str(exc))}")
            self.status.emit("تعذّر فتح الملف")

    # -- sending -----------------------------------------------------------

    def _set_enabled(self, enabled: bool) -> None:
        self.input.setEnabled(enabled)
        self.btn_send.setEnabled(enabled)

    def _send(self) -> None:
        if self._busy or self._client is None:
            return
        question = self.input.toPlainText().strip()
        if not question:
            return

        client_id, _name = self._client
        self.input.clear()
        self._user_block(question)

        self._busy = True
        self._set_enabled(False)
        self.btn_send.setText("جارٍ التفكير...")
        self.status.emit("جارٍ البحث في ملف القضية...")

        self._worker = FunctionWorker(rag.ask, client_id, question, parent=self)
        self._worker.finished_ok.connect(self._on_answer)
        self._worker.failed.connect(self._on_error)
        self._worker.start()

    def _reset_composer(self) -> None:
        self._busy = False
        self.btn_send.setText("إرسال")
        if self._client is not None:
            self._set_enabled(True)

    def _on_answer(self, result) -> None:
        self._reset_composer()
        self._answer_block(result.get("answer", ""), result.get("sources", []))
        self.status.emit("تم")

    def _on_error(self, message: str) -> None:
        self._reset_composer()
        hint = ""
        if "Connection" in message or "refused" in message.lower():
            hint = (
                "<br><br>تأكد من أن Ollama "
                "يعمل: <code>ollama serve</code>"
            )
        self.transcript.append(
            f'<div style="margin:8px 0;color:{theme.T["danger"]};">'
            f'{theme.badge("خطأ", "danger")} '
            f'<span style="font-size:9.5pt;">{_html_escape(message.splitlines()[0])}</span>'
            f'{hint}</div>'
        )
        self.status.emit("خطأ في المساعد")


def _condense(text: str, limit: int = SOURCE_EXCERPT_LIMIT) -> str:
    """Collapse whitespace and cap length for a tidy inline excerpt."""
    collapsed = " ".join((text or "").split())
    if len(collapsed) > limit:
        collapsed = collapsed[:limit].rstrip() + "…"
    return collapsed


def _within_data_dir(path: Path) -> bool:
    """True only if ``path`` resolves inside the client data tree.

    Defence in depth: source links are built from our own database, but we
    still never hand an arbitrary path to the OS 'open' handler.
    """
    try:
        child = os.path.normcase(str(path.resolve()))
        base = os.path.normcase(str(config.CLIENTS_DIR.resolve()))
    except OSError:
        return False
    return child == base or child.startswith(base + os.sep)


def _html_escape(text: str) -> str:
    return (
        text.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )
