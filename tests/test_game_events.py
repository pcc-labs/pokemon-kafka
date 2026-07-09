# tests/test_game_events.py
"""Tests for game event schema and publisher."""

import json

from game_events import (
    SCHEMA_GAME_EVENT,
    GameEventCollector,
    build_battle_end_event,
    build_battle_event,
    build_discovery_event,
    build_map_change_event,
    build_milestone_event,
    build_overworld_event,
    build_session_event,
    build_stuck_event,
)


def test_build_battle_event_has_required_fields():
    event = build_battle_event(
        turn=42,
        player_hp=45,
        player_max_hp=50,
        enemy_hp=12,
        enemy_max_hp=35,
        action={"action": "fight", "move_index": 0},
    )
    assert event["schema"] == SCHEMA_GAME_EVENT
    assert event["event_type"] == "battle"
    assert event["turn"] == 42
    data = event["data"]
    assert data["player_hp"] == 45
    assert data["enemy_hp"] == 12
    assert data["action"] == '{"action": "fight", "move_index": 0}'
    assert "occurred_at" in event


def test_build_battle_event_omits_context_when_unset():
    """Backward-compat: enrichment fields appear only when supplied."""
    event = build_battle_event(1, 10, 10, 10, 10, {"action": "fight"})
    for key in ("battle_type", "map_id", "enemy_species", "enemy_level", "player_species"):
        assert key not in event["data"]


def test_build_battle_event_includes_context_when_set():
    event = build_battle_event(
        1,
        10,
        10,
        10,
        10,
        {"action": "fight"},
        battle_type=2,
        map_id=54,
        enemy_species="Onix",
        enemy_level=14,
        player_species="Squirtle",
        player_level=14,
    )
    data = event["data"]
    assert data["battle_type"] == 2
    assert data["map_id"] == 54
    assert data["enemy_species"] == "Onix"
    assert data["enemy_level"] == 14
    assert data["player_species"] == "Squirtle"


def test_build_battle_end_event():
    party = [{"species": "Squirtle", "level": 14, "hp": 40, "max_hp": 44}]
    event = build_battle_end_event(
        turn=120,
        won=True,
        battle_turns=8,
        battle_type=2,
        map_id=54,
        opponent_species="Geodude",
        opponent_level=12,
        party=party,
    )
    assert event["event_type"] == "battle_end"
    data = event["data"]
    assert data["won"] is True
    assert data["battle_turns"] == 8
    assert data["battle_type"] == 2
    assert data["opponent_species"] == "Geodude"
    assert data["party"] == party


def test_collector_battle_end_emits_and_accumulates():
    collector = GameEventCollector()
    collector.battle_end(120, True, 8, 2, 54, "Onix", 14, [])
    assert len(collector.events) == 1
    assert collector.events[0]["event_type"] == "battle_end"


def test_build_overworld_event():
    event = build_overworld_event(
        turn=100,
        map_id=1,
        x=5,
        y=8,
        badges=0,
        party_count=1,
        action="down",
        stuck_turns=0,
    )
    assert event["event_type"] == "overworld"
    assert event["data"]["map_id"] == 1
    assert event["data"]["position"] == {"x": 5, "y": 8}


def test_build_map_change_event():
    event = build_map_change_event(turn=50, prev_map=0, new_map=1, x=3, y=10)
    assert event["event_type"] == "map_change"
    assert event["data"]["prev_map"] == 0
    assert event["data"]["new_map"] == 1


def test_build_stuck_event():
    event = build_stuck_event(turn=75, map_id=0, x=8, y=4, last_action="up", streak=5)
    assert event["event_type"] == "stuck"
    assert event["data"]["streak"] == 5


def test_build_milestone_event():
    event = build_milestone_event(turn=200, description="Reached Viridian City!")
    assert event["event_type"] == "milestone"
    assert event["data"]["description"] == "Reached Viridian City!"


def test_build_session_event_start():
    event = build_session_event(turn=0, phase="start")
    assert event["event_type"] == "session"
    assert event["data"]["phase"] == "start"


def test_build_session_event_end():
    event = build_session_event(turn=1000, phase="end", battles_won=5, maps_visited=3)
    assert event["data"]["battles_won"] == 5


def test_events_are_json_serializable():
    """All event builders produce JSON-serializable dicts."""
    events = [
        build_battle_event(1, 10, 20, 5, 15, {"action": "fight", "move_index": 0}),
        build_overworld_event(2, 0, 5, 8, 0, 1, "down", 0),
        build_map_change_event(3, 0, 1, 3, 10),
        build_stuck_event(4, 0, 8, 4, "up", 5),
        build_milestone_event(5, "test"),
        build_session_event(6, "start"),
    ]
    for event in events:
        json.dumps(event)  # should not raise


