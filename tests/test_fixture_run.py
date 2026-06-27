from __future__ import annotations

import json
from pathlib import Path

from fixtures.make_fixture_run import make_fixture_run


def test_fixture_run_is_complete(tmp_path: Path):
    run_dir = make_fixture_run(tmp_path, "demo")
    assert (run_dir / "summary.json").exists()
    frames = list((run_dir / "frames").glob("*.png"))
    assert len(frames) >= 3
    events = (run_dir / "events.jsonl").read_text().splitlines()
    kinds = {json.loads(line)["event_type"] for line in events}
    assert {"milestone", "overworld", "battle"} <= kinds
