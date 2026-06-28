# Oak's Parcel quest: navigation milestones

A record of the milestones overcome getting the autonomous agent from "stuck at the Route 1
entrance forever" to "navigating Route 1, Viridian City, and into the Viridian Mart" while running
the Oak's Parcel quest. Each milestone was a real, separable fix, validated by a live run.

Branch: `feat/parcel-quest`. Sibling context: the autotune sweep that motivated this work is in
[pcc-labs/autotune#4](https://github.com/pcc-labs/autotune/issues/4).

## Why this work exists

A 10-agent autotune sweep over the navigation/battle genome proved the agent's road to Brock is
blocked by **game-progression context the agent doesn't model**, not by any tunable `EVOLVE_PARAMS`
value: every config flat-lined in the same spot. Pokemon Red hard-gates the north exit of Viridian
City behind the **Oak's Parcel errand** (collect it at the Viridian Mart, deliver it to Prof. Oak,
receive the Pokedex). No parameter tuning passes a scripted gate, so the agent needed to actually
run the errand. That meant teaching it to navigate and interact, which is where the milestones
below came from.

## Milestone 1: the collision grid was dead, so A* never ran

`pyboy.game_wrapper` is a **property** in the installed PyBoy, but `CollisionMap.update` called it
as a method (`game_wrapper()`), raising `TypeError` that the agent swallowed in a bare `except`.
The walkability grid therefore stayed all-walls, A* pathfinding never executed, and the agent
navigated blind with greedy axis-stepping that jammed on any obstacle. This single bug crippled
*all* navigation and explains the original universal wall. Fixed to support both PyBoy API shapes.

## Milestone 2: the agent walked two tiles per step

`GameController.move` held the d-pad for 20 frames, which walks **two tiles** per call, so the
agent could only ever land on same-parity columns. Route 1's one-tile-wide gap through the
mid-route tree line sits at an odd x; the agent stepped over it on every pass and oscillated
forever. Cutting the hold to 8 frames commits exactly one tile, and the agent crossed Route 1 into
Viridian City for the first time. (Discovered surgically: a per-step trace showed the agent only
ever visiting even columns.)

## Milestone 3: the backtrack/stall machinery fought the working pathfinder

The agent's restore-on-stall and 500-turn forced-backtrack were crutches tuned around the broken
collision grid. With A* working they teleported the agent backward into Pallet and Red's house.
They are now suppressed while the parcel quest is steering, so forward progress isn't undone.

## Milestone 4: the quest itself (state machine + real coordinates)

`parcel_quest.py` is a pure state machine `TO_MART -> TO_OAK -> GO_NORTH -> DONE`, driven by bag /
Pokedex / map signals read from RAM (`has_parcel` = bag holds item `0x46`, `has_pokedex` = wd74b
bit 5, plus player facing at `0xC109`). Every tile coordinate it targets (Mart door `29,19`; clerk
`0,5` behind the counter; Oak `5,2`; Oak's Lab door `12,11`; the Old Man at `17,5`; Route 1's
north exit) comes from the **pret/pokered disassembly**, not guesswork.

## Milestone 5: un-blinding the agent with a WorldMap

The agent only ever saw a 9x10 collision window and forgot it the instant it moved, so off-screen
gaps (Viridian's north corridor vs the Mart's column) were unfindable by local heuristics. The new
`WorldMap` stamps each turn's collision window into a persistent per-map occupancy grid using the
player's absolute coordinates, then pathfinds (A*) over the whole accumulated map. Unknown tiles
are treated as optimistically walkable, so the search is drawn toward the goal and self-corrects as
walls are observed. This replaced a stack of hand-tuned pilot heuristics (ledge-escape,
wall-follow, drift) with one principled planner. It is not pixel vision: it reuses the exact
walkability the game already exposes, just remembered.

## Milestone 6: crossing map boundaries (`cross_step`)

Heading to a map's edge by targeting `(x, 0)` fails once the columns straight ahead are blocked:
the planner gives up and drifts back. `cross_step` instead advances toward the edge whenever the
tile ahead is enterable, and otherwise BFS-sweeps along the boundary to the nearest column where
forward is still open, learning the non-exit tiles as it goes. This carried the agent off Route 1
through its real exit and up to the Viridian Mart door.

## Milestone 7: learning impassable tiles from failed moves (carefully)

The collision grid marks some unenterable tiles as walkable: map-edge non-exits, ledges (walkable
to stand on, only a downhill hop), NPCs. When a move doesn't change position, the agent now
hard-blocks the attempted tile so A* reroutes, and `observe` never overwrites that block. Two
guards keep this from poisoning the map:

