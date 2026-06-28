"""Comprehensive tests for agent.py — targeting 100% line coverage."""

import importlib
import io
import json
import os
import runpy
import sys
from pathlib import Path
from unittest.mock import MagicMock, call, patch

# Import the agent module — pyboy is available in this env via deps
import agent
import pytest
from agent import (
    EARLY_GAME_TARGETS,
    MOVE_DATA,
    ROUTES_PATH,
    SCRIPT_DIR,
    TYPE_CHART_PATH,
    BacktrackManager,
    BattleStrategy,
    GameController,
    Navigator,
    PokemonAgent,
    Snapshot,
    StrategyEngine,
    load_type_chart,
    main,
)
from memory_reader import BattleState, MemoryReader, OverworldState

# ===================================================================
# Module-level import branches (lines 19-28)
# ===================================================================


class TestModuleImportBranches:
    """Cover the try/except ImportError blocks at module level."""

    def test_pyboy_import_error(self):
        """Lines 21-23: PyBoy import fails -> print + sys.exit(1)."""
        # Remove agent from sys.modules so it re-imports
        saved_modules = {}
        for mod_name in list(sys.modules):
            if mod_name == "agent" or mod_name.startswith("agent."):
                saved_modules[mod_name] = sys.modules.pop(mod_name)
        # Also remove pyboy so the import fails
        saved_pyboy = sys.modules.pop("pyboy", None)

        try:
            # Make pyboy import fail
            import builtins

            original_import = builtins.__import__

            def fail_pyboy(name, *args, **kwargs):
                if name == "pyboy":
                    raise ImportError("no pyboy")
                return original_import(name, *args, **kwargs)

            with patch.object(builtins, "__import__", side_effect=fail_pyboy):
                with pytest.raises(SystemExit) as exc_info:
                    importlib.import_module("agent")
                assert exc_info.value.code == 1
        finally:
            # Restore modules
            if saved_pyboy is not None:
                sys.modules["pyboy"] = saved_pyboy
            for mod_name, mod in saved_modules.items():
                sys.modules[mod_name] = mod

    def test_pil_import_error(self):
        """Lines 27-28: PIL import fails -> Image = None."""
        saved_modules = {}
        for mod_name in list(sys.modules):
            if mod_name == "agent" or mod_name.startswith("agent."):
                saved_modules[mod_name] = sys.modules.pop(mod_name)
        saved_pil = sys.modules.pop("PIL", None)
        saved_pil_image = sys.modules.pop("PIL.Image", None)

        try:
            import builtins

            original_import = builtins.__import__

            def fail_pil(name, *args, **kwargs):
                if name == "PIL" or name == "PIL.Image":
                    raise ImportError("no PIL")
                return original_import(name, *args, **kwargs)

            with patch.object(builtins, "__import__", side_effect=fail_pil):
                mod = importlib.import_module("agent")
                assert mod.Image is None
        finally:
            if saved_pil is not None:
                sys.modules["PIL"] = saved_pil
            if saved_pil_image is not None:
                sys.modules["PIL.Image"] = saved_pil_image
            for mod_name, mod_obj in saved_modules.items():
                sys.modules[mod_name] = mod_obj


# ===================================================================
# load_type_chart
# ===================================================================


class TestLoadTypeChart:
    """Test the JSON-loading function with file-exists and fallback paths."""

    def test_loads_from_file(self, tmp_path):
        chart_data = {"fire": {"grass": 2.0}}
        chart_file = tmp_path / "type_chart.json"
        chart_file.write_text(json.dumps(chart_data))

        with patch.object(agent, "TYPE_CHART_PATH", chart_file):
            result = load_type_chart()

        assert result == chart_data

    def test_fallback_when_file_missing(self, tmp_path):
        missing = tmp_path / "nope.json"
        with patch.object(agent, "TYPE_CHART_PATH", missing):
            result = load_type_chart()

        # The fallback dict must have the keys defined in agent.py
        assert "fire" in result
        assert "water" in result
        assert "grass" in result
        assert "electric" in result
        assert "ground" in result
        assert "ice" in result
        assert "psychic" in result
        assert "normal" in result


# ===================================================================
# GameController
# ===================================================================


class TestGameController:
    def setup_method(self):
        self.pyboy = MagicMock()
        self.ctrl = GameController(self.pyboy)

    def test_press(self):
        self.ctrl.press("a", hold_frames=5, release_frames=3)
        self.pyboy.button.assert_called_once_with("a", delay=5)
        assert self.pyboy.tick.call_count == 3

    def test_press_defaults(self):
        self.ctrl.press("b")
        self.pyboy.button.assert_called_once_with("b", delay=20)
        assert self.pyboy.tick.call_count == 10

    def test_wait(self):
        self.ctrl.wait(10)
        assert self.pyboy.tick.call_count == 10

    def test_wait_default(self):
        self.ctrl.wait()
        assert self.pyboy.tick.call_count == 30

    def test_move(self):
        self.ctrl.move("up")
        # hold_frames=8 commits exactly one tile (20 walked two, breaking odd-parity gaps)
        self.pyboy.button.assert_called_once_with("up", delay=8)
        # release_frames=8 + wait(30) = 38 ticks
        assert self.pyboy.tick.call_count == 38

    def test_mash_a(self):
        self.ctrl.mash_a(times=2, delay=10)
        assert self.pyboy.button.call_count == 2
        # Each mash_a iteration: press("a") -> 10 ticks + wait(10) -> 10 ticks = 20
        # 2 iterations = 40
        assert self.pyboy.tick.call_count == 40

    def test_mash_a_defaults(self):
        self.ctrl.mash_a()
        assert self.pyboy.button.call_count == 5

    def test_navigate_menu_down(self):
        self.ctrl.navigate_menu(target_index=2, current_index=0)
        # 2 down presses + 1 "a" press = 3 button calls
        assert self.pyboy.button.call_count == 3
        button_calls = [c[0][0] for c in self.pyboy.button.call_args_list]
        assert button_calls == ["down", "down", "a"]

    def test_navigate_menu_up(self):
        self.ctrl.navigate_menu(target_index=0, current_index=3)
        button_calls = [c[0][0] for c in self.pyboy.button.call_args_list]
        assert button_calls == ["up", "up", "up", "a"]

    def test_navigate_menu_same_index(self):
        self.ctrl.navigate_menu(target_index=0, current_index=0)
        # No direction presses, just "a"
        assert self.pyboy.button.call_count == 1
        self.pyboy.button.assert_called_with("a", delay=20)


# ===================================================================
# BattleStrategy
# ===================================================================


class TestBattleStrategy:
    def setup_method(self):
        self.chart = {
            "fire": {"grass": 2.0, "water": 0.5, "fire": 0.5},
            "water": {"fire": 2.0, "grass": 0.5},
            "normal": {"rock": 0.5, "ghost": 0.0},
        }
        self.strategy = BattleStrategy(self.chart)

    # -- constructor defaults --

    def test_default_params(self):
        assert self.strategy.hp_run_threshold == 0.1
        assert self.strategy.hp_heal_threshold == 0.25
        assert self.strategy.unknown_move_score == 10.0
        assert self.strategy.status_move_score == 1.0

    def test_custom_params(self):
        s = BattleStrategy(
            self.chart,
            hp_run_threshold=0.05,
            hp_heal_threshold=0.4,
            unknown_move_score=20.0,
            status_move_score=5.0,
        )
        assert s.hp_run_threshold == 0.05
        assert s.hp_heal_threshold == 0.4
        assert s.unknown_move_score == 20.0
        assert s.status_move_score == 5.0

    # -- score_move --

    def test_score_move_no_pp(self):
        assert self.strategy.score_move(0x01, 0) == -1.0

    def test_score_move_negative_pp(self):
        assert self.strategy.score_move(0x01, -5) == -1.0

    def test_score_move_unknown_move(self):
        assert self.strategy.score_move(0xFF, 10) == 10.0

    def test_score_move_unknown_move_custom(self):
        s = BattleStrategy(self.chart, unknown_move_score=20.0)
        assert s.score_move(0xFF, 10) == 20.0

    def test_score_move_status_move(self):
        # Thunder Wave: power=0
        assert self.strategy.score_move(0x56, 10) == 1.0

    def test_score_move_status_move_custom(self):
        s = BattleStrategy(self.chart, status_move_score=5.0)
        assert s.score_move(0x56, 10) == 5.0

    def test_score_move_no_move(self):
        # 0x00 = "(No move)", power=0, accuracy=0
        assert self.strategy.score_move(0x00, 10) == 1.0

    def test_score_move_normal_effectiveness(self):
        # Pound: 40 power, 100 acc, "normal" type vs "normal" enemy
        score = self.strategy.score_move(0x01, 10, "normal")
        assert score == 40 * 1.0 * 1.0  # 40.0

    def test_score_move_super_effective(self):
        # Ember (fire) vs grass: 40 * 1.0 * 2.0 = 80
        score = self.strategy.score_move(0x2D, 10, "grass")
        assert score == 80.0

    def test_score_move_not_very_effective(self):
        # Ember (fire) vs water: 40 * 1.0 * 0.5 = 20
        score = self.strategy.score_move(0x2D, 10, "water")
        assert score == 20.0

    def test_score_move_type_not_in_chart(self):
        # Psychic type not in our chart -> effectiveness = 1.0
        score = self.strategy.score_move(0x5D, 10, "normal")
        assert score == 90 * 1.0 * 1.0

    def test_score_move_enemy_not_in_chart_entry(self):
        # Ember (fire) vs "dragon" -- "dragon" not in fire's chart entry -> 1.0
        score = self.strategy.score_move(0x2D, 10, "dragon")
        assert score == 40 * 1.0 * 1.0

    def test_score_move_accuracy_factor(self):
        # Tackle: 35 power, 95 acc
        score = self.strategy.score_move(0x21, 10, "normal")
        assert score == pytest.approx(35 * 0.95 * 1.0)

    # -- choose_action --

    def _make_battle(self, **kwargs):
        defaults = {
            "battle_type": 1,
            "player_hp": 100,
            "player_max_hp": 100,
            "enemy_hp": 50,
            "enemy_max_hp": 50,
            "moves": [0x01, 0x2D, 0x00, 0x00],
            "move_pp": [10, 10, 0, 0],
        }
        defaults.update(kwargs)
        return BattleState(**defaults)

    def test_choose_action_run_when_low_hp_wild(self):
        battle = self._make_battle(player_hp=5, player_max_hp=100, battle_type=1)
        action = self.strategy.choose_action(battle)
        assert action == {"action": "run"}

    def test_choose_action_item_when_low_hp_trainer(self):
        # hp_ratio = 0.20 -- not < 0.2, so run won't trigger; but 0.20 < 0.25 -> item
        battle = self._make_battle(player_hp=20, player_max_hp=100, battle_type=2)
        action = self.strategy.choose_action(battle, bag_healing=(0, 0x14))
        assert action == {"action": "item", "item": "Potion", "bag_index": 0}

    def test_choose_action_item_when_low_hp_wild_above_run_threshold(self):
        # hp_ratio = 0.24 -- above 0.2, below 0.25 -> item
        battle = self._make_battle(player_hp=24, player_max_hp=100, battle_type=1)
        action = self.strategy.choose_action(battle, bag_healing=(2, 0x19))
        assert action == {"action": "item", "item": "Super Potion", "bag_index": 2}

    def test_choose_action_no_item_when_no_bag_healing(self):
        """Low HP but no healing items -> fall through to fight."""
        battle = self._make_battle(player_hp=20, player_max_hp=100, battle_type=2)
        action = self.strategy.choose_action(battle, bag_healing=None)
        assert action["action"] == "fight"

    def test_choose_action_fight_best_move(self):
        battle = self._make_battle(
            moves=[0x01, 0x2D, 0x00, 0x00],
            move_pp=[10, 10, 0, 0],
        )
        action = self.strategy.choose_action(battle)
        assert action["action"] == "fight"
        assert action["move_index"] in (0, 1)

    def test_choose_action_all_no_pp(self):
        # All moves have 0 PP -> all scores < 0 -> fallback fight index 0
        battle = self._make_battle(
            moves=[0x01, 0x2D, 0x21, 0x37],
            move_pp=[0, 0, 0, 0],
        )
        action = self.strategy.choose_action(battle)
        assert action == {"action": "fight", "move_index": 0}

    def test_choose_action_all_empty_moves(self):
        # All moves are 0x00 -- filtered out, moves list empty -> fallback
        battle = self._make_battle(
            moves=[0x00, 0x00, 0x00, 0x00],
            move_pp=[10, 10, 10, 10],
        )
        action = self.strategy.choose_action(battle)
        assert action == {"action": "fight", "move_index": 0}

    def test_choose_action_max_hp_zero(self):
        # max_hp = 0 -> max(0, 1) = 1, hp_ratio = 0/1 = 0 -> run (wild, 0 < 0.1)
        battle = self._make_battle(player_hp=0, player_max_hp=0, battle_type=1)
        action = self.strategy.choose_action(battle)
        assert action == {"action": "run"}

    def test_choose_action_custom_run_threshold(self):
        # With hp_run_threshold=0.4, hp_ratio=0.35 should trigger run
        s = BattleStrategy(self.chart, hp_run_threshold=0.4)
        battle = self._make_battle(player_hp=35, player_max_hp=100, battle_type=1)
        action = s.choose_action(battle)
        assert action == {"action": "run"}

    def test_choose_action_custom_heal_threshold(self):
        # With hp_heal_threshold=0.5, hp_ratio=0.45 (above default run 0.2) triggers heal
        s = BattleStrategy(self.chart, hp_heal_threshold=0.5)
        battle = self._make_battle(player_hp=45, player_max_hp=100, battle_type=2)
        action = s.choose_action(battle, bag_healing=(0, 0x14))
        assert action == {"action": "item", "item": "Potion", "bag_index": 0}

    def test_run_attempts_fallback_after_3(self):
        """After 3 failed run attempts, strategy stops returning run."""
        battle = self._make_battle(player_hp=5, player_max_hp=100, battle_type=1)
        # First 3 attempts return run
        for _ in range(3):
            action = self.strategy.choose_action(battle)
            assert action == {"action": "run"}
        # 4th attempt falls through — no bag_healing so fights
        action = self.strategy.choose_action(battle)
        assert action["action"] == "fight"

    def test_run_attempts_reset(self):
        """Resetting _run_attempts allows running again."""
        battle = self._make_battle(player_hp=5, player_max_hp=100, battle_type=1)
        for _ in range(3):
            self.strategy.choose_action(battle)
        # Reset as the main loop does when a battle ends
        self.strategy._run_attempts = 0
        action = self.strategy.choose_action(battle)
        assert action == {"action": "run"}

    # -- fight-first: high HP always fights, even in wild battles --

    def test_fight_first_high_hp_wild(self):
        """At full HP in a wild battle, the agent fights (not run)."""
        battle = self._make_battle(player_hp=100, player_max_hp=100, battle_type=1)
        action = self.strategy.choose_action(battle)
        assert action["action"] == "fight"

    def test_fight_first_moderate_hp_wild_with_healing(self):
        """At 15% HP with healing items, the agent heals."""
        battle = self._make_battle(player_hp=15, player_max_hp=100, battle_type=1)
        action = self.strategy.choose_action(battle, bag_healing=(0, 0x14))
        assert action["action"] == "item"

    def test_fight_first_moderate_hp_wild_no_healing(self):
        """At 15% HP without healing items, the agent fights."""
        battle = self._make_battle(player_hp=15, player_max_hp=100, battle_type=1)
        action = self.strategy.choose_action(battle, bag_healing=None)
        assert action["action"] == "fight"

    # -- type-aware scoring via choose_action --

    def test_choose_action_uses_enemy_type(self):
        """choose_action passes enemy_type_name to score_move for effectiveness."""
        # Ember (fire) vs grass enemy -> super effective
        battle = self._make_battle(
            moves=[0x2D, 0x01, 0x00, 0x00],  # Ember, Pound
            move_pp=[10, 10, 0, 0],
            enemy_type1=0x17,  # grass
        )
        action = self.strategy.choose_action(battle)
        assert action["action"] == "fight"
        assert action["move_index"] == 0  # Ember is super effective vs grass

    def test_choose_action_prefers_effective_move(self):
        """When enemy type makes one move clearly better, it's chosen."""
        # Water Gun (water) vs fire enemy -> super effective; Pound (normal) -> neutral
        battle = self._make_battle(
            moves=[0x01, 0x37, 0x00, 0x00],  # Pound, Water Gun
            move_pp=[10, 10, 0, 0],
            enemy_type1=0x14,  # fire
        )
        action = self.strategy.choose_action(battle)
        assert action["action"] == "fight"
        assert action["move_index"] == 1  # Water Gun super effective vs fire


# ===================================================================
# Navigator
# ===================================================================


