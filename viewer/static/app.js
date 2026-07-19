const API = "";
let frames = [], feed = [], states = [], runId = null, idx = 0, timer = null, liveWs = null;
let isolated = null; // null = show every kind; otherwise show only this one

function kindForEvent(et) {
  if (et === "milestone" || et === "map_change") return "milestone";
  if (
    et === "battle" || et === "overworld" || et === "stuck" ||
    et === "battle_end" || et === "battle_outcome" || et === "move_result"
  ) return "telemetry";
  if (et === "discovery") return "observation";
  if (et === "decision") return "decision";
  return null;
}

function textForEvent(msg) {
  const et = msg.event_type;
  const data = msg.data || {};
  if (et === "milestone") return data.description || "milestone";
  if (et === "map_change") return `Map ${data.prev_map} → ${data.new_map}`;
  if (et === "battle") return `Battle — player HP ${data.player_hp}, enemy HP ${data.enemy_hp}`;
  if (et === "overworld") {
    const pos = data.position || {};
    return `map ${data.map_id} (${pos.x},${pos.y}) ${data.action || ""}`.trim();
  }
  if (et === "stuck") return `Stuck \xd7${data.streak} at ${JSON.stringify(data.position || {})}`;
  if (et === "discovery") return data.text || "discovery";
  if (et === "battle_end") {
    const outcome = data.won ? "won" : "lost";
    return `Battle ${outcome} vs ${data.opponent_species} (Lv${data.opponent_level})`;
  }
  if (et === "battle_outcome") {
    const outcome = data.won ? "won" : "lost";
    return `Battle outcome: ${outcome} vs ${data.enemy_species} (Lv${data.enemy_level})`;
  }
  if (et === "move_result") {
    const result = data.fainted ? "enemy fainted" : `${data.damage_dealt} dmg`;
    return `${data.user_species} used ${data.move} — ${result}`;
  }
  if (et === "decision") {
    const buttons = (data.buttons || []).join("+") || "wait";
    return `▸ ${buttons} — ${data.reason || ""}`;
  }
  return et || "event";
}

function beatNumberFromLabel(label) {
  const m = /^(\d+)\s*·/.exec(label || "");
  return m ? m[1] : null;
}

function maybePushBeatRoute(label) {
  const beat = beatNumberFromLabel(label);
  if (beat && location.pathname !== `/${beat}`) {
    history.pushState({}, "", `/${beat}`);
  }
}

function closeLive() {
  if (liveWs) { liveWs.close(); liveWs = null; }
}

async function showGrid() {
  stop();
  closeLive();
  if (location.pathname !== "/") history.pushState({}, "", "/");
  const { runs } = await (await fetch(`${API}/api/runs`)).json();
  const g = document.getElementById("grid");
  g.innerHTML = "";
  runs.forEach(r => {
    const tile = document.createElement("div");
    tile.className = `tile ${r.status}`;
    const thumbnailHtml = r.thumbnail
      ? `<img src="${API}/runs/${r.run_id}/frames/${r.thumbnail}">`
      : `<div class="tile-noframe">no preview</div>`;
    const labelHtml = r.label ? `<b class="run-label">${r.label}</b><br>` : "";
    tile.innerHTML = `${thumbnailHtml}
      <div class="meta">${labelHtml}${r.run_id}<br>⚔️${r.battles_won} 🗺️${r.maps_visited}</div>`;
    tile.addEventListener("click", () => { document.body.dataset.view = "focus"; selectRun(r.run_id, r.label); });
    g.appendChild(tile);
  });
  document.body.dataset.view = "grid";
}

async function routeInitial() {
  const m = /^\/(\d+)$/.exec(location.pathname);
  if (!m) { showGrid(); return; }
  const { runs } = await (await fetch(`${API}/api/runs`)).json();
  const match = runs.find(r => beatNumberFromLabel(r.label) === m[1]);
  if (!match) { showGrid(); return; }
  document.body.dataset.view = "focus";
  await selectRun(match.run_id, match.label);
}

async function selectRun(id, label) {
  runId = id;
  closeLive();
  maybePushBeatRoute(label);
  const detail = await (await fetch(`${API}/api/runs/${id}`)).json();
  frames = detail.frames;
  feed = (await (await fetch(`${API}/api/runs/${id}/feed`)).json()).feed;
  states = (await (await fetch(`${API}/api/runs/${id}/agent_state`)).json()).states;
  idx = 0;
  renderFeed();
  showFrame(idx);
  renderStatePanel(frames.length ? turnForFrame(idx) : 0);
  if (detail.status === "live") {
    const proto = location.protocol === "https:" ? "wss:" : "ws:";
    liveWs = new WebSocket(`${proto}//${location.host}/ws/live/${id}`);
    liveWs.onmessage = (e) => {
      const msg = JSON.parse(e.data);
      if (msg.type === "done") {
        closeLive();
      } else if (msg.type === "event") {
        if (msg.event_type === "agent_state") {
          states.push({ turn: msg.turn, ts: msg.occurred_at || "", data: msg.data || {} });
          renderStatePanel(msg.turn);
          return;
        }
        const kind = kindForEvent(msg.event_type);
        if (kind !== null) {
          feed.push({ kind, turn: msg.turn, ts: msg.occurred_at || "", text: textForEvent(msg) });
          renderFeed();
        }
      } else if (msg.type === "frame") {
        document.getElementById("screen").src = `data:image/png;base64,${msg.png_b64}`;
      }
    };
  } else {
    play();
  }
}

