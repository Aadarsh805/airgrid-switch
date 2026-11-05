// AirGrid ops wall: live (API + SSE) or replay (static fixtures). No business logic here:
// coverage, ETAs, READY counts and violations arrive computed. Only the API is the truth.
import { City } from "./city.js";
import { Replay } from "./replay.js";

const $ = (id) => document.getElementById(id);
const params = new URLSearchParams(location.search);
const API = new URL("/", location.href).href;
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]);
const noCity = { reset() {}, update() {}, event() {}, toggleOrbit: () => false, direct() {}, chaseCam() {}, fps: 0 };

const S = {
  replay: null, world: null, state: null, status: null, map: null,
  events: [], privacy: [], approvals: [], landing: {},
  kpi: null, scorecard: false, sawLive: false, skipHide: 0, lastT: null,
};
const GET = async (path) => {
  const r = await fetch(API + path);
  if (!r.ok) throw new Error(`${path}: HTTP ${r.status}`);
  return r.json();
};
const POST = async (path, body) => {
  const r = await fetch(API + path, {
    method: "POST", headers: { "content-type": "application/json" },
    body: body ? JSON.stringify(body) : undefined,
  });
  const j = await r.json().catch(() => ({}));
  if (!r.ok || j.ok === false) toast(`${j.code || r.status}: ${j.reason || "request failed"}`);
  return j;
};
function toast(msg) {
  const t = $("toast");
  t.textContent = msg;
  t.hidden = false;
  clearTimeout(t._h);
  t._h = setTimeout(() => (t.hidden = true), 4000);
}
const fullClock = (c) => (c && c.length === 5 ? `${c}:00` : c || "--:--:--");

// ---------------------------------------------------------------- frames (shared by SSE + replay)
function applySnapshot(d) {
  S.state = d;
  S.lastT = d.t;
  S.city.reset();
  S.events = []; S.privacy = []; S.approvals = []; S.landing = {};
  S.kpi = null; S.sawLive = !d.done;
  setScorecard(false);
  S.city.update(d, true);
  dirty();
}
function applyTick(d, quiet = false) {
  const st = S.state;
  if (!st) return;
  for (const [k, v] of Object.entries(d)) {
    if (["drones", "docks", "pilots", "incidents"].includes(k)) st[k] = { ...st[k], ...v };
    else st[k] = v;
  }
  if (!quiet && S.lastT != null && d.t - S.lastT > 1) markSkip();
  S.lastT = d.t;
  S.city.update(st, quiet);
  if (st.done && S.sawLive) { S.sawLive = false; onShiftEnd(); }
  else if (!st.done) S.sawLive = true;
  dirty();
}
function applyEvent(e) {
  S.events.push(e);
  S.city.event(e);
  if (S.events.length > 500) S.events.shift();
}
function markSkip() {
  $("skipTag").hidden = false;
  S.skipHide = performance.now() + 4000;
}
function onShiftEnd() {
  if (S.game) return; // the game keeps its own score screen
  if (S.replay) { setScorecard(true); return; }
  // Only when this page watched the shift end; loading a finished run never writes.
  POST("baseline/run").then((j) => { if (j.managed) S.kpi = j; setScorecard(true); });
}

// ---------------------------------------------------------------- render (throttled)
let pending = false, lastRender = 0;
function dirty() {
  if (pending) return;
  pending = true;
  const wait = Math.max(0, 250 - (performance.now() - lastRender));
  setTimeout(() => { pending = false; lastRender = performance.now(); render(); }, S.replay?.seeking ? 0 : wait);
}
function render() {
  if (!S.state) return;
  renderTop(); renderBanner(); renderFleet(); renderIncidents(); renderKpi(); renderTicker(); renderFeed(); renderSectors();
  if (S.scorecard) renderScorecard();
}

