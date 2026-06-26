"""Tests for run_10_agents.py — 100% coverage."""

import json
import os
import runpy
import signal
import subprocess as sp
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

import run_10_agents as mod
from run_10_agents import (
    PARAM_VARIANTS,
    MAX_TURNS,
    score,  # re-exported from evolve
    run_one_agent,
    main,
    _has_paper,
    _extract_fitness,
    _terminate_tree,
)


def _fake_proc(stdout="", returncode=0, communicate_exc=None, pid=999999):
    """Build a mock subprocess.Popen object for run_one_agent tests."""
    proc = MagicMock()
    proc.pid = pid
    proc.returncode = returncode
    if communicate_exc is not None:
        proc.communicate.side_effect = communicate_exc
    else:
        proc.communicate.return_value = (stdout, "")
    return proc


# ── PARAM_VARIANTS validation ────────────────────────────────────────


class TestParamVariants:
    def test_has_12_variants(self):
        assert len(PARAM_VARIANTS) == 12

    def test_all_variants_have_required_keys(self):
        required = {"stuck_threshold", "door_cooldown", "waypoint_skip_distance",
                     "axis_preference_map_0", "label",
                     "bt_max_snapshots", "bt_restore_threshold",
                     "bt_max_attempts", "bt_snapshot_interval"}
        for i, variant in enumerate(PARAM_VARIANTS):
            missing = required - set(variant.keys())
            assert not missing, f"Variant {i} ({variant.get('label', '?')}) missing: {missing}"

    def test_labels_unique(self):
        labels = [v["label"] for v in PARAM_VARIANTS]
        assert len(labels) == len(set(labels)), "Duplicate labels found"


# ── score() ───────────────────────────────────────────────────────────


class TestScore:
    def test_zero_fitness(self):
        assert score({}) == 0.0

    def test_positive_score(self):
        f = {
            "final_map_id": 1,
            "badges": 1,
            "party_size": 1,
            "battles_won": 5,
            "stuck_count": 2,
            "turns": 100,
        }
        expected = 1000 + 5000 + 500 + 50 - 10 - 10.0
        assert score(f) == expected

    def test_stuck_penalizes(self):
        base = {"final_map_id": 1}
        stuck = {"final_map_id": 1, "stuck_count": 100}
        assert score(stuck) < score(base)


# ── _has_paper() ──────────────────────────────────────────────────────


class TestHasPaper:
    def test_true_when_paper_present(self):
        with patch("run_10_agents.subprocess.run", return_value=MagicMock()):
            assert _has_paper() is True

    def test_false_when_not_installed(self):
        with patch("run_10_agents.subprocess.run",
                   side_effect=FileNotFoundError("no paper")):
            assert _has_paper() is False

    def test_false_on_timeout(self):
        with patch("run_10_agents.subprocess.run",
                   side_effect=sp.TimeoutExpired("paper", 5)):
            assert _has_paper() is False


# ── _extract_fitness() ────────────────────────────────────────────────


class TestExtractFitness:
    def test_extracts_valid_json(self):
        out = 'noise\n  {\n  "party_size": 2,\n  "turns": 10\n}\n more'
        assert _extract_fitness(out) == {"party_size": 2, "turns": 10}

    def test_no_match_returns_empty(self):
        assert _extract_fitness("nothing here") == {}

    def test_matching_blob_but_invalid_json_returns_empty(self):
        # Regex matches a {...party_size...} blob, but it isn't valid JSON.
        assert _extract_fitness('garbage {"party_size": } trailing') == {}


# ── run_one_agent() ───────────────────────────────────────────────────


