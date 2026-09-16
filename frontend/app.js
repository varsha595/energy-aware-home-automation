const API = "";
let lastSeq = 0;
let energyHistory = []; // [{minute, total}]
let paused = false;

const el = (id) => document.getElementById(id);

async function post(path, body) {
  const res = await fetch(API + path, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body || {}),
  });
  return res.json();
}

async function poll() {
  try {
    const res = await fetch(`/api/state?since=${lastSeq}`);
    const s = await res.json();
    render(s);
  } catch (e) {
    console.error("poll failed", e);
  }
  setTimeout(poll, 500);
}

function render(s) {
  // Clock + price badge
  el("clock").textContent = s.clock;
  const badge = el("price-badge");
  badge.textContent = s.is_peak ? `peak x${s.price_multiplier}` : `off-peak x${s.price_multiplier}`;
  badge.className = "badge" + (s.is_peak ? "" : " offpeak");

  // Rooms
  const roomsDiv = el("rooms");
  roomsDiv.innerHTML = "";
  for (const r of s.rooms) {
    const card = document.createElement("div");
    card.className = "room-card" + (r.dirty ? " dirty" : "") + (r.clean ? " clean" : "") + (r.robot_here ? " robot-here" : "");
    card.innerHTML = `
      <div class="room-name">${r.robot_here ? '<span class="robot-icon">🤖</span> ' : ""}${r.name}</div>
      <div class="room-status">${r.clean ? "clean" : r.dirty ? "dirty" : "—"}</div>
    `;
    roomsDiv.appendChild(card);
  }

  // Washer
  const washerDiv = el("washer-loads");
  washerDiv.innerHTML = "";
  if (s.loads.length === 0) {
    washerDiv.innerHTML = '<span class="hint">No loads</span>';
  }
  for (const l of s.loads) {
    const steps = [l.loaded, l.detergent_added, l.wash_done, l.dry_done];
    const labels = ["loaded", "detergent", "washed", "dried"];
    const dots = steps.map((done, i) => {
      const isNextIncomplete = !done && steps.slice(0, i).every(Boolean);
      const cls = done ? "done" : isNextIncomplete ? "active" : "";
      return `<span class="step-dot ${cls}" title="${labels[i]}"></span>`;
    }).join("");
    const row = document.createElement("div");
    row.className = "load-row";
    row.innerHTML = `<span class="load-name">${l.name}</span>${dots}${l.dry_done ? " ✅" : ""}`;
    washerDiv.appendChild(row);
  }

  // Dishwasher
  const dw = s.dishwasher;
  const dwSteps = [dw.loaded, dw.detergent_added, dw.clean];
  const dwLabels = ["loaded", "detergent", "washed"];
  const dwDots = dwSteps.map((done, i) => {
    const isNextIncomplete = !done && dwSteps.slice(0, i).every(Boolean);
    const cls = done ? "done" : isNextIncomplete ? "active" : "";
    return `<span class="step-dot ${cls}" title="${dwLabels[i]}"></span>`;
  }).join("");
  el("dishwasher-body").innerHTML = dw.dirty || dw.clean
    ? `<div class="load-row"><span class="load-name">Dishes</span>${dwDots}${dw.clean ? " ✅" : ""}</div>`
    : '<span class="hint">Nothing to wash</span>';

  // Budget bar
  const used = s.budget_per_window - s.budget_remaining;
  const pct = Math.max(0, Math.min(100, (used / s.budget_per_window) * 100));
  el("budget-used").textContent = used.toFixed(0);
  el("budget-total").textContent = s.budget_per_window;
  el("budget-bar-fill").style.width = pct + "%";
  el("total-energy").textContent = s.total_energy_used;
  el("replan-count").textContent = s.replan_count;

  // Energy history for chart
  const last = energyHistory[energyHistory.length - 1];
  if (!last || last.total !== s.total_energy_used) {
    energyHistory.push({ minute: s.sim_minute, total: s.total_energy_used });
    if (energyHistory.length > 300) energyHistory.shift();
    drawEnergyChart();
  }

  // Timeline
  renderTimeline(s);

  // Events (only new ones)
  if (s.events.length) {
    for (const e of s.events) appendLogEntry(e);
    lastSeq = s.latest_seq;
  }

  // Pause button label
  el("pause-btn").textContent = s.running ? "Pause" : "Resume";
  paused = !s.running;

  if (s.done) showBanner("All goals satisfied 🎉", "done");
  else if (s.failed) showBanner("Planner could not find a feasible plan", "failure");
}

function renderTimeline(s) {
  const tl = el("timeline");
  tl.innerHTML = "";
  const doneSteps = s.plan_done.map((st) => ({ ...st, state: "done" }));
  const upcoming = s.plan_upcoming.map((st, i) => ({ ...st, state: i === 0 ? "active" : "upcoming" }));
  for (const st of [...doneSteps, ...upcoming]) {
    const div = document.createElement("div");
    div.className = "timeline-step " + st.state;
    div.innerHTML = `
      <div class="ts-agent">${st.agent}</div>
      <div class="ts-name">${st.name}</div>
      <div class="ts-meta">t=${st.start_minute} · ${st.duration}min · ${st.cost.toFixed ? st.cost.toFixed(1) : st.cost}u</div>
    `;
    tl.appendChild(div);
  }
  if (doneSteps.length === 0 && upcoming.length === 0) {
    tl.innerHTML = '<span class="hint">No plan yet</span>';
  }
}

