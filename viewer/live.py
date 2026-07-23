"""In-memory async pub/sub for live run streaming."""

from __future__ import annotations

import asyncio
from collections import defaultdict


class LiveHub:
    def __init__(self) -> None:
        self._subs: dict[str, list[asyncio.Queue]] = defaultdict(list)

    def subscribe(self, run_id: str) -> asyncio.Queue:
        q: asyncio.Queue = asyncio.Queue()
        self._subs[run_id].append(q)
        return q

    def unsubscribe(self, run_id: str, q: asyncio.Queue) -> None:
        if q in self._subs.get(run_id, []):
            self._subs[run_id].remove(q)

    async def publish(self, run_id: str, message: dict) -> None:
        for q in list(self._subs.get(run_id, [])):
            await q.put(message)

    async def broadcast(self, message: dict) -> None:
        """Publish to every subscriber of every run (alerts aren't tied to one run)."""
        for run_id in list(self._subs):
            await self.publish(run_id, message)
