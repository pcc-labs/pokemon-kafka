#!/usr/bin/env python3
"""
Pokemon Agent — Autonomous turn-based RPG player via PyBoy.

Runs headless. Reads game state from memory. Makes decisions.
Sends inputs. Logs everything. Designed for stereOS + Tapes.

Usage:
    python3 agent.py path/to/pokemon_red.gb [--strategy low|medium|high]
"""

import argparse
import io
import json
import os
import random
import sys
import time
import uuid
from collections import deque
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

try:
    from pyboy import PyBoy
except ImportError:
    print("PyBoy not installed. Run: pip install pyboy")
    sys.exit(1)

try:
    from PIL import Image
except ImportError:
    Image = None

from game_events import GameEventCollector
from game_profile import RED_BLUE, YELLOW, detect_profile
from memory_file import MemoryFile
from memory_reader import (
    HEALING_ITEM_IDS,
    SPECIES_ID_MAP,
    TYPE_ID_MAP,
    BattleState,
    CollisionMap,
    MemoryReader,
    OverworldState,
)
from parcel_quest import ParcelQuest, QuestSignals
from pathfinding import astar_path
from recorder import RunRecorder
from world_map import WorldMap


def build_recorder(record, runs_dir, run_id, grabber, frame_interval=10, live=None):
    """Return a configured RunRecorder, or None when recording is disabled."""
    if not record:
        return None
    return RunRecorder(run_id, runs_dir, frame_grabber=grabber, frame_interval=frame_interval, live=live)


# ---------------------------------------------------------------------------
# Type chart (simplified — super effective multipliers)
# ---------------------------------------------------------------------------

SCRIPT_DIR = Path(__file__).parent
TYPE_CHART_PATH = SCRIPT_DIR.parent / "references" / "type_chart.json"
ROUTES_PATH = SCRIPT_DIR.parent / "references" / "routes.json"

# Extra A* g-cost the WorldMap planner pays to re-enter a tile where a wild encounter has fired.
# Routes equal-ish paths around already-seen tall grass (e.g. across Viridian Forest) so the agent
# walks into far fewer battles and actually reaches the exit within its turn budget. A wall-free
# detour up to this many tiles longer is preferred over re-crossing one known grass tile.
GRASS_ENCOUNTER_COST = 8

# Max fight turns the agent will spend in a single wild battle WITHOUT the enemy's HP dropping
# before it gives up and flees. Escapes livelocks where the only PP'd move can't end the fight
# (e.g. a 0-power status move scored as "unknown") — the agent would otherwise loop forever.
# Reset whenever real damage lands, so winnable battles are never abandoned.
WILD_BATTLE_PATIENCE = 10

# Gen-1's physical/special split is decided by a move's TYPE (not per-move): these types are
# physical (they hit the target's Defense); the rest (fire/water/grass/electric/psychic/ice/
# dragon) are special (they hit its Special). This matters against physical walls that pump
# Defense — Brock's Geodude/Onix have high Defense and low Special and boost Defense further, so a
# physical move (Scratch) stalls to ~0 while a resisted special move (Ember) still lands real
# damage. When our move stops denting the enemy we switch category instead of looping a dead move.
_PHYSICAL_TYPES = frozenset({"normal", "fighting", "flying", "poison", "ground", "rock", "bug", "ghost"})


def move_category(move_type: str) -> str:
    """Gen-1 physical/special category, decided by the move's type."""
    return "physical" if move_type in _PHYSICAL_TYPES else "special"


# Early-game scripted targets to get from Red's room to Oak's lab.
# Coords are taken from pret/pokered map object data.
EARLY_GAME_TARGETS = {
    38: {"name": "Red's bedroom", "target": (7, 1), "axis": "x"},
    37: {"name": "Red's house 1F", "target": (3, 9), "axis": "y"},
    # Map 0 (Pallet Town) uses waypoints from routes.json instead of a single target.
    # The waypoint path (8,10)→(8,4)→(8,1)→(8,0) follows the center corridor to Route 1.
}

# Move ID → (name, type, power, accuracy)
# Subset of Gen 1 moves for demonstration
MOVE_DATA = {
    0x01: ("Pound", "normal", 40, 100),
    0x0A: ("Scratch", "normal", 40, 100),
    0x21: ("Tackle", "normal", 35, 95),
    0x2D: ("Growl", "normal", 0, 100),  # 0x2D is Growl (status), NOT Ember — Ember is 0x34
    0x34: ("Ember", "fire", 40, 100),
    0x37: ("Water Gun", "water", 40, 100),
    0x49: ("Vine Whip", "grass", 35, 100),
    0x55: ("Thunderbolt", "electric", 95, 100),
    0x56: ("Thunder Wave", "electric", 0, 100),
    0x59: ("Thunder", "electric", 120, 70),
    0x3A: ("Ice Beam", "ice", 95, 100),
    0x3F: ("Flamethrower", "fire", 95, 100),
    0x39: ("Surf", "water", 95, 100),
    0x16: ("Razor Leaf", "grass", 55, 95),
    0x5D: ("Psychic", "psychic", 90, 100),
    0x1A: ("Body Slam", "normal", 85, 100),
    0x26: ("Earthquake", "ground", 100, 100),
    0x00: ("(No move)", "none", 0, 0),
}


def load_type_chart():
    """Load type effectiveness chart from JSON."""
    if TYPE_CHART_PATH.exists():
        with open(TYPE_CHART_PATH) as f:
            return json.load(f)
    # Fallback: minimal chart
    return {
        "fire": {"grass": 2.0, "water": 0.5, "fire": 0.5, "ice": 2.0},
        "water": {"fire": 2.0, "grass": 0.5, "water": 0.5, "ground": 2.0, "rock": 2.0},
        "grass": {"water": 2.0, "fire": 0.5, "grass": 0.5, "ground": 2.0, "rock": 2.0},
        "electric": {"water": 2.0, "grass": 0.5, "electric": 0.5, "ground": 0.0, "flying": 2.0},
        "ground": {"fire": 2.0, "electric": 2.0, "grass": 0.5, "flying": 0.0, "rock": 2.0},
        "ice": {"grass": 2.0, "ground": 2.0, "flying": 2.0, "dragon": 2.0, "fire": 0.5},
        "psychic": {"fighting": 2.0, "poison": 2.0, "psychic": 0.5},
        "normal": {"rock": 0.5, "ghost": 0.0},
    }


# ---------------------------------------------------------------------------
# Input helpers
# ---------------------------------------------------------------------------


class GameController:
    """Send inputs to PyBoy with proper frame timing."""

    def __init__(self, pyboy: PyBoy):
        self.pyboy = pyboy

    def press(self, button: str, hold_frames: int = 20, release_frames: int = 10):
        """Press and release a button with frame advance.

        Uses pyboy.button() which handles press+hold+release internally.
        button_press()/button_release() do not work reliably in headless mode.
        """
        self.pyboy.button(button, delay=hold_frames)
        for _ in range(release_frames):
            self.pyboy.tick()

    def wait(self, frames: int = 30):
        """Advance N frames without input."""
        for _ in range(frames):
            self.pyboy.tick()

    def move(self, direction: str):
        """Move a single tile in the overworld.

        hold_frames=20 held the d-pad long enough to walk *two* tiles per call, so the agent could
        only ever land on same-parity columns — fatal where a one-tile-wide gap sits on the other
        parity (e.g. the Route 1 ledge gap at x=9). 8 frames is enough to commit exactly one step.
        """
        self.press(direction, hold_frames=8, release_frames=8)
        self.wait(30)

    def mash_a(self, times: int = 5, delay: int = 20):
        """Mash A to advance text boxes."""
        for _ in range(times):
            self.press("a")
            self.wait(delay)

    def navigate_menu(self, target_index: int, current_index: int = 0):
        """Move cursor to a menu item (assumes vertical menu)."""
        diff = target_index - current_index
        direction = "down" if diff > 0 else "up"
        for _ in range(abs(diff)):
            self.press(direction)
            self.wait(8)
        self.press("a")
        self.wait(20)

    # The Gen-1 battle menu is a 2x2 grid, not a vertical list:
    #     FIGHT  PKMN
    #     ITEM   RUN
    _BATTLE_MENU_PATH = {
        "fight": (),
        "pkmn": ("right",),
        "item": ("down",),
        "run": ("down", "right"),
    }

    def battle_menu_select(self, target: str):
        """Select a corner of the 2x2 battle menu (fight/pkmn/item/run) and confirm with A.

        ``navigate_menu`` only walks vertically, so it can never reach the right column
        (PKMN/RUN) — which is why fleeing never worked and the cursor desynced (the menu
        remembers its last position, so a stale RUN cursor made a later "press A for FIGHT"
        re-pick RUN, freezing the fight). Normalize to FIGHT (top-left) first, then walk the
        real grid.
        """
        # Use the default (longer) press timing — the same as ``navigate_menu``, which works for
        # the move list. The 8-frame overworld timing is too short for the battle-menu cursor to
        # register a move, which left the cursor stuck on FIGHT and the flee never happening.
        self.press("up")  # normalize: up+left -> FIGHT (top-left)
        self.wait(8)
        self.press("left")
        self.wait(8)
        for direction in self._BATTLE_MENU_PATH[target]:
            self.press(direction)
            self.wait(8)
        self.press("a")
        self.wait(20)


# ---------------------------------------------------------------------------
# Battle strategy
# ---------------------------------------------------------------------------