class TestNavigator:
    # -- _add_direction --

    def test_add_direction_appends(self):
        nav = Navigator({})
        dirs = []
        nav._add_direction(dirs, "up")
        assert dirs == ["up"]

    def test_add_direction_no_duplicates(self):
        nav = Navigator({})
        dirs = ["up"]
        nav._add_direction(dirs, "up")
        assert dirs == ["up"]

    def test_add_direction_none_ignored(self):
        nav = Navigator({})
        dirs = []
        nav._add_direction(dirs, None)
        assert dirs == []

    # -- _direction_toward_target --

    def test_direction_at_target_returns_none(self):
        """When at target with stuck < 8, no fallback directions are added."""
        nav = Navigator({})
        state = OverworldState(x=5, y=5)
        result = nav._direction_toward_target(state, 5, 5)
        assert result is None

    def test_direction_at_target_stuck_returns_cardinal(self):
        """When at target but stuck >= 8, fallback directions are added."""
        nav = Navigator({})
        state = OverworldState(x=5, y=5)
        result = nav._direction_toward_target(state, 5, 5, stuck_turns=8)
        assert result == "up"

    def test_direction_toward_target_empty_ordered(self):
        """Line 246-247: defensive branch where ordered list is empty.
        Must mock _add_direction to be a no-op so ordered stays empty."""
        nav = Navigator({})
        state = OverworldState(x=5, y=5)
        with patch.object(nav, "_add_direction"):
            result = nav._direction_toward_target(state, 5, 5)
        assert result is None

    def test_direction_x_preference(self):
        nav = Navigator({})
        state = OverworldState(x=3, y=3)
        result = nav._direction_toward_target(state, 5, 5, axis_preference="x")
        assert result == "right"

    def test_direction_y_preference(self):
        nav = Navigator({})
        state = OverworldState(x=3, y=3)
        result = nav._direction_toward_target(state, 5, 5, axis_preference="y")
        assert result == "down"

    def test_direction_left_up(self):
        nav = Navigator({})
        state = OverworldState(x=5, y=5)
        result = nav._direction_toward_target(state, 3, 3, axis_preference="x")
        assert result == "left"

    def test_direction_stuck_rotates(self):
        nav = Navigator({})
        state = OverworldState(x=3, y=3)
        # Target at 5,5, x-pref: ordered = [right, down, up, left]
        r0 = nav._direction_toward_target(state, 5, 5, stuck_turns=0)
        r1 = nav._direction_toward_target(state, 5, 5, stuck_turns=1)
        assert r0 == "right"
        assert r1 == "down"

    def test_direction_only_horizontal(self):
        nav = Navigator({})
        state = OverworldState(x=3, y=5)
        result = nav._direction_toward_target(state, 5, 5, axis_preference="x")
        assert result == "right"

    def test_direction_only_vertical(self):
        nav = Navigator({})
        state = OverworldState(x=5, y=3)
        result = nav._direction_toward_target(state, 5, 5, axis_preference="y")
        assert result == "down"

    def test_direction_random_jitter_at_stuck_20(self):
        """At stuck_turns >= 20, direction is random from all four cardinals."""
        nav = Navigator({})
        state = OverworldState(x=3, y=3)
        results = {nav._direction_toward_target(state, 5, 5, stuck_turns=20) for _ in range(50)}
        assert results <= {"up", "down", "left", "right"}
        assert len(results) > 1  # not deterministic

    # -- next_direction --

    def test_next_direction_early_game_target(self):
        nav = Navigator({})
        # Map 38 = Red's bedroom, target (7, 1)
        state = OverworldState(map_id=38, x=3, y=3)
        result = nav.next_direction(state)
        assert result == "right"  # x-preference towards x=7

    def test_next_direction_map_change_resets_waypoint(self):
        nav = Navigator({"10": [{"x": 5, "y": 5}]})
        nav.current_map = "9"
        nav.current_waypoint = 3
        state = OverworldState(map_id=10, x=3, y=3)
        nav.next_direction(state)
        assert nav.current_map == "10"
        assert nav.current_waypoint == 0

    def test_next_direction_no_route_cycles(self):
        nav = Navigator({})
        state = OverworldState(map_id=99, x=5, y=5)
        directions = ["down", "right", "down", "left", "up", "down"]
        for turn in range(6):
            result = nav.next_direction(state, turn=turn)
            assert result == directions[turn % 6]

    def test_next_direction_route_dict_with_waypoints(self):
        routes = {
            "10": {
                "name": "Test Route",
                "waypoints": [
                    {"x": 5, "y": 5},
                    {"x": 10, "y": 10},
                ],
            }
        }
        nav = Navigator(routes)
        state = OverworldState(map_id=10, x=3, y=3)
        result = nav.next_direction(state)
        assert result in ("right", "down")

    def test_next_direction_route_raw_list(self):
        """Line 276: waypoints = route (the else branch when route is a raw list)."""
        routes = {
            "10": [
                {"x": 5, "y": 5},
                {"x": 10, "y": 10},
            ]
        }
        nav = Navigator(routes)
        state = OverworldState(map_id=10, x=3, y=3)
        result = nav.next_direction(state)
        assert result in ("right", "down")

    def test_next_direction_route_complete(self):
        routes = {"10": [{"x": 5, "y": 5}]}
        nav = Navigator(routes)
        nav.current_map = "10"
        nav.current_waypoint = 1  # past the end
        state = OverworldState(map_id=10, x=5, y=5)
        result = nav.next_direction(state)
        assert result is None

    def test_next_direction_loop_route_wraps(self):
        """Routes with loop=true reset waypoint index instead of returning None."""
        routes = {"10": {"loop": True, "waypoints": [{"x": 5, "y": 5}, {"x": 10, "y": 10}]}}
        nav = Navigator(routes)
        nav.current_map = "10"
        nav.current_waypoint = 2  # past the end
        state = OverworldState(map_id=10, x=0, y=0)
        result = nav.next_direction(state)
        assert result is not None
        assert nav.current_waypoint == 0

    def test_next_direction_waypoint_reached_advances(self):
        """When at a waypoint, the navigator advances and recurses."""
        routes = {
            "10": [
                {"x": 5, "y": 5},
                {"x": 10, "y": 10},
            ]
        }
        nav = Navigator(routes)
        state = OverworldState(map_id=10, x=5, y=5)
        result = nav.next_direction(state)
        assert nav.current_waypoint == 1
        assert result in ("right", "down")

    def test_next_direction_waypoint_reached_last_returns_none(self):
        """When at the final waypoint, advancing makes route complete -> None."""
        routes = {"10": [{"x": 5, "y": 5}]}
        nav = Navigator(routes)
        state = OverworldState(map_id=10, x=5, y=5)
        result = nav.next_direction(state)
        assert result is None
        assert nav.current_waypoint == 1


# ===================================================================
# PokemonAgent -- helper to build one with mocks
# ===================================================================


def _make_agent(tmp_path, screenshots=False, routes=None, type_chart_data=None):
    """Build a PokemonAgent with all external I/O mocked."""
    from collections import defaultdict

    mock_pb = MagicMock()
    # Use a defaultdict(int) for pyboy.memory so that memory[addr] returns 0
    # instead of a MagicMock. This prevents TypeError when format strings like
    # {val:02X} are used on memory read results.
    mock_pb.memory = defaultdict(int)

    tc_path = tmp_path / "tc.json"
    if type_chart_data:
        tc_path.write_text(json.dumps(type_chart_data))

    rp = tmp_path / "routes.json"
    if routes is not None:
        rp.write_text(json.dumps(routes))

    pokedex_dir = tmp_path / "pokedex"
    frames_dir = tmp_path / "frames"

    with (
        patch("agent.PyBoy", return_value=mock_pb),
        patch.object(agent, "TYPE_CHART_PATH", tc_path),
        patch.object(agent, "ROUTES_PATH", rp),
        patch.object(agent, "SCRIPT_DIR", tmp_path),
    ):
        ag = PokemonAgent(
            str(tmp_path / "fake.gb"),
            strategy="low",
            screenshots=screenshots,
        )

    # Override dirs to use tmp_path
    ag.pokedex_dir = pokedex_dir
    ag.pokedex_dir.mkdir(parents=True, exist_ok=True)
    ag.frames_dir = frames_dir
    if screenshots:
        ag.frames_dir.mkdir(parents=True, exist_ok=True)

    return ag


# ===================================================================
# BacktrackManager tests
# ===================================================================


class TestBacktrackManager:
    """Tests for Snapshot dataclass and BacktrackManager."""

    def test_snapshot_defaults(self):
        buf = io.BytesIO(b"state")
        snap = Snapshot(state_bytes=buf, map_id=1, x=5, y=10, turn=42)
        assert snap.attempts == 0
        assert snap.map_id == 1
        assert snap.turn == 42

    def test_init_defaults(self):
        bm = BacktrackManager()
        assert bm.max_snapshots == 8
        assert bm.restore_threshold == 15
        assert bm.max_attempts == 3
        assert bm.total_restores == 0
        assert len(bm.snapshots) == 0

    def test_init_custom(self):
        bm = BacktrackManager(max_snapshots=4, restore_threshold=10, max_attempts=5)
        assert bm.max_snapshots == 4
        assert bm.restore_threshold == 10
        assert bm.max_attempts == 5

    def test_save_snapshot(self):
        bm = BacktrackManager(max_snapshots=3)
        mock_pyboy = MagicMock()
        state = OverworldState(map_id=1, x=5, y=10)

        bm.save_snapshot(mock_pyboy, state, turn=10)
        assert len(bm.snapshots) == 1
        assert bm.snapshots[0].map_id == 1
        assert bm.snapshots[0].x == 5
        assert bm.snapshots[0].y == 10
        assert bm.snapshots[0].turn == 10
        mock_pyboy.save_state.assert_called_once()

    def test_save_snapshot_deque_bounds(self):
        bm = BacktrackManager(max_snapshots=2)
        mock_pyboy = MagicMock()
        for i in range(5):
            state = OverworldState(map_id=i, x=i, y=i)
            bm.save_snapshot(mock_pyboy, state, turn=i)
        assert len(bm.snapshots) == 2
        # Oldest snapshots should have been evicted
        assert bm.snapshots[0].map_id == 3
        assert bm.snapshots[1].map_id == 4

    def test_should_restore_below_threshold(self):
        bm = BacktrackManager(restore_threshold=15)
        mock_pyboy = MagicMock()
        bm.save_snapshot(mock_pyboy, OverworldState(map_id=0, x=0, y=0), turn=0)
        assert bm.should_restore(14) is False

    def test_should_restore_no_snapshots(self):
        bm = BacktrackManager(restore_threshold=5)
        assert bm.should_restore(10) is False

    def test_should_restore_all_exhausted(self):
        bm = BacktrackManager(restore_threshold=5, max_attempts=1)
        snap = Snapshot(io.BytesIO(b"x"), map_id=0, x=0, y=0, turn=0, attempts=1)
        bm.snapshots.append(snap)
        assert bm.should_restore(10) is False

    def test_should_restore_viable(self):
        bm = BacktrackManager(restore_threshold=5, max_attempts=3)
        mock_pyboy = MagicMock()
        bm.save_snapshot(mock_pyboy, OverworldState(map_id=0, x=0, y=0), turn=0)
        assert bm.should_restore(5) is True

    def test_restore_loads_state(self):
        bm = BacktrackManager(max_attempts=3)
        mock_pyboy = MagicMock()
        bm.save_snapshot(mock_pyboy, OverworldState(map_id=1, x=3, y=7), turn=20)

        snap = bm.restore(mock_pyboy)
        assert snap is not None
        assert snap.map_id == 1
        assert snap.x == 3
        assert snap.y == 7
        assert snap.turn == 20
        assert snap.attempts == 1
        assert bm.total_restores == 1
        mock_pyboy.load_state.assert_called_once()

    def test_restore_keeps_snapshot_if_attempts_remain(self):
        bm = BacktrackManager(max_attempts=3)
        mock_pyboy = MagicMock()
        bm.save_snapshot(mock_pyboy, OverworldState(map_id=1, x=0, y=0), turn=10)

        bm.restore(mock_pyboy)
        # Snapshot re-appended with attempts=1
        assert len(bm.snapshots) == 1
        assert bm.snapshots[0].attempts == 1

    def test_restore_removes_snapshot_at_max_attempts(self):
        bm = BacktrackManager(max_attempts=1)
        mock_pyboy = MagicMock()
        bm.save_snapshot(mock_pyboy, OverworldState(map_id=1, x=0, y=0), turn=10)

        snap = bm.restore(mock_pyboy)
        assert snap is not None
        assert snap.attempts == 1
        # Not re-appended since attempts == max_attempts
        assert len(bm.snapshots) == 0

    def test_restore_none_when_all_exhausted(self):
        bm = BacktrackManager(max_attempts=1)
        snap = Snapshot(io.BytesIO(b"x"), map_id=0, x=0, y=0, turn=0, attempts=1)
        bm.snapshots.append(snap)

        mock_pyboy = MagicMock()
        result = bm.restore(mock_pyboy)
        assert result is None
        assert bm.total_restores == 0

    def test_restore_picks_most_recent_viable(self):
        bm = BacktrackManager(max_attempts=2)
        # First snapshot exhausted
        exhausted = Snapshot(io.BytesIO(b"old"), map_id=0, x=0, y=0, turn=5, attempts=2)
        bm.snapshots.append(exhausted)
        # Second snapshot viable
        mock_pyboy = MagicMock()
        bm.save_snapshot(mock_pyboy, OverworldState(map_id=1, x=3, y=3), turn=15)

        snap = bm.restore(mock_pyboy)
        assert snap is not None
        assert snap.map_id == 1
        assert snap.turn == 15

    def test_total_restores_accumulates(self):
        bm = BacktrackManager(max_attempts=5)
        mock_pyboy = MagicMock()
        bm.save_snapshot(mock_pyboy, OverworldState(map_id=0, x=0, y=0), turn=0)

        bm.restore(mock_pyboy)
        bm.restore(mock_pyboy)
        assert bm.total_restores == 2


