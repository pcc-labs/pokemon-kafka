# Pokemon Agent

> See also: [pokemon-kafka](https://github.com/papercomputeco/pokemon-kafka) — Streams gameplay events through Kafka for large-scale data processing and uses Flink for real-time anomaly detection and self-healing.

![Pokemon Agent](hero2.png)

Autonomous Pokemon Red player that reads game memory, makes strategic decisions, and plays headlessly inside a stereOS VM.

## Architecture

```
stereOS VM (/workspace)
┌──────────────────────────────────────────────────┐
│                                                  │
│  PyBoy (headless, window="null")                 │
│    ↓ memory addresses                            │
│  MemoryReader → BattleState / OverworldState      │
│    ↓                                             │
│  Strategy Engine (heuristic or LLM)              │
│    ↓ button inputs                               │
│  GameController → PyBoy                          │
│                                                  │
│  paperd ← proxies LLM API calls, records sessions│
│                                                  │
└──────────────────────────────────────────────────┘
  ↕ shared mount (./ ↔ /workspace)
Host: frames/  pokedex/
```

The agent runs a tight loop: read game state from known memory addresses, pick an action, send button inputs, tick the emulator forward. No display server needed. Screenshots come from PyBoy's internal frame buffer (`screen.ndarray`), not from the OS.

**Shared mount permissions.** The `[[shared]]` mount in `jcard.toml` maps `./` on the host to `/workspace` in the VM. Files keep their host ownership (UID 501 on macOS), but the VM runs as `admin` (UID 1000). This means host-created directories are read-only inside the VM by default. The install script opens write permissions on output directories (`frames/`, `pokedex/`) so the agent can write session data that persists back to the host.

## Quickstart

### stereOS (recommended)

```bash
mb up          # boot the VM, install deps, start the agent through Paper
mb attach      # watch it play
```

The VM configuration lives in `jcard.toml`. It mounts the repo at `/workspace`, installs Python + PyBoy, and runs the agent. `paperd` is assumed to be running on the host (authenticate the `paper` CLI with `paper status` first); it proxies the agents' LLM calls via `ANTHROPIC_BASE_URL`.

### Local

```bash
bash scripts/install.sh
uv run scripts/agent.py rom/pokemon_red.gb --strategy heuristic --max-turns 1000
```

Add `--save-screenshots` to capture frames every 10 turns into `frames/`.

> You must supply your own legally obtained ROM file in `rom/`.

## How It Works

**Game loop.** Each turn the agent ticks PyBoy forward, reads memory, decides, and acts. Turns are cheap — headless mode removes the 60fps cap and all rendering, so the emulator runs ~100x faster than real-time. The agent runs hundreds of thousands of them to progress through the game.

**Memory reading.** `MemoryReader` pulls structured data from fixed addresses in Pokemon Red's RAM: battle type, HP, moves, PP, map ID, coordinates, badges, party state. These addresses are specific to the US release.

**Battle strategy.** When a battle is detected (`0xD057 != 0`), the agent evaluates available moves using a type effectiveness chart, picks the highest-damage option, and manages healing and switching. The heuristic strategy requires no API calls.

**Overworld navigation.** Outside battle, the agent follows waypoints defined in `references/routes.json`. It handles early-game scripted sequences (Red's room to Oak's lab) and general map-to-map routing. A stuck counter triggers random movement to break out of loops.

## Paper Traces

**What is Paper?** [Paper](https://papercompute.com) is an LLM session recorder: `paperd`, a local daemon, sits in front of the Anthropic API and transparently captures every request/response pair the agent makes. Point the agent at it via `ANTHROPIC_BASE_URL` and no code changes are needed — the recording happens at the proxy layer, not in `scripts/agent.py`.

This is distinct from the [Kafka Telemetry Pipeline](#kafka-telemetry-pipeline) below. Paper records *LLM* activity — what Claude was asked, what it decided, how many tokens it burned, at what cost — one entry per agent session. Kafka streams *game* activity — battles, movement, map changes — as structured events, independent of whether an LLM is involved at all (the heuristic strategy makes zero API calls and has nothing for Paper to record). The two pipelines don't depend on each other: run with `--strategy heuristic` and Paper stays empty; run without Docker Compose up and Kafka stays empty. Together they answer two different questions — "what did the agent think?" (Paper) and "what did the agent do?" (Kafka).

Each agent session appears in the Paper dashboard with its own session ID, turn count, and cost. When the multi-agent runner spawns variants via `paper start claude`, each one is a full, independently recorded Claude Code session. Authenticate the `paper` CLI once with `paper status` before launching.

## Observational Memory

Inspired by [Mastra's observational memory](https://mastra.ai/blog/observational-memory), this system reads recorded Paper sessions, extracts noteworthy events via heuristic pattern matching (no LLM calls), and writes prioritized observations to memory files.

`paper_reader.py` is a two-source hybrid: it discovers sessions through the local paperd API (filtered to this working directory) and reads their transcript content from the Claude Code JSONL files under `~/.claude/projects/`. When paperd is unavailable, it falls back to scanning the JSONL directory directly, so observation still works offline. The observer walks each session, identifies patterns (errors, file creations, token usage), and writes observations to `pokedex/memory/`.

```
pokedex/memory/
├── observations.md      # date-grouped observations with priority tags
└── observer_state.json  # watermark tracking processed sessions
```

**What it extracts:**
- Session goals (first user message)
- Tool errors and exception tracebacks
- Files created during the session
- Token usage summaries

Each observation is tagged `[important]`, `[possible]`, or `[informational]` based on keyword matching (e.g. bug/error/crash are important, test/refactor are possible).

```bash
# Preview observations without writing
uv run scripts/observe_cli.py --dry-run

# Process all unprocessed sessions
uv run scripts/observe_cli.py

# Reprocess everything from scratch
uv run scripts/observe_cli.py --reset

# Process a single session by ID
uv run scripts/observe_cli.py --session <harness_session_id>
```

Sessions are discovered from paperd (read from `ANTHROPIC_BASE_URL`), or from the local JSONL transcripts when paperd is offline. The watermark in `observer_state.json` is stamped with the reader identity and auto-resets if the reader changes, so upgrades don't reprocess old sessions.

## Kafka Telemetry Pipeline

The agent streams real-time **game events** to Kafka as structured events. `scripts/agent.py` publishes `pokemon.game.v1` events directly to the `agent.game.events` topic via `scripts/publisher.py` — no proxy required. Downstream consumers and Flink jobs process the stream in real time. (LLM sessions are recorded separately by Paper; see [Observational Memory](#observational-memory).)

```
Agent → Kafka (agent.game.events)
            ├→ game-consumer (prints + writes JSONL)
            ├→ Flink SQL (anomaly detection)
            │    └→ Kafka (agent.telemetry.alerts)
            │         └→ alerts-consumer (prints + writes observations to pokedex/memory)
            └→ DuckDB (ad-hoc queries on JSONL sink)

JSONL sink (data/telemetry/*.jsonl)
  └→ dlt pipeline → DuckDB warehouse (local)
                  → Snowflake / Confluent Cloud (production)
```

Each event carries the event type (`battle`, `overworld`, `map_change`, `stuck`, `milestone`, `session`), turn, timestamp, and a flat data payload (map, position, HP, action, badges, …). Flink reads these to flag navigation deadlocks and battle loops in real time: `GAME_STUCK_LOOP`, `BATTLE_WIPE`, `BATTLE_LOOP`, `POSITION_DEADLOCK`, `NO_PROGRESS`.

```bash
# Start the full pipeline (Kafka, Flink, consumers)
docker compose up -d

# Watch raw game events
docker compose logs -f game-consumer

# Watch anomaly alerts
docker compose logs -f alerts-consumer

# Flink dashboard
open http://localhost:8081
```

LLM sessions are recorded by Paper (paperd) on the host — point the agent at it with `ANTHROPIC_BASE_URL`. A local-first alternative for the game-event stream exists without a broker: pass `--telemetry-dir` to the agent and it writes JSONL files directly via `scripts/publisher.py`.

## Confluent Cloud Setup

Stream game and telemetry events to Confluent Cloud instead of (or alongside) local JSONL files. The publisher is opt-in and requires a cluster, two topics, and an API key.

For a guided setup with troubleshooting, install the [confluent-cloud-setup](https://github.com/papercomputeco/skills/tree/main/skills/confluent-cloud-setup) skill:

```bash
npx skills add papercomputeco/skills
```

### Quick start

1. Create a **Basic** cluster in Confluent Cloud (free tier, no ACL enforcement)
2. Create topics: `pokemon.telemetry.raw` and `pokemon.game.events`
3. Create a **My account** API key (not service account)
4. Set env vars and create `config.toml`:

```bash
export CONFLUENT_API_KEY="<your-api-key>"
export CONFLUENT_API_SECRET="<your-api-secret>"
```

```toml
version = 1

[telemetry]
dir = "data/telemetry"

[telemetry.confluent]
enabled = true
bootstrap_servers = "pkc-xxxxx.us-east-2.aws.confluent.cloud:9092"
topic_prefix = "pokemon"
api_key_env = "CONFLUENT_API_KEY"
api_secret_env = "CONFLUENT_API_SECRET"
```

5. Install the optional dependency and run:

```bash
uv sync --extra confluent
uv run scripts/agent.py rom/pokemon_red.gb --config config.toml --strategy low --max-turns 500
```

Events stream to both local JSONL and Confluent Cloud via the `FanoutPublisher`. If Confluent fails, the agent continues writing locally.

## Flink Anomaly Detection

Apache Flink (1.18) runs SQL jobs against the `agent.game.events` stream:

| Job | Window | Trigger | What it catches |
|---|---|---|---|
| `GAME_STUCK_LOOP` | 60s tumbling | 5+ stuck events on a map | Navigation stuck on one tile |
| `BATTLE_WIPE` | 5min tumbling | Player HP hits 0 | Party wipe / failed battle |
| `BATTLE_LOOP` | 30s tumbling | 20+ battle events at same enemy HP | Input spam not dealing damage |
| `POSITION_DEADLOCK` | 2min tumbling | 50+ overworld events at one position | Bouncing off an impassable obstacle |
| `NO_PROGRESS` | 5min tumbling | 100+ overworld events, ≤5 unique tiles | Navigation completely stalled |

The jobs write alerts to the `agent.telemetry.alerts` Kafka topic. The alerts consumer picks them up and appends each as an `[important]` observation to `pokedex/memory/observations.md`, feeding anomalies into the observational memory the agent loads at session start.

Flink SQL definitions live in `docker/flink-sql/init.sql`. The connector JAR is downloaded automatically at startup.

## Data Warehouse

The JSONL files in `data/telemetry/` serve as the universal interchange format -- the same files whether a Kafka consumer or the local publisher wrote them. The dlt pipeline is the load step that moves those files into a persistent, queryable warehouse.

dlt handles schema normalization and incremental loading. The destination is a one-line swap: `duckdb` for local development, `snowflake` for production. Both `query_telemetry.py` and `historical_observer.py` work against either source via the `--db` flag.

```bash
# Install dlt (optional dependency group)
uv sync --group dlt

# Load JSONL into a local DuckDB warehouse
uv run scripts/dlt_pipeline.py

# Load into Snowflake instead
uv run scripts/dlt_pipeline.py --destination snowflake

# Query the warehouse directly
uv run scripts/query_telemetry.py --db data/telemetry.duckdb

# Historical insights from the warehouse
uv run scripts/historical_observer.py --db data/telemetry.duckdb
```

Without `--db`, both query scripts fall back to scanning JSONL files directly -- nothing changes for existing workflows.

## AlphaEvolve Strategy Evolution

Inspired by [AlphaEvolve](https://arxiv.org/abs/2506.13131) (DeepMind), the agent can automatically improve its navigation parameters through headless evaluation runs. Instead of manually tuning thresholds, the evolution harness runs 10 agent variants in parallel, scores them against a composite fitness function, and keeps the winner.

**How it works.** The agent's navigator has tunable knobs: stuck threshold, door cooldown, waypoint skip distance, axis preference. The harness treats these as a genome. Each generation, it either asks an LLM to propose a mutation (informed by observer diagnostics) or randomly perturbs values. The variant runs headless, and its fitness is compared to the current best.

```bash
# Run the evolution harness (LLM-free random perturbation by default)
uv run scripts/evolve.py rom/pokemon_red.gb --generations 5 --max-turns 1000

# Run 10 parameter variants in parallel and rank them
uv run scripts/run_10_agents.py rom/pokemon_red.gb
```

The observer feeds failure context (stuck events, tool errors) into the LLM mutation prompt so variants target actual problems rather than making blind changes.

### Closing the loop: bounds, history, and stagnation detection

The case study below showed a clear gap: every run hit a plateau where the LLM proposed near-identical variants for multiple consecutive generations. Three mechanisms now close that loop:

**Parameter bounds enforcement.** `PARAM_BOUNDS` defines valid ranges for every evolvable parameter. `clamp_params()` enforces type coercion and clamping on all mutations, whether from the LLM or random perturbation. The LLM can no longer propose `stuck_threshold: -5` or `hp_run_threshold: 99.0`. Invalid enum values fall back to defaults. This replaced scattered ad-hoc `max(1, ...)` guards with a single source of truth.

**Variant history in the LLM prompt.** Each generation's outcome (score, improvement status, parameter diffs from defaults) is fed back into the next mutation prompt. The LLM sees a compact log of the last 10 generations and is instructed to avoid repeating failed combinations. In the case study, Run 4's Gen 8 breakthrough happened *despite* having no memory of prior attempts. Now the LLM starts every generation with full context of what has already been tried.

**Convergence detection with forced exploration.** `detect_stagnation()` fires when the last 3 generations all fail to improve. When triggered:
- The LLM receives a WARNING directive to make larger, multi-parameter changes
- The no-LLM fallback switches from `_perturb()` (1 param, small delta) to `_forced_exploration_perturb()` (3-4 params, 2x deltas, axis flip)

This is the mechanism that was missing in the case study. Run 1 locked into one axis preference for 9 stale generations. With stagnation detection, generation 4 would have triggered forced exploration, potentially finding the Gen 8-style breakthrough 4 generations earlier.

**First finding:** `door_cooldown=2` beats the default of 8. Shorter cooldown means fewer wasted turns walking away from doors before retrying. Confirmed across two milestones (Pokemon selection and rival battle) with 10 independent runs each.

### Long-session mode

You can still run the agent the traditional way for a single long session, the way [ClaudePlaysPokemon](https://www.twitch.tv/claudeplayspokemon) works on Twitch:

```bash
uv run scripts/agent.py rom/pokemon_red.gb --strategy heuristic --max-turns 50000
```

The two approaches complement each other. Long sessions are better for discovering new capabilities and debugging game-specific logic. The evolution loop is better for optimizing parameters once the code structure exists.

## autotune Integration

[autotune](https://github.com/pcc-labs/autotune) is a sibling training loop (Try → Check → Reward → Nudge) that runs this agent, scores each run against the canonical Route-1 story, and feeds what it learns back here. The agent has no runtime dependency on autotune; `scripts/autotune_bridge.py` reads autotune's output and degrades to no-ops when it is absent.

There are two consumer seams:

**Genome from `notes.md`.** autotune writes a genome block into `notes.md`:

```
<!-- autotune:genome
{"stuck_threshold": 8, "door_cooldown": 10, ...}
-->
```

The agent reads the last such block at startup and uses it as its `EVOLVE_PARAMS` baseline. The `EVOLVE_PARAMS` env var still overrides it, so behavior is unchanged when no block is present.

```bash
# With a genome block in notes.md, the agent applies it automatically:
uv run scripts/agent.py rom/pokemon_red.gb
```

**Local model as the evolve proposer.** `evolve.py` can use autotune's locally-trained MLX model as its mutation proposer instead of Claude. No API key is needed.

```bash
uv run scripts/evolve.py rom/pokemon_red.gb --llm local
```

`--llm` accepts `anthropic` (default, Claude), `local` (autotune's model via `mlx_lm generate`), or `none` (random perturbation). `--no-llm` still works.

See the [autotune integration doc](https://github.com/pcc-labs/autotune/blob/main/docs/pokemon-kafka-integration.md) for the full workflow.

## Testing

100% line coverage enforced via `pytest-cov` (`fail_under = 100` in `pyproject.toml`).

```bash
# Run the full test suite
uv run pytest

# Run a single test class
uv run pytest tests/test_agent.py::TestLabPokemonSelection -xvs
```

### Live integration test

Boot the agent against a real ROM and confirm it selects a starter Pokemon within 1000 turns:

```bash
mb up
# or locally:
PYTHONPATH=scripts .venv/bin/python scripts/agent.py "rom/Pokemon - Red Version (USA, Europe) (SGB Enhanced).gb" --max-turns 1000
```

The agent streams structured log lines as it plays. Look for `Party: 1` in the output to confirm a Pokemon was selected. A typical run selects a starter around turn 100 and wins the rival battle shortly after.

## Project Structure

```
pokemon-agent/
├── README.md                # this file
├── LICENSE                  # MIT license
├── CONTRIBUTING.md          # contributor guide
├── SKILL.md                 # skill definition for stereOS agents
├── jcard.toml               # stereOS VM configuration
├── frames/                  # screenshot output (gitignored)
├── rom/                     # user-provided ROM files (gitignored)
├── docker-compose.yml       # Kafka + Flink + consumers stack
├── docker/
│   ├── game-consumer/       # game event consumer + JSONL writer
│   ├── alerts-consumer/     # anomaly alert consumer → pokedex/memory
│   └── flink-sql/
│       ├── init.sql          # Flink SQL anomaly jobs (game events)
│       └── submit-jobs.sh    # startup script for SQL client
├── scripts/
│   ├── install.sh           # setup: Python, PyBoy, checks paperd
│   ├── agent.py             # main agent loop + strategies
│   ├── memory_reader.py     # memory address definitions
│   ├── memory_file.py       # agent memory management
│   ├── paper_reader.py      # Paper API + JSONL transcript reader (stdlib only)
│   ├── observer.py          # heuristic observation extractor
│   ├── observe_cli.py       # CLI for running the observer
│   ├── publisher.py         # local-first JSONL telemetry publisher
│   ├── dlt_pipeline.py      # dlt warehouse loader (JSONL → DuckDB/Snowflake)
│   ├── historical_observer.py # cross-session insights via DuckDB
│   ├── query_telemetry.py   # ad-hoc telemetry queries
│   ├── memory_writer.py     # appends observations to pokedex/memory
│   ├── pathfinding.py       # collision map + backtrack manager
│   ├── evolve.py            # AlphaEvolve strategy evolution harness
│   └── run_10_agents.py     # parallel multi-agent evaluation runner
├── references/
│   ├── routes.json          # overworld waypoints
│   └── type_chart.json      # type effectiveness data
├── pokedex/
│   └── log1.md              # session log: stereOS setup notes
└── tests/                   # 100% coverage test suite
```

## Pokedex

The `pokedex/` directory contains session logs and development notes. Each log documents what happened during a run: setup blockers, fixes, observations about agent behavior. These serve as a record of how the project evolved and what the agent encountered.

## Speed Run Milestones

Target turn counts for community benchmarking. Fork it, improve the strategy, post your numbers.

| Milestone | Low | Medium | High |
|---|---|---|---|
| Get Charmander + beat rival | ~200 | ~200 | ~200 |
| Reach Viridian City | ~2,000 | ~1,000 | ~500 |
| Reach Pewter City | ~5,000 | ~3,000 | ~1,500 |
| Beat Brock (1st gym) | ~8,000 | ~5,000 | ~3,000 |
| Clear Mt. Moon | ~20,000 | ~10,000 | ~5,000 |
| Beat Misty (2nd gym) | ~30,000 | ~15,000 | ~8,000 |
| Beat Lt. Surge (3rd gym) | ~50,000 | ~25,000 | ~15,000 |
| 8 badges | ~200,000 | ~100,000 | ~60,000 |
| Elite Four | ~300,000 | ~150,000 | ~80,000 |

## FLE-Style Backtracking

Inspired by the [Factorio Learning Environment](https://arxiv.org/abs/2503.09617)'s `BacktrackingAgent`, the agent snapshots game state at key moments (map changes, periodic intervals) and restores when stuck. This directly addresses navigation dead-ends like Route 1's y=28 blocker — instead of wasting turns in a loop, the agent reverts to a known-good state and tries an alternate path.

Snapshots use PyBoy's `save_state()`/`load_state()` with in-memory `BytesIO` buffers (~130KB each, <1ms). A bounded deque keeps the most recent 8 snapshots. Each snapshot tracks its restore count, and after 3 failed attempts from the same snapshot it's discarded. Four parameters control the behavior and are evolvable through AlphaEvolve:

| Parameter | Default | Description |
|---|---|---|
| `bt_max_snapshots` | 8 | Max snapshots in the deque |
| `bt_restore_threshold` | 15 | Stuck turns before restoring |
| `bt_max_attempts` | 3 | Retries per snapshot |
| `bt_snapshot_interval` | 50 | Periodic snapshot frequency |
| `hp_run_threshold` | 0.2 | HP ratio below which to run from wild battles |
| `hp_heal_threshold` | 0.25 | HP ratio below which to use a healing item |
| `unknown_move_score` | 10.0 | Baseline score for unknown moves |
| `status_move_score` | 1.0 | Score for zero-power status moves |

Scripted areas like Oak's Lab (map 40) disable backtracking entirely — the lab's multi-phase cutscene looks "stuck" but is progressing naturally.

## Case Study: 10,000-Turn Viridian City Speedrun

Four 10-generation evolution runs with all features enabled: LLM-guided mutation, observational memory, historical observer with JSONL telemetry, and Tapes persistence. Each successive run had access to all previous runs' telemetry via the historical observer.

### Results

| Run | Historical entries | Gens improved | Final score | Evolution pattern |
|-----|-------------------|---------------|-------------|-------------------|
| 1 (cold) | 0 | 1/10 | 39,415 | One lucky jump at Gen 1, then 9 stale |
| 2 | 10 | 3/10 | 12,836 | Three incremental steps (Gen 1, 5, 6) |
| 3 | 20+ | 3/10 | 17,319 | Three progressive steps (Gen 1, 2, 4) |
| 4 | 30+ | **4/10** | **39,423** | Four steps (Gen 1, 3, 4, 8), late breakthrough |

### What the data shows

**Improvement rate scales with historical context.** Cold start: 1/10 generations improved. With history: 3, 3, 4 out of 10. The LLM makes better mutations when it can see what failed before.

**Exploration diversity increases.** Run 1 locked into one axis preference immediately. Runs 2-4 explored both `axis_preference: y` and `axis_preference: x` across generations. Run 4 explored for 7 generations before finding a 39k+ score at Gen 8 through a novel param combination (`unknown_move_score: 18.0`, `bt_max_snapshots: 14`) that no previous run had tried.

**Score convergence through different paths.** Run 4 (39,423) matched Run 1 (39,415) but through systematic exploration across 8 generations rather than a lucky first guess. The historical observer enabled the LLM to find an equivalent optimum through data-informed search.

### Run 4 detail (best run)

| Gen | Score | Improved? | Key mutation |
|-----|-------|-----------|-------------|
| 1 | 11,429 | Yes | Lowered `stuck_threshold` to 4, `bt_restore_threshold` to 12 |
| 2 | -9,559 | No | |
| 3 | 11,991 | Yes | Switched to `axis_preference: x`, `waypoint_skip_distance: 6` |
| 4 | 12,836 | Yes | Fine-tuned `stuck_threshold` to 4, kept x-axis |
| 5-7 | ~11,400 | No | Plateau |
| 8 | **39,423** | Yes | `unknown_move_score: 18`, `bt_max_snapshots: 14`, `hp_heal: 0.35` |
| 9-10 | ~39,423 / 7,721 | No | |

Gen 8 broke out of a local optimum by touching params previous runs had left alone (`unknown_move_score`, `status_move_score`). The historical telemetry showed the standard param space was exhausted, pushing the LLM to explore new dimensions.

### Broader applications

The feedback loop (agent runs, telemetry persists, historical observer surfaces patterns, next run reads those patterns) applies beyond games:

- **Large-scale refactors** — each PR is a "generation." Cross-session telemetry prevents re-discovering the same edge cases across dozens of migration PRs.
- **Product engineering** — DuckDB queries across sprint telemetry reveal which modules have the highest revision rates or where debugging tokens concentrate.
- **Day-to-day AI coding** — every `claude code` session writes telemetry. The historical observer turns that into quantified patterns rather than starting each session cold.

### The gap (now closed)

Every run hit a plateau where the LLM proposed near-identical variants for multiple consecutive generations. The historical observer recorded convergence but nothing acted on it. Run 4's Gen 8 breakthrough happened despite this gap, not because of a designed escape mechanism.

This gap is now closed. The evolution loop enforces parameter bounds, feeds variant history into every LLM prompt, and detects stagnation to trigger forced exploration. See [Closing the loop](#closing-the-loop-bounds-history-and-stagnation-detection) above for the full mechanism.

All four runs were entirely local — JSONL files and DuckDB, no Kafka broker or managed services required. Raw telemetry lives in `data/telemetry/` and is queryable with `scripts/query_telemetry.py`.

## Inspiration & References

- [Factorio Learning Environment](https://arxiv.org/abs/2503.09617) — Backtracking agent patterns, structured observations, and incremental report distillation for game-playing LLM agents
- [AlphaEvolve](https://arxiv.org/abs/2506.13131) — DeepMind's LLM-driven code evolution framework
- [Discovering Multiagent Learning Algorithms with LLMs](https://arxiv.org/abs/2602.16928) — AlphaEvolve applied to game-playing agents
- [ClaudePlaysPokemon](https://www.twitch.tv/claudeplayspokemon) — Anthropic's Claude-plays-Pokemon Twitch stream
- [Insights into Claude Opus 4.5 from Pokemon](https://www.lesswrong.com/posts/u6Lacc7wx4yYkBQ3r/insights-into-claude-opus-4-5-from-pokemon) — Navigation, memory notes, and spatial reasoning analysis
- [ClaudePlaysPokemon Harness Changes](https://docs.google.com/document/u/1/d/e/2PACX-1vRIsu2pLI21W4KjfYbN13or8E-8cvJYw570wGMEp4UQU63ZhEh9FPGgj2ark8Yk7Vyrtt9MWq3jnn4h/pub) — Minimap, navigator, and memory file evolution
- [Claude Plays Pokemon](https://jurgengravestein.substack.com/p/claude-plays-pokemon) — Why games reveal AI capabilities better than benchmarks
- [ClaudePlaysPokemonStarter](https://github.com/davidhershey/ClaudePlaysPokemonStarter) — Official minimal starter harness
- [LLM Pokemon Scaffold](https://github.com/cicero225/llm_pokemon_scaffold) — Multi-model scaffold (Claude, Gemini, o3)
