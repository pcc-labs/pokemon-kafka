---
name: pokemon-agent
description: "Play turn-based RPGs autonomously via Game Boy emulation. Use when the user asks to 'play pokemon', 'emulate a game boy game', 'automate pokemon battles', 'grind pokemon', 'run a pokemon nuzlocke', 'play an RPG for me', or mentions headless emulation, PyBoy, or turn-based game automation."
version: 0.2.0
metadata:
  { "openclaw": { "emoji": "🎮", "requires": { "bins": ["python3", "paper"], "env": [] }, "install": [{ "id": "pip", "kind": "node", "label": "Install PyBoy + dependencies (pip)" }] } }
---

# Pokemon Agent

Autonomous turn-based RPG player using headless Game Boy emulation via PyBoy.
Sessions are recorded via Paper — every API call is captured and observable.

## Requirements

- Python 3.10+ with PyBoy (`pip install pyboy Pillow numpy`)
- `paper` CLI authenticated (`paper status`)
- A legally obtained ROM file (`.gb` or `.gbc`)

## Setup

```bash
cd {baseDir}
bash scripts/install.sh
```

## Running a single agent

```bash
~/venv/bin/python3 scripts/agent.py 'rom/Pokemon - Red Version (USA, Europe) (SGB Enhanced).gb' \
  --strategy low --max-turns 5000
```

When run via `paper start claude`, this session is automatically recorded in Paper.

## Running the multi-agent evolution loop

`run_10_agents.py` spawns one `paper start claude` process per parameter variant.
Each agent is a full Claude Code session — independently recorded, scoreable, observable.

```bash
paper start claude -- --print --dangerously-skip-permissions \
  "Run the Pokemon agent evolution loop: python3 scripts/run_10_agents.py \
  'rom/Pokemon - Red Version (USA, Europe) (SGB Enhanced).gb'"
```

Or directly from the host (paperd must be running):

```bash
python3 scripts/run_10_agents.py 'rom/Pokemon - Red Version (USA, Europe) (SGB Enhanced).gb'
```

Each sub-agent receives a prompt like:

> Run the Pokemon agent with the parameters below and print the fitness JSON.
> `EVOLVE_PARAMS='{"stuck_threshold": 8, ...}' ~/venv/bin/python3 scripts/agent.py ...`
> When it finishes, read the output JSON and print ONLY its raw JSON contents as the final line.

The orchestrator parses each agent's fitness from stdout, ranks by score, and saves results to `pokedex/evolve_results.json`.

## When invoked as a sub-agent by run_10_agents.py

You will receive a prompt containing:
1. An `EVOLVE_PARAMS` JSON blob with navigator parameters
2. A bash command to run `agent.py` with `--output-json /tmp/pokemon_fitness_<label>.json`
3. An instruction to read that file and print its JSON as the last line

Do exactly this:
1. Run the bash command using a 10-minute timeout
2. Read the output JSON file
3. Print the raw JSON as your final output line — no markdown, no explanation

## Paper telemetry

`paperd` proxies all LLM API calls transparently. No instrumentation needed.
Every agent session appears in the Paper dashboard with its own session ID, turn count, and cost.

After a run, read sessions via `paper_reader.py`:

```python
from scripts.paper_reader import TapeReader
reader = TapeReader()
for session_id in reader.list_sessions():
    session = reader.read_session(session_id)
    print(session.session_id, len(session.entries))
```

## Observational memory

The observer reads Paper sessions and distills them into `pokedex/observations.md` —
errors hit, parameters tried, progress made. The next agent generation loads this file for continuity.

```bash
# Preview observations from past sessions
python3 scripts/observe_cli.py --dry-run

# Write observations to disk
python3 scripts/observe_cli.py
```

## How the agent works

### Game loop

1. **Boot**: Launch PyBoy in headless mode (`window="null"`)
2. **Read state**: Extract game data from memory addresses
3. **Decide**: Heuristic or LLM strategy
4. **Act**: Send button inputs
5. **Advance**: Tick emulator, repeat

### Memory map (Pokemon Red/Blue US)

| Address | Data |
|---------|------|
| `0xD057` | Battle type (0=none, 1=wild, 2=trainer) |
| `0xCFE6` | Enemy current HP |
| `0xCFE7` | Enemy max HP |
| `0xD015` | Player lead HP (high byte) |
| `0xD016` | Player lead HP (low byte) |
| `0xD014` | Player lead level |
| `0xD163` | Party size |
| `0xD01C`–`0xD01F` | Move 1–4 IDs |
| `0xD02C`–`0xD02F` | Move 1–4 PP |
| `0xD35E` | Current map ID |
| `0xD361` | Player X |
| `0xD362` | Player Y |
| `0xD31D` | Badge count |

### Navigator parameters (evolved by run_10_agents.py)