function renderTop() {
  const s = S.state, st = S.status;
  $("clock").textContent = fullClock(s.clock);
  const m = /^(mon|tue|wed|thu|fri|sat|sun)/i.exec(st?.title || s.scenario === "FRIDAY-RUSH" && "Fri" || "");
  $("day").textContent = m ? m[1][0].toUpperCase() + m[1].slice(1, 3).toLowerCase() : "";
  const mode = $("modeTag");
  mode.textContent = S.replay ? "REPLAY · recorded golden run · seed DRONE-01" : `${s.mode} · ${s.scenario}`;
  mode.className = S.replay ? "tag tag-sky" : "tag";
  const run = $("runTag");
  const live = S.replay ? { on: S.replay.playing, rate: S.replay.rate } : { on: st?.runner && st?.clock_running, rate: st?.clock_sim_min_per_s };
  run.hidden = false;
  run.textContent = s.done ? "shift ended" : live.on ? `running · ${live.rate} sim-min/s` : "paused";
  for (const b of document.querySelectorAll("[data-speed]")) b.setAttribute("aria-pressed", String(Number(b.dataset.speed) === live.rate));
  if (performance.now() > S.skipHide) $("skipTag").hidden = !st?.clock_skipping;
  const btn = $("btnBaseline");
  btn.textContent = `Baseline: ${S.scorecard ? "on" : "off"}`;
  btn.setAttribute("aria-pressed", String(S.scorecard));
  if (S.replay) $("scrub").value = s.t;
}

function renderBanner() {
  const s = S.state, st = S.status, b = $("banner");
  const wait = S.approvals.filter((a) => a.status === "pending");
  let text = "", gold = false;
  if (wait.length) {
    text = `◆ WAITING FOR YOU: approve ${wait[0].id} in #dispatch${wait.length > 1 ? ` (+${wait.length - 1} more)` : ""}`;
    gold = true;
  } else if (s.done) {
    text = "";
  } else if (S.replay ? !S.replay.playing : st && !(st.runner && st.clock_running)) {
    text = st && !st.runner ? "⏸ PAUSED · press ▶ to run the shift" : "⏸ PAUSED";
  } else if (st?.clock_held?.length) {
    text = "Clock held: agents working";
  }
  b.hidden = !text;
  b.textContent = text;
  b.className = gold ? "banner gold" : "banner";
}

const place = (x) => (Array.isArray(x) ? "the reported location" : String(x ?? "").replace(/_/g, " "));
// One plain sentence per event, from the event's own payload. null = too minor for the feed.
function speak(e) {
  const p = e.payload || {};
  const pct = (b) => `${Math.round(b)}%`;
  switch (e.type) {
    case "cad_alert": return `New ${p.kind} reported at ${place(p.at)}: ${p.report}.`;
    case "launch": return `${p.drone} takes off for ${p.incident || "its mission"}.`;
    case "rotation": return `${p.drone} takes off to take over the ${p.loop} patrol${p.relieves ? ` from ${p.relieves}` : ""}.`;
    case "rtb": return `${p.drone} heads home to ${p.dock} on ${pct(p.battery)} battery.`;
    case "landing": return `${p.drone} lands at ${p.dock} with ${pct(p.battery)}.`;
    case "eyes_on": return `Eyes on ${p.incident}: ${p.drone} got there in ${Math.round(p.seconds)} seconds.`;
    case "approved": return `${p.by} approved ${p.what}${p.incident ? ` for ${p.incident}` : ""}.`;
    case "approval_needed": return `${p.incident || "A request"} needs a human decision: ${place(p.what)}.`;
    case "divert": return `${p.drone} leaves its patrol to ${p.mission} ${p.incident}.`;
    case "relay_planned": return `${p.drone} launches to relieve ${p.relieves} on the chase.`;
    case "handoff": return `Handoff: ${p.to} takes the chase from ${p.from}, gap ${p.gap_s} seconds.`;
    case "hold": return `${p.drone} stops at the ${place(p.zone)} line. Drones can't enter.`;
    case "reacquire": return `${p.drone} picks the suspect up again.`;
    case "take_control": return `${p.pilot} takes manual control of ${p.drone}.`;
    case "rejected": return `${p.pilot} is at capacity: ${p.drone} goes to ${p.fallback_pilot} (${p.code}).`;
    case "refusal": return `Refused, ${p.code}: "${p.request}".`;
    case "incident_closed": return `${p.incident} closed: ${p.note}.`;
    case "unit_tasked": return `${place(p.unit)} sent to ${p.incident}, about ${Math.ceil(p.eta_min)} min away.`;
    case "unit_on_scene": return `${place(p.unit)} arrives at ${p.incident}.`;
    case "unit_released": return `${place(p.unit)} released early, saving ${p.saves_min} unit-minutes.`;
    case "weather_grounded": return `Wind ${p.wind_ms} m/s: ${p.sectors.join(" and ")} grounded until ${p.until}.`;
    case "weather_reopened": return `Wind has dropped: ${p.sectors.join(" and ")} open again.`;
    case "dock_fault": return `Dock ${p.dock} goes offline: ${p.note}.`;
    case "dock_restored": return `Dock ${p.dock} is back online.`;
    case "slot_reroute": case "feed_fact": case "shift_brief": case "unit_available":
    case "unit_stood_down": case "unit_cleared": return null;
    default: return e.timeline ? e.text.replace(/^[^\p{L}\p{N}]+/u, "") : null;
  }
}
function renderFeed() {
  const lines = [];
  for (let i = S.events.length - 1; i >= 0 && lines.length < 6; i--) {
    const text = speak(S.events[i]);
    if (text) lines.push({ c: S.events[i].clock.slice(0, 5), text });
  }
  const [now, ...rest] = lines;
  $("feed").innerHTML = now
    ? `<p class="now"><time>${esc(now.c)}</time>${esc(now.text)}</p><ol>${rest.map((l) => `<li><time>${esc(l.c)}</time>${esc(l.text)}</li>`).join("")}</ol>`
    : `<p class="now">All quiet: patrols flying, no incidents yet.</p>`;
}

