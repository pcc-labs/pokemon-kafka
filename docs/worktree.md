# Demo Worktrees

How to run each talk demo in an **isolated git worktree** with a **pinned game
state**, so every beat is deterministic and reproducible on any machine. Pairs
with [`talk-demo-outline.md`](talk-demo-outline.md).

## Why worktrees

- **Isolation** — each demo has its own working copy; a crash or stray file in
  one doesn't touch the others or your main checkout.
- **Determinism** — each demo loads a fixed savestate, so the agent starts from
  the same place every rehearsal.
- **Reproducibility** — anyone can recreate the exact setup from the prompt
  below.

## What a worktree does *not* inherit (important)

Git worktrees only check out **tracked** files. These are all gitignored, so a
fresh worktree starts **without** them — the setup must seed them:

| Thing | Gitignored? | How the worktree gets it |
|---|---|---|
| ROM (`rom/*.gb`) | yes (legal — not distributable) | **symlink** from your main checkout |
| Save states (`*.state`) | yes | **copy** from `../autotune/states/` |
| `runs/`, `frames/` | yes | created fresh at runtime (good — clean slate) |
| `pokedex/memory/` | yes | written fresh by the agent as it plays |
| `notes.md` | yes | per-run working memory, created at runtime |

**You must supply your own legally-obtained Pokémon Red ROM.** Nothing here
distributes it.

## The setup prompt (copy-paste into Claude Code)

Run this from the repo root. It creates the nine demo worktrees, symlinks the
ROM, and seeds the states we have.

```
Set up the nine demo worktrees for the talk.

For each demo in this list, create a detached git worktree under .worktrees/
based on the current HEAD, then seed it:

  1. demo-1-oak      — no state (fresh NEW GAME; failure/no-help beat)
  2. demo-2-npc      — no state (fresh NEW GAME; discovery/talk-to-NPCs beat)
  3. demo-3-starter  — state: at-oaks-lab.state IF it exists, else fresh NEW GAME
  4. demo-4-battle   — state: first_battle.state
  5. demo-5-flee     — state: route1.state
  6. demo-6-grind    — state: route1.state
  7. demo-7-forest   — state: viridian-forest.state (map 51)
  8. demo-8-bughunt  — state: route2.state (map 13)
  9. demo-9-signs    — no state (fresh NEW GAME; discovery/reading-signs beat)

Seeding steps for every worktree:
  - mkdir -p <wt>/rom and symlink the .gb from the main checkout's rom/ into it
  - mkdir -p <wt>/states and copy the needed .state from demo-runs/states/
    (first_battle.state, route1.state from ../autotune/states/;
    viridian-forest.state, route2.state captured per "State inventory" below)
    — skip if the beat uses no state
  - do NOT copy the ROM (symlink only), and never commit ROMs or states

Then print a table: worktree path, seeded state (or "fresh"), and the exact
`uv run` command to launch that demo with --live.
```

## Running a demo (what each worktree launches)

Start the viewer once, pointed at the worktree you're demoing:

```bash
cd .worktrees/demo-5-flee
uv run python -m viewer --runs-dir runs        # http://localhost:8200
```

Then drive the agent from Claude Code (prompt) or directly. **Use
`--strategy low`** (verified: no LLM, deterministic; `medium`/`high` call the
LLM). The ROM filename has spaces, so capture it in a var:

```bash
ROM="$(ls rom/*.gb | head -1)"

# Beat 1 (the flail) — naive mode: strip learned scaffolding, wander:
DEMO_NAIVE=1 uv run python scripts/agent.py "$ROM" --strategy low --live

# Beats 2–3 (fresh NEW GAME, learned routing on):
uv run python scripts/agent.py "$ROM" --strategy low --live

# Beat 4 (first battle):
uv run python scripts/agent.py "$ROM" --strategy low --live \
  --load-state states/first_battle.state

# Beat 5 (flee to traverse) — high flee threshold:
EVOLVE_PARAMS='{"hp_run_threshold":0.95}' \
  uv run python scripts/agent.py "$ROM" --strategy low --live \
  --load-state states/route1.state

# Beat 6 (never flee, grind/level) — SAME state, force fight:
AUTOTUNE_FORCE_FIGHT=1 \
  uv run python scripts/agent.py "$ROM" --strategy low --live \
  --load-state states/route1.state
```

In the browser: reload the gallery, click the **live** tile to watch. Frames
render every `--frame-interval` turns (default 10) — a fast slideshow plus the
event feed, not 60fps video. Keep `--strategy low` for determinism. Beats 5 and
6 diverge *only* by the env var; let Beat 6 run ~500 turns for the level-ups to
show (a 60-turn run looks the same as flee).

## Per-beat worktree map