function renderFeed() {
  const ul = document.getElementById("feed");
  ul.innerHTML = "";
  feed.forEach((e, i) => {
    if (isolated !== null && e.kind !== isolated) return;
    const li = document.createElement("li");
    li.className = `entry ${e.kind}`;
    li.dataset.feedIdx = i;
    // ts is ISO-8601 ("2026-07-19T14:17:26.000000Z") — show the HH:MM:SS slice.
    const time = e.ts ? e.ts.slice(11, 19) : "";
    li.textContent = `T${e.turn}${time ? " " + time : ""} [${e.kind}] ${e.text}`;
    li.addEventListener("click", () => { stop(); showFrame(frameIndexForTurn(e.turn)); });
    ul.appendChild(li);
  });
  highlightCurrentFeedEntry();
}

function stateForTurn(turn) {
  let best = null;
  for (const s of states) {
    if (s.turn <= turn) best = s;
    else break;
  }
  return best;
}

function renderStatePanel(turn) {
  const snap = stateForTurn(turn);
  const policy = document.getElementById("st-policy");
  if (!snap) {
    policy.textContent = "no agent state";
    ["st-plan", "st-memory", "st-status"].forEach(id => { document.getElementById(id).textContent = ""; });
    updateStatsBar(turn, null);
    return;
  }
  const d = snap.data;
  policy.textContent = `tier: ${d.tier || "?"}`;
  const wps = (d.route_waypoints || []).map(w => `(${w.x},${w.y})`).join(" → ");
  document.getElementById("st-plan").textContent = `${d.goal || "no active goal"}${wps ? "\nroute: " + wps : ""}`;
  document.getElementById("st-memory").textContent = d.notes_excerpt || "(empty)";
  const pos = d.position || {};
  document.getElementById("st-status").textContent =
    `map ${pos.map_id} (${pos.x},${pos.y})\nparty: ${d.party_count}  stuck: ${d.stuck_streak}`;
  updateStatsBar(turn, d);
}

function updateStatsBar(turn, d) {
  document.getElementById("sb-run").textContent = runId || "";
  document.getElementById("sb-tier").textContent = d ? `tier ${d.tier}` : "";
  document.getElementById("sb-turn").textContent = `Turn ${turn}`;
  document.getElementById("sb-battles").textContent = d ? `⚔️ ${d.battles_won}` : "";
  document.getElementById("sb-maps").textContent = d ? `🗺️ ${d.maps_visited}` : "";
}

function turnForFrame(i) {
  return parseInt(frames[i] || "0", 10) || 0;
}

function frameIndexForTurn(turn) {
  for (let i = 0; i < frames.length; i++) {
    if (turnForFrame(i) >= turn) return i;
  }
  return frames.length - 1;
}

function currentFeedEntryIndex(frameTurn) {
  let best = -1;
  for (let i = 0; i < feed.length; i++) {
    if (feed[i].turn <= frameTurn) best = i;
    else break;
  }
  return best;
}

function highlightCurrentFeedEntry() {
  const ul = document.getElementById("feed");
  const currentIdx = currentFeedEntryIndex(turnForFrame(idx));
  ul.querySelectorAll("li.entry").forEach(li => li.classList.remove("current"));
  if (currentIdx < 0) return;
  const li = ul.querySelector(`li[data-feed-idx="${currentIdx}"]`);
  if (li) {
    li.classList.add("current");
    li.scrollIntoView({ block: "nearest" });
  }
}

function showFrame(i) {
  if (!frames.length) return;
  idx = Math.max(0, Math.min(i, frames.length - 1));
  document.getElementById("screen").src = `${API}/runs/${runId}/frames/${frames[idx]}`;
  document.getElementById("scrub").value = idx;
  document.getElementById("scrub").max = frames.length - 1;
  const readout = document.getElementById("turn-readout");
  if (readout) readout.textContent = `Turn ${turnForFrame(idx)}`;
  highlightCurrentFeedEntry();
  renderStatePanel(turnForFrame(idx));
}

// Playback speed (ms per frame). Higher = slower. Tune live with [ and ] keys.
let frameDelay = 650;
function currentFrameDelay() {
  const currentIdx = currentFeedEntryIndex(turnForFrame(idx));
  const kind = currentIdx >= 0 ? feed[currentIdx].kind : null;
  return kind === "telemetry" ? frameDelay * 2 : frameDelay;
}
function play() {
  stop();
  scheduleNext();
}
function scheduleNext() {
  timer = setTimeout(() => {
    showFrame(idx >= frames.length - 1 ? 0 : idx + 1);
    scheduleNext();
  }, currentFrameDelay());
}
function stop() { if (timer) clearTimeout(timer); timer = null; }
function setSpeed(ms) {
  frameDelay = Math.max(80, Math.min(2000, ms));
  if (timer) play();  // restart the loop at the new speed
}

document.addEventListener("DOMContentLoaded", () => {
  document.getElementById("scrub").addEventListener("input", e => { stop(); showFrame(+e.target.value); });
  // Live playback controls: [ slower, ] faster, space = play/pause.
  document.addEventListener("keydown", e => {
    if (e.key === "[") setSpeed(frameDelay + 150);
    else if (e.key === "]") setSpeed(frameDelay - 150);
    else if (e.key === " ") { e.preventDefault(); timer ? stop() : play(); }
  });
  document.querySelectorAll(".chip").forEach(c =>
    c.addEventListener("click", () => {
      isolated = isolated === c.dataset.kind ? null : c.dataset.kind;
      document.querySelectorAll(".chip").forEach(other =>
        other.classList.toggle("off", isolated !== null && other.dataset.kind !== isolated));
      renderFeed();
    }));
  window.addEventListener("popstate", routeInitial);
  routeInitial();
});