function droneStatus(d, s) {
  if (d.fault) return ["st-bad", "✖", "FAULT"];
  if (d.out_of_service) return ["st-bad", "✖", "OUT"];
  if (d.state === "airborne") {
    if (d.held || d.mission === "hold") return ["st-chg", "■", "HOLD"];
    return d.mission === "returning" ? ["st-air", "↩", "RTB"] : ["st-air", "▲", "AIR"];
  }
  const dock = s.docks[d.slot];
  if (dock && !dock.online) return ["st-off", "⊘", "DOCK OFF"];
  return d.battery < 100 ? ["st-chg", "⚡", "CHARGING"] : ["st-ready", "●", "DOCKED"];
}
function missionText(d) {
  if (d.state !== "airborne") return "";
  const land = S.landing[d.id];
  if (d.mission === "returning") return `RTB${land ? ` · lands ${Math.round(land.lands_pct)}% ${land.lands_at.slice(0, 5)}` : ""}`;
  const where = d.loop || d.incident || "";
  const name = d.mission === "incident" ? "on scene" : d.mission;
  return `${name ?? ""} ${where}`.trim() + (d.manual ? " · manual" : "") + (d.recording ? " · REC" : "");
}
function renderFleet() {
  const s = S.state;
  const all = Object.values(s.drones);
  const air = all.filter((d) => d.state === "airborne").sort((a, b) => a.battery - b.battery);
  const rest = all.filter((d) => d.state !== "airborne").sort((a, b) => a.id.localeCompare(b.id));
  $("airCount").textContent = `· ${air.length}, tightest battery first`;
  $("air").innerHTML = air.map((d) => {
    const [cls, ic] = droneStatus(d, s);
    return `<div class="ar"><b>${esc(d.id)}</b><span class="st ${cls}"><i>${ic}</i>${Math.round(d.battery)}%</span><span class="bar"><b style="width:${d.battery}%"></b></span><span class="cut">${esc(missionText(d))}</span></div>`;
  }).join("") || `<p class="empty">Nothing airborne.</p>`;
  if ($("details").hidden) return;
  $("fleetSub").textContent = `· ${air.length} airborne, tightest battery first`;
  $("fleet").innerHTML = [...air, ...rest].map((d) => {
    const [cls, ic, word] = droneStatus(d, s);
    const fill = d.state === "airborne" ? "" : cls === "st-chg" ? "chg" : "dock";
    const slot = d.state === "airborne" ? (d.booked_slot ? `→${d.booked_slot}` : "") : d.slot;
    return `<div class="row"><span class="id">${esc(d.id)}</span>
      <span class="st ${cls}"><i>${ic}</i>${word}</span>
      <span class="bat"><span class="bar"><b class="${fill}" style="width:${d.battery}%"></b></span><span>${Math.round(d.battery)}%</span></span>
      <span class="cut">${esc(missionText(d))}</span><span class="cut dim">${esc(d.pilot || "")}</span><span class="cut dim">${esc(slot || "")}</span></div>`;
  }).join("");
}

