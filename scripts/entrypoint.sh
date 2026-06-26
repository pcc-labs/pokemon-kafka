#!/usr/bin/env bash
set -euo pipefail

cd /workspace

echo "[entrypoint] Installing dependencies..."
bash scripts/install.sh

export LD_LIBRARY_PATH="${HOME}/.nix-profile/lib:${LD_LIBRARY_PATH:-}"
export PATH="/usr/local/bin:${PATH}"

echo "[entrypoint] Starting Kafka..."
docker compose up -d kafka

echo "[entrypoint] Waiting for Kafka to be ready..."
until docker compose exec -T kafka kafka-topics --bootstrap-server kafka:29092 --list >/dev/null 2>&1; do
    sleep 2
done
echo "[entrypoint] Kafka ready."

# LLM session recording is handled by Paper (paperd), not a local proxy.
# paperd runs on the host; the agent reaches it via ANTHROPIC_BASE_URL.
if [ -z "${ANTHROPIC_BASE_URL:-}" ]; then
    echo "[entrypoint] WARNING: ANTHROPIC_BASE_URL is not set."
    echo "[entrypoint] Run 'paper init' on the host and ensure paperd is running"
    echo "[entrypoint] before starting agents, so sessions are recorded by Paper."
else
    echo "[entrypoint] Paper proxy: ${ANTHROPIC_BASE_URL}"
fi

echo "[entrypoint] Starting telemetry services (Kafka consumers + Flink)..."
docker compose up -d

echo "[entrypoint] Launching agent..."
~/venv/bin/python3 scripts/agent.py rom/pokemon_red.gb --strategy heuristic --max-turns 500000
