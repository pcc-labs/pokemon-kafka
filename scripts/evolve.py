#!/usr/bin/env python3
"""AlphaEvolve-inspired strategy evolution for the Pokemon agent.

Runs the agent headless, collects fitness metrics, uses an LLM to propose
parameter variants, and keeps improvements. Starts with navigator knobs
(numeric thresholds) rather than full function rewrites.

Usage:
    uv run scripts/evolve.py <rom> --generations 5 --max-turns 200
"""

import argparse
import json
import os
import subprocess
import sys
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

SCRIPT_DIR = Path(__file__).parent

# ---------------------------------------------------------------------------
# Genome: evolvable navigator parameters
# ---------------------------------------------------------------------------

DEFAULT_PARAMS = {
    "stuck_threshold": 8,
    "door_cooldown": 8,
    "waypoint_skip_distance": 3,
    "axis_preference_map_0": "y",
    "bt_max_snapshots": 8,
    "bt_restore_threshold": 15,
    "bt_max_attempts": 3,
    "bt_snapshot_interval": 50,
    "hp_run_threshold": 0.2,
    "hp_heal_threshold": 0.25,
    "unknown_move_score": 10.0,
    "status_move_score": 1.0,
}

# Bounds for each evolvable parameter: (min, max, type) or tuple of valid values for enums
PARAM_BOUNDS = {
    "stuck_threshold": (3, 20, int),
    "door_cooldown": (4, 16, int),
    "waypoint_skip_distance": (1, 8, int),
    "axis_preference_map_0": ("x", "y"),
    "bt_max_snapshots": (2, 16, int),
    "bt_restore_threshold": (8, 30, int),
    "bt_max_attempts": (1, 5, int),
    "bt_snapshot_interval": (20, 100, int),
    "hp_run_threshold": (0.05, 0.5, float),
    "hp_heal_threshold": (0.1, 0.6, float),
    "unknown_move_score": (1.0, 30.0, float),
    "status_move_score": (0.0, 10.0, float),
}


def clamp_params(params: dict) -> dict:
    """Clamp parameters to their defined bounds. Pure function."""
    clamped = dict(params)
    for key, bounds in PARAM_BOUNDS.items():
        if key not in clamped:
            continue
        # Enum parameter: validate against allowed values
        if all(isinstance(v, str) for v in bounds):
            if clamped[key] not in bounds:
                clamped[key] = DEFAULT_PARAMS[key]
        else:
            lo, hi, typ = bounds
            try:
                clamped[key] = typ(clamped[key])
            except (ValueError, TypeError):
                clamped[key] = DEFAULT_PARAMS[key]
                continue
            clamped[key] = max(lo, min(hi, clamped[key]))
    return clamped


@dataclass
class EvolutionResult:
    """Outcome of a single generation."""

    generation: int = 0
    params: dict = field(default_factory=dict)
    fitness: dict = field(default_factory=dict)
    score: float = 0.0
    improved: bool = False


# ---------------------------------------------------------------------------
# Fitness scoring
# ---------------------------------------------------------------------------


MAP_PROGRESS = {
    37: 1,  # Player house 1F
    38: 2,  # Player house 2F
    40: 3,  # Oak's Lab
    0: 4,  # Pallet Town
    12: 5,  # Route 1
    1: 6,  # Viridian City
    13: 7,  # Route 2
    51: 8,  # Viridian Forest
    2: 9,  # Pewter City
}


def score(fitness: dict) -> float:
    """Composite fitness score weighted toward navigation progress."""
    map_id = fitness.get("final_map_id", 0)
    progress = MAP_PROGRESS.get(map_id, 0)
    return (
        progress * 1000
        + fitness.get("badges", 0) * 5000
        + fitness.get("party_size", 0) * 500
        + fitness.get("battles_won", 0) * 100
        - fitness.get("stuck_count", 0) * 5
        - fitness.get("turns", 0) * 0.1
        - fitness.get("backtrack_restores", 0) * 2
    )


# ---------------------------------------------------------------------------
# Agent runner (subprocess isolation)
# ---------------------------------------------------------------------------