| Parameter | Default | Effect |
|-----------|---------|--------|
| `stuck_threshold` | 8 | Turns before declaring stuck |
| `door_cooldown` | 4 | Turns to ignore a door after using it |
| `waypoint_skip_distance` | 3 | A* skip distance for waypoints |
| `axis_preference_map_0` | `"y"` | Preferred movement axis on map 0 |
| `bt_restore_threshold` | 15 | Turns stuck before backtracking |
| `bt_max_attempts` | 3 | Max backtrack attempts per stuck event |

## File structure

```
pokemon-agent/
├── SKILL.md                  # This file
├── jcard.toml                # stereOS VM config
├── scripts/
│   ├── install.sh            # Setup (installs PyBoy, checks paperd)
│   ├── agent.py              # Main agent loop
│   ├── run_10_agents.py      # Multi-agent evolution orchestrator
│   ├── evolve.py             # Fitness scoring + LLM-guided mutation
│   ├── paper_reader.py       # Paper API + JSONL transcript reader
│   ├── observer.py           # Observation extraction from sessions
│   └── observe_cli.py        # Observer CLI
├── pokedex/                  # Run logs and evolution results
└── references/
    ├── routes.json            # Overworld route plans
    └── type_chart.json        # Type effectiveness
```

## Verification

Before deploying or after making changes, verify the agent works end-to-end:

```bash
# Unit tests (205 tests, 100% coverage required)
uv run pytest

# Live integration: run 1000 turns, confirm Pokemon selected
PYTHONPATH=scripts .venv/bin/python scripts/agent.py "rom/Pokemon - Red Version (USA, Europe) (SGB Enhanced).gb" --max-turns 1000
```

**What to look for in live output:**
- `Party: 1` appears around turn 100 — starter Pokemon selected
- `Battle ended. Total wins: 1` — rival battle won
- `MAP CHANGE | 40 -> 0` — exited Oak's Lab to Pallet Town
- Output streams in real-time with `[HH:MM:SS]` timestamps

A healthy run navigates: Red's bedroom (map 38) → house 1F (map 37) → Pallet Town (map 0) → Oak trigger → Oak's Lab (map 40) → pick starter → fight rival → exit.

### Evolution and parameter tuning

```bash
# Run 10 parameter variants in parallel and rank by fitness
uv run scripts/run_10_agents.py "rom/Pokemon - Red Version (USA, Europe) (SGB Enhanced).gb"

# Run evolution harness (mutate + evaluate over generations)
uv run scripts/evolve.py "rom/Pokemon - Red Version (USA, Europe) (SGB Enhanced).gb" --generations 5 --max-turns 1000
```

## Running on stereOS

This skill is designed to run inside a stereOS VM via Master Blaster. See `jcard.toml` for the VM configuration.

```bash
mb up       # boot VM, install deps, start agent through Tapes
mb attach   # watch the agent play
```

### Shared Mount Permissions

The `[[shared]]` mount maps the host repo to `/workspace` inside the VM. Host files retain their original ownership (UID 501 on macOS), but the VM runs as `admin` (UID 1000). Output directories (`frames/`, `pokedex/`) need world-writable permissions so the agent can write data that persists back to the host. The install script handles this automatically with `chmod a+rwx`.

### Game Events + Kafka Telemetry

The agent emits real-time game events (`pokemon.game.v1`) as JSONL via `scripts/publisher.py`; the `game-event-bridge` service tails that sink and produces to the `agent.game.events` topic live. Flink SQL jobs detect navigation and battle anomalies from the stream. LLM sessions are recorded separately by Paper (paperd) — see Observational Memory below.

```
Agent → JSONL sink → game-event-bridge → Kafka (agent.game.events)
                                              ↓
             Flink SQL jobs (stuck loops, battle wipes, position deadlocks)
                                              ↓
                                Kafka (agent.telemetry.alerts)
```

Start the full local stack:

```bash
docker compose up -d   # Kafka + Zookeeper + Flink + bridge + consumers
```

Inspect Paper sessions and memory:

```bash
paper status                          # check paperd is running and authenticated
cat pokedex/memory/observations.md    # distilled observations from past sessions
```

### Observational Memory

Long agent runs hit context compaction — when the context window fills up, older messages are compressed and cache prefixes are destroyed. Paper solves this by recording the full conversation (via paperd + Claude Code JSONL transcripts) regardless of what happens to the live context.

The observational memory system reads Paper sessions and distills them into a lightweight observations file that the agent can load at session start. This gives the agent durable memory across compaction boundaries and between sessions.

**Session start:** Read `pokedex/memory/observations.md` to recall what happened in previous sessions — errors hit, files created, progress made. This is cheap to load and keeps the agent from repeating mistakes or rediscovering things it already learned.

**Session end:** Run the observer to extract observations from the current session into the memory file.

```bash
# Check observations from past sessions before starting
cat pokedex/memory/observations.md

# After a session, distill new observations
python3 scripts/observe_cli.py

# Preview what would be extracted without writing
python3 scripts/observe_cli.py --dry-run
```