function authLine(a, s) {
  const left = a.expires_min != null ? a.expires_min - s.t : null;
  const active = a.status === "active" && (left == null || left > 0);
  const clock = (x) => `${Math.floor(x)}:${String(Math.floor((x % 1) * 60)).padStart(2, "0")}`;
  if (!active) return `<div class="auth done"><span>✓ ${esc(a.kind)} ${esc(a.status)}</span><span>by ${esc(a.approved_by)}</span></div>`;
  const late = left != null && left <= 5;
  const count = left != null ? `${late ? "⚠ " : ""}T-${clock(left)}` : "no expiry";
  return `<div class="auth ${late ? "late" : "active"}"><span>🔐 ${esc(a.kind)} · ${esc(a.scope || "approved")} · ${esc(a.approved_by)}</span><span>${count}</span></div>`;
}
function renderIncidents() {
  const s = S.state;
  const list = Object.values(s.incidents || {});
  if (!list.length && !S.approvals.some((a) => a.status === "pending")) {
    $("incidents").innerHTML = `<p class="empty">No incidents. Calm shift so far.</p>`;
    return;
  }
  const open = list.filter((i) => i.status === "open").reverse();
  const closed = list.filter((i) => i.status !== "open").reverse();
  const needs = (i) => {
    const want = S.events.filter((e) => e.type === "approval_needed" && e.payload?.incident === i.id).map((e) => e.payload.what);
    const pend = S.approvals.filter((a) => a.status === "pending" && a.args?.incident === i.id).map((a) => `${a.id} ${a.type}`);
    const got = new Set((i.authorisations || []).map((a) => a.kind));
    return [...pend, ...want.filter((w) => !got.has(w) && !pend.some((p) => p.endsWith(w)))];
  };
  const cards = open.map((i) => {
    const wait = needs(i);
    if (!wait.length && !i.drones.length && !i.units.length) {
      return `<article class="card closed">⚠ ${esc(i.id)} ${esc(i.kind)} · open, no drones or units assigned · opened ${esc(i.opened)}</article>`;
    }
    const last = i.facts?.at(-1);
    const fact = last ? Object.entries(last.fact).map(([k, v]) => `${k.replace(/_/g, " ")} ${v}`).join(", ") : i.report;
    const units = i.units.map((u) => u.replace(/_/g, " ")).join(", ");
    return `<article class="card ${wait.length ? "gold" : ""}">
      <h3><span>⚠ ${esc(i.id)} ${esc(i.kind)}</span><span class="right">P${esc(i.priority)} · ${esc(place(i.at))} · ${esc(i.opened)}</span></h3>
      <p><span class="k">Drones</span> ${esc(i.drones.join(", ") || "none")}${i.eyes_on ? ` <span class="k">· eyes-on</span> ${esc(i.eyes_on)}` : ""}</p>
      <p><span class="k">Units</span> ${esc(units || "none")} <span class="k">· Recording</span> ${i.recording.length ? `● ${esc(i.recording.join(", "))}` : "off"}</p>
      <p title="${esc(fact)}"><span class="k">Latest</span> ${esc(fact)}</p>
      ${wait.map((w) => `<div class="auth pending"><span>◆ Awaiting decision: ${esc(w)}</span><span>${esc(w.includes("track") ? "watch commander" : "human")}</span></div>`).join("")}
      ${(i.authorisations || []).map((a) => authLine(a, s)).join("")}
    </article>`;
  });
  const floating = S.approvals.filter((a) => a.status === "pending" && !a.args?.incident).map((a) =>
    `<article class="card gold"><div class="auth pending"><span>◆ Awaiting decision: ${esc(a.id)} ${esc(a.type)}</span><span>${esc(a.created)}</span></div><p>${esc(a.summary)}</p></article>`);
  const shut = closed.map((i) => `<article class="card closed">✓ ${esc(i.id)} ${esc(i.kind)} · closed ${esc(i.closed)}</article>`);
  $("incidents").innerHTML = [...floating, ...cards, ...shut].join("");
}

