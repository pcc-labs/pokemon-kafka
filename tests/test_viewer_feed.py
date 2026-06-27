from __future__ import annotations

from pathlib import Path

from viewer.feed import _event_text, build_feed, load_anomalies, load_observations


def test_build_feed_tags_kinds_and_orders():
    events = [
        {"event_type": "battle", "turn": 30, "data": {"player_hp": 19, "enemy_hp": 0}},
        {"event_type": "milestone", "turn": 10, "data": {"description": "Picked starter"}},
        {"event_type": "session", "turn": 0, "data": {"phase": "start"}},
    ]
    feed = build_feed(events)
    kinds = [(e.turn, e.kind) for e in feed]
    assert kinds == [(10, "milestone"), (30, "telemetry")]  # session skipped, sorted by turn
    assert "starter" in feed[0].text


def test_build_feed_includes_observations_and_anomalies():
    feed = build_feed(
        [],
        observations=["The agent oscillated at a door for 200 turns."],
        anomalies=[{"alert_type": "GAME_STUCK_LOOP", "detail": "map=0 streak=8", "turn": 40}],
    )
    kinds = {e.kind for e in feed}
    assert kinds == {"observation", "anomaly"}


def test_loaders_missing_files_return_empty(tmp_path: Path):
    assert load_observations(tmp_path / "none.md") == []
    assert load_anomalies(tmp_path / "none.jsonl") == []


def test_load_observations_strips_headings(tmp_path: Path):
    p = tmp_path / "observations.md"
    p.write_text("# Heading\n\n- did a thing\nplain line\n")
    assert load_observations(p) == ["- did a thing", "plain line"]


def test_feed_entry_to_dict():
    entry = build_feed([{"event_type": "battle", "turn": 5, "data": {"x": 1}}])[0]
    d = entry.to_dict()
    assert d["kind"] == "telemetry"
    assert d["turn"] == 5


def test_build_feed_all_event_types():
    events = [
        {"event_type": "map_change", "turn": 1, "data": {"prev_map": 1, "new_map": 2}},
        {"event_type": "overworld", "turn": 2, "data": {"map_id": 3, "position": {"x": 10, "y": 20}, "action": "walk"}},
        {"event_type": "stuck", "turn": 3, "data": {"streak": 5, "position": {"x": 0, "y": 0}}},
    ]
    feed = build_feed(events)
    assert len(feed) == 3
    assert feed[0].kind == "milestone"
    assert "Map 1 → 2" in feed[0].text
    assert feed[1].kind == "telemetry"
    assert "3 (10,20) walk" in feed[1].text
    assert feed[2].kind == "telemetry"
    assert "Stuck ×5" in feed[2].text


def test_load_anomalies_parses_jsonl(tmp_path: Path):
    p = tmp_path / "anomalies.jsonl"
    p.write_text(
        '{"alert_type": "TYPE1", "detail": "d1", "turn": 5}\n{"alert_type": "TYPE2", "detail": "d2"}\ninvalid json\n\n'
    )
    anomalies = load_anomalies(p)
    assert len(anomalies) == 2
    assert anomalies[0]["alert_type"] == "TYPE1"
    assert anomalies[1]["alert_type"] == "TYPE2"


def test_build_feed_overworld_without_action():
    events = [
        {"event_type": "overworld", "turn": 1, "data": {"map_id": 5, "position": {"x": 1, "y": 2}}},
    ]
    feed = build_feed(events)
    assert len(feed) == 1
    assert "map 5 (1,2)" in feed[0].text


def test_event_text_fallback():
    text = _event_text({"event_type": "unknown"})
    assert text == "unknown"
    text = _event_text({})
    assert text == "event"
