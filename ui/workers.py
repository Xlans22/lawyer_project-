"""
Reusable background worker.

OCR, embedding, and LLM calls all take seconds to minutes. Running any of them
on the Qt main thread freezes the window, so every slow operation goes through
this QThread wrapper and reports back via signals.
"""

from __future__ import annotations

import traceback
from typing import Any, Callable

from PyQt6.QtCore import QThread, pyqtSignal


class FunctionWorker(QThread):
    """Runs `fn(*args, **kwargs)` off the UI thread.

    Signals
    -------
    finished_ok(object) : the return value
    failed(str)         : a formatted error message
    """

    finished_ok = pyqtSignal(object)
    failed = pyqtSignal(str)

    def __init__(
        self,
        fn: Callable[..., Any],
        *args: Any,
        parent=None,
        **kwargs: Any,
    ) -> None:
        super().__init__(parent)
        self._fn = fn
        self._args = args
        self._kwargs = kwargs

    def run(self) -> None:  # noqa: D102 - QThread entry point
        try:
            result = self._fn(*self._args, **self._kwargs)
        except Exception as exc:
            detail = traceback.format_exc(limit=3)
            self.failed.emit(f"{exc}\n\n{detail}")
        else:
            self.finished_ok.emit(result)