function renderKpi() {
  const s = S.state, k = s.kpi || {};
  const eyes = Object.entries(k.eyes_on_s || {}).at(-1);
  const cell = (val, label, cls = "") => `<div class="${cls}"><b>${esc(val)}</b><small>${label}</small></div>`;
  const safe = (n) => (n === 0 ? `<em>✓ ok</em>` : `<em>✖ fail</em>`);
  $("kpi").innerHTML = [
    cell(eyes ? `${eyes[1]} s` : "—", `eyes-on last${eyes ? `<br>${esc(eyes[0])}` : ""}`),
    cell(k.released_early ?? "—", "units<br>released"),
    cell(k.coverage_pct != null ? `${k.coverage_pct.toFixed(1)}%` : "—", "coverage<br>so far"),
    cell(k.reserve_breaches ?? "—", `reserve breaches<br>${safe(k.reserve_breaches)}`, k.reserve_breaches ? "bad" : "ok"),
    cell(k.red_zone_entries ?? "—", `red-zone entries<br>${safe(k.red_zone_entries)}`, k.red_zone_entries ? "bad" : "ok"),
  ].join("");
}

function renderTicker() {
  const items = [];
  for (const i of Object.values(S.state.incidents || {})) {
    for (const a of i.authorisations || []) {
      const exp = a.expires ? `, exp ${a.expires}` : "";
      items.push({ m: a.approved_min, html: `<time>${esc(fmtMin(a.approved_min))}</time>🔐 ${esc(a.kind)} approved @${esc(a.approved_by)} (${esc(i.id)}${esc(exp)})` });
    }
  }
  for (const r of S.privacy) {
    const refused = r.kind.includes("refused");
    const label = refused ? "⛔ PRIVACY_POLICY: face ID refused" : `${r.kind === "recording_on" ? "● rec on" : "○ rec off"} ${r.drone || ""}${r.incident ? ` (${r.incident})` : ""}`;
    const kind = ["recording_on", "recording_off"].includes(r.kind) || refused ? label : `${r.kind.replace(/_/g, " ")} ${r.drone || ""}`;
    items.push({ m: r.sim_min, html: `<time>${esc(r.clock)}</time><span class="${refused ? "bad" : ""}">${esc(kind)}</span>` });
  }
  items.sort((a, b) => b.m - a.m);
  $("ticker").innerHTML = items.slice(0, 12).map((i) => `<span>${i.html}</span>`).join("") || `<span class="empty">no recording, no authorisations yet</span>`;
}
const fmtMin = (m) => `${String(Math.floor(m / 60)).padStart(2, "0")}:${String(Math.floor(m % 60)).padStart(2, "0")}`;

function renderSectors() {
  if ($("details").hidden) return;
  const s = S.state;
  const dead = new Set((s.intrusions || []).map((x) => x.sector));
  $("sectors").innerHTML = Object.entries(s.weather || {}).map(([id, w]) => {
    const bad = w.grounded || dead.has(id);
    const text = w.grounded ? `⚠ grounded ${w.wind_ms} m/s` : dead.has(id) ? "⚠ intrusion hold" : "✓ open";
    return `<div><span>${esc(id)}</span><span class="${bad ? "bad" : "ok"}">${esc(text)}</span></div>`;
  }).join("");
}

// ---------------------------------------------------------------- scorecard (values straight from /kpi)
function setScorecard(on) {
  S.scorecard = on;
  $("scorecard").hidden = !on;
  if (on) renderScorecard();
  renderTop2();
}
function renderTop2() { if (S.state) renderTop(); }

