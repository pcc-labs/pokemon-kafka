# Pokédex Viewer — Design

**Date:** 2026-06-26
**Status:** Approved (design); pending spec review
**Branch:** `pokedex-viewer`

## Summary

A self-contained web app that visualizes Pokémon agent runs as Pokédex-styled
"streaming content": live gameplay (or replay) in the device's screen, with a
merged, time-ordered feed of observations and summaries scrolling in the entry
panel beside it. Built as a pure-Python package served by FastAPI with a
no-build static frontend, so it runs identically on Ubuntu and macOS.

## Goals

- Watch an agent play in real time, and replay any past run on demand.
- Present runs in a Pokédex-skinned UI: gameplay "screen" + scrolling "entry" panel.
- Merge four content sources into one filterable feed: milestones, live game
  telemetry, LLM observations/summaries, and anomaly alerts.
- Two views: a **grid** of runs (overview) and a **focused** single-run view.
- Run on Ubuntu and macOS with one toolchain (Python/`uv`), no build step.
- Demo and test **without a ROM or a live agent** via a synthetic fixture run.

## Non-Goals (deferred)

- Authentication, multi-user accounts, access control.
- A persistent database — the filesystem (`runs/`) is the store.
- MP4/video transcoding — PNG frames + JS playback are sufficient.
- Dependence on the Kafka/Flink stack — anomaly/observation sources are read
  from files when present and degrade gracefully when absent.

## Constraints

- **Cross-platform:** Ubuntu + macOS, verified the same way on both.
- **Toolchain:** Python via `uv` (matches repo `AGENTS.md`). No npm/Node.
- **Frontend:** vanilla HTML/CSS/JS, no build pipeline.
- **Decoupling:** must work when Kafka, Flink, and the observer are all down.

## Architecture

A new `viewer/` package, served by FastAPI + uvicorn (added to `uv` deps). It
serves one Pokédex-skinned page plus a small REST + WebSocket API. The same UI
drives two modes:

- **Replay** — REST endpoints read recorded runs from disk.
- **Live** — a WebSocket pushes frames + events from a running agent; on run
  end the same on-disk run becomes immediately replayable.

Launch: `uv run viewer` starts the server and opens the browser.

### Per-run recorder (additive change to the agent)

Today frames are sparse and game events are written to **per-day** JSONL files,
so there is no clean per-run artifact to replay. We add an **opt-in** recorder
(`agent --record`) that writes a self-contained run directory:

```
runs/<run_id>/
  frames/000123.png    # captured at a steady cadence (configurable interval)
  events.jsonl         # milestones + telemetry + map changes, keyed by turn and timestamp
  summary.json         # final fitness, badges, maps visited, battles won, params
```

- Off by default; does not alter existing agent behavior when not set.
- `run_id` is generated at agent start (timestamp + short random suffix).
- This directory is the **source of truth** for replay.

### Components (each isolated, single-purpose, independently testable)

| Module | Responsibility | Depends on |
|--------|----------------|------------|
| `viewer/recorder.py` | Write `runs/<id>/{frames,events.jsonl,summary.json}` from agent callbacks at a fixed cadence | agent hooks, filesystem |
| `viewer/store.py` | Read-only run index: list runs, load one run's events, frame list, summary | filesystem (`runs/`) |
| `viewer/feed.py` | Merge the 4 sources into one time-ordered, `kind`-tagged feed; read `observations.md` + alerts memory file when present | `store`, optional files |
| `viewer/live.py` | In-memory pub/sub: accept a producer (the running agent), fan out to WS subscribers | asyncio |
| `viewer/server.py` | FastAPI app: REST + static mounts + `/ws/live/{id}` | `store`, `feed`, `live` |
| `viewer/static/` | `index.html` (Pokédex chrome), `app.js` (views, playback, feed+filters, scrubber), `style.css` | — |

### API surface

- `GET /api/runs` → list of run summaries (id, status live/done, stats, thumbnail ref)
- `GET /api/runs/{id}` → run metadata + frame manifest + summary
- `GET /api/runs/{id}/feed` → merged, time-ordered feed entries (each tagged `kind`)
- `GET /frames/{id}/{frame}` → static frame image
- `WS /ws/live/{id}` → live frames + feed entries pushed to subscribed browsers
- Producer path (agent → server, localhost): WS or HTTP POST of frames + events into `live.py`

### Feed model

Each feed entry is a typed record:

```json
{ "ts": "...", "turn": 123, "kind": "milestone|telemetry|observation|anomaly",
  "text": "Won rival battle", "data": { ... } }
```

- `milestone` — from `milestone` + `map_change` events (play-by-play beats)
- `telemetry` — from `battle` / `overworld` / `stuck` events (HP, position, stuck counts)
- `observation` — from `pokedex/memory/observations.md` and/or per-run summary
- `anomaly` — from the alerts memory file (Flink output) when present

