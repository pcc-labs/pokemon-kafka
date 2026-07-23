from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from fixtures.make_fixture_run import make_fixture_run
from starlette.websockets import WebSocketDisconnect

from viewer.live import LiveHub
from viewer.server import create_app


def _client(tmp_path: Path) -> TestClient:
    make_fixture_run(tmp_path, "20260626-000001-aaaa")
    return TestClient(create_app(tmp_path))


def test_list_runs(tmp_path: Path):
    r = _client(tmp_path).get("/api/runs")
    assert r.status_code == 200
    assert r.json()["runs"][0]["run_id"] == "20260626-000001-aaaa"


def test_run_detail_and_404(tmp_path: Path):
    c = _client(tmp_path)
    ok = c.get("/api/runs/20260626-000001-aaaa")
    assert ok.status_code == 200
    assert "000010.png" in ok.json()["frames"]
    assert c.get("/api/runs/missing").status_code == 404


def test_feed_endpoint(tmp_path: Path):
    c = _client(tmp_path)
    feed = c.get("/api/runs/20260626-000001-aaaa/feed").json()["feed"]
    kinds = {e["kind"] for e in feed}
    assert {"milestone", "telemetry"} <= kinds


def test_frame_bytes_and_404(tmp_path: Path):
    c = _client(tmp_path)
    img = c.get("/runs/20260626-000001-aaaa/frames/000010.png")
    assert img.status_code == 200
    assert img.headers["content-type"] == "image/png"
    assert c.get("/runs/20260626-000001-aaaa/frames/nope.png").status_code == 404


def test_frame_non_png_404(tmp_path: Path):
    c = _client(tmp_path)
    assert c.get("/runs/20260626-000001-aaaa/frames/not_an_image.txt").status_code == 404


def test_feed_404(tmp_path: Path):
    assert _client(tmp_path).get("/api/runs/missing/feed").status_code == 404


def test_index_served(tmp_path: Path):
    assert _client(tmp_path).get("/").status_code == 200


def test_beat_route_serves_index(tmp_path: Path):
    c = _client(tmp_path)
    beat = c.get("/5")
    assert beat.status_code == 200
    assert beat.text == c.get("/").text


def test_run_summary_has_grid_fields(tmp_path: Path):
    r = _client(tmp_path).get("/api/runs").json()["runs"][0]
    assert r["thumbnail"] == "000040.png"
    assert r["status"] == "done"
    assert {"run_id", "turns", "battles_won", "maps_visited", "badges", "frame_count"} <= r.keys()


def test_ws_live_telemetry_event_roundtrip(tmp_path: Path):
    make_fixture_run(tmp_path, "r")
    app = create_app(tmp_path, hub=LiveHub())
    client = TestClient(app)
    with client.websocket_connect("/ws/live/r") as sub:
        with client.websocket_connect("/ws/produce/r") as prod:
            prod.send_json({"type": "event", "event_type": "battle", "turn": 5, "data": {"player_hp": 9}})
            got = sub.receive_json()
    assert got["event_type"] == "battle" and got["turn"] == 5


def test_ws_producer_to_subscriber(tmp_path: Path):
    make_fixture_run(tmp_path, "r")
    app = create_app(tmp_path, hub=LiveHub())
    client = TestClient(app)
    with client.websocket_connect("/ws/live/r") as sub:
        with client.websocket_connect("/ws/produce/r") as prod:
            prod.send_json({"type": "event", "turn": 1, "text": "hi"})
            assert sub.receive_json()["text"] == "hi"


def test_ws_live_no_hub(tmp_path: Path):
    make_fixture_run(tmp_path, "r")
    app = create_app(tmp_path, hub=None)
    client = TestClient(app)
    with client.websocket_connect("/ws/live/r") as ws:
        # hub is None — server accepts then immediately closes; verify the server closed
        with pytest.raises(WebSocketDisconnect):
            ws.receive_text()


def test_ws_live_disconnect_covers_except(tmp_path: Path):
    """Covers except WebSocketDisconnect (line 83) in the /ws/live handler.

    ws.send_json() never raises WebSocketDisconnect via the TestClient's in-memory
    transport (no OSError path).  The except clause IS reachable when WebSocketDisconnect
    is raised from *inside* the try block — specifically from a second await q.get() call
    after the client has signalled disconnect.  We model this by patching get() on the
    queue returned by super().subscribe(), so the queue is registered through the parent's
    public path (no direct _subs access in the override) and raises WebSocketDisconnect on
    its second call, simulating what a broken connection would produce in production.
    """

    class _OnceHub(LiveHub):
        def subscribe(self, run_id: str) -> asyncio.Queue:  # type: ignore[override]
            q = super().subscribe(run_id)  # registers queue via parent's public path
            _count = 0
            _orig_get = q.get

            async def _once_get() -> object:
                nonlocal _count
                _count += 1
                if _count > 1:
                    raise WebSocketDisconnect(code=1001)
                return await _orig_get()

            q.get = _once_get  # type: ignore[method-assign]
            return q

    hub = _OnceHub()
    make_fixture_run(tmp_path, "r")
    app = create_app(tmp_path, hub=hub)
    # WSD is caught inside the server handler so no uncaught server exception surfaces;
    # raise_server_exceptions=True (default) is intentional — a real crash would not be hidden.
    client = TestClient(app)
    with client.websocket_connect("/ws/live/r") as sub:
        with client.websocket_connect("/ws/produce/r") as prod:
            prod.send_json({"type": "event", "text": "hi"})
            sub.receive_json()
            # Server loops; second q.get() raises WebSocketDisconnect
            # → except WebSocketDisconnect: pass (line 83) + finally: unsubscribe
    # Assert cleanup: the finally block must have removed the subscription.
    # The server's finally runs before the handler returns, which happens before
    # the with-sub exit completes, so this check is race-free.
    assert hub._subs.get("r", []) == []