class TestBacktrackIntegration:
    """Test BacktrackManager integration with PokemonAgent."""

    def test_agent_has_backtrack_manager(self, tmp_path):
        ag = _make_agent(tmp_path)
        assert hasattr(ag, "backtrack")
        assert isinstance(ag.backtrack, BacktrackManager)

    def test_agent_backtrack_defaults(self, tmp_path):
        ag = _make_agent(tmp_path)
        assert ag.backtrack.max_snapshots == 8
        assert ag.backtrack.restore_threshold == 15
        assert ag.backtrack.max_attempts == 3
        assert ag._bt_snapshot_interval == 50

    def test_evolve_params_flow_to_backtrack(self, tmp_path):
        params = {
            "stuck_threshold": 8,
            "door_cooldown": 8,
            "waypoint_skip_distance": 3,
            "axis_preference_map_0": "y",
            "bt_max_snapshots": 4,
            "bt_restore_threshold": 10,
            "bt_max_attempts": 5,
            "bt_snapshot_interval": 25,
        }
        ag = _make_agent_with_evolve(tmp_path, evolve_params=params)
        assert ag.backtrack.max_snapshots == 4
        assert ag.backtrack.restore_threshold == 10
        assert ag.backtrack.max_attempts == 5
        assert ag._bt_snapshot_interval == 25

    def test_snapshot_on_map_change(self, tmp_path):
        ag = _make_agent(tmp_path)
        state1 = OverworldState(map_id=0, x=5, y=5)
        state2 = OverworldState(map_id=1, x=3, y=3)

        ag.memory.read_overworld_state = MagicMock(return_value=state1)
        ag._bt_last_map_id = 0  # set previous map
        ag.run_overworld()

        # No map change yet
        initial_count = len(ag.backtrack.snapshots)

        ag._bt_last_map_id = 0
        ag.memory.read_overworld_state = MagicMock(return_value=state2)
        ag.run_overworld()

        # Map changed from 0 -> 1, should have saved a snapshot
        assert len(ag.backtrack.snapshots) > initial_count

    def test_periodic_snapshot(self, tmp_path):
        ag = _make_agent(tmp_path)
        ag._bt_snapshot_interval = 5
        state = OverworldState(map_id=0, x=5, y=5)
        ag.memory.read_overworld_state = MagicMock(return_value=state)
        ag._bt_last_map_id = 0
        ag.stuck_turns = 0

        # Run until turn_count hits the interval
        for _ in range(6):
            ag.turn_count += 1
            if ag.turn_count % ag._bt_snapshot_interval == 0 and ag.stuck_turns == 0:
                ag.backtrack.save_snapshot(ag.pyboy, state, ag.turn_count)

        assert len(ag.backtrack.snapshots) == 1

    def test_restore_on_stuck(self, tmp_path):
        ag = _make_agent(tmp_path)
        ag.backtrack.restore_threshold = 3
        state = OverworldState(map_id=0, x=5, y=5)
        ag.memory.read_overworld_state = MagicMock(return_value=state)

        # Save a snapshot manually
        ag.backtrack.save_snapshot(ag.pyboy, state, turn=0)
        ag._bt_last_map_id = 0

        # Simulate being stuck
        ag.stuck_turns = 3
        ag.run_overworld()

        # Should have restored
        assert ag.backtrack.total_restores == 1
        assert ag.stuck_turns == 0

    def test_force_restore_on_progress_stall(self, tmp_path):
        # When no meaningful progress for >500 turns (and not in Oak's Lab),
        # run_overworld forces a backtrack restore even if not "stuck".
        ag = _make_agent(tmp_path)
        state = OverworldState(map_id=0, x=5, y=5)
        ag.memory.read_overworld_state = MagicMock(return_value=state)
        ag.backtrack.save_snapshot(ag.pyboy, state, turn=0)
        ag._bt_last_map_id = 0

        # 600-turn progress gap, but not stuck (so only the stall path fires)
        ag.turn_count = 600
        ag._last_progress_turn = 0
        ag.stuck_turns = 0

        ag.run_overworld()

        stall_events = [e for e in ag.events if "PROGRESS STALL" in e]
        assert len(stall_events) == 1
        assert ag.backtrack.total_restores == 1
        assert ag.stuck_turns == 0
        # progress watermark advances to the current turn after the restore
        assert ag._last_progress_turn == 600

    def test_compute_fitness_includes_backtrack_restores(self, tmp_path):
        ag = _make_agent(tmp_path)
        ag.backtrack.total_restores = 7
        ag.memory.read_overworld_state = MagicMock(return_value=OverworldState(map_id=0, x=0, y=0))
        fitness = ag.compute_fitness()
        assert fitness["backtrack_restores"] == 7

    def test_backtrack_event_logged(self, tmp_path):
        ag = _make_agent(tmp_path)
        ag.backtrack.restore_threshold = 1
        state = OverworldState(map_id=0, x=5, y=5)
        ag.memory.read_overworld_state = MagicMock(return_value=state)
        ag.backtrack.save_snapshot(ag.pyboy, state, turn=0)
        ag._bt_last_map_id = 0
        ag.stuck_turns = 1

        ag.run_overworld()

        backtrack_events = [e for e in ag.events if "BACKTRACK" in e]
        assert len(backtrack_events) == 1

    def test_restore_resets_script_gate_flags(self, tmp_path):
        ag = _make_agent(tmp_path)
        ag.backtrack.restore_threshold = 1
        state = OverworldState(map_id=0, x=5, y=5)
        ag.memory.read_overworld_state = MagicMock(return_value=state)
        ag.backtrack.save_snapshot(ag.pyboy, state, turn=0)
        ag._bt_last_map_id = 0
        ag.stuck_turns = 1

        # Set flags that should be cleared on restore
        ag._oak_wait_done = True
        ag._pallet_diag_done = True
        ag._house_diag_done = True

        ag.run_overworld()

        assert not hasattr(ag, "_oak_wait_done")
        assert not hasattr(ag, "_pallet_diag_done")
        assert not hasattr(ag, "_house_diag_done")

    def test_backtrack_skipped_in_oaks_lab(self, tmp_path):
        """Backtrack should NOT trigger in Oak's Lab (map 40) at all."""
        ag = _make_agent(tmp_path)
        ag.backtrack.restore_threshold = 1
        state = OverworldState(map_id=40, party_count=0, x=5, y=3)
        ag.memory.read_overworld_state = MagicMock(return_value=state)
        ag.backtrack.save_snapshot(ag.pyboy, state, turn=0)
        ag._bt_last_map_id = 40
        ag.stuck_turns = 5  # well above threshold

        with patch.object(agent, "Image", None):
            ag.run_overworld()

        # Should NOT have restored despite being stuck
        assert ag.backtrack.total_restores == 0

    def test_backtrack_skipped_in_oaks_lab_with_party(self, tmp_path):
        """Backtrack should NOT trigger in Oak's Lab even after getting Pokemon."""
        ag = _make_agent(tmp_path)
        ag.backtrack.restore_threshold = 1
        state = OverworldState(map_id=40, party_count=1, x=7, y=5)
        ag.memory.read_overworld_state = MagicMock(return_value=state)
        ag.backtrack.save_snapshot(ag.pyboy, state, turn=0)
        ag._bt_last_map_id = 40
        ag.stuck_turns = 5

        with patch.object(agent, "Image", None):
            ag.run_overworld()

        assert ag.backtrack.total_restores == 0

    def test_periodic_snapshot_skips_duplicate_position(self, tmp_path):
        ag = _make_agent(tmp_path)
        ag._bt_snapshot_interval = 1  # every turn
        state = OverworldState(map_id=0, x=5, y=5)
        ag.memory.read_overworld_state = MagicMock(return_value=state)
        ag._bt_last_map_id = 0
        ag.stuck_turns = 0

        # First overworld call at turn 1 should snapshot
        ag.turn_count = 1
        ag.run_overworld()
        assert len(ag.backtrack.snapshots) == 1

        # Second call at same position should NOT add another
        ag.turn_count = 2
        ag.stuck_turns = 0
        ag.run_overworld()
        assert len(ag.backtrack.snapshots) == 1


# ===================================================================
# StrategyEngine tests
# ===================================================================


class TestStrategyEngine:
    def test_low_tier_no_notes(self):
        engine = StrategyEngine("low")
        assert engine.tier == "low"
        assert engine.notes is None

    def test_medium_tier_has_notes(self, tmp_path):
        engine = StrategyEngine("medium", notes_path=str(tmp_path / "notes.md"))
        assert engine.tier == "medium"
        assert engine.notes is not None

    def test_high_tier_has_notes(self, tmp_path):
        engine = StrategyEngine("high", notes_path=str(tmp_path / "notes.md"))
        assert engine.tier == "high"
        assert engine.notes is not None

    def test_medium_no_notes_path(self):
        engine = StrategyEngine("medium")
        assert engine.notes is None

    def test_should_call_llm_low_never(self):
        engine = StrategyEngine("low")
        assert engine.should_call_llm(stuck_turns=100, map_changed=True) is False

    def test_should_call_llm_medium_when_stuck(self):
        engine = StrategyEngine("medium")
        assert engine.should_call_llm(stuck_turns=10, map_changed=False) is True

    def test_should_call_llm_medium_on_map_change(self):
        engine = StrategyEngine("medium")
        assert engine.should_call_llm(stuck_turns=0, map_changed=True) is True

    def test_should_call_llm_medium_not_stuck(self):
        engine = StrategyEngine("medium")
        assert engine.should_call_llm(stuck_turns=5, map_changed=False) is False

    def test_should_call_llm_high_always(self):
        engine = StrategyEngine("high")
        assert engine.should_call_llm(stuck_turns=0, map_changed=False) is True


# ===================================================================
# PokemonAgent tests
# ===================================================================


class TestPokemonAgentInit:
    def test_init_without_screenshots(self, tmp_path):
        ag = _make_agent(tmp_path, screenshots=False)
        assert ag.screenshots is False
        assert ag.turn_count == 0
        assert ag.battles_won == 0
        assert ag.stuck_turns == 0
        assert ag.events == []
        assert ag.last_overworld_state is None

    def test_init_with_screenshots(self, tmp_path):
        ag = _make_agent(tmp_path, screenshots=True)
        assert ag.screenshots is True
        assert ag.frames_dir.exists()

    def test_init_loads_routes(self, tmp_path):
        routes = {"12": [{"x": 5, "y": 33}]}
        ag = _make_agent(tmp_path, routes=routes)
        assert ag.navigator.routes == routes

    def test_init_no_routes_file(self, tmp_path):
        ag = _make_agent(tmp_path)
        assert ag.navigator.routes == {}

    def test_init_with_type_chart(self, tmp_path):
        chart = {"fire": {"grass": 2.0}}
        ag = _make_agent(tmp_path, type_chart_data=chart)
        assert ag.type_chart == chart


class TestUpdateOverworldProgress:
    def test_first_call(self, tmp_path):
        ag = _make_agent(tmp_path)
        state = OverworldState(map_id=0, x=5, y=5)
        ag.update_overworld_progress(state)
        assert (0, 5, 5) in ag.recent_positions
        assert 0 in ag.maps_visited

    def test_map_change(self, tmp_path):
        ag = _make_agent(tmp_path)
        old = OverworldState(map_id=0, x=5, y=5)
        ag.last_overworld_state = old
        ag.recent_positions = [(0, 5, 5)]
        ag.stuck_turns = 3

        new = OverworldState(map_id=12, x=5, y=33)
        ag.update_overworld_progress(new)

        assert ag.stuck_turns == 0
        assert ag.recent_positions == [(12, 5, 33)]
        assert 12 in ag.maps_visited
        assert any("MAP CHANGE" in e for e in ag.events)

    def test_oscillation_increments_stuck(self, tmp_path):
        ag = _make_agent(tmp_path)
        old = OverworldState(map_id=0, x=5, y=5)
        ag.last_overworld_state = old
        ag.recent_positions = [(0, 5, 5)]

        state = OverworldState(map_id=0, x=5, y=5)
        ag.update_overworld_progress(state)
        assert ag.stuck_turns == 1

    def test_no_oscillation_resets_stuck(self, tmp_path):
        ag = _make_agent(tmp_path)
        old = OverworldState(map_id=0, x=5, y=5)
        ag.last_overworld_state = old
        ag.recent_positions = [(0, 5, 5)]
        ag.stuck_turns = 3

        state = OverworldState(map_id=0, x=6, y=5)
        ag.update_overworld_progress(state)
        assert ag.stuck_turns == 0

    def test_recent_positions_capped_at_16(self, tmp_path):
        ag = _make_agent(tmp_path)
        old = OverworldState(map_id=0, x=0, y=0)
        ag.last_overworld_state = old
        ag.recent_positions = [(0, i, 0) for i in range(16)]

        state = OverworldState(map_id=0, x=99, y=0)
        ag.update_overworld_progress(state)
        assert len(ag.recent_positions) == 16

    def test_stuck_log_at_2(self, tmp_path):
        ag = _make_agent(tmp_path)
        old = OverworldState(map_id=0, x=5, y=5)
        ag.last_overworld_state = old
        ag.recent_positions = [(0, 5, 5)]
        ag.stuck_turns = 1

        state = OverworldState(map_id=0, x=5, y=5)
        ag.update_overworld_progress(state)
        assert ag.stuck_turns == 2
        assert any("STUCK" in e for e in ag.events)

    def test_stuck_log_at_5(self, tmp_path):
        ag = _make_agent(tmp_path)
        old = OverworldState(map_id=0, x=5, y=5)
        ag.last_overworld_state = old
        ag.recent_positions = [(0, 5, 5)]
        ag.stuck_turns = 4

        state = OverworldState(map_id=0, x=5, y=5)
        ag.update_overworld_progress(state)
        assert ag.stuck_turns == 5
        assert any("STUCK" in e for e in ag.events)

    def test_stuck_log_at_10(self, tmp_path):
        ag = _make_agent(tmp_path)
        old = OverworldState(map_id=0, x=5, y=5)
        ag.last_overworld_state = old
        ag.recent_positions = [(0, 5, 5)]
        ag.stuck_turns = 9

        state = OverworldState(map_id=0, x=5, y=5)
        ag.update_overworld_progress(state)
        assert ag.stuck_turns == 10
        assert any("STUCK" in e for e in ag.events)

    def test_stuck_no_log_at_3(self, tmp_path):
        ag = _make_agent(tmp_path)
        old = OverworldState(map_id=0, x=5, y=5)
        ag.last_overworld_state = old
        ag.recent_positions = [(0, 5, 5)]
        ag.stuck_turns = 2

        state = OverworldState(map_id=0, x=5, y=5)
        ag.update_overworld_progress(state)
        assert ag.stuck_turns == 3
        stuck_events = [e for e in ag.events if "STUCK" in e]
        assert len(stuck_events) == 0


class TestChooseOverworldAction:
    def test_text_box_active_returns_a(self, tmp_path):
        ag = _make_agent(tmp_path)
        state = OverworldState(text_box_active=True)
        assert ag.choose_overworld_action(state) == "a"

    def test_oaks_lab_no_party_returns_a(self, tmp_path):
        ag = _make_agent(tmp_path)
        state = OverworldState(map_id=40, party_count=0)
        with patch.object(agent, "Image", None):
            result = ag.choose_overworld_action(state)
        # Phase 0 of the lab strategy: dismiss text (b) or move south (down)
        assert result in ("a", "b", "down", "right", "up")

    def test_oaks_lab_with_party_uses_lab_exit(self, tmp_path):
        ag = _make_agent(tmp_path)
        state = OverworldState(map_id=40, party_count=1, x=5, y=5)
        result = ag.choose_overworld_action(state)
        # Lab exit navigation: at x=5 (center), should go down or A
        assert result in ("down", "a")

    def test_navigator_returns_none_falls_back_to_a(self, tmp_path):
        ag = _make_agent(tmp_path)
        ag.navigator.next_direction = MagicMock(return_value=None)
        state = OverworldState(map_id=99, x=5, y=5)
        assert ag.choose_overworld_action(state) == "a"

    def test_normal_direction(self, tmp_path):
        ag = _make_agent(tmp_path)
        ag.navigator.next_direction = MagicMock(return_value="left")
        state = OverworldState(map_id=99, x=5, y=5)
        assert ag.choose_overworld_action(state) == "left"


class TestLog:
    def test_log_appends_event(self, tmp_path):
        ag = _make_agent(tmp_path)
        ag.log("test message")
        assert len(ag.events) == 1
        assert "test message" in ag.events[0]

    def test_log_has_timestamp(self, tmp_path):
        ag = _make_agent(tmp_path)
        ag.log("hello")
        # Format: [HH:MM:SS] hello
        assert ag.events[0].startswith("[")
        assert "]" in ag.events[0]


class TestWritePokedexEntry:
    def test_writes_markdown_file(self, tmp_path):
        ag = _make_agent(tmp_path)
        ag.turn_count = 50
        ag.battles_won = 3
        ag.maps_visited = {0, 12}
        ag.events = [
            "[00:00:01] MAP CHANGE | 0 -> 12 | Pos: (5, 33)",
            "[00:00:02] BATTLE | Player HP: 30/40 | Enemy HP: 0/20 | Action: fight",
            "[00:00:03] STUCK | Map: 12 | Pos: (5, 30) | Last move: up | Streak: 2",
            "[00:00:04] Some random event",
        ]
        mock_ow = OverworldState(map_id=12, x=5, y=10, badges=0, party_count=1)
        ag.memory.read_overworld_state = MagicMock(return_value=mock_ow)

        ag.write_pokedex_entry()

        logs = list(ag.pokedex_dir.glob("log*.md"))
        assert len(logs) == 1
        content = logs[0].read_text()
        assert "Log 1" in content
        assert "Turns:** 50" in content
        assert "Battles won:** 3" in content
        assert "Maps visited:** 2" in content
        assert "MAP CHANGE" in content
        assert "BATTLE" in content
        assert "STUCK" in content
        assert "Some random event" in content

    def test_increments_log_number(self, tmp_path):
        ag = _make_agent(tmp_path)
        (ag.pokedex_dir / "log1.md").write_text("old")
        ag.memory.read_overworld_state = MagicMock(return_value=OverworldState(map_id=0, x=0, y=0))
        ag.write_pokedex_entry()
        assert (ag.pokedex_dir / "log2.md").exists()

    def test_pokedex_includes_encounters(self, tmp_path):
        ag = _make_agent(tmp_path)
        ag.encounter_log = [
            {"species": "Pidgey", "type": "normal", "won": True},
            {"species": "Pidgey", "type": "normal", "won": True},
            {"species": "Rattata", "type": "normal", "won": True},
        ]
        ag.memory.read_overworld_state = MagicMock(return_value=OverworldState(map_id=0, x=0, y=0))
        ag.write_pokedex_entry()
        logs = list(ag.pokedex_dir.glob("log*.md"))
        content = logs[0].read_text()
        assert "Encounters:** 3" in content
        assert "Pidgey: 2" in content
        assert "Rattata: 1" in content


class TestEncounterLog:
    def test_init_has_encounter_log(self, tmp_path):
        ag = _make_agent(tmp_path)
        assert ag.encounter_log == []
        assert ag._current_enemy_species == ""
        assert ag._current_enemy_type == ""

    def test_battle_end_logs_encounter(self, tmp_path):
        ag = _make_agent(tmp_path)
        battle_active = BattleState(
            battle_type=1,
            player_hp=50,
            player_max_hp=100,
            enemy_hp=30,
            enemy_max_hp=40,
            enemy_species=0x24,  # Pidgey
            enemy_type1=0x00,  # normal
            enemy_type2=0x02,  # flying
            moves=[0x01, 0x00, 0x00, 0x00],
            move_pp=[10, 0, 0, 0],
            player_level=5,
        )
        battle_none = BattleState(battle_type=0)
        overworld = OverworldState(map_id=0, x=5, y=5)

        ag.memory.read_battle_state = MagicMock(side_effect=[battle_active, battle_active, battle_none, battle_none])
        ag.memory.read_overworld_state = MagicMock(return_value=overworld)
        ag.memory.find_healing_item = MagicMock(return_value=None)
        ag.memory.read_party_species = MagicMock(return_value=[0xB0])
        # Win is now derived from end-of-battle party HP (a loss/white-out flips it to False).
        ag.memory.player_whited_out = MagicMock(return_value=False)
        ag.memory.read_party = MagicMock(return_value=[{"species": "Charmander", "level": 5, "hp": 20, "max_hp": 20}])

        with patch.object(agent, "Image", None):
            ag.run(max_turns=2)

        assert len(ag.encounter_log) == 1
        assert ag.encounter_log[0]["species"] == "Pidgey"
        assert ag.encounter_log[0]["type"] == "normal"
        assert ag.encounter_log[0]["won"] is True

    def test_compute_fitness_includes_encounters(self, tmp_path):
        ag = _make_agent(tmp_path)
        ag.encounter_log = [{"species": "Pidgey", "type": "normal", "won": True}] * 3
        ag.memory.read_overworld_state = MagicMock(return_value=OverworldState(map_id=0, x=0, y=0))
        fitness = ag.compute_fitness()
        assert fitness["encounters"] == 3


