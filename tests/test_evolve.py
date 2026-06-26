"""Tests for evolve.py — 100% coverage."""

import json
import runpy
from pathlib import Path
from unittest.mock import ANY, MagicMock, patch

import evolve as evolve_mod
import pytest
from evolve import (
    DEFAULT_PARAMS,
    PARAM_BOUNDS,
    STALE_THRESHOLD,
    EvolutionResult,
    _forced_exploration_perturb,
    _make_historical_fn,
    _make_llm_fn,
    _make_observer_fn,
    _perturb,
    build_mutation_prompt,
    clamp_params,
    detect_stagnation,
    evolve,
    main,
    parse_llm_response,
    run_agent,
    score,
)

# ── EvolutionResult dataclass ──────────────────────────────────────────


class TestEvolutionResult:
    def test_defaults(self):
        r = EvolutionResult()
        assert r.generation == 0
        assert r.params == {}
        assert r.fitness == {}
        assert r.score == 0.0
        assert r.improved is False


# ── DEFAULT_PARAMS ─────────────────────────────────────────────────────


class TestDefaultParams:
    def test_keys(self):
        assert "stuck_threshold" in DEFAULT_PARAMS
        assert "door_cooldown" in DEFAULT_PARAMS
        assert "waypoint_skip_distance" in DEFAULT_PARAMS
        assert "axis_preference_map_0" in DEFAULT_PARAMS
        assert "bt_max_snapshots" in DEFAULT_PARAMS
        assert "bt_restore_threshold" in DEFAULT_PARAMS
        assert "bt_max_attempts" in DEFAULT_PARAMS
        assert "bt_snapshot_interval" in DEFAULT_PARAMS
        assert "hp_run_threshold" in DEFAULT_PARAMS
        assert "hp_heal_threshold" in DEFAULT_PARAMS
        assert "unknown_move_score" in DEFAULT_PARAMS
        assert "status_move_score" in DEFAULT_PARAMS


# ── clamp_params() ─────────────────────────────────────────────────────


class TestClampParams:
    def test_valid_passthrough(self):
        result = clamp_params(dict(DEFAULT_PARAMS))
        assert result == DEFAULT_PARAMS

    def test_clamp_int_above(self):
        params = dict(DEFAULT_PARAMS, stuck_threshold=999)
        result = clamp_params(params)
        assert result["stuck_threshold"] == 20  # max bound

    def test_clamp_int_below(self):
        params = dict(DEFAULT_PARAMS, stuck_threshold=0)
        result = clamp_params(params)
        assert result["stuck_threshold"] == 3  # min bound

    def test_clamp_float_above(self):
        params = dict(DEFAULT_PARAMS, hp_run_threshold=1.0)
        result = clamp_params(params)
        assert result["hp_run_threshold"] == 0.5

    def test_clamp_float_below(self):
        params = dict(DEFAULT_PARAMS, hp_run_threshold=0.001)
        result = clamp_params(params)
        assert result["hp_run_threshold"] == 0.05

    def test_type_coercion_float_to_int(self):
        params = dict(DEFAULT_PARAMS, stuck_threshold=8.7)
        result = clamp_params(params)
        assert result["stuck_threshold"] == 8
        assert isinstance(result["stuck_threshold"], int)

    def test_type_coercion_int_to_float(self):
        params = dict(DEFAULT_PARAMS, hp_run_threshold=0)
        result = clamp_params(params)
        assert isinstance(result["hp_run_threshold"], float)
        assert result["hp_run_threshold"] == 0.05  # clamped to min

    def test_invalid_enum_uses_default(self):
        params = dict(DEFAULT_PARAMS, axis_preference_map_0="z")
        result = clamp_params(params)
        assert result["axis_preference_map_0"] == DEFAULT_PARAMS["axis_preference_map_0"]

    def test_valid_enum_passes(self):
        params = dict(DEFAULT_PARAMS, axis_preference_map_0="x")
        result = clamp_params(params)
        assert result["axis_preference_map_0"] == "x"

    def test_unconvertible_value_uses_default(self):
        params = dict(DEFAULT_PARAMS, stuck_threshold="abc")
        result = clamp_params(params)
        assert result["stuck_threshold"] == DEFAULT_PARAMS["stuck_threshold"]

    def test_missing_key_ignored(self):
        params = {"stuck_threshold": 10}
        result = clamp_params(params)
        assert result["stuck_threshold"] == 10
        assert "door_cooldown" not in result


# ── score() ────────────────────────────────────────────────────────────