def test_ws_live_breaks_after_done_message(tmp_path: Path):
    """The /ws/live handler must forward a done message then unsubscribe (break the loop).

    Verified by asserting: (a) the done message is delivered to the subscriber, and
    (b) the subscription is removed after done (the finally block ran).
    """
    make_fixture_run(tmp_path, "r")
    hub = LiveHub()
    app = create_app(tmp_path, hub=hub)
    client = TestClient(app)
    with client.websocket_connect("/ws/live/r") as sub:
        with client.websocket_connect("/ws/produce/r") as prod:
            prod.send_json({"type": "event", "turn": 1, "data": {}})
            event_msg = sub.receive_json()
            assert event_msg["type"] == "event"
            prod.send_json({"type": "done"})
            done_msg = sub.receive_json()
            assert done_msg == {"type": "done"}
    # After the done message, the server handler broke the loop and the finally block
    # ran hub.unsubscribe(). The subscription must be gone.
    assert hub._subs.get("r", []) == []


def test_ws_produce_disconnect_publishes_done(tmp_path: Path):
    """When producer disconnects without sending done, a done is published to subscribers."""
    make_fixture_run(tmp_path, "r")
    hub = LiveHub()
    app = create_app(tmp_path, hub=hub)
    client = TestClient(app)
    with client.websocket_connect("/ws/live/r") as sub:
        with client.websocket_connect("/ws/produce/r") as prod:
            prod.send_json({"type": "event", "turn": 1, "data": {}})
            sub.receive_json()
        # prod context exits → producer WS disconnects → server publishes {"type": "done"}
        done_msg = sub.receive_json()
        assert done_msg == {"type": "done"}


def test_ws_produce_disconnect_no_hub(tmp_path: Path):
    """Producer disconnect with hub=None must not raise (the hub is None branch)."""
    make_fixture_run(tmp_path, "r")
    app = create_app(tmp_path, hub=None)
    client = TestClient(app)
    # Just exercise the connect+disconnect path without crashing
    with client.websocket_connect("/ws/produce/r") as prod:
        prod.send_json({"type": "event", "turn": 1})
    # No assertion needed beyond no exception being raised


def test_ws_live_streams_appended_alerts_as_anomalies(tmp_path: Path):
    """Alerts appended to alerts.jsonl while a live subscriber is connected are
    pushed over the live websocket as anomaly messages."""
    import time

    make_fixture_run(tmp_path, "r")
    alerts = tmp_path / "alerts.jsonl"
    app = create_app(tmp_path, alerts_path=alerts, hub=LiveHub(), alerts_poll_interval=0.02)
    with TestClient(app) as client:  # context manager → startup event runs the tail task
        with client.websocket_connect("/ws/live/r") as sub:
            time.sleep(0.1)  # let the tail record its starting offset
            with alerts.open("a") as fh:
                fh.write(json.dumps({"alert_type": "STUCK_LOOP", "detail": "map=51", "turn": 42}) + "\n")
            msg = sub.receive_json()
    assert msg["type"] == "anomaly"
    assert msg["alert_type"] == "STUCK_LOOP"
    assert msg["turn"] == 42


def test_no_tail_task_without_hub(tmp_path: Path):
    """With hub=None the app must start cleanly even when alerts_path is set."""
    make_fixture_run(tmp_path, "r")
    app = create_app(tmp_path, alerts_path=tmp_path / "alerts.jsonl", hub=None)
    with TestClient(app) as client:
        assert client.get("/api/runs").status_code == 200


def test_agent_state_endpoint_and_404(tmp_path: Path):
    run = tmp_path / "r1"
    run.mkdir()
    events = [
        {"event_type": "agent_state", "turn": 20, "occurred_at": "t2", "data": {"tier": "low", "stuck_streak": 0}},
        {"event_type": "agent_state", "turn": 10, "occurred_at": "t1", "data": {"tier": "low", "stuck_streak": 3}},
        {"event_type": "decision", "turn": 10, "occurred_at": "t1", "data": {"mode": "overworld"}},
    ]
    (run / "events.jsonl").write_text("\n".join(json.dumps(e) for e in events))
    client = TestClient(create_app(tmp_path))
    resp = client.get("/api/runs/r1/agent_state")
    assert resp.status_code == 200
    states = resp.json()["states"]
    assert [s["turn"] for s in states] == [10, 20]
    assert states[0] == {"turn": 10, "ts": "t1", "data": {"tier": "low", "stuck_streak": 3}}
    assert client.get("/api/runs/nope/agent_state").status_code == 404


def test_agent_state_endpoint_tolerates_malformed_turn(tmp_path: Path):
    run = tmp_path / "r1"
    run.mkdir()
    events = [
        {"event_type": "agent_state", "turn": None, "occurred_at": "t1", "data": {"tier": "low"}},
        {"event_type": "agent_state", "turn": 5, "occurred_at": "t2", "data": {"tier": "low"}},
    ]
    (run / "events.jsonl").write_text("\n".join(json.dumps(e) for e in events))
    client = TestClient(create_app(tmp_path))
    resp = client.get("/api/runs/r1/agent_state")
    assert resp.status_code == 200
    assert [s["turn"] for s in resp.json()["states"]] == [0, 5]
