# JSONL → Kafka Bridge: Live Local Game-Event Streaming

**Date:** 2026-07-19
**Status:** Draft (follow-up to the Confluent Cloud removal)

## Goal

Make game events flow into the local `agent.game.events` topic **live** while
the agent runs, instead of requiring a manual pre-seed before demos. The agent
stays broker-free: it keeps writing JSONL via `scripts/publisher.py`, and a
new bridge service tails the sink and produces to Kafka.

## Decision

Two shapes were considered:

1. **JSONL → Kafka bridge (chosen):** a docker-compose service tails
   `data/telemetry/game/*.jsonl` and produces each complete line to the
   topic. The agent keeps zero broker dependencies; JSONL stays the source
   of truth (`Agent → JSONL → Kafka`, exactly the diagram the README ships
   today); replay-from-start doubles as the demo seeder.
2. **Direct local producer in publisher.py (rejected):** lowest latency, but
   re-adds the `confluent-kafka` dependency and the FanoutPublisher deleted
   in PR #43, and makes the agent process broker-aware again.

## Components

### `docker/game-event-bridge/` (new)

- `bridge.py` — the tailer/producer. Pure-Python core functions (testable
  without Kafka), thin `main()` around them:
  - `scan(telemetry_dir) -> list[Path]` — sorted `*.jsonl` files, so date
    rotation (new day, new file) is picked up automatically.
  - `read_new_lines(path, offset) -> tuple[list[str], int]` — reads from
    byte `offset`, returns only **complete** lines (up to the last `\n`);
    a partially written trailing line stays unread until the writer
    finishes it.
  - `load_state(path) / save_state(path, state)` — `{filename: byte_offset}`
    JSON, saved after each produced batch.
  - Main loop: every `POLL_MS`, for each file produce new lines with
    `key = parsed["schema"]`, `value = raw line`; skip-and-log lines that
    fail to parse as JSON (protects the downstream Flink JSON format and
    consumers); `producer.poll(0)` per batch; flush + save state on
    SIGTERM/SIGINT.
- `Dockerfile` — mirrors `docker/game-consumer` (python-slim +
  `confluent-kafka`).

### `docker-compose.yml`

New `game-event-bridge` service:

- `depends_on: kafka: condition: service_healthy` (same as the consumers).
- Env: `KAFKA_BOOTSTRAP_SERVERS=kafka:29092`, `KAFKA_TOPIC=agent.game.events`,
  `TELEMETRY_DIR=/telemetry`, `POLL_MS=500`, `FROM_BEGINNING=1`.
- Volume: `./data/telemetry/game:/telemetry:ro` only.
- **State is container-local** (`STATE_FILE` defaults to
  `/tmp/bridge-offsets.json`, no volume). Found during the live E2E test:
  the kafka service has no volume, so a `compose down && up` wipes the
  topic — durable bridge state would then skip the replay and leave the
  topic silently empty. Tying state to the container lifecycle keeps them
  in sync: fresh stack → fresh state → full replay. `compose restart`
  keeps the container filesystem, so offsets survive routine restarts; a
  force-recreate against a surviving broker re-replays, which
  at-least-once semantics already tolerate.

## Semantics

- **At-least-once**: offsets are saved after produce, not after per-message
  delivery confirmation. A crash between produce and save can re-send a
  tail of events. Downstream (game-consumer printing, Flink anomaly
  detection, alerts) tolerates duplicates; exactly-once is out of scope.
- **Seeding built in**: with empty state and `FROM_BEGINNING=1` (default),
  first start replays the whole sink — the demo "pre-seed" step disappears.
  Set `FROM_BEGINNING=0` to initialize offsets at end-of-file instead
  (live-only tailing).
- **Ordering**: per-file order is preserved; cross-file order follows sorted
  filenames (date-partitioned, so chronological).

## Docs impact

- README pipeline section: `Agent → JSONL → bridge → Kafka
  (agent.game.events)`; drop the "seed it with kafka-console-producer"
  instruction.
- Talk outline Act 2: drop the pre-seed step — start compose, run the agent,
  watch `game-consumer` logs fill live.

## Testing

- `tests/test_game_event_bridge.py`, stubbing `confluent_kafka` in
  `sys.modules` exactly like `tests/test_alerts_consumer.py`:
  - complete/partial line splitting (trailing half-line not emitted, then
    emitted once completed)
  - offset persistence across loop iterations and process restarts
  - new file pickup (rotation) and sorted replay order
  - malformed JSON line skipped with a log, valid neighbors still produced
  - `FROM_BEGINNING=0` initializes offsets at EOF
- Bridge lives under `docker/` (like both consumers), outside the
  `[tool.coverage.run] source` roots — tested, but not part of the 100%
  gate on `scripts/` + `viewer/`.
- Manual: `docker compose up -d`, run the agent with `--telemetry-dir`,
  `docker compose logs -f game-consumer` shows events within ~1s.

## Out of scope

- Exactly-once delivery, schema registry, backpressure handling.
- Bridging any directory other than the game-event sink.
- Changes to `scripts/publisher.py` or the agent (they stay broker-free).
