"""Tests for alerts-consumer — observational-memory integration."""

import sys
import types
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

# The consumer imports confluent_kafka which isn't installed in the test env.
# Provide a stub module so the import succeeds.
CONSUMER_PATH = Path(__file__).resolve().parent.parent / "docker" / "alerts-consumer"


@pytest.fixture(autouse=True)
def _consumer_env():
    """Add consumer dir to sys.path and stub confluent_kafka."""
    kafka_mod = types.ModuleType("confluent_kafka")
    kafka_mod.Consumer = MagicMock
    kafka_mod.KafkaError = type("KafkaError", (), {"_PARTITION_EOF": -191})
    sys.modules["confluent_kafka"] = kafka_mod

    sys.path.insert(0, str(CONSUMER_PATH))
    yield
    sys.path.remove(str(CONSUMER_PATH))

    for name in ("consumer", "confluent_kafka"):
        sys.modules.pop(name, None)


def _import_consumer(memory_dir=None):
    """Import the consumer module with MEMORY_DIR set."""
    env_patch = {"MEMORY_DIR": memory_dir} if memory_dir else {"MEMORY_DIR": ""}
    sys.modules.pop("consumer", None)
    with patch.dict("os.environ", env_patch, clear=False):
        import importlib

        import consumer

        importlib.reload(consumer)
        return consumer


class TestFormatAlert:
    def test_formats_alert(self):
        consumer = _import_consumer()
        data = {
            "alert_type": "STUCK_LOOP",
            "root_hash": "abcdef123456789",
            "detail": "Agent stuck for 50 turns",
            "window_start": "2026-03-09T10:00:00Z",
            "window_end": "2026-03-09T10:05:00Z",
            "event_count": 5,
        }
        result = consumer.format_alert(data)
        assert "STUCK_LOOP" in result
        assert "abcdef12" in result
        assert "Agent stuck" in result


class TestAlertObservation:
    def test_shapes_full_alert(self):
        consumer = _import_consumer()
        obs = consumer.alert_observation(
            {
                "alert_type": "BATTLE_LOOP",
                "detail": "enemy_hp=12 player_hp=9",
                "event_count": 20,
                "window_end": "2026-06-26T10:05:00Z",
            }
        )
        assert obs["priority"] == "important"
        assert obs["source_session"] == "flink"
        assert obs["referenced_time"] == "2026-06-26T10:05:00Z"
        assert obs["content"] == "Flink alert [BATTLE_LOOP]: enemy_hp=12 player_hp=9 (count=20)"

    def test_falls_back_to_window_start_and_omits_zero_count(self):
        consumer = _import_consumer()
        obs = consumer.alert_observation(
            {"alert_type": "NO_PROGRESS", "detail": "", "window_start": "2026-06-26T09:00:00Z"}
        )
        assert obs["referenced_time"] == "2026-06-26T09:00:00Z"
        # empty detail trimmed, no count suffix
        assert obs["content"] == "Flink alert [NO_PROGRESS]:"


class TestMemoryIntegration:
    def test_alert_written_as_observation(self, tmp_path):
        consumer = _import_consumer(str(tmp_path))
        from memory_writer import append_observations

        data = {
            "alert_type": "POSITION_DEADLOCK",
            "detail": "map=12 pos=(5,31)",
            "event_count": 50,
            "window_end": "2026-06-26T10:00:00Z",
        }
        n = append_observations(str(tmp_path), [consumer.alert_observation(data)], dedupe=True)

        assert n == 1
        content = (tmp_path / "observations.md").read_text()
        assert "## 2026-06-26" in content
        assert "- [important] Flink alert [POSITION_DEADLOCK]: map=12 pos=(5,31) (count=50) (session: flink)" in content

    def test_no_memory_dir_configured(self):
        consumer = _import_consumer(None)
        assert not consumer.MEMORY_DIR


class TestAppendAlertLine:
    def test_appends_raw_alert_json(self, tmp_path):
        """Alerts land in alerts.jsonl so the viewer can merge and live-stream them."""
        import json

        consumer = _import_consumer(str(tmp_path))
        data = {"alert_type": "STUCK_LOOP", "detail": "map=51", "event_count": 3}
        consumer.append_alert_line(str(tmp_path), data)
        consumer.append_alert_line(str(tmp_path), {"alert_type": "NO_PROGRESS"})

        lines = (tmp_path / "alerts.jsonl").read_text().splitlines()
        assert len(lines) == 2
        assert json.loads(lines[0]) == data
        assert json.loads(lines[1])["alert_type"] == "NO_PROGRESS"
