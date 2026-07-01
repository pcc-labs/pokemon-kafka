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
  Beats 4–6 and Beat 3 load a pinned savestate; Beats 1–2 start from NEW GAME.
- **Pacing (important).** Headless runs ~100× real-time, so a few hundred turns
  finish in *seconds*. That's why `--max-turns` below is set high — you want it
  still playing while you talk. The gallery keeps every finished run, so even
  after it ends you can click its tile to scrub frames and read the feed. Bump
  `--max-turns` further for a longer beat.
- **Prereq:** worktrees built per `worktree.md` (ROM symlinked, states seeded).

---

## Step 0 — Start the gallery + open Chrome (paste once, before the talk)

```
Start the Pokédex viewer from the repo root in the background pointed at ./runs
on port 8200 with --no-open, then open it in Google Chrome:

  uv run python -m viewer --runs-dir runs --no-open   # (run in background)
  open -a "Google Chrome" http://127.0.0.1:8200

Leave both running for the whole session.
```

Verified working (visually confirmed in Chrome): the viewer serves the game
frames (`image/png`) and the event feed (the on-screen `BATTLE`/`OVERWORLD`
logs) over HTTP. The viewer is the merged **Pokédex Viewer** (PR #29) — a FastAPI
server + `recorder.py`/`live_producer.py` capture pipeline. Design:
[`docs/specs/2026-06-26-pokedex-viewer-design.md`](specs/2026-06-26-pokedex-viewer-design.md).

---

## Beat 1 — Professor Oak's cabin, no help (the flail)

```
Run demo Beat 1 (the naive flail) live in the background from worktree
.worktrees/demo-1-oak. It should wander and never reach Oak's lab:

  cd .worktrees/demo-1-oak
  ROM="$(ls rom/*.gb | head -1)"
  DEMO_NAIVE=1 uv run python scripts/agent.py "$ROM" \
    --strategy low --live --frame-interval 3 --runs-dir ../../runs --max-turns 800 \
    --label "1 · Flail (no help)"

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
    --strategy low --live --frame-interval 3 --runs-dir ../../runs --max-turns 1500 \
    --label "2 · Talk to NPCs → lab"

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
    --strategy low --live --frame-interval 3 --runs-dir ../../runs \
    --load-state states/at-oaks-lab.state --max-turns 800 \
    --label "3 · Choose starter (B not A)"

Background it, run_id, remind me to click the live tile. Starts in Oak's lab
(party 0, verified) so it walks to the table and confirms the starter with B.
```

## Beat 4 — First battle (battle knowledge)

```
Run demo Beat 4 live in the background from worktree .worktrees/demo-4-battle,
loading the first-battle state:

  cd .worktrees/demo-4-battle
  ROM="$(ls rom/*.gb | head -1)"
  uv run python scripts/agent.py "$ROM" \
    --strategy low --live --frame-interval 3 --runs-dir ../../runs \
    --load-state states/first_battle.state --max-turns 600 \
    --label "4 · First battle"

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
    --strategy low --live --frame-interval 3 --runs-dir ../../runs \
    --load-state states/route1.state --max-turns 2000 \
    --label "5 · Route 1 by fleeing"

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
    --strategy low --live --frame-interval 3 --runs-dir ../../runs \
    --load-state states/route1.state --max-turns 3000 \
    --label "6 · Level up, never flee"

Background it, run_id, remind me to click the live tile. Point out `BATTLE ...
Action: fight` even at low HP — same start as Beat 5, opposite behavior. Let it
run for the level-ups.
```

---

## Beat 7 — Viridian Forest, mapping the maze (navigation is hard)

```
Run demo Beat 7 live in the background from worktree .worktrees/demo-7-forest.
The forest is a maze the agent only sees 9x10 tiles of — watch the WorldMap on
the right fill in as it explores:

  cd .worktrees/demo-7-forest
  ROM="$(ls rom/*.gb | head -1)"
  uv run python scripts/agent.py "$ROM" \
    --strategy low --live --frame-interval 20 --runs-dir ../../runs \
    --load-state states/viridian-forest.state \
    --worldmap-file states/forest.worldmap --max-turns 700 \
    --label "7 · Viridian Forest — mapping the maze"

Background it, run_id, remind me to click the live tile. Point out `STUCK`
events when it wedges and recovers, and that it maps the maze but doesn't walk
straight out — the exit is the frontier. Run it 2–3× keeping forest.worldmap to
show the learned map accumulate. Skill: /forest-navigation-demo
```

---

## Beat 8 — Bug hunt on Route 2 (battle intelligence)

```
Run demo Beat 8 live in the background from worktree .worktrees/demo-8-bughunt —
force-fight through Route 2's bug grass into the forest:

  cd .worktrees/demo-8-bughunt
  ROM="$(ls rom/*.gb | head -1)"
  AUTOTUNE_FORCE_FIGHT=1 uv run python scripts/agent.py "$ROM" \
    --strategy low --live --frame-interval 15 --runs-dir ../../runs \
    --load-state states/route2.state --max-turns 1300 --battle-limit 6 \
    --label "8 · Bug hunt — Route 2 into the Forest"

Background it, run_id, remind me to click the live tile. Point out the `MOVE`
lines — Tackle vs Weedle(bug), the KO, the level-up. It fights, it doesn't catch
(no catch mechanic). Skill: /bug-catcher-demo
```

---

## Beat 9 — Discovery engine, reading the world (signs + dialogue)

```
Run demo Beat 9 live in the background from worktree .worktrees/demo-9-signs —
a fresh NEW GAME so the dialogue-dense intro feeds the discovery stream:

  cd .worktrees/demo-9-signs
  rm -f rom/*.gb.ram
  ROM="$(ls rom/*.gb | head -1)"
  uv run python scripts/agent.py "$ROM" \
    --strategy low --live --frame-interval 30 --runs-dir ../../runs \
    --max-turns 2000 --label "9 · Discovery — reading the world"

Background it, run_id, remind me to click the live tile. Filter the feed to
`discovery` — each is a sign / NPC line / Pokedex blurb decoded off the tilemap
into a Kafka event. The agent reads pixels, not an API. Skill:
/discovery-signs-demo
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
