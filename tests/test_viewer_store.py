from __future__ import annotations

from pathlib import Path

from fixtures.make_fixture_run import make_fixture_run

from viewer.store import RunStore


def test_list_runs_empty_when_missing(tmp_path: Path):
    assert RunStore(tmp_path / "nope").list_runs() == []


def test_list_and_load(tmp_path: Path):
    make_fixture_run(tmp_path, "20260626-000001-aaaa")
    make_fixture_run(tmp_path, "20260626-000002-bbbb")
    store = RunStore(tmp_path)
    runs = store.list_runs()
    assert [r.run_id for r in runs] == ["20260626-000002-bbbb", "20260626-000001-aaaa"]
    assert runs[0].status == "done"
    assert runs[0].battles_won == 1
    assert runs[0].frame_count == 4
    assert runs[0].thumbnail == "000010.png"


def test_load_events_skips_malformed(tmp_path: Path):
    run_dir = make_fixture_run(tmp_path, "r")
    with open(run_dir / "events.jsonl", "a", encoding="utf-8") as fh:
        fh.write("NOT JSON\n")
    events = RunStore(tmp_path).load_events("r")
    assert len(events) == 5  # malformed line skipped
    assert RunStore(tmp_path).frame_names("r")[0] == "000010.png"


def test_live_status_without_summary(tmp_path: Path):
    run_dir = make_fixture_run(tmp_path, "r")
    (run_dir / "summary.json").unlink()
    assert RunStore(tmp_path).list_runs()[0].status == "live"


def test_run_summary_to_dict(tmp_path: Path):
    make_fixture_run(tmp_path, "r")
    summary = RunStore(tmp_path).list_runs()[0]
    d = summary.to_dict()
    assert d["run_id"] == "r"
    assert d["status"] == "done"
    assert d["battles_won"] == 1


def test_get_summary_with_invalid_json(tmp_path: Path):
    run_dir = tmp_path / "r"
    run_dir.mkdir(parents=True)
    (run_dir / "summary.json").write_text("INVALID JSON")
    assert RunStore(tmp_path).get_summary("r") == {}


def test_frame_names_missing_frames_dir(tmp_path: Path):
    run_dir = tmp_path / "r"
    run_dir.mkdir(parents=True)
    assert RunStore(tmp_path).frame_names("r") == []


def test_load_events_missing_events_file(tmp_path: Path):
    run_dir = tmp_path / "r"
    run_dir.mkdir(parents=True)
    assert RunStore(tmp_path).load_events("r") == []


def test_load_events_with_empty_lines(tmp_path: Path):
    run_dir = tmp_path / "r"
    run_dir.mkdir(parents=True)
    (run_dir / "events.jsonl").write_text('{"a": 1}\n\n{"b": 2}\n  \n{"c": 3}')
    events = RunStore(tmp_path).load_events("r")
    assert len(events) == 3
    assert events[0] == {"a": 1}