| # | Worktree | Start state | Strategy / flags | Demonstrates | Note it points at |
|---|----------|-------------|------------------|--------------|-------------------|
| 1 | `demo-1-oak` | fresh NEW GAME | `DEMO_NAIVE=1` | failure / no-help (live flail) | a failing `pokedex/log*.md` |
| 2 | `demo-2-npc` | fresh NEW GAME | `--strategy low` | talk to NPCs | `observations.md`: "talk to NPCs" |
| 3 | `demo-3-starter` | `at-oaks-lab.state` | `--strategy low` | door cooldown + B-not-A | observer state + `observations.md` |
| 4 | `demo-4-battle` | `first_battle.state` | `--strategy low` | battle knowledge | battle-mechanics observations |
| 5 | `demo-5-flee` | `route1.state` | `EVOLVE_PARAMS='{"hp_run_threshold":0.95}'` | traverse by fleeing (`Action: run`) | `observations.md`: "flee to progress" |
| 6 | `demo-6-grind` | `route1.state` | `AUTOTUNE_FORCE_FIGHT=1` | level up, never flee (`Action: fight`) | `observations.md`: when to fight |
| 7 | `demo-7-forest` | `viridian-forest.state` | `--worldmap-file states/forest.worldmap` | map the maze under 9x10 visibility (`STUCK` + recover) | the WorldMap accumulating |
| 8 | `demo-8-bughunt` | `route2.state` | `AUTOTUNE_FORCE_FIGHT=1` | type-effective bug battles (`MOVE ... vs Weedle`) | `type_chart.json` in action |
| 9 | `demo-9-signs` | fresh NEW GAME | `--strategy low` | decode signs/dialogue into `discovery` events | the discovery feed |

Beats 7–9 are the "harder frontier" set: navigation as a real maze, battle
intelligence against bugs, and the read-the-world discovery engine. See the
`/forest-navigation-demo`, `/bug-catcher-demo`, and `/discovery-signs-demo`
skills for the full per-beat walkthroughs. Note beat 7 maps the forest but does
**not** cleanly exit to Pewter in one run — that is the honest point.

**Beat 1 (verified by smoke test):** with the learned scaffolding on, the agent
reaches Oak's lab and picks a starter even with "no help" — so the flail needs
`DEMO_NAIVE=1`, which strips the scripted targets + route waypoints. With it, the
agent stays stuck near the bedroom (map 38, party 0) — a real, deterministic
live flail. All six worktrees boot, ROM symlinks resolve, states load, and each
writes its own `pokedex/log*.md` (isolation confirmed).

## State inventory (what exists vs. what to capture)

Available today:

- `first_battle.state` (from `../autotune/states/`) — first wild battle → **Beat 4**
- `route1.state` (from `../autotune/states/`) — entering Route 1 → **Beats 5 & 6**
- `at-oaks-lab.state` — **captured**, in `.worktrees/demo-3-starter/states/`.
  Verified map 40, party 0 (pre-starter) → **Beat 3**.

To re-capture `at-oaks-lab.state` (e.g. after recreating the worktree):
```bash
uv run python scripts/agent.py "$(ls rom/*.gb|head -1)" --strategy low --max-turns 260 \
  --save-state-on-map "40:states/at-oaks-lab.state"   # map 40 = Oak's Lab
```

Beats 1–2 intentionally use **no** state (Beat 1 flails via `DEMO_NAIVE=1`;
Beat 2 discovers from a fresh game). Beat 9 also uses **no** state (fresh NEW
GAME feeds the discovery stream from the dialogue-dense intro).

Captured for beats 7–8 (stored under gitignored `demo-runs/states/`, recapture
by running forward from `route1.state`):

- `viridian-forest.state` (map 51, entering the Forest) → **Beat 7**
  ```bash
  uv run python scripts/agent.py "$(ls rom/*.gb|head -1)" --strategy low --max-turns 1400 \
    --load-state demo-runs/states/route1.state \
    --save-state-on-map "51:demo-runs/states/viridian-forest.state"
  ```
- `route2.state` (map 13, entering Route 2) → **Beat 8**
  ```bash
  uv run python scripts/agent.py "$(ls rom/*.gb|head -1)" --strategy low --max-turns 600 \
    --load-state demo-runs/states/route1.state \
    --save-state-on-map "13:demo-runs/states/route2.state"
  ```

## Cleanup

```bash
git worktree list                 # see all worktrees
git worktree remove .worktrees/demo-5-flee
git worktree prune                # tidy stale entries
```

Symlinked ROMs and copied states live only inside `.worktrees/` (gitignored),
so removing a worktree removes them with it. The real ROM and the
`../autotune/states/` originals are untouched.