class TestScore:
    def test_zero_fitness(self):
        # Map ID 255 (unknown) has no progress entry, so progress = 0
        f = {
            "final_map_id": 255,
            "badges": 0,
            "party_size": 0,
            "battles_won": 0,
            "stuck_count": 0,
            "turns": 0,
        }
        assert score(f) == 0.0

    def test_pallet_town_has_progress(self):
        # Map ID 0 (Pallet Town) should have progress 4, not 0
        f = {
            "final_map_id": 0,
            "badges": 0,
            "party_size": 0,
            "battles_won": 0,
            "stuck_count": 0,
            "turns": 0,
        }
        assert score(f) == 4000.0

    def test_positive_score(self):
        f = {
            "final_map_id": 1,  # Viridian City, progress = 6
            "badges": 1,
            "party_size": 1,
            "battles_won": 5,
            "stuck_count": 2,
            "turns": 100,
        }
        # 6*1000 + 1*5000 + 1*500 + 5*100 - 2*5 - 100*0.1
        expected = 6000 + 5000 + 500 + 500 - 10 - 10.0
        assert score(f) == expected

    def test_viridian_beats_oaks_lab(self):
        # Viridian City (map 1, progress 6) should score higher than
        # Oak's Lab (map 40, progress 3) since it's further in the game
        oaks = {"final_map_id": 40, "badges": 0, "party_size": 0, "battles_won": 0, "stuck_count": 0, "turns": 0}
        viridian = {"final_map_id": 1, "badges": 0, "party_size": 0, "battles_won": 0, "stuck_count": 0, "turns": 0}
        assert score(viridian) > score(oaks)

    def test_missing_keys_uses_map_zero(self):
        # Empty dict defaults final_map_id to 0 (Pallet Town, progress 4)
        assert score({}) == 4000.0

    def test_high_stuck_penalizes(self):
        base = {"final_map_id": 1, "badges": 0, "party_size": 0, "battles_won": 0, "stuck_count": 0, "turns": 0}
        stuck = dict(base, stuck_count=100)
        assert score(stuck) < score(base)

    def test_backtrack_restores_penalizes(self):
        base = {
            "final_map_id": 1,
            "badges": 0,
            "party_size": 0,
            "battles_won": 0,
            "stuck_count": 0,
            "turns": 0,
            "backtrack_restores": 0,
        }
        with_bt = dict(base, backtrack_restores=10)
        assert score(with_bt) < score(base)
        # Penalty is -2 per restore
        assert score(base) - score(with_bt) == 20


# ── run_agent() ────────────────────────────────────────────────────────


class TestRunAgent:
    def test_success(self, tmp_path):
        fitness = {
            "turns": 50,
            "battles_won": 3,
            "maps_visited": 2,
            "final_map_id": 1,
            "final_x": 5,
            "final_y": 10,
            "badges": 0,
            "party_size": 1,
            "stuck_count": 2,
        }

        # Mock subprocess to write fitness JSON
        def mock_run(cmd, env=None, capture_output=False, timeout=None):
            output_path = cmd[cmd.index("--output-json") + 1]
            Path(output_path).write_text(json.dumps(fitness))
            return MagicMock(returncode=0)

        with patch("evolve.subprocess.run", side_effect=mock_run):
            result = run_agent("/fake/rom.gb", 200, DEFAULT_PARAMS)

        assert result["turns"] == 50
        assert result["battles_won"] == 3

    def test_timeout_returns_fallback(self):
        import subprocess as sp

        with patch("evolve.subprocess.run", side_effect=sp.TimeoutExpired("cmd", 600)):
            result = run_agent("/fake/rom.gb", 200, DEFAULT_PARAMS)

        assert result["stuck_count"] == 200
        assert result["battles_won"] == 0

    def test_missing_output_returns_fallback(self):
        with patch("evolve.subprocess.run"):
            # subprocess.run completes but output file doesn't exist
            result = run_agent("/fake/rom.gb", 100, DEFAULT_PARAMS)

        assert result["turns"] == 100

    def test_invalid_json_returns_fallback(self, tmp_path):
        def mock_run(cmd, env=None, capture_output=False, timeout=None):
            output_path = cmd[cmd.index("--output-json") + 1]
            Path(output_path).write_text("not json")
            return MagicMock(returncode=0)

        with patch("evolve.subprocess.run", side_effect=mock_run):
            result = run_agent("/fake/rom.gb", 100, DEFAULT_PARAMS)

        assert result["battles_won"] == 0

    def test_unlink_oserror_ignored(self):
        """Lines 108-109: OSError on cleanup is silently ignored."""

        def mock_run(cmd, env=None, capture_output=False, timeout=None):
            output_path = cmd[cmd.index("--output-json") + 1]
            Path(output_path).write_text(
                json.dumps(
                    {
                        "turns": 1,
                        "battles_won": 0,
                        "maps_visited": 0,
                        "final_map_id": 0,
                        "final_x": 0,
                        "final_y": 0,
                        "badges": 0,
                        "party_size": 0,
                        "stuck_count": 0,
                    }
                )
            )
            return MagicMock(returncode=0)

        with (
            patch("evolve.subprocess.run", side_effect=mock_run),
            patch("evolve.os.unlink", side_effect=OSError("perm denied")),
        ):
            result = run_agent("/fake/rom.gb", 100, DEFAULT_PARAMS)

        assert result["turns"] == 1

    def test_params_passed_as_env(self, tmp_path):
        captured_env = {}

        def mock_run(cmd, env=None, capture_output=False, timeout=None):
            captured_env.update(env or {})
            output_path = cmd[cmd.index("--output-json") + 1]
            Path(output_path).write_text(
                json.dumps(
                    {
                        "turns": 1,
                        "battles_won": 0,
                        "maps_visited": 0,
                        "final_map_id": 0,
                        "final_x": 0,
                        "final_y": 0,
                        "badges": 0,
                        "party_size": 0,
                        "stuck_count": 0,
                    }
                )
            )
            return MagicMock(returncode=0)

        params = {"stuck_threshold": 10, "door_cooldown": 6, "waypoint_skip_distance": 5, "axis_preference_map_0": "x"}

        with patch("evolve.subprocess.run", side_effect=mock_run):
            run_agent("/fake/rom.gb", 100, params)

        assert "EVOLVE_PARAMS" in captured_env
        assert json.loads(captured_env["EVOLVE_PARAMS"]) == params