class TestEvolutionDetection:
    def test_init_has_evolution_fields(self, tmp_path):
        ag = _make_agent(tmp_path)
        assert ag._pre_battle_species == []
        assert ag._pre_battle_level == 0
        assert ag.evolution_log == []
        assert ag.level_ups == 0

    def test_level_up_detected(self, tmp_path):
        ag = _make_agent(tmp_path)
        battle_active = BattleState(
            battle_type=1,
            player_hp=50,
            player_max_hp=100,
            enemy_hp=30,
            enemy_max_hp=40,
            player_level=5,
            moves=[0x01, 0x00, 0x00, 0x00],
            move_pp=[10, 0, 0, 0],
        )
        battle_none = BattleState(battle_type=0)
        overworld = OverworldState(map_id=0, x=5, y=5)

        ag.memory.read_battle_state = MagicMock(side_effect=[battle_active, battle_active, battle_none, battle_none])
        ag.memory.read_overworld_state = MagicMock(return_value=overworld)
        ag.memory.find_healing_item = MagicMock(return_value=None)
        ag.memory.read_party_species = MagicMock(return_value=[0xB0])
        # After battle, player level reads as 6 (leveled up from 5)
        # ADDR_PLAYER_LEVEL reads 0 when not in battle, so falls through to party struct
        ag.pyboy.memory[MemoryReader.PARTY_BASE + 33] = 6

        with patch.object(agent, "Image", None):
            ag.run(max_turns=2)

        assert ag.level_ups == 1
        assert any("LEVEL UP" in e for e in ag.events)
        assert any("Lv5 -> Lv6" in e for e in ag.events)

    def test_evolution_detected(self, tmp_path):
        ag = _make_agent(tmp_path)
        battle_active = BattleState(
            battle_type=1,
            player_hp=50,
            player_max_hp=100,
            enemy_hp=30,
            enemy_max_hp=40,
            player_level=16,
            moves=[0x01, 0x00, 0x00, 0x00],
            move_pp=[10, 0, 0, 0],
        )
        battle_none = BattleState(battle_type=0)
        overworld = OverworldState(map_id=0, x=5, y=5)

        # Pre-battle: Charmander; post-battle: Charmeleon
        species_calls = [MagicMock(return_value=[0xB0]), MagicMock(return_value=[0xB2])]
        call_count = [0]

        def mock_read_species():
            idx = min(call_count[0], len(species_calls) - 1)
            call_count[0] += 1
            return species_calls[idx]()

        ag.memory.read_battle_state = MagicMock(side_effect=[battle_active, battle_active, battle_none, battle_none])
        ag.memory.read_overworld_state = MagicMock(return_value=overworld)
        ag.memory.find_healing_item = MagicMock(return_value=None)
        ag.memory.read_party_species = MagicMock(side_effect=mock_read_species)

        with patch.object(agent, "Image", None):
            ag.run(max_turns=2)

        assert len(ag.evolution_log) == 1
        assert ag.evolution_log[0]["from"] == "Charmander"
        assert ag.evolution_log[0]["to"] == "Charmeleon"
        assert ag.evolution_log[0]["slot"] == 0
        assert any("EVOLUTION" in e for e in ag.events)

    def test_no_evolution_when_species_unchanged(self, tmp_path):
        ag = _make_agent(tmp_path)
        battle_active = BattleState(
            battle_type=1,
            player_hp=50,
            player_max_hp=100,
            enemy_hp=30,
            enemy_max_hp=40,
            player_level=5,
            moves=[0x01, 0x00, 0x00, 0x00],
            move_pp=[10, 0, 0, 0],
        )
        battle_none = BattleState(battle_type=0)
        overworld = OverworldState(map_id=0, x=5, y=5)

        ag.memory.read_battle_state = MagicMock(side_effect=[battle_active, battle_active, battle_none, battle_none])
        ag.memory.read_overworld_state = MagicMock(return_value=overworld)
        ag.memory.find_healing_item = MagicMock(return_value=None)
        ag.memory.read_party_species = MagicMock(return_value=[0xB0])

        with patch.object(agent, "Image", None):
            ag.run(max_turns=2)

        assert ag.evolution_log == []

    def test_pre_battle_snapshot_only_on_first_turn(self, tmp_path):
        """Pre-battle species is only captured on the first battle turn."""
        ag = _make_agent(tmp_path)
        battle_active = BattleState(
            battle_type=1,
            player_hp=50,
            player_max_hp=100,
            enemy_hp=30,
            enemy_max_hp=40,
            player_level=5,
            moves=[0x01, 0x00, 0x00, 0x00],
            move_pp=[10, 0, 0, 0],
        )
        battle_none = BattleState(battle_type=0)
        overworld = OverworldState(map_id=0, x=5, y=5)

        # 2 battle turns (iter1: still fighting, iter2: battle ends), then 1 overworld
        # Each battle iter: main loop read + run_battle_turn read + post-check read = 3
        # iter1: active, active, active (battle continues)
        # iter2: active, active, none (battle ends)
        # iter3: none (overworld)
        ag.memory.read_battle_state = MagicMock(
            side_effect=[
                battle_active,
                battle_active,
                battle_active,
                battle_active,
                battle_active,
                battle_none,
                battle_none,
            ]
        )
        ag.memory.read_overworld_state = MagicMock(return_value=overworld)
        ag.memory.find_healing_item = MagicMock(return_value=None)
        ag.memory.read_party_species = MagicMock(return_value=[0xB0])

        with patch.object(agent, "Image", None):
            ag.run(max_turns=3)

        # read_party_species called: once for pre-battle snapshot, once for post-battle check
        assert ag.memory.read_party_species.call_count == 2

    def test_compute_fitness_includes_evolution_and_levelups(self, tmp_path):
        ag = _make_agent(tmp_path)
        ag.evolution_log = [{"slot": 0, "from": "Charmander", "to": "Charmeleon"}]
        ag.level_ups = 3
        ag.memory.read_overworld_state = MagicMock(return_value=OverworldState(map_id=0, x=0, y=0))
        fitness = ag.compute_fitness()
        assert fitness["level_ups"] == 3
        assert fitness["evolutions"] == 1

    def test_pokedex_includes_evolution_section(self, tmp_path):
        ag = _make_agent(tmp_path)
        ag.evolution_log = [{"slot": 0, "from": "Charmander", "to": "Charmeleon"}]
        ag.level_ups = 2
        ag.memory.read_overworld_state = MagicMock(return_value=OverworldState(map_id=0, x=0, y=0))
        ag.write_pokedex_entry()
        logs = list(ag.pokedex_dir.glob("log*.md"))
        content = logs[0].read_text()
        assert "## Evolutions" in content
        assert "Charmander -> Charmeleon" in content
        assert "## Level Ups: 2" in content

    def test_pokedex_no_evolution_section_when_empty(self, tmp_path):
        ag = _make_agent(tmp_path)
        ag.memory.read_overworld_state = MagicMock(return_value=OverworldState(map_id=0, x=0, y=0))
        ag.write_pokedex_entry()
        logs = list(ag.pokedex_dir.glob("log*.md"))
        content = logs[0].read_text()
        assert "## Evolutions" not in content
        assert "## Level Ups" not in content


class TestTakeScreenshot:
    def test_no_screenshots_flag(self, tmp_path):
        ag = _make_agent(tmp_path, screenshots=False)
        ag.take_screenshot()  # Should do nothing, no error
        assert not ag.frames_dir.exists() or not list(ag.frames_dir.glob("*.png"))

    def test_image_none(self, tmp_path):
        ag = _make_agent(tmp_path, screenshots=True)
        with patch.object(agent, "Image", None):
            ag.take_screenshot()

    def test_saves_screenshot(self, tmp_path):
        ag = _make_agent(tmp_path, screenshots=True)
        mock_image_mod = MagicMock()
        mock_img = MagicMock()
        mock_image_mod.fromarray.return_value = mock_img
        ag.pyboy.screen.ndarray = MagicMock()

        with patch.object(agent, "Image", mock_image_mod):
            ag.turn_count = 42
            ag.take_screenshot()

        mock_image_mod.fromarray.assert_called_once_with(ag.pyboy.screen.ndarray)
        mock_img.save.assert_called_once()
        saved_path = mock_img.save.call_args[0][0]
        assert "turn42.png" in str(saved_path)
        assert any("SCREENSHOT" in e for e in ag.events)


class TestRunBattleTurn:
    def _setup_agent_for_battle(self, tmp_path, action_dict):
        ag = _make_agent(tmp_path)
        battle = BattleState(
            battle_type=1,
            player_hp=50,
            player_max_hp=100,
            enemy_hp=30,
            enemy_max_hp=40,
            moves=[0x01, 0x00, 0x00, 0x00],
            move_pp=[10, 0, 0, 0],
        )
        ag.memory.read_battle_state = MagicMock(return_value=battle)
        ag.memory.find_healing_item = MagicMock(return_value=None)
        ag.battle_strategy.choose_action = MagicMock(return_value=action_dict)
        return ag

    def test_fight_action(self, tmp_path):
        ag = self._setup_agent_for_battle(tmp_path, {"action": "fight", "move_index": 2})
        initial_turns = ag.turn_count
        ag.run_battle_turn()
        assert ag.turn_count == initial_turns + 1
        assert any("BATTLE" in e for e in ag.events)

    def test_run_action(self, tmp_path):
        ag = self._setup_agent_for_battle(tmp_path, {"action": "run"})
        ag.run_battle_turn()
        assert ag.turn_count == 1

    def test_item_action(self, tmp_path):
        ag = self._setup_agent_for_battle(tmp_path, {"action": "item", "item": "Potion", "bag_index": 0})
        ag.run_battle_turn()
        assert ag.turn_count == 1

    def test_item_action_navigates_to_bag_index(self, tmp_path):
        ag = self._setup_agent_for_battle(tmp_path, {"action": "item", "item": "Super Potion", "bag_index": 3})
        ag.controller = MagicMock()
        ag.run_battle_turn()
        # Should navigate_menu(1) for BAG, then navigate_menu(3) for item
        menu_calls = [c for c in ag.controller.navigate_menu.call_args_list]
        assert menu_calls[0] == call(1)
        assert menu_calls[1] == call(3)

    def test_item_action_default_bag_index(self, tmp_path):
        ag = self._setup_agent_for_battle(tmp_path, {"action": "item", "item": "Potion"})
        ag.controller = MagicMock()
        ag.run_battle_turn()
        menu_calls = [c for c in ag.controller.navigate_menu.call_args_list]
        assert menu_calls[1] == call(0)

    def test_switch_action(self, tmp_path):
        ag = self._setup_agent_for_battle(tmp_path, {"action": "switch", "slot": 2})
        ag.run_battle_turn()
        assert ag.turn_count == 1

    def test_switch_action_default_slot(self, tmp_path):
        ag = self._setup_agent_for_battle(tmp_path, {"action": "switch"})
        ag.run_battle_turn()
        assert ag.turn_count == 1

    def test_calls_find_healing_item(self, tmp_path):
        """run_battle_turn passes find_healing_item result to choose_action."""
        ag = _make_agent(tmp_path)
        battle = BattleState(
            battle_type=1,
            player_hp=50,
            player_max_hp=100,
            enemy_hp=30,
            enemy_max_hp=40,
            moves=[0x01, 0x00, 0x00, 0x00],
            move_pp=[10, 0, 0, 0],
        )
        ag.memory.read_battle_state = MagicMock(return_value=battle)
        ag.memory.find_healing_item = MagicMock(return_value=(0, 0x14))
        ag.battle_strategy.choose_action = MagicMock(return_value={"action": "fight", "move_index": 0})
        ag.run_battle_turn()
        ag.battle_strategy.choose_action.assert_called_once_with(battle, bag_healing=(0, 0x14))


class TestRunOverworld:
    def test_directional_movement(self, tmp_path):
        ag = _make_agent(tmp_path)
        state = OverworldState(map_id=99, x=5, y=5)
        ag.memory.read_overworld_state = MagicMock(return_value=state)
        ag.choose_overworld_action = MagicMock(return_value="up")
        # Replace controller with a mock so we can assert calls
        ag.controller = MagicMock()
        ag.turn_count = 0

        ag.run_overworld()

        ag.controller.move.assert_called_once_with("up")
        assert ag.last_overworld_state == state
        assert ag.last_overworld_action == "up"

    def test_a_press_action(self, tmp_path):
        ag = _make_agent(tmp_path)
        state = OverworldState(map_id=99, x=5, y=5, text_box_active=True)
        ag.memory.read_overworld_state = MagicMock(return_value=state)
        ag.choose_overworld_action = MagicMock(return_value="a")
        ag.controller = MagicMock()
        ag.turn_count = 0

        ag.run_overworld()

        ag.controller.press.assert_called_once_with("a", hold_frames=20, release_frames=12)
        ag.controller.wait.assert_called_once_with(24)
        assert ag.last_overworld_action == "a"

    def test_logs_every_100_steps(self, tmp_path):
        ag = _make_agent(tmp_path)
        state = OverworldState(map_id=12, x=5, y=10, badges=1, party_count=2)
        ag.memory.read_overworld_state = MagicMock(return_value=state)
        ag.choose_overworld_action = MagicMock(return_value="down")
        ag.controller = MagicMock()
        ag.turn_count = 100  # divisible by 100

        ag.run_overworld()

        overworld_logs = [e for e in ag.events if "OVERWORLD" in e]
        assert len(overworld_logs) == 1
        assert "Map: 12" in overworld_logs[0]

    def test_no_log_at_non_100(self, tmp_path):
        ag = _make_agent(tmp_path)
        state = OverworldState(map_id=12, x=5, y=10)
        ag.memory.read_overworld_state = MagicMock(return_value=state)
        ag.choose_overworld_action = MagicMock(return_value="down")
        ag.controller = MagicMock()
        ag.turn_count = 99

        ag.run_overworld()

        overworld_logs = [e for e in ag.events if "OVERWORLD" in e]
        assert len(overworld_logs) == 0

    def test_logs_at_turn_0(self, tmp_path):
        ag = _make_agent(tmp_path)
        state = OverworldState(map_id=12, x=5, y=10)
        ag.memory.read_overworld_state = MagicMock(return_value=state)
        ag.choose_overworld_action = MagicMock(return_value="down")
        ag.controller = MagicMock()
        ag.turn_count = 0  # 0 % 100 == 0

        ag.run_overworld()

        overworld_logs = [e for e in ag.events if "OVERWORLD" in e]
        assert len(overworld_logs) == 1


