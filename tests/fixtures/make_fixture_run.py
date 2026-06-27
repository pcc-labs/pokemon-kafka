"""Build a synthetic agent run for tests and ROM-free demos."""

from __future__ import annotations

import json
from pathlib import Path

from PIL import Image

_COLORS = [(40, 80, 40), (60, 110, 60), (90, 150, 90)]


def make_fixture_run(runs_dir: Path, run_id: str = "demo") -> Path:
    run_dir = Path(runs_dir) / run_id
    frames_dir = run_dir / "frames"
    frames_dir.mkdir(parents=True, exist_ok=True)

    events = [
        {
            "schema": "pokemon.game.v1",
            "event_type": "session",
            "turn": 0,
            "occurred_at": "2026-06-26T22:00:00.000Z",
            "data": {"phase": "start"},
        },
        {
            "schema": "pokemon.game.v1",
            "event_type": "milestone",
            "turn": 10,
            "occurred_at": "2026-06-26T22:00:01.000Z",
            "data": {"description": "Picked starter"},
        },
        {
            "schema": "pokemon.game.v1",
            "event_type": "overworld",
            "turn": 20,
            "occurred_at": "2026-06-26T22:00:02.000Z",
            "data": {
                "map_id": 0,
                "position": {"x": 5, "y": 6},
                "badges": 0,
                "party_count": 1,
                "action": "down",
                "stuck_turns": 0,
            },
        },
        {
            "schema": "pokemon.game.v1",
            "event_type": "battle",
            "turn": 30,
            "occurred_at": "2026-06-26T22:00:03.000Z",
            "data": {
                "player_hp": 19,
                "player_max_hp": 19,
                "enemy_hp": 0,
                "enemy_max_hp": 18,
                "action": '{"move": "tackle"}',
            },
        },
        {
            "schema": "pokemon.game.v1",
            "event_type": "stuck",
            "turn": 40,
            "occurred_at": "2026-06-26T22:00:04.000Z",
            "data": {"map_id": 0, "position": {"x": 5, "y": 6}, "last_action": "down", "streak": 6},
        },
    ]
    with open(run_dir / "events.jsonl", "w", encoding="utf-8") as fh:
        for ev in events:
            fh.write(json.dumps(ev) + "\n")

    for i, turn in enumerate((10, 20, 30, 40)):
        Image.new("RGB", (160, 144), _COLORS[i % len(_COLORS)]).save(frames_dir / f"{turn:06d}.png")

    (run_dir / "summary.json").write_text(
        json.dumps(
            {
                "run_id": run_id,
                "battles_won": 1,
                "maps_visited": 2,
                "badges": 0,
                "final_map_id": 0,
                "turns": 40,
                "params": {"strategy": "low"},
            },
            indent=2,
        )
    )
    return run_dir
