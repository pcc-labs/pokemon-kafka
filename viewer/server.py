"""FastAPI app serving replay REST endpoints, frames, and the static UI."""

from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from viewer.feed import build_feed, load_anomalies, load_observations
from viewer.store import RunStore

_STATIC = Path(__file__).parent / "static"


def create_app(runs_dir, *, observations_path=None, alerts_path=None, hub=None) -> FastAPI:
    runs_dir = Path(runs_dir)
    store = RunStore(runs_dir)
    app = FastAPI(title="Pokédex Viewer")

    @app.get("/api/runs")
    def list_runs():
        return {"runs": [r.to_dict() for r in store.list_runs()]}

    @app.get("/api/runs/{run_id}")
    def run_detail(run_id: str):
        if not (runs_dir / run_id).is_dir():
            raise HTTPException(status_code=404, detail="run not found")
        summary = store.get_summary(run_id)
        status = "done" if (runs_dir / run_id / "summary.json").exists() else "live"
        return {"summary": summary, "frames": store.frame_names(run_id), "status": status}

    @app.get("/api/runs/{run_id}/feed")
    def run_feed(run_id: str):
        if not (runs_dir / run_id).is_dir():
            raise HTTPException(status_code=404, detail="run not found")
        observations = load_observations(observations_path) if observations_path else []
        anomalies = load_anomalies(alerts_path) if alerts_path else []
        feed = build_feed(store.load_events(run_id), observations, anomalies)
        return {"feed": [e.to_dict() for e in feed]}

    @app.get("/runs/{run_id}/frames/{name}")
    def frame(run_id: str, name: str):
        path = runs_dir / run_id / "frames" / name
        if not path.is_file() or not name.endswith(".png"):
            raise HTTPException(status_code=404, detail="frame not found")
        return FileResponse(path, media_type="image/png")

    @app.get("/")
    def index():
        return FileResponse(_STATIC / "index.html")

    @app.get("/{beat:int}")
    def beat_route(beat: int):
        return FileResponse(_STATIC / "index.html")

    if _STATIC.is_dir():
        app.mount("/static", StaticFiles(directory=_STATIC), name="static")

    app.state.hub = hub  # used by live task; harmless when None

    @app.websocket("/ws/produce/{run_id}")
    async def produce(ws: WebSocket, run_id: str):
        await ws.accept()
        hub = app.state.hub
        try:
            while True:
                msg = await ws.receive_json()
                if hub is not None:
                    await hub.publish(run_id, msg)
        except WebSocketDisconnect:
            if hub is not None:
                await hub.publish(run_id, {"type": "done"})

    @app.websocket("/ws/live/{run_id}")
    async def live(ws: WebSocket, run_id: str):
        await ws.accept()
        hub = app.state.hub
        if hub is None:
            await ws.close()
            return
        q = hub.subscribe(run_id)
        try:
            while True:
                msg = await q.get()
                await ws.send_json(msg)
                if isinstance(msg, dict) and msg.get("type") == "done":
                    break
        except WebSocketDisconnect:
            pass
        finally:
            hub.unsubscribe(run_id, q)

    return app
