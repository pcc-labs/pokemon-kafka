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


def test_start_writes_label_meta(tmp_path: Path):
    rec = RunRecorder("runlabel", tmp_path, frame_grabber=None, frame_interval=10)
    rec.start({"strategy": "low", "label": "Beat 1 — flail"})
    meta = json.loads((tmp_path / "runlabel" / "meta.json").read_text())
    assert meta == {"label": "Beat 1 — flail"}


def test_start_without_label_writes_no_meta(tmp_path: Path):
    rec = RunRecorder("runnolabel", tmp_path, frame_grabber=None, frame_interval=10)
    rec.start({"strategy": "low"})
    assert not (tmp_path / "runnolabel" / "meta.json").exists()


def test_frame_captured_on_interval_only(tmp_path: Path):
    rec = RunRecorder("run2", tmp_path, frame_grabber=_grabber, frame_interval=10)
    rec.start({})
    rec.on_event({"event_type": "overworld", "turn": 5, "data": {}})  # no frame
    rec.on_event({"event_type": "overworld", "turn": 10, "data": {}})  # frame
    rec.finish({})
    frames = sorted((tmp_path / "run2" / "frames").glob("*.png"))
    assert [f.name for f in frames] == ["000010.png"]


def test_force_capture_types_bypass_interval(tmp_path: Path):
    rec = RunRecorder("run2d", tmp_path, frame_grabber=_grabber, frame_interval=10)
    rec.start({})
    rec.on_event({"event_type": "discovery", "turn": 8, "data": {}})
    rec.on_event({"event_type": "milestone", "turn": 9, "data": {}})
    rec.on_event({"event_type": "battle", "turn": 11, "data": {}})
    rec.finish({})
    frames = sorted((tmp_path / "run2d" / "frames").glob("*.png"))
    assert [f.name for f in frames] == ["000008.png", "000009.png", "000011.png"]


def test_non_force_capture_type_off_interval_produces_no_frame(tmp_path: Path):
    rec = RunRecorder("run2e", tmp_path, frame_grabber=_grabber, frame_interval=10)
    rec.start({})
    rec.on_event({"event_type": "overworld", "turn": 7, "data": {}})
    rec.finish({})
    assert list((tmp_path / "run2e" / "frames").glob("*.png")) == []


def test_tick_captures_on_interval_without_event(tmp_path: Path):
    rec = RunRecorder("run2b", tmp_path, frame_grabber=_grabber, frame_interval=10)
    rec.start({})
    rec.tick(5)  # no frame, not on interval
    rec.tick(10)  # frame, no event involved
    rec.finish({})
    frames = sorted((tmp_path / "run2b" / "frames").glob("*.png"))
    assert [f.name for f in frames] == ["000010.png"]


def test_tick_with_no_grabber_is_noop(tmp_path: Path):
    rec = RunRecorder("run2c", tmp_path, frame_grabber=None, frame_interval=1)
    rec.start({})
    rec.tick(1)
    rec.finish({})
    assert list((tmp_path / "run2c" / "frames").glob("*.png")) == []


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

    # Assert event message shape and content
    sent_event = next(m for m in sent if m["type"] == "event")
    assert sent_event["event_type"] == "milestone"
    assert sent_event["turn"] == 10
    assert sent_event["data"] == {"description": "x"}

    # Assert frame message shape and content
    sent_frame = next(m for m in sent if m["type"] == "frame")
    assert sent_frame["turn"] == 10
    assert "png_b64" in sent_frame and isinstance(sent_frame["png_b64"], str) and sent_frame["png_b64"]


def test_finish_emits_done_to_live_before_close(tmp_path: Path):
    """finish() must call live({"type": "done"}) before closing the events file."""
    sent = []
    rec = RunRecorder("rd", tmp_path, live=sent.append)
    rec.start({})
    rec.finish({"battles_won": 1})
    done_msgs = [m for m in sent if m.get("type") == "done"]
    assert len(done_msgs) == 1, "finish() must emit exactly one done message to live"
    # summary.json must exist (finish completed successfully after emitting done)
    assert (tmp_path / "rd" / "summary.json").exists()


def test_finish_no_live_skips_done(tmp_path: Path):
    """finish() without a live callback must not crash and must still write summary.json."""
    rec = RunRecorder("rn", tmp_path, live=None)
    rec.start({})
    rec.finish({"battles_won": 0})
    assert (tmp_path / "rn" / "summary.json").exists()
