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

Run this from the repo root. It creates the six demo worktrees, symlinks the
ROM, and seeds the states we have.

```
Set up the six demo worktrees for the talk.

For each demo in this list, create a detached git worktree under .worktrees/
based on the current HEAD, then seed it:

  1. demo-1-oak      — no state (fresh NEW GAME; failure/no-help beat)
  2. demo-2-npc      — no state (fresh NEW GAME; discovery/talk-to-NPCs beat)
  3. demo-3-starter  — state: at-oaks-lab.state IF it exists, else fresh NEW GAME
  4. demo-4-battle   — state: first_battle.state
  5. demo-5-flee     — state: route1.state
  6. demo-6-grind    — state: route1.state

Seeding steps for every worktree:
  - mkdir -p <wt>/rom and symlink the .gb from the main checkout's rom/ into it
  - mkdir -p <wt>/states and copy the needed .state from ../autotune/states/
    (first_battle.state, route1.state) — skip if the beat uses no state
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

Then drive the agent from Claude Code (prompt) or directly:

```bash
# Beats 1–2 (fresh NEW GAME):
uv run python scripts/agent.py rom/*.gb --strategy heuristic --live

# Beats 4–6 (pinned state):
uv run python scripts/agent.py rom/*.gb --strategy heuristic --live \
  --load-state states/route1.state
```

In the browser: reload the gallery, click the **live** tile to watch. Frames
render every `--frame-interval` turns (default 10) — a fast slideshow plus the
event feed, not 60fps video. For determinism keep `--strategy heuristic` (an
LLM strategy with temperature will drift run-to-run even from a fixed state).

## Per-beat worktree map

| # | Worktree | Start state | Strategy / flags | Demonstrates | Note it points at |
|---|----------|-------------|------------------|--------------|-------------------|
| 1 | `demo-1-oak` | fresh NEW GAME | no NPC help | failure / no-help | a failing `pokedex/log*.md` |
| 2 | `demo-2-npc` | fresh NEW GAME | discovery on | talk to NPCs | `observations.md`: "talk to NPCs" |
| 3 | `demo-3-starter` | at-lab* | — | door cooldown + B-not-A | observer state + `observations.md` |
| 4 | `demo-4-battle` | `first_battle.state` | — | battle knowledge | battle-mechanics observations |
| 5 | `demo-5-flee` | `route1.state` | flee wilds | traverse by fleeing | `observations.md`: "flee to progress" |
| 6 | `demo-6-grind` | `route1.state` | fight everything | level up, never flee | `observations.md`: when to fight |

`*` = state we don't have yet; see below.

## State inventory (what exists vs. what to capture)

Available today (in the sibling `../autotune/states/`, gitignored):

- `first_battle.state` — paused at the first wild battle → **Beat 4**
- `route1.state` — entering Route 1 → **Beats 5 & 6**

Missing — capture before the talk if you want those beats deterministic:

- **`at-oaks-lab.state`** for Beat 3 (choose starter). Capture it:
  ```bash
  uv run python scripts/agent.py rom/*.gb --strategy heuristic \
    --save-state-on-map "40:states/at-oaks-lab.state"   # map 40 = Oak's Lab
  ```
  Beats 1–2 intentionally use **no** state (the point is watching it fail, then
  discover).

## Cleanup

```bash
git worktree list                 # see all worktrees
git worktree remove .worktrees/demo-5-flee
git worktree prune                # tidy stale entries
```

Symlinked ROMs and copied states live only inside `.worktrees/` (gitignored),
so removing a worktree removes them with it. The real ROM and the
`../autotune/states/` originals are untouched.
