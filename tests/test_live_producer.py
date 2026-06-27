"""Tests for scripts/live_producer.py — covers all branches for 100% line coverage."""

from __future__ import annotations

import json
from unittest.mock import MagicMock

import live_producer
from live_producer import LiveProducer


def test_send_is_best_effort_when_unreachable():
    # No server running on port 1; must not raise.
    LiveProducer("ws://127.0.0.1:1/ws/produce/r", "r").send({"type": "event", "turn": 1})


def test_send_success_path(monkeypatch):
    """When connect succeeds, send() calls ws.send with JSON-encoded message."""
    fake_ws = MagicMock()
    monkeypatch.setattr(live_producer, "connect", lambda url: fake_ws)
    p = LiveProducer("ws://127.0.0.1:1/ws/produce/r", "r")
    p.send({"type": "event", "turn": 1})
    fake_ws.send.assert_called_once()
    sent_data = fake_ws.send.call_args[0][0]
    assert json.loads(sent_data) == {"type": "event", "turn": 1}


def test_send_is_noop_when_connect_is_none(monkeypatch):
    """When websockets is not available (connect=None), send() is a no-op."""
    monkeypatch.setattr(live_producer, "connect", None)
    p = LiveProducer("ws://127.0.0.1:1/ws/produce/r", "r")
    p.send({"type": "event"})  # must not raise
    assert p._ws is None
