"""Best-effort WebSocket producer: streams run messages to the viewer."""

from __future__ import annotations

import json

try:
    from websockets.sync.client import connect
except Exception:  # pragma: no cover - optional dep guard
    connect = None


class LiveProducer:
    def __init__(self, url: str, run_id: str) -> None:
        self.url = url
        self.run_id = run_id
        self._ws = None

    def _ensure(self) -> None:
        if self._ws is None and connect is not None:
            self._ws = connect(self.url)

    def send(self, message: dict) -> None:
        try:
            self._ensure()
            if self._ws is not None:
                self._ws.send(json.dumps(message))
        except Exception:
            self._ws = None  # reset; never block gameplay