class TestRunOneAgent:
    def _make_fitness(self, **overrides):
        f = {"final_map_id": 1, "badges": 0, "party_size": 1,
             "battles_won": 3, "stuck_count": 2, "turns": 50}
        f.update(overrides)
        return f

    def test_success(self):
        fitness = self._make_fitness()
        params = {"stuck_threshold": 8, "door_cooldown": 4,
                  "waypoint_skip_distance": 3, "axis_preference_map_0": "y",
                  "label": "test_label"}

        with patch("run_10_agents.subprocess.Popen",
                   return_value=_fake_proc(stdout=json.dumps(fitness))):
            result = run_one_agent("/fake/rom.gb", params, 0, use_paper=False)

        assert result["agent_id"] == 0
        assert result["label"] == "test_label"
        assert result["fitness"] == fitness
        assert result["score"] == score(fitness)
        assert result["returncode"] == 0
        assert "error" not in result
        # label should be stripped from params passed to agent
        assert "label" not in result["params"]

    def test_label_defaults_to_agent_id(self):
        fitness = self._make_fitness()
        params = {"stuck_threshold": 8, "door_cooldown": 4,
                  "waypoint_skip_distance": 3, "axis_preference_map_0": "y"}

        with patch("run_10_agents.subprocess.Popen",
                   return_value=_fake_proc(stdout=json.dumps(fitness))):
            result = run_one_agent("/fake/rom.gb", params, 7, use_paper=False)

        assert result["label"] == "agent_7"

    def test_launches_in_new_session_for_group_kill(self):
        # The child MUST be launched with start_new_session=True so a timeout
        # can kill the whole process tree (the orphan-prevention fix).
        fitness = self._make_fitness()
        captured = {}

        def fake_popen(cmd, **kwargs):
            captured.update(kwargs)
            return _fake_proc(stdout=json.dumps(fitness))

        with patch("run_10_agents.subprocess.Popen", side_effect=fake_popen):
            run_one_agent("/fake/rom.gb", {"label": "sess"}, 0, use_paper=False)

        assert captured.get("start_new_session") is True

    def test_timeout_kills_tree_and_returns_error(self):
        params = {"stuck_threshold": 8, "label": "timeout_test"}
        proc = _fake_proc(communicate_exc=sp.TimeoutExpired("cmd", 300))

        with patch("run_10_agents.subprocess.Popen", return_value=proc), \
             patch("run_10_agents._terminate_tree") as term:
            result = run_one_agent("/fake/rom.gb", params, 1, use_paper=False)

        # On timeout we must tear down the whole process group, not leak it.
        term.assert_called_once_with(proc)
        assert result["score"] == -999
        assert result["fitness"] == {}
        assert result["error"] == "timeout"

    def test_file_not_found_returns_error_without_terminate(self):
        # Popen itself raising (e.g. binary missing) leaves no process to kill.
        params = {"stuck_threshold": 8, "label": "fnf_test"}

        with patch("run_10_agents.subprocess.Popen",
                   side_effect=FileNotFoundError("no claude")), \
             patch("run_10_agents._terminate_tree") as term:
            result = run_one_agent("/fake/rom.gb", params, 2, use_paper=False)

        term.assert_not_called()
        assert result["score"] == -999
        assert result["error"] == "no claude"

    def test_generic_error_after_spawn_kills_tree(self):
        # A failure during communicate() (after the child spawned) must still
        # tear down the process group.
        params = {"stuck_threshold": 8, "label": "boom"}
        proc = _fake_proc(communicate_exc=RuntimeError("kaboom"))

        with patch("run_10_agents.subprocess.Popen", return_value=proc), \
             patch("run_10_agents._terminate_tree") as term:
            result = run_one_agent("/fake/rom.gb", params, 4, use_paper=False)

        term.assert_called_once_with(proc)
        assert result["score"] == -999
        assert result["error"] == "kaboom"

    def test_invalid_json_returns_error(self):
        params = {"stuck_threshold": 8, "label": "bad_json"}

        with patch("run_10_agents.subprocess.Popen",
                   return_value=_fake_proc(stdout="not json at all")):
            result = run_one_agent("/fake/rom.gb", params, 3, use_paper=False)

        # _extract_fitness returns {} when no party_size JSON found → score -999
        assert result["score"] == -999

    def test_params_embedded_in_prompt(self):
        fitness = self._make_fitness()
        captured_cmd = []

        def fake_popen(cmd, **kwargs):
            captured_cmd.extend(cmd)
            return _fake_proc(stdout=json.dumps(fitness))

        params = {"stuck_threshold": 10, "door_cooldown": 6, "label": "env_test"}

        with patch("run_10_agents.subprocess.Popen", side_effect=fake_popen):
            run_one_agent("/fake/rom.gb", params, 0, use_paper=False)

        # EVOLVE_PARAMS is embedded in the prompt string (last cmd arg)
        prompt = captured_cmd[-1]
        assert "EVOLVE_PARAMS" in prompt
        assert '"stuck_threshold": 10' in prompt
        assert "label" not in json.loads(
            prompt.split("EVOLVE_PARAMS='")[1].split("'")[0]
        )

    def test_use_paper_builds_paper_cmd_and_strips_keys(self):
        # use_paper=True must invoke `paper start claude` and strip the API
        # key / base URL from the child env (paper handles auth itself).
        fitness = self._make_fitness()
        captured = {}

        def fake_popen(cmd, env=None, **kwargs):
            captured["cmd"] = cmd
            captured["env"] = env or {}
            return _fake_proc(stdout=json.dumps(fitness))

        params = {"stuck_threshold": 8, "label": "paper_test"}

        with patch("run_10_agents.subprocess.Popen", side_effect=fake_popen), \
             patch.dict(os.environ,
                        {"ANTHROPIC_API_KEY": "secret", "ANTHROPIC_BASE_URL": "http://x"}):
            result = run_one_agent("/fake/rom.gb", params, 0, use_paper=True)

        assert captured["cmd"][:3] == ["paper", "start", "claude"]
        assert "ANTHROPIC_API_KEY" not in captured["env"]
        assert "ANTHROPIC_BASE_URL" not in captured["env"]
        assert result["returncode"] == 0

    def test_non_paper_keeps_api_key_in_env(self):
        # Without paper, auth comes from the inherited env, so the API key
        # must be preserved (unlike the paper branch which strips it).
        fitness = self._make_fitness()
        captured = {}

        def fake_popen(cmd, env=None, **kwargs):
            captured["env"] = env or {}
            return _fake_proc(stdout=json.dumps(fitness))

        params = {"stuck_threshold": 8, "label": "env_key_test"}

        with patch("run_10_agents.subprocess.Popen", side_effect=fake_popen), \
             patch.dict(os.environ, {"ANTHROPIC_API_KEY": "test-key"}):
            run_one_agent("/fake/rom.gb", params, 0, use_paper=False)

        assert captured["env"].get("ANTHROPIC_API_KEY") == "test-key"


