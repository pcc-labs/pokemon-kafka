"""Healer — anomaly-triggered parameter races (the self-healing tuning loop).

Closes the between-run loop with zero human input: a bad run's fitness JSON
(the agent's --output-json) trips a rule, the healer races seeded parameter
variants on the implicated knobs via evolve.run_agent, and a winner that
beats the current genome by a margin is appended to notes.md in the
autotune genome format the agent already loads at startup.

Broker-free and host-side by design: races launch PyBoy subprocesses, and
reading fitness files means healing works with or without the Kafka stack.

Wrapper usage:
    uv run scripts/agent.py ROM --output-json f.json ... \
        && uv run scripts/healer.py check --fitness f.json --rom ROM

`check` always exits 0 — a healing failure must never fail the run wrapper.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import random
import sys
from dataclasses import dataclass
from pathlib import Path

from autotune_bridge import load_genome_from_notes
from evolve import DEFAULT_PARAMS, PARAM_BOUNDS, clamp_params, run_agent, score

STUCK_COUNT_THRESHOLD = 12
TERMINAL_WEDGE_STREAK = 50
BACKTRACK_RESTORES_THRESHOLD = 3
NO_PROGRESS_MAX_MAPS = 1
NO_PROGRESS_MIN_TURNS = 500
MARGIN = 0.05
COOLDOWN_HOURS = 6.0
ESCALATE_AFTER_REJECTS = 2

# Trigger rules are data: adding one is a table edit, not new machinery.
RULES = [
    {
        "name": "navigation-thrash",
        "fires": lambda f: (
            f.get("stuck_count", 0) >= STUCK_COUNT_THRESHOLD
            or f.get("backtrack_restores", 0) >= BACKTRACK_RESTORES_THRESHOLD
        ),
        "params": ["door_cooldown", "stuck_threshold", "waypoint_skip_distance", "bt_restore_threshold"],
    },
    {
        # stuck_count only counts wedge *episodes* (STUCK logs at streaks 2/5/10/20),
        # so one fatal wedge stays under navigation-thrash. Streak length catches it.
        "name": "terminal-wedge",
        "fires": lambda f: f.get("max_stuck_streak", 0) >= TERMINAL_WEDGE_STREAK,
        "params": ["door_cooldown", "stuck_threshold", "waypoint_skip_distance", "bt_restore_threshold"],
    },
    {
        "name": "no-progress",
        "fires": lambda f: (
            f.get("maps_visited", 0) <= NO_PROGRESS_MAX_MAPS and f.get("turns", 0) >= NO_PROGRESS_MIN_TURNS
        ),
        "params": ["door_cooldown", "stuck_threshold", "bt_snapshot_interval"],
    },
]


@dataclass
class RaceResult:
    params: dict
    fitness: dict
    score: float


def evaluate_rules(fitness: dict) -> list[dict]:
    """Return the rules this run's fitness trips, in table order."""
    return [rule for rule in RULES if rule["fires"](fitness)]


def sample_variants(base: dict, param_names: list[str], n: int, rng: random.Random) -> list[dict]:
    """n copies of *base*, each with only the implicated params resampled within bounds."""
    variants = []
    for _ in range(n):
        v = dict(base)
        for name in param_names:
            bounds = PARAM_BOUNDS[name]
            if all(isinstance(b, str) for b in bounds):  # enum parameter
                v[name] = rng.choice(bounds)
            else:
                lo, hi, typ = bounds
                v[name] = rng.randint(lo, hi) if typ is int else rng.uniform(lo, hi)
        variants.append(clamp_params(v))
    return variants


def decide(control_score: float, winner_score: float, margin: float = MARGIN) -> bool:
    """Accept only a clear win: beat the control by margin·|control| (any gain when control is 0)."""
    return winner_score > control_score + abs(control_score) * margin


def cooldown_active(state: dict, now_ts: float, hours: float = COOLDOWN_HOURS) -> bool:
    last = state.get("last_race_at")
    return last is not None and (now_ts - last) < hours * 3600


def load_state(path) -> dict:
    try:
        return json.loads(Path(path).read_text())
    except (OSError, json.JSONDecodeError):
        return {}


def save_state(path, state: dict) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(state, indent=2))


def append_genome(notes_path, genome: dict, reason_line: str) -> None:
    """Append a fresh autotune genome block (last block wins for load_genome_from_notes)."""
    path = Path(notes_path)
    existing = path.read_text() if path.exists() else "# Agent Notes\n"
    block = f"\n{reason_line}\n<!-- autotune:genome\n{json.dumps(genome)}\n-->\n"
    path.write_text(existing + block)