class TestRun:
    def _mock_battle_helpers(self, ag):
        """Set up common mocks for battle-related memory methods."""
        ag.memory.find_healing_item = MagicMock(return_value=None)
        ag.memory.read_party_species = MagicMock(return_value=[0xB0])

    def test_run_battle_then_overworld(self, tmp_path):
        ag = _make_agent(tmp_path)
        self._mock_battle_helpers(ag)

        battle_active = BattleState(
            battle_type=1,
            player_hp=50,
            player_max_hp=100,
            enemy_hp=30,
            enemy_max_hp=40,
            moves=[0x01, 0x00, 0x00, 0x00],
            move_pp=[10, 0, 0, 0],
            player_level=5,
        )
        battle_none = BattleState(battle_type=0)
        overworld = OverworldState(map_id=0, x=5, y=5)

        # Turn 1: battle -> run_battle_turn (reads battle_active inside),
        #   post-battle check reads battle_none -> battles_won++
        # Turn 2: reads battle_none -> run_overworld
        ag.memory.read_battle_state = MagicMock(side_effect=[battle_active, battle_active, battle_none, battle_none])
        ag.memory.read_overworld_state = MagicMock(return_value=overworld)

        with patch.object(agent, "Image", None):
            ag.run(max_turns=2)

        assert ag.battles_won == 1
        assert any("Battle ended" in e for e in ag.events)
        assert any("Session complete" in e for e in ag.events)

    def test_run_resets_run_attempts_on_battle_end(self, tmp_path):
        ag = _make_agent(tmp_path)
        self._mock_battle_helpers(ag)
        ag.battle_strategy._run_attempts = 3  # simulate exhausted run attempts

        battle_active = BattleState(
            battle_type=1,
            player_hp=50,
            player_max_hp=100,
            enemy_hp=30,
            enemy_max_hp=40,
            moves=[0x01, 0x00, 0x00, 0x00],
            move_pp=[10, 0, 0, 0],
            player_level=5,
        )
        battle_none = BattleState(battle_type=0)
        overworld = OverworldState(map_id=0, x=5, y=5)

        ag.memory.read_battle_state = MagicMock(side_effect=[battle_active, battle_active, battle_none, battle_none])
        ag.memory.read_overworld_state = MagicMock(return_value=overworld)

        with patch.object(agent, "Image", None):
            ag.run(max_turns=2)

        assert ag.battle_strategy._run_attempts == 0

    def test_run_battle_limit_stops_early(self, tmp_path):
        """Loop breaks when battles_won reaches battle_limit."""
        ag = _make_agent(tmp_path)
        self._mock_battle_helpers(ag)

        battle_active = BattleState(
            battle_type=1,
            player_hp=50,
            player_max_hp=100,
            enemy_hp=30,
            enemy_max_hp=40,
            moves=[0x01, 0x00, 0x00, 0x00],
            move_pp=[10, 0, 0, 0],
            player_level=5,
        )
        battle_none = BattleState(battle_type=0)

        # One battle: loop read -> run_battle_turn read -> post-battle read (ends)
        ag.memory.read_battle_state = MagicMock(side_effect=[battle_active, battle_active, battle_none])
        ag.memory.read_overworld_state = MagicMock(return_value=OverworldState(map_id=0, x=5, y=5))

        with patch.object(agent, "Image", None):
            ag.run(max_turns=100, battle_limit=1)

        assert ag.battles_won == 1
        assert any("Battle limit reached (1)" in e for e in ag.events)
        # Loop exited after 1 battle, well before max_turns
        assert ag.turn_count < 100

    def test_run_load_state_skips_intro(self, tmp_path):
        """--load-state loads a PyBoy state and skips the intro."""
        ag = _make_agent(tmp_path)
        state = tmp_path / "first_battle.state"
        state.write_bytes(b"savestate")
        ag.memory.read_overworld_state = MagicMock(return_value=OverworldState(map_id=38, x=3, y=4))
        with patch.object(ag, "_advance_intro") as adv, patch.object(agent, "Image", None):
            ag.run(max_turns=0, load_state=str(state))
        adv.assert_not_called()
        ag.pyboy.load_state.assert_called_once()
        assert any("Loaded save state" in e for e in ag.events)

    def test_run_saves_state_on_first_battle(self, tmp_path):
        """--save-state-on-battle dumps a state at the first detected battle."""
        ag = _make_agent(tmp_path)
        self._mock_battle_helpers(ag)
        out = tmp_path / "first_battle.state"
        battle_active = BattleState(
            battle_type=1,
            player_hp=50,
            player_max_hp=100,
            enemy_hp=30,
            enemy_max_hp=40,
            moves=[0x01, 0x00, 0x00, 0x00],
            move_pp=[10, 0, 0, 0],
            player_level=5,
        )
        battle_none = BattleState(battle_type=0)
        ag.memory.read_battle_state = MagicMock(side_effect=[battle_active, battle_active, battle_none])
        ag.memory.read_overworld_state = MagicMock(return_value=OverworldState(map_id=0, x=5, y=5))
        with patch.object(agent, "Image", None):
            ag.run(max_turns=100, battle_limit=1, save_state_on_battle=str(out))
        ag.pyboy.save_state.assert_called_once()
        assert ag._battle_state_saved is True
        assert any("Saved battle state" in e for e in ag.events)

    def test_run_saves_state_on_map(self, tmp_path):
        """--save-state-on-map dumps a state when first reaching the target map."""
        ag = _make_agent(tmp_path)
        out = tmp_path / "route1.state"
        ag.memory.read_battle_state = MagicMock(return_value=BattleState(battle_type=0))
        ag.memory.read_overworld_state = MagicMock(return_value=OverworldState(map_id=12, x=5, y=33))
        ag.run_overworld = MagicMock()
        with patch.object(agent, "Image", None):
            ag.run(max_turns=1, save_state_on_map=f"12:{out}")
        ag.pyboy.save_state.assert_called_once()
        assert ag._map_state_saved is True
        assert any("Saved map-12 state" in e for e in ag.events)

    def test_run_saves_periodic_checkpoint(self, tmp_path):
        """--save-state-every "N:PATH" overwrites a checkpoint every N turns."""
        ag = _make_agent(tmp_path)
        out = tmp_path / "checkpoint.state"
        ag.memory.read_battle_state = MagicMock(return_value=BattleState(battle_type=0))
        ag.memory.read_overworld_state = MagicMock(return_value=OverworldState(map_id=12, x=5, y=5))
        ag.run_overworld = MagicMock()
        with patch.object(agent, "Image", None):
            ag.run(max_turns=4, save_state_every=f"2:{out}")
        # Checkpoints at turn 2 and turn 4.
        assert ag.pyboy.save_state.call_count == 2
        assert any("Checkpoint at turn" in e for e in ag.events)

    def test_run_overworld_only(self, tmp_path):
        ag = _make_agent(tmp_path)
        battle_none = BattleState(battle_type=0)
        overworld = OverworldState(map_id=0, x=5, y=5)

        ag.memory.read_battle_state = MagicMock(return_value=battle_none)
        ag.memory.read_overworld_state = MagicMock(return_value=overworld)

        with patch.object(agent, "Image", None):
            ag.run(max_turns=2)

        assert ag.turn_count >= 2
        assert any("Session complete" in e for e in ag.events)

    def test_run_takes_screenshots_every_10(self, tmp_path):
        ag = _make_agent(tmp_path, screenshots=True)
        battle_none = BattleState(battle_type=0)
        overworld = OverworldState(map_id=0, x=5, y=5)

        ag.memory.read_battle_state = MagicMock(return_value=battle_none)
        ag.memory.read_overworld_state = MagicMock(return_value=overworld)

        mock_image_mod = MagicMock()
        mock_img = MagicMock()
        mock_image_mod.fromarray.return_value = mock_img
        ag.pyboy.screen.ndarray = MagicMock()

        with patch.object(agent, "Image", mock_image_mod):
            ag.run(max_turns=11)

        # Screenshot fires when turn_count % 10 == 0
        assert mock_image_mod.fromarray.call_count >= 1

    def test_run_battle_not_ended(self, tmp_path):
        """Battle still active after run_battle_turn -- no battles_won increment."""
        ag = _make_agent(tmp_path)
        self._mock_battle_helpers(ag)
        battle_active = BattleState(
            battle_type=1,
            player_hp=50,
            player_max_hp=100,
            enemy_hp=30,
            enemy_max_hp=40,
            moves=[0x01, 0x00, 0x00, 0x00],
            move_pp=[10, 0, 0, 0],
        )
        overworld = OverworldState(map_id=0, x=5, y=5)

        # All battle reads return active battle -- battle never ends
        ag.memory.read_battle_state = MagicMock(return_value=battle_active)
        ag.memory.read_overworld_state = MagicMock(return_value=overworld)

        with patch.object(agent, "Image", None):
            ag.run(max_turns=1)

        assert ag.battles_won == 0

    def test_run_pyboy_stop_permission_error(self, tmp_path):
        ag = _make_agent(tmp_path)
        battle_none = BattleState(battle_type=0)
        overworld = OverworldState(map_id=0, x=5, y=5)

        ag.memory.read_battle_state = MagicMock(return_value=battle_none)
        ag.memory.read_overworld_state = MagicMock(return_value=overworld)
        ag.pyboy.stop.side_effect = PermissionError("read-only mount")

        # Should not raise
        with patch.object(agent, "Image", None):
            ag.run(max_turns=1)
        assert any("Session complete" in e for e in ag.events)

    def test_run_writes_pokedex_entry(self, tmp_path):
        ag = _make_agent(tmp_path)
        battle_none = BattleState(battle_type=0)
        overworld = OverworldState(map_id=0, x=5, y=5)

        ag.memory.read_battle_state = MagicMock(return_value=battle_none)
        ag.memory.read_overworld_state = MagicMock(return_value=overworld)

        with patch.object(agent, "Image", None):
            ag.run(max_turns=1)

        logs = list(ag.pokedex_dir.glob("log*.md"))
        assert len(logs) == 1


# ===================================================================
# main()
# ===================================================================


class TestMain:
    def test_main_success(self, tmp_path):
        rom = tmp_path / "game.gb"
        rom.write_text("fake rom")

        mock_agent = MagicMock()

        with (
            patch("sys.argv", ["agent.py", str(rom), "--strategy", "low", "--max-turns", "5"]),
            patch("agent.PokemonAgent", return_value=mock_agent) as mock_cls,
        ):
            main()

        mock_cls.assert_called_once_with(str(rom), strategy="low", screenshots=False)
        mock_agent.run.assert_called_once_with(
            max_turns=5,
            battle_limit=0,
            load_state=None,
            save_state_on_battle=None,
            save_state_on_map=None,
            save_state_on_trainer=None,
            save_state_every=None,
        )

    def test_main_rom_not_found(self, tmp_path):
        missing = tmp_path / "nope.gb"

        with patch("sys.argv", ["agent.py", str(missing)]):
            with pytest.raises(SystemExit) as exc_info:
                main()
            assert exc_info.value.code == 1

    def test_main_with_screenshots(self, tmp_path):
        rom = tmp_path / "game.gb"
        rom.write_text("fake rom")

        mock_agent = MagicMock()

        with (
            patch(
                "sys.argv",
                ["agent.py", str(rom), "--save-screenshots", "--max-turns", "10"],
            ),
            patch("agent.PokemonAgent", return_value=mock_agent) as mock_cls,
        ):
            main()

        mock_cls.assert_called_once_with(str(rom), strategy="low", screenshots=True)
        mock_agent.run.assert_called_once_with(
            max_turns=10,
            battle_limit=0,
            load_state=None,
            save_state_on_battle=None,
            save_state_on_map=None,
            save_state_on_trainer=None,
            save_state_every=None,
        )

    def test_main_default_args(self, tmp_path):
        rom = tmp_path / "game.gb"
        rom.write_text("fake rom")

        mock_agent = MagicMock()

        with (
            patch("sys.argv", ["agent.py", str(rom)]),
            patch("agent.PokemonAgent", return_value=mock_agent) as mock_cls,
        ):
            main()

        mock_cls.assert_called_once_with(str(rom), strategy="low", screenshots=False)
        mock_agent.run.assert_called_once_with(
            max_turns=100_000,
            battle_limit=0,
            load_state=None,
            save_state_on_battle=None,
            save_state_on_map=None,
            save_state_on_trainer=None,
            save_state_every=None,
        )


# ===================================================================
# __name__ == "__main__" guard (line 600)
# ===================================================================


class TestMainGuard:
    def test_dunder_main_calls_main(self, tmp_path):
        """Line 599-600: if __name__ == '__main__': main()"""
        rom = tmp_path / "game.gb"
        rom.write_text("fake rom")

        # Create a mock pyboy module with a mock PyBoy class.
        # The mock PyBoy instance needs memory that returns int(0)
        # for any address so MemoryReader works correctly.
        from collections import defaultdict

        fake_mem = defaultdict(int)  # returns 0 for any key

        mock_pyboy_mod = MagicMock()
        mock_pb_instance = MagicMock()
        mock_pb_instance.memory = fake_mem
        mock_pyboy_mod.PyBoy = MagicMock(return_value=mock_pb_instance)

        # Also set up pokedex/frames dirs that __init__ tries to create
        pokedex_dir = tmp_path / "pokedex"
        pokedex_dir.mkdir(parents=True, exist_ok=True)

        # Remove PIL so the re-imported agent sets Image = None,
        # avoiding Image.fromarray() on a MagicMock screen.ndarray.
        saved_pil = sys.modules.pop("PIL", None)
        saved_pil_image = sys.modules.pop("PIL.Image", None)
        import builtins

        original_import = builtins.__import__

        def fail_pil(name, *args, **kwargs):
            if name in ("PIL", "PIL.Image"):
                raise ImportError("no PIL for test")
            return original_import(name, *args, **kwargs)

        # Use --max-turns 0 so the main loop body never executes.
        with (
            patch("sys.argv", ["agent.py", str(rom), "--max-turns", "0"]),
            patch.object(builtins, "__import__", side_effect=fail_pil),
        ):
            saved_pyboy = sys.modules.get("pyboy")
            sys.modules["pyboy"] = mock_pyboy_mod
            try:
                runpy.run_path(
                    str(Path(agent.__file__).resolve()),
                    run_name="__main__",
                )
            finally:
                if saved_pyboy is not None:
                    sys.modules["pyboy"] = saved_pyboy
                else:
                    sys.modules.pop("pyboy", None)
                if saved_pil is not None:
                    sys.modules["PIL"] = saved_pil
                if saved_pil_image is not None:
                    sys.modules["PIL.Image"] = saved_pil_image

        # If we got here without error, line 600 (main()) was executed.
        mock_pyboy_mod.PyBoy.assert_called_once()


# ===================================================================
# Module-level constants sanity checks
# ===================================================================


class TestModuleConstants:
    def test_script_dir_is_path(self):
        assert isinstance(SCRIPT_DIR, Path)

    def test_type_chart_path_is_path(self):
        assert isinstance(TYPE_CHART_PATH, Path)

    def test_routes_path_is_path(self):
        assert isinstance(ROUTES_PATH, Path)

    def test_early_game_targets_has_keys(self):
        assert 38 in EARLY_GAME_TARGETS
        assert 37 in EARLY_GAME_TARGETS
        # Map 0 (Pallet Town) uses waypoints instead of EARLY_GAME_TARGETS
        assert 0 not in EARLY_GAME_TARGETS

    def test_move_data_has_entries(self):
        assert 0x01 in MOVE_DATA
        assert 0x00 in MOVE_DATA
        assert 0x56 in MOVE_DATA  # Thunder Wave (status)


# ===================================================================
# Navigator -- collision_grid + A* integration
# ===================================================================


