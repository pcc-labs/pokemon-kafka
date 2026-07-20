# tests/test_healer.py
"""Tests for the self-healing tuning loop (scripts/healer.py)."""

import json
import random
from unittest.mock import patch

import healer
import pytest
from autotune_bridge import load_genome_from_notes
from evolve import DEFAULT_PARAMS, PARAM_BOUNDS

# ---------------------------------------------------------------------------
# Rule evaluation
# ---------------------------------------------------------------------------


def _fitness(**kw):
    base = {
        "turns": 300,
        "battles_won": 1,
        "maps_visited": 3,
        "final_map_id": 12,
        "badges": 0,
        "party_size": 1,
        "stuck_count": 2,
        "backtrack_restores": 0,
    }
    base.update(kw)
    return base


def test_no_rules_fire_on_healthy_run():
    assert healer.evaluate_rules(_fitness()) == []


def test_navigation_thrash_fires_on_stuck_count():
    fired = healer.evaluate_rules(_fitness(stuck_count=12))
    assert [r["name"] for r in fired] == ["navigation-thrash"]


def test_navigation_thrash_fires_on_restores():
    fired = healer.evaluate_rules(_fitness(backtrack_restores=3))
    assert [r["name"] for r in fired] == ["navigation-thrash"]


def test_navigation_thrash_boundary_below():
    assert healer.evaluate_rules(_fitness(stuck_count=11, backtrack_restores=2)) == []


def test_no_progress_fires():
    fired = healer.evaluate_rules(_fitness(maps_visited=1, turns=500))
    assert [r["name"] for r in fired] == ["no-progress"]


def test_no_progress_boundary_below():
    assert healer.evaluate_rules(_fitness(maps_visited=2, turns=500)) == []
    assert healer.evaluate_rules(_fitness(maps_visited=1, turns=499)) == []


def test_both_rules_can_fire():
    fired = healer.evaluate_rules(_fitness(stuck_count=20, maps_visited=0, turns=800))
    assert [r["name"] for r in fired] == ["navigation-thrash", "no-progress"]


def test_missing_fitness_keys_treated_as_zero():
    assert healer.evaluate_rules({}) == []


# ---------------------------------------------------------------------------
# Variant sampling
# ---------------------------------------------------------------------------


def test_sample_variants_only_perturbs_implicated_params():
    rng = random.Random(7)
    base = dict(DEFAULT_PARAMS)
    variants = healer.sample_variants(base, ["door_cooldown", "stuck_threshold"], 5, rng)
    assert len(variants) == 5
    for v in variants:
        for key, val in v.items():
            if key not in ("door_cooldown", "stuck_threshold"):
                assert val == base[key]


def test_sample_variants_respects_bounds():
    rng = random.Random(11)
    for v in healer.sample_variants(dict(DEFAULT_PARAMS), ["door_cooldown", "hp_run_threshold"], 20, rng):
        lo, hi, _ = PARAM_BOUNDS["door_cooldown"]
        assert lo <= v["door_cooldown"] <= hi
        lo_f, hi_f, _ = PARAM_BOUNDS["hp_run_threshold"]
        assert lo_f <= v["hp_run_threshold"] <= hi_f


def test_sample_variants_enum_param():
    rng = random.Random(3)
    for v in healer.sample_variants(dict(DEFAULT_PARAMS), ["axis_preference_map_0"], 10, rng):
        assert v["axis_preference_map_0"] in ("x", "y")


def test_sample_variants_deterministic_for_seed():
    a = healer.sample_variants(dict(DEFAULT_PARAMS), ["door_cooldown"], 4, random.Random(42))
    b = healer.sample_variants(dict(DEFAULT_PARAMS), ["door_cooldown"], 4, random.Random(42))
    assert a == b


# ---------------------------------------------------------------------------
# Acceptance + cooldown + state
# ---------------------------------------------------------------------------


def test_decide_requires_margin_over_positive_control():
    assert healer.decide(1000, 1051) is True
    assert healer.decide(1000, 1050) is False
    assert healer.decide(1000, 900) is False


