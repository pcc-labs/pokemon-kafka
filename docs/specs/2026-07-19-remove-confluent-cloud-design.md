# Remove Confluent Cloud (Keep Local Kafka + Flink)

**Date:** 2026-07-19
**Status:** Approved

## Goal

Remove the Confluent Cloud vendor path entirely. The local story is the
focus: the docker-compose Kafka + Flink stack and its consumers stay.

## Scope decision

"Confluent" appears in the repo as two distinct things:

1. **Confluent Cloud path (REMOVE):** `ConfluentPublisher` (SASL to cloud
   brokers), `[telemetry.confluent]` config with `CONFLUENT_*` env
   overrides, README cloud-setup docs, the `confluent` optional extra.
2. **Confluent-branded local pieces (KEEP):** `confluentinc/cp-kafka` /
   `cp-zookeeper` docker images and the `confluent-kafka` Python client in
   `docker/game-consumer` + `docker/alerts-consumer` — these ARE the local
   Kafka stack and are untouched.

## Changes

- `scripts/publisher.py`: delete `ConfluentPublisher`, `_TOPIC_MAP`, and
  `FanoutPublisher` (unreachable from `make_publisher` once only JSONL/Noop
  remain). `make_publisher` returns `JSONLPublisher` when `telemetry_dir`
  is set, else `NoopPublisher`. Update module docstring to local-first
  wording (local JSONL → local Kafka, no cloud graduation).
- `scripts/config.py`: drop the `telemetry.confluent` defaults and the
  `_apply_env_overrides` / `_is_truthy` machinery (they exist only for
  `CONFLUENT_*`). `load_config` = defaults + optional TOML deep-merge.
- `config.toml.example`: remove the `[telemetry.confluent]` block and
  `CONFLUENT_*` env docs; keep `[telemetry] dir`.
- `pyproject.toml`: remove the `confluent` optional extra; refresh
  `uv.lock`.
- Docstring/comment mentions scrubbed: `scripts/agent.py` publisher
  comment, `scripts/game_events.py` collector docstring,
  `scripts/historical_observer.py` module docstring.
- `README.md`: remove the Confluent Cloud setup section and
  `uv sync --extra confluent`; local Kafka/Flink docs stay.
- Tests: delete ConfluentPublisher / Fanout / env-override /
  confluent-enabled factory tests; keep and, where needed, extend
  JSONL/Noop/factory coverage (100% gate enforces no dead code remains).

## Explicitly untouched

- `docker-compose.yml` (cp-kafka, cp-zookeeper, flink, consumers).
- `docker/*/consumer.py` and their tests (stubbed `confluent_kafka`).
- `docs/talk-demo-outline.md` — narrative doc for the talk; its "live
  Confluent" beats are a content decision for the talk owner, flagged in
  the PR rather than edited.

## Testing

`uv run pytest --cov` (100% enforced), `uv run ruff check .` and
`format --check`, plus `uv lock` consistency.