The client renders the feed with filter chips toggling each `kind`.

## Views

- **Grid** — tiles of runs; live runs pulse. Each tile: latest-frame thumbnail +
  mini status (turn, badges, live/done). Click → focused view.
- **Focused** — the Pokédex device: gameplay in the screen; the merged feed
  scrolling in the entry panel with filter chips; playback controls + scrubber
  (replay) or live-follow (live); run stats panel (badges, maps, wins, fitness).

## Data Flow

- **Replay:** browser → `GET /api/runs` (grid) → pick → `GET /api/runs/{id}` +
  `/feed` → frames play by timestamp/turn, feed scrolls in sync; scrubber seeks
  both together.
- **Live:** `agent --record --live` → streams frames + events to the server →
  `live.py` fans out over `/ws/live/{id}` → browser renders in real time. Run
  end flushes `runs/<id>/`, which is then a normal replayable run.

## Error Handling & Degradation

- Missing `observations.md` or alerts file → those `kind`s simply absent; feed
  still renders milestones + telemetry.
- Missing/sparse frames → playback shows last available frame; no crash.
- Corrupt JSONL line → skipped with a logged warning (mirrors Flink's
  `json.ignore-parse-errors`).
- No `runs/` directory → grid shows an empty state with instructions.
- Live producer disconnects → run marked `done` from last known state.

## Testing

- Unit tests (pytest, repo's coverage norm) for:
  - `store.py` — run discovery, ordering, partial/missing files
  - `feed.py` — merge ordering across sources, graceful degradation when a
    source is absent, `kind` tagging
  - `recorder.py` — writes the expected directory layout and cadence
- A **synthetic fixture run** (committed fake frames + events + summary) so the
  viewer demos and the full test suite pass with no ROM and no live agent.
- Server smoke test via FastAPI `TestClient` for each REST route + a WS round-trip.

## Build Order (phased)

1. **Replay core** — `recorder`, `store`, `feed`, `server` REST, focused
   Pokédex view, merged feed + filters, fixture-run demo. *(No ROM needed.)*
2. **Grid overview** — run tiles + thumbnails + navigation to focused view.
3. **Live streaming** — `live.py` pub/sub, `/ws/live/{id}`, agent `--live`
   producer, client live-follow.

## Open Questions

- None blocking. Frame cadence default (e.g. every N turns or every M ms) to be
  fixed during planning; chosen to balance replay smoothness vs. disk use.

## Follow-ups / Known limitations

Captured from implementation (PR #29) and its reviews. None block the replay or
live happy path; listed so they aren't lost to the conversation.

**Deferred features (not yet built):**

- **Grid live-refresh + "pulse"** — the grid renders once on load and does not
  poll `/api/runs`, so a run that starts/finishes while the grid is open does
  not appear or update without a manual reload. The design's "live runs pulse"
  styling is also not wired. Add a periodic `/api/runs` poll and a `.live`
  pulse style.
- **`LiveProducer` reconnect backoff** — `send()` resets the socket on any
  failure and reconnects lazily on the next call (`scripts/live_producer.py`).
  A flaky-but-reachable viewer can trigger a blocking `connect()` on the game
  loop per event. Add a consecutive-failure cap / short backoff and a small
  `open_timeout`.

**Open review findings (PR #29, not yet addressed):**

- **`/ws/produce` does not catch `JSONDecodeError`** (`viewer/server.py`) — a
  non-JSON frame from any client kills the produce coroutine and strands that
  run's subscribers. Our own `LiveProducer` only sends valid JSON, so this is a
  robustness gap, not a live bug.
- **`selectRun` rapid-click race** (`viewer/static/app.js`) — sets `runId` then
  awaits; clicking run A then B can let A's slower response overwrite `frames`
  while `runId === "B"`, yielding 404 frame requests / a blank screen. Guard
  with a per-call token that bails if `runId` changed after the await.
- **Observations sort to the top of the feed** (`viewer/feed.py`) —
  observations get `turn=0` (anomalies often too) and the feed sorts by `turn`,
  so distilled summaries render before the gameplay that produced them. Stamp
  them with the turn they describe, or append rather than sort-to-front.
- **Client/server feed-logic duplication** (`viewer/static/app.js` vs
  `viewer/feed.py`) — `kindForEvent`/`textForEvent` re-derive the server's
  `_event_text` + kind taxonomy (two sources of truth; already diverge on
  `stuck` formatting). Consider having the server publish already-mapped
  `FeedEntry`-shaped messages over the live socket.

**Resolved in PR #29 review (for the record):** live runs now emit a `done`
signal (subscribers no longer hang); `agent.run()` is wrapped in `try/finally`
so `--record` runs always finalize; observation/alerts paths are passed eagerly
so files written after startup are picked up; the live socket derives
`ws`/`wss` from the page protocol.
