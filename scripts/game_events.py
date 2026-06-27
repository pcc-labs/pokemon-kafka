# scripts/game_events.py
"""Game event schema and builders for Kafka publishing.

Each builder returns a dict ready for JSON serialization and Kafka publishing.
All events share a common envelope: schema, event_type, turn, occurred_at, data.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone

SCHEMA_GAME_EVENT = "pokemon.game.v1"


def _envelope(event_type: str, turn: int, data: dict) -> dict:
    return {
        "schema": SCHEMA_GAME_EVENT,
        "event_type": event_type,
        "turn": turn,
        "occurred_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ"),
        "data": data,
    }


def build_battle_event(
    turn: int,
    player_hp: int,
    player_max_hp: int,
    enemy_hp: int,
    enemy_max_hp: int,
    action: dict,
) -> dict:
    return _envelope(
        "battle",
        turn,
        {
            "player_hp": player_hp,
            "player_max_hp": player_max_hp,
            "enemy_hp": enemy_hp,
            "enemy_max_hp": enemy_max_hp,
            # Serialize to string — action dicts have variable shape (fight/item/run)
            # and Flink reads this as a STRING column.
            "action": json.dumps(action),
        },
    )


def build_overworld_event(
    turn: int,
    map_id: int,
    x: int,
    y: int,
    badges: int,
    party_count: int,
    action: str,
    stuck_turns: int,
    waypoint_info: str | None = None,
) -> dict:
    data: dict = {
        "map_id": map_id,
        "position": {"x": x, "y": y},
        "badges": badges,
        "party_count": party_count,
        "action": action,
        "stuck_turns": stuck_turns,
    }
    if waypoint_info:
        data["waypoint_info"] = waypoint_info
    return _envelope("overworld", turn, data)


def build_map_change_event(turn: int, prev_map: int, new_map: int, x: int, y: int) -> dict:
    return _envelope(
        "map_change",
        turn,
        {
            "prev_map": prev_map,
            "new_map": new_map,
            "position": {"x": x, "y": y},
        },
    )


def build_stuck_event(turn: int, map_id: int, x: int, y: int, last_action: str, streak: int) -> dict:
    return _envelope(
        "stuck",
        turn,
        {
            "map_id": map_id,
            "position": {"x": x, "y": y},
            "last_action": last_action,
            "streak": streak,
        },
    )


def build_milestone_event(turn: int, description: str) -> dict:
    return _envelope("milestone", turn, {"description": description})


def build_session_event(
    turn: int,
    phase: str,
    battles_won: int | None = None,
    maps_visited: int | None = None,
) -> dict:
    data: dict = {"phase": phase}
    if battles_won is not None:
        data["battles_won"] = battles_won
    if maps_visited is not None:
        data["maps_visited"] = maps_visited
    return _envelope("session", turn, data)


class GameEventCollector:
    """Collects structured game events during an agent session.

    Provides typed emit methods so agent.py call sites stay concise.
    Events accumulate in ``self.events``.  When an optional *publisher* is
    provided, each event is also published in real-time so data reaches
    Confluent Cloud immediately instead of being batched after the run.
    """

    def __init__(self, publisher=None, recorder=None):
        self.events: list[dict] = []
        self._publisher = publisher
        self._recorder = recorder

    def _emit(self, event: dict) -> None:
        """Append *event* to the local list, publish, and record if configured."""
        self.events.append(event)
        if self._publisher is not None:
            try:
                self._publisher.publish(event)
            except Exception as exc:
                print(f"[game_events] publish error: {exc}")
        if self._recorder is not None:
            try:
                self._recorder.on_event(event)
            except Exception as exc:
                print(f"[game_events] recorder error: {exc}")

    def battle(self, turn: int, player_hp: int, player_max_hp: int, enemy_hp: int, enemy_max_hp: int, action: dict):
        self._emit(build_battle_event(turn, player_hp, player_max_hp, enemy_hp, enemy_max_hp, action))

    def overworld(
        self,
        turn: int,
        map_id: int,
        x: int,
        y: int,
        badges: int,
        party_count: int,
        action: str,
        stuck_turns: int,
        waypoint_info: str | None = None,
    ):
        self._emit(build_overworld_event(turn, map_id, x, y, badges, party_count, action, stuck_turns, waypoint_info))

    def map_change(self, turn: int, prev_map: int, new_map: int, x: int, y: int):
        self._emit(build_map_change_event(turn, prev_map, new_map, x, y))

    def stuck(self, turn: int, map_id: int, x: int, y: int, last_action: str, streak: int):
        self._emit(build_stuck_event(turn, map_id, x, y, last_action, streak))

    def milestone(self, turn: int, description: str):
        self._emit(build_milestone_event(turn, description))

    def session(self, turn: int, phase: str, battles_won: int | None = None, maps_visited: int | None = None):
        self._emit(build_session_event(turn, phase, battles_won, maps_visited))