class BattleStrategy:
    """Heuristic-based battle decision engine."""

    def __init__(
        self,
        type_chart: dict,
        hp_run_threshold: float = 0.1,
        hp_heal_threshold: float = 0.25,
        unknown_move_score: float = 10.0,
        status_move_score: float = 1.0,
        force_fight: bool | None = None,
    ):
        self.type_chart = type_chart
        self.hp_run_threshold = hp_run_threshold
        self.hp_heal_threshold = hp_heal_threshold
        # Data-collection mode: never flee on low HP, so every battle resolves to a win or a
        # faint — yielding clean win/loss labels for the win-probability dataset. The stall guard
        # still breaks truly unwinnable loops. Off by default; AUTOTUNE_FORCE_FIGHT=1 turns it on.
        if force_fight is None:
            force_fight = os.environ.get("AUTOTUNE_FORCE_FIGHT", "") not in ("", "0", "false", "False")
        self.force_fight = force_fight
        self.unknown_move_score = unknown_move_score
        self.status_move_score = status_move_score
        self._run_attempts = 0
        # Stall guard: count fight turns in the current wild battle without enemy-HP progress.
        self._wild_fight_turns = 0
        self._last_enemy_hp: int | None = None
        # Move-category selection: the last damage each category dealt to the current enemy (-1 =
        # not yet tried), the category of our last move (to attribute the next enemy-HP delta), and
        # the enemy's species (to reset all of this when a new Pokemon is sent out).
        self._cat_dmg: dict[str, int] = {"physical": -1, "special": -1}
        self._last_move_cat: str | None = None
        self._stall_enemy_species: int | None = None

    def score_move(self, move_id: int, move_pp: int, enemy_type: str = "normal") -> float:
        """Score a move based on power, PP, and type effectiveness."""
        if move_pp <= 0:
            return -1.0
        if move_id not in MOVE_DATA:
            return self.unknown_move_score

        name, move_type, power, accuracy = MOVE_DATA[move_id]
        if power == 0:
            return self.status_move_score

        effectiveness = 1.0
        if move_type in self.type_chart:
            effectiveness = self.type_chart[move_type].get(enemy_type, 1.0)

        return power * (accuracy / 100.0) * effectiveness

    def choose_action(self, battle: BattleState, bag_healing: tuple[int, int] | None = None) -> dict:
        """
        Decide what to do in battle.  Fight-first: leveling up beats running.

        Args:
            battle: Current battle state from memory.
            bag_healing: (bag_index, item_id) from find_healing_item(), or None.

        Returns:
            {"action": "fight", "move_index": 0-3}
            {"action": "item", "item": "potion", "bag_index": N}
            {"action": "switch", "slot": 1-5}
            {"action": "run"}
        """
        hp_ratio = battle.player_hp / max(battle.player_max_hp, 1)
        enemy_type = battle.enemy_type_name

        # Stall guard (both battle types): if the enemy's HP isn't dropping across turns, we're
        # stuck — PP-dry damaging moves, a 0-power move scored as "unknown", or a desynced battle
        # menu (observed: the Brock fight frozen at a constant enemy HP for 40+ turns). Track
        # progress and recover before looping forever; real damage resets the counter, so winnable
        # fights are never abandoned. Recovery differs by battle type (below).
        # A new enemy Pokemon (e.g. Brock's Onix after Geodude faints) resets the per-enemy stall
        # state and the category-damage memory, so we re-probe from scratch.
        if battle.enemy_species != self._stall_enemy_species:
            self._stall_enemy_species = battle.enemy_species
            self._wild_fight_turns = 0
            self._last_enemy_hp = None
            self._cat_dmg = {"physical": -1, "special": -1}
            self._last_move_cat = None
        # Attribute the enemy-HP delta since our last move to that move's category — this is how we
        # LEARN (rather than are told) which category actually damages this enemy: a physical wall
        # (Onix: high Defense, low Special) takes far more from a resisted special move (Ember) than
        # a physical one (Scratch). Record the LAST amount (including 0), not the best, so a move
        # that DEGRADES — a physical move once a Defense-Curl'd Geodude walls it — is demoted and we
        # commit to the category still landing damage.
        if self._last_enemy_hp is not None:
            dealt = self._last_enemy_hp - battle.enemy_hp
            if dealt > 0:
                self._wild_fight_turns = 0
            if self._last_move_cat is not None and dealt >= 0:
                self._cat_dmg[self._last_move_cat] = dealt
        self._last_enemy_hp = battle.enemy_hp
        if self._wild_fight_turns >= WILD_BATTLE_PATIENCE:
            if battle.battle_type == 1:
                # Wild: keep fleeing until the battle ends. Unlike the low-HP run (capped, because
                # fighting on can still level us), a stall means fighting is futile — never fall
                # back to fight here.
                return {"action": "run"}
            if battle.battle_type == 2:
                # Trainer battles can't be fled, so a stall is a stuck menu, not a futile matchup.
                # Reset the battle menu (mash B) so the next FIGHT lands. Reset the counter so we
                # alternate unstick/fight rather than unsticking forever.
                self._wild_fight_turns = 0
                return {"action": "unstick"}

        # Only run when critically low (<10%) in wild battles AND run hasn't
        # failed 3 times already.  Leveling up is more valuable than preserving HP.
        if hp_ratio < self.hp_run_threshold and battle.battle_type == 1 and not self.force_fight:
            if self._run_attempts < 3:
                self._run_attempts += 1
                return {"action": "run"}
            # Fall through to fight — running isn't working

        if hp_ratio < self.hp_heal_threshold and bag_healing is not None:
            bag_index, item_id = bag_healing
            return {"action": "item", "item": HEALING_ITEM_IDS.get(item_id, "potion"), "bag_index": bag_index}

        # Score all moves using the enemy's actual type for effectiveness.
        scored = [
            (i, self.score_move(battle.moves[i], battle.move_pp[i], enemy_type))
            for i in range(4)
            if battle.moves[i] != 0x00
        ]
        move_index = self._pick_move(battle, scored)

        chosen = MOVE_DATA.get(battle.moves[move_index] if 0 <= move_index < len(battle.moves) else 0)
        self._last_move_cat = move_category(chosen[1]) if (chosen and chosen[2] > 0) else None

        if battle.battle_type in (1, 2):
            self._wild_fight_turns += 1  # advance the stall guard (reset above on real damage)
        return {"action": "fight", "move_index": move_index}

    def _pick_move(self, battle, scored: list[tuple[int, float]]) -> int:
        """Choose a move index. Among damaging moves, when both a physical and a special option
        exist, try each once and then commit to whichever dealt more (learned per enemy) — so the
        agent picks the move that actually damages a wall, not just the type-best one. Otherwise
        (or for status/Struggle fallbacks) take the highest-scored move."""
        if not scored or all(s < 0 for _, s in scored):
            return 0  # No PP left — Struggle auto-triggers, just press FIGHT.

        damaging = [
            (i, s, move_category(MOVE_DATA[battle.moves[i]][1]))
            for i, s in scored
            if s > 0 and MOVE_DATA.get(battle.moves[i], ("", "", 0))[2] > 0
        ]
        cats = {c for _, _, c in damaging}
        if len(cats) == 2:
            untried = [c for c in cats if self._cat_dmg[c] < 0]
            target = None
            if len(untried) == 1:
                target = untried[0]  # explore the category we haven't measured yet
            elif not untried:
                target = max(cats, key=lambda c: self._cat_dmg[c])  # commit to the higher-damage one
            if target is not None:
                return max((m for m in damaging if m[2] == target), key=lambda m: m[1])[0]

        pool = damaging or scored
        return max(pool, key=lambda m: m[1])[0]


# ---------------------------------------------------------------------------
# Overworld navigation
# ---------------------------------------------------------------------------


class Navigator:
    """Simple overworld movement."""

    def __init__(self, routes: dict, stuck_threshold: int = 8, skip_distance: int = 3, naive: bool | None = None):
        self.routes = routes
        self.current_waypoint = 0
        self.current_map = None
        self.stuck_threshold = stuck_threshold
        self.skip_distance = skip_distance
        # DEMO_NAIVE=1 strips the learned early-game scaffolding (scripted targets + route
        # waypoints) so the agent wanders like the pre-observation baseline. Off by default.
        if naive is None:
            naive = os.environ.get("DEMO_NAIVE", "") not in ("", "0", "false", "False")
        self.naive = naive
        # An optional per-turn override target (same shape as EARLY_GAME_TARGETS entries) set by
        # the Oak's Parcel quest; takes precedence over the baked early-game targets when present.
        self.quest_target: dict | None = None

    def _add_direction(self, directions: list[str], direction: str | None):
        """Append a direction once while preserving order."""
        if direction and direction not in directions:
            directions.append(direction)

    def _direction_toward_target(
        self,
        state: OverworldState,
        target_x: int,
        target_y: int,
        axis_preference: str = "x",
        stuck_turns: int = 0,
    ) -> str | None:
        """Choose a movement direction and rotate alternatives when blocked."""
        horizontal = None
        vertical = None

        if state.x < target_x:
            horizontal = "right"
        elif state.x > target_x:
            horizontal = "left"

        if state.y < target_y:
            vertical = "down"
        elif state.y > target_y:
            vertical = "up"

        ordered: list[str] = []

        primary = [horizontal, vertical] if axis_preference == "x" else [vertical, horizontal]
        secondary = [vertical, horizontal] if axis_preference == "x" else [horizontal, vertical]

        for direction in primary:
            self._add_direction(ordered, direction)
        for direction in secondary:
            self._add_direction(ordered, direction)

        # Only add backward directions after being stuck a while
        if stuck_turns >= 8:
            for direction in ("up", "right", "down", "left"):
                self._add_direction(ordered, direction)

        if not ordered:
            return None

        # Random jitter to break deterministic oscillation loops
        if stuck_turns >= 20:
            return random.choice(["up", "down", "left", "right"])

        # At low stuck counts, only cycle through forward directions
        forward_count = min(2, len(ordered))
        if stuck_turns < 8:
            return ordered[stuck_turns % forward_count]
        return ordered[stuck_turns % len(ordered)]

    def _try_astar(self, state: OverworldState, target_x: int, target_y: int, collision_grid: list) -> str | None:
        """Try A* pathfinding to target. Returns first direction or None."""
        screen_target_row = 4 + (target_y - state.y)
        screen_target_col = 4 + (target_x - state.x)
        if 0 <= screen_target_row < 9 and 0 <= screen_target_col < 10:
            result = astar_path(collision_grid, (4, 4), (screen_target_row, screen_target_col))
            if result["status"] in ("success", "partial") and result["directions"]:
                return result["directions"][0]
        return None

    def next_direction(
        self,
        state: OverworldState,
        turn: int = 0,
        stuck_turns: int = 0,
        collision_grid: list | None = None,
    ) -> str | None:
        """Get the next direction to move based on current position and route plan."""
        map_key = str(state.map_id)

        # Reset waypoint index on map change
        if map_key != self.current_map:
            self.current_map = map_key
            self.current_waypoint = 0

        # DEMO_NAIVE: skip scripted targets + route waypoints entirely and just cycle directions,
        # reproducing the aimless early-game wandering from before the agent learned to route.
        if self.naive:
            directions = ["down", "right", "down", "left", "up", "down"]
            return directions[turn % len(directions)]

        # An active quest override wins over the baked early-game targets (e.g. the map-0 rule).
        special_target = self.quest_target
        if special_target is None:
            special_target = EARLY_GAME_TARGETS.get(state.map_id)
            # Map 0: after getting a Pokemon, head north to Route 1 exit
            if state.map_id == 0 and state.party_count > 0:
                special_target = {"name": "Route 1 exit", "target": (10, 0), "axis": "y", "at_target": "up"}
        if special_target:
            target_x, target_y = special_target["target"]
            # At target: use at_target hint to walk through doors/grass
            if state.x == target_x and state.y == target_y:
                return special_target.get("at_target", "down")
            if collision_grid is not None:
                astar_dir = self._try_astar(state, target_x, target_y, collision_grid)
                if astar_dir is not None:
                    return astar_dir
            return self._direction_toward_target(
                state,
                target_x,
                target_y,
                axis_preference=special_target.get("axis", "x"),
                stuck_turns=stuck_turns,
            )

        if map_key not in self.routes:
            # No route data — cycle directions to explore and find exits
            directions = ["down", "right", "down", "left", "up", "down"]
            return directions[turn % len(directions)]

        route = self.routes[map_key]
        waypoints = route["waypoints"] if isinstance(route, dict) and "waypoints" in route else route
        if self.current_waypoint >= len(waypoints):
            # Loop waypoints if route has "loop": true
            if isinstance(route, dict) and route.get("loop"):
                self.current_waypoint = 0
            else:
                return None  # Route complete

        target = waypoints[self.current_waypoint]
        tx, ty = target["x"], target["y"]

        if state.x == tx and state.y == ty:
            self.current_waypoint += 1
            return self.next_direction(state, turn=turn, stuck_turns=stuck_turns, collision_grid=collision_grid)

        # Skip waypoint if close enough but stuck too long
        dist = abs(state.x - tx) + abs(state.y - ty)
        if (
            stuck_turns >= self.stuck_threshold
            and dist <= self.skip_distance
            and self.current_waypoint < len(waypoints) - 1
        ):
            self.current_waypoint += 1
            return self.next_direction(state, turn=turn, stuck_turns=0, collision_grid=collision_grid)

        if collision_grid is not None:
            astar_dir = self._try_astar(state, tx, ty, collision_grid)
            if astar_dir is not None:
                return astar_dir

        return self._direction_toward_target(state, tx, ty, stuck_turns=stuck_turns)


