# tests/test_publisher.py
"""Tests for telemetry publisher."""

import json
import sys
from pathlib import Path

import pytest

SCRIPTS_PATH = Path(__file__).resolve().parent.parent / "scripts"
WRITER_PATH = Path(__file__).resolve().parent.parent / "docker" / "telemetry-consumer"


@pytest.fixture(autouse=True)
def _scripts_env():
    """Add scripts and telemetry-consumer dirs to sys.path."""
    sys.path.insert(0, str(SCRIPTS_PATH))
    sys.path.insert(0, str(WRITER_PATH))
    yield
    sys.path.remove(str(SCRIPTS_PATH))
    sys.path.remove(str(WRITER_PATH))
    for mod in ("publisher", "jsonl_writer"):
        sys.modules.pop(mod, None)


def test_jsonl_publisher_writes_event(tmp_path):
    """JSONLPublisher writes a fitness event as JSONL."""
    from publisher import JSONLPublisher

    pub = JSONLPublisher(str(tmp_path))
    event = {
        "type": "fitness",
        "key": "root-abc123",
        "node": {"fitness": {"turns": 100, "badges": 0}},
    }
    pub.publish(event)
    pub.close()

    files = list(tmp_path.glob("*.jsonl"))
    assert len(files) == 1
    line = json.loads(files[0].read_text().strip())
    assert line["type"] == "fitness"
    assert line["key"] == "root-abc123"


def test_jsonl_publisher_adds_timestamp(tmp_path):
    """Publisher adds occurred_at timestamp if not present."""
    from publisher import JSONLPublisher

    pub = JSONLPublisher(str(tmp_path))
    pub.publish({"type": "fitness", "key": "k1"})
    pub.close()

    line = json.loads(list(tmp_path.glob("*.jsonl"))[0].read_text().strip())
    assert "occurred_at" in line


def test_noop_publisher_does_nothing():
    """NoopPublisher accepts events without error."""
    from publisher import NoopPublisher

    pub = NoopPublisher()
    pub.publish({"type": "fitness"})
    pub.close()  # should not raise


def test_make_publisher_returns_jsonl_when_dir_set(tmp_path):
    """make_publisher returns JSONLPublisher when telemetry_dir is set."""
    from publisher import JSONLPublisher, make_publisher

    pub = make_publisher(telemetry_dir=str(tmp_path))
    assert isinstance(pub, JSONLPublisher)
    pub.close()


def test_make_publisher_returns_noop_when_no_dir():
    """make_publisher returns NoopPublisher when telemetry_dir is None."""
    from publisher import NoopPublisher, make_publisher

    pub = make_publisher(telemetry_dir=None)
    assert isinstance(pub, NoopPublisher)


def test_jsonl_publisher_writes_game_events(tmp_path):
    """JSONLPublisher writes game events to a separate directory."""
    from game_events import build_battle_event
    from publisher import JSONLPublisher

    game_dir = tmp_path / "game"
    game_dir.mkdir()
    pub = JSONLPublisher(str(game_dir))

    event = build_battle_event(
        turn=1,
        player_hp=45,
        player_max_hp=50,
        enemy_hp=12,
        enemy_max_hp=35,
        action={"action": "fight", "move_index": 0},
    )
    pub.publish(event)
    pub.close()

    files = list(game_dir.glob("*.jsonl"))
    assert len(files) == 1
    line = json.loads(files[0].read_text().strip())
    assert line["schema"] == "pokemon.game.v1"
    assert line["event_type"] == "battle"
    assert line["data"]["player_hp"] == 45
