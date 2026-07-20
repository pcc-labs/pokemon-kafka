"""Discovery engine — LLM capability healing (loop 3).

Heals what parameter tuning cannot. The healer escalates exhausted tuning
into data/discovery_queue.json; this engine hands the evidence to Claude
Code headless in an isolated git worktree, runs the gates itself (full
test suite, ruff, fitness eval vs baseline), and opens a PR with the
proof. A human merges — never auto-merge.

Usage:
    uv run scripts/discovery.py run --rom rom/pokemon_red.gb
    uv run scripts/discovery.py run --rom ROM --reason "forest wall glitch"

`run` always exits 0 — safe to chain after healer checks or cron.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path

from evolve import run_agent, score
from healer import decide, load_state, save_state

DISCOVERY_COOLDOWN_HOURS = 24.0
OBSERVATIONS_PATH = "pokedex/memory/observations.md"
OBSERVATIONS_TAIL_CHARS = 4000

# Which code a rule implicates — the proposer's starting map, not a fence.
RULE_CODE_MAP = {
    "navigation-thrash": ["scripts/pathfinding.py", "scripts/world_map.py", "scripts/agent.py"],
    "no-progress": ["scripts/pathfinding.py", "scripts/world_map.py", "scripts/agent.py"],
    "manual": ["scripts/agent.py"],
}

PROMPT_TEMPLATE = """You are the discovery engine for the pokemon-kafka agent. Parameter tuning \
has been exhausted for the problem below — the fix requires a code change.

## Problem
Rule fired: {rule} (escalation reason: {reason}{detail})
Fitness of the failing run: {fitness}

## Recent healer races (parameter tuning already tried)
{races}

## Recent observations
{observations}

## Where to look first
{code_map}

## Constraints
- Diagnose the root cause before editing; explain the diagnosis in your commit message.
- Make the smallest code change that fixes the root cause. Minimal diff.
- Do not delete or weaken tests. Add a test that captures the failure mode.
- Run the focused tests for what you change (`uv run pytest tests/... -q`).
- Commit your change when done.
"""


# ---------------------------------------------------------------------------
# Queue
# ---------------------------------------------------------------------------


def load_queue(path) -> list[dict]:
    try:
        return json.loads(Path(path).read_text())
    except (OSError, json.JSONDecodeError):
        return []


def pick_entry(entries: list[dict], manual_reason: str | None) -> tuple[dict | None, int | None]:
    """Oldest unhandled queue entry, or a synthetic entry for a manual reason."""
    if manual_reason:
        return {"rule": "manual", "reason": "manual", "detail": manual_reason, "fitness": {}}, None
    pending = [(i, e) for i, e in enumerate(entries) if not e.get("handled")]
    if not pending:
        return None, None
    idx, entry = min(pending, key=lambda pair: pair[1].get("at", 0))
    return entry, idx


def mark_handled(path, idx: int) -> None:
    entries = load_queue(path)
    entries[idx]["handled"] = True
    Path(path).write_text(json.dumps(entries, indent=2))


# ---------------------------------------------------------------------------
# Context bundle + prompt
# ---------------------------------------------------------------------------


def build_bundle(entry: dict, races: list[dict], observations: str) -> dict:
    return {
        "rule": entry["rule"],
        "reason": entry.get("reason", ""),
        "detail": entry.get("detail", ""),
        "fitness": entry.get("fitness", {}),
        "races": races,
        "observations": observations,
        "code_map": RULE_CODE_MAP.get(entry["rule"], RULE_CODE_MAP["manual"]),
    }


def build_prompt(bundle: dict) -> str:
    detail = f" — {bundle['detail']}" if bundle["detail"] else ""
    return PROMPT_TEMPLATE.format(
        rule=bundle["rule"],
        reason=bundle["reason"],
        detail=detail,
        fitness=json.dumps(bundle["fitness"]),
        races=json.dumps(bundle["races"], indent=2) or "none",
        observations=bundle["observations"] or "none",
        code_map="\n".join(f"- {p}" for p in bundle["code_map"]),
    )


def branch_name(rule: str, date_str: str) -> str:
    return f"discovery/{rule}-{date_str}"


def recent_races(healer_state_path, n: int = 5) -> list[dict]:
    return load_state(healer_state_path).get("races", [])[-n:]


def read_observations_tail(path=OBSERVATIONS_PATH, chars: int = OBSERVATIONS_TAIL_CHARS) -> str:
    try:
        return Path(path).read_text()[-chars:]
    except OSError:
        return ""


# ---------------------------------------------------------------------------
# Impure edges: subprocesses, worktree, gates, PR
# ---------------------------------------------------------------------------


def sh(cmd: list[str], cwd=None, timeout: int = 3600) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, timeout=timeout)


def worktree_add(repo_root: Path, branch: str) -> tuple[Path, str]:
    """Create the isolated worktree; returns (path, starting HEAD sha)."""
    wt = Path(repo_root) / ".discovery" / branch.replace("/", "-")
    result = sh(["git", "worktree", "add", str(wt), "-b", branch], cwd=repo_root)
    if result.returncode != 0:
        raise RuntimeError(f"worktree add failed: {result.stderr.strip()}")
    return wt, sh(["git", "rev-parse", "HEAD"], cwd=wt).stdout.strip()


def cleanup(repo_root: Path, worktree: Path, branch: str) -> None:
    sh(["git", "worktree", "remove", "--force", str(worktree)], cwd=repo_root)
    sh(["git", "branch", "-D", branch], cwd=repo_root)


def propose(worktree: Path, prompt: str, max_turns: int) -> str:
    result = sh(
        ["claude", "-p", prompt, "--permission-mode", "acceptEdits", "--max-turns", str(max_turns)],
        cwd=worktree,
        timeout=3600,
    )
    return result.stdout


def has_changes(worktree: Path, start_sha: str) -> bool:
    dirty = sh(["git", "status", "--porcelain"], cwd=worktree).stdout.strip() != ""
    head = sh(["git", "rev-parse", "HEAD"], cwd=worktree).stdout.strip()
    return dirty or head != start_sha


def eval_candidate(worktree: Path, rom: str, runs: int, turns: int) -> list[float]:
    """Score the WORKTREE's agent (candidate code) over *runs* headless runs."""
    scores = []
    for _ in range(runs):
        with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as f:
            out = Path(f.name)
        try:
            sh(
                [
                    sys.executable,
                    str(Path(worktree) / "scripts" / "agent.py"),
                    rom,
                    "--max-turns",
                    str(turns),
                    "--output-json",
                    str(out),
                ],
                cwd=worktree,
                timeout=1200,
            )
            try:
                scores.append(score(json.loads(out.read_text())))
            except (OSError, json.JSONDecodeError):
                scores.append(float("-inf"))
        finally:
            out.unlink(missing_ok=True)
    return scores


