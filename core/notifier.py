"""
Court Session Notifier (مفكرة الجلسات)

A daemon thread that polls the database for upcoming sessions and deadlines and
raises a native Windows notification for each one, once.

The notifier deliberately knows nothing about Qt. It reports through an optional
`on_event` callback, so the UI can marshal to the Qt thread itself while the
core stays reusable from a script or a test.

Notification backends, in order of preference:
  1. winotify  — most reliable native toast on Windows 10/11
  2. plyer     — cross-platform, requested baseline
  3. callback  — in-app banner, so a reminder is never silently lost
"""

from __future__ import annotations

import threading
from datetime import datetime
from typing import Callable

from core import config, database

EventCallback = Callable[[str, str], None]  # (title, message)


class _Backend:
    """Resolves the best available notification mechanism once, at startup."""

    def __init__(self) -> None:
        self.kind = "callback"
        self._notify = None

        try:
            from winotify import Notification, audio

            def _winotify(title: str, message: str) -> None:
                toast = Notification(
                    app_id="Lawyer Agent",
                    title=title,
                    msg=message,
                    duration="long",
                )
                toast.set_audio(audio.Default, loop=False)
                toast.show()

            self._notify = _winotify
            self.kind = "winotify"
            return
        except Exception:
            pass

        try:
            from plyer import notification

            def _plyer(title: str, message: str) -> None:
                notification.notify(
                    title=title,
                    message=message,
                    app_name="Lawyer Agent",
                    timeout=15,
                )

            self._notify = _plyer
            self.kind = "plyer"
            return
        except Exception:
            pass

    def send(self, title: str, message: str) -> None:
        if self._notify is None:
            return
        try:
            self._notify(title, message)
        except Exception:
            # A failed toast must never kill the notifier thread.
            pass


class SessionNotifier(threading.Thread):
    """Polls for due sessions and notifies once per session."""

    def __init__(self, on_event: EventCallback | None = None) -> None:
        super().__init__(name="SessionNotifier", daemon=True)
        self._stop = threading.Event()
        self._on_event = on_event
        self._backend = _Backend()
        self._first_pass = True

    # -- lifecycle ---------------------------------------------------------

    def stop(self) -> None:
        self._stop.set()

    def run(self) -> None:
        while not self._stop.is_set():
            try:
                self._tick()
            except Exception:
                # Never let a transient DB error end the reminder service.
                pass
            # First pass runs immediately; afterwards settle into the interval.
            self._first_pass = False
            self._stop.wait(config.NOTIFY_POLL_SECONDS)

    # -- work --------------------------------------------------------------

    def _tick(self) -> None:
        rows = database.due_reminders(config.NOTIFY_LOOKAHEAD_DAYS)
        for row in rows:
            self._notify_session(row)
            database.mark_reminded(row["id"])

    def _notify_session(self, row) -> None:
        when = _humanise(row["session_date"])
        kind = "جلسة" if row["kind"] == "session" else "موعد نهائي"
        # جلسة / موعد نهائي
        title = f"{kind}: {row['client_name']}"
        parts = [when]
        if row["court"]:
            parts.append(row["court"])
        if row["title"]:
            parts.append(row["title"])
        message = " — ".join(parts)

        self._backend.send(title, message)
        if self._on_event:
            self._on_event(title, message)


def _humanise(iso: str) -> str:
    """'2026-09-24 09:30' -> 'غدًا 09:30' / 'اليوم 09:30' / the full date."""
    try:
        dt = datetime.fromisoformat(iso)
    except ValueError:
        return iso

    today = datetime.now().date()
    delta = (dt.date() - today).days
    time_part = dt.strftime("%H:%M")

    if delta == 0:
        return f"اليوم {time_part}"          # اليوم
    if delta == 1:
        return f"غدًا {time_part}"                # غدًا
    if delta < 0:
        return f"متأخر — {dt.strftime('%Y-%m-%d')}"
    return f"بعد {delta} يوم — {dt.strftime('%Y-%m-%d')}"


def notify_now(title: str, message: str) -> None:
    """Fire a one-off toast (used for mobile uploads and errors)."""
    _Backend().send(title, message)