Observations are tagged by priority:
- `[important]` — errors, crashes, bugs, security issues
- `[possible]` — tests added, refactors, dependency updates
- `[informational]` — session goals, token usage, general context

For long speed runs, the pattern is:
1. Load observations at session start for continuity
2. Play the game, making decisions informed by past sessions
3. Run the observer after the session to capture what happened
4. Next session picks up where this one left off, even if context was compacted

## File Structure

```
pokemon-agent/
├── SKILL.md              # This file
├── jcard.toml            # stereOS VM config
├── docker-compose.yml    # Kafka + Flink + bridge + consumers stack
├── pokedex/
│   └── memory/           # Observational memory output (observations.md)
├── scripts/
│   ├── install.sh        # Setup script (installs PyBoy, checks paperd)
│   ├── agent.py          # Main agent loop (1000 lines)
│   ├── memory_reader.py  # Memory address definitions
│   ├── memory_file.py    # Agent memory management
│   ├── pathfinding.py    # A* pathfinding + collision maps
│   ├── evolve.py         # AlphaEvolve parameter evolution
│   ├── run_10_agents.py  # Parallel multi-agent evaluation
│   ├── paper_reader.py   # Paper session reader (paperd API + Claude Code JSONL)
│   ├── memory_writer.py  # Appends observations to pokedex/memory
│   ├── observer.py       # Observation extraction heuristics
│   └── observe_cli.py    # Observer CLI
├── docker/
│   ├── game-consumer/    # Game event consumer + JSONL writer
│   ├── alerts-consumer/  # Flink anomaly alert consumer → pokedex/memory
│   └── flink-sql/        # Flink SQL anomaly detection jobs (game events)
├── tests/                # 100% coverage test suite (205 tests)
└── references/
    ├── routes.json        # Overworld waypoints by map ID
    └── type_chart.json    # Pokemon type effectiveness
```

## Self-Healing Navigation

When a run fails — high stuck counts, low battle wins, agent trapped in a loop — **fix the waypoints in `references/routes.json` before re-tuning parameters**. The AlphaEvolve harness optimizes numeric knobs but cannot fix a path that walks into an impassable ledge.

### Diagnosis workflow

After a failed run, read the pokedex log (`pokedex/logN.md`) and check:

1. **Stuck events vs battles won** — a ratio above 100:1 means a navigation dead-end, not a parameter problem
2. **Final position** — if the agent ended on the same map it entered, it never progressed
3. **Map changes** — fewer than 5 means the agent is trapped in one area
4. **Stuck position cluster** — grep the log for `STUCK` lines; if they all cluster around the same y-coordinate, there's a physical obstacle (ledge, tree, NPC) blocking the path

### Fix sequence

1. **Identify the obstacle** — check where stuck events cluster (e.g., Route 1 y=24 is a one-way ledge)
2. **Update `references/routes.json`** — reroute waypoints around the obstacle, not through it
3. **Use `"loop": true`** for grind zones — when the goal is battles (not progression), loop waypoints keep the agent farming encounters in a known-good grass area instead of advancing into obstacles
4. **Re-run immediately** — don't burn turns tuning parameters for a broken path; fix the path first, then evolve

### Route design principles

- **Grind routes loop**: set `"loop": true` and keep waypoints in tall grass (e.g., Route 1 south at y=29-33)
- **Progression routes go one way**: no loop flag, waypoints lead to the next city
- **Ledges are one-way down**: never route the agent northward through a known ledge; go around or stay south
- **Keep waypoints close together**: big jumps between waypoints (e.g., y=27 to y=21) hide obstacles the agent can't see

### When to use AlphaEvolve vs waypoint fixes

| Symptom | Fix |
|---|---|
| High stuck count, agent trapped at one position | Fix waypoints in `routes.json` |
| Agent progresses but loses battles | Tune battle params via `evolve.py` |
| Agent navigates but slowly | Tune navigation params (`stuck_threshold`, `bt_*`) |
| Agent oscillates between two maps | Add `"loop": true` or adjust door cooldown |

### Example: Route 1 grind zone

The south grass on Route 1 (y=29-33) has reliable wild encounters. A looping route keeps the agent here:

```json
"12": {
  "name": "Route 1",
  "loop": true,
  "waypoints": [
    {"x": 5, "y": 33, "note": "Enter from Pallet Town — south grass zone"},
    {"x": 5, "y": 29, "note": "Walk north into tall grass"},
    {"x": 9, "y": 31, "note": "Sweep right through grass"},
    {"x": 5, "y": 33, "note": "Loop back south"}
  ]
}
```

When the agent faints, it respawns in Pallet Town, walks north, and re-enters the grind loop automatically.

## Limitations

- ROM not included — supply your own legally obtained copy.
- Memory addresses are specific to Pokemon Red/Blue (US). Other games need adjusted offsets.
- `paper` CLI must be authenticated before launching agents (`paper status`).