def run_gates(worktree: Path, rom: str, eval_runs: int, race_turns: int) -> tuple[bool, str]:
    """Engine-run gates; the proposer's own claims are never trusted."""
    report = []

    tests = sh(["uv", "run", "pytest", "-q"], cwd=worktree)
    report.append(f"pytest: {'pass' if tests.returncode == 0 else 'FAIL'}\n{tests.stdout[-2000:]}")
    if tests.returncode != 0:
        return False, "\n".join(report)

    lint = sh(["uv", "run", "ruff", "check", "."], cwd=worktree)
    report.append(f"ruff: {'pass' if lint.returncode == 0 else 'FAIL'}\n{lint.stdout[-500:]}")
    if lint.returncode != 0:
        return False, "\n".join(report)

    if eval_runs <= 0:
        report.append("fitness eval skipped (--eval-runs 0)")
        return True, "\n".join(report)

    candidate_scores = eval_candidate(worktree, rom, eval_runs, race_turns)
    baseline_scores = [score(run_agent(rom, race_turns, {})) for _ in range(eval_runs)]
    candidate_mean = sum(candidate_scores) / len(candidate_scores)
    baseline_mean = sum(baseline_scores) / len(baseline_scores)
    passed = decide(baseline_mean, candidate_mean)
    report.append(
        f"fitness eval: baseline mean {baseline_mean:.0f}, candidate mean {candidate_mean:.0f} "
        f"-> {'pass' if passed else 'FAIL'}"
    )
    return passed, "\n".join(report)


def commit_if_needed(worktree: Path, message: str) -> None:
    if sh(["git", "status", "--porcelain"], cwd=worktree).stdout.strip():
        sh(["git", "add", "-A"], cwd=worktree)
        sh(["git", "commit", "-m", message], cwd=worktree)


