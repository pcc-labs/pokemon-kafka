# Live Viewer Detail: Decision Transcript + Agent State Panel

**Date:** 2026-07-19
**Status:** Approved

## Goal

Bring the `--live` viewer (and replay) up to exo-dashboard-level detail: a
timestamped decision/action transcript, an AGENT STATE side panel, and header
stats — while keeping the Pokédex clamshell visual identity. The agent is
heuristic, so "transcript" means per-turn decisions and button presses (not LLM
chat) and there is no dollar cost; turn count, strategy tier, notes.md memory,
stuck streak, and current route/goal are the equivalents.

Reference: the exo dashboard layout — game frame left, timestamped transcript
center (`action {"buttons": ["right"]}` lines interleaved with reasoning), and
an AGENT STATE panel right (TOOLS / POLICY / MEMORY / PLAN).

## Decisions Made

- **Scope:** all three layers — transcript, agent-state panel, header stats.
- **Persistence:** live + replay. New detail flows through the existing
  `GameEventCollector → RunRecorder → events.jsonl + WebSocket` path so
  scrubbing a finished run shows the same panes. No second data path.
- **Look:** keep the Pokédex skin. Extend the clamshell rather than adopting
  exo's dark terminal aesthetic.
- **Data source:** new event types, not viewer-side synthesis (reasoning,
  plan, and memory only exist inside the agent) and not a sidecar state file
  (live-only, racy, second path to maintain).

## Event Schema (`scripts/game_events.py`)

Two new builders using the existing `_envelope` (which already carries
`occurred_at` — transcript timestamps come from it, no envelope change):

```python
build_decision_event(turn, mode, reason, buttons)
# event_type "decision"
# data: {"mode": "overworld" | "battle" | "menu" | "dialogue",
#        "reason": "pilot to staircase (7,1) on map 38",
#        "buttons": ["right"]}

build_agent_state_event(turn, tier, goal, route_waypoints, stuck_streak,
                        notes_excerpt, party, position, battles_won,
                        maps_visited)
# event_type "agent_state"
# data: snapshot of the agent's believed state; notes_excerpt is the first
#       ~500 chars of notes.md (empty string for tier "low" / no notes)
```

`GameEventCollector` gains `decision(...)` and `agent_state(...)` methods
following the existing builder-thin-wrapper pattern.

## Agent Instrumentation (`scripts/agent.py`)

- Call `collector.decision(...)` at existing choice points with a one-line
  human-readable reason: overworld pilot step (goal tile + chosen direction),
  battle move pick, stuck recovery, dialogue/menu handling. One decision event
  per acted turn.
- Call `collector.agent_state(...)` on the frame-interval cadence (same turns
  as `tick()` captures frames) **plus** on map change, stuck-streak change,
  and party change.
- No changes to `RunRecorder` or `LiveProducer` — both new event types ride
  the existing `on_event` path (persisted to events.jsonl, streamed as
  `{"type": "event", ...}`).

## Viewer

**Feed/store (`viewer/feed.py`, `viewer/store.py`):** include the new event
types in the feed API. `agent_state` snapshots are returned in-order so the
client can select the latest snapshot ≤ the current scrub turn (same pattern
as `currentFeedEntryIndex`).

**`viewer/static/` (index.html, app.js, style.css):**

- **Transcript** — the right clamshell page: `decision` events interleaved
  with the existing feed as monospace lines,
  `T70 14:17:26 ▸ right — pilot to staircase`, with a new `decision` filter
  chip alongside milestone/telemetry/observation/anomaly.
- **AGENT STATE panel** — a third column styled as a Pokédex flip-out panel:
  POLICY (strategy tier), PLAN (goal + route waypoints), MEMORY (notes
  excerpt), stuck streak, party. On scrub/replay it shows the latest snapshot
  ≤ current turn; during live it updates as snapshots arrive.
- **Header stats** — top bar: run id · tier · Turn N · ⚔️ battles won ·
  🗺️ maps visited. Live values come from the latest `agent_state`; replay
  values track the scrub position.

## Compatibility & Error Handling

- Old runs without the new events render exactly as today; the AGENT STATE
  panel shows "no agent state".
- Unknown event types remain ignored by `kindForEvent` (returns null).
- Live sends stay best-effort: `LiveProducer.send` never blocks gameplay.

## Testing

- pytest: new builders (envelope shape, field passthrough), collector
  methods, feed API inclusion of new types and snapshot ordering.
- Extend the existing `--live` agent test (`tests/test_agent.py`) to assert
  `decision` and `agent_state` events land in events.jsonl.
- Viewer JS has no test infra; verify manually via `uv run python -m viewer`
  against a recorded run and a live run.
- Lint: `uv run ruff check .` and `uv run ruff format --check .` before
  commit (git hooks enforce).

## Out of Scope

- LLM transcript/cost display (agent is heuristic; no $ metric exists).
- Exo-style dark dashboard theme.
- JS test infrastructure.
