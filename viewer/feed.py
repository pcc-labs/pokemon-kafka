"""Merge game events and anomalies into one tagged feed.

Observations are in-game only: `discovery` events (signs, dialogue the agent
read). Session/dev memory like observations.md is deliberately NOT merged — the
Pokédex narrates what happened in the game, in human-readable terms.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from pathlib import Path

_MILESTONE_TYPES = {"milestone", "map_change"}
_TELEMETRY_TYPES = {"battle", "overworld", "battle_end", "battle_outcome", "move_result"}
_OBSERVATION_TYPES = {"discovery"}
_DECISION_TYPES = {"decision"}
# A stuck event means the agent wedged somewhere its route didn't predict — the
# in-run anomaly signal (Flink alerts cover the aggregated/windowed kind).
_ANOMALY_TYPES = {"stuck"}


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
    if et == "discovery":
        return data.get("text", "discovery")
    if et == "battle_end":
        outcome = "won" if data.get("won") else "lost"
        return f"Battle {outcome} vs {data.get('opponent_species')} (Lv{data.get('opponent_level')})"
    if et == "battle_outcome":
        outcome = "won" if data.get("won") else "lost"
        return f"Battle outcome: {outcome} vs {data.get('enemy_species')} (Lv{data.get('enemy_level')})"
    if et == "move_result":
        result = "enemy fainted" if data.get("fainted") else f"{data.get('damage_dealt')} dmg"
        return f"{data.get('user_species')} used {data.get('move')} — {result}"
    if et == "decision":
        buttons = "+".join(data.get("buttons") or []) or "wait"
        return f"▸ {buttons} — {data.get('reason', '')}"
    return et or "event"


def build_feed(events, anomalies=None) -> list[FeedEntry]:
    entries: list[FeedEntry] = []
    for ev in events:
        et = ev.get("event_type")
        if et in _MILESTONE_TYPES:
            kind = "milestone"
        elif et in _TELEMETRY_TYPES:
            kind = "telemetry"
        elif et in _OBSERVATION_TYPES:
            kind = "observation"
        elif et in _DECISION_TYPES:
            kind = "decision"
        elif et in _ANOMALY_TYPES:
            kind = "anomaly"
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