def run_agent(rom_path: str, max_turns: int, params: dict) -> dict:
    """Run the agent in a subprocess and return fitness metrics.

    Passes params as the EVOLVE_PARAMS env var (JSON). The agent reads
    this to override navigator defaults.
    """
    with tempfile.NamedTemporaryFile(suffix=".json", delete=False, mode="w") as f:
        output_path = f.name

    env = os.environ.copy()
    env["EVOLVE_PARAMS"] = json.dumps(params)

    cmd = [
        sys.executable,
        str(SCRIPT_DIR / "agent.py"),
        rom_path,
        "--max-turns",
        str(max_turns),
        "--output-json",
        output_path,
    ]

    try:
        subprocess.run(cmd, env=env, capture_output=True, timeout=600)
        fitness = json.loads(Path(output_path).read_text())
    except (subprocess.TimeoutExpired, FileNotFoundError, json.JSONDecodeError):
        fitness = {
            "turns": max_turns,
            "battles_won": 0,
            "maps_visited": 0,
            "final_map_id": 0,
            "final_x": 0,
            "final_y": 0,
            "badges": 0,
            "party_size": 0,
            "stuck_count": max_turns,
        }
    finally:
        try:
            os.unlink(output_path)
        except OSError:
            pass

    return fitness


# ---------------------------------------------------------------------------
# LLM variant proposal
# ---------------------------------------------------------------------------


def build_mutation_prompt(
    params: dict,
    fitness: dict,
    observations: list[dict] | None = None,
    historical: list[dict] | None = None,
    evolution_history: list[EvolutionResult] | None = None,
    stagnant: bool = False,
) -> str:
    """Build a prompt asking the LLM to propose a parameter variant."""
    obs_section = ""
    if observations:
        obs_lines = []
        for o in observations:
            obs_lines.append(f"  - [{o['priority']}] {o['content']}")
        obs_section = "\nRecent observations:\n" + "\n".join(obs_lines) + "\n"

    hist_section = ""
    if historical:
        hist_lines = []
        for h in historical:
            hist_lines.append(f"  - [{h['priority']}] {h['content']}")
        hist_section = "\nCross-session historical insights:\n" + "\n".join(hist_lines) + "\n"

    evo_section = ""
    if evolution_history:
        evo_lines = []
        for r in evolution_history[-10:]:
            diffs = {k: v for k, v in r.params.items() if v != DEFAULT_PARAMS.get(k)}
            status = "improved" if r.improved else "no improvement"
            evo_lines.append(f"  gen {r.generation}: score={r.score:.1f} ({status}) diffs={json.dumps(diffs)}")
        evo_section = "\nPrevious generations (avoid repeating failed combinations):\n" + "\n".join(evo_lines) + "\n"

    stagnant_section = ""
    if stagnant:
        stagnant_section = (
            "\nWARNING: The last several generations showed NO improvement. "
            "Make LARGER changes to escape this plateau. "
            "Try changing 3-4 parameters simultaneously with bigger deltas.\n"
        )

    return f"""You are tuning navigation parameters for a Pokemon Red AI agent.

Current parameters:
{json.dumps(params, indent=2)}

Current fitness:
{json.dumps(fitness, indent=2)}

Current score: {score(fitness):.1f}
{obs_section}{hist_section}{evo_section}{stagnant_section}
Parameter descriptions:
- stuck_threshold: how many stuck turns before skipping a waypoint (int, 3-20)
- door_cooldown: frames to walk away from a door after exiting (int, 4-16)
- waypoint_skip_distance: max Manhattan distance to skip a waypoint when stuck (int, 1-8)
- axis_preference_map_0: preferred movement axis on Pallet Town map ("x" or "y")
- bt_max_snapshots: max number of backtrack snapshots to keep (int, 2-16)
- bt_restore_threshold: stuck turns before restoring a snapshot (int, 8-30)
- bt_max_attempts: max times to retry from the same snapshot (int, 1-5)
- bt_snapshot_interval: turns between periodic snapshots when not stuck (int, 20-100)
- hp_run_threshold: HP ratio below which the agent runs from wild battles (float, 0.05-0.5)
- hp_heal_threshold: HP ratio below which the agent uses a healing item (float, 0.1-0.6)
- unknown_move_score: baseline score for moves not in the known move table (float, 1.0-30.0)
- status_move_score: score assigned to zero-power status moves (float, 0.0-10.0)

Propose ONE set of modified parameters to improve the score. Focus on reducing
stuck_count, increasing maps_visited, and winning battles. Return ONLY valid JSON
with the same keys, nothing else."""


def parse_llm_response(response: str | None) -> dict | None:
    """Extract a params dict from an LLM response. Returns None on failure."""
    if response is None:
        return None
    # Try to find JSON in the response
    text = response.strip()
    # Strip markdown code fences if present
    if text.startswith("```"):
        lines = text.split("\n")
        lines = [line for line in lines if not line.startswith("```")]
        text = "\n".join(lines).strip()

    try:
        params = json.loads(text)
    except json.JSONDecodeError:
        return None

    # Validate expected keys
    for key in DEFAULT_PARAMS:
        if key not in params:
            return None

    return clamp_params(params)


