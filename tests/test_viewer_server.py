from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient
from fixtures.make_fixture_run import make_fixture_run

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
        # hub is None — server should accept then immediately close
        ws.close()


def test_ws_live_disconnect_covers_except(tmp_path: Path):
    """Covers except WebSocketDisconnect (line 83) in the /ws/live handler.

    ws.send_json() never raises WebSocketDisconnect via the TestClient's in-memory
    transport (no OSError path).  The except clause IS reachable when WebSocketDisconnect
    is raised from *inside* the try block — specifically from a second await q.get() call
    after the client has signalled disconnect.  We model this with a custom Queue that
    raises WebSocketDisconnect on its second get(), simulating what a broken connection
    would produce in production.
    """
    import asyncio as _asyncio

    from starlette.websockets import WebSocketDisconnect as _WSD

    class _OnceQueue(_asyncio.Queue):
        def __init__(self) -> None:
            super().__init__()
            self._n = 0

        async def get(self):
            self._n += 1
            if self._n > 1:
                raise _WSD(code=1001)
            return await super().get()

    class _OnceHub(LiveHub):
        def subscribe(self, run_id: str) -> _asyncio.Queue:  # type: ignore[override]
            q = _OnceQueue()
            self._subs[run_id].append(q)
            return q

    make_fixture_run(tmp_path, "r")
    app = create_app(tmp_path, hub=_OnceHub())
    client = TestClient(app, raise_server_exceptions=False)
    with client.websocket_connect("/ws/live/r") as sub:
        with client.websocket_connect("/ws/produce/r") as prod:
            prod.send_json({"type": "event", "text": "hi"})
            sub.receive_json()
            # Server loops, second q.get() raises WebSocketDisconnect
            # → except WebSocketDisconnect: pass (line 83) + finally: unsubscribe