# ── build_mutation_prompt() ────────────────────────────────────────────


class TestBuildMutationPrompt:
    def test_includes_params_and_fitness(self):
        prompt = build_mutation_prompt(DEFAULT_PARAMS, {"turns": 100, "badges": 0})
        assert "stuck_threshold" in prompt
        assert '"turns": 100' in prompt

    def test_includes_bt_descriptions(self):
        prompt = build_mutation_prompt(DEFAULT_PARAMS, {})
        assert "bt_max_snapshots" in prompt
        assert "bt_restore_threshold" in prompt
        assert "bt_max_attempts" in prompt
        assert "bt_snapshot_interval" in prompt

    def test_includes_battle_descriptions(self):
        prompt = build_mutation_prompt(DEFAULT_PARAMS, {})
        assert "hp_run_threshold" in prompt
        assert "hp_heal_threshold" in prompt
        assert "unknown_move_score" in prompt
        assert "status_move_score" in prompt

    def test_includes_observations(self):
        obs = [{"priority": "important", "content": "Tool error: boom"}]
        prompt = build_mutation_prompt(DEFAULT_PARAMS, {}, obs)
        assert "Tool error: boom" in prompt
        assert "[important]" in prompt

    def test_no_observations(self):
        prompt = build_mutation_prompt(DEFAULT_PARAMS, {})
        assert "observations" not in prompt.lower() or "Recent" not in prompt

    def test_includes_historical(self):
        hist = [{"priority": "important", "content": "Fitness declining over 5 runs"}]
        prompt = build_mutation_prompt(DEFAULT_PARAMS, {}, historical=hist)
        assert "Fitness declining over 5 runs" in prompt
        assert "[important]" in prompt
        assert "historical" in prompt.lower() or "Cross-session" in prompt

    def test_includes_evolution_history(self):
        history = [
            EvolutionResult(generation=1, params=dict(DEFAULT_PARAMS, stuck_threshold=5), score=4000.0, improved=True),
        ]
        prompt = build_mutation_prompt(DEFAULT_PARAMS, {}, evolution_history=history)
        assert "gen 1" in prompt
        assert "improved" in prompt
        assert "Previous generations" in prompt

    def test_evolution_history_diff_format(self):
        """Only params that differ from defaults appear in the diff."""
        params = dict(DEFAULT_PARAMS, stuck_threshold=5)
        history = [EvolutionResult(generation=1, params=params, score=100.0, improved=False)]
        prompt = build_mutation_prompt(DEFAULT_PARAMS, {}, evolution_history=history)
        assert '"stuck_threshold": 5' in prompt
        # bt_max_snapshots is default so shouldn't appear in diffs
        assert '"bt_max_snapshots"' not in prompt.split("Previous generations")[1]

    def test_evolution_history_none_omits_section(self):
        prompt = build_mutation_prompt(DEFAULT_PARAMS, {}, evolution_history=None)
        assert "Previous generations" not in prompt

    def test_evolution_history_empty_omits_section(self):
        prompt = build_mutation_prompt(DEFAULT_PARAMS, {}, evolution_history=[])
        assert "Previous generations" not in prompt

    def test_evolution_history_capped_at_10(self):
        history = [
            EvolutionResult(generation=i, params=dict(DEFAULT_PARAMS, stuck_threshold=i + 3), score=float(i * 100))
            for i in range(1, 15)
        ]
        prompt = build_mutation_prompt(DEFAULT_PARAMS, {}, evolution_history=history)
        # Should only include generations 5-14 (last 10)
        assert "gen 5" in prompt
        assert "gen 14" in prompt
        assert "gen 4" not in prompt

    def test_stagnant_flag_adds_warning(self):
        prompt = build_mutation_prompt(DEFAULT_PARAMS, {}, stagnant=True)
        assert "WARNING" in prompt
        assert "LARGER" in prompt

    def test_stagnant_false_no_warning(self):
        prompt = build_mutation_prompt(DEFAULT_PARAMS, {}, stagnant=False)
        assert "WARNING" not in prompt