function renderScorecard() {
  const k = S.kpi, s = S.state;
  const m = k?.managed, b = k?.baseline;
  if (!m) { $("scorecard").innerHTML = `<div class="sc"><header><h2>Scorecard</h2></header><p class="note">Waiting for /kpi.</p></div>`; return; }
  const incidents = s.incidents || {};
  const wins = k.wins || {};
  const result = (w) => (w === true ? `<td class="res win">✓ managed</td>` : w === false ? `<td class="res lose">✖ baseline</td>` : `<td class="res na">pending</td>`);
  const withheld = "withheld until final";
  const suspect = Object.keys(b?.suspect_lost || {});
  const rows = [];
  const firstEyes = Object.keys(m.eyes_on_s).concat(Object.keys(b?.eyes_on_min || {})).find((i) => incidents[i]?.kind && !suspect.includes(i));
  const eyesRow = (iid, key) => {
    if (!iid) return;
    const secs = m.eyes_on_s[iid], base = b?.eyes_on_min?.[iid];
    rows.push(["line", `eyes-on ${incidents[iid]?.kind ?? iid}`, secs != null ? `${secs} s` : "none", base != null ? `van ${base.toFixed(1)} min` : withheld, wins[key]]);
  };
  eyesRow(firstEyes, "eyes_on");
  for (const key of Object.keys(wins).filter((x) => x.startsWith("eyes_on:"))) eyesRow(key.split(":")[1], key);
  for (const iid of Object.values(incidents).filter((i) => (i.authorisations || []).some((a) => a.kind === "track") || suspect.includes(i.id))) {
    const tracked = iid.authorisations.some((a) => a.kind === "track");
    const text = (tracked ? "tracked" : "not tracked") + (tracked && iid.closed ? `, detained ${iid.closed}` : "");
    const lost = b?.suspect_lost?.[iid.id];
    const base = lost == null ? withheld : `van +${b.eyes_on_min[iid.id]?.toFixed(1)} min → suspect ${lost ? "LOST" : "found"}`;
    rows.push(["line", `${iid.kind} outcome`, text, base, wins[iid.kind]]);
  }
  rows.push(["line", "ground units released early", m.released_early, b ? b.released_early : "—", wins.released_early]);
  rows.push(["line", "unit-minutes saved by early release", m.unit_minutes_saved, b ? b.unit_minutes_saved : "—", wins.unit_minutes_saved]);
  rows.push(["line", "patrol junction coverage", `${m.coverage_pct.toFixed(1)}%`, b?.coverage_pct != null ? `~${Math.round(b.coverage_pct)}%` : withheld, wins.coverage]);

  const counted = Object.values(wins);
  const nWon = counted.filter((w) => w === true).length;
  const final = s.done && b && !b.pending && counted.every((w) => w !== null);
  const verdict = final
    ? `<span class="verdict" style="${nWon === counted.length ? "" : "color:var(--amber)"}">Managed wins ${nWon} of ${counted.length} counted lines</span>`
    : `<span class="verdict wait">${b ? "Shift running: baseline withheld where it depends on the future" : s.done ? "Press Baseline to compare" : "Managed figures so far"}</span>`;
  const safety = (key, label) => {
    const st = k.safety?.[key];
    return `<tr class="info"><td>${label}</td><td class="m">${esc(m[key])}</td><td>n/a</td><td class="res ${st === "ok" ? "win" : "lose"}">${st === "ok" ? "✓ ok" : "✖ fail"}</td></tr>`;
  };
  const first = Object.keys(m.first_unit_on_scene_min).find((i) => i === firstEyes) ?? null;
  const fm = first ? m.first_unit_on_scene_min[first] : null, fb = first ? b?.first_unit_on_scene_min?.[first] : null;
  const info = first ? `<tr class="info"><td>first ground unit on scene, ${esc(incidents[first]?.kind)}</td><td>+${esc(fm)} min</td><td>${fb != null ? `+${esc(fb)} min` : withheld}</td><td class="res na">info, not counted</td></tr>` : "";
  $("scorecard").innerHTML = `<div class="sc">
    <header><h2>Managed vs baseline · ${esc(s.scenario)}</h2>${verdict}</header>
    <table><thead><tr><th>Line</th><th>Managed</th><th>Baseline</th><th>Result</th></tr></thead><tbody>
    ${rows.map(([, l, mv, bv, w]) => `<tr><td>${esc(l)}</td><td class="m">${esc(mv)}</td><td>${esc(bv)}</td>${result(w)}</tr>`).join("")}
    <tr class="group"><td colspan="4">Safety checks</td></tr>
    ${safety("reserve_breaches", "reserve breaches")}${safety("red_zone_entries", "red-zone entries")}
    <tr class="group"><td colspan="4">Information</td></tr>${info}
    </tbody></table>
    <p class="note">Simulated. Baseline: ${esc(b?.model ?? "not run yet")}. The first-unit row uses the same unit model in both runs.</p>
  </div>`;
}

