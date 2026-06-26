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

## Paper Telemetry

[Paper](https://papercompute.com) records every LLM session the agent runs. The local `paperd` daemon proxies all API calls transparently (via `ANTHROPIC_BASE_URL`) — no instrumentation in the agent code. Each agent session appears in the Paper dashboard with its own session ID, turn count, and cost.

When the multi-agent runner spawns variants via `paper start claude`, each one is a full, independently recorded Claude Code session. Authenticate the `paper` CLI once with `paper status` before launching.

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

**First finding:** `door_cooldown=2` beats the default of 8. Shorter cooldown means fewer wasted turns walking away from doors before retrying. Confirmed across two milestones (Pokemon selection and rival battle) with 10 independent runs each.

### Long-session mode

You can still run the agent the traditional way for a single long session, the way [ClaudePlaysPokemon](https://www.twitch.tv/claudeplayspokemon) works on Twitch:

```bash
uv run scripts/agent.py rom/pokemon_red.gb --strategy heuristic --max-turns 50000
```

The two approaches complement each other. Long sessions are better for discovering new capabilities and debugging game-specific logic. The evolution loop is better for optimizing parameters once the code structure exists.

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
├── scripts/
│   ├── install.sh           # setup: Python, PyBoy, checks paperd
│   ├── agent.py             # main agent loop + strategies
│   ├── memory_reader.py     # memory address definitions
│   ├── memory_file.py       # agent memory management
│   ├── paper_reader.py      # Paper API + JSONL transcript reader (stdlib only)
│   ├── observer.py          # heuristic observation extractor
│   ├── observe_cli.py       # CLI for running the observer
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

Scripted areas like Oak's Lab (map 40) disable backtracking entirely — the lab's multi-phase cutscene looks "stuck" but is progressing naturally.

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
