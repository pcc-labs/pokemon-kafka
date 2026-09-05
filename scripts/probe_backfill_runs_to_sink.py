"""Backfill recorder rows into the telemetry sink.

Until 2026-09-05 the expedition rig's collector had no publisher: a supervisor-driven fight left
its pokemon.game.v1 rows only in runs/<run_id>/events.jsonl, so the game-event-bridge (which
tails data/telemetry/game/*.jsonl into Kafka) and Flink never saw them. The rig now publishes
to the sink as well; this one-off appends the rows of the runs recorded before that, in the
publisher's compact shape, skipping any event_id the sink already carries (idempotent).

    uv run python scripts/probe_backfill_runs_to_sink.py [YYYYMMDD prefix, default today]
"""

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
RUNS = ROOT / "runs"
SINK_DIR = ROOT / "data" / "telemetry" / "game"


def main() -> int:
    prefix = sys.argv[1] if len(sys.argv) > 1 else datetime.now(timezone.utc).strftime("%Y%m%d")
    sink = SINK_DIR / f"{datetime.now(timezone.utc):%Y-%m-%d}.jsonl"
    seen: set[str] = set()
    if sink.exists():
        with open(sink) as fh:
            for line in fh:
                if '"event_id":"' in line:
                    seen.add(line.split('"event_id":"', 1)[1].split('"', 1)[0])
    added = 0
    per_run: dict[str, int] = {}
    with open(sink, "a") as out:
        for run_dir in sorted(RUNS.glob(f"{prefix}-*")):
            events = run_dir / "events.jsonl"
            if not events.exists():
                continue
            n = 0
            with open(events) as fh:
                for line in fh:
                    try:
                        ev = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    eid = ev.get("event_id")
                    if not eid or eid in seen:
                        continue
                    seen.add(eid)
                    out.write(json.dumps(ev, separators=(",", ":")) + "\n")
                    n += 1
            if n:
                per_run[run_dir.name] = n
                added += n
    for run_id, n in per_run.items():
        print(f"{run_id} +{n}")
    print(f"backfilled {added} rows from {len(per_run)} runs into {sink}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