# ── parse_llm_response() ──────────────────────────────────────────────


class TestParseLlmResponse:
    def test_valid_json(self):
        resp = json.dumps(DEFAULT_PARAMS)
        assert parse_llm_response(resp) == DEFAULT_PARAMS

    def test_json_with_code_fences(self):
        resp = f"```json\n{json.dumps(DEFAULT_PARAMS)}\n```"
        assert parse_llm_response(resp) == DEFAULT_PARAMS

    def test_invalid_json(self):
        assert parse_llm_response("not json at all") is None

    def test_missing_keys(self):
        assert parse_llm_response('{"stuck_threshold": 5}') is None

    def test_extra_whitespace(self):
        resp = f"  \n{json.dumps(DEFAULT_PARAMS)}\n  "
        assert parse_llm_response(resp) == DEFAULT_PARAMS

    def test_clamps_out_of_bounds(self):
        params = dict(DEFAULT_PARAMS, stuck_threshold=999, hp_run_threshold=5.0)
        resp = json.dumps(params)
        result = parse_llm_response(resp)
        assert result["stuck_threshold"] == 20
        assert result["hp_run_threshold"] == 0.5

    def test_none_input(self):
        assert parse_llm_response(None) is None


# ── _perturb() ─────────────────────────────────────────────────────────


class TestPerturb:
    def test_returns_dict_with_same_keys(self):
        result = _perturb(DEFAULT_PARAMS)
        assert set(result.keys()) == set(DEFAULT_PARAMS.keys())

    def test_at_least_one_value_differs(self):
        """Over many runs, perturbation should change something."""
        import random

        random.seed(42)
        diffs = 0
        for _ in range(20):
            result = _perturb(DEFAULT_PARAMS)
            if result != DEFAULT_PARAMS:
                diffs += 1
        assert diffs > 0

    def test_respects_param_bounds(self):
        """Numeric params should stay within PARAM_BOUNDS."""
        import random

        random.seed(0)
        params = dict(
            DEFAULT_PARAMS,
            stuck_threshold=3,
            door_cooldown=4,
            waypoint_skip_distance=1,
            bt_max_snapshots=2,
            bt_restore_threshold=8,
            bt_max_attempts=1,
            bt_snapshot_interval=20,
        )
        for _ in range(50):
            result = _perturb(params)
            for key, bounds in PARAM_BOUNDS.items():
                if all(isinstance(v, str) for v in bounds):
                    assert result[key] in bounds
                else:
                    lo, hi, _ = bounds
                    assert lo <= result[key] <= hi, f"{key}={result[key]} outside [{lo}, {hi}]"

    def test_can_perturb_bt_keys(self):
        """bt_* keys should be reachable by perturbation."""
        import random

        random.seed(123)
        bt_changed = set()
        for _ in range(200):
            result = _perturb(DEFAULT_PARAMS)
            for key in ("bt_max_snapshots", "bt_restore_threshold", "bt_max_attempts", "bt_snapshot_interval"):
                if result[key] != DEFAULT_PARAMS[key]:
                    bt_changed.add(key)
        assert len(bt_changed) > 0

    def test_can_perturb_battle_float_keys(self):
        """Battle float keys should be reachable by perturbation."""
        import random

        random.seed(456)
        changed = set()
        for _ in range(200):
            result = _perturb(DEFAULT_PARAMS)
            for key in ("hp_run_threshold", "hp_heal_threshold", "unknown_move_score", "status_move_score"):
                if result[key] != DEFAULT_PARAMS[key]:
                    changed.add(key)
        assert len(changed) > 0

    def test_float_perturbation_respects_bounds(self):
        """Float params should stay within PARAM_BOUNDS."""
        import random

        random.seed(0)
        params = dict(
            DEFAULT_PARAMS, hp_run_threshold=0.05, hp_heal_threshold=0.1, unknown_move_score=1.0, status_move_score=0.0
        )
        for _ in range(50):
            result = _perturb(params)
            for key in ("hp_run_threshold", "hp_heal_threshold", "unknown_move_score", "status_move_score"):
                lo, hi, _ = PARAM_BOUNDS[key]
                assert lo <= result[key] <= hi, f"{key}={result[key]} outside [{lo}, {hi}]"


