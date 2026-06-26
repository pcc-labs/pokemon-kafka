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

## Running on stereOS

The VM mounts this repo at `/workspace`. paperd is assumed to be running on the host;
the VM inherits `ANTHROPIC_BASE_URL` via the shared mount or jcard secrets.

```bash
mb init pokemon-agent
mb up
mb attach  # watch the agent play
```

## Limitations

- ROM not included — supply your own legally obtained copy.
- Memory addresses are specific to Pokemon Red/Blue (US). Other games need adjusted offsets.
- `paper` CLI must be authenticated before launching agents (`paper status`).