const KIND_LABEL = {
  action: "action", wait: "wait", replan: "REPLAN", failure: "FAILURE",
  "fault-armed": "fault armed", "new-goal": "NEW GOAL", "price-spike": "PRICE SPIKE", done: "DONE",
};

function appendLogEntry(e) {
  const log = el("event-log");
  const div = document.createElement("div");
  div.className = `log-entry kind-${e.kind}`;
  const clockStr = formatMinute(e.sim_minute);
  div.innerHTML = `<span class="log-time">${clockStr}</span><span>[${KIND_LABEL[e.kind] || e.kind}] ${e.message}</span>`;
  log.appendChild(div);
  while (log.children.length > 200) log.removeChild(log.firstChild);
}

function formatMinute(m) {
  const tod = m % (24 * 60);
  const h = Math.floor(tod / 60), mm = tod % 60;
  return `${String(h).padStart(2, "0")}:${String(mm).padStart(2, "0")}`;
}

function showBanner(text, kind) {
  if (document.getElementById("done-banner")) return;
  const b = document.createElement("div");
  b.id = "done-banner";
  b.textContent = text;
  b.style.cssText = `position:fixed;bottom:20px;left:50%;transform:translateX(-50%);
    background:${kind === "done" ? "#16a34a" : "#dc2626"};color:white;padding:12px 22px;
    border-radius:999px;font-weight:700;box-shadow:0 6px 20px rgba(0,0,0,0.2);z-index:50;`;
  document.body.appendChild(b);
}

function drawEnergyChart() {
  const canvas = el("energy-chart");
  const ctx = canvas.getContext("2d");
  const dpr = window.devicePixelRatio || 1;
  const w = canvas.clientWidth, h = canvas.clientHeight || 160;
  canvas.width = w * dpr;
  canvas.height = h * dpr;
  ctx.scale(dpr, dpr);
  ctx.clearRect(0, 0, w, h);

  if (energyHistory.length < 1) return;

  const budget = 800;
  const maxVal = Math.max(budget, ...energyHistory.map((p) => p.total)) * 1.1;
  const padding = { l: 40, r: 10, t: 10, b: 20 };
  const plotW = w - padding.l - padding.r;
  const plotH = h - padding.t - padding.b;

  const minMinute = energyHistory[0].minute;
  const maxMinute = Math.max(energyHistory[energyHistory.length - 1].minute, minMinute + 1);

  const x = (m) => padding.l + ((m - minMinute) / (maxMinute - minMinute)) * plotW;
  const y = (v) => padding.t + plotH - (v / maxVal) * plotH;

  // axes
  ctx.strokeStyle = "#e5e7eb";
  ctx.lineWidth = 1;
  ctx.beginPath();
  ctx.moveTo(padding.l, padding.t);
  ctx.lineTo(padding.l, padding.t + plotH);
  ctx.lineTo(padding.l + plotW, padding.t + plotH);
  ctx.stroke();

  // budget reference line (per-window, shown as flat reference for scale)
  ctx.strokeStyle = "#9ca3af";
  ctx.setLineDash([4, 4]);
  ctx.beginPath();
  ctx.moveTo(padding.l, y(budget));
  ctx.lineTo(padding.l + plotW, y(budget));
  ctx.stroke();
  ctx.setLineDash([]);
  ctx.fillStyle = "#6b7280";
  ctx.font = "10px sans-serif";
  ctx.fillText("budget/window", padding.l + 4, y(budget) - 4);

  // energy line
  ctx.strokeStyle = "#2563eb";
  ctx.lineWidth = 2;
  ctx.beginPath();
  energyHistory.forEach((p, i) => {
    const px = x(p.minute), py = y(p.total);
    if (i === 0) ctx.moveTo(px, py); else ctx.lineTo(px, py);
  });
  ctx.stroke();

  // fill under line
  ctx.lineTo(x(energyHistory[energyHistory.length - 1].minute), y(0));
  ctx.lineTo(x(minMinute), y(0));
  ctx.closePath();
  ctx.fillStyle = "rgba(37,99,235,0.08)";
  ctx.fill();

  // y-axis label
  ctx.fillStyle = "#6b7280";
  ctx.fillText(maxVal.toFixed(0), 4, padding.t + 8);
  ctx.fillText("0", 4, padding.t + plotH);
}

// Controls
el("pause-btn").addEventListener("click", async () => {
  if (paused) await post("/api/resume"); else await post("/api/pause");
});
el("reset-btn").addEventListener("click", async () => {
  energyHistory = [];
  lastSeq = 0;
  el("event-log").innerHTML = "";
  const banner = document.getElementById("done-banner");
  if (banner) banner.remove();
  await post("/api/reset");
});
el("fault-btn").addEventListener("click", () => post("/api/trigger_fault"));
el("load-btn").addEventListener("click", () => post("/api/add_load"));
el("room-btn").addEventListener("click", () => {
  const candidates = ["LivingRoom", "Bathroom", "LaundryRoom", "Kitchen", "Bedroom"];
  const room = candidates[Math.floor(Math.random() * candidates.length)];
  post("/api/add_dirty_room", { room });
});
el("spike-btn").addEventListener("click", () => post("/api/price_spike", { amount: 400 }));
el("speed").addEventListener("input", (e) => post("/api/speed", { delay_seconds: parseFloat(e.target.value) }));

window.addEventListener("resize", drawEnergyChart);

poll();
