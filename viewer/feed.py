"""Merge game events, observations, and anomalies into one tagged feed."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from pathlib import Path

_MILESTONE_TYPES = {"milestone", "map_change"}
_TELEMETRY_TYPES = {"battle", "overworld", "stuck"}


@dataclass
class FeedEntry:
    ts: str
    turn: int
    kind: str
    text: str
    data: dict

    def to_dict(self) -> dict:
        return asdict(self)


def _event_text(event: dict) -> str:
    et = event.get("event_type")
    data = event.get("data", {})
    if et == "milestone":
        return data.get("description", "milestone")
    if et == "map_change":
        return f"Map {data.get('prev_map')} → {data.get('new_map')}"
    if et == "battle":
        return f"Battle — player HP {data.get('player_hp')}, enemy HP {data.get('enemy_hp')}"
    if et == "overworld":
        pos = data.get("position", {})
        return f"map {data.get('map_id')} ({pos.get('x')},{pos.get('y')}) {data.get('action', '')}".strip()
    if et == "stuck":
        return f"Stuck ×{data.get('streak')} at {data.get('position', {})}"
    return et or "event"


def build_feed(events, observations=None, anomalies=None) -> list[FeedEntry]:
    entries: list[FeedEntry] = []
    for ev in events:
        et = ev.get("event_type")
        if et in _MILESTONE_TYPES:
            kind = "milestone"
        elif et in _TELEMETRY_TYPES:
            kind = "telemetry"
        else:
            continue
        entries.append(
            FeedEntry(
                ts=ev.get("occurred_at", ""),
                turn=int(ev.get("turn", 0)),
                kind=kind,
                text=_event_text(ev),
                data=ev.get("data", {}),
            )
        )
    for obs in observations or []:
        entries.append(FeedEntry(ts="", turn=0, kind="observation", text=obs, data={}))
    for an in anomalies or []:
        entries.append(
            FeedEntry(
                ts="",
                turn=int(an.get("turn", 0)),
                kind="anomaly",
                text=f"{an.get('alert_type', 'ANOMALY')}: {an.get('detail', '')}",
                data=an,
            )
        )
    return sorted(entries, key=lambda e: e.turn)


def load_observations(path: Path) -> list[str]:
    path = Path(path)
    if not path.exists():
        return []
    out = []
    for line in path.read_text().splitlines():
        s = line.strip()
        if not s or s.startswith("#"):
            continue
        out.append(s)
    return out


def load_anomalies(path: Path) -> list[dict]:
    path = Path(path)
    if not path.exists():
        return []
    out = []
    for line in path.read_text().splitlines():
        s = line.strip()
        if not s:
            continue
        try:
            out.append(json.loads(s))
        except json.JSONDecodeError:
            continue
    return out
