"""JSONL → Kafka bridge — tails the game-event sink and produces to Kafka.

The agent stays broker-free: it writes pokemon.game.v1 events to
data/telemetry/game/*.jsonl via scripts/publisher.py. This service tails
those files and produces each complete line to the local topic, so the
Kafka + Flink stack lights up live while the agent runs.

Delivery is at-least-once: byte offsets are persisted after each produced
batch, so a crash between produce and save can re-send a tail of events.
With FROM_BEGINNING=1 (default) an empty state replays the whole sink —
first start doubles as the demo seeder.
"""

import json
import os
import signal
import time
from pathlib import Path

from confluent_kafka import Producer

TOPIC = os.environ.get("KAFKA_TOPIC", "agent.game.events")
BOOTSTRAP = os.environ.get("KAFKA_BOOTSTRAP_SERVERS", "kafka:29092")
TELEMETRY_DIR = os.environ.get("TELEMETRY_DIR", "/telemetry")
STATE_FILE = os.environ.get("STATE_FILE", "/state/offsets.json")
POLL_MS = int(os.environ.get("POLL_MS", "500"))
FROM_BEGINNING = os.environ.get("FROM_BEGINNING", "1") == "1"


def scan(telemetry_dir) -> list[Path]:
    """Sorted *.jsonl files — date-partitioned names, so sorted == chronological."""
    return sorted(Path(telemetry_dir).glob("*.jsonl"))


def read_new_lines(path: Path, offset: int) -> tuple[list[str], int]:
    """Read complete lines from byte *offset*; a partial trailing line stays unread."""
    with open(path, "rb") as f:
        f.seek(offset)
        chunk = f.read()
    last_newline = chunk.rfind(b"\n")
    if last_newline < 0:
        return [], offset
    complete = chunk[: last_newline + 1]
    lines = [line for line in complete.decode("utf-8").split("\n") if line.strip()]
    return lines, offset + last_newline + 1


def load_state(path) -> dict:
    """{filename: byte_offset}; missing or corrupt state starts fresh."""
    try:
        return json.loads(Path(path).read_text())
    except (OSError, json.JSONDecodeError):
        return {}


def save_state(path, state: dict) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(state))


def initial_state(telemetry_dir, from_beginning: bool) -> dict:
    """Empty state replays the sink; otherwise start tailing at end-of-file."""
    if from_beginning:
        return {}
    return {p.name: p.stat().st_size for p in scan(telemetry_dir)}


def run_once(producer, topic: str, telemetry_dir, state: dict) -> dict:
    """Produce all new complete lines across the sink; return updated offsets."""
    state = dict(state)
    for path in scan(telemetry_dir):
        lines, new_offset = read_new_lines(path, state.get(path.name, 0))
        for line in lines:
            try:
                key = json.loads(line).get("schema", "")
            except json.JSONDecodeError:
                print(f"[bridge] skipping unparseable line in {path.name}", flush=True)
                continue
            producer.produce(topic, key=key.encode("utf-8"), value=line.encode("utf-8"))
        if lines:
            producer.poll(0)
        state[path.name] = new_offset
    return state


def main():
    print(f"[bridge] {TELEMETRY_DIR} -> {BOOTSTRAP} topic={TOPIC}", flush=True)
    producer = Producer({"bootstrap.servers": BOOTSTRAP})
    state = load_state(STATE_FILE) or initial_state(TELEMETRY_DIR, FROM_BEGINNING)

    running = True

    def _stop(signum, frame):
        nonlocal running
        running = False

    signal.signal(signal.SIGTERM, _stop)
    signal.signal(signal.SIGINT, _stop)

    while running:
        new_state = run_once(producer, TOPIC, TELEMETRY_DIR, state)
        if new_state != state:
            state = new_state
            save_state(STATE_FILE, state)
        time.sleep(POLL_MS / 1000)

    producer.flush(timeout=10)
    save_state(STATE_FILE, state)
    print("[bridge] stopped, state saved", flush=True)


if __name__ == "__main__":
    main()
