# Demo Prompts (copy-paste into Claude Code)

Each beat is a block you paste into Claude Code. Claude runs the agent **live in
the pinned worktree**, streaming to the viewer at <http://localhost:8200> while
you talk through the logs. Pairs with [`talk-demo-outline.md`](talk-demo-outline.md)
(narrative) and [`worktree.md`](worktree.md) (how the worktrees are built).

## How it runs (read once)

- **One gallery for all beats.** Every beat writes to the *same* `runs/` dir
  (`--runs-dir ../../runs` from inside a worktree resolves to the main checkout's
  `runs/`), so you start the viewer **once** and just click the new **live** tile
  each beat. No restarting the viewer between beats.
- **Background, not blocking.** Each beat launches the agent in the background so
  Claude stays responsive and the browser streams while you talk. The viewer's
  event feed *is* the logs on screen (game on one side, `BATTLE`/`OVERWORLD`
  lines on the other). Ask Claude to "tail the log" any time you want the raw
  terminal lines too.
- **Deterministic.** `--strategy low` = no LLM, same behavior every rehearsal.
  Beats 4–6 load a pinned savestate; Beats 1–3 start from NEW GAME.
- **Prereq:** worktrees built per `worktree.md` (ROM symlinked, states seeded).

---

## Step 0 — Start the gallery (paste once, before the talk)

```
Start the Pokédex viewer from the repo root pointed at ./runs on port 8200 and
open http://localhost:8200 in my browser. Run it in the background and leave it
running for the whole session.
```

---

## Beat 1 — Professor Oak's cabin, no help (the flail)

```
Run demo Beat 1 (the naive flail) live in the background from worktree
.worktrees/demo-1-oak. It should wander and never reach Oak's lab:

  cd .worktrees/demo-1-oak
  ROM="$(ls rom/*.gb | head -1)"
  DEMO_NAIVE=1 uv run python scripts/agent.py "$ROM" \
    --strategy low --live --runs-dir ../../runs --max-turns 150

Run it in the background, give me the run_id, and remind me to click the live
tile. This one is SUPPOSED to fail — it stays stuck near the bedroom (map 38),
no Pokémon.
```

## Beat 2 — Discovery engine: talk to NPCs (reaches the lab)

```
Run demo Beat 2 live in the background from worktree .worktrees/demo-2-npc —
same fresh game as Beat 1 but WITHOUT naive mode, so the learned routing kicks in
and it reaches Oak's lab:

  cd .worktrees/demo-2-npc
  ROM="$(ls rom/*.gb | head -1)"
  uv run python scripts/agent.py "$ROM" \
    --strategy low --live --runs-dir ../../runs --max-turns 500

Background it, give me the run_id, remind me to click the live tile. Point out
it now reaches Oak's lab (map 40) and picks a starter (party 1).
```

## Beat 3 — Choose your Pokémon (door cooldown + B, not A)

```
Run demo Beat 3 live in the background from worktree .worktrees/demo-3-starter —
watch it reach the Pokéball table, handle the door cooldown, and confirm the
starter with B (not A):

  cd .worktrees/demo-3-starter
  ROM="$(ls rom/*.gb | head -1)"
  uv run python scripts/agent.py "$ROM" \
    --strategy low --live --runs-dir ../../runs --max-turns 500

Background it, run_id, remind me to click the live tile. (If states/at-oaks-lab.state
exists, add --load-state states/at-oaks-lab.state to skip straight to the lab.)
```

## Beat 4 — First battle (battle knowledge)

```
Run demo Beat 4 live in the background from worktree .worktrees/demo-4-battle,
loading the first-battle state:

  cd .worktrees/demo-4-battle
  ROM="$(ls rom/*.gb | head -1)"
  uv run python scripts/agent.py "$ROM" \
    --strategy low --live --runs-dir ../../runs \
    --load-state states/first_battle.state --max-turns 150

Background it, run_id, remind me to click the live tile. Point out it reads the
matchup and picks an effective move.
```

## Beat 5 — Complete Route 1 by fleeing

```
Run demo Beat 5 live in the background from worktree .worktrees/demo-5-flee,
from the Route 1 state, with a high flee threshold so it RUNS from wild battles:

  cd .worktrees/demo-5-flee
  ROM="$(ls rom/*.gb | head -1)"
  EVOLVE_PARAMS='{"hp_run_threshold":0.95}' uv run python scripts/agent.py "$ROM" \
    --strategy low --live --runs-dir ../../runs \
    --load-state states/route1.state --max-turns 400

Background it, run_id, remind me to click the live tile. Point out the feed:
`BATTLE ... Action: run` — it declines fights to traverse the route.
```

## Beat 6 — Level up and never flee (the reversal)

```
Run demo Beat 6 live in the background from worktree .worktrees/demo-6-grind —
the SAME Route 1 state as Beat 5, but force-fight so it never runs and levels up:

  cd .worktrees/demo-6-grind
  ROM="$(ls rom/*.gb | head -1)"
  AUTOTUNE_FORCE_FIGHT=1 uv run python scripts/agent.py "$ROM" \
    --strategy low --live --runs-dir ../../runs \
    --load-state states/route1.state --max-turns 800

Background it, run_id, remind me to click the live tile. Point out `BATTLE ...
Action: fight` even at low HP — same start as Beat 5, opposite behavior. Let it
run for the level-ups.
```

---

## Between beats / reset

```
Stop the currently running demo agent (leave the viewer up).
```

The gallery keeps every run as a tile, so you can scroll back to a prior beat.
To wipe the gallery between rehearsals:

```
Clear the ./runs directory so the gallery starts empty, but keep the viewer
running.
```

## The through-line to say out loud

Every beat's fix is a line the agent wrote in `pokedex/memory/observations.md`
or a `pokedex/log*.md`. Beat 1 fails *and records that it failed*; Beats 2–6 are
those records paying off. The notes are the product.