def should_escalate(state: dict, rule_name: str) -> str | None:
    """Escalation check, run AFTER the current race is recorded in state.

    Escalates only when the knobs have failed: the current race rejected AND
    either the previous race for this rule was accepted (the tuned fix did
    not hold) or the last ESCALATE_AFTER_REJECTS races for it all rejected.
    """
    races = [r for r in state.get("races", []) if r.get("rule") == rule_name]
    if not races or races[-1].get("accepted"):
        return None
    if len(races) >= 2 and races[-2].get("accepted"):
        return "refire-after-accept"
    recent = races[-ESCALATE_AFTER_REJECTS:]
    if len(recent) == ESCALATE_AFTER_REJECTS and not any(r.get("accepted") for r in recent):
        return "rejects-exhausted"
    return None


def append_escalation(queue_path, entry: dict) -> None:
    """Append an escalation entry for the discovery engine (loop 3)."""
    path = Path(queue_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        entries = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError):
        entries = []
    entries.append(entry)
    path.write_text(json.dumps(entries, indent=2))


def default_seed(fitness_path) -> int:
    """Deterministic per fitness-file content, so a rerun races the same variants."""
    digest = hashlib.sha256(Path(fitness_path).read_bytes()).hexdigest()
    return int(digest[:16], 16)


def run_race(rom: str, turns: int, candidates: list[dict]) -> list[RaceResult]:
    """Run every candidate through evolve.run_agent; the only impure racing code."""
    results = []
    for params in candidates:
        fitness = run_agent(rom, turns, params)
        results.append(RaceResult(params=params, fitness=fitness, score=score(fitness)))
    return results


def _check(args) -> None:
    fitness_path = Path(args.fitness)
    try:
        fitness = json.loads(fitness_path.read_text())
    except (OSError, json.JSONDecodeError) as exc:
        print(f"[healer] unreadable fitness file {fitness_path}: {exc}")
        return

    fired = evaluate_rules(fitness)
    if not fired:
        print("[healer] run healthy — no rules fired")
        return

    rule = fired[0]  # one race per check; first rule in table order wins
    param_names = rule["params"]

    state = load_state(args.state)
    now_ts = fitness_path.stat().st_mtime
    if cooldown_active(state, now_ts, args.cooldown_hours):
        print(f"[healer] {rule['name']} fired but cooldown active — skipping race")
        return

    if args.dry_run:
        print(f"[healer] dry-run: {rule['name']} fired, would race {args.variants} variants of {param_names}")
        return

    base = clamp_params({**DEFAULT_PARAMS, **load_genome_from_notes(args.notes)})
    seed = args.seed if args.seed is not None else default_seed(fitness_path)
    candidates = [base] + sample_variants(base, param_names, args.variants, random.Random(seed))

    results = run_race(args.rom, args.race_turns, candidates)
    control = results[0]
    winner = max(results, key=lambda r: r.score)
    accepted = winner is not control and decide(control.score, winner.score)

    if accepted:
        changed = {k: f"{base[k]}→{winner.params[k]}" for k in param_names if winner.params[k] != base[k]}
        reason = (
            f"Healer: {rule['name']} — {', '.join(f'{k} {v}' for k, v in changed.items()) or 'no change'} "
            f"(score {control.score:.0f}→{winner.score:.0f})"
        )
        append_genome(args.notes, winner.params, reason)
        print(f"[healer] accepted: {reason}")
    else:
        print(f"[healer] kept current genome (control {control.score:.0f}, best variant {winner.score:.0f})")

    state.setdefault("races", []).append(
        {"at": now_ts, "rule": rule["name"], "accepted": accepted, "genome": winner.params if accepted else None}
    )
    state["last_race_at"] = now_ts
    save_state(args.state, state)

    escalation = should_escalate(state, rule["name"])
    if escalation:
        append_escalation(
            args.queue,
            {"at": now_ts, "rule": rule["name"], "reason": escalation, "fitness": fitness, "handled": False},
        )
        print(f"[healer] escalating {rule['name']} to the discovery engine ({escalation})")


def main() -> int:
    parser = argparse.ArgumentParser(description="Self-healing tuning loop")
    sub = parser.add_subparsers(dest="command", required=True)
    check = sub.add_parser("check", help="evaluate a run's fitness and race parameters if a rule fires")
    check.add_argument("--fitness", required=True, help="fitness JSON from agent.py --output-json")
    check.add_argument("--rom", required=True, help="ROM path for race runs")
    check.add_argument("--race-turns", type=int, default=800, help="turns per race candidate")
    check.add_argument("--variants", type=int, default=6, help="variants to race (control always included)")
    check.add_argument("--seed", type=int, default=None, help="sampling seed (default: fitness file hash)")
    check.add_argument("--dry-run", action="store_true", help="print the decision without racing")
    check.add_argument("--notes", default="notes.md", help="notes.md holding the autotune genome")
    check.add_argument("--state", default="data/healer_state.json", help="healer cooldown/race history")
    check.add_argument("--queue", default="data/discovery_queue.json", help="loop-3 escalation queue")
    check.add_argument("--cooldown-hours", type=float, default=COOLDOWN_HOURS)
    args = parser.parse_args()

    try:
        _check(args)
    except Exception as exc:  # healing must never fail the run wrapper
        print(f"[healer] healer error: {exc}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
