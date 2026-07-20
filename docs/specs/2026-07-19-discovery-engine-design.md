# Discovery Engine: LLM Capability Healing (Loop 3)

**Date:** 2026-07-19
**Status:** Approved (autonomy/proposer/trigger decisions made by bdougie)

## Goal

Heal what parameter tuning cannot. When the healer's races stop working —
the same rule re-fires after an accepted fix, or races keep rejecting —
the problem is a missing capability, not a bad knob. The discovery engine
hands the evidence to Claude Code headless, which proposes a **code
change** on a branch; the engine (not the proposer) runs the gates and
opens a PR with the proof. A human merges.

## Decisions (locked)

- **Autonomy:** branch + PR. Diagnosis, patch, and evidence are unattended;
  merge is human. Never auto-merge.
- **Proposer:** Claude Code headless (`claude -p` in an isolated git
  worktree) — repo-aware, edits files, runs focused tests itself. Matches
  the `run_10_agents` precedent of driving claude subprocesses.
- **Trigger:** healer escalation plus a manual `run` for demos. Every
  anomaly goes to the free parameter race first; only exhausted tuning
  escalates to LLM spend.

## Escalation (change to `scripts/healer.py`)

After recording a race, the healer evaluates `should_escalate(state, rule)`:

- **refire-after-accept:** this rule's most recent race was accepted, and
  the rule fired again on a later run — the tuned fix didn't hold.
- **rejects-exhausted:** the last `ESCALATE_AFTER_REJECTS = 2` races for
  this rule were all rejected — the knobs have nothing left.

On escalation, append an entry to `data/discovery_queue.json`:
`{"at": ts, "rule": name, "reason": "refire-after-accept" | "rejects-exhausted",
"fitness": {...}, "handled": false}`. The healer's job ends there — it
never invokes the LLM itself (`check` stays fast and free).

## Component: `scripts/discovery.py`

```
uv run scripts/discovery.py run --rom ROM
    [--queue data/discovery_queue.json] [--reason TEXT]  # manual trigger
    [--eval-runs 3] [--race-turns 800] [--dry-run]
    [--state data/discovery_state.json] [--max-claude-turns 40]
```

`run` flow (always exits 0; every failure path logged, never raised):

1. **Pick work:** oldest unhandled queue entry, or a synthetic entry from
   `--reason` (manual/demo path). None → "nothing to discover".
2. **Cooldown:** `DISCOVERY_COOLDOWN_HOURS = 24` via `discovery_state.json`
   (records every attempt: entry, branch, gates, outcome). One attempt per
   escalation entry — entries are marked handled whether or not gates pass.
3. **Context bundle** (pure function): the entry, last N healer races,
   tail of `pokedex/memory/observations.md`, and the rule→code map
   (navigation rules → `scripts/pathfinding.py`, `scripts/world_map.py`,
   `scripts/agent.py`). Rendered into the proposer prompt with hard
   constraints: minimal change, no test deletions/weakening, must explain
   the diagnosis in the commit message.
4. **Worktree:** `git worktree add .discovery/<branch> -b
   discovery/<rule>-<UTC date>` — the main checkout is never touched.
5. **Propose:** `claude -p <prompt> --permission-mode acceptEdits
   --max-turns <max-claude-turns>` with `cwd=worktree`. If the worktree has
   no diff afterwards → record "no proposal", clean up, done.
6. **Gates (engine-run, proposer output is never trusted):**
   - `uv run pytest -q` full suite in the worktree (coverage gate included).
   - `uv run ruff check .` in the worktree.
   - **Fitness eval:** `--eval-runs` runs of the *worktree's* agent vs the
     same number of baseline runs from the main checkout (same seed'd
     conditions, `evolve.score` each; candidate mean must beat baseline
     mean by `MARGIN = 0.05` abs-margin, same formula as the healer).
     Requires a ROM; `--eval-runs 0` skips the gate and the PR is titled
     with `[eval pending]` so the gap is visible, never silent.
7. **Ship or discard:** gates pass → commit (if the proposer didn't), push,
   `gh pr create` with the diagnosis, diff stat, test output tail, and the
   fitness table as the body. Any gate fails → record the failure in state,
   remove the worktree and branch, leave nothing behind.

## Guardrails

- One attempt per queue entry; 24h cooldown between attempts; both in
  `discovery_state.json`.
- `--max-claude-turns` bounds proposer spend per attempt.
- The proposer runs in a worktree with `acceptEdits` (file edits only, no
  arbitrary approval); gates and git/gh operations are the engine's code.
- `run` exits 0 always — cron/wrapper safe.

## Testing

`tests/test_discovery.py` with every subprocess boundary patched
(`claude`, `git`, `gh`, pytest/ruff gates, agent eval runs):
escalation conditions in the healer (refire-after-accept, rejects-exhausted,
boundaries); queue append/pop/handled marking; context bundle and prompt
rendering (constraints present); cooldown; gate decision math (reuses
healer's abs-margin `decide`); eval-runs 0 → `[eval pending]` title; no-diff
→ no PR; gate failure → cleanup called, state recorded; exit 0 on every
failure. `scripts/` is under the 100% coverage gate.

## Out of scope

- Auto-merge (never).
- Multiple proposals per escalation, proposal retries, or model selection.
- Mid-run healing; parameter tuning (that's the healer's job).
- Prompt-tuning the proposer beyond the v1 template.
