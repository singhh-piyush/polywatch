"""Desktop notifications through notify-send (libnotify)."""
from __future__ import annotations

import html
import logging
import shutil
import subprocess
import threading
import webbrowser
from collections.abc import Callable
from typing import Any

log = logging.getLogger(__name__)


def _background(fn: Callable[[], None]) -> None:
    threading.Thread(target=fn, daemon=True).start()


class Notifier:
    def __init__(self, enabled: bool = True, *, runner: Callable[..., Any] = subprocess.run,
                 opener: Callable[[str], Any] = webbrowser.open, spawn: Callable[[Callable[[], None]], None] | None = None,
                 available: bool | None = None) -> None:
        self.enabled = enabled
        self._runner = runner
        self._opener = opener
        self._spawn = spawn or _background
        self._available = shutil.which("notify-send") is not None if available is None else available

    def notify(self, title: str, body: str, url: str) -> None:
        if self.enabled and self._available:
            self._spawn(lambda: self._show(title, body, url))

    def _show(self, title: str, body: str, url: str) -> None:
        try:
            # --action waits until the notification is dismissed and prints the chosen action.
            result = self._runner(
                ["notify-send", "--app-name=polywatch", "--action=open=Open market", title, html.escape(body)],
                capture_output=True, text=True, timeout=900,
            )
            if (result.stdout or "").strip() == "open":
                self._opener(url)
        except Exception:
            log.debug("desktop notification failed", exc_info=True)