# ---------------------------------------------------------------------------
# Evolution loop
# ---------------------------------------------------------------------------


def evolve(
    rom_path: str,
    max_generations: int = 5,
    max_turns: int = 200,
    llm_fn=None,
    observer_fn=None,
    historical_fn=None,
) -> list[EvolutionResult]:
    """Run the evolution loop.

    Args:
        rom_path: Path to the Pokemon ROM.
        max_generations: Number of generations to run.
        max_turns: Max turns per agent run.
        llm_fn: Callable(prompt) -> str. If None, uses random perturbation.
        observer_fn: Callable() -> list[dict]. Returns observations for LLM context.
        historical_fn: Callable() -> list[dict]. Returns cross-session insights.

    Returns:
        List of EvolutionResult for each generation.
    """
    current_params = dict(DEFAULT_PARAMS)
    results: list[EvolutionResult] = []

    # Baseline run
    baseline_fitness = run_agent(rom_path, max_turns, current_params)
    baseline_score = score(baseline_fitness)
    print(f"[evolve] Baseline score: {baseline_score:.1f}")
    print(f"[evolve] Baseline fitness: {json.dumps(baseline_fitness)}")

    for gen in range(max_generations):
        print(f"\n[evolve] === Generation {gen + 1}/{max_generations} ===")

        # Get observations if available
        observations = observer_fn() if observer_fn else None
        historical = historical_fn() if historical_fn else None

        # Detect stagnation
        stagnant = detect_stagnation(results)

        # Propose variant
        if llm_fn:
            prompt = build_mutation_prompt(
                current_params,
                baseline_fitness,
                observations,
                historical,
                evolution_history=results,
                stagnant=stagnant,
            )
            response = llm_fn(prompt)
            variant_params = parse_llm_response(response)
            if variant_params is None:
                print("[evolve] LLM returned invalid params, skipping generation")
                results.append(
                    EvolutionResult(
                        generation=gen + 1,
                        params=current_params,
                        fitness=baseline_fitness,
                        score=baseline_score,
                        improved=False,
                    )
                )
                continue
        else:
            # No LLM: use forced exploration when stagnant, else simple perturbation
            if stagnant:
                variant_params = _forced_exploration_perturb(current_params)
            else:
                variant_params = _perturb(current_params)

        print(f"[evolve] Variant params: {json.dumps(variant_params)}")

        # Run variant
        variant_fitness = run_agent(rom_path, max_turns, variant_params)
        variant_score = score(variant_fitness)
        print(f"[evolve] Variant score: {variant_score:.1f} (baseline: {baseline_score:.1f})")

        improved = variant_score > baseline_score
        if improved:
            print("[evolve] Improvement found! Adopting variant.")
            current_params = variant_params
            baseline_fitness = variant_fitness
            baseline_score = variant_score
        else:
            print("[evolve] No improvement. Keeping baseline.")

        results.append(
            EvolutionResult(
                generation=gen + 1,
                params=dict(current_params),
                fitness=variant_fitness if improved else baseline_fitness,
                score=variant_score if improved else baseline_score,
                improved=improved,
            )
        )

    print(f"\n[evolve] Final params: {json.dumps(current_params, indent=2)}")
    print(f"[evolve] Final score: {baseline_score:.1f}")

    return results


STALE_THRESHOLD = 3


def detect_stagnation(results: list[EvolutionResult], threshold: int = STALE_THRESHOLD) -> bool:
    """Return True if the last `threshold` results all failed to improve."""
    if len(results) < threshold:
        return False
    return all(not r.improved for r in results[-threshold:])


def _forced_exploration_perturb(params: dict) -> dict:
    """Aggressive perturbation: mutate 3-4 params with 2x deltas."""
    import random

    INT_KEYS = [
        "stuck_threshold",
        "door_cooldown",
        "waypoint_skip_distance",
        "bt_max_snapshots",
        "bt_restore_threshold",
        "bt_max_attempts",
        "bt_snapshot_interval",
    ]
    FLOAT_KEYS = [
        "hp_run_threshold",
        "hp_heal_threshold",
        "unknown_move_score",
        "status_move_score",
    ]

    new = dict(params)
    num_changes = random.choice([3, 4])
    keys = random.sample(INT_KEYS + FLOAT_KEYS, min(num_changes, len(INT_KEYS + FLOAT_KEYS)))
    for key in keys:
        if key in FLOAT_KEYS:
            delta = random.choice([-0.2, -0.1, 0.1, 0.2])
            new[key] = round(new[key] + delta, 4)
        else:
            delta = random.choice([-4, -3, -2, 2, 3, 4])
            new[key] = new[key] + delta
    # Always flip axis preference during forced exploration
    new["axis_preference_map_0"] = "x" if new["axis_preference_map_0"] == "y" else "y"
    return clamp_params(new)


