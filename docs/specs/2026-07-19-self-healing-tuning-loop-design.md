# Self-Healing Tuning Loop: Anomaly-Triggered Parameter Races

**Date:** 2026-07-19
**Status:** Draft

## Goal

Close the between-run healing loop with zero human input: a bad run's own
fitness triggers a parameter race on the implicated knobs, and the winning
genome is persisted so the next run behaves differently. Today every
component exists — `evolve.run_agent` subprocess races, `score()`,
`PARAM_BOUNDS`/`clamp_params`, and `autotune_bridge.load_genome_from_notes`
feeding `EVOLVE_PARAMS` at agent startup — but a human decides when to race.
This spec adds the trigger.

## Non-goals (the other two loops)

- **In-run reflex** (agent reacting to Flink alerts mid-run): the agent's
  local stuck detection already covers most of it; revisit only if Flink
  surfaces patterns local heuristics miss.
- **LLM capability healing** (code changes proposed from observations):
  separate effort; this loop only tunes existing heuristics.

## Component: `scripts/healer.py` (host-side, broker-free)

Runs on the host because races launch PyBoy subprocesses — the docker
consumers can't. Reads **fitness JSON** (the agent's `--output-json`
output), not Kafka: the loop works with or without the compose stack up.

### CLI

```
uv run scripts/healer.py check --fitness FILE --rom ROM
    [--race-turns 800] [--variants 6] [--seed N] [--dry-run]
    [--notes notes.md] [--state data/healer_state.json]
```

- `check` evaluates the trigger rules against the fitness JSON; when a rule
  fires (and no guardrail blocks), it runs the race and possibly writes the
  genome. Exit 0 always (a healing failure must never fail a run wrapper);
  prints one summary line per decision.
- `--dry-run` stops after printing the decision (rule fired? which params?
  guardrail state?) without racing.
- Chained invocation is the intended wrapper:
  `agent.py ROM --output-json f.json ... && healer.py check --fitness f.json --rom ROM`

### Trigger rules (v1, extensible table)

| Rule | Condition (fitness JSON) | Implicated parameters |
|---|---|---|
| navigation-thrash | `stuck_count >= 12` or `backtrack_restores >= 3` | `door_cooldown`, `stuck_threshold`, `waypoint_skip_distance`, `bt_restore_threshold` |
| no-progress | `maps_visited <= 1` and `turns >= 500` | `door_cooldown`, `stuck_threshold`, `bt_snapshot_interval` |

Rules are data (`RULES` list of dicts) so adding battle rules later is a
table edit, not new machinery. Thresholds are constants at the top of the
file, overridable by CLI flags of the same name.

### Race

- Baseline genome = `DEFAULT_PARAMS` overlaid with
  `load_genome_from_notes(notes.md)`.
- Variants: `--variants` genomes sampled with a **seeded** `random.Random`
  (`--seed`; default derived from the fitness file's content hash so reruns
  are reproducible) — each variant perturbs only the rule's implicated
  parameters, uniform within `PARAM_BOUNDS`, then `clamp_params`.
- The baseline itself always races as the control.
- Each candidate runs via `evolve.run_agent(rom, race_turns, params)`
  (existing subprocess isolation); scored with `evolve.score`.

### Acceptance guardrails

- **Win margin:** persist the winner only if
  `score(winner) > score(control) * (1 + MARGIN)` with `MARGIN = 0.05`.
  Ties and losses keep the current genome (no churn from noise).
- **Cooldown:** `healer_state.json` records
  `{last_race_at, races: [{at, rule, accepted, genome}]}`. No new race
  within `COOLDOWN_HOURS = 6` of the last one (timestamp comes from the
  fitness file's `occurred_at`-style field if present, else file mtime —
  healer logic itself never calls `datetime.now()` directly except in
  `main()`, keeping the core pure/testable).
- **Persistence:** on acceptance, append a fresh
  `<!-- autotune:genome {...} -->` block to notes.md (last block wins per
  `autotune_bridge._GENOME_BLOCK_RE`) plus a human-readable line above it:
  `Healer: navigation-thrash on 2026-07-19 — door_cooldown 8→4 (score 3120→3510)`.
  The next agent run picks it up automatically (existing L2 baseline path).

## Structure (all pure functions except `main`)

- `evaluate_rules(fitness: dict) -> list[Rule]`
- `sample_variants(base: dict, params: list[str], n: int, rng) -> list[dict]`
- `decide(control_score, winner_score, margin) -> bool`
- `cooldown_active(state: dict, now_ts: float, hours: float) -> bool`
- `append_genome(notes_path, genome, reason_line) -> None`
- `run_race(rom, turns, candidates) -> list[(params, fitness, score)]`
  (thin loop over `evolve.run_agent`; the only impure racing code)
- `main()` — argparse + wiring.

## Testing

`tests/test_healer.py`, all with `evolve.run_agent` patched (no emulation):
rule evaluation on boundary values; variant sampling respects bounds and
only perturbs implicated params (seeded, deterministic); control always
included; acceptance margin math incl. tie/loss; cooldown honored and
expired; genome block appended in `autotune_bridge`-parseable form and
`load_genome_from_notes` round-trips it; `--dry-run` races nothing; exit
code 0 on malformed fitness JSON (logged, no crash). `scripts/` is under
the 100% coverage gate — healer must be fully covered.

## Docs

- README: short "Self-healing loop" subsection under the evolution section —
  the wrapper one-liner and what triggers a race (kept small; README is
  also being edited on two open branches).
- `docs/talk-demo-outline.md` Act 3 gains its money beat: run with a bad
  `door_cooldown`, watch the healer race and fix it, next run improves —
  fully unattended.

## Out of scope

- Kafka/Flink-driven triggering (fitness-file-driven is broker-free; an
  `--observations` input counting Flink alert lines is a listed v2 idea).
- LLM-proposed mutations (`evolve.py`'s LLM path stays manual).
- Mid-run healing, code-change healing.
- Daemon mode; the healer is invoke-after-run only.