# ── detect_stagnation() ────────────────────────────────────────────────


class TestDetectStagnation:
    def test_empty_results(self):
        assert detect_stagnation([]) is False

    def test_below_threshold(self):
        results = [EvolutionResult(generation=1, improved=False)]
        assert detect_stagnation(results) is False

    def test_at_threshold_all_failed(self):
        results = [EvolutionResult(generation=i, improved=False) for i in range(1, STALE_THRESHOLD + 1)]
        assert detect_stagnation(results) is True

    def test_improvement_resets_streak(self):
        results = [
            EvolutionResult(generation=1, improved=False),
            EvolutionResult(generation=2, improved=True),
            EvolutionResult(generation=3, improved=False),
            EvolutionResult(generation=4, improved=False),
        ]
        assert detect_stagnation(results) is False

    def test_custom_threshold(self):
        results = [EvolutionResult(generation=i, improved=False) for i in range(1, 3)]
        assert detect_stagnation(results, threshold=2) is True
        assert detect_stagnation(results, threshold=3) is False


# ── _forced_exploration_perturb() ─────────────────────────────────────


class TestForcedExplorationPerturb:
    def test_multiple_params_changed(self):
        import random

        random.seed(42)
        result = _forced_exploration_perturb(DEFAULT_PARAMS)
        changed = [k for k in DEFAULT_PARAMS if result[k] != DEFAULT_PARAMS[k]]
        # Should change at least 3 params (3-4 numeric + axis flip)
        assert len(changed) >= 3

    def test_respects_bounds(self):
        import random

        random.seed(0)
        for _ in range(50):
            result = _forced_exploration_perturb(DEFAULT_PARAMS)
            for key, bounds in PARAM_BOUNDS.items():
                if all(isinstance(v, str) for v in bounds):
                    assert result[key] in bounds
                else:
                    lo, hi, _ = bounds
                    assert lo <= result[key] <= hi, f"{key}={result[key]} outside [{lo}, {hi}]"

    def test_flips_axis_preference(self):
        import random

        random.seed(42)
        result = _forced_exploration_perturb(DEFAULT_PARAMS)
        # DEFAULT_PARAMS has "y", forced exploration always flips
        assert result["axis_preference_map_0"] == "x"


# ── evolve() ───────────────────────────────────────────────────────────


