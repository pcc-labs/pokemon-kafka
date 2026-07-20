"""The viewer's HEAL button: POST /api/runs/{id}/heal runs healer.py check on that run."""

from __future__ import annotations

import json
import time
from pathlib import Path
from types import SimpleNamespace

from fastapi.testclient import TestClient
from fixtures.make_fixture_run import make_fixture_run

from viewer.heal import HealJobs
from viewer.server import create_app

RUN_ID = "20260626-000001-aaaa"
VERDICT_LINE = "[healer] kept current genome (control 5720, best variant 5720)"


class FakeRunner:
    def __init__(self, stdout: str = VERDICT_LINE):
        self.stdout = stdout
        self.calls: list[list[str]] = []

    def __call__(self, cmd, **kwargs):
        self.calls.append(cmd)
        return SimpleNamespace(stdout=self.stdout, returncode=0)


def _run_with_rom(tmp_path: Path) -> Path:
    """Fixture run whose summary names a ROM that exists."""
    run_dir = make_fixture_run(tmp_path, RUN_ID)
    rom = tmp_path / "red.gb"
    rom.write_bytes(b"\x00")
    summary = json.loads((run_dir / "summary.json").read_text())
    summary["params"]["rom"] = str(rom)
    (run_dir / "summary.json").write_text(json.dumps(summary))
    return run_dir


def _client(tmp_path: Path, runner=None, background: bool = False):
    jobs = HealJobs(tmp_path, runner=runner or FakeRunner(), background=background)
    return TestClient(create_app(tmp_path, heal_jobs=jobs)), jobs


def test_post_heal_runs_healer_on_run_summary(tmp_path: Path):
    _run_with_rom(tmp_path)
    runner = FakeRunner()
    client, _ = _client(tmp_path, runner)

    r = client.post(f"/api/runs/{RUN_ID}/heal")

    assert r.status_code == 200
    assert r.json() == {"state": "done", "verdict": "kept current genome (control 5720, best variant 5720)"}
    cmd = runner.calls[0]
    assert cmd[1].endswith("healer.py")
    assert "check" in cmd
    assert cmd[cmd.index("--fitness") + 1].endswith(f"{RUN_ID}/summary.json")
    assert cmd[cmd.index("--rom") + 1].endswith("red.gb")
    assert "--cooldown-hours" not in cmd


def test_post_heal_force_overrides_cooldown(tmp_path: Path):
    _run_with_rom(tmp_path)
    runner = FakeRunner()
    client, _ = _client(tmp_path, runner)

    client.post(f"/api/runs/{RUN_ID}/heal?force=true")

    cmd = runner.calls[0]
    assert cmd[cmd.index("--cooldown-hours") + 1] == "0"


def test_heal_status_starts_idle(tmp_path: Path):
    _run_with_rom(tmp_path)
    client, _ = _client(tmp_path)
    r = client.get(f"/api/runs/{RUN_ID}/heal")
    assert r.status_code == 200
    assert r.json()["state"] == "idle"


def test_heal_404_for_missing_run(tmp_path: Path):
    make_fixture_run(tmp_path, RUN_ID)
    client, _ = _client(tmp_path)
    assert client.post("/api/runs/missing/heal").status_code == 404
    assert client.get("/api/runs/missing/heal").status_code == 404


def test_heal_errors_on_live_run_without_summary(tmp_path: Path):
    run_dir = make_fixture_run(tmp_path, RUN_ID)
    (run_dir / "summary.json").unlink()
    runner = FakeRunner()
    client, _ = _client(tmp_path, runner)

    r = client.post(f"/api/runs/{RUN_ID}/heal")

    assert r.json()["state"] == "error"
    assert "summary" in r.json()["verdict"]
    assert runner.calls == []


def test_heal_errors_when_rom_missing(tmp_path: Path):
    make_fixture_run(tmp_path, RUN_ID)  # fixture params have no rom
    runner = FakeRunner()
    client, _ = _client(tmp_path, runner)

    r = client.post(f"/api/runs/{RUN_ID}/heal")

    assert r.json()["state"] == "error"
    assert "rom" in r.json()["verdict"]
    assert runner.calls == []


def test_second_post_while_running_does_not_start_again(tmp_path: Path):
    _run_with_rom(tmp_path)
    runner = FakeRunner()
    client, jobs = _client(tmp_path, runner)
    jobs.jobs[RUN_ID] = {"state": "running", "verdict": None}

    r = client.post(f"/api/runs/{RUN_ID}/heal")

    assert r.json()["state"] == "running"
    assert runner.calls == []


def test_runner_failure_reports_error(tmp_path: Path):
    _run_with_rom(tmp_path)

    def exploding_runner(cmd, **kwargs):
        raise OSError("healer missing")

    client, _ = _client(tmp_path, runner=exploding_runner)

    r = client.post(f"/api/runs/{RUN_ID}/heal")

    assert r.json() == {"state": "error", "verdict": "healer missing"}


def test_no_healer_line_in_output_still_completes(tmp_path: Path):
    _run_with_rom(tmp_path)
    client, _ = _client(tmp_path, runner=FakeRunner(stdout="something unexpected"))

    r = client.post(f"/api/runs/{RUN_ID}/heal")

    assert r.json() == {"state": "done", "verdict": "no healer output"}


def test_background_mode_completes_via_thread(tmp_path: Path):
    _run_with_rom(tmp_path)
    client, _ = _client(tmp_path, background=True)

    # An instant fake runner may finish before the POST response is built.
    assert client.post(f"/api/runs/{RUN_ID}/heal").json()["state"] in {"running", "done"}

    deadline = time.time() + 2
    while time.time() < deadline:
        state = client.get(f"/api/runs/{RUN_ID}/heal").json()
        if state["state"] == "done":
            break
        time.sleep(0.02)
    assert state["state"] == "done"
    assert state["verdict"].startswith("kept current genome")