# ---------------------------------------------------------------------------
# FLE-style backtracking
# ---------------------------------------------------------------------------


@dataclass
class Snapshot:
    """A saved game state for backtracking."""

    state_bytes: io.BytesIO
    map_id: int
    x: int
    y: int
    turn: int
    attempts: int = 0


class BacktrackManager:
    """Save/restore game state to escape stuck navigation."""

    def __init__(self, max_snapshots: int = 8, restore_threshold: int = 15, max_attempts: int = 3):
        self.snapshots: deque[Snapshot] = deque(maxlen=max_snapshots)
        self.max_snapshots = max_snapshots
        self.restore_threshold = restore_threshold
        self.max_attempts = max_attempts
        self.total_restores = 0

    def save_snapshot(self, pyboy, state: OverworldState, turn: int):
        """Capture current game state into an in-memory snapshot."""
        buf = io.BytesIO()
        pyboy.save_state(buf)
        buf.seek(0)
        self.snapshots.append(Snapshot(buf, state.map_id, state.x, state.y, turn))

    def should_restore(self, stuck_turns: int) -> bool:
        """Check if we should restore a snapshot based on stuck duration."""
        if stuck_turns < self.restore_threshold or not self.snapshots:
            return False
        return any(s.attempts < self.max_attempts for s in self.snapshots)

    def restore(self, pyboy) -> Snapshot | None:
        """Restore the most recent viable snapshot. Returns it or None."""
        for i in range(len(self.snapshots) - 1, -1, -1):
            snap = self.snapshots[i]
            if snap.attempts < self.max_attempts:
                del self.snapshots[i]
                snap.state_bytes.seek(0)
                pyboy.load_state(snap.state_bytes)
                snap.attempts += 1
                self.total_restores += 1
                if snap.attempts < self.max_attempts:
                    self.snapshots.append(snap)  # keep for more attempts
                return snap
        return None


# ---------------------------------------------------------------------------
# Strategy engine
# ---------------------------------------------------------------------------


class StrategyEngine:
    """Controls intelligence level based on strategy tier."""

    STUCK_THRESHOLD = 10

    def __init__(self, tier: str, notes_path: str | None = None):
        self.tier = tier
        self.notes: MemoryFile | None = None
        if tier in ("medium", "high") and notes_path:
            self.notes = MemoryFile(notes_path)

    def should_call_llm(self, stuck_turns: int = 0, map_changed: bool = False) -> bool:
        """Determine if an LLM call should be made this turn."""
        if self.tier == "low":
            return False
        if self.tier == "high":
            return True
        # medium: call on triggers only
        return stuck_turns >= self.STUCK_THRESHOLD or map_changed


# ---------------------------------------------------------------------------
# Main agent loop
# ---------------------------------------------------------------------------