def _perturb(params: dict) -> dict:
    """Simple random perturbation of numeric params (no LLM needed)."""
    import random

    INT_KEYS = [
        "stuck_threshold",
        "door_cooldown",
        "waypoint_skip_distance",
        "bt_max_snapshots",
        "bt_restore_threshold",
        "bt_max_attempts",
        "bt_snapshot_interval",
    ]
    FLOAT_KEYS = [
        "hp_run_threshold",
        "hp_heal_threshold",
        "unknown_move_score",
        "status_move_score",
    ]

    new = dict(params)
    key = random.choice(INT_KEYS + FLOAT_KEYS)
    if key in FLOAT_KEYS:
        delta = random.choice([-0.1, -0.05, 0.05, 0.1])
        new[key] = round(new[key] + delta, 4)
    else:
        delta = random.choice([-2, -1, 1, 2])
        new[key] = new[key] + delta
    # Randomly flip axis preference
    if random.random() < 0.3:
        new["axis_preference_map_0"] = "x" if new["axis_preference_map_0"] == "y" else "y"
    return clamp_params(new)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _make_observer_fn():
    """Create an observer function that distills the latest Paper session.

    Reads recorded Paper sessions (paperd API + Claude Code JSONL) via the
    observer; returns an empty list when no sessions are available yet.
    """

    def observer_fn():
        from observer import observe_session_inline

        return observe_session_inline()

    return observer_fn


def _make_historical_fn(telemetry_dir: str | None = None):
    """Create a function that returns cross-session insights from JSONL files."""
    if not telemetry_dir:
        return None

    def historical_fn():
        if not Path(telemetry_dir).exists():
            return []
        from historical_observer import observe

        return observe(telemetry_dir)

    return historical_fn


def _make_llm_fn():
    """Create an LLM function using the Anthropic API, if available."""
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        return None

    try:
        import anthropic
    except ImportError:
        print("[evolve] anthropic package not installed, using random perturbation")
        return None

    client = anthropic.Anthropic(api_key=api_key, max_retries=3)

    def llm_fn(prompt: str) -> str | None:
        try:
            response = client.messages.create(
                model="claude-sonnet-4-20250514",
                max_tokens=1024,
                messages=[{"role": "user", "content": prompt}],
            )
            return response.content[0].text
        except anthropic.APIError as exc:
            print(f"[evolve] Anthropic API error: {exc}; falling back to random perturbation")
            return None

    print("[evolve] Using Anthropic API for LLM-guided mutation")
    return llm_fn


def main():
    parser = argparse.ArgumentParser(description="Evolve Pokemon agent parameters")
    parser.add_argument("rom", help="Path to ROM file")
    parser.add_argument(
        "--generations",
        type=int,
        default=5,
        help="Number of evolution generations (default: 5)",
    )
    parser.add_argument(
        "--max-turns",
        type=int,
        default=200,
        help="Max turns per agent run (default: 200)",
    )
    parser.add_argument(
        "--no-llm",
        action="store_true",
        help="Disable LLM mutation, use random perturbation only",
    )
    parser.add_argument(
        "--no-observer",
        action="store_true",
        help="Disable observational memory feedback",
    )
    parser.add_argument(
        "--telemetry-dir",
        default=str(SCRIPT_DIR.parent / "data" / "telemetry"),
        help="JSONL telemetry directory for historical insights (default: data/telemetry)",
    )
    parser.add_argument(
        "--no-historical",
        action="store_true",
        help="Disable cross-session historical insights",
    )
    args = parser.parse_args()

    if not Path(args.rom).exists():
        print(f"ROM not found: {args.rom}")
        sys.exit(1)

    llm_fn = None if args.no_llm else _make_llm_fn()
    observer_fn = None if args.no_observer else _make_observer_fn()
    historical_fn = None if args.no_historical else _make_historical_fn(args.telemetry_dir)

    results = evolve(
        args.rom,
        max_generations=args.generations,
        max_turns=args.max_turns,
        llm_fn=llm_fn,
        observer_fn=observer_fn,
        historical_fn=historical_fn,
    )

    # Summary
    improvements = [r for r in results if r.improved]
    print(f"\n[evolve] {len(improvements)}/{len(results)} generations improved")


if __name__ == "__main__":
    main()