# ── _terminate_tree() ─────────────────────────────────────────────────


class TestTerminateTree:
    def test_kills_group_then_reaps(self):
        proc = MagicMock(pid=4321)
        with patch("run_10_agents.os.getpgid", return_value=4321), \
             patch("run_10_agents.os.killpg") as killpg:
            _terminate_tree(proc)
        killpg.assert_called_once()
        # SIGKILL the whole group, then wait to reap the leader.
        assert killpg.call_args[0][1] == signal.SIGKILL
        proc.wait.assert_called_once()

    def test_swallows_missing_process(self):
        # Group already gone → ProcessLookupError must be swallowed.
        proc = MagicMock(pid=4321)
        with patch("run_10_agents.os.getpgid", side_effect=ProcessLookupError):
            _terminate_tree(proc)  # must not raise
        proc.wait.assert_called_once()

    def test_swallows_wait_timeout(self):
        # If the leader won't reap in time, the TimeoutExpired is swallowed.
        proc = MagicMock(pid=4321)
        proc.wait.side_effect = sp.TimeoutExpired("cmd", 10)
        with patch("run_10_agents.os.getpgid", return_value=4321), \
             patch("run_10_agents.os.killpg"):
            _terminate_tree(proc)  # must not raise


# ── main() ────────────────────────────────────────────────────────────