// ---------------------------------------------------------------- controls
const LiveCtl = {
  async run() { await POST(S.status?.runner ? "control/resume" : "control/start"); poll(); },
  async pause() { await POST("control/pause"); poll(); },
  async skip() { await POST("control/skip"); poll(); },
  async reset() { await POST("control/reset"); poll(); },
  async speed(v) { await POST("control/speed", { sim_min_per_s: v }); poll(); },
  async trigger(kind) { const j = await POST("trigger", { kind }); if (j.ok) toast(`Trigger fired: ${kind}`); },
  async load(body) { await POST("scenario/load", body); poll(); },
  async baseline() {
    const k = await GET("kpi");
    S.kpi = k.baseline ? k : await POST("baseline/run");
    setScorecard(true);
  },
};
const ReplayCtl = {
  run() { S.replay.play(); dirty(); },
  pause() { S.replay.pause(); dirty(); },
  skip() { markSkip(); S.replay.skip(); },
  reset() { S.replay.seek(S.replay.start); dirty(); },
  speed(v) { S.replay.rate = v; dirty(); },
  baseline() { setScorecard(true); },
};
function wire() {
  const C = S.replay ? ReplayCtl : LiveCtl;
  $("btnRun").onclick = () => C.run();
  $("btnPause").onclick = () => C.pause();
  $("btnSkip").onclick = () => C.skip();
  let armed = 0;
  const reset = $("btnReset");
  reset.onclick = () => {
    if (performance.now() > armed) {
      armed = performance.now() + 3000;
      reset.classList.add("armed");
      setTimeout(() => reset.classList.remove("armed"), 3000);
      toast("Click reset again to restart the shift");
      return;
    }
    armed = 0; reset.classList.remove("armed"); C.reset();
  };
  for (const b of document.querySelectorAll("[data-speed]")) b.onclick = () => C.speed(Number(b.dataset.speed));
  $("btnOrbit").onclick = () => $("btnOrbit").setAttribute("aria-pressed", String(S.city.toggleOrbit()));
  $("btnDetails").onclick = () => {
    const d = $("details");
    d.hidden = !d.hidden;
    $("btnDetails").setAttribute("aria-pressed", String(!d.hidden));
    render();
  };
  $("btnBaseline").onclick = () => (S.scorecard ? setScorecard(false) : C.baseline());
  if (S.replay) {
    $("btnTriggers").disabled = true;
    $("btnTriggers").title = "Triggers need the live simulator";
    const scrub = $("scrub");
    scrub.hidden = false;
    scrub.min = S.replay.start; scrub.max = S.replay.end; scrub.step = 0.1;
    scrub.oninput = () => S.replay.seek(Number(scrub.value));
    return;
  }
  for (const b of document.querySelectorAll("[data-trigger]")) {
    b.onclick = () => { $("picker").hidePopover(); LiveCtl.trigger(b.dataset.trigger); };
  }
  $("scenarioForm").onsubmit = (e) => {
    e.preventDefault();
    const f = new FormData(e.target);
    $("picker").hidePopover();
    LiveCtl.load({ name: f.get("name"), seed: f.get("seed") || null, mode: f.get("mode") });
  };
}