class TestEvolve:
    def _mock_run_agent(self, fitness_seq):
        """Return a patched run_agent that yields fitness dicts from a sequence."""
        call_count = {"n": 0}

        def mock_fn(rom, turns, params):
            idx = min(call_count["n"], len(fitness_seq) - 1)
            call_count["n"] += 1
            return fitness_seq[idx]

        return mock_fn

    def test_basic_evolution_no_llm(self):
        baseline = {"final_map_id": 0, "badges": 0, "party_size": 1, "battles_won": 0, "stuck_count": 5, "turns": 100}
        improved = {"final_map_id": 1, "badges": 0, "party_size": 1, "battles_won": 2, "stuck_count": 1, "turns": 80}

        # baseline run, then gen1 variant (improved)
        mock_run = self._mock_run_agent([baseline, improved])

        with patch("evolve.run_agent", side_effect=mock_run):
            results = evolve("/fake.gb", max_generations=1, max_turns=100)

        assert len(results) == 1
        assert results[0].improved is True

    def test_no_improvement(self):
        good = {"final_map_id": 1, "badges": 0, "party_size": 1, "battles_won": 5, "stuck_count": 0, "turns": 50}
        worse = {"final_map_id": 0, "badges": 0, "party_size": 0, "battles_won": 0, "stuck_count": 10, "turns": 200}

        mock_run = self._mock_run_agent([good, worse])

        with patch("evolve.run_agent", side_effect=mock_run):
            results = evolve("/fake.gb", max_generations=1, max_turns=100)

        assert len(results) == 1
        assert results[0].improved is False

    def test_with_llm_fn(self):
        baseline = {"final_map_id": 0, "badges": 0, "party_size": 1, "battles_won": 0, "stuck_count": 5, "turns": 100}
        improved = {"final_map_id": 1, "badges": 0, "party_size": 1, "battles_won": 2, "stuck_count": 1, "turns": 80}

        variant_params = dict(DEFAULT_PARAMS, stuck_threshold=5)
        llm_fn = MagicMock(return_value=json.dumps(variant_params))
        mock_run = self._mock_run_agent([baseline, improved])

        with patch("evolve.run_agent", side_effect=mock_run):
            results = evolve("/fake.gb", max_generations=1, max_turns=100, llm_fn=llm_fn)

        assert llm_fn.called
        assert results[0].improved is True

    def test_llm_invalid_response_skips(self):
        baseline = {"final_map_id": 0, "badges": 0, "party_size": 1, "battles_won": 0, "stuck_count": 5, "turns": 100}

        llm_fn = MagicMock(return_value="garbage response")
        mock_run = self._mock_run_agent([baseline])

        with patch("evolve.run_agent", side_effect=mock_run):
            results = evolve("/fake.gb", max_generations=1, max_turns=100, llm_fn=llm_fn)

        assert len(results) == 1
        assert results[0].improved is False

    def test_with_observer_fn(self):
        baseline = {"final_map_id": 0, "badges": 0, "party_size": 1, "battles_won": 0, "stuck_count": 5, "turns": 100}
        variant = {"final_map_id": 0, "badges": 0, "party_size": 1, "battles_won": 0, "stuck_count": 3, "turns": 100}

        obs = [{"priority": "important", "content": "Stuck at map 0"}]
        observer_fn = MagicMock(return_value=obs)

        variant_params = dict(DEFAULT_PARAMS, stuck_threshold=5)
        llm_fn = MagicMock(return_value=json.dumps(variant_params))
        mock_run = self._mock_run_agent([baseline, variant])

        with patch("evolve.run_agent", side_effect=mock_run):
            evolve("/fake.gb", max_generations=1, max_turns=100, llm_fn=llm_fn, observer_fn=observer_fn)

        # Observer was called
        assert observer_fn.called
        # LLM prompt should include observations
        prompt_arg = llm_fn.call_args[0][0]
        assert "Stuck at map 0" in prompt_arg

    def test_gen2_prompt_contains_gen1_result(self):
        baseline = {"final_map_id": 0, "badges": 0, "party_size": 1, "battles_won": 0, "stuck_count": 5, "turns": 100}
        worse = {"final_map_id": 0, "badges": 0, "party_size": 0, "battles_won": 0, "stuck_count": 10, "turns": 200}

        variant_params = dict(DEFAULT_PARAMS, stuck_threshold=5)
        llm_fn = MagicMock(return_value=json.dumps(variant_params))
        mock_run = self._mock_run_agent([baseline, worse, worse])

        with patch("evolve.run_agent", side_effect=mock_run):
            evolve("/fake.gb", max_generations=2, max_turns=100, llm_fn=llm_fn)

        # Second call to llm_fn should have gen 1 result in prompt
        assert llm_fn.call_count == 2
        gen2_prompt = llm_fn.call_args_list[1][0][0]
        assert "gen 1" in gen2_prompt
        assert "Previous generations" in gen2_prompt

    def test_stagnation_triggers_exploration_no_llm(self):
        baseline = {"final_map_id": 0, "badges": 0, "party_size": 1, "battles_won": 0, "stuck_count": 5, "turns": 100}
        worse = {"final_map_id": 0, "badges": 0, "party_size": 0, "battles_won": 0, "stuck_count": 10, "turns": 200}

        # Run enough generations to trigger stagnation (STALE_THRESHOLD = 3)
        mock_run = self._mock_run_agent([baseline] + [worse] * 5)

        with (
            patch("evolve.run_agent", side_effect=mock_run),
            patch("evolve._forced_exploration_perturb", wraps=_forced_exploration_perturb) as mock_forced,
        ):
            evolve("/fake.gb", max_generations=5, max_turns=100)

        # After 3 non-improving gens, forced exploration kicks in for gens 4 and 5
        assert mock_forced.call_count >= 1

    def test_stagnation_triggers_warning_in_llm_prompt(self):
        baseline = {"final_map_id": 0, "badges": 0, "party_size": 1, "battles_won": 0, "stuck_count": 5, "turns": 100}
        worse = {"final_map_id": 0, "badges": 0, "party_size": 0, "battles_won": 0, "stuck_count": 10, "turns": 200}

        variant_params = dict(DEFAULT_PARAMS, stuck_threshold=5)
        llm_fn = MagicMock(return_value=json.dumps(variant_params))
        mock_run = self._mock_run_agent([baseline] + [worse] * 5)

        with patch("evolve.run_agent", side_effect=mock_run):
            evolve("/fake.gb", max_generations=5, max_turns=100, llm_fn=llm_fn)

        # Gen 4 prompt (index 3) should contain WARNING since gens 1-3 all failed
        gen4_prompt = llm_fn.call_args_list[3][0][0]
        assert "WARNING" in gen4_prompt

    def test_multiple_generations(self):
        fitness_seq = [
            {
                "final_map_id": 0,
                "badges": 0,
                "party_size": 1,
                "battles_won": 0,
                "stuck_count": 5,
                "turns": 100,
            },  # baseline
            {
                "final_map_id": 1,
                "badges": 0,
                "party_size": 1,
                "battles_won": 2,
                "stuck_count": 1,
                "turns": 80,
            },  # gen1 (better)
            {
                "final_map_id": 0,
                "badges": 0,
                "party_size": 1,
                "battles_won": 0,
                "stuck_count": 8,
                "turns": 150,
            },  # gen2 (worse)
        ]

        mock_run = self._mock_run_agent(fitness_seq)

        with patch("evolve.run_agent", side_effect=mock_run):
            results = evolve("/fake.gb", max_generations=2, max_turns=100)

        assert len(results) == 2
        assert results[0].improved is True
        assert results[1].improved is False


