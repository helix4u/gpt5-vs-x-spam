from __future__ import annotations
import threading
import time
import logging
from typing import Callable, Optional


_SCOPES = ("scrape", "block")


class _PauseState:
    def __init__(self):
        self._log = logging.getLogger("app.pause")
        # Event semantics: set => running; clear => paused
        self._events: dict[str, threading.Event] = {s: threading.Event() for s in _SCOPES}
        for ev in self._events.values():
            ev.set()

    def pause(self, scope: str | None = None):
        scopes = _SCOPES if not scope or scope == "all" else (scope,)
        for s in scopes:
            ev = self._events.get(s)
            if ev:
                ev.clear()
                self._log.info("paused scope=%s", s)

    def resume(self, scope: str | None = None):
        scopes = _SCOPES if not scope or scope == "all" else (scope,)
        for s in scopes:
            ev = self._events.get(s)
            if ev:
                ev.set()
                self._log.info("resumed scope=%s", s)

    def is_paused(self, scope: str) -> bool:
        ev = self._events.get(scope)
        return False if ev is None else (not ev.is_set())

    def wait_if_paused(self, scope: str, on_evt: Optional[Callable[[dict], None]] = None):
        ev = self._events.get(scope)
        if not ev:
            return
        # While paused, sleep in 1s steps and emit a 'paused' evt
        while not ev.is_set():
            try:
                self._log.debug("waiting paused scope=%s", scope)
            except Exception:
                pass
            if on_evt:
                try:
                    on_evt({"kind": "paused", "scope": scope})
                except Exception:
                    pass
            time.sleep(1)


STATE = _PauseState()

pause = STATE.pause
resume = STATE.resume
is_paused = STATE.is_paused
wait_if_paused = STATE.wait_if_paused
