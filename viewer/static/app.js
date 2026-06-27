const API = "";
let frames = [], feed = [], runId = null, idx = 0, timer = null;
const active = new Set(["milestone", "telemetry", "observation", "anomaly"]);

async function loadRuns() {
  const { runs } = await (await fetch(`${API}/api/runs`)).json();
  if (runs.length) selectRun(runs[0].run_id);
}

async function selectRun(id) {
  runId = id;
  const detail = await (await fetch(`${API}/api/runs/${id}`)).json();
  frames = detail.frames;
  feed = (await (await fetch(`${API}/api/runs/${id}/feed`)).json()).feed;
  idx = 0;
  renderFeed();
  showFrame(idx);
  play();
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

function play() {
  stop();
  timer = setInterval(() => {
    if (idx >= frames.length - 1) return stop();
    showFrame(idx + 1);
  }, 300);
}
function stop() { if (timer) clearInterval(timer); timer = null; }

document.addEventListener("DOMContentLoaded", () => {
  document.getElementById("scrub").addEventListener("input", e => { stop(); showFrame(+e.target.value); });
  document.querySelectorAll(".chip").forEach(c =>
    c.addEventListener("click", () => {
      c.classList.toggle("off");
      active.has(c.dataset.kind) ? active.delete(c.dataset.kind) : active.add(c.dataset.kind);
      renderFeed();
    }));
  loadRuns();
});