# ── main() CLI ─────────────────────────────────────────────────────────


class TestMakeLlmFn:
    def test_returns_none_without_api_key(self):
        with patch.dict("os.environ", {}, clear=True):
            assert _make_llm_fn() is None

    def test_returns_none_without_anthropic_package(self):
        import builtins

        real_import = builtins.__import__

        def deny_anthropic(name, *args, **kwargs):
            if name == "anthropic":
                raise ImportError("no anthropic")
            return real_import(name, *args, **kwargs)

        with (
            patch.dict("os.environ", {"ANTHROPIC_API_KEY": "test-key"}),
            patch.dict("sys.modules", {"anthropic": None}),
            patch("builtins.__import__", side_effect=deny_anthropic),
        ):
            assert _make_llm_fn() is None

    def test_returns_callable_with_api_key(self):
        mock_client = MagicMock()
        mock_anthropic = MagicMock()
        mock_anthropic.Anthropic.return_value = mock_client

        with (
            patch.dict("os.environ", {"ANTHROPIC_API_KEY": "test-key"}),
            patch.dict("sys.modules", {"anthropic": mock_anthropic}),
        ):
            fn = _make_llm_fn()

        assert callable(fn)

    def test_callable_calls_anthropic_api(self):
        mock_response = MagicMock()
        mock_response.content = [MagicMock(text="response text")]
        mock_client = MagicMock()
        mock_client.messages.create.return_value = mock_response
        mock_anthropic = MagicMock()
        mock_anthropic.Anthropic.return_value = mock_client

        with (
            patch.dict("os.environ", {"ANTHROPIC_API_KEY": "test-key"}),
            patch.dict("sys.modules", {"anthropic": mock_anthropic}),
        ):
            fn = _make_llm_fn()
            result = fn("test prompt")

        assert result == "response text"
        mock_client.messages.create.assert_called_once()

    def test_callable_returns_none_on_api_error(self):
        mock_client = MagicMock()
        mock_anthropic = MagicMock()
        mock_anthropic.Anthropic.return_value = mock_client
        mock_anthropic.APIError = type("APIError", (Exception,), {})
        mock_client.messages.create.side_effect = mock_anthropic.APIError("test error")

        with (
            patch.dict("os.environ", {"ANTHROPIC_API_KEY": "test-key"}),
            patch.dict("sys.modules", {"anthropic": mock_anthropic}),
        ):
            fn = _make_llm_fn()
            result = fn("test prompt")

        assert result is None


