const API = "";
let frames = [], feed = [], runId = null, idx = 0, timer = null, liveWs = null;
const active = new Set(["milestone", "telemetry", "observation", "anomaly"]);

function kindForEvent(et) {
  if (et === "milestone" || et === "map_change") return "milestone";
  if (et === "battle" || et === "overworld" || et === "stuck") return "telemetry";
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
  return et || "event";
}

function closeLive() {
  if (liveWs) { liveWs.close(); liveWs = null; }
}

async function showGrid() {
  stop();
  closeLive();
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
    tile.addEventListener("click", () => { document.body.dataset.view = "focus"; selectRun(r.run_id); });
    g.appendChild(tile);
  });
  document.body.dataset.view = "grid";
}

async function selectRun(id) {
  runId = id;
  closeLive();
  const detail = await (await fetch(`${API}/api/runs/${id}`)).json();
  frames = detail.frames;
  feed = (await (await fetch(`${API}/api/runs/${id}/feed`)).json()).feed;
  idx = 0;
  renderFeed();
  showFrame(idx);
  if (detail.status === "live") {
    const proto = location.protocol === "https:" ? "wss:" : "ws:";
    liveWs = new WebSocket(`${proto}//${location.host}/ws/live/${id}`);
    liveWs.onmessage = (e) => {
      const msg = JSON.parse(e.data);
      if (msg.type === "done") {
        closeLive();
      } else if (msg.type === "event") {
        const kind = kindForEvent(msg.event_type);
        if (kind !== null) {
          feed.push({ kind, turn: msg.turn, text: textForEvent(msg) });
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
  feed.filter(e => active.has(e.kind)).forEach(e => {
    const li = document.createElement("li");
    li.className = `entry ${e.kind}`;
    li.textContent = `T${e.turn} [${e.kind}] ${e.text}`;
    ul.appendChild(li);
  });
}

function showFrame(i) {
  if (!frames.length) return;
  idx = Math.max(0, Math.min(i, frames.length - 1));
  document.getElementById("screen").src = `${API}/runs/${runId}/frames/${frames[idx]}`;
  document.getElementById("scrub").value = idx;
  document.getElementById("scrub").max = frames.length - 1;
}

// Playback speed (ms per frame). Higher = slower. Tune live with [ and ] keys.
let frameDelay = 650;
function play() {
  stop();
  timer = setInterval(() => {
    // Loop back to the start instead of stopping — keeps the screen animating
    // continuously while presenting.
    showFrame(idx >= frames.length - 1 ? 0 : idx + 1);
  }, frameDelay);
}
function stop() { if (timer) clearInterval(timer); timer = null; }
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
      c.classList.toggle("off");
      active.has(c.dataset.kind) ? active.delete(c.dataset.kind) : active.add(c.dataset.kind);
      renderFeed();
    }));
  showGrid();
});
