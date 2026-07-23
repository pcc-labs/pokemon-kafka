"""Tests for viewer.alerts_tail — streaming alerts.jsonl into the LiveHub."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

from viewer.alerts_tail import read_new_alerts, tail_alerts
from viewer.live import LiveHub


class TestReadNewAlerts:
    def test_missing_file_returns_empty_and_zero_offset(self, tmp_path: Path):
        alerts, offset = read_new_alerts(tmp_path / "alerts.jsonl", 0)
        assert alerts == []
        assert offset == 0

    def test_reads_lines_from_offset(self, tmp_path: Path):
        path = tmp_path / "alerts.jsonl"
        path.write_text(json.dumps({"alert_type": "A"}) + "\n")
        first, offset = read_new_alerts(path, 0)
        assert [a["alert_type"] for a in first] == ["A"]

        with path.open("a") as fh:
            fh.write(json.dumps({"alert_type": "B"}) + "\n")
        second, offset2 = read_new_alerts(path, offset)
        assert [a["alert_type"] for a in second] == ["B"]
        assert offset2 > offset

    def test_skips_bad_json_lines(self, tmp_path: Path):
        path = tmp_path / "alerts.jsonl"
        path.write_text('not json\n\n{"alert_type": "OK"}\n')
        alerts, _ = read_new_alerts(path, 0)
        assert [a["alert_type"] for a in alerts] == ["OK"]

    def test_truncated_file_resets_offset(self, tmp_path: Path):
        path = tmp_path / "alerts.jsonl"
        path.write_text(json.dumps({"alert_type": "A"}) * 3 + "\n")
        _, offset = read_new_alerts(path, 0)
        path.write_text(json.dumps({"alert_type": "B"}) + "\n")  # shorter than before
        alerts, _ = read_new_alerts(path, offset)
        assert [a["alert_type"] for a in alerts] == ["B"]


class TestLiveHubBroadcast:
    def test_broadcast_reaches_all_runs(self):
        async def run():
            hub = LiveHub()
            q1 = hub.subscribe("run-a")
            q2 = hub.subscribe("run-b")
            await hub.broadcast({"type": "anomaly", "alert_type": "X"})
            return q1.get_nowait(), q2.get_nowait()

        m1, m2 = asyncio.run(run())
        assert m1["alert_type"] == "X"
        assert m2["alert_type"] == "X"


class TestTailAlerts:
    def test_streams_only_new_lines_as_anomalies(self, tmp_path: Path):
        """Lines appended after the tail starts are published; pre-existing ones are not
        (the REST feed already serves those)."""
        path = tmp_path / "alerts.jsonl"
        path.write_text(json.dumps({"alert_type": "OLD"}) + "\n")

        async def run():
            hub = LiveHub()
            q = hub.subscribe("r")
            task = asyncio.create_task(tail_alerts(path, hub, poll_interval=0.01))
            await asyncio.sleep(0.05)  # let the tail record its starting offset
            with path.open("a") as fh:
                fh.write(json.dumps({"alert_type": "NEW", "detail": "d", "turn": 7}) + "\n")
            msg = await asyncio.wait_for(q.get(), timeout=2)
            task.cancel()
            return msg, q

        msg, q = asyncio.run(run())
        assert msg["type"] == "anomaly"
        assert msg["alert_type"] == "NEW"
        assert msg["turn"] == 7
        assert q.empty()  # OLD was never published

    def test_survives_missing_file_then_created(self, tmp_path: Path):
        path = tmp_path / "alerts.jsonl"

        async def run():
            hub = LiveHub()
            q = hub.subscribe("r")
            task = asyncio.create_task(tail_alerts(path, hub, poll_interval=0.01))
            await asyncio.sleep(0.05)
            path.write_text(json.dumps({"alert_type": "FIRST"}) + "\n")
            msg = await asyncio.wait_for(q.get(), timeout=2)
            task.cancel()
            return msg

        msg = asyncio.run(run())
        assert msg["alert_type"] == "FIRST"


@pytest.mark.parametrize("poll", [0.01])
def test_tail_cancellation_is_clean(tmp_path: Path, poll: float):
    async def run():
        hub = LiveHub()
        task = asyncio.create_task(tail_alerts(tmp_path / "alerts.jsonl", hub, poll_interval=poll))
        await asyncio.sleep(0.03)
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            return True
        return False

    assert asyncio.run(run())
