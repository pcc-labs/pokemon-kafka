#!/usr/bin/env python3
"""Run agent variants in parallel via paper start claude.

Each agent is a full Claude Code session (recorded in Paper) that runs
agent.py with its parameter variant and reports fitness as JSON to stdout.

Usage:
    python3 scripts/run_10_agents.py <rom>
"""

import json
import os
import re
import signal
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

SCRIPT_DIR = Path(__file__).parent
WORKSPACE = SCRIPT_DIR.parent

from evolve import score  # noqa: E402

_BT_DEFAULTS = {
    "bt_max_snapshots": 8,
    "bt_restore_threshold": 15,
    "bt_max_attempts": 3,
    "bt_snapshot_interval": 50,
}

PARAM_VARIANTS = [
    {
        "stuck_threshold": 8,
        "door_cooldown": 4,
        "waypoint_skip_distance": 3,
        "axis_preference_map_0": "y",
        **_BT_DEFAULTS,
        "label": "baseline_4dc",
    },
    {
        "stuck_threshold": 8,
        "door_cooldown": 8,
        "waypoint_skip_distance": 3,
        "axis_preference_map_0": "y",
        **_BT_DEFAULTS,
        "label": "original",
    },
    {
        "stuck_threshold": 8,
        "door_cooldown": 2,
        "waypoint_skip_distance": 3,
        "axis_preference_map_0": "y",
        **_BT_DEFAULTS,
        "label": "dc2",
    },
    {
        "stuck_threshold": 4,
        "door_cooldown": 4,
        "waypoint_skip_distance": 3,
        "axis_preference_map_0": "y",
        **_BT_DEFAULTS,
        "label": "low_stuck_dc4",
    },
    {
        "stuck_threshold": 12,
        "door_cooldown": 4,
        "waypoint_skip_distance": 3,
        "axis_preference_map_0": "y",
        **_BT_DEFAULTS,
        "label": "high_stuck_dc4",
    },
    {
        "stuck_threshold": 8,
        "door_cooldown": 4,
        "waypoint_skip_distance": 6,
        "axis_preference_map_0": "y",
        **_BT_DEFAULTS,
        "label": "wide_skip_dc4",
    },
    {
        "stuck_threshold": 8,
        "door_cooldown": 4,
        "waypoint_skip_distance": 1,
        "axis_preference_map_0": "y",
        **_BT_DEFAULTS,
        "label": "narrow_dc4",
    },
    {
        "stuck_threshold": 8,
        "door_cooldown": 4,
        "waypoint_skip_distance": 3,
        "axis_preference_map_0": "x",
        **_BT_DEFAULTS,
        "label": "x_axis_dc4",
    },
    {
        "stuck_threshold": 3,
        "door_cooldown": 2,
        "waypoint_skip_distance": 5,
        "axis_preference_map_0": "y",
        **_BT_DEFAULTS,
        "label": "aggressive",
    },
    {
        "stuck_threshold": 6,
        "door_cooldown": 6,
        "waypoint_skip_distance": 4,
        "axis_preference_map_0": "y",
        **_BT_DEFAULTS,
        "label": "moderate",
    },
    {
        "stuck_threshold": 8,
        "door_cooldown": 4,
        "waypoint_skip_distance": 3,
        "axis_preference_map_0": "y",
        "bt_max_snapshots": 8,
        "bt_restore_threshold": 10,
        "bt_max_attempts": 5,
        "bt_snapshot_interval": 50,
        "label": "aggressive_bt",
    },
    {
        "stuck_threshold": 8,
        "door_cooldown": 4,
        "waypoint_skip_distance": 3,
        "axis_preference_map_0": "y",
        "bt_max_snapshots": 0,
        "bt_restore_threshold": 999,
        "bt_max_attempts": 3,
        "bt_snapshot_interval": 50,
        "label": "no_bt",
    },
    # Battle-strategy variants (fork): tune HP thresholds and status-move scoring
    {
        "stuck_threshold": 8,
        "door_cooldown": 4,
        "waypoint_skip_distance": 3,
        "axis_preference_map_0": "y",
        **_BT_DEFAULTS,
        "hp_run_threshold": 0.1,
        "hp_heal_threshold": 0.15,
        "label": "aggressive_battle",
    },
    {
        "stuck_threshold": 8,
        "door_cooldown": 4,
        "waypoint_skip_distance": 3,
        "axis_preference_map_0": "y",
        **_BT_DEFAULTS,
        "hp_run_threshold": 0.35,
        "hp_heal_threshold": 0.4,
        "label": "cautious_battle",
    },
    {
        "stuck_threshold": 8,
        "door_cooldown": 4,
        "waypoint_skip_distance": 3,
        "axis_preference_map_0": "y",
        **_BT_DEFAULTS,
        "status_move_score": 5.0,
        "label": "status_moves",
    },
    {
        "stuck_threshold": 3,
        "door_cooldown": 2,
        "waypoint_skip_distance": 5,
        "axis_preference_map_0": "y",
        **_BT_DEFAULTS,
        "hp_run_threshold": 0.1,
        "hp_heal_threshold": 0.15,
        "label": "full_aggressive",
    },
]