class TestNavigatorCollisionGrid:
    """Tests for A* pathfinding integration in Navigator.next_direction."""

    def _open_grid(self):
        """Return a fully walkable 9x10 grid."""
        return [[1] * 10 for _ in range(9)]

    def test_with_collision_grid_uses_astar_for_waypoint(self):
        """When collision_grid is provided and target is on screen, A* is used."""
        routes = {"10": [{"x": 7, "y": 6}]}
        nav = Navigator(routes)
        # Player at (5, 5), target at (7, 6) -> screen target = (4+1, 4+2) = (5, 6)
        state = OverworldState(map_id=10, x=5, y=5)
        grid = self._open_grid()
        result = nav.next_direction(state, collision_grid=grid)
        # A* should give a direction toward (5, 6) from (4, 4)
        assert result in ("down", "right")

    def test_with_collision_grid_astar_returns_first_direction(self):
        """A* path result is used to pick the first direction."""
        routes = {"10": [{"x": 6, "y": 5}]}
        nav = Navigator(routes)
        # Player at (5, 5), target at (6, 5) -> screen target = (4, 5)
        state = OverworldState(map_id=10, x=5, y=5)
        grid = self._open_grid()
        result = nav.next_direction(state, collision_grid=grid)
        # Target is to the right on screen
        assert result == "right"

    def test_with_collision_grid_falls_back_when_astar_fails(self):
        """When A* returns failure (all walls), fall back to _direction_toward_target."""
        routes = {"10": [{"x": 6, "y": 5}]}
        nav = Navigator(routes)
        state = OverworldState(map_id=10, x=5, y=5)
        # All walls except the player position
        grid = [[0] * 10 for _ in range(9)]
        grid[4][4] = 1  # player position is walkable
        result = nav.next_direction(state, collision_grid=grid)
        # A* fails, falls back to _direction_toward_target
        assert result == "right"  # x-preference default

    def test_without_collision_grid_behaves_as_before(self):
        """When collision_grid is None (default), behavior is unchanged."""
        routes = {"10": [{"x": 6, "y": 5}]}
        nav = Navigator(routes)
        state = OverworldState(map_id=10, x=5, y=5)
        result = nav.next_direction(state)
        assert result == "right"  # _direction_toward_target with x-preference

    def test_with_collision_grid_target_offscreen_falls_back(self):
        """When target is offscreen, A* is not attempted."""
        routes = {"10": [{"x": 20, "y": 5}]}
        nav = Navigator(routes)
        # Player at (5, 5), target at (20, 5) -> screen col = 4 + (20-5) = 19 -> offscreen
        state = OverworldState(map_id=10, x=5, y=5)
        grid = self._open_grid()
        result = nav.next_direction(state, collision_grid=grid)
        # Falls back to _direction_toward_target
        assert result == "right"

    def test_with_collision_grid_target_offscreen_negative_falls_back(self):
        """When target is offscreen in the negative direction, A* is not attempted."""
        routes = {"10": [{"x": 0, "y": 0}]}
        nav = Navigator(routes)
        # Player at (10, 10), target at (0, 0) -> screen row = 4 + (0-10) = -6 -> offscreen
        state = OverworldState(map_id=10, x=10, y=10)
        grid = self._open_grid()
        result = nav.next_direction(state, collision_grid=grid)
        assert result == "left"  # x-preference fallback

    def test_with_collision_grid_for_early_game_targets(self):
        """A* is used for early game targets when collision_grid is provided."""
        nav = Navigator({})
        # Map 38 = Red's bedroom, target (7, 1), axis "x"
        # Player at (3, 3) -> screen target = (4 + (1-3), 4 + (7-3)) = (2, 8)
        state = OverworldState(map_id=38, x=3, y=3)
        grid = self._open_grid()
        result = nav.next_direction(state, collision_grid=grid)
        # A* should navigate toward (2, 8) from (4, 4)
        assert result in ("right", "up")

    def test_with_collision_grid_early_game_offscreen_falls_back(self):
        """Early game target offscreen falls back to _direction_toward_target."""
        nav = Navigator({})
        # Map 38 = Red's bedroom, target (7, 1)
        # Player at (3, 20) -> screen target = (4 + (1-20), 4 + (7-3)) = (-15, 8) -> offscreen
        state = OverworldState(map_id=38, x=3, y=20)
        grid = self._open_grid()
        result = nav.next_direction(state, collision_grid=grid)
        assert result == "right"  # axis "x" for Red's bedroom, x=3 -> x=7

    def test_with_collision_grid_early_game_astar_failure_falls_back(self):
        """Early game A* failure falls back to _direction_toward_target."""
        nav = Navigator({})
        # Map 38, target (7, 1), player at (3, 3)
        # screen target = (2, 8) -- make all walls except player
        state = OverworldState(map_id=38, x=3, y=3)
        grid = [[0] * 10 for _ in range(9)]
        grid[4][4] = 1  # player only
        result = nav.next_direction(state, collision_grid=grid)
        assert result == "right"  # x-preference fallback

    def test_with_collision_grid_astar_partial_result_used(self):
        """A* partial result (target is wall but path approaches it) is used."""
        routes = {"10": [{"x": 6, "y": 5}]}
        nav = Navigator(routes)
        state = OverworldState(map_id=10, x=5, y=5)
        grid = self._open_grid()
        # Make the target cell a wall so A* returns partial
        grid[4][5] = 0
        result = nav.next_direction(state, collision_grid=grid)
        # Partial result still gives a direction
        assert result is not None

    def test_with_collision_grid_astar_empty_directions_falls_back(self):
        """When A* succeeds but returns no directions (at target), falls back."""
        routes = {"10": [{"x": 5, "y": 5}, {"x": 6, "y": 5}]}
        nav = Navigator(routes)
        # Player is AT the first waypoint -> advances to second, then A* on second
        state = OverworldState(map_id=10, x=5, y=5)
        grid = self._open_grid()
        result = nav.next_direction(state, collision_grid=grid)
        assert result is not None

    def test_collision_grid_forwarded_on_recursive_call(self):
        """When waypoint is reached and next_direction recurses, collision_grid is forwarded."""
        routes = {"10": [{"x": 5, "y": 5}, {"x": 6, "y": 5}]}
        nav = Navigator(routes)
        state = OverworldState(map_id=10, x=5, y=5)
        grid = self._open_grid()
        # Block the direct path in _direction_toward_target but leave A* path open
        # This verifies collision_grid gets forwarded in recursion
        result = nav.next_direction(state, collision_grid=grid)
        assert nav.current_waypoint == 1
        assert result == "right"


# ===================================================================
# PokemonAgent -- CollisionMap integration
# ===================================================================


class TestPokemonAgentCollisionMap:
    """Tests for CollisionMap integration in PokemonAgent."""

    def test_agent_creates_collision_map(self, tmp_path):
        """PokemonAgent.__init__ creates a collision_map attribute."""
        ag = _make_agent(tmp_path)
        assert hasattr(ag, "collision_map")
        from memory_reader import CollisionMap

        assert isinstance(ag.collision_map, CollisionMap)

    def test_run_overworld_updates_collision_map(self, tmp_path):
        """run_overworld calls collision_map.update before choosing action."""
        ag = _make_agent(tmp_path)
        state = OverworldState(map_id=99, x=5, y=5)
        ag.memory.read_overworld_state = MagicMock(return_value=state)
        ag.controller = MagicMock()
        ag.turn_count = 1

        # Mock the collision_map to track update calls
        ag.collision_map = MagicMock()
        ag.collision_map.grid = [[1] * 10 for _ in range(9)]

        ag.run_overworld()

        ag.collision_map.update.assert_called_once_with(ag.pyboy)

    def test_run_overworld_handles_collision_map_failure(self, tmp_path):
        """run_overworld continues even if collision_map.update raises."""
        ag = _make_agent(tmp_path)
        state = OverworldState(map_id=99, x=5, y=5)
        ag.memory.read_overworld_state = MagicMock(return_value=state)
        ag.controller = MagicMock()
        ag.turn_count = 1

        # Make collision_map.update raise
        ag.collision_map = MagicMock()
        ag.collision_map.update.side_effect = Exception("no game_wrapper")
        ag.collision_map.grid = [[1] * 10 for _ in range(9)]

        # Should not raise
        ag.run_overworld()

        assert ag.last_overworld_state == state

    def test_choose_overworld_action_passes_collision_grid(self, tmp_path):
        """choose_overworld_action passes collision_grid to navigator."""
        ag = _make_agent(tmp_path)
        ag.collision_map = MagicMock()
        ag.collision_map.grid = [[1] * 10 for _ in range(9)]

        state = OverworldState(map_id=99, x=5, y=5)
        ag.navigator.next_direction = MagicMock(return_value="down")

        ag.choose_overworld_action(state)

        ag.navigator.next_direction.assert_called_once_with(
            state,
            turn=ag.turn_count,
            stuck_turns=ag.stuck_turns,
            collision_grid=ag.collision_map.grid,
        )


# ===================================================================
# Navigator._try_astar -- lines 284, 289
# ===================================================================


class TestTryAstar:
    """Cover _try_astar returning first A* direction (284) and None (289)."""

    def _open_grid(self):
        return [[1] * 10 for _ in range(9)]

    def test_astar_returns_first_direction(self):
        """Line 284: A* succeeds and returns the first direction."""
        nav = Navigator({})
        state = OverworldState(map_id=10, x=5, y=5)
        # Target at (6, 5) -> screen (4, 5), player at screen (4, 4)
        result = nav._try_astar(state, 6, 5, self._open_grid())
        assert result == "right"

    def test_astar_out_of_bounds_returns_none(self):
        """Line 289: screen target out of bounds -> returns None."""
        nav = Navigator({})
        state = OverworldState(map_id=10, x=5, y=5)
        # Target at (20, 5) -> screen col = 4 + 15 = 19, out of 10-wide grid
        result = nav._try_astar(state, 20, 5, self._open_grid())
        assert result is None

    def test_astar_no_path_returns_none(self):
        """Line 289: target in bounds but no path found -> returns None."""
        nav = Navigator({})
        state = OverworldState(map_id=10, x=5, y=5)
        # All walls, no path possible
        grid = [[0] * 10 for _ in range(9)]
        grid[4][4] = 1  # only player cell walkable
        result = nav._try_astar(state, 6, 5, grid)
        assert result is None


# ===================================================================
# Navigator.next_direction -- early game target nulled by party (284)
# and at_target return (289), skip waypoint when stuck (322-323)
# ===================================================================


class TestNextDirectionUncoveredBranches:
    """Cover lines 284, 289, 322-323 in next_direction."""

    def test_map0_with_party_targets_route1_exit(self):
        """Map 0 with party > 0 routes north to Route 1 exit at (10, 0)."""
        routes = {"0": [{"x": 8, "y": 10}]}
        nav = Navigator(routes)
        state = OverworldState(map_id=0, x=5, y=11, party_count=1)
        result = nav.next_direction(state)
        # Should head north (axis="y") toward (10, 0)
        assert result == "up"

    def test_map0_with_party_at_exit_returns_up(self):
        """Map 0 with party at (10, 0) returns 'up' to exit to Route 1."""
        nav = Navigator({})
        state = OverworldState(map_id=0, x=10, y=0, party_count=1)
        result = nav.next_direction(state)
        assert result == "up"

    def test_at_early_game_target_returns_at_target_hint(self):
        """Line 289: at target returns at_target hint (default 'down')."""
        nav = Navigator({})
        # Map 38 target is (7, 1) with axis "x" — no at_target key => default "down"
        state = OverworldState(map_id=38, x=7, y=1)
        result = nav.next_direction(state)
        assert result == "down"

    def test_skip_waypoint_when_stuck_and_close(self):
        """Lines 322-323: stuck_turns>=8, dist<=3, skip waypoint."""
        routes = {"10": [{"x": 5, "y": 6}, {"x": 10, "y": 10}]}
        nav = Navigator(routes)
        # Player at (5, 5), first waypoint at (5, 6) -> dist=1
        # stuck_turns=8, dist<=3, not last waypoint -> skip
        state = OverworldState(map_id=10, x=5, y=5)
        result = nav.next_direction(state, stuck_turns=8)
        # Should have skipped first waypoint and now be navigating to second
        assert nav.current_waypoint == 1
        assert result is not None


# ===================================================================
# update_overworld_progress -- lines 426, 452
# ===================================================================


class TestUpdateOverworldProgressUncovered:
    """Cover door cooldown on interior exit (426) and Viridian milestone (452)."""

    def test_door_cooldown_on_interior_exit_map37(self, tmp_path):
        """Line 426: exiting map 37 to map 0 sets door_cooldown = 8."""
        ag = _make_agent(tmp_path)
        ag.last_overworld_state = OverworldState(map_id=37, x=3, y=9)
        state = OverworldState(map_id=0, x=5, y=5)
        ag.update_overworld_progress(state)
        assert ag.door_cooldown == 8

    def test_door_cooldown_on_interior_exit_map38(self, tmp_path):
        """Line 426: exiting map 38 to map 0 sets door_cooldown = 8."""
        ag = _make_agent(tmp_path)
        ag.last_overworld_state = OverworldState(map_id=38, x=7, y=1)
        state = OverworldState(map_id=0, x=5, y=5)
        ag.update_overworld_progress(state)
        assert ag.door_cooldown == 8

    def test_short_door_cooldown_on_oak_lab_exit(self, tmp_path):
        """Exiting Oak's Lab (40) gets short cooldown=3 (left only, no south push)."""
        ag = _make_agent(tmp_path)
        ag.last_overworld_state = OverworldState(map_id=40, x=5, y=5)
        state = OverworldState(map_id=0, x=5, y=11)
        ag.update_overworld_progress(state)
        assert ag.door_cooldown == 3

    def test_no_door_cooldown_on_non_interior_exit(self, tmp_path):
        """Line 426 not hit: exiting map 12 to map 0 does not set cooldown."""
        ag = _make_agent(tmp_path)
        ag.last_overworld_state = OverworldState(map_id=12, x=5, y=5)
        state = OverworldState(map_id=0, x=5, y=5)
        ag.update_overworld_progress(state)
        assert ag.door_cooldown == 0

    def test_viridian_city_milestone_fires_on_first_visit(self, tmp_path):
        """Line 415: milestone log fires when map 1 is visited for the first time."""
        ag = _make_agent(tmp_path)
        ag.last_overworld_state = OverworldState(map_id=0, x=5, y=0)
        ag.maps_visited = {0}
        state = OverworldState(map_id=1, x=5, y=35)
        ag.update_overworld_progress(state)
        assert any("MILESTONE" in e for e in ag.events)


# ===================================================================
# choose_overworld_action -- door cooldown phases (461-467)
# ===================================================================


class TestDoorCooldownPhases:
    """Cover lines 461-467: door cooldown phases."""

    def test_door_cooldown_high_returns_a(self, tmp_path):
        """Lines 462-464: cooldown >= 6 -> wait + return 'a'."""
        ag = _make_agent(tmp_path)
        ag.door_cooldown = 7  # will be decremented to 6, >= 6
        ag.controller = MagicMock()
        state = OverworldState(map_id=0, x=5, y=5)
        result = ag.choose_overworld_action(state)
        assert result == "a"
        ag.controller.wait.assert_called_once_with(60)

    def test_door_cooldown_mid_returns_down(self, tmp_path):
        """Lines 465-466: cooldown >= 3 -> return 'down'."""
        ag = _make_agent(tmp_path)
        ag.door_cooldown = 4  # decremented to 3, >= 3
        state = OverworldState(map_id=0, x=5, y=5)
        result = ag.choose_overworld_action(state)
        assert result == "down"

    def test_door_cooldown_low_returns_left(self, tmp_path):
        """Line 467: cooldown < 3 -> return 'left'."""
        ag = _make_agent(tmp_path)
        ag.door_cooldown = 2  # decremented to 1, < 3
        state = OverworldState(map_id=0, x=5, y=5)
        result = ag.choose_overworld_action(state)
        assert result == "left"


# ===================================================================
# choose_overworld_action -- Oak's Lab phases (494-514, 521)
# ===================================================================


class TestOaksLabPhases:
    """Cover lab phases 0->1->2 with no Pokemon and lab with Pokemon."""

    def test_lab_phase0_y_ge_4_transitions_to_phase1(self, tmp_path):
        """Phase 0, y>=4 -> transition to phase 1, return 'right'."""
        ag = _make_agent(tmp_path)
        with patch.object(agent, "Image", None):
            state = OverworldState(map_id=40, party_count=0, x=3, y=4)
            result = ag.choose_overworld_action(state)
        assert result == "right"
        assert ag._lab_phase == 1
        assert any("phase 0" in e for e in ag.events)

    def test_lab_phase0_odd_turn_returns_b(self, tmp_path):
        """Phase 0, _lab_turns odd -> return 'b'."""
        ag = _make_agent(tmp_path)
        ag._lab_turns = 0  # will be incremented to 1 (odd)
        ag._lab_phase = 0
        with patch.object(agent, "Image", None):
            state = OverworldState(map_id=40, party_count=0, x=3, y=2)
            result = ag.choose_overworld_action(state)
        assert result == "b"

    def test_lab_phase0_even_turn_returns_down(self, tmp_path):
        """Phase 0, _lab_turns even -> return 'down'."""
        ag = _make_agent(tmp_path)
        ag._lab_turns = 1  # will be incremented to 2 (even)
        ag._lab_phase = 0
        with patch.object(agent, "Image", None):
            state = OverworldState(map_id=40, party_count=0, x=3, y=2)
            result = ag.choose_overworld_action(state)
        assert result == "down"

    def test_lab_phase1_x_ge_6_transitions_to_phase2(self, tmp_path):
        """Phase 1, x>=6 -> transition to phase 2, return 'up'."""
        ag = _make_agent(tmp_path)
        ag._lab_phase = 1
        ag._lab_turns = 0
        with patch.object(agent, "Image", None):
            state = OverworldState(map_id=40, party_count=0, x=6, y=4)
            result = ag.choose_overworld_action(state)
        assert result == "up"
        assert ag._lab_phase == 2
        assert any("phase 1" in e for e in ag.events)

    def test_lab_phase1_x_lt_6_returns_right(self, tmp_path):
        """Phase 1, x<6 -> return 'right'."""
        ag = _make_agent(tmp_path)
        ag._lab_phase = 1
        ag._lab_turns = 0
        with patch.object(agent, "Image", None):
            state = OverworldState(map_id=40, party_count=0, x=4, y=4)
            result = ag.choose_overworld_action(state)
        assert result == "right"

    def test_lab_phase2_even_turn_returns_up(self, tmp_path):
        """Phase 2, _lab_turns even -> return 'up'."""
        ag = _make_agent(tmp_path)
        ag._lab_phase = 2
        ag._lab_turns = 1  # incremented to 2 (even)
        with patch.object(agent, "Image", None):
            state = OverworldState(map_id=40, party_count=0, x=6, y=4)
            result = ag.choose_overworld_action(state)
        assert result == "up"

    def test_lab_phase2_odd_turn_returns_a(self, tmp_path):
        """Phase 2, _lab_turns odd -> return 'a'."""
        ag = _make_agent(tmp_path)
        ag._lab_phase = 2
        ag._lab_turns = 0  # incremented to 1 (odd)
        with patch.object(agent, "Image", None):
            state = OverworldState(map_id=40, party_count=0, x=6, y=4)
            result = ag.choose_overworld_action(state)
        assert result == "a"

    def test_lab_exit_early_mash_a(self, tmp_path):
        """First 30 turns in lab with party: mostly A-mash."""
        ag = _make_agent(tmp_path)
        state = OverworldState(map_id=40, party_count=1, x=7, y=5)
        # First call -> _lab_exit_turns = 1, not % 5 == 0 -> "a"
        result = ag.choose_overworld_action(state)
        assert result == "a"

    def test_lab_exit_early_occasional_down(self, tmp_path):
        """Early lab exit: every 5th turn returns 'down'."""
        ag = _make_agent(tmp_path)
        ag._lab_exit_turns = 4  # will be 5, 5 % 5 == 0
        state = OverworldState(map_id=40, party_count=1, x=7, y=5)
        result = ag.choose_overworld_action(state)
        assert result == "down"

    def test_lab_exit_go_left_when_east(self, tmp_path):
        """After 30 turns, x>5 -> go left."""
        ag = _make_agent(tmp_path)
        ag._lab_exit_turns = 30  # will be 31, not % 3 == 0
        state = OverworldState(map_id=40, party_count=1, x=7, y=5)
        result = ag.choose_overworld_action(state)
        assert result == "left"

    def test_lab_exit_go_left_interleave_a(self, tmp_path):
        """After 30 turns, x>5, every 3rd turn -> A."""
        ag = _make_agent(tmp_path)
        ag._lab_exit_turns = 32  # will be 33, 33 % 3 == 0
        state = OverworldState(map_id=40, party_count=1, x=7, y=5)
        result = ag.choose_overworld_action(state)
        assert result == "a"

    def test_lab_exit_go_south_at_center(self, tmp_path):
        """At x<=5, go south toward exit."""
        ag = _make_agent(tmp_path)
        ag._lab_exit_turns = 30  # will be 31, not % 4 == 0
        state = OverworldState(map_id=40, party_count=1, x=5, y=5)
        result = ag.choose_overworld_action(state)
        assert result == "down"

    def test_lab_exit_south_interleave_a(self, tmp_path):
        """At x<=5, every 4th turn -> A."""
        ag = _make_agent(tmp_path)
        ag._lab_exit_turns = 31  # will be 32, 32 % 4 == 0
        state = OverworldState(map_id=40, party_count=1, x=5, y=5)
        result = ag.choose_overworld_action(state)
        assert result == "a"


