"""The agent chains healer.py check automatically at session end (self-healing loop)."""

import json
from pathlib import Path

from agent import run_self_heal
from evolve import run_agent


class RecordingRunner:
    def __init__(self):
        self.calls = []

    def __call__(self, cmd, **kwargs):
        self.calls.append(cmd)


def test_run_self_heal_invokes_healer_check(tmp_path):
    fitness_path = tmp_path / "fit.json"
    fitness_path.write_text(json.dumps({"turns": 700, "stuck_count": 20}))
    runner = RecordingRunner()

    launched = run_self_heal({"turns": 700, "stuck_count": 20}, "rom/red.gb", str(fitness_path), runner=runner)

    assert launched
    assert len(runner.calls) == 1
    cmd = runner.calls[0]
    assert cmd[1].endswith("healer.py")
    assert "check" in cmd
    assert cmd[cmd.index("--fitness") + 1] == str(fitness_path)
    assert cmd[cmd.index("--rom") + 1] == "rom/red.gb"


def test_run_self_heal_writes_fitness_when_no_path_given():
    fitness = {"turns": 700, "maps_visited": 1}
    runner = RecordingRunner()

    launched = run_self_heal(fitness, "rom/red.gb", None, runner=runner)

    assert launched
    cmd = runner.calls[0]
    written = json.loads(Path(cmd[cmd.index("--fitness") + 1]).read_text())
    assert written == fitness


def test_run_self_heal_skips_when_no_fitness():
    runner = RecordingRunner()
    assert not run_self_heal(None, "rom/red.gb", None, runner=runner)
    assert runner.calls == []


def test_run_self_heal_never_raises(tmp_path):
    fitness_path = tmp_path / "fit.json"
    fitness_path.write_text("{}")

    def exploding_runner(cmd, **kwargs):
        raise OSError("healer missing")

    assert not run_self_heal({"turns": 1}, "rom/red.gb", str(fitness_path), runner=exploding_runner)


def test_evolve_race_children_do_not_self_heal(monkeypatch):
    captured = {}

    def fake_run(cmd, **kwargs):
        captured["cmd"] = cmd
        Path(cmd[cmd.index("--output-json") + 1]).write_text(json.dumps({"turns": 1}))

    monkeypatch.setattr("evolve.subprocess.run", fake_run)
    run_agent("rom/red.gb", max_turns=1, params={})

    assert "--no-self-heal" in captured["cmd"]