MAX_TURNS = 5000
CONCURRENCY = 5
AGENT_TIMEOUT = 600  # seconds — agent.py can run several minutes for 5000 turns


def _has_paper() -> bool:
    try:
        subprocess.run(["paper", "--version"], capture_output=True, timeout=5)
        return True
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return False


def _build_prompt(rom_path: str, params: dict, label: str) -> str:
    agent_params = {k: v for k, v in params.items() if k != "label"}
    output_json = f"/tmp/pokemon_fitness_{label}.json"
    return (
        f"Run the Pokemon agent with the parameters below and print the fitness JSON.\n\n"
        f"```bash\n"
        f"EVOLVE_PARAMS='{json.dumps(agent_params)}' "
        f"~/venv/bin/python3 scripts/agent.py '{rom_path}' "
        f"--max-turns {MAX_TURNS} --output-json {output_json}\n"
        f"```\n\n"
        f"The command runs a headless Game Boy emulator and takes 2-5 minutes. "
        f"Use a 10-minute bash timeout. "
        f"When it finishes, read {output_json} and print ONLY its raw JSON contents "
        f"as the final line of your response — no markdown, no explanation."
    )


def _extract_fitness(stdout: str) -> dict:
    """Find a JSON fitness object in Claude's stdout.

    agent.py writes indented JSON, so we search the full output blob rather
    than scanning line by line.
    """
    m = re.search(r'\{[^{}]*"party_size"[^{}]*\}', stdout, re.DOTALL)
    if m:
        try:
            return json.loads(m.group(0))
        except json.JSONDecodeError:
            pass
    return {}


def _terminate_tree(proc: subprocess.Popen) -> None:
    """Kill the agent's entire process group.

    ``paper start claude`` spawns claude, which spawns agent.py. A plain
    ``subprocess.run(timeout=...)`` only SIGKILLs the direct child on timeout,
    orphaning those descendants — they keep running and paperd never sees the
    session end, so it shows "Running" forever. Because the child was launched
    with ``start_new_session=True`` it leads its own process group, so we can
    signal the whole group and reap the tree.
    """
    try:
        os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
    except (ProcessLookupError, PermissionError, OSError):
        pass  # already gone, or we can't signal it
    try:
        proc.wait(timeout=10)
    except subprocess.TimeoutExpired:
        pass