# ===================================================================
# run_overworld -- House 1F diagnostic (651-654)
# ===================================================================


class TestRunOverworldHouseDiag:
    """Cover lines 651-654: House 1F diagnostic on first visit."""

    def test_house_1f_diagnostic_on_first_visit(self, tmp_path):
        """Lines 651-654: map 37, first visit -> screenshot + collision log."""
        ag = _make_agent(tmp_path)
        state = OverworldState(map_id=37, x=3, y=5)
        ag.memory.read_overworld_state = MagicMock(return_value=state)
        ag.controller = MagicMock()
        ag.collision_map = MagicMock()
        ag.collision_map.grid = [[1] * 10 for _ in range(9)]
        ag.collision_map.to_ascii.return_value = "...\n...\n"
        ag.turn_count = 1

        with patch.object(agent, "Image", None):
            ag.run_overworld()

        assert hasattr(ag, "_house_diag_done")
        assert ag._house_diag_done is True
        assert any("DIAG | House 1F" in e for e in ag.events)

    def test_house_1f_diagnostic_only_once(self, tmp_path):
        """Lines 651-654: second visit to map 37 does not re-trigger."""
        ag = _make_agent(tmp_path)
        ag._house_diag_done = True  # already done
        state = OverworldState(map_id=37, x=3, y=5)
        ag.memory.read_overworld_state = MagicMock(return_value=state)
        ag.controller = MagicMock()
        ag.collision_map = MagicMock()
        ag.collision_map.grid = [[1] * 10 for _ in range(9)]
        ag.turn_count = 1

        ag.run_overworld()

        # No DIAG event since _house_diag_done was already True
        assert not any("DIAG | House 1F" in e for e in ag.events)


# ===================================================================
# run_overworld -- Pallet Town Oak trigger (658-692)
# ===================================================================


class TestRunOverworldOakTrigger:
    """Cover lines 658-692: Pallet Town Oak trigger diagnostic."""

    def test_pallet_diag_at_y_le_3_no_party(self, tmp_path):
        """Lines 658-668: map 0, y<=3, no party -> diagnostic log + screenshot."""
        ag = _make_agent(tmp_path)
        state = OverworldState(map_id=0, x=5, y=3, party_count=0)
        ag.memory.read_overworld_state = MagicMock(return_value=state)
        ag.controller = MagicMock()
        ag.collision_map = MagicMock()
        ag.collision_map.grid = [[1] * 10 for _ in range(9)]
        ag.turn_count = 5  # 5 % 5 == 0 -> triggers log

        with patch.object(agent, "Image", None):
            ag.run_overworld()

        assert hasattr(ag, "_pallet_diag_done")
        assert ag._pallet_diag_done is True
        assert any("DIAG | Pallet" in e for e in ag.events)

    def test_oak_wait_at_y_le_1(self, tmp_path):
        """Lines 672-692: map 0, y<=1, no party -> Oak wait sequence."""
        ag = _make_agent(tmp_path)
        state = OverworldState(map_id=0, x=5, y=1, party_count=0)
        post_wait_state = OverworldState(map_id=40, x=5, y=3, party_count=0)
        # read_overworld_state called: (1) top of run_overworld, (2) inside oak trigger
        ag.memory.read_overworld_state = MagicMock(side_effect=[state, post_wait_state])
        ag.controller = MagicMock()
        ag.collision_map = MagicMock()
        ag.collision_map.grid = [[1] * 10 for _ in range(9)]
        ag.turn_count = 5  # divisible by 5

        with patch.object(agent, "Image", None):
            ag.run_overworld()

        assert hasattr(ag, "_oak_wait_done")
        assert ag._oak_wait_done is True
        assert any("OAK TRIGGER" in e for e in ag.events)
        # Should have called wait(600) for initial Oak walk
        ag.controller.wait.assert_any_call(600)
        # 4 rounds of mash_a(30) + wait(300)
        assert ag.controller.mash_a.call_count == 4
        for c in ag.controller.mash_a.call_args_list:
            assert c == call(30, delay=30)
        wait_300_calls = [c for c in ag.controller.wait.call_args_list if c == call(300)]
        assert len(wait_300_calls) == 4

    def test_oak_wait_only_once(self, tmp_path):
        """Lines 673: _oak_wait_done already set -> skip Oak sequence."""
        ag = _make_agent(tmp_path)
        ag._oak_wait_done = True
        ag._pallet_diag_done = True
        state = OverworldState(map_id=0, x=5, y=1, party_count=0)
        ag.memory.read_overworld_state = MagicMock(return_value=state)
        ag.controller = MagicMock()
        ag.collision_map = MagicMock()
        ag.collision_map.grid = [[1] * 10 for _ in range(9)]
        ag.turn_count = 5

        with patch.object(agent, "Image", None):
            ag.run_overworld()

        # No wait(600) call since _oak_wait_done was already True
        calls = [c for c in ag.controller.wait.call_args_list if c == call(600)]
        assert len(calls) == 0

    def test_pallet_diag_no_log_at_non_5_turn(self, tmp_path):
        """Line 661: turn_count % 5 != 0 -> no DIAG log."""
        ag = _make_agent(tmp_path)
        state = OverworldState(map_id=0, x=5, y=3, party_count=0)
        ag.memory.read_overworld_state = MagicMock(return_value=state)
        ag.controller = MagicMock()
        ag.collision_map = MagicMock()
        ag.collision_map.grid = [[1] * 10 for _ in range(9)]
        ag.turn_count = 7  # 7 % 5 != 0

        with patch.object(agent, "Image", None):
            ag.run_overworld()

        # _pallet_diag_done still set (screenshot unconditional), but no DIAG log
        diag_logs = [e for e in ag.events if "DIAG | Pallet" in e]
        assert len(diag_logs) == 0


# ===================================================================
# run_overworld -- B-button dispatch (699-700)
# ===================================================================


class TestRunOverworldBButton:
    """Cover lines 699-700: action == 'b' -> press B."""

    def test_b_action_presses_b(self, tmp_path):
        """Lines 698-700: action='b' dispatches press('b', ...)."""
        ag = _make_agent(tmp_path)
        state = OverworldState(map_id=99, x=5, y=5)
        ag.memory.read_overworld_state = MagicMock(return_value=state)
        ag.choose_overworld_action = MagicMock(return_value="b")
        ag.controller = MagicMock()
        ag.turn_count = 1

        ag.run_overworld()

        ag.controller.press.assert_called_once_with("b", hold_frames=20, release_frames=12)
        ag.controller.wait.assert_called_once_with(24)
        assert ag.last_overworld_action == "b"


# ===================================================================
# run_overworld -- Wait action dispatch
# ===================================================================


class TestRunOverworldWaitAction:
    """Cover action == 'wait' -> controller.wait() with no button press."""

    def test_wait_action_just_waits(self, tmp_path):
        ag = _make_agent(tmp_path)
        state = OverworldState(map_id=40, x=5, y=3)
        ag.memory.read_overworld_state = MagicMock(return_value=state)
        ag.choose_overworld_action = MagicMock(return_value="wait")
        ag.controller = MagicMock()
        ag.turn_count = 1

        ag.run_overworld()

        ag.controller.wait.assert_called_once_with(30)
        ag.controller.press.assert_not_called()
        ag.controller.move.assert_not_called()
        assert ag.last_overworld_action == "wait"


# ===================================================================
# run_overworld -- Waypoint info logging (711-715)
# ===================================================================


class TestRunOverworldWaypointLogging:
    """Cover lines 711-715: waypoint info in OVERWORLD log."""

    def test_waypoint_info_in_log(self, tmp_path):
        """Lines 710-715: route exists, waypoint available -> WP info in log."""
        routes = {"12": {"waypoints": [{"x": 8, "y": 10}, {"x": 8, "y": 4}]}}
        ag = _make_agent(tmp_path, routes=routes)
        state = OverworldState(map_id=12, x=5, y=10, badges=0, party_count=1)
        ag.memory.read_overworld_state = MagicMock(return_value=state)
        ag.choose_overworld_action = MagicMock(return_value="down")
        ag.controller = MagicMock()
        ag.turn_count = 50  # 50 % 50 == 0 -> logs
        # Set navigator map state
        ag.navigator.current_map = "12"
        ag.navigator.current_waypoint = 0

        ag.run_overworld()

        overworld_logs = [e for e in ag.events if "OVERWORLD" in e]
        assert len(overworld_logs) == 1
        assert "WP: 0" in overworld_logs[0]
        assert "(8,10)" in overworld_logs[0]

    def test_waypoint_info_list_route_format(self, tmp_path):
        """Lines 711-715: route as plain list (not dict with 'waypoints')."""
        routes = {"12": [{"x": 8, "y": 10}]}
        ag = _make_agent(tmp_path, routes=routes)
        state = OverworldState(map_id=12, x=5, y=10, badges=0, party_count=1)
        ag.memory.read_overworld_state = MagicMock(return_value=state)
        ag.choose_overworld_action = MagicMock(return_value="down")
        ag.controller = MagicMock()
        ag.turn_count = 50
        ag.navigator.current_map = "12"
        ag.navigator.current_waypoint = 0

        ag.run_overworld()

        overworld_logs = [e for e in ag.events if "OVERWORLD" in e]
        assert len(overworld_logs) == 1
        assert "WP:" in overworld_logs[0]

    def test_no_waypoint_info_when_past_all_waypoints(self, tmp_path):
        """Lines 713 guard: current_waypoint >= len -> no WP info."""
        routes = {"12": [{"x": 8, "y": 10}]}
        ag = _make_agent(tmp_path, routes=routes)
        state = OverworldState(map_id=12, x=5, y=10, badges=0, party_count=1)
        ag.memory.read_overworld_state = MagicMock(return_value=state)
        ag.choose_overworld_action = MagicMock(return_value="down")
        ag.controller = MagicMock()
        ag.turn_count = 50
        ag.navigator.current_map = "12"
        ag.navigator.current_waypoint = 5  # past all waypoints

        ag.run_overworld()

        overworld_logs = [e for e in ag.events if "OVERWORLD" in e]
        assert len(overworld_logs) == 1
        assert "WP:" not in overworld_logs[0]


# ===================================================================
# compute_fitness()
# ===================================================================


class TestComputeFitness:
    def test_returns_dict_with_expected_keys(self, tmp_path):
        ag = _make_agent(tmp_path)
        ag.turn_count = 42
        ag.battles_won = 3
        ag.maps_visited = {0, 1, 40}
        ag.events = ["[t] STUCK | some info", "[t] other", "[t] STUCK again"]
        ag.memory.read_overworld_state = MagicMock(
            return_value=OverworldState(map_id=1, x=5, y=10, badges=1, party_count=2)
        )

        result = ag.compute_fitness()
        assert result["turns"] == 42
        assert result["battles_won"] == 3
        assert result["maps_visited"] == 3
        assert result["final_map_id"] == 1
        assert result["final_x"] == 5
        assert result["final_y"] == 10
        assert result["badges"] == 1
        assert result["party_size"] == 2
        assert result["stuck_count"] == 2

    def test_empty_state(self, tmp_path):
        ag = _make_agent(tmp_path)
        ag.memory.read_overworld_state = MagicMock(return_value=OverworldState())
        result = ag.compute_fitness()
        assert result["turns"] == 0
        assert result["stuck_count"] == 0


# ===================================================================
# run() returns fitness dict
# ===================================================================


class TestRunReturnsFitness:
    def test_run_returns_fitness(self, tmp_path):
        ag = _make_agent(tmp_path)
        battle_none = BattleState(battle_type=0)
        overworld = OverworldState(map_id=0, x=5, y=5)

        ag.memory.read_battle_state = MagicMock(return_value=battle_none)
        ag.memory.read_overworld_state = MagicMock(return_value=overworld)

        with patch.object(agent, "Image", None):
            result = ag.run(max_turns=1)

        assert isinstance(result, dict)
        assert "turns" in result
        assert "battles_won" in result
        assert "final_map_id" in result


# ===================================================================
# --output-json CLI flag
# ===================================================================


class TestOutputJsonFlag:
    def test_output_json_writes_file(self, tmp_path):
        rom = tmp_path / "game.gb"
        rom.write_text("fake rom")
        output = tmp_path / "fitness.json"

        mock_agent = MagicMock()
        mock_agent.run.return_value = {"turns": 10, "battles_won": 0}

        with (
            patch(
                "sys.argv",
                ["agent.py", str(rom), "--max-turns", "5", "--output-json", str(output)],
            ),
            patch("agent.PokemonAgent", return_value=mock_agent),
        ):
            main()

        data = json.loads(output.read_text())
        assert data["turns"] == 10

    def test_output_json_creates_parent_dirs(self, tmp_path):
        rom = tmp_path / "game.gb"
        rom.write_text("fake rom")
        output = tmp_path / "deep" / "nested" / "fitness.json"

        mock_agent = MagicMock()
        mock_agent.run.return_value = {"turns": 5, "battles_won": 1}

        with (
            patch(
                "sys.argv",
                ["agent.py", str(rom), "--output-json", str(output)],
            ),
            patch("agent.PokemonAgent", return_value=mock_agent),
        ):
            main()

        assert output.exists()

    def test_no_output_json_by_default(self, tmp_path):
        rom = tmp_path / "game.gb"
        rom.write_text("fake rom")

        mock_agent = MagicMock()
        mock_agent.run.return_value = {"turns": 5}

        with (
            patch("sys.argv", ["agent.py", str(rom), "--max-turns", "5"]),
            patch("agent.PokemonAgent", return_value=mock_agent),
        ):
            main()

        # No fitness.json created anywhere in tmp_path
        assert not list(tmp_path.glob("**/fitness.json"))


# ===================================================================
# EVOLVE_PARAMS environment variable
# ===================================================================


def _make_agent_with_evolve(tmp_path, evolve_params=None, routes=None):
    """Build a PokemonAgent with EVOLVE_PARAMS env var set."""
    from collections import defaultdict

    mock_pb = MagicMock()
    mock_pb.memory = defaultdict(int)

    tc_path = tmp_path / "tc.json"
    rp = tmp_path / "routes.json"
    if routes is not None:
        rp.write_text(json.dumps(routes))

    env_patch = patch.dict(
        os.environ,
        {"EVOLVE_PARAMS": json.dumps(evolve_params)} if evolve_params else {},
        clear=False,
    )
    # Remove EVOLVE_PARAMS if not set, to ensure clean state
    if not evolve_params and "EVOLVE_PARAMS" in os.environ:
        del os.environ["EVOLVE_PARAMS"]

    with (
        env_patch,
        patch("agent.PyBoy", return_value=mock_pb),
        patch.object(agent, "TYPE_CHART_PATH", tc_path),
        patch.object(agent, "ROUTES_PATH", rp),
        patch.object(agent, "SCRIPT_DIR", tmp_path),
    ):
        ag = PokemonAgent(
            str(tmp_path / "fake.gb"),
            strategy="low",
        )

    ag.pokedex_dir = tmp_path / "pokedex"
    ag.pokedex_dir.mkdir(parents=True, exist_ok=True)
    ag.frames_dir = tmp_path / "frames"

    return ag