// ---------------------------------------------------------------- live transport
async function poll() {
  try {
    const [st, fl, pv, ap] = await Promise.all([GET("status"), GET("fleet"), GET("privacy-log"), GET("approvals")]);
    S.approvals = ap.approvals;
    S.status = st;
    S.privacy = pv.privacy_log;
    S.landing = Object.fromEntries(fl.drones.filter((d) => d.lands_at).map((d) => [d.id, d]));
    if (S.scorecard || S.state?.done) S.kpi = await GET("kpi");
    if (S.state?.done && !S.scorecard && !S.seenDone && !S.game) { S.seenDone = true; setScorecard(true); }
    dirty();
  } catch { setLink(false); }
}
async function backfill() {
  const [ev, ap] = await Promise.all([GET("events"), GET("approvals")]);
  S.events = ev.events.slice(-500);
  S.approvals = ap.approvals;
  dirty();
}
function setLink(ok) {
  const t = $("modeTag");
  if (!ok) { t.textContent = "⚠ sim link lost, retrying"; t.className = "tag tag-red"; }
}
function connect() {
  const es = new EventSource(`${API}events/stream`);
  const on = (name, fn) => es.addEventListener(name, (e) => fn(JSON.parse(e.data)));
  on("snapshot", (d) => { applySnapshot(d); S.seenDone = false; backfill().then(poll); });
  on("tick", (d) => applyTick(d));
  on("event", applyEvent);
  on("approval", async () => { S.approvals = (await GET("approvals")).approvals; dirty(); });
  es.onerror = () => setLink(false);
  setInterval(poll, 1500);
}

// ---------------------------------------------------------------- boot
function replayFrame(f, quiet) {
  switch (f.f) {
    case "snapshot": applySnapshot(structuredClone(f.d)); break;
    case "tick": applyTick(f.d, quiet); break;
    case "event": applyEvent(f.d); break;
    case "privacy": S.privacy.push(f.d); break;
    case "approvals": S.approvals = f.d; break;
    case "landing": S.landing = f.d; break;
    case "kpi": S.kpi = f.d; break;
  }
}
function webglFail(msg) {
  const w = $("webgl");
  w.hidden = false;
  w.textContent = msg;
}
async function boot() {
  const src = params.get("replay");
  try {
    if (src) {
      const dir = src.slice(0, src.lastIndexOf("/") + 1);
      const r = await fetch(`${dir}world.json`);
      if (!r.ok) throw new Error(`${dir}world.json: HTTP ${r.status}`);
      S.world = await r.json();
      S.replay = await Replay.load(src, replayFrame, () => dirty());
      $("source").textContent = "recorded run · replay, no live sim";
    } else {
      S.world = await GET("world");
      $("source").textContent = "live from the simulator";
    }
  } catch (e) {
    const f = $("fatal");
    f.hidden = false;
    f.innerHTML = `<p>${src ? "Could not load the replay." : "The simulator is not reachable."} (${esc(e.message)})</p>
      <p><a href="?replay=fixtures/golden.jsonl">Open the recorded replay</a></p>`;
    return;
  }
  $("district").textContent = `${S.world.district.name.toUpperCase()} (fictional)`;
  const key = params.get("key");
  const wanted = params.get("game");
  const ops = params.get("ops") === "1" || wanted === "0" || !!src;
  const mode = ops ? "ops" : wanted === "1" ? "game" : key ? "city" : "ops";
  const stations = mode === "city" ? (await GET("city")).stations : [];
  S.world.stations = stations;
  try {
    S.city = new City($("cityPane"), $("labels"), S.world, { cityMode: mode === "city" });
    S.city.onLost = () => webglFail("The 3D view lost its graphics context. Reload the page.");
  } catch (e) {
    S.city = noCity;
    webglFail(`The 3D city needs WebGL, which this browser could not start (${e.message}). Turn on hardware acceleration or try another browser. The panels on the right stay live.`);
  }
  wire();
  if (S.replay) {
    S.replay.begin();
    const at = params.get("t");
    if (at) { S.replay.seek(Replay.minutes(at)); } else { S.replay.play(); }
    render();
  } else {
    S.game = mode !== "ops"; // both keep the ops scorecard out of their view
    connect();
    if (mode === "game") (await import("./game.js")).startGame({ key, S, speak, API });
    if (mode === "city") (await import("./citymode.js")).startCityMode({ key, S, API, stations });
  }
  window.airgrid = { S, fps: () => S.city.fps, models: () => S.city.modelState, seek: (t) => S.replay?.seek(Replay.minutes(t)) }; // used by the screenshot gate
}
boot();