def test_collector_battle():
    c = GameEventCollector()
    c.battle(1, 45, 50, 12, 35, {"action": "fight", "move_index": 0})
    assert len(c.events) == 1
    assert c.events[0]["event_type"] == "battle"


def test_collector_overworld():
    c = GameEventCollector()
    c.overworld(10, 0, 5, 8, 0, 1, "down", 0)
    assert c.events[0]["event_type"] == "overworld"


def test_collector_map_change():
    c = GameEventCollector()
    c.map_change(50, 0, 1, 3, 10)
    assert c.events[0]["event_type"] == "map_change"


def test_build_discovery_event():
    event = build_discovery_event(turn=12, map_id=51, x=3, y=4, text="TRAINER TIPS")
    assert event["event_type"] == "discovery"
    assert event["data"]["map_id"] == 51
    assert event["data"]["position"] == {"x": 3, "y": 4}
    assert event["data"]["text"] == "TRAINER TIPS"
    assert event["data"]["kind"] == "dialogue"  # default


def test_collector_discovery():
    c = GameEventCollector()
    c.discovery(12, 51, 3, 4, "TRAINER TIPS", kind="sign")
    assert c.events[0]["event_type"] == "discovery"
    assert c.events[0]["data"]["kind"] == "sign"
    assert c.events[0]["data"]["text"] == "TRAINER TIPS"


def test_collector_stuck():
    c = GameEventCollector()
    c.stuck(75, 0, 8, 4, "up", 5)
    assert c.events[0]["event_type"] == "stuck"


def test_collector_milestone():
    c = GameEventCollector()
    c.milestone(200, "Reached Viridian City!")
    assert c.events[0]["event_type"] == "milestone"


def test_collector_session():
    c = GameEventCollector()
    c.session(0, "start")
    c.session(1000, "end", battles_won=5, maps_visited=3)
    assert len(c.events) == 2
    assert c.events[1]["data"]["battles_won"] == 5


def test_collector_accumulates_mixed_events():
    """Collector accumulates events of different types in order."""
    c = GameEventCollector()
    c.session(0, "start")
    c.overworld(10, 0, 5, 8, 0, 1, "down", 0)
    c.battle(20, 45, 50, 12, 35, {"action": "fight", "move_index": 0})
    c.map_change(30, 0, 1, 3, 10)
    c.stuck(40, 1, 3, 10, "up", 5)
    c.milestone(50, "test")
    c.session(60, "end", battles_won=1, maps_visited=2)
    assert len(c.events) == 7
    types = [e["event_type"] for e in c.events]
    assert types == ["session", "overworld", "battle", "map_change", "stuck", "milestone", "session"]


def test_collector_tees_events_to_recorder():
    seen = []

    class Rec:
        def on_event(self, event):
            seen.append(event)

    c = GameEventCollector(recorder=Rec())
    c.milestone(3, "picked starter")
    assert seen and seen[0]["event_type"] == "milestone"
    assert seen[0]["turn"] == 3


def test_collector_recorder_error_is_swallowed(capsys):
    class Rec:
        def on_event(self, event):
            raise RuntimeError("boom")

    c = GameEventCollector(recorder=Rec())
    c.milestone(1, "x")  # must not raise
    assert "recorder error" in capsys.readouterr().out


def test_collector_tick_forwards_to_recorder():
    seen = []

    class Rec:
        def tick(self, turn):
            seen.append(turn)

    c = GameEventCollector(recorder=Rec())
    c.tick(30)
    assert seen == [30]


def test_collector_tick_noop_without_recorder():
    c = GameEventCollector()
    c.tick(30)  # must not raise


def test_collector_tick_recorder_error_is_swallowed(capsys):
    class Rec:
        def tick(self, turn):
            raise RuntimeError("boom")

    c = GameEventCollector(recorder=Rec())
    c.tick(1)  # must not raise
    assert "recorder error" in capsys.readouterr().out


def test_collector_battle_frame_forwards_to_recorder():
    seen = []

    class Rec:
        def battle_frame(self, turn):
            seen.append(turn)

    c = GameEventCollector(recorder=Rec())
    c.battle_frame(7)
    assert seen == [7]


def test_collector_battle_frame_noop_without_recorder():
    c = GameEventCollector()
    c.battle_frame(7)  # must not raise


def test_collector_battle_frame_error_is_swallowed(capsys):
    class Rec:
        def battle_frame(self, turn):
            raise RuntimeError("boom")

    c = GameEventCollector(recorder=Rec())
    c.battle_frame(1)  # must not raise
    assert "recorder error" in capsys.readouterr().out


def test_collector_stamps_game_on_events():
    collector = GameEventCollector(game="yellow")
    collector.milestone(7, "Reached Viridian City!")
    assert collector.events[-1]["game"] == "yellow"


def test_collector_defaults_to_red_blue():
    collector = GameEventCollector()
    collector.session(0, "start")
    assert collector.events[-1]["game"] == "red_blue"