class TestEvolveParams:
    def test_valid_evolve_params_applied(self, tmp_path):
        """Lines 411-416: valid JSON sets evolve_params and door_cooldown."""
        params = {"stuck_threshold": 5, "door_cooldown": 12, "waypoint_skip_distance": 2, "axis_preference_map_0": "x"}
        ag = _make_agent_with_evolve(tmp_path, evolve_params=params)
        assert ag.evolve_params == params
        assert ag._evolve_door_cooldown == 12
        assert ag.navigator.stuck_threshold == 5
        assert ag.navigator.skip_distance == 2

    def test_invalid_json_ignored(self, tmp_path):
        """Lines 413-414: invalid JSON -> evolve_params stays empty."""
        from collections import defaultdict

        mock_pb = MagicMock()
        mock_pb.memory = defaultdict(int)
        tc_path = tmp_path / "tc.json"
        rp = tmp_path / "routes.json"

        with (
            patch.dict(os.environ, {"EVOLVE_PARAMS": "not json!!!"}),
            patch("agent.PyBoy", return_value=mock_pb),
            patch.object(agent, "TYPE_CHART_PATH", tc_path),
            patch.object(agent, "ROUTES_PATH", rp),
            patch.object(agent, "SCRIPT_DIR", tmp_path),
        ):
            ag = PokemonAgent(str(tmp_path / "fake.gb"), strategy="low")

        assert ag.evolve_params == {}
        assert ag._evolve_door_cooldown == 8

    def test_no_evolve_params_uses_defaults(self, tmp_path):
        """Default: no EVOLVE_PARAMS env -> defaults."""
        saved = os.environ.pop("EVOLVE_PARAMS", None)
        try:
            ag = _make_agent_with_evolve(tmp_path)
            assert ag.evolve_params == {}
            assert ag._evolve_door_cooldown == 8
            assert ag.navigator.stuck_threshold == 8
            assert ag.navigator.skip_distance == 3
        finally:
            if saved is not None:
                os.environ["EVOLVE_PARAMS"] = saved

    def test_evolve_params_print_logged(self, tmp_path, capsys):
        """Line 431: evolve params are printed when set."""
        params = {"stuck_threshold": 5, "door_cooldown": 10, "waypoint_skip_distance": 2, "axis_preference_map_0": "y"}
        _make_agent_with_evolve(tmp_path, evolve_params=params)
        output = capsys.readouterr().out
        assert "Evolve params" in output

    def test_battle_params_flow_to_strategy(self, tmp_path):
        """Battle params from EVOLVE_PARAMS flow to BattleStrategy."""
        params = {
            "stuck_threshold": 8,
            "door_cooldown": 8,
            "waypoint_skip_distance": 3,
            "axis_preference_map_0": "y",
            "hp_run_threshold": 0.35,
            "hp_heal_threshold": 0.4,
            "unknown_move_score": 20.0,
            "status_move_score": 5.0,
        }
        ag = _make_agent_with_evolve(tmp_path, evolve_params=params)
        assert ag.battle_strategy.hp_run_threshold == 0.35
        assert ag.battle_strategy.hp_heal_threshold == 0.4
        assert ag.battle_strategy.unknown_move_score == 20.0
        assert ag.battle_strategy.status_move_score == 5.0

    def test_no_battle_params_uses_defaults(self, tmp_path):
        """Without battle params in EVOLVE_PARAMS, BattleStrategy uses defaults."""
        saved = os.environ.pop("EVOLVE_PARAMS", None)
        try:
            ag = _make_agent_with_evolve(tmp_path)
            assert ag.battle_strategy.hp_run_threshold == 0.1
            assert ag.battle_strategy.hp_heal_threshold == 0.25
            assert ag.battle_strategy.unknown_move_score == 10.0
            assert ag.battle_strategy.status_move_score == 1.0
        finally:
            if saved is not None:
                os.environ["EVOLVE_PARAMS"] = saved


# ===================================================================
# Integration: run agent until Pokemon is selected
# ===================================================================


class TestLabPokemonSelection:
    """Integration test: agent navigates Oak's Lab and selects a starter."""

    def test_agent_selects_pokemon_within_1000_turns(self, tmp_path):
        """Agent should walk through lab phases and pick Charmander."""
        ag = _make_agent(tmp_path)

        # Simulated game state that responds to agent movement
        state = {"x": 3, "y": 2, "party_count": 0, "a_at_ball": 0}

        def read_overworld():
            return OverworldState(
                map_id=40,
                x=state["x"],
                y=state["y"],
                party_count=state["party_count"],
            )

        def on_move(direction):
            if direction == "down" and state["y"] < 11:
                state["y"] += 1
            elif direction == "up" and state["y"] > 0:
                # Pokeball table at y=3, x=6-8 blocks upward movement
                if 6 <= state["x"] <= 8 and state["y"] == 4:
                    pass  # face up but collide with table
                else:
                    state["y"] -= 1
            elif direction == "left" and state["x"] > 0:
                state["x"] -= 1
            elif direction == "right" and state["x"] < 10:
                state["x"] += 1

        def on_press(button, **kwargs):
            if button == "a" and state["x"] >= 6 and state["y"] >= 3 and state["y"] <= 4 and state["party_count"] == 0:
                state["a_at_ball"] += 1
                if state["a_at_ball"] >= 3:
                    state["party_count"] = 1

        ag.memory.read_overworld_state = read_overworld
        ag.memory.read_battle_state = MagicMock(
            return_value=BattleState(battle_type=0),
        )
        ag.controller = MagicMock()
        ag.controller.move = MagicMock(side_effect=on_move)
        ag.controller.press = MagicMock(side_effect=on_press)

        with patch.object(agent, "Image", None):
            fitness = ag.run(max_turns=1000)

        assert fitness["party_size"] == 1, f"Pokemon not selected. Final pos: ({state['x']}, {state['y']})"


# ===================================================================
# Game event publish error path in main()
# ===================================================================


class TestGameEventPublish:
    """Tests for real-time game event publishing architecture."""

    def test_game_publisher_setup_failure_is_caught(self, tmp_path, capsys):
        """Cover except branch when make_publisher fails during setup (agent.py lines 1219-1220).

        When make_publisher raises, game_pub stays None and the agent still runs.
        """
        rom = tmp_path / "game.gb"
        rom.write_text("fake rom")

        mock_agent = MagicMock()
        mock_agent.run.return_value = {"turns": 5}

        with (
            patch("sys.argv", ["agent.py", str(rom), "--max-turns", "5", "--telemetry-dir", str(tmp_path)]),
            patch("agent.PokemonAgent", return_value=mock_agent),
            patch("publisher.make_publisher", side_effect=RuntimeError("kafka down")),
        ):
            main()  # should not raise

        captured = capsys.readouterr()
        assert "game publisher setup failed" in captured.out
        assert "kafka down" in captured.out
        # collector should NOT have been reassigned since game_pub is None
        mock_agent.run.assert_called_once()

    def test_game_publisher_close_called_on_success(self, tmp_path):
        """Cover game_pub.close() path (agent.py lines 1231-1232).

        When make_publisher succeeds, game_pub.close() is called after run().
        """
        rom = tmp_path / "game.gb"
        rom.write_text("fake rom")

        mock_agent = MagicMock()
        mock_agent.run.return_value = {"turns": 5}

        mock_pub = MagicMock()
        mock_pub.close = MagicMock()

        with (
            patch("sys.argv", ["agent.py", str(rom), "--max-turns", "5", "--telemetry-dir", str(tmp_path)]),
            patch("agent.PokemonAgent", return_value=mock_agent),
            patch("publisher.make_publisher", return_value=mock_pub),
        ):
            main()

        # close() is called for both the game publisher and the general telemetry publisher
        # (both use make_publisher which returns the same mock here); at least one call covers the path.
        assert mock_pub.close.call_count >= 1

    def test_collector_emit_catches_publisher_error(self, capsys):
        """Cover _emit except branch (game_events.py lines 135-136).

        When publisher.publish() raises, the error is caught and printed,
        and the event is still stored locally.
        """
        from game_events import GameEventCollector

        failing_pub = MagicMock()
        failing_pub.publish.side_effect = RuntimeError("broker unreachable")

        collector = GameEventCollector(publisher=failing_pub)
        collector.milestone(turn=1, description="test milestone")

        # Event should still be collected locally despite publish failure
        assert len(collector.events) == 1
        assert collector.events[0]["event_type"] == "milestone"

        captured = capsys.readouterr()
        assert "publish error" in captured.out
        assert "broker unreachable" in captured.out

    def test_collector_publishes_events_in_realtime(self):
        """Verify events are published via the publisher in real-time."""
        from game_events import GameEventCollector

        mock_pub = MagicMock()
        collector = GameEventCollector(publisher=mock_pub)

        collector.battle(
            turn=1, player_hp=100, player_max_hp=100, enemy_hp=50, enemy_max_hp=50, action={"type": "fight"}
        )
        collector.overworld(turn=2, map_id=1, x=5, y=10, badges=0, party_count=1, action="move", stuck_turns=0)
        collector.map_change(turn=3, prev_map=0, new_map=1, x=5, y=10)
        collector.stuck(turn=4, map_id=1, x=5, y=10, last_action="move", streak=3)
        collector.milestone(turn=5, description="got badge")
        collector.session(turn=6, phase="start", battles_won=0, maps_visited=1)

        # All 6 events collected locally
        assert len(collector.events) == 6

        # All 6 events published in real-time (one publish call per emit)
        assert mock_pub.publish.call_count == 6

        # Verify event types in order
        published_types = [c.args[0]["event_type"] for c in mock_pub.publish.call_args_list]
        assert published_types == ["battle", "overworld", "map_change", "stuck", "milestone", "session"]

    def test_collector_without_publisher_still_collects(self):
        """Verify collector works without a publisher (publisher=None)."""
        from game_events import GameEventCollector

        collector = GameEventCollector()
        collector.milestone(turn=1, description="no publisher")

        assert len(collector.events) == 1
        assert collector.events[0]["event_type"] == "milestone"


# ===================================================================
# build_recorder
# ===================================================================


def test_build_recorder_returns_none_without_flag(tmp_path):
    from agent import build_recorder

    assert build_recorder(record=False, runs_dir=tmp_path, run_id="r", grabber=None) is None


def test_build_recorder_configures_run_dir(tmp_path):
    from agent import build_recorder

    rec = build_recorder(record=True, runs_dir=tmp_path, run_id="r", grabber=None, frame_interval=7)
    assert rec is not None
    assert rec.run_dir == tmp_path / "r"
    assert rec.frame_interval == 7


# ===================================================================
# --record CLI flag wiring
# ===================================================================


class TestRecordFlag:
    def test_record_flag_calls_recorder_start_and_finish(self, tmp_path):
        """When --record is passed, recorder.start() and recorder.finish() are called."""
        rom = tmp_path / "game.gb"
        rom.write_text("fake rom")
        runs_dir = tmp_path / "runs"

        mock_agent = MagicMock()
        mock_agent.run.return_value = {"turns": 5, "battles_won": 0}
        mock_recorder = MagicMock()

        with (
            patch(
                "sys.argv",
                ["agent.py", str(rom), "--record", "--runs-dir", str(runs_dir), "--telemetry-dir", ""],
            ),
            patch("agent.PokemonAgent", return_value=mock_agent),
            patch("agent.build_recorder", return_value=mock_recorder),
        ):
            main()

        mock_recorder.start.assert_called_once_with({"strategy": "low", "rom": str(rom)})
        mock_recorder.finish.assert_called_once_with({"turns": 5, "battles_won": 0})

    def test_record_frame_interval_passed_to_build_recorder(self, tmp_path):
        """--frame-interval is forwarded to build_recorder."""
        rom = tmp_path / "game.gb"
        rom.write_text("fake rom")
        runs_dir = tmp_path / "runs"

        mock_agent = MagicMock()
        mock_agent.run.return_value = {"turns": 3}

        with (
            patch(
                "sys.argv",
                [
                    "agent.py",
                    str(rom),
                    "--record",
                    "--runs-dir",
                    str(runs_dir),
                    "--frame-interval",
                    "15",
                    "--telemetry-dir",
                    "",
                ],
            ),
            patch("agent.PokemonAgent", return_value=mock_agent),
            patch("agent.build_recorder", return_value=None) as mock_br,
        ):
            main()

        assert mock_br.call_args.kwargs["frame_interval"] == 15


# ===================================================================
# --live CLI flag wiring
# ===================================================================


class TestLiveFlag:
    def test_live_flag_builds_producer_and_wires_to_recorder(self, tmp_path):
        """--live creates a LiveProducer and passes its .send as the live callback."""
        rom = tmp_path / "game.gb"
        rom.write_text("fake rom")
        runs_dir = tmp_path / "runs"

        mock_agent = MagicMock()
        mock_agent.run.return_value = {"turns": 5, "battles_won": 0}
        mock_recorder = MagicMock()
        mock_producer = MagicMock()

        with (
            patch(
                "sys.argv",
                ["agent.py", str(rom), "--live", "--runs-dir", str(runs_dir), "--telemetry-dir", ""],
            ),
            patch("agent.PokemonAgent", return_value=mock_agent),
            patch("agent.build_recorder", return_value=mock_recorder) as mock_br,
            patch("live_producer.LiveProducer", return_value=mock_producer),
        ):
            main()

        mock_recorder.start.assert_called_once()
        mock_recorder.finish.assert_called_once_with({"turns": 5, "battles_won": 0})
        # The live callback must be producer.send
        assert mock_br.call_args.kwargs.get("live") == mock_producer.send


# ===================================================================
# Fix 2: try/finally — recorder always finalizes on agent.run() raise
# ===================================================================


class TestRecorderFinalizeOnCrash:
    def test_recorder_finish_called_with_empty_dict_when_agent_run_raises(self, tmp_path):
        """If agent.run() raises, recorder.finish({}) must be called and the exception propagates."""
        rom = tmp_path / "game.gb"
        rom.write_text("fake rom")
        runs_dir = tmp_path / "runs"

        mock_agent = MagicMock()
        mock_agent.run.side_effect = RuntimeError("crash")
        mock_recorder = MagicMock()
        mock_game_pub = MagicMock()

        with (
            patch(
                "sys.argv",
                ["agent.py", str(rom), "--record", "--runs-dir", str(runs_dir), "--telemetry-dir", ""],
            ),
            patch("agent.PokemonAgent", return_value=mock_agent),
            patch("agent.build_recorder", return_value=mock_recorder),
            patch("publisher.make_publisher", return_value=mock_game_pub),
            pytest.raises(RuntimeError, match="crash"),
        ):
            main()

        mock_recorder.finish.assert_called_once_with({})

    def test_game_pub_closed_when_agent_run_raises(self, tmp_path):
        """If agent.run() raises, game_pub.close() must still be called."""
        rom = tmp_path / "game.gb"
        rom.write_text("fake rom")
        runs_dir = tmp_path / "runs"

        mock_agent = MagicMock()
        mock_agent.run.side_effect = RuntimeError("oops")
        mock_recorder = MagicMock()
        mock_game_pub = MagicMock()

        with (
            patch(
                "sys.argv",
                [
                    "agent.py",
                    str(rom),
                    "--record",
                    "--runs-dir",
                    str(runs_dir),
                    "--telemetry-dir",
                    str(tmp_path / "tel"),
                ],
            ),
            patch("agent.PokemonAgent", return_value=mock_agent),
            patch("agent.build_recorder", return_value=mock_recorder),
            patch("publisher.make_publisher", return_value=mock_game_pub),
            pytest.raises(RuntimeError, match="oops"),
        ):
            main()

        mock_game_pub.close.assert_called_once()


def _build_agent_with_script_dir(tmp_path, script_dir):
    """Construct a PokemonAgent with SCRIPT_DIR pinned (so notes.md = script_dir.parent)."""
    from collections import defaultdict

    mock_pb = MagicMock()
    mock_pb.memory = defaultdict(int)
    with (
        patch("agent.PyBoy", return_value=mock_pb),
        patch.object(agent, "TYPE_CHART_PATH", tmp_path / "tc.json"),
        patch.object(agent, "ROUTES_PATH", tmp_path / "routes.json"),
        patch.object(agent, "SCRIPT_DIR", script_dir),
    ):
        return PokemonAgent(str(tmp_path / "fake.gb"), strategy="low")


def test_evolve_params_seeded_from_notes(tmp_path, monkeypatch):
    """L2: a genome block in notes.md seeds evolve_params at startup."""
    monkeypatch.delenv("EVOLVE_PARAMS", raising=False)
    scripts_dir = tmp_path / "scripts"
    scripts_dir.mkdir()
    (tmp_path / "notes.md").write_text(
        '# Agent Notes\n<!-- autotune:genome\n{"stuck_threshold": 5, "door_cooldown": 12}\n-->\n'
    )
    ag = _build_agent_with_script_dir(tmp_path, scripts_dir)
    assert ag.evolve_params["stuck_threshold"] == 5
    assert ag._evolve_door_cooldown == 12


def test_env_evolve_params_override_notes(tmp_path, monkeypatch):
    """L2: EVOLVE_PARAMS env wins over the notes.md baseline."""
    monkeypatch.setenv("EVOLVE_PARAMS", '{"door_cooldown": 4}')
    scripts_dir = tmp_path / "scripts"
    scripts_dir.mkdir()
    (tmp_path / "notes.md").write_text('<!-- autotune:genome\n{"door_cooldown": 12}\n-->\n')
    ag = _build_agent_with_script_dir(tmp_path, scripts_dir)
    assert ag._evolve_door_cooldown == 4
