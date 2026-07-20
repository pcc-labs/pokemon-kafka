# tests/test_game_event_bridge.py
"""Tests for the JSONL → Kafka game-event bridge."""

import json
import sys
import types
from pathlib import Path

import pytest

BRIDGE_DIR = Path(__file__).resolve().parent.parent / "docker" / "game-event-bridge"


@pytest.fixture()
def bridge(monkeypatch):
    """Import bridge.py with confluent_kafka stubbed (same pattern as test_alerts_consumer)."""
    kafka_mod = types.ModuleType("confluent_kafka")

    class _Producer:
        def __init__(self, conf):
            self.conf = conf
            self.produced = []

        def produce(self, topic, key=None, value=None):
            self.produced.append((topic, key, value))

        def poll(self, timeout):
            return 0

        def flush(self, timeout=None):
            return 0

    kafka_mod.Producer = _Producer
    monkeypatch.setitem(sys.modules, "confluent_kafka", kafka_mod)
    monkeypatch.syspath_prepend(str(BRIDGE_DIR))
    sys.modules.pop("bridge", None)
    import bridge as mod

    yield mod
    sys.modules.pop("bridge", None)


def _write(path: Path, text: str):
    path.write_text(text, encoding="utf-8")


def test_scan_returns_sorted_jsonl_only(bridge, tmp_path):
    (tmp_path / "2026-07-19.jsonl").write_text("")
    (tmp_path / "2026-07-18.jsonl").write_text("")
    (tmp_path / "notes.txt").write_text("")
    names = [p.name for p in bridge.scan(tmp_path)]
    assert names == ["2026-07-18.jsonl", "2026-07-19.jsonl"]


def test_read_new_lines_complete_only(bridge, tmp_path):
    f = tmp_path / "a.jsonl"
    _write(f, '{"n":1}\n{"n":2}\n{"n":3')  # third line incomplete
    lines, offset = bridge.read_new_lines(f, 0)
    assert lines == ['{"n":1}', '{"n":2}']
    # Offset stops after the last newline; the partial line is re-read later.
    assert offset == len('{"n":1}\n{"n":2}\n')
    # Writer finishes the third line -> it is picked up from the saved offset.
    _write(f, '{"n":1}\n{"n":2}\n{"n":3}\n')
    lines2, offset2 = bridge.read_new_lines(f, offset)
    assert lines2 == ['{"n":3}']
    assert offset2 == f.stat().st_size


def test_read_new_lines_skips_blank_lines(bridge, tmp_path):
    f = tmp_path / "a.jsonl"
    _write(f, '{"n":1}\n\n{"n":2}\n')
    lines, _ = bridge.read_new_lines(f, 0)
    assert lines == ['{"n":1}', '{"n":2}']


def test_state_round_trip(bridge, tmp_path):
    state_file = tmp_path / "state" / "offsets.json"
    bridge.save_state(state_file, {"a.jsonl": 42})
    assert bridge.load_state(state_file) == {"a.jsonl": 42}


def test_load_state_missing_or_corrupt_returns_empty(bridge, tmp_path):
    assert bridge.load_state(tmp_path / "nope.json") == {}
    bad = tmp_path / "bad.json"
    bad.write_text("{not json")
    assert bridge.load_state(bad) == {}


def test_initial_state_from_beginning_is_empty(bridge, tmp_path):
    (tmp_path / "a.jsonl").write_text('{"n":1}\n')
    assert bridge.initial_state(tmp_path, from_beginning=True) == {}


def test_initial_state_at_eof_when_not_from_beginning(bridge, tmp_path):
    f = tmp_path / "a.jsonl"
    f.write_text('{"n":1}\n')
    state = bridge.initial_state(tmp_path, from_beginning=False)
    assert state == {"a.jsonl": f.stat().st_size}


def _event(schema="pokemon.game.v1", **kw):
    return json.dumps({"schema": schema, **kw})


def test_run_once_produces_new_lines_with_schema_key(bridge, tmp_path):
    f = tmp_path / "2026-07-19.jsonl"
    _write(f, _event(turn=1) + "\n" + _event(turn=2) + "\n")
    producer = bridge.Producer({})
    state = bridge.run_once(producer, "agent.game.events", tmp_path, {})
    assert len(producer.produced) == 2
    topic, key, value = producer.produced[0]
    assert topic == "agent.game.events"
    assert key == b"pokemon.game.v1"
    assert json.loads(value)["turn"] == 1
    assert state == {f.name: f.stat().st_size}
    # Second pass with the returned state produces nothing new.
    state2 = bridge.run_once(producer, "agent.game.events", tmp_path, state)
    assert len(producer.produced) == 2
    assert state2 == state


def test_run_once_picks_up_rotated_file(bridge, tmp_path):
    old = tmp_path / "2026-07-18.jsonl"
    _write(old, _event(turn=1) + "\n")
    producer = bridge.Producer({})
    state = bridge.run_once(producer, "t", tmp_path, {})
    new = tmp_path / "2026-07-19.jsonl"
    _write(new, _event(turn=2) + "\n")
    state = bridge.run_once(producer, "t", tmp_path, state)
    assert [json.loads(v)["turn"] for _, _, v in producer.produced] == [1, 2]
    assert set(state) == {"2026-07-18.jsonl", "2026-07-19.jsonl"}


def test_run_once_skips_malformed_lines(bridge, tmp_path, capsys):
    f = tmp_path / "a.jsonl"
    _write(f, _event(turn=1) + "\n" + "{torn line\n" + _event(turn=2) + "\n")
    producer = bridge.Producer({})
    bridge.run_once(producer, "t", tmp_path, {})
    assert [json.loads(v)["turn"] for _, _, v in producer.produced] == [1, 2]
    assert "skipping unparseable line" in capsys.readouterr().out


def test_run_once_missing_schema_uses_empty_key(bridge, tmp_path):
    f = tmp_path / "a.jsonl"
    _write(f, '{"turn":1}\n')
    producer = bridge.Producer({})
    bridge.run_once(producer, "t", tmp_path, {})
    assert producer.produced[0][1] == b""