class TestMain:
    def test_no_args_exits(self, capsys):
        with patch("sys.argv", ["run_10_agents.py"]):
            with pytest.raises(SystemExit) as exc:
                main()
            assert exc.value.code == 1
        assert "Usage" in capsys.readouterr().out

    def test_rom_not_found_exits(self, capsys):
        with patch("sys.argv", ["run_10_agents.py", "/nonexistent/rom.gb"]):
            with pytest.raises(SystemExit) as exc:
                main()
            assert exc.value.code == 1
        assert "ROM not found" in capsys.readouterr().out

    def test_full_run(self, tmp_path, capsys):
        rom = tmp_path / "test.gb"
        rom.write_bytes(b"\x00" * 100)

        fake_result = {
            "agent_id": 0,
            "label": "test",
            "params": {},
            "fitness": {"final_map_id": 1, "badges": 0, "party_size": 1,
                        "battles_won": 3, "stuck_count": 2, "turns": 50},
            "score": 1530.0,
            "elapsed": 1.0,
            "returncode": 0,
        }

        def mock_run_one_agent(rom_path, params, agent_id, use_paper):
            return dict(fake_result, agent_id=agent_id,
                        label=params.get("label", f"agent_{agent_id}"))

        scripts_dir = tmp_path / "scripts"
        scripts_dir.mkdir()

        with patch("sys.argv", ["run_10_agents.py", str(rom)]), \
             patch("run_10_agents.run_one_agent", side_effect=mock_run_one_agent), \
             patch("run_10_agents.ThreadPoolExecutor", ThreadPoolExecutor), \
             patch.object(mod, "SCRIPT_DIR", scripts_dir), \
             patch.object(mod, "WORKSPACE", tmp_path):
            main()

        output = capsys.readouterr().out
        assert "Winner:" in output
        assert "score=" in output

        saved = tmp_path / "pokedex" / "evolve_results.json"
        assert saved.exists()
        data = json.loads(saved.read_text())
        assert len(data) == len(PARAM_VARIANTS)

    def test_error_result_shows_fail(self, tmp_path, capsys):
        rom = tmp_path / "test.gb"
        rom.write_bytes(b"\x00" * 100)

        def mock_run_one_agent(rom_path, params, agent_id, use_paper):
            return {
                "agent_id": agent_id,
                "label": params.get("label", f"agent_{agent_id}"),
                "params": {},
                "fitness": {},
                "score": -999,
                "elapsed": 0.5,
                "error": "timeout",
            }

        scripts_dir = tmp_path / "scripts"
        scripts_dir.mkdir()

        with patch("sys.argv", ["run_10_agents.py", str(rom)]), \
             patch("run_10_agents.run_one_agent", side_effect=mock_run_one_agent), \
             patch("run_10_agents.ThreadPoolExecutor", ThreadPoolExecutor), \
             patch.object(mod, "SCRIPT_DIR", scripts_dir), \
             patch.object(mod, "WORKSPACE", tmp_path):
            main()

        output = capsys.readouterr().out
        assert "[FAIL(" in output


# ── __main__ guard ────────────────────────────────────────────────────


class TestMainGuard:
    def test_dunder_main_calls_main(self, tmp_path, capsys):
        # runpy executes the script in a fresh ``__main__`` namespace, so
        # ``patch("run_10_agents.run_one_agent")`` does NOT reach the executed
        # copy — the real ``main()`` runs end to end. The ONLY external boundary
        # that must be sealed is ``subprocess.run``: we patch it on the shared
        # subprocess module (which the runpy ``__main__`` imports too), so no
        # real ``paper``/``claude``/``agent.py`` process can ever spawn.
        #
        # We run the REAL file (not a copy) so coverage attributes correctly,
        # and save/restore the repo's evolve_results.json since main() writes to
        # WORKSPACE — which, for the real file, is the repo itself.
        rom = tmp_path / "test.gb"
        rom.write_bytes(b"\x00" * 100)

        fitness = {"final_map_id": 0, "badges": 0, "party_size": 1,
                   "battles_won": 0, "stuck_count": 0, "turns": 0}

        def fake_popen(cmd, **kwargs):
            return _fake_proc(stdout=json.dumps(fitness))

        results_path = Path(mod.WORKSPACE) / "pokedex" / "evolve_results.json"
        backup = results_path.read_text() if results_path.exists() else None
        try:
            # Seal both subprocess boundaries: Popen (run_one_agent) and run
            # (_has_paper's `paper --version` probe), so no real process spawns
            # regardless of what's installed on the host.
            with patch("sys.argv", ["run_10_agents.py", str(rom)]), \
                 patch("run_10_agents.subprocess.Popen", side_effect=fake_popen), \
                 patch("run_10_agents.subprocess.run", return_value=MagicMock()):
                runpy.run_path(
                    str(Path(mod.__file__).resolve()),
                    run_name="__main__",
                )
            # Prove main() actually ran to completion (not a vacuous pass):
            # it must have processed every variant and printed a winner.
            output = capsys.readouterr().out
            assert "Winner:" in output
            assert output.count("score=") >= len(PARAM_VARIANTS)
        finally:
            # Leave the repo's results file exactly as we found it.
            if backup is not None:
                results_path.write_text(backup)
            elif results_path.exists():
                results_path.unlink()
