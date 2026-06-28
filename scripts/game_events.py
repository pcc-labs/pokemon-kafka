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
    *,
    battle_type: int | None = None,
    map_id: int | None = None,
    enemy_species: str | None = None,
    enemy_level: int | None = None,
    player_species: str | None = None,
    player_level: int | None = None,
) -> dict:
    data: dict = {
        "player_hp": player_hp,
        "player_max_hp": player_max_hp,
        "enemy_hp": enemy_hp,
        "enemy_max_hp": enemy_max_hp,
        # Serialize to string — action dicts have variable shape (fight/item/run)
        # and Flink reads this as a STRING column.
        "action": json.dumps(action),
    }
    # Additive battle context: only present when supplied so the fixed Flink ROW
    # payload and existing positional callers stay unchanged.
    if battle_type is not None:
        data["battle_type"] = battle_type
    if map_id is not None:
        data["map_id"] = map_id
    if enemy_species is not None:
        data["enemy_species"] = enemy_species
    if enemy_level is not None:
        data["enemy_level"] = enemy_level
    if player_species is not None:
        data["player_species"] = player_species
    if player_level is not None:
        data["player_level"] = player_level
    return _envelope("battle", turn, data)


def build_battle_end_event(
    turn: int,
    won: bool,
    battle_turns: int,
    battle_type: int,
    map_id: int,
    opponent_species: str,
    opponent_level: int,
    party: list[dict],
) -> dict:
    """Summary of a finished battle.

    ``party`` is a list of ``{species, level, hp, max_hp}`` for the post-battle party.
    ``battle_turns`` is the number of in-battle turns the fight lasted.
    """
    return _envelope(
        "battle_end",
        turn,
        {
            "won": won,
            "battle_turns": battle_turns,
            "battle_type": battle_type,
            "map_id": map_id,
            "opponent_species": opponent_species,
            "opponent_level": opponent_level,
            "party": party,
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


def build_discovery_event(
    turn: int, map_id: int, x: int, y: int, text: str, kind: str = "dialogue"
) -> dict:
    return _envelope(
        "discovery",
        turn,
        {
            "map_id": map_id,
            "position": {"x": x, "y": y},
            "kind": kind,
            "text": text,
        },
    )


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

    def battle(
        self,
        turn: int,
        player_hp: int,
        player_max_hp: int,
        enemy_hp: int,
        enemy_max_hp: int,
        action: dict,
        *,
        battle_type: int | None = None,
        map_id: int | None = None,
        enemy_species: str | None = None,
        enemy_level: int | None = None,
        player_species: str | None = None,
        player_level: int | None = None,
    ):
        self._emit(
            build_battle_event(
                turn,
                player_hp,
                player_max_hp,
                enemy_hp,
                enemy_max_hp,
                action,
                battle_type=battle_type,
                map_id=map_id,
                enemy_species=enemy_species,
                enemy_level=enemy_level,
                player_species=player_species,
                player_level=player_level,
            )
        )

    def battle_end(
        self,
        turn: int,
        won: bool,
        battle_turns: int,
        battle_type: int,
        map_id: int,
        opponent_species: str,
        opponent_level: int,
        party: list[dict],
    ):
        self._emit(
            build_battle_end_event(
                turn, won, battle_turns, battle_type, map_id, opponent_species, opponent_level, party
            )
        )

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

    def discovery(self, turn: int, map_id: int, x: int, y: int, text: str, kind: str = "dialogue"):
        self._emit(build_discovery_event(turn, map_id, x, y, text, kind))

    def map_change(self, turn: int, prev_map: int, new_map: int, x: int, y: int):
        self._emit(build_map_change_event(turn, prev_map, new_map, x, y))

    def stuck(self, turn: int, map_id: int, x: int, y: int, last_action: str, streak: int):
        self._emit(build_stuck_event(turn, map_id, x, y, last_action, streak))

    def milestone(self, turn: int, description: str):
        self._emit(build_milestone_event(turn, description))

    def session(self, turn: int, phase: str, battles_won: int | None = None, maps_visited: int | None = None):
        self._emit(build_session_event(turn, phase, battles_won, maps_visited))
