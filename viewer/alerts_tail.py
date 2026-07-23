"""Tail alerts.jsonl and publish new alerts to the LiveHub as anomaly messages.

The REST feed merges the whole alerts file on page load; this tail covers the
live path, so anomalies land in the feed while a run is still streaming.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

from viewer.live import LiveHub


def read_new_alerts(path: Path, offset: int) -> tuple[list[dict], int]:
    """Return alerts appended past *offset* and the new offset.

    A file smaller than *offset* was truncated/rewritten — start over from 0.
    """
    path = Path(path)
    if not path.exists():
        return [], 0
    size = path.stat().st_size
    if size < offset:
        offset = 0
    if size == offset:
        return [], offset
    with path.open("r", encoding="utf-8") as fh:
        fh.seek(offset)
        chunk = fh.read()
        new_offset = fh.tell()
    alerts = []
    for line in chunk.splitlines():
        s = line.strip()
        if not s:
            continue
        try:
            alerts.append(json.loads(s))
        except json.JSONDecodeError:
            continue
    return alerts, new_offset


async def tail_alerts(path: Path, hub: LiveHub, poll_interval: float = 1.0) -> None:
    """Poll *path* forever, broadcasting each newly appended alert to every live run.

    Starts at the current end of file: pre-existing alerts are already served by
    the REST feed, so only alerts arriving during the session are streamed.
    """
    path = Path(path)
    offset = path.stat().st_size if path.exists() else 0
    while True:
        alerts, offset = read_new_alerts(path, offset)
        for alert in alerts:
            await hub.broadcast({"type": "anomaly", **alert})
        await asyncio.sleep(poll_interval)
