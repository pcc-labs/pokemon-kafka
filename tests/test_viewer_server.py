from __future__ import annotations

import asyncio
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


def test_run_summary_has_grid_fields(tmp_path: Path):
    r = _client(tmp_path).get("/api/runs").json()["runs"][0]
    assert r["thumbnail"] == "000010.png"
    assert r["status"] == "done"
    assert {"run_id", "turns", "battles_won", "maps_visited", "badges", "frame_count"} <= r.keys()


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
