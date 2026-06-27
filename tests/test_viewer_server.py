from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient
from fixtures.make_fixture_run import make_fixture_run

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
    assert {"turns", "battles_won", "maps_visited", "badges", "frame_count"} <= r.keys()