def test_decide_handles_negative_control():
    # winner must exceed control + |control| * margin: -100 + 5 = -95
    assert healer.decide(-100, -94) is True
    assert healer.decide(-100, -96) is False


def test_decide_zero_control_requires_any_improvement():
    assert healer.decide(0, 1) is True
    assert healer.decide(0, 0) is False


def test_cooldown_active_and_expired():
    state = {"last_race_at": 1000.0}
    assert healer.cooldown_active(state, now_ts=1000.0 + 5 * 3600, hours=6) is True
    assert healer.cooldown_active(state, now_ts=1000.0 + 7 * 3600, hours=6) is False


def test_cooldown_empty_state():
    assert healer.cooldown_active({}, now_ts=123.0, hours=6) is False


def test_state_round_trip_and_tolerant_load(tmp_path):
    p = tmp_path / "state.json"
    healer.save_state(p, {"last_race_at": 5.0, "races": []})
    assert healer.load_state(p) == {"last_race_at": 5.0, "races": []}
    assert healer.load_state(tmp_path / "missing.json") == {}
    bad = tmp_path / "bad.json"
    bad.write_text("{nope")
    assert healer.load_state(bad) == {}


# ---------------------------------------------------------------------------
# Genome persistence
# ---------------------------------------------------------------------------


def test_append_genome_round_trips_through_autotune_bridge(tmp_path):
    notes = tmp_path / "notes.md"
    notes.write_text('# Agent Notes\n<!-- autotune:genome\n{"door_cooldown": 12}\n-->\n')
    genome = dict(DEFAULT_PARAMS, door_cooldown=4)
    healer.append_genome(notes, genome, "Healer: navigation-thrash — door_cooldown 8→4")
    loaded = load_genome_from_notes(notes)
    assert loaded["door_cooldown"] == 4  # new block wins over the old 12
    assert "Healer: navigation-thrash" in notes.read_text()


def test_append_genome_creates_missing_notes(tmp_path):
    notes = tmp_path / "notes.md"
    healer.append_genome(notes, dict(DEFAULT_PARAMS), "Healer: no-progress")
    assert load_genome_from_notes(notes) == dict(DEFAULT_PARAMS)


# ---------------------------------------------------------------------------
# Race + seed
# ---------------------------------------------------------------------------


def test_default_seed_deterministic_per_content(tmp_path):
    f = tmp_path / "fit.json"
    f.write_text('{"stuck_count": 12}')
    first = healer.default_seed(f)
    assert first == healer.default_seed(f)
    assert isinstance(first, int)
    f.write_text('{"stuck_count": 13}')
    assert healer.default_seed(f) != first  # content change changes the seed


def test_run_race_scores_each_candidate():
    candidates = [dict(DEFAULT_PARAMS), dict(DEFAULT_PARAMS, door_cooldown=4)]
    fake_fitness = [_fitness(stuck_count=10), _fitness(stuck_count=1)]
    with patch.object(healer, "run_agent", side_effect=fake_fitness) as ra:
        results = healer.run_race("rom.gb", 800, candidates)
    assert ra.call_count == 2
    assert ra.call_args_list[0].args == ("rom.gb", 800, candidates[0])
    assert len(results) == 2
    assert results[1].score > results[0].score  # fewer stuck events scores higher
    assert results[0].params == candidates[0]


# ---------------------------------------------------------------------------
# main() / check flow
# ---------------------------------------------------------------------------


@pytest.fixture()
def check_env(tmp_path):
    fitness_file = tmp_path / "fit.json"
    fitness_file.write_text(json.dumps(_fitness(stuck_count=15)))
    notes = tmp_path / "notes.md"
    state = tmp_path / "state.json"
    argv = [
        "healer.py",
        "check",
        "--fitness",
        str(fitness_file),
        "--rom",
        "rom.gb",
        "--notes",
        str(notes),
        "--state",
        str(state),
        "--variants",
        "2",
    ]
    return tmp_path, fitness_file, notes, state, argv


def _race_results(scores):
    return [
        healer.RaceResult(params=dict(DEFAULT_PARAMS, door_cooldown=4 + i), fitness={}, score=s)
        for i, s in enumerate(scores)
    ]