def run_one_agent(rom_path: str, params: dict, agent_id: int, use_paper: bool) -> dict:
    label = params.get("label", f"agent_{agent_id}")
    prompt = _build_prompt(rom_path, params, label)
    stripped = {k: v for k, v in params.items() if k != "label"}

    if use_paper:
        # Strip API key and base URL — paper start handles auth
        env = {k: v for k, v in os.environ.items() if k not in ("ANTHROPIC_API_KEY", "ANTHROPIC_BASE_URL")}
        cmd = ["paper", "start", "claude", "--", "--print", "--dangerously-skip-permissions", prompt]
    else:
        env = os.environ.copy()
        cmd = ["claude", "--print", "--dangerously-skip-permissions", prompt]

    start = time.time()
    proc = None
    try:
        # start_new_session=True puts the child in its own process group so a
        # timeout can kill the whole paper→claude→agent.py tree, not just the
        # direct child (which would otherwise orphan the emulator process).
        proc = subprocess.Popen(
            cmd,
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            cwd=str(WORKSPACE),
            start_new_session=True,
        )
        stdout, _ = proc.communicate(timeout=AGENT_TIMEOUT)
        elapsed = time.time() - start
        fitness = _extract_fitness(stdout)
        return {
            "agent_id": agent_id,
            "label": label,
            "params": stripped,
            "fitness": fitness,
            "score": score(fitness) if fitness else -999,
            "elapsed": round(elapsed, 1),
            "returncode": proc.returncode,
        }
    except subprocess.TimeoutExpired:
        _terminate_tree(proc)
        return {
            "agent_id": agent_id,
            "label": label,
            "params": stripped,
            "fitness": {},
            "score": -999,
            "elapsed": round(time.time() - start, 1),
            "error": "timeout",
        }
    except Exception as e:
        if proc is not None:
            _terminate_tree(proc)
        return {
            "agent_id": agent_id,
            "label": label,
            "params": stripped,
            "fitness": {},
            "score": -999,
            "elapsed": round(time.time() - start, 1),
            "error": str(e),
        }


def main():
    if len(sys.argv) < 2:
        print("Usage: python3 scripts/run_10_agents.py <rom>")
        sys.exit(1)

    rom_path = str(Path(sys.argv[1]).resolve())
    if not Path(rom_path).exists():
        print(f"ROM not found: {rom_path}")
        sys.exit(1)

    use_paper = _has_paper()
    harness = "paper start claude" if use_paper else "claude --print"

    print(
        f"[run_10] {len(PARAM_VARIANTS)} agents | harness={harness} | concurrency={CONCURRENCY} | max_turns={MAX_TURNS}"
    )
    print(f"[run_10] ROM: {rom_path}\n")

    all_results = []
    start_time = time.time()

    with ThreadPoolExecutor(max_workers=CONCURRENCY) as executor:
        futures = {
            executor.submit(run_one_agent, rom_path, params, i, use_paper): i for i, params in enumerate(PARAM_VARIANTS)
        }
        for future in as_completed(futures):
            r = future.result()
            f = r.get("fitness", {})
            status = "OK" if "error" not in r else f"FAIL({r.get('error', '')})"
            print(
                f"  [{status}] {r['agent_id']:2d} ({r['label']:14s}) | "
                f"score={r['score']:8.1f} | map={f.get('final_map_id', '?')} "
                f"party={f.get('party_size', '?')} stuck={f.get('stuck_count', '?')} | "
                f"{r['elapsed']}s"
            )
            all_results.append(r)

    total_time = time.time() - start_time
    all_results.sort(key=lambda r: r["score"], reverse=True)

    print(f"\n{'=' * 70}")
    print(f"[run_10] {len(all_results)} agents done in {total_time:.1f}s")
    print(f"{'=' * 70}\n")
    print(f"{'Rank':>4} {'Label':14s} {'Score':>8} {'Map':>4} {'Party':>5} {'Stuck':>5} {'Turns':>5} {'Time':>6}")
    print("-" * 60)
    for rank, r in enumerate(all_results, 1):
        f = r.get("fitness", {})
        print(
            f"{rank:4d} {r['label']:14s} {r['score']:8.1f} "
            f"{str(f.get('final_map_id', '?')):>4} {str(f.get('party_size', '?')):>5} "
            f"{str(f.get('stuck_count', '?')):>5} {str(f.get('turns', '?')):>5} "
            f"{r['elapsed']:5.1f}s"
        )

    winner = all_results[0]
    print(f"\nWinner: {winner['label']} (score={winner['score']:.1f})")
    print(f"Params: {json.dumps(winner['params'], indent=2)}")

    results_path = WORKSPACE / "pokedex" / "evolve_results.json"
    results_path.parent.mkdir(parents=True, exist_ok=True)
    results_path.write_text(json.dumps(all_results, indent=2) + "\n")
    print(f"\nSaved to: {results_path}")


if __name__ == "__main__":
    main()
