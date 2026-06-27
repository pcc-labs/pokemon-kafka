from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from PIL import Image
from recorder import RunRecorder  # noqa: E402


def _grabber():
    return Image.new("RGB", (160, 144), (10, 20, 30))


def test_new_run_id_is_sortable_and_tagged():
    now = datetime(2026, 6, 26, 15, 18, 7, tzinfo=timezone.utc)
    rid = RunRecorder.new_run_id(now, "a1b2")
    assert rid == "20260626-151807-a1b2"


def test_start_creates_layout_and_on_event_appends(tmp_path: Path):
    rec = RunRecorder("run1", tmp_path, frame_grabber=_grabber, frame_interval=10)
    rec.start({"strategy": "low"})
    rec.on_event({"event_type": "milestone", "turn": 1, "data": {"description": "hi"}})
    rec.finish({"battles_won": 0})

    run_dir = tmp_path / "run1"
    assert (run_dir / "frames").is_dir()
    lines = (run_dir / "events.jsonl").read_text().splitlines()
    assert json.loads(lines[0])["data"]["description"] == "hi"
    summary = json.loads((run_dir / "summary.json").read_text())
    assert summary["battles_won"] == 0
    assert summary["params"] == {"strategy": "low"}


def test_frame_captured_on_interval_only(tmp_path: Path):
    rec = RunRecorder("run2", tmp_path, frame_grabber=_grabber, frame_interval=10)
    rec.start({})
    rec.on_event({"event_type": "overworld", "turn": 5, "data": {}})  # no frame
    rec.on_event({"event_type": "overworld", "turn": 10, "data": {}})  # frame
    rec.finish({})
    frames = sorted((tmp_path / "run2" / "frames").glob("*.png"))
    assert [f.name for f in frames] == ["000010.png"]


def test_no_grabber_means_no_frames(tmp_path: Path):
    rec = RunRecorder("run3", tmp_path, frame_grabber=None, frame_interval=1)
    rec.start({})
    rec.on_event({"event_type": "overworld", "turn": 1, "data": {}})
    rec.finish({})
    assert list((tmp_path / "run3" / "frames").glob("*.png")) == []


def test_capture_frame_with_no_grabber(tmp_path: Path):
    rec = RunRecorder("run4", tmp_path, frame_grabber=None, frame_interval=1)
    rec.start({})
    # Explicitly call capture_frame with no grabber - should be a no-op
    rec.capture_frame(1)
    rec.finish({})
    assert list((tmp_path / "run4" / "frames").glob("*.png")) == []


def test_recorder_forwards_to_live(tmp_path: Path):
    sent = []
    rec = RunRecorder("rl", tmp_path, frame_grabber=_grabber, frame_interval=10, live=sent.append)
    rec.start({})
    rec.on_event({"event_type": "milestone", "turn": 10, "data": {"description": "x"}})
    rec.finish({})
    types = [m["type"] for m in sent]
    assert "event" in types and "frame" in types