def test_check_accepts_winner_and_writes_genome(check_env, capsys):
    tmp_path, fitness_file, notes, state, argv = check_env
    with patch.object(healer, "run_race", return_value=_race_results([100.0, 300.0])):
        with patch("sys.argv", argv):
            assert healer.main() == 0
    assert load_genome_from_notes(notes) != {}
    saved = healer.load_state(state)
    assert saved["races"][0]["accepted"] is True
    assert "accepted" in capsys.readouterr().out


def test_check_rejects_when_margin_not_met(check_env, capsys):
    tmp_path, fitness_file, notes, state, argv = check_env
    with patch.object(healer, "run_race", return_value=_race_results([100.0, 101.0])):
        with patch("sys.argv", argv):
            assert healer.main() == 0
    assert load_genome_from_notes(notes) == {}  # nothing written
    assert healer.load_state(state)["races"][0]["accepted"] is False
    assert "kept current genome" in capsys.readouterr().out


def test_check_healthy_run_races_nothing(check_env, capsys):
    tmp_path, fitness_file, notes, state, argv = check_env
    fitness_file.write_text(json.dumps(_fitness()))
    with patch.object(healer, "run_race") as rr:
        with patch("sys.argv", argv):
            assert healer.main() == 0
    rr.assert_not_called()
    assert "healthy" in capsys.readouterr().out


def test_check_dry_run_races_nothing(check_env, capsys):
    tmp_path, fitness_file, notes, state, argv = check_env
    with patch.object(healer, "run_race") as rr:
        with patch("sys.argv", argv + ["--dry-run"]):
            assert healer.main() == 0
    rr.assert_not_called()
    out = capsys.readouterr().out
    assert "navigation-thrash" in out and "dry-run" in out


def test_check_cooldown_blocks_race(check_env, capsys):
    tmp_path, fitness_file, notes, state, argv = check_env
    now = fitness_file.stat().st_mtime
    healer.save_state(state, {"last_race_at": now - 3600, "races": []})
    with patch.object(healer, "run_race") as rr:
        with patch("sys.argv", argv):
            assert healer.main() == 0
    rr.assert_not_called()
    assert "cooldown" in capsys.readouterr().out


def test_check_malformed_fitness_exits_zero(check_env, capsys):
    tmp_path, fitness_file, notes, state, argv = check_env
    fitness_file.write_text("{broken")
    with patch("sys.argv", argv):
        assert healer.main() == 0
    assert "unreadable fitness" in capsys.readouterr().out


def test_check_race_failure_exits_zero(check_env, capsys):
    tmp_path, fitness_file, notes, state, argv = check_env
    with patch.object(healer, "run_race", side_effect=RuntimeError("emulator exploded")):
        with patch("sys.argv", argv):
            assert healer.main() == 0
    assert "healer error" in capsys.readouterr().out


def test_module_entrypoint_exits_zero(check_env):
    import runpy

    tmp_path, fitness_file, notes, state, argv = check_env
    fitness_file.write_text(json.dumps(_fitness()))  # healthy -> no race
    with patch("sys.argv", argv):
        with pytest.raises(SystemExit) as exc:
            runpy.run_path(str(healer.__file__), run_name="__main__")
    assert exc.value.code == 0


def test_check_seed_flag_passed_to_sampling(check_env):
    tmp_path, fitness_file, notes, state, argv = check_env
    captured = {}

    def fake_race(rom, turns, candidates):
        captured["candidates"] = candidates
        return _race_results([100.0] * len(candidates))

    with patch.object(healer, "run_race", side_effect=fake_race):
        with patch("sys.argv", argv + ["--seed", "42"]):
            healer.main()
        first = captured["candidates"]
        state.unlink()  # reset cooldown so the second check actually races
        with patch("sys.argv", argv + ["--seed", "42"]):
            healer.main()
    # Control (baseline) is first, then deterministic variants.
    assert captured["candidates"] == first
    assert captured["candidates"][0] == dict(DEFAULT_PARAMS)
