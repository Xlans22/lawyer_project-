"""Centre panel — the client's dossier: category tree, files, and actions."""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import (
    QComboBox,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from core import config, database, ocr, rag, storage
from ui.workers import FunctionWorker


class DocumentPanel(QWidget):
    """Shows one client's files and drives import / OCR / indexing."""

    status = pyqtSignal(str)
    documents_changed = pyqtSignal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("DocPanel")
        self._client: tuple[int, str] | None = None
        self._worker: FunctionWorker | None = None

        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(12)

        # -- header ---------------------------------------------------------
        self.title = QLabel("لم يتم اختيار عميل")
        self.title.setProperty("role", "heading")
        layout.addWidget(self.title)

        self.subtitle = QLabel("")
        self.subtitle.setProperty("role", "caption")
        layout.addWidget(self.subtitle)

        # -- actions --------------------------------------------------------
        actions = QHBoxLayout()
        actions.setSpacing(8)

        self.btn_import = QPushButton("إضافة مستندات")
        self.btn_import.setToolTip("Import PDFs or images into the dossier")
        self.btn_import.clicked.connect(self._import)

        self.category = QComboBox()
        for key in config.CATEGORIES:
            self.category.addItem(config.category_label(key), key)
        self.category.setFixedWidth(170)

        self.btn_open = QPushButton("فتح")
        self.btn_open.setProperty("variant", "ghost")
        self.btn_open.clicked.connect(self._open_file)

        self.btn_reindex = QPushButton("إعادة الفهرسة")
        self.btn_reindex.setProperty("variant", "ghost")
        self.btn_reindex.clicked.connect(self._reindex)

        self.btn_delete = QPushButton("حذف")
        self.btn_delete.setProperty("variant", "ghost")
        self.btn_delete.clicked.connect(self._delete_file)

        actions.addWidget(self.btn_import)
        actions.addWidget(self.category)
        actions.addStretch(1)
        actions.addWidget(self.btn_open)
        actions.addWidget(self.btn_reindex)
        actions.addWidget(self.btn_delete)
        layout.addLayout(actions)

        # -- tree -----------------------------------------------------------
        self.tree = QTreeWidget()
        self.tree.setHeaderLabels(
            ["المستند", "المصدر", "الفهرسة"]
        )
        self.tree.setColumnWidth(0, 280)
        self.tree.itemDoubleClicked.connect(lambda *_: self._open_file())
        layout.addWidget(self.tree, 1)

        # -- progress -------------------------------------------------------
        self.progress = QProgressBar()
        self.progress.setRange(0, 0)          # indeterminate busy bar
        self.progress.setVisible(False)
        self.progress.setFixedHeight(6)
        self.progress.setTextVisible(False)
        layout.addWidget(self.progress)

        self._set_enabled(False)

    # -- public API --------------------------------------------------------

    def set_client(self, client_id: int, name: str) -> None:
        self._client = (client_id, name)
        self.title.setText(name)
        record = database.get_client(client_id)
        case_no = (record["case_number"] if record else "") or "—"
        self.subtitle.setText(
            f"رقم القضية: {case_no}"
            f"   •   مجلد الملف: {storage.client_folder_name(client_id, name)}"
        )
        self._set_enabled(True)
        self.reload()

    def clear(self) -> None:
        self._client = None
        self.tree.clear()
        self.title.setText("لم يتم اختيار عميل")
        self.subtitle.setText("")
        self._set_enabled(False)

    def reload(self) -> None:
        if self._client is None:
            return
        client_id, name = self._client
        indexed = {
            row["filename"]: bool(row["ocr_done"])
            for row in database.list_documents(client_id)
        }

        self.tree.clear()
        tree = storage.dossier_tree(client_id, name)
        for category, files in tree.items():
            parent = QTreeWidgetItem(
                [f"{config.category_label(category)}  ({len(files)})", "", ""]
            )
            parent.setFirstColumnSpanned(False)
            parent.setExpanded(True)
            font = parent.font(0)
            font.setBold(True)
            parent.setFont(0, font)
            for filename in files:
                state = "✓" if indexed.get(filename) else "—"
                child = QTreeWidgetItem([filename, category, state])
                child.setData(0, Qt.ItemDataRole.UserRole, filename)
                child.setData(0, Qt.ItemDataRole.UserRole + 1, category)
                parent.addChild(child)
            self.tree.addTopLevelItem(parent)

    # -- helpers -----------------------------------------------------------

    def _set_enabled(self, enabled: bool) -> None:
        for widget in (
            self.btn_import, self.btn_open, self.btn_reindex,
            self.btn_delete, self.category, self.tree,
        ):
            widget.setEnabled(enabled)

    def _selected_file(self) -> tuple[str, str] | None:
        item = self.tree.currentItem()
        if item is None or item.parent() is None:
            return None
        return item.data(0, Qt.ItemDataRole.UserRole), item.data(0, Qt.ItemDataRole.UserRole + 1)

    def _busy(self, active: bool, message: str = "") -> None:
        self.progress.setVisible(active)
        if message:
            self.status.emit(message)

    # -- actions -----------------------------------------------------------

    def _import(self) -> None:
        if self._client is None:
            return
        patterns = " ".join(f"*{ext}" for ext in sorted(config.SUPPORTED_ALL))
        paths, _ = QFileDialog.getOpenFileNames(
            self,
            "اختر المستندات",
            "",
            f"مستندات ({patterns});;كل الملفات (*.*)",
        )
        if not paths:
            return

        client_id, name = self._client
        category = self.category.currentData()

        # Copy files into the dossier synchronously (fast), then OCR in the
        # background (slow).
        copied: list[Path] = []
        for raw in paths:
            src = Path(raw)
            dest = storage.unique_path(
                storage.category_dir(client_id, name, category), src.name
            )
            shutil.copy2(src, dest)
            database.add_document(
                client_id, dest.name, str(dest), category=category, source="import"
            )
            copied.append(dest)

        self.reload()
        self.documents_changed.emit()
        self._index_files(copied)

    def _index_files(self, paths: list[Path]) -> None:
        if self._client is None or not paths:
            return
        client_id, _ = self._client
        category = self.category.currentData()

        def work() -> list[tuple[str, int, str]]:
            results = []
            for path in paths:
                try:
                    text = ocr.extract_text(path)
                    chunks = rag.index_text(
                        client_id, text, source=path.name, category=category
                    )
                    results.append((path.name, chunks, ""))
                except Exception as exc:
                    results.append((path.name, 0, str(exc)))
            return results

        self._busy(True, "جارٍ التعرف على النص وفهرسته...")
        self._worker = FunctionWorker(work, parent=self)
        self._worker.finished_ok.connect(self._on_indexed)
        self._worker.failed.connect(self._on_index_failed)
        self._worker.start()

    def _on_indexed(self, results) -> None:
        self._busy(False)
        if self._client is None:
            return
        client_id, _ = self._client

        ok, failed = 0, []
        for filename, chunks, error in results:
            if error:
                failed.append(f"{filename}: {error}")
                continue
            ok += 1
            for row in database.list_documents(client_id):
                if row["filename"] == filename:
                    database.mark_document_ocr(row["id"])
                    break

        self.reload()
        self.documents_changed.emit()
        self.status.emit(f"تمت فهرسة {ok} ملف")

        if failed:
            QMessageBox.warning(
                self,
                "تمت الفهرسة جزئيًا",
                "تعذرت معالجة:\n\n" + "\n".join(failed[:6]),
            )

    def _on_index_failed(self, message: str) -> None:
        self._busy(False)
        QMessageBox.critical(
            self,
            "خطأ في الفهرسة",
            message,
        )

    def _open_file(self) -> None:
        selected = self._selected_file()
        if selected is None or self._client is None:
            return
        filename, category = selected
        client_id, name = self._client
        path = storage.category_dir(client_id, name, category) / filename
        if not path.exists():
            QMessageBox.warning(self, "غير موجود", "The file is no longer on disk.")
            return
        _open_with_os(path)

    def _reindex(self) -> None:
        selected = self._selected_file()
        if selected is None or self._client is None:
            QMessageBox.information(
                self, "اختر مستندًا",
                "Select a document in the tree first.",
            )
            return
        filename, category = selected
        client_id, name = self._client
        path = storage.category_dir(client_id, name, category) / filename
        if path.exists():
            self._index_files([path])

    def _delete_file(self) -> None:
        selected = self._selected_file()
        if selected is None or self._client is None:
            return
        filename, _category = selected
        client_id, _name = self._client

        confirm = QMessageBox.question(
            self, "تأكيد الحذف",
            f"هل تريد حذف «{filename}» من الملف ومن القرص؟",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if confirm != QMessageBox.StandardButton.Yes:
            return

        for row in database.list_documents(client_id):
            if row["filename"] == filename:
                Path(row["rel_path"]).unlink(missing_ok=True)
                database.delete_document(row["id"])
                break

        # Note: vectors are keyed by source filename; re-indexing rebuilds them.
        self.reload()
        self.documents_changed.emit()


def _open_with_os(path: Path) -> None:
    """Open a file in the OS default application."""
    try:
        if sys.platform == "win32":
            os.startfile(str(path))  # type: ignore[attr-defined]
        elif sys.platform == "darwin":
            subprocess.Popen(["open", str(path)])
        else:
            subprocess.Popen(["xdg-open", str(path)])
    except Exception as exc:
        QMessageBox.warning(None, "خطأ", f"Could not open the file: {exc}")