class PokemonAgent:
    """Autonomous Pokemon player."""

    def __init__(self, rom_path: str, strategy: str = "low", screenshots: bool = False, game: str | None = None):
        self.rom_path = rom_path
        self.pyboy = PyBoy(rom_path, window="null")
        self.controller = GameController(self.pyboy)
        overrides = {"red_blue": RED_BLUE, "yellow": YELLOW}
        self.profile = overrides.get(game or "") or detect_profile(self.pyboy)
        self.memory = MemoryReader(self.pyboy, self.profile)
        self.type_chart = load_type_chart()
        self.battle_strategy = BattleStrategy(self.type_chart)  # re-created below with evolve params
        self.strategy_engine = StrategyEngine(
            strategy,
            notes_path=str(SCRIPT_DIR.parent / "notes.md") if strategy != "low" else None,
        )
        self.turn_count = 0
        self.battles_won = 0
        self.screenshots = screenshots
        self.last_overworld_state: OverworldState | None = None
        self.last_overworld_action: str | None = None
        self.stuck_turns = 0
        self._last_progress_turn = 0  # turn of last meaningful progress (map change, battle win)
        self.recent_positions: list[tuple[int, int, int]] = []
        self.maps_visited: set[int] = set()
        self.events: list[str] = []
        self.collector = GameEventCollector(game=self.profile.name)
        self.collision_map = CollisionMap()
        self.door_cooldown: int = 0  # Steps to walk away from door after exiting a building
        # Oak's Parcel quest: drives the Viridian Mart pickup → Oak delivery → Old-Man gate, the
        # scripted progression that pure waypoint navigation cannot pass on its own.
        self.parcel_quest = ParcelQuest()
        # Accumulated occupancy map per location: every turn's collision window is stamped in, so
        # navigation can pathfind over the whole explored map instead of the 9x10 screen window.
        self.world = WorldMap()
        # When set, the accumulated WorldMap is loaded from / periodically saved to this file, so a
        # segmented run (reset turns) keeps the geometry it has already learned across processes.
        self.worldmap_file: str | None = None
        self._last_logged_map: int | None = None
        # True while the quest is actively steering; suppresses the backtrack/stall restores that
        # were tuned around the old broken collision grid and now thrash the agent backward.
        self._quest_nav_active = False
        self.encounter_log: list[dict] = []
        self._current_enemy_species: str = ""
        self._current_enemy_type: str = ""
        self._pre_battle_species: list[int] = []
        self._pre_battle_level: int = 0
        self.evolution_log: list[dict] = []
        self.level_ups: int = 0
        # Per-battle tracking (snapshotted at battle start; battle RAM clears at end).
        self._battle_start_turn: int = 0
        self._battle_type: int = 0
        self._battle_map_id: int = 0
        self._battle_opponent_species: str = ""
        self._battle_opponent_level: int = 0
        # Brock (gym 1) outcome, recorded once. Map id is parameterized because the
        # Pewter Gym interior is its own map (not Pewter City map 2); set BROCK_MAP_ID
        # once discovered to pin detection, otherwise fall back to a level heuristic.
        self.brock_map_id: int | None = int(os.environ["BROCK_MAP_ID"]) if os.environ.get("BROCK_MAP_ID") else None
        self.brock_turns: int | None = None
        self.brock_won: bool | None = None
        self.brock_lead_species: str | None = None
        self.brock_lead_level: int | None = None

        # Screenshot output directory
        self.frames_dir = SCRIPT_DIR.parent / "frames"
        if self.screenshots:
            self.frames_dir.mkdir(parents=True, exist_ok=True)

        # Pokedex log directory
        self.pokedex_dir = SCRIPT_DIR.parent / "pokedex"
        self.pokedex_dir.mkdir(parents=True, exist_ok=True)

        # Load routes (waypoint file is per-game: Yellow rearranged some maps)
        routes = {}
        routes_path = ROUTES_PATH.parent / self.profile.routes_file
        if routes_path.exists():
            with open(routes_path) as f:
                routes = json.load(f)
        self.navigator = Navigator(routes)  # re-created below with evolve params

        # Apply evolvable parameters from environment (set by evolve.py)
        self.evolve_params = {}
        evolve_json = os.environ.get("EVOLVE_PARAMS")
        if evolve_json:
            try:
                self.evolve_params = json.loads(evolve_json)
            except json.JSONDecodeError:
                pass
        # Baseline from autotune's persisted genome in notes.md (env above overrides it).
        from autotune_bridge import load_genome_from_notes

        notes_genome = load_genome_from_notes(SCRIPT_DIR.parent / "notes.md")
        if notes_genome:
            self.evolve_params = {**notes_genome, **self.evolve_params}
        if "door_cooldown" in self.evolve_params:
            self._evolve_door_cooldown = int(self.evolve_params["door_cooldown"])
        else:
            self._evolve_door_cooldown = 8

        # Backtracking support (FLE-style)
        self.backtrack = BacktrackManager(
            max_snapshots=int(self.evolve_params.get("bt_max_snapshots", 8)),
            restore_threshold=int(self.evolve_params.get("bt_restore_threshold", 15)),
            max_attempts=int(self.evolve_params.get("bt_max_attempts", 3)),
        )
        self._bt_snapshot_interval = int(self.evolve_params.get("bt_snapshot_interval", 50))
        self._bt_last_map_id: int | None = None

        # Rebuild navigator and battle strategy with evolved params
        if self.evolve_params:
            self.navigator = Navigator(
                routes,
                stuck_threshold=int(self.evolve_params.get("stuck_threshold", 8)),
                skip_distance=int(self.evolve_params.get("waypoint_skip_distance", 3)),
            )
            self.battle_strategy = BattleStrategy(
                self.type_chart,
                hp_run_threshold=float(self.evolve_params.get("hp_run_threshold", 0.2)),
                hp_heal_threshold=float(self.evolve_params.get("hp_heal_threshold", 0.25)),
                unknown_move_score=float(self.evolve_params.get("unknown_move_score", 10.0)),
                status_move_score=float(self.evolve_params.get("status_move_score", 1.0)),
            )

        print(f"[agent] Loaded ROM: {rom_path}")
        print(f"[agent] Game: {self.profile.label} ({self.profile.name})")
        print(f"[agent] Strategy: {strategy}")
        if self.evolve_params:
            print(f"[agent] Evolve params: {json.dumps(self.evolve_params)}")
        print("[agent] Running headless — no display")

    def update_overworld_progress(self, state: OverworldState):
        """Track whether the last overworld action moved the player."""
        pos = (state.map_id, state.x, state.y)

        # Milestone detection (before adding to maps_visited)
        if state.map_id == 1 and state.map_id not in self.maps_visited:
            self.log("MILESTONE | Reached Viridian City!")
            self.collector.milestone(self.turn_count, "Reached Viridian City!")

        self.maps_visited.add(state.map_id)

        if self.last_overworld_state is None:
            self.recent_positions.append(pos)
            return

        if state.map_id != self.last_overworld_state.map_id:
            self.stuck_turns = 0
            self._last_progress_turn = self.turn_count
            self.recent_positions.clear()
            self.recent_positions.append(pos)
            # Set door cooldown when exiting interior maps to avoid re-entry.
            # Houses (37, 38) get the full cooldown (down then left).
            # Oak's Lab (40) gets a short cooldown (3 = left only) because
            # its exit at (5,11) is near the south boundary — "down" would
            # trap the agent at y=12 instead of letting it head north.
            prev = self.last_overworld_state.map_id
            if prev in (37, 38) and state.map_id == 0:
                self.door_cooldown = self._evolve_door_cooldown
            elif prev == 40 and state.map_id == 0:
                self.door_cooldown = 3  # sidestep left to clear lab door
            self.log(f"MAP CHANGE | {prev} -> {state.map_id} | Pos: ({state.x}, {state.y})")
            self.collector.map_change(self.turn_count, prev, state.map_id, state.x, state.y)
            return

        # Detect oscillation: if current position was visited recently,
        # increment stuck counter so the navigator tries alternate directions.
        if pos in self.recent_positions:
            self.stuck_turns += 1
        else:
            self.stuck_turns = 0

        self.recent_positions.append(pos)
        if len(self.recent_positions) > 16:
            self.recent_positions.pop(0)

        if self.stuck_turns in {2, 5, 10, 20}:
            self.log(
                f"STUCK | Map: {state.map_id} | Pos: ({state.x}, {state.y}) | "
                f"Last move: {self.last_overworld_action} | Streak: {self.stuck_turns}"
            )
            self.collector.stuck(
                self.turn_count,
                state.map_id,
                state.x,
                state.y,
                self.last_overworld_action,
                self.stuck_turns,
            )

    def choose_overworld_action(self, state: OverworldState) -> str:
        """Pick the next overworld action."""
        if state.text_box_active:
            # Discovery capture: read the sign / NPC dialogue on screen before dismissing it.
            # Text renders progressively and persists across frames, so only emit when the decoded
            # string changes (dedup) to avoid spamming the same line every turn.
            text = self.memory.read_dialogue()
            if text and text != getattr(self, "_last_discovery", None):
                self._last_discovery = text
                self.log(f'DISCOVERY | map={state.map_id} pos=({state.x},{state.y}) text="{text}"')
                self.collector.discovery(self.turn_count, state.map_id, state.x, state.y, text)
            return "a"

        # Viridian Mart parcel cutscene (pret ViridianMartDefaultScript): entering the Mart shows
        # the clerk's text, then SIMULATES the joypad to walk the player to the counter and hands
        # over OAK'S PARCEL. Our own directional inputs fight that simulated movement, so until the
        # parcel is in the bag we just advance text / wait and let the script run.
        if state.map_id == 42 and not self.memory.has_parcel() and not self.memory.has_pokedex():
            return "a"

        # After exiting a building, walk away from the door to avoid re-entry
        if self.door_cooldown > 0:
            self.door_cooldown -= 1
            if self.door_cooldown >= 6:
                self.controller.wait(60)  # let game scripts complete
                return "a"  # dismiss any dialogue
            if self.door_cooldown >= 3:
                return "down"  # walk south away from door
            return "left"  # sidestep to avoid door on return north

        # In Oak's lab with no Pokemon: walk to the Pokeball table and pick one.
        # The Pallet Town script (0xD5F1) tracks the cutscene state but we don't
        # gate on it — the phases below handle all states by pressing B to
        # dismiss dialogue and navigating to the Pokeball table.
        # Red/Blue: balls at (6,3)=Charmander, (7,3)=Squirtle, (8,3)=Bulbasaur.
        # Yellow: a single Eevee ball at (7,3) — interacting triggers the rival
        # grabbing it and Oak handing over Pikachu. The target column comes from
        # the game profile. Interact from y=4 facing UP.
        if state.map_id == 40 and state.party_count == 0:
            lab_script = self.memory._read(self.profile.addr_lab_script)
            if not hasattr(self, "_lab_turns"):
                self._lab_turns = 0
            self._lab_turns += 1

            if self.turn_count % 50 == 0:
                self.log(f"LAB | script={lab_script} pos=({state.x},{state.y}) turn={self.turn_count}")
                if self.turn_count % 200 == 0:
                    self.take_screenshot(f"lab_t{self.turn_count}", force=True)

            if not hasattr(self, "_lab_phase"):
                self._lab_phase = 0

            if self._lab_phase == 0:
                # Dismiss text with B, move south to y=4 (interaction row)
                if state.y >= 4:
                    self._lab_phase = 1
                    self.log(f"LAB | phase 0→1 south at ({state.x},{state.y})")
                    return "right"
                if self._lab_turns % 2 == 1:
                    return "b"
                return "down"

            elif self._lab_phase == 1:
                # Move east to the target Pokeball column (per-game, from the profile)
                if state.x >= self.profile.lab_ball_x:
                    self._lab_phase = 2
                    self.log(f"LAB | phase 1→2 at pokeball column ({state.x},{state.y})")
                    return "up"
                return "right"

            else:
                # Phase 2: at Pokeball — face up and press A to interact
                if self._lab_turns % 2 == 0:
                    return "up"
                return "a"

        # In Oak's Lab with a Pokemon: navigate to exit and trigger rival.
        # After picking a starter, the rival picks his, then challenges when
        # the player walks toward the exit.  NPCs can block the path south
        # from the table area, so go left first, then south.
        # The exit door is at roughly (4, 11).
        # Only on the FIRST visit (no parcel yet, no Pokédex). Later lab visits — delivering the
        # parcel and exiting afterward — are driven by the parcel quest below.
        if (
            state.map_id == 40
            and state.party_count > 0
            and not self.memory.has_parcel()
            and not self.memory.has_pokedex()
        ):
            if not hasattr(self, "_lab_exit_turns"):
                self._lab_exit_turns = 0
            self._lab_exit_turns += 1

            # First 30 turns: heavy A-mash to clear rival pick dialogue
            if self._lab_exit_turns <= 30:
                if self._lab_exit_turns % 5 == 0:
                    return "down"
                return "a"

            # Navigate: go left to center column, then south to exit door (~y=11)
            if state.x > 5:
                if self._lab_exit_turns % 3 == 0:
                    return "a"  # talk to NPCs / clear dialogue
                return "left"
            # Keep walking south — door is at y=11, interleave A for dialogue
            if self._lab_exit_turns % 4 == 0:
                return "a"  # interact with rival when intercepted / clear text
            return "down"

        # Viridian Forest (map 51) is a maze. The legacy waypoint Navigator sees only the 9x10
        # screen, so it wedges pressing into a tree it can't see around (run 18: frozen at (18,33)
        # for 7000+ turns). Drive it with the persistent WorldMap planner instead — the same one
        # the parcel quest uses — pathfinding toward the north exit, learning trees as walls from
        # failed moves and steering around known grass via the encounter cost. ``cross_step`` then
        # sweeps the top boundary for the real exit column into Pewter.
        if state.map_id == 51:
            # Drive the maze with the persistent WorldMap planner. Walls — and bug-catcher NPCs,
            # whose tiles collision reports impassable — are learned by run_overworld's normal
            # two-consecutive-failed-steps rule and routed around: we don't need to battle a catcher
            # to *cross*, and any catcher whose line of sight we enter still starts a battle on its
            # own. (An earlier "press A when blocked to engage the catcher" hack backfired badly: the
            # A reset run_overworld's failed-step streak, and acting on a single failed step mistook
            # a mere turn-in-place — the first press only turns the character — for a wall, so it
            # hard-blocked walkable tiles and trapped the agent in the entrance pocket.)
            route = self.navigator.routes.get("51", {})
            wps = route.get("waypoints") if isinstance(route, dict) else None
            ex, ey = (wps[-1]["x"], wps[-1]["y"]) if wps else (2, 0)
            # Once the exit is reachable over KNOWN-walkable tiles, commit to it.
            if self.world.known_reachable(state.map_id, state.x, state.y, ex, ey):
                d = self._pilot_to(state, ex, ey)
                return d if d is not None else self._collision_pilot(state, "north")
            # Otherwise head toward the exit optimistically (A* treats unknown tiles as passable).
            # With the now-correct two-failed-steps wall learning, real walls get recorded and A*
            # reroutes around them — so the agent snakes toward the far-left exit column instead of
            # plateauing on nearby frontiers the way pure nearest-frontier exploration does (it kept
            # mapping the forest body but never trekked to the NW exit pocket). Frontier exploration
            # is the fallback when the optimistic plan has nowhere to go. Persisting the WorldMap
            # across runs (load -> run -> save) lets successive epochs accumulate the whole maze, so
            # known_reachable eventually fires and the commit branch above carries it out the exit.
            d = self._pilot_to(state, ex, ey)
            if d is not None:
                return d
            explore = self.world.explore_step(state.map_id, state.x, state.y)
            return explore if explore is not None else self._collision_pilot(state, "north")

        # Oak's Parcel quest: run the errand that unblocks Viridian's north exit (Mart pickup →
        # Oak delivery → Old-Man gate). Outdoor legs return a "pilot" directive (the agent
        # collision-follows directly, bypassing the thrash-prone Navigator); buildings return a
        # precise coordinate target the (now-working) A* navigator handles. None once satisfied.
        quest = self._quest_target(state)
        self._quest_nav_active = quest is not None
        if quest is not None and "pilot" in quest:
            self.navigator.quest_target = None
            return self._collision_pilot(state, quest["pilot"])
        if quest is not None and "pilot_to" in quest:
            self.navigator.quest_target = None
            tx, ty = quest["pilot_to"]
            d = self._pilot_to(state, tx, ty)
            if d is not None:
                return d
            # Arrived: alternate the facing press (at_target) and A, so we both turn to face the
            # NPC/door and then interact — talking to a clerk/Oak needs facing them first.
            self._at_target_toggle = not getattr(self, "_at_target_toggle", False)
            return quest.get("at_target", "a") if self._at_target_toggle else "a"

        self.navigator.quest_target = quest
        direction = self.navigator.next_direction(
            state,
            turn=self.turn_count,
            stuck_turns=self.stuck_turns,
            collision_grid=self.collision_map.grid,
        )
        return direction or "a"

    def _collision_pilot(self, state: OverworldState, goal: str) -> str:
        """Cross the current map in cardinal direction ``goal`` ("north"/"south") toward the next
        map, using the WorldMap's boundary-sweeping ``cross_step`` — it advances toward the edge
        when it can and otherwise sweeps along the boundary to the real exit column, learning the
        map-edge non-exits as it goes (via the failed-move hard-blocks)."""
        return self.world.cross_step(state.map_id, state.x, state.y, goal)

    def _pilot_to(self, state: OverworldState, tx: int, ty: int) -> str | None:
        """Navigate toward tile ``(tx, ty)`` by pathfinding over the accumulated WorldMap. Returns
        ``None`` once standing on the tile (the caller then presses the target's ``at_target``
        action). Whole-map A* routes around remembered walls — e.g. it follows the fence north of
        the Mart all the way to the gap on the centre corridor instead of stalling beneath it."""
        if state.x == tx and state.y == ty:
            return None
        return self.world.plan_step(state.map_id, state.x, state.y, tx, ty, encounter_cost=GRASS_ENCOUNTER_COST)

    def _quest_target(self, state: OverworldState) -> dict | None:
        """Build the parcel-quest nav override for this turn, or None to defer to waypoints.

        Skipped until the player has a starter (party > 0) so it never disturbs the intro / lab
        cutscene. Also emits a one-line map-transition log (map, pos, facing, parcel, pokedex,
        phase) the first time each map is entered — the discovery signal that confirms map ids."""
        if state.party_count <= 0:
            return None
        sig = QuestSignals(
            map_id=state.map_id,
            x=state.x,
            y=state.y,
            has_parcel=self.memory.has_parcel(),
            has_pokedex=self.memory.has_pokedex(),
        )
        target = self.parcel_quest.next_target(sig)
        if state.map_id != self._last_logged_map:
            self._last_logged_map = state.map_id
            self.log(
                f"QUEST | map={state.map_id} pos=({state.x},{state.y}) "
                f"facing={self.memory.read_player_facing_name()} {self.parcel_quest.describe(sig)} "
                f"target={target['name'] if target else None}"
            )
        return target

    def _waypoint_goal(self, state: OverworldState) -> tuple[str, list[dict]]:
        """Current navigator goal for this map: ("WP: 2→(7,1)", waypoint list) or ("", [])."""
        route = self.navigator.routes.get(str(state.map_id))
        if not route:
            return "", []
        waypoints = route["waypoints"] if isinstance(route, dict) and "waypoints" in route else route
        if self.navigator.current_waypoint >= len(waypoints):
            return "", list(waypoints)
        wp = waypoints[self.navigator.current_waypoint]
        return f"WP: {self.navigator.current_waypoint}→({wp['x']},{wp['y']})", list(waypoints)

    def _maybe_emit_agent_state(self, state: OverworldState) -> None:
        """Snapshot agent state every 10 turns, plus on map/party/stuck-transition changes."""
        sig = (state.map_id, state.party_count, self.stuck_turns > 0, self.battles_won)
        if self.turn_count % 10 != 0 and sig == getattr(self, "_last_state_sig", None):
            return
        self._last_state_sig = sig
        goal, waypoints = self._waypoint_goal(state)
        notes = self.strategy_engine.notes
        self.collector.agent_state(
            self.turn_count,
            tier=self.strategy_engine.tier,
            goal=goal,
            route_waypoints=waypoints,
            stuck_streak=self.stuck_turns,
            notes_excerpt=notes.read()[:500] if notes is not None else "",
            party_count=state.party_count,
            position={"map_id": state.map_id, "x": state.x, "y": state.y},
            battles_won=self.battles_won,
            maps_visited=len(self.maps_visited),
        )

    def log(self, msg: str):
        """Structured log line for Tapes to capture."""
        timestamp = time.strftime("%H:%M:%S")
        line = f"[{timestamp}] {msg}"
        print(line, flush=True)
        self.events.append(line)

    def write_pokedex_entry(self):
        """Write a session summary to the pokedex directory."""
        final_state = self.memory.read_overworld_state()
        timestamp = time.strftime("%Y-%m-%d_%H%M%S")

        # Find next log number
        existing = list(self.pokedex_dir.glob("log*.md"))
        next_num = len(existing) + 1
        path = self.pokedex_dir / f"log{next_num}.md"

        # Count notable events
        map_changes = [e for e in self.events if "MAP CHANGE" in e]
        battles = [e for e in self.events if "BATTLE" in e]
        stuck_events = [e for e in self.events if "STUCK" in e]

        # Encounter summary
        species_counts: dict[str, int] = {}
        for enc in self.encounter_log:
            species_counts[enc["species"]] = species_counts.get(enc["species"], 0) + 1

        lines = [
            f"# Log {next_num}: Session {timestamp}",
            "",
            "## Summary",
            "",
            f"- **Turns:** {self.turn_count}",
            f"- **Battles won:** {self.battles_won}",
            f"- **Encounters:** {len(self.encounter_log)}",
            f"- **Maps visited:** {len(self.maps_visited)} ({', '.join(str(m) for m in sorted(self.maps_visited))})",
            f"- **Final position:** Map {final_state.map_id} ({final_state.x}, {final_state.y})",
            f"- **Badges:** {final_state.badges}",
            f"- **Party size:** {final_state.party_count}",
            f"- **Strategy:** {self.battle_strategy.__class__.__name__}",
            "",
            "## Encounters",
            "",
        ]
        for species, count in sorted(species_counts.items()):
            lines.append(f"- {species}: {count}")
        lines.append("")

        if self.evolution_log:
            lines += ["## Evolutions", ""]
            for evo in self.evolution_log:
                lines.append(f"- Slot {evo['slot']}: {evo['from']} -> {evo['to']}")
            lines.append("")

        if self.level_ups > 0:
            lines += [f"## Level Ups: {self.level_ups}", ""]

        lines += [
            "## Stats",
            "",
            f"- Map changes: {len(map_changes)}",
            f"- Battle turns: {len(battles)}",
            f"- Stuck events: {len(stuck_events)}",
            "",
            "## Event Log",
            "",
        ]

        for event in self.events:
            lines.append(f"    {event}")

        lines.append("")
        path.write_text("\n".join(lines))
        self.log(f"POKEDEX | Wrote {path}")

    def take_screenshot(self, label: str = "", force: bool = False):
        """Save current frame as turn{N}.png."""
        if not force and not self.screenshots:
            return
        if Image is None:
            return
        suffix = f"_{label}" if label else ""
        path = self.frames_dir / f"turn{self.turn_count}{suffix}.png"
        path.parent.mkdir(parents=True, exist_ok=True)
        img = Image.fromarray(self.pyboy.screen.ndarray)
        img.save(path)
        self.log(f"SCREENSHOT | {path}")

    def run_battle_turn(self):
        """Execute one battle turn."""
        # Capture the battle screen now, while it is guaranteed up — before menu
        # selection, animations, and the post-faint return to the overworld. The
        # recorder keys this under a protected tag so a same-turn overworld write
        # cannot clobber it (the root cause of "no battle screen" in playback).
        self.collector.battle_frame(self.turn_count)
        battle = self.memory.read_battle_state()
        bag_healing = self.memory.find_healing_item()
        action = self.battle_strategy.choose_action(battle, bag_healing=bag_healing)

        act_desc = action["action"]
        if act_desc == "fight":
            mv_idx = action["move_index"]
            move_id = battle.moves[mv_idx] if 0 <= mv_idx < len(battle.moves) else 0
            mv_name, mv_type, mv_power, _acc = MOVE_DATA.get(move_id, (f"#{move_id:02X}", "unknown", 0, 0))
            act_desc = f"fight {mv_name}"
        elif act_desc == "item":
            act_desc = f"item {action.get('item', '?')}"
        self.collector.decision(
            self.turn_count,
            "battle",
            f"vs Lv{battle.enemy_level} {battle.enemy_species_name}: {act_desc}",
            ["a"],
        )

        self._current_enemy_species = battle.enemy_species_name
        self._current_enemy_type = battle.enemy_type_name

        type2 = battle.enemy_type_name
        type2_str = TYPE_ID_MAP.get(battle.enemy_type2, "")
        type_label = f"{type2}/{type2_str}" if type2_str and type2_str != type2 else type2
        self.log(
            f"BATTLE | Lv{battle.enemy_level} {battle.enemy_species_name} ({type_label}) | "
            f"Player HP: {battle.player_hp}/{battle.player_max_hp} | "
            f"Action: {action['action']}"
        )
        self.collector.battle(
            self.turn_count,
            battle.player_hp,
            battle.player_max_hp,
            battle.enemy_hp,
            battle.enemy_max_hp,
            action,
            battle_type=battle.battle_type,
            map_id=self.memory._read(self.memory.ADDR_MAP_ID),
            enemy_species=battle.enemy_species_name,
            enemy_level=battle.enemy_level,
            player_species=SPECIES_ID_MAP.get(battle.player_species, f"#{battle.player_species:02X}"),
            player_level=battle.player_level,
        )
        # OBSERVE the learnset: which moves the lead knows at this level (emit on change). This is
        # how the agent *discovers* (rather than is told) when moves like Ember come online.
        ms_names = [MOVE_DATA.get(m, (f"#{m:02X}",))[0] for m in battle.moves if m]
        ms_key = (battle.player_level, tuple(battle.moves))
        if ms_key != getattr(self, "_last_moveset_key", None):
            self._last_moveset_key = ms_key
            self.collector.moveset(
                self.turn_count,
                SPECIES_ID_MAP.get(battle.player_species, f"#{battle.player_species:02X}"),
                battle.player_level,
                ms_names,
            )
            self.log(f"MOVESET | L{battle.player_level} {ms_names}")

        if action["action"] == "fight":
            # mv_idx / move_id / mv_name / mv_type / mv_power were resolved at decision time above.
            enemy_hp_before = battle.enemy_hp
            player_hp_before = battle.player_hp
            # Execute the move RELIABLY. The fixed-timing menu selection occasionally catches the
            # game mid-animation (from the enemy's previous move) and leaves the move list open
            # without confirming — observed ~50% of hits missing vs Brock, enemy HP frozen for many
            # turns. Confirm by watching the turn actually resolve (HP changes or the battle ends);
            # if it didn't, back out (B) to the top menu and re-select. A real turn resolves first try.
            for _attempt in range(4):
                self.controller.battle_menu_select("fight")  # open the move list
                self.controller.navigate_menu(mv_idx)  # pick the move (vertical list)
                self.controller.press("a")  # confirm the selected move
                if self._await_turn_resolved(enemy_hp_before, player_hp_before):
                    break
                self.controller.press("b")  # still at the move list — back out and retry
                self.controller.wait(20)
            self.controller.mash_a(8, delay=30)  # Clear all text boxes
            # OBSERVE the raw outcome via a before/after enemy-HP delta. The fight branch also runs
            # on menu/text frames (no move landed) and post-faint frames, so we EMIT ONLY a real
            # landed hit (enemy was alive and HP dropped, or it fainted) — that filters the phantom
            # dmg=0 noise without changing battle timing. Numbers only, never a "super-effective"
            # label: the type chart is learned from data, not fed.
            battle_over = self.memory._read(self.memory.ADDR_BATTLE_TYPE) == 0
            after_hp = self.memory.read_enemy_hp()
            fainted = battle_over or after_hp <= 0
            enemy_hp_after = 0 if fainted else after_hp
            dmg = max(0, enemy_hp_before - enemy_hp_after)
            # Record only a real landed hit: enemy was alive and HP actually dropped (or it fainted).
            if enemy_hp_before > 0 and (dmg > 0 or fainted):
                self.collector.move_result(
                    self.turn_count,
                    SPECIES_ID_MAP.get(battle.player_species, f"#{battle.player_species:02X}"),
                    battle.player_level,
                    mv_name,
                    mv_type,
                    mv_power,
                    battle.enemy_species_name,
                    battle.enemy_level,
                    battle.enemy_type_name,
                    dmg,
                    enemy_hp_before,
                    battle.enemy_max_hp,
                    fainted,
                )
                self.log(
                    f"MOVE | L{battle.player_level} {mv_name}({mv_type}) vs "
                    f"{battle.enemy_species_name}({battle.enemy_type_name}) "
                    f"dmg={dmg}/{battle.enemy_max_hp}{' KO' if fainted else ''}"
                )

        elif action["action"] == "run":
            self.controller.battle_menu_select("run")
            self.controller.wait(120)
            self.controller.mash_a(5, delay=30)

        elif action["action"] == "item":
            self.controller.battle_menu_select("item")
            self.controller.wait(20)
            # Navigate to the correct item slot (the bag is a vertical list)
            bag_index = action.get("bag_index", 0)
            self.controller.navigate_menu(bag_index)
            self.controller.wait(120)
            self.controller.mash_a(5, delay=30)

        elif action["action"] == "switch":
            self.controller.battle_menu_select("pkmn")
            self.controller.wait(20)
            self.controller.navigate_menu(action.get("slot", 1))
            self.controller.wait(120)
            self.controller.mash_a(5, delay=30)

        elif action["action"] == "unstick":
            # Recover a stalled trainer battle: the enemy's HP hasn't dropped for several turns and
            # the FIGHT selection isn't landing (a desynced battle menu / lingering text box). Mash
            # B to close any submenu/text and return to a clean top-level battle menu so the next
            # FIGHT selection registers. Trainer battles can't be fled, so recovery is a menu reset,
            # never "run".
            for _ in range(6):
                self.controller.press("b")
                self.controller.wait(20)
            self.controller.wait(40)

        self.turn_count += 1

    def _await_turn_resolved(self, enemy_hp_before: int, player_hp_before: int, frames: int = 260) -> bool:
        """Tick up to ``frames``, returning True once the battle turn actually resolved — our HP or
        the enemy's HP changed, or the battle ended — i.e. the selected move registered. Returns
        False if nothing changed, so the caller re-selects (the fixed-timing menu occasionally leaves
        the move list open without confirming). Re-selecting a move that genuinely did 0 while the
        enemy also did nothing is harmless (it just does 0 again)."""
        for _ in range(max(1, frames // 4)):
            self.controller.wait(4)
            if self.memory._read(self.memory.ADDR_BATTLE_TYPE) == 0:
                return True  # battle ended (a faint)
            state = self.memory.read_battle_state()
            if state.enemy_hp != enemy_hp_before or state.player_hp != player_hp_before:
                self.controller.wait(60)  # let the rest of the turn's animation/text play out
                return True
        return False

    def _resolve_brock_badge(self, won: bool) -> bool:
        """Whether Brock is really beaten = the Boulder Badge bit. It is awarded during Brock's
        post-battle dialogue, which plays AFTER battle_type clears, so a straight read here caught it
        too early and recorded a win as a loss. On a win (not a white-out), advance the dialogue
        (mash A) until the bit sets; on a loss it never sets, so we don't waste presses."""
        badges = self.memory._read(self.memory.ADDR_BADGES)
        if won:
            for _ in range(20):
                if badges & 0x01:
                    break
                self.controller.mash_a(3, delay=20)
                badges = self.memory._read(self.memory.ADDR_BADGES)
        return bool(badges & 0x01)

    def run_overworld(self):
        """Move in the overworld."""
        state = self.memory.read_overworld_state()

        # If last turn's move didn't change our position, the tile we tried to enter may be
        # impassable even though the collision grid called it walkable (a ledge, a map-edge
        # non-exit, an NPC). But the FIRST press in a new direction only turns the character without
        # moving, so a single failure isn't proof — only hard-block a tile after two consecutive
        # failed steps into the *same* tile, so a mere turn never poisons the map.
        prev = self.last_overworld_state
        last_act = self.last_overworld_action
        attempted = None
        # Require facing to have turned to the pressed direction: a real wall lets the turn happen
        # (face == direction, but no step), whereas a script / warp-settle state ignores the input
        # entirely (facing unchanged). Only the former is evidence of a wall — so a cutscene that
        # swallows our inputs (e.g. the Mart clerk's parcel script) never poisons the map.
        if (
            prev is not None
            and last_act in ("up", "down", "left", "right")
            and state.map_id == prev.map_id
            and state.x == prev.x
            and state.y == prev.y
            and self.memory.read_player_facing_name() == last_act
        ):
            bdx = {"left": -1, "right": 1}.get(last_act, 0)
            bdy = {"up": -1, "down": 1}.get(last_act, 0)
            attempted = (state.map_id, state.x + bdx, state.y + bdy)
            if attempted == getattr(self, "_last_fail_tile", None):
                self.world.block(*attempted)  # turned into it twice → a real wall
        self._last_fail_tile = attempted  # None on a move or an ignored input, resetting the streak

        self.update_overworld_progress(state)
        try:
            self.collision_map.update(self.pyboy)
        except Exception:
            pass  # game_wrapper may not be available in all contexts
        # Remember what we can see, so navigation builds a real map of each location over time.
        self.world.observe(state.map_id, state.x, state.y, self.collision_map.grid)

        # --- FLE backtracking ---
        # Skip all backtracking while in Oak's Lab (map 40).
        # The lab has multiple scripted sequences (picking starter, rival battle)
        # that look "stuck" but are progressing.  Restoring mid-sequence
        # undoes progress even after the player picks up a Pokemon.
        in_oaks_lab = state.map_id == 40
        # Viridian Forest (map 51) is a forward-only maze: there is no earlier good state to fall
        # back to, so a stall-triggered restore teleports the agent clear back to a pre-forest
        # snapshot (observed: turn 137, 51 -> Pallet -> Red's house) and the crossing never happens.
        # Like Oak's Lab, the forest must push forward (interact + explore), never restore.
        in_forest = state.map_id == 51

        # Snapshot on map change (skip in Oak's Lab)
        if not in_oaks_lab:
            if self._bt_last_map_id is not None and state.map_id != self._bt_last_map_id:
                self.backtrack.save_snapshot(self.pyboy, state, self.turn_count)
        self._bt_last_map_id = state.map_id

        # Periodic snapshot when making progress (skip in Oak's Lab)
        if (
            not in_oaks_lab
            and self._bt_snapshot_interval > 0
            and self.turn_count > 0
            and self.turn_count % self._bt_snapshot_interval == 0
            and self.stuck_turns == 0
        ):
            last_snap = self.backtrack.snapshots[-1] if self.backtrack.snapshots else None
            if (
                last_snap is None
                or last_snap.map_id != state.map_id
                or last_snap.x != state.x
                or last_snap.y != state.y
            ):
                self.backtrack.save_snapshot(self.pyboy, state, self.turn_count)

        # Force restore when no meaningful progress for 500 turns (skip in Oak's Lab, and while
        # the quest pilot is driving — a restore would teleport it off the route it's crossing).
        progress_gap = self.turn_count - self._last_progress_turn
        if (
            not in_oaks_lab
            and not in_forest
            and not self._quest_nav_active
            and progress_gap > 500
            and self.backtrack.snapshots
        ):
            self.log(f"PROGRESS STALL | No progress for {progress_gap} turns, forcing backtrack restore")
            snap = self.backtrack.restore(self.pyboy)
            if snap is not None:
                self.stuck_turns = 0
                self._last_progress_turn = self.turn_count
                self.recent_positions.clear()
                state = self.memory.read_overworld_state()
                self.log(
                    f"BACKTRACK | Restored to turn {snap.turn} "
                    f"map={snap.map_id} ({snap.x},{snap.y}) "
                    f"attempt={snap.attempts}"
                )
                self.collector.decision(
                    self.turn_count,
                    "overworld",
                    f"backtrack restore to turn {snap.turn} map={snap.map_id} ({snap.x},{snap.y})",
                    [],
                )

        # Restore when stuck too long (skip in Oak's Lab / Forest and while the quest pilot drives)
        if (
            not in_oaks_lab
            and not in_forest
            and not self._quest_nav_active
            and self.backtrack.should_restore(self.stuck_turns)
        ):
            snap = self.backtrack.restore(self.pyboy)
            if snap is not None:
                self.stuck_turns = 0
                self.recent_positions.clear()
                # Reset script-gate flags so one-time sequences can re-trigger
                for attr in (
                    "_oak_wait_done",
                    "_pallet_diag_done",
                    "_house_diag_done",
                    "_lab_phase",
                    "_lab_turns",
                    "_lab_exit_turns",
                ):
                    if hasattr(self, attr):
                        delattr(self, attr)
                state = self.memory.read_overworld_state()
                self.log(
                    f"BACKTRACK | Restored to turn {snap.turn} "
                    f"map={snap.map_id} ({snap.x},{snap.y}) "
                    f"attempt={snap.attempts}"
                )
                self.collector.decision(
                    self.turn_count,
                    "overworld",
                    f"backtrack restore to turn {snap.turn} map={snap.map_id} ({snap.x},{snap.y})",
                    [],
                )

        # Diagnostic: capture screen and collision data at key positions
        if state.map_id == 37 and not hasattr(self, "_house_diag_done"):
            self._house_diag_done = True
            self.take_screenshot("house_1f", force=True)
            self.log(f"DIAG | House 1F at ({state.x},{state.y}) collision map:")
            self.log(self.collision_map.to_ascii())

        if state.map_id == 0 and state.y <= 3 and state.party_count == 0:
            # Log game state near the Oak trigger zone
            wd730 = self.memory._read(self.profile.addr_wd730)
            wd74b = self.memory._read(self.profile.addr_wd74b)
            cur_script = self.memory._read(self.profile.addr_diag_script)
            if self.turn_count % 5 == 0:
                self.log(
                    f"DIAG | Pallet y={state.y} x={state.x} wd730=0x{wd730:02X} wd74b=0x{wd74b:02X} script={cur_script}"
                )
            if not hasattr(self, "_pallet_diag_done"):
                self._pallet_diag_done = True
                self.take_screenshot("pallet_north", force=True)

            # Oak's Pallet Town script triggers at a per-game y (Red/Blue: y==1;
            # Yellow: y==0 — the player must step onto the north boundary row).
            # Stop movement and wait for Oak to walk over, then mash A through
            # dialogue (in Yellow that includes the scripted wild-Pikachu catch).
            if state.y <= self.profile.oak_trigger_y:
                if not hasattr(self, "_oak_wait_done"):
                    self._oak_wait_done = True
                    self.log(f"OAK TRIGGER | At y={state.y} x={state.x}. Waiting for Oak script...")
                    self.take_screenshot("oak_trigger", force=True)
                    # Wait for Oak to walk from Route 1 to the player (~600 frames)
                    self.controller.wait(600)
                    # Oak's lab intro has multiple scripted walking + dialogue phases:
                    # 1. Oak escorts player to lab (walk script ~300 frames)
                    # 2. Oak talks about research (several text boxes)
                    # 3. Oak walks to Pokeball table (walk script ~200 frames)
                    # 4. Oak says "choose a Pokemon" (text boxes)
                    # Alternate mashing A and waiting for walk scripts.
                    for _ in range(4):
                        self.controller.mash_a(30, delay=30)
                        self.controller.wait(300)
                    s = self.memory.read_overworld_state()
                    wd730 = self.memory._read(self.profile.addr_wd730)
                    self.log(
                        f"OAK TRIGGER | After wait: map={s.map_id} ({s.x},{s.y}) "
                        f"party={s.party_count} wd730=0x{wd730:02X}"
                    )
                    self.take_screenshot("oak_after_wait", force=True)

        action = self.choose_overworld_action(state)

        if action == "wait":
            self.controller.wait(30)
        elif action in {"up", "down", "left", "right"}:
            self.controller.move(action)
        elif action == "b":
            self.controller.press("b", hold_frames=20, release_frames=12)
            self.controller.wait(24)
        else:
            self.controller.press("a", hold_frames=20, release_frames=12)
            self.controller.wait(24)

        goal, _ = self._waypoint_goal(state)
        reason = f"map {state.map_id} ({state.x},{state.y}) stuck={self.stuck_turns}"
        if goal:
            reason += f" | {goal}"
        buttons = [action] if action in {"up", "down", "left", "right", "a", "b"} else []
        self.collector.decision(self.turn_count, "overworld", reason, buttons)
        self._maybe_emit_agent_state(state)

        # Log position every 50 steps (or every 10 on map 0 for debugging)
        log_interval = 10 if state.map_id == 0 else 50
        if self.turn_count % log_interval == 0:
            wp_info = f" | {goal}" if goal else ""
            self.log(
                f"OVERWORLD | Map: {state.map_id} | "
                f"Pos: ({state.x}, {state.y}) | "
                f"Badges: {state.badges} | "
                f"Party: {state.party_count} | "
                f"Action: {action} | "
                f"Stuck: {self.stuck_turns}{wp_info}"
            )
            self.collector.overworld(
                self.turn_count,
                state.map_id,
                state.x,
                state.y,
                state.badges,
                state.party_count,
                action,
                self.stuck_turns,
                wp_info or None,
            )

        self.last_overworld_state = state
        self.last_overworld_action = action

    def compute_fitness(self) -> dict:
        """Return structured metrics from the current run state."""
        final = self.memory.read_overworld_state()
        return {
            "turns": self.turn_count,
            "battles_won": self.battles_won,
            "maps_visited": len(self.maps_visited),
            "final_map_id": final.map_id,
            "final_x": final.x,
            "final_y": final.y,
            "badges": final.badges,
            "party_size": final.party_count,
            "stuck_count": len([e for e in self.events if "STUCK" in e]),
            "backtrack_restores": self.backtrack.total_restores,
            "encounters": len(self.encounter_log),
            "level_ups": self.level_ups,
            "evolutions": len(self.evolution_log),
            # Brock (gym 1) outcome — None until the Brock fight resolves.
            "brock_turns": self.brock_turns,
            "brock_won": self.brock_won,
            "brock_lead_species": self.brock_lead_species,
            "brock_lead_level": self.brock_lead_level,
        }

    def _advance_intro(self):
        """Advance through the title screen, Oak's intro, and name selection."""
        # Advance through title screen (needs ~1500 frames to reach "Press Start")
        self.controller.wait(1500)
        self.controller.press("start")
        self.controller.wait(60)

        # Handle save file: when a .gb.ram exists, the title screen shows
        # CONTINUE / NEW GAME with CONTINUE pre-selected. Press DOWN to
        # select NEW GAME, then A to confirm. When no save exists, START
        # goes directly to Oak's intro and these presses are harmless.
        self.controller.press("down")
        self.controller.wait(30)
        self.controller.press("a")
        self.controller.wait(60)

        # Mash through Oak's entire intro, name selection, rival naming.
        # Need long frame waits — the game has slow text scroll and animations.
        # This takes ~600 A presses with proper wait times.
        for i in range(600):
            self.controller.press("a")
            self.controller.wait(30)  # Longer waits for text to scroll

        # Dismiss any leftover text boxes (e.g. SNES console dialogue in
        # Red's bedroom) that the intro A-mashing may have triggered.
        for _ in range(10):
            self.controller.press("b")
            self.controller.wait(15)

        self.log("Intro complete. Entering game loop.")

        # Diagnostic: capture game state right after intro
        intro_state = self.memory.read_overworld_state()
        self.take_screenshot("post_intro", force=True)
        wd730 = self.memory._read(self.profile.addr_wd730)
        wd74b = self.memory._read(self.profile.addr_wd74b)
        self.log(
            f"DIAG | Post-intro: map={intro_state.map_id} pos=({intro_state.x},{intro_state.y}) "
            f"party={intro_state.party_count} wd730=0x{wd730:02X} wd74b=0x{wd74b:02X}"
        )

        # Validate intro completed correctly — player should be in Red's
        # bedroom (map 38). map=0 pos=(0,0) means the game never started,
        # likely because a .gb.ram save file changed the title screen menu.
        if intro_state.map_id == 0 and intro_state.x == 0 and intro_state.y == 0:
            self.log(
                "WARN | Intro failed: still at map=0 (0,0). "
                "A .gb.ram save file may have caused CONTINUE instead of NEW GAME. "
                "Delete the .ram file or check intro sequence."
            )

    def run(
        self,
        max_turns: int = 100_000,
        battle_limit: int = 0,
        load_state=None,
        save_state_on_battle=None,
        save_state_on_map=None,
        save_state_on_trainer=None,
        save_state_every=None,
    ):
        """Main agent loop. Returns fitness dict at end.

        load_state: path to a PyBoy save state to load instead of running the intro.
        save_state_on_battle: path to dump a save state at the first detected battle.
        save_state_on_map: "MAPID:PATH" to dump a state when first reaching that map.
        save_state_on_trainer: "MAPID:PATH" to dump a state the first time a trainer
            battle starts on that map, or "brock:PATH" to trigger on the first
            gym-leader-level trainer (opponent level >= 12) when the gym map is unknown.
        save_state_every: "N:PATH" to overwrite a checkpoint state every N turns, so a
            segmented run can resume the journey with ``--load-state PATH`` (see the
            brock-battle-learning skill).
        """
        self.log("Agent starting.")
        self.collector.session(0, "start")

        if load_state:
            with open(load_state, "rb") as f:
                self.pyboy.load_state(f)
            self.log(f"Loaded save state from {load_state}. Skipping intro.")
        else:
            self._advance_intro()

        self._battle_state_saved = False
        self._map_state_saved = False
        self._trainer_state_saved = False
        save_map_target, save_map_path = None, None
        if save_state_on_map:
            _mid, save_map_path = save_state_on_map.split(":", 1)
            save_map_target = int(_mid)
        save_trainer_target, save_trainer_path = None, None
        save_trainer_by_level = False
        if save_state_on_trainer:
            _tid, save_trainer_path = save_state_on_trainer.split(":", 1)
            if _tid == "brock":
                save_trainer_by_level = True
            else:
                save_trainer_target = int(_tid)
        save_every_n, save_every_path = 0, None
        if save_state_every:
            _every, save_every_path = save_state_every.split(":", 1)
            save_every_n = int(_every)
        self._last_checkpoint_turn = -1
        for _ in range(max_turns):
            battle = self.memory.read_battle_state()

            if battle.battle_type > 0:
                # Capture a reusable save state at the first battle (for the autotune battle test)
                if save_state_on_battle and not self._battle_state_saved:
                    with open(save_state_on_battle, "wb") as f:
                        self.pyboy.save_state(f)
                    self._battle_state_saved = True
                    self.log(f"Saved battle state to {save_state_on_battle}")

                # Snapshot pre-battle state on first battle turn (battle RAM is cleared
                # once the fight ends, so opponent identity must be captured here).
                if not self._pre_battle_species:
                    self._pre_battle_species = self.memory.read_party_species()
                    self._pre_battle_level = battle.player_level
                    self._battle_start_turn = self.turn_count
                    self._battle_type = battle.battle_type
                    self._battle_map_id = self.memory._read(self.memory.ADDR_MAP_ID)
                    self._battle_opponent_species = battle.enemy_species_name
                    self._battle_opponent_level = battle.enemy_level
                    # Win-probability features observed at battle start: HP buffer, my move types,
                    # and whether a heal item is on hand.
                    self._battle_my_hp_start = battle.player_hp
                    self._battle_my_max_hp = battle.player_max_hp
                    self._battle_enemy_type = battle.enemy_type_name
                    self._battle_my_move_types = [MOVE_DATA.get(m, ("", "none", 0, 0))[1] for m in battle.moves if m]
                    self._battle_had_healing = self.memory.find_healing_item() is not None

                    # Learn the grass: a wild battle (type 1) fires on the tile the agent just
                    # stepped onto, so mark its last overworld position as an encounter tile. The
                    # WorldMap planner then pays GRASS_ENCOUNTER_COST to re-enter it, steering
                    # later traversals onto the dirt path through the Forest.
                    if battle.battle_type == 1 and self.last_overworld_state is not None:
                        ow = self.last_overworld_state
                        self.world.mark_encounter(ow.map_id, ow.x, ow.y)

                # Dump a save state the first time a trainer fight starts on the target
                # map (i.e. the instant Brock's battle begins).
                if (
                    save_trainer_path
                    and not self._trainer_state_saved
                    and battle.battle_type == 2
                    and (
                        (
                            save_trainer_target is not None
                            and self.memory._read(self.memory.ADDR_MAP_ID) == save_trainer_target
                        )
                        or (save_trainer_by_level and battle.enemy_level >= 12)
                    )
                ):
                    with open(save_trainer_path, "wb") as f:
                        self.pyboy.save_state(f)
                    self._trainer_state_saved = True
                    self.log(f"Saved trainer-battle state (map {save_trainer_target}) to {save_trainer_path}")

                self.run_battle_turn()

                # Check if battle ended — wait long enough for turn to fully resolve
                self.controller.wait(60)
                new_battle = self.memory.read_battle_state()
                if new_battle.battle_type == 0:
                    # A loss also clears battle_type, so derive the real outcome from party
                    # HP at end-of-battle (before any white-out heal-warp completes) rather
                    # than assuming a win. battles_won is left as-is to preserve existing
                    # fitness semantics for downstream consumers.
                    won = not self.memory.player_whited_out()
                    battle_turns = self.turn_count - self._battle_start_turn
                    self.battles_won += 1
                    self._last_progress_turn = self.turn_count
                    self.battle_strategy._run_attempts = 0
                    self.battle_strategy._wild_fight_turns = 0
                    self.battle_strategy._last_enemy_hp = None
                    self.encounter_log.append(
                        {
                            "species": self._current_enemy_species,
                            "type": self._current_enemy_type,
                            "won": won,
                        }
                    )
                    self.log(f"Battle ended. Won: {won}. Total wins: {self.battles_won}")

                    # Dismiss evolution/level-up screens
                    self.controller.mash_a(10, delay=30)

                    # Detect level-ups — read from party struct (offset 33 = level)
                    # since battle addresses are cleared after battle ends
                    post_level = self.memory._read(self.memory.PARTY_BASE + 33)
                    if self._pre_battle_level > 0 and post_level > self._pre_battle_level:
                        self.level_ups += 1
                        self.log(f"LEVEL UP | Lv{self._pre_battle_level} -> Lv{post_level}")

                    # Detect evolution (species change)
                    post_species = self.memory.read_party_species()
                    for slot, (pre, post) in enumerate(zip(self._pre_battle_species, post_species)):
                        if pre != post:
                            pre_name = SPECIES_ID_MAP.get(pre, f"#{pre:02X}")
                            post_name = SPECIES_ID_MAP.get(post, f"#{post:02X}")
                            self.log(f"EVOLUTION | Slot {slot}: {pre_name} -> {post_name}!")
                            self.evolution_log.append({"slot": slot, "from": pre_name, "to": post_name})

                    # Record the Brock (gym 1) outcome once. The Pewter Gym interior is its
                    # own map, so identify Brock by the configured map id when known, else by
                    # a trainer fight whose opponent is gym-leader level (>=12) — this skips
                    # the low-level Viridian Forest bug-catcher trainers. The badge bit
                    # (Boulder Badge = bit 0) is the authoritative win signal.
                    is_brock = self._battle_type == 2 and (
                        (self.brock_map_id is not None and self._battle_map_id == self.brock_map_id)
                        or (self.brock_map_id is None and self._battle_opponent_level >= 12)
                    )
                    if self.brock_turns is None and is_brock:
                        lead = self._pre_battle_species[0] if self._pre_battle_species else 0
                        self.brock_turns = battle_turns
                        self.brock_won = self._resolve_brock_badge(won)
                        self.brock_lead_species = SPECIES_ID_MAP.get(lead, f"#{lead:02X}")
                        self.brock_lead_level = self._pre_battle_level

                    # Emit a battle-end summary (turns, outcome, post-battle party).
                    self.collector.battle_end(
                        self.turn_count,
                        won,
                        battle_turns,
                        self._battle_type,
                        self._battle_map_id,
                        self._battle_opponent_species,
                        self._battle_opponent_level,
                        self.memory.read_party(),
                    )

                    # Emit the labeled WIN-PROBABILITY row: start-of-battle features + result.
                    self.collector.battle_outcome(
                        self.turn_count,
                        SPECIES_ID_MAP.get(self._pre_battle_species[0] if self._pre_battle_species else 0, ""),
                        self._pre_battle_level,
                        getattr(self, "_battle_my_hp_start", 0),
                        getattr(self, "_battle_my_max_hp", 0),
                        # Post-battle lead HP from the PARTY struct (the battle struct is cleared).
                        (self.memory._read_party_hp(1) or [0])[0],
                        getattr(self, "_battle_my_move_types", []),
                        getattr(self, "_battle_had_healing", False),
                        self._battle_opponent_species,
                        self._battle_opponent_level,
                        getattr(self, "_battle_enemy_type", ""),
                        self._battle_type,
                        battle_turns,
                        won,
                    )

                    # Reset pre-battle snapshots
                    self._pre_battle_species = []
                    self._pre_battle_level = 0

                    if battle_limit > 0 and self.battles_won >= battle_limit:
                        self.log(f"Battle limit reached ({battle_limit}). Stopping.")
                        break
            else:
                if save_map_path and not self._map_state_saved:
                    if self.memory.read_overworld_state().map_id == save_map_target:
                        with open(save_map_path, "wb") as f:
                            self.pyboy.save_state(f)
                        self._map_state_saved = True
                        self.log(f"Saved map-{save_map_target} state to {save_map_path}")
                self.run_overworld()
                self.turn_count += 1

            self.collector.tick(self.turn_count)

            if self.turn_count % 10 == 0:
                self.take_screenshot()

            # Periodic checkpoint so a segmented run can resume with --load-state.
            if (
                save_every_path
                and save_every_n > 0
                and self.turn_count > 0
                and self.turn_count % save_every_n == 0
                and self.turn_count != self._last_checkpoint_turn
            ):
                with open(save_every_path, "wb") as f:
                    self.pyboy.save_state(f)
                self._last_checkpoint_turn = self.turn_count
                self.log(f"Checkpoint at turn {self.turn_count} -> {save_every_path}")

            # Periodically persist the learned WorldMap so a killed/reset run keeps its geometry.
            if self.worldmap_file and self.turn_count > 0 and self.turn_count % 500 == 0:
                self.world.save(self.worldmap_file)

        if self.worldmap_file:
            self.world.save(self.worldmap_file)  # final persist of everything learned this segment
        self.log(f"Session complete. Turns: {self.turn_count} | Wins: {self.battles_won}")
        self.collector.session(
            self.turn_count,
            "end",
            battles_won=self.battles_won,
            maps_visited=len(self.maps_visited),
        )
        self.write_pokedex_entry()
        fitness = self.compute_fitness()
        try:
            self.pyboy.stop()
        except PermissionError:
            pass  # ROM save file write fails on read-only mounts
        return fitness


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def main():
    parser = argparse.ArgumentParser(description="Pokemon Agent — autonomous RPG player")
    parser.add_argument("rom", help="Path to ROM file (.gb or .gbc)")
    parser.add_argument(
        "--strategy",
        choices=["low", "medium", "high"],
        default="low",
        help="Decision strategy (default: low)",
    )
    parser.add_argument(
        "--game",
        choices=["auto", "red_blue", "yellow"],
        default="auto",
        help="Force a game profile (default: auto-detect from the ROM header title)",
    )
    parser.add_argument(
        "--max-turns",
        type=int,
        default=100_000,
        help="Maximum turns before stopping (default: 100000)",
    )
    parser.add_argument(
        "--battle-limit",
        type=int,
        default=0,
        help="Stop after this many battle wins (0 = unlimited)",
    )
    parser.add_argument(
        "--save-screenshots",
        action="store_true",
        help="Save periodic screenshots to ./frames/",
    )
    parser.add_argument(
        "--output-json",
        type=str,
        default=None,
        help="Write fitness metrics JSON to this path at end of run",
    )
    parser.add_argument(
        "--telemetry-dir",
        type=str,
        default="data/telemetry",
        help="Directory for JSONL telemetry (default: data/telemetry, empty to disable)",
    )
    parser.add_argument(
        "--config",
        type=str,
        default="config.toml",
        help="Path to config.toml (default: config.toml in cwd)",
    )
    parser.add_argument("--record", action="store_true", help="Record a replayable run folder")
    parser.add_argument("--runs-dir", default="runs", help="Directory for recorded runs")
    parser.add_argument("--frame-interval", type=int, default=10, help="Capture a frame every N turns")
    parser.add_argument("--live", action="store_true", help="Stream live to viewer over WebSocket (implies --record)")
    parser.add_argument("--viewer-url", default="ws://127.0.0.1:8200", help="Viewer WebSocket base URL")
    parser.add_argument("--label", default="", help="Human-readable label shown on the viewer's run tile")
    parser.add_argument("--load-state", default=None, help="Load a PyBoy save state and skip the intro")
    parser.add_argument("--save-state-on-battle", default=None, help="Dump a save state at the first detected battle")
    parser.add_argument("--save-state-on-map", default=None, help='Dump a state at a map, as "MAPID:PATH"')
    parser.add_argument(
        "--save-state-on-trainer",
        default=None,
        help='Dump a state at the first trainer battle on a map, as "MAPID:PATH" (e.g. Brock)',
    )
    parser.add_argument(
        "--save-state-every",
        default=None,
        help='Overwrite a checkpoint state every N turns, as "N:PATH" (for segmented resume)',
    )
    parser.add_argument(
        "--worldmap-file",
        default=None,
        help="Load/save the accumulated WorldMap (learned geometry) here, so a reset run keeps it",
    )
    args = parser.parse_args()

    if not Path(args.rom).exists():
        print(f"ROM not found: {args.rom}")
        sys.exit(1)

    # Set up real-time game event publisher (Confluent Cloud / JSONL)
    config_path = Path(args.config) if args.config else None
    game_pub = None
    if args.telemetry_dir:
        try:
            from publisher import make_publisher as _make_game_pub

            game_pub = _make_game_pub(telemetry_dir=str(Path(args.telemetry_dir) / "game"), config_path=config_path)
        except Exception as exc:
            print(f"[agent] game publisher setup failed: {exc}")

    agent = PokemonAgent(
        args.rom,
        strategy=args.strategy,
        screenshots=args.save_screenshots,
        game=None if args.game == "auto" else args.game,
    )
    if args.worldmap_file:
        agent.worldmap_file = args.worldmap_file
        agent.world = WorldMap.load(args.worldmap_file)  # resume learned geometry, if any

    producer = None
    run_id = None
    if args.live:
        from live_producer import LiveProducer as _LiveProducer

        run_id = RunRecorder.new_run_id(datetime.now(timezone.utc), uuid.uuid4().hex[:4])
        producer = _LiveProducer(f"{args.viewer_url}/ws/produce/{run_id}", run_id)

    recorder = None
    if args.record or args.live:
        if run_id is None:
            run_id = RunRecorder.new_run_id(datetime.now(timezone.utc), uuid.uuid4().hex[:4])
        recorder = build_recorder(
            record=True,
            runs_dir=Path(args.runs_dir),
            run_id=run_id,
            grabber=lambda: Image.fromarray(agent.pyboy.screen.ndarray),
            frame_interval=args.frame_interval,
            live=producer.send if producer is not None else None,
        )
    if game_pub is not None or recorder is not None:
        agent.collector = GameEventCollector(publisher=game_pub, recorder=recorder, game=agent.profile.name)
    if recorder is not None:
        recorder.start({"strategy": args.strategy, "rom": args.rom, "label": args.label})

    fitness = None
    try:
        fitness = agent.run(
            max_turns=args.max_turns,
            battle_limit=args.battle_limit,
            load_state=args.load_state,
            save_state_on_battle=args.save_state_on_battle,
            save_state_on_map=args.save_state_on_map,
            save_state_on_trainer=args.save_state_on_trainer,
            save_state_every=args.save_state_every,
        )
    finally:
        if recorder is not None:
            recorder.finish(fitness if fitness is not None else {})
        if game_pub is not None:
            game_pub.close()

    if args.output_json:
        Path(args.output_json).parent.mkdir(parents=True, exist_ok=True)
        Path(args.output_json).write_text(json.dumps(fitness, indent=2) + "\n")

    if args.telemetry_dir:
        try:
            from publisher import make_publisher

            pub = make_publisher(telemetry_dir=args.telemetry_dir, config_path=config_path)
            pub.publish(
                {
                    "schema": "tapes.node.v1",
                    "type": "fitness",
                    "root_hash": f"local-{Path(args.rom).stem}",
                    "node": {
                        "bucket": {"role": "agent", "model": "pokemon-agent"},
                        "usage": {"input_tokens": 0, "output_tokens": 0},
                        "project": "pokemon-kafka",
                    },
                    "fitness": fitness,
                    "params": json.loads(os.environ.get("EVOLVE_PARAMS", "{}")),
                }
            )
            pub.close()
        except Exception as exc:
            print(f"[agent] telemetry publish failed: {exc}")


if __name__ == "__main__":
    main()