class TestMakeObserverFn:
    def test_returns_callable(self):
        assert callable(_make_observer_fn())

    def test_callable_distills_latest_paper_session(self, monkeypatch):
        import observer

        monkeypatch.setattr(
            observer,
            "observe_session_inline",
            lambda *a, **k: [{"priority": "important", "content": "error: stuck in loop"}],
        )
        result = _make_observer_fn()()
        assert result == [{"priority": "important", "content": "error: stuck in loop"}]

    def test_callable_returns_empty_when_no_sessions(self, monkeypatch):
        import observer

        monkeypatch.setattr(observer, "observe_session_inline", lambda *a, **k: [])
        assert _make_observer_fn()() == []


class TestMakeHistoricalFn:
    def test_returns_none_when_no_dir(self):
        assert _make_historical_fn(None) is None
        assert _make_historical_fn("") is None

    def test_returns_callable_when_dir_set(self, tmp_path):
        fn = _make_historical_fn(str(tmp_path))
        assert callable(fn)

    def test_returns_empty_list_when_dir_empty(self, tmp_path):
        fn = _make_historical_fn(str(tmp_path))
        assert fn() == []

    def test_returns_empty_list_when_dir_missing(self, tmp_path):
        fn = _make_historical_fn(str(tmp_path / "nonexistent"))
        assert fn() == []

    def test_no_historical_flag(self, tmp_path):
        """--no-historical passes historical_fn=None to evolve()."""
        rom = tmp_path / "test.gb"
        rom.write_bytes(b"\x00" * 100)

        with patch(
            "sys.argv",
            [
                "evolve.py",
                str(rom),
                "--generations",
                "1",
                "--max-turns",
                "10",
                "--no-llm",
                "--no-observer",
                "--no-historical",
            ],
        ):
            with patch("evolve.evolve", return_value=[EvolutionResult(generation=1, improved=False)]) as mock_evolve:
                main()

        mock_evolve.assert_called_once_with(
            str(rom),
            max_generations=1,
            max_turns=10,
            llm_fn=None,
            observer_fn=None,
            historical_fn=None,
        )


class TestMain:
    def test_rom_not_found(self):
        with patch("sys.argv", ["evolve.py", "/nonexistent/rom.gb"]):
            with pytest.raises(SystemExit) as exc:
                main()
            assert exc.value.code == 1

    def test_runs_evolution(self, tmp_path):
        rom = tmp_path / "test.gb"
        rom.write_bytes(b"\x00" * 100)

        with patch(
            "sys.argv", ["evolve.py", str(rom), "--generations", "1", "--max-turns", "10", "--no-llm", "--no-observer"]
        ):
            with patch("evolve.evolve", return_value=[EvolutionResult(generation=1, improved=False)]) as mock_evolve:
                main()

        mock_evolve.assert_called_once_with(
            str(rom),
            max_generations=1,
            max_turns=10,
            llm_fn=None,
            observer_fn=None,
            historical_fn=ANY,
        )

    def test_observer_enabled_by_default(self, tmp_path):
        rom = tmp_path / "test.gb"
        rom.write_bytes(b"\x00" * 100)

        with patch("sys.argv", ["evolve.py", str(rom), "--generations", "1", "--max-turns", "10", "--no-llm"]):
            with patch("evolve.evolve", return_value=[EvolutionResult(generation=1, improved=False)]) as mock_evolve:
                main()

        # Observer feedback (Paper-based) is on unless --no-observer is passed.
        assert mock_evolve.call_args[1]["observer_fn"] is not None

    def test_no_observer_flag(self, tmp_path):
        rom = tmp_path / "test.gb"
        rom.write_bytes(b"\x00" * 100)

        with patch(
            "sys.argv", ["evolve.py", str(rom), "--generations", "1", "--max-turns", "10", "--no-llm", "--no-observer"]
        ):
            with patch("evolve.evolve", return_value=[EvolutionResult(generation=1, improved=False)]) as mock_evolve:
                main()

        mock_evolve.assert_called_once_with(
            str(rom),
            max_generations=1,
            max_turns=10,
            llm_fn=None,
            observer_fn=None,
            historical_fn=ANY,
        )


# ── __main__ guard ─────────────────────────────────────────────────────


class TestMainGuard:
    def test_dunder_main_calls_main(self, tmp_path):
        """if __name__ == '__main__': main()"""
        rom = tmp_path / "test.gb"
        rom.write_bytes(b"\x00" * 100)

        with (
            patch(
                "sys.argv",
                ["evolve.py", str(rom), "--generations", "1", "--max-turns", "1", "--no-observer", "--no-historical"],
            ),
            patch("evolve.evolve", return_value=[EvolutionResult(generation=1, improved=False)]),
        ):
            runpy.run_path(
                str(Path(evolve_mod.__file__).resolve()),
                run_name="__main__",
            )