- **Two consecutive failures into the same tile** are required (the first press in a new direction
  only turns the character without moving, so one failure isn't proof of a wall).
- **Facing must have turned to the pressed direction.** A real wall lets the turn happen; a
  cutscene or warp-settle state that ignores inputs leaves facing unchanged. Only the former counts,
  so the Mart clerk's parcel script never traps the agent by making it block walkable tiles.

## Milestone 8: running the Mart parcel cutscene

Per pret's `ViridianMartDefaultScript`, entering the Mart shows the clerk's text, then **simulates
the joypad** to auto-walk the player to the counter and hands over OAK'S PARCEL. The agent's own
directional inputs fight that simulated movement, so the fix is to recognise the Mart and simply
advance text / wait until the parcel is in the bag. With that, the clerk's script runs and the
parcel drops — the quest flips to `TO_OAK`.

## Milestone 9: NPC interaction and building warps

Two interaction details made the round-trip work:

- **Talk-then-face.** The clerk sits behind a counter (the counter tile itself is a wall); the
  player stands one tile out and faces *into* it. On arrival at a door/NPC the agent now alternates
  the facing press and A, so it turns to face the clerk / Oak before interacting.
- **Doormat exits.** You leave a building by walking *down* off its doormat warp, not by sitting on
  the tile. Building exits target the warp tile and press down to step out.

## Milestone 10: the full quest completes, then the corridor to Pewter

With the above, the agent runs the **entire errand end to end**: gets the parcel, carries it back
south to Pallet, **delivers it to Oak and receives the Pokédex**, then heads north — and the Old
Man has stepped aside. It crosses into **Route 2**, walks **through the gate building**, and into
**Viridian Forest** — past the scripted gate that flat-lined the original autotune sweep, run the
way the game intends. Post-Pokédex navigation simply pilots north through every map (routes, gate
buildings, the forest) until Pewter, where it hands back to normal navigation for the gym.

## Where it stands (handoff)

From a completely dead Route 1 grind loop, the agent now **completes the Oak's Parcel quest** end
to end — parcel pickup, delivery, Pokédex, the Old Man gate cleared — and climbs the corridor north
through Route 2, its gate building, and **into Viridian Forest (map 51)**, all on general whole-map
pathfinding (the WorldMap pilot) rather than per-barrier heuristics. Verified live in run 18.

### Verified map sequence (run 18)

`12 (Route 1) → 1 (Viridian) → 42 (Mart, parcel) → 1 → 12 → 0 (Pallet) → 40 (Oak's Lab, Pokédex) →
0 → 12 → 1 → 13 (Route 2) → 50 (Route 2 gate) → 51 (Viridian Forest)`.

### Open blocker: surviving Viridian Forest

The agent reaches the Forest but **gets stuck in wild battles** (e.g. frozen on a Lv5 Weedle at
1/26 HP, action `fight`, neither winning nor fleeing nor fainting). Root cause is in
`BattleStrategy.choose_action` (scripts/agent.py): in a wild battle it only attempts `run` while
`hp_ratio < hp_run_threshold` **and** `_run_attempts < 3`; after three failed runs it falls through
to `fight` at critical HP, with no potions in the bag to heal. The Forest's dense encounters then
trap a low-HP starter. This is a battle-survival problem, separate from navigation.

Likely fixes to try next:
- Reset `_run_attempts` per battle (it may persist across battles, capping flees too early), and/or
  raise the flee threshold so the agent flees Forest wild battles instead of grinding them at 1 HP.
- Or grind/heal earlier (level the starter on Route 1 / Forest edge before pushing through), or buy
  Potions at the Viridian Mart on the way (the agent now reaches that Mart).
- Investigate why the specific battle doesn't terminate (move selection / PP / menu), since it is
  stuck rather than losing.

### Capturing the pre-Brock state (the goal once the Forest is crossed)

Runs already arm `--save-state-on-trainer "brock:<autotune>/states/pre_brock.state"`, which dumps a
save state the instant Brock's fight begins (the first gym-leader-level trainer) — that is the
pre-Brock state autotune's `--mode brock` loop optimises against. For the **level lever** in the
brock loop, also capture an *overworld* pre-gym state with `--save-state-on-map
"<pewter-gym-map-id>:..."` (the Pewter Gym is its own map id, discovered when the agent first enters
it; see `autotune/docs/brock-loop.md`). Neither fires yet because the agent has not reached Pewter.

### How to run

```
cd pokemon-kafka
ROM="<autotune>/rom/Pokemon - Red Version (USA, Europe) (SGB Enhanced).gb"
uv run python -u scripts/agent.py "$ROM" --strategy medium \
  --load-state "<autotune>/states/route1.state" --max-turns 40000 \
  --telemetry-dir data/run/telemetry --output-json data/run/fitness.json --config "" \
  --save-state-on-trainer "brock:<autotune>/states/pre_brock.state"
```

`route1.state` is a post-starter Route 1 save, the fast iteration seed. Watch progress via the
`QUEST |` and `MAP CHANGE |` log lines. All work is on branch `feat/parcel-quest` (tests green:
`uv run pytest -q`; `uv run ruff check`).

## Method note

Several milestones were cracked by the same surgical loop: when the agent froze, dump the live
collision grid and a per-step trace (position, facing, chosen action, planner state) at the exact
stuck tile, read the discrepancy, fix the one cause, re-run. That loop turned "it's stuck
somewhere in Route 1" into "the gap is one tile at odd-x=9 and we only land on even columns" — a
fix instead of a guess.