def push_and_pr(worktree: Path, branch: str, title: str, body: str) -> str:
    push = sh(["git", "push", "-u", "origin", branch], cwd=worktree)
    if push.returncode != 0:
        raise RuntimeError(f"push failed: {push.stderr.strip()}")
    pr = sh(["gh", "pr", "create", "--title", title, "--body", body], cwd=worktree)
    if pr.returncode != 0:
        raise RuntimeError(f"gh pr create failed: {pr.stderr.strip()}")
    return pr.stdout.strip()


# ---------------------------------------------------------------------------
# run flow
# ---------------------------------------------------------------------------


def _run(args) -> None:
    entries = load_queue(args.queue)
    entry, idx = pick_entry(entries, args.reason)
    if entry is None:
        print("[discovery] nothing to discover — queue is empty")
        return

    state = load_state(args.state)
    now_ts = time.time()
    last = state.get("last_attempt_at")
    if last is not None and (now_ts - last) < args.cooldown_hours * 3600:
        print("[discovery] cooldown active — skipping attempt")
        return

    bundle = build_bundle(entry, recent_races(args.healer_state), read_observations_tail())
    if args.dry_run:
        print(f"[discovery] dry-run: would attempt {bundle['rule']} ({bundle['reason']}) via {bundle['code_map']}")
        return

    repo_root = Path.cwd()
    date_str = datetime.now(timezone.utc).strftime("%Y-%m-%d-%H%M")
    branch = branch_name(entry["rule"], date_str)
    worktree, start_sha = worktree_add(repo_root, branch)
    outcome = "no-proposal"
    try:
        propose(worktree, build_prompt(bundle), args.max_claude_turns)
        if not has_changes(worktree, start_sha):
            print("[discovery] proposer made no changes — discarding attempt")
            cleanup(repo_root, worktree, branch)
        else:
            passed, report = run_gates(worktree, args.rom, args.eval_runs, args.race_turns)
            if passed:
                commit_if_needed(worktree, f"discovery: proposed fix for {entry['rule']}")
                pending = " [eval pending]" if args.eval_runs <= 0 else ""
                title = f"discovery: {entry['rule']} — unattended capability fix{pending}"
                body = (
                    f"Escalation: {entry.get('reason', 'manual')}\n\n"
                    f"Fitness that triggered it: `{json.dumps(entry.get('fitness', {}))}`\n\n"
                    f"## Gates (engine-run)\n\n```\n{report}\n```\n"
                )
                url = push_and_pr(worktree, branch, title, body)
                outcome = "pr-opened"
                print(f"[discovery] PR opened: {url}")
            else:
                outcome = "gates-failed"
                print(f"[discovery] gates failed — discarding attempt\n{report}")
                cleanup(repo_root, worktree, branch)
    finally:
        if idx is not None:
            mark_handled(args.queue, idx)  # one attempt per entry, whatever the outcome
        state.setdefault("attempts", []).append(
            {"at": now_ts, "rule": entry["rule"], "branch": branch, "outcome": outcome}
        )
        state["last_attempt_at"] = now_ts
        save_state(args.state, state)


def main() -> int:
    parser = argparse.ArgumentParser(description="Discovery engine — LLM capability healing")
    sub = parser.add_subparsers(dest="command", required=True)
    run = sub.add_parser("run", help="attempt one discovery from the escalation queue")
    run.add_argument("--rom", required=True, help="ROM path for fitness eval runs")
    run.add_argument("--queue", default="data/discovery_queue.json", help="escalation queue from the healer")
    run.add_argument("--reason", default=None, help="manual trigger: describe the problem instead of using the queue")
    run.add_argument("--eval-runs", type=int, default=3, help="fitness eval runs per side (0 skips, PR marked)")
    run.add_argument("--race-turns", type=int, default=800, help="turns per eval run")
    run.add_argument("--dry-run", action="store_true", help="print the plan without a worktree or LLM call")
    run.add_argument("--state", default="data/discovery_state.json", help="attempt history + cooldown")
    run.add_argument("--healer-state", default="data/healer_state.json", help="healer race history for context")
    run.add_argument("--max-claude-turns", type=int, default=40, help="proposer turn budget")
    run.add_argument("--cooldown-hours", type=float, default=DISCOVERY_COOLDOWN_HOURS)
    args = parser.parse_args()

    try:
        _run(args)
    except Exception as exc:  # discovery must never fail a wrapper or cron
        print(f"[discovery] discovery error: {exc}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
