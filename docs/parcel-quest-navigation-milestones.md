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

## Where it stands

From a completely dead Route 1 grind loop, the agent now reliably: crosses Route 1, enters Viridian
City, navigates the city, and **steps into the Viridian Mart** (map `42`) heading for the clerk.
That is the furthest it has ever gotten, and the navigation is now general (whole-map pathfinding)
rather than a pile of per-barrier heuristics.

**Open blocker:** the parcel pickup itself. Entering the Mart triggers the clerk's scripted
sequence, during which the agent's directional inputs are ignored; completing that interaction
(and then the Oak delivery round-trip and the push past the now-cleared Old Man) is the remaining
work. The quest logic and coordinates for all of it are in place behind this one scripted
interaction.

## Method note

Several milestones were cracked by the same surgical loop: when the agent froze, dump the live
collision grid and a per-step trace (position, facing, chosen action, planner state) at the exact
stuck tile, read the discrepancy, fix the one cause, re-run. That loop turned "it's stuck
somewhere in Route 1" into "the gap is one tile at odd-x=9 and we only land on even columns" — a
fix instead of a guess.
