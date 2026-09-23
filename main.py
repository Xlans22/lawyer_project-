"""
Lawyer Agent — Phase 2 desktop application.

A fully offline workspace for Egyptian law firms: client dossiers, Arabic OCR,
local semantic search over case files, court-session reminders, and direct
photo transfer from a phone over the local network.

    python main.py

Nothing is sent to any external server. The only network activity is the local
Ollama endpoint and, when you explicitly start it, the LAN mobile bridge.
"""

from __future__ import annotations

import sys
import traceback
from pathlib import Path

# Allow `python main.py` from any working directory.
sys.path.insert(0, str(Path(__file__).resolve().parent))

from core import config  # noqa: E402


def _configure_console() -> None:
    """UTF-8 console so Arabic diagnostics never raise UnicodeEncodeError."""
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass
    if sys.platform == "win32":
        try:
            import ctypes

            ctypes.windll.kernel32.SetConsoleOutputCP(65001)
        except Exception:
            pass


def _excepthook(exc_type, exc_value, exc_tb) -> None:
    """Surface unhandled errors in a dialog rather than dying silently."""
    message = "".join(traceback.format_exception(exc_type, exc_value, exc_tb))
    print(message, file=sys.stderr)
    try:
        from PyQt6.QtWidgets import QApplication, QMessageBox

        if QApplication.instance() is not None:
            QMessageBox.critical(
                None,
                "خطأ غير متوقع",
                f"{exc_type.__name__}: {exc_value}\n\n"
                "التفاصيل الكاملة في الطرفية (console).",
            )
    except Exception:
        pass


def main() -> int:
    _configure_console()
    sys.excepthook = _excepthook

    from PyQt6.QtWidgets import QApplication

    from core import database
    from ui.main_window import MainWindow
    from ui.theme import apply_theme

    config.ensure_dirs()
    database.init_db()

    app = QApplication(sys.argv)
    app.setApplicationName("Lawyer Agent")
    app.setOrganizationName("Lawyer Agent")

    # Fusion is style-sheet friendly and consistent across platforms, so the
    # custom QSS (flat buttons, thin scrollbars, rounded combos) renders the
    # same everywhere instead of being partly overridden by the native style.
    app.setStyle("Fusion")

    apply_theme(app)

    window = MainWindow()
    window.show()

    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
