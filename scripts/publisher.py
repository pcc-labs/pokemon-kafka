# scripts/publisher.py
"""Telemetry publisher — local-first event publishing.

Local-first complement to the tapes Kafka publisher. The tapes proxy
already ships a Kafka-backed Publisher for the broker path; this module
provides a zero-infrastructure alternative that writes directly to
date-partitioned JSONL files on disk. Same event shape, no broker needed.

The local JSONL path lets us iterate on the learning loop (agent →
telemetry → Historical Observer → evolution) without any broker, with
the docker-compose Kafka + Flink stack as the streaming story.

Two implementations:
- JSONLPublisher: writes events to date-partitioned JSONL files
- NoopPublisher: discards events (for runs without telemetry)
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Protocol, runtime_checkable


@runtime_checkable
class Publisher(Protocol):
    """Event publisher protocol — same shape as the tapes Kafka publisher."""

    def publish(self, event: dict) -> None: ...
    def close(self) -> None: ...


class JSONLPublisher:
    """Publishes events to date-partitioned JSONL files.

    Reuses the JSONLWriter from the telemetry-consumer for file handling.
    """

    def __init__(self, telemetry_dir: str):
        import sys

        writer_dir = str(Path(__file__).resolve().parent.parent / "docker" / "telemetry-consumer")
        if writer_dir not in sys.path:
            sys.path.insert(0, writer_dir)
        from jsonl_writer import JSONLWriter

        self._writer = JSONLWriter(telemetry_dir)

    def publish(self, event: dict) -> None:
        if "occurred_at" not in event:
            event = {
                **event,
                "occurred_at": datetime.now(timezone.utc).isoformat() + "Z",
            }
        self._writer.write(event)

    def close(self) -> None:
        self._writer.close()


class NoopPublisher:
    """Discards all events. Used when telemetry is disabled."""

    def publish(self, event: dict) -> None:
        pass

    def close(self) -> None:
        pass


def make_publisher(telemetry_dir: str | None = None) -> Publisher:
    """Factory: JSONLPublisher when telemetry_dir is set, NoopPublisher otherwise."""
    if telemetry_dir:
        return JSONLPublisher(telemetry_dir)
    return NoopPublisher()
