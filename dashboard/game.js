// Game mode "Friday Rush" (docs/game-mode.md, L54). Shows what /game serves and sends the player's
// choices back; score, timers, catch and refusals are all the sim's. Sim-time pacing is the sim's
// too: this file only draws. The 3D city and its SSE feed come from app.js.
const $ = (id) => document.getElementById(id);
const RATING = { Commander: "Outstanding", Officer: "Solid", Rookie: "Needs review" };
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]);
const words = (s) => String(s ?? "").replace(/([a-z])_([a-z0-9])/gi, "$1 $2");

const TITLE = {
  crash: "CRASH: is that unit still needed?",
  robbery: "ROBBERY: follow the suspect?",
  trap_face: "A request you should think about",
  trap_route: "A shortcut you should think about",
  finale: "YOUR TURN TO FLY",
};
const STEPS = [["crash", "Crash"], ["robbery", "Robbery"], ["trap", "Trap"], ["finale", "Chase"]];
const REFUSED = {
  PRIVACY_POLICY: {
    head: "Face recognition is never allowed.",
    alt: "The lawful way: keep following the vehicle, and let officers identify the person on the ground.",
  },
  NO_FLY_ZONE: {
    head: "No drone enters the airfield. There is no override.",
    alt: "The lawful way: hold at the boundary and pick the suspect up on the far side with another drone.",
  },
};
const GOOD = {
  "crash:release": "Unit released. It's free for the next call.",
  "crash:keep": "Unit stays on scene.",
  "robbery:approve": "Track approved, with a time limit. A drone follows the suspect.",
  "robbery:reject": "Track refused. No drone follows the suspect.",
  "trap_face:lawful": "Good call: request declined and logged.",
  "trap_route:lawful": "Good call: request declined and logged.",
  "finale:take_control": "You have control.",
  "finale:leave": "Ground units carry on without you.",
  "finale_no_track:leave": "Ground units carry on without you.",
};
const KEYS = {
  KeyW: [0, 1], ArrowUp: [0, 1], KeyS: [0, -1], ArrowDown: [0, -1],
  KeyA: [-1, 0], ArrowLeft: [-1, 0], KeyD: [1, 0], ArrowRight: [1, 0],
};
const CATCH_MS = 3200;

export function startGame({ key, S, speak, API }) {
  const G = window.airgridGame = {
    phase: "start", view: null, score: null, seen: new Set(), lastPrompt: null, promptMax: 20,
    deadline: 0, note: null, noteUntil: 0, refusal: null, refusalUntil: 0, keys: new Set(),
    sent: "0,0", sentAt: 0, wasBlocked: false, incidents: 0, lastSeen: null, chosen: false,
  };
  document.body.classList.add("game");
  const send = async (path, body) => {
    const r = await fetch(API + path, {
      method: body === undefined ? "GET" : "POST",
      headers: { "X-Game-Key": key || "", "content-type": "application/json" },
      body: body === undefined ? undefined : JSON.stringify(body),
    });
    return { status: r.status, body: await r.json().catch(() => ({})) };
  };

  // --- screens
  function showStart(problem = "") {
    $("gStart").hidden = false;
    document.body.classList.add("gMenu");
    $("gStart").innerHTML = `<div class="gTitle"><p class="gKicker">AIRGRID · NORTHGATE DISTRICT (fictional)</p>
      <h1>FRIDAY RUSH</h1>
      <p class="gLead">You're the Watch Commander. Make three calls, then fly the drone yourself.</p>
      <button id="gGo" class="gBig">START SHIFT</button>
      <p class="gSmall">About 3 minutes · a simulation of a made-up city</p>
      ${problem ? `<p class="gProblem">${esc(problem)}</p>` : ""}</div>`;
    $("gGo").onclick = begin;
    $("gGo").focus();
  }
  async function begin() {
    $("gGo")?.setAttribute("disabled", "");
    if ($("gPlayAgain")) $("gPlayAgain").disabled = true;
    const r = await send("game/start", {});
    if (r.status !== 200) {
      showStart(r.status === 403 ? "This link has no valid game key. Use the URL printed by run_game.sh." : `Could not start (HTTP ${r.status}).`);
      return;
    }
    Object.assign(G, { phase: "play", score: null, note: null, refusal: null, lastPrompt: null, wasBlocked: false, incidents: 0, lastSeen: null, chosen: false, keys: new Set(), sent: "0,0" });
    G.seen = new Set();
    $("gStart").hidden = true;
    $("gEnd").hidden = true;
    document.body.classList.remove("gMenu");
    S.city.setAutoOrbit?.(false);
    apply(r.body);
  }

  async function choose(id) {
    if (G.chosen) return;
    G.chosen = true;
    const r = await send("game/choose", { choice: id });
    if (r.status === 200) apply(r.body);
    else G.chosen = false;
  }

  // --- state from the sim
  function apply(v) {
    G.view = v;
    if (v.step === "idle") return;
    if (G.phase === "start") { G.phase = "play"; $("gStart").hidden = true; document.body.classList.remove("gMenu"); S.city.setAutoOrbit?.(false); }
    if (v.beat && v.beat !== G.lastPrompt) {
      G.lastPrompt = v.beat;
      G.chosen = false;
      G.promptMax = Math.max(v.remaining_s ?? 20, 1);
      G.refusal = null;
    }
    if (!v.beat) G.lastPrompt = null;
    if (v.remaining_s != null) G.deadline = performance.now() + v.remaining_s * 1000;
    const res = v.result;
    if (res) {
      const id = `${res.beat}:${res.choice}`;
      if (!G.seen.has(id)) { G.seen.add(id); onResult(res); }
    }
    if (v.flight && !G.flying) { G.flying = true; document.body.classList.add("flying"); }
    if (!v.flight && G.flying) { G.flying = false; document.body.classList.remove("flying"); }
    const blocked = !!v.flight?.blocked;
    if (blocked && !G.wasBlocked) flash("gWall");
    G.wasBlocked = blocked;
    if (v.done && G.phase === "play") finish();
  }

  function onResult(r) {
    if (r.ok === false) {
      const known = REFUSED[r.code] || { head: words(r.reason), alt: words(r.hint) };
      G.refusal = { code: r.code, ...known, reason: r.reason };
      G.refusalUntil = performance.now() + 6000;
      flash("gAlarm");
      return;
    }
    const line = GOOD[`${r.beat}:${r.choice}`] || "Done.";
    G.note = r.timed_out ? `Out of time, so the safe default was used. ${line}` : line;
    G.noteUntil = performance.now() + 5000;
  }

  function flash(id) {
    const n = $(id);
    n.classList.remove("on");
    void n.offsetWidth;
    n.classList.add("on");
  }

  async function finish() {
    G.phase = "ending";
    releaseKeys();
    let r = await send("game/score");
    for (let i = 0; i < 20 && !r.body.done; i++) {
      await new Promise((ok) => setTimeout(ok, 150));
      r = await send("game/score");
    }
    G.score = r.body;
    if (r.body.caught) {
      $("gCaught").hidden = false;
      $("gCaught").innerHTML = `<div><h1>VEHICLE LOCKED</h1><p>You stayed on the scooter until the ground units closed in${r.body.caught_at ? ` at ${esc(r.body.caught_at)}` : ""}.</p></div>`;
      await new Promise((ok) => setTimeout(ok, CATCH_MS));
      $("gCaught").hidden = true;
    }
    showEnd(r.body);
  }

  function showEnd(s) {
    G.phase = "end";
    document.body.classList.add("gMenu");
    S.city.thaw?.(S.state);
    const lines = Object.values(s.breakdown || {}).map((b) =>
      `<li><span>${esc(b.detail)}</span><b>${esc(b.points)}/${esc(b.max)}</b></li>`).join("");
    const rows = (s.old_way || []).map((r) =>
      `<tr><td>${esc(r.label)}</td><td class="you">${esc(words(r.you))}</td><td>${esc(words(r.old_way))}</td></tr>`).join("");
    const slow = (s.timed_out || []).length;
    const how = s.caught ? "You held the vehicle until ground units closed in." : "Ground units made the arrest without you.";
    $("gEnd").hidden = false;
    $("gEnd").innerHTML = `<div class="gEndCard">
      <div class="gEndTop">
        <div><p class="gKicker">SHIFT SCORE</p><p class="gScore"><b>${esc(s.score)}</b><span>/${esc(s.max)}</span></p></div>
        <div><p class="gKicker">SHIFT RATING</p><p class="gRank">${esc(RATING[s.rank] ?? s.rank)}</p></div>
        <p class="gHow">${esc(how)}${slow ? ` ${slow} ${slow === 1 ? "call" : "calls"} timed out.` : ""}</p>
      </div>
      <div class="gEndGrid">
        <table><thead><tr><th>Against the old way</th><th>You</th><th>Old way</th></tr></thead><tbody>${rows}</tbody></table>
        <ul class="gPoints">${lines}</ul>
      </div>
      <button id="gPlayAgain" class="gBig">RUN ANOTHER SHIFT</button>
    </div>`;
    $("gPlayAgain").onclick = begin;
    $("gPlayAgain").focus();
    S.city.direct(null);
    S.city.chaseCam(null);
    S.city.setAutoOrbit?.(true);
  }

  // --- the card
  function render() {
    const v = G.view;
    const now = performance.now();
    $("gScoreChip").textContent = v ? `${v.score_so_far ?? 0} pts` : "0 pts";
    const clock = v?.clock || "";
    if (G.phase === "play") $("gClock").textContent = clock.length === 5 ? `${clock}:00` : clock;
    const steps = STEPS.map(([id, label]) => {
      const at = STEPS.findIndex((x) => x[0] === v?.step);
      const i = STEPS.findIndex((x) => x[0] === id);
      const cls = v?.step === "score" || i < at ? "done" : i === at ? "now" : "";
      return `<span class="${cls}">${cls === "done" ? "✓ " : ""}${label}</span>`;
    }).join("");
    if ($("gSteps").dataset.html !== steps) { $("gSteps").dataset.html = steps; $("gSteps").innerHTML = steps; }
    const playing = G.phase === "play" && v && v.step !== "idle";
    $("gHud").hidden = G.phase === "start" || G.phase === "end";
    const refuse = G.refusal && now < G.refusalUntil ? G.refusal : null;
    $("gRefuse").hidden = !refuse;
    if (refuse && $("gRefuse").dataset.code !== refuse.code) {
      $("gRefuse").dataset.code = refuse.code;
      $("gRefuse").innerHTML = `<p class="gCode">⛔ REFUSED · ${esc(refuse.code)}</p><h2>${esc(refuse.head)}</h2><p>${esc(refuse.alt)}</p>`;
    }
    if (!refuse) delete $("gRefuse").dataset.code;
    renderCard(playing, refuse, now);
    renderFlight(playing ? v.flight : null);
    aimCamera(v);
  }

  function renderCard(playing, refuse, now) {
    const v = G.view, card = $("gCard");
    const prompt = playing && v.beat && !refuse;
    card.hidden = !playing || !!v.flight || !!refuse;
    if (card.hidden) return;
    if (prompt) {
      if (card.dataset.beat !== v.beat) {
        card.dataset.beat = v.beat;
        const opts = v.options.map((o, i) => {
          const tone = o.id === "try" ? "risky" : ["release", "approve", "take_control", "lawful"].includes(o.id) ? "main" : "";
          return `<button class="gOpt ${tone}" data-id="${esc(o.id)}"><kbd>${i + 1}</kbd>${esc(o.label)}</button>`;
        }).join("");
        card.innerHTML = `<div class="gTimer"><b id="gSecs"></b><span class="gTrack"><i id="gBar"></i></span></div>
          <h2>${esc(TITLE[v.beat] || "Your call")}</h2>
          <p class="gPrompt">${esc(words(v.prompt))}</p>
          <div class="gOpts">${opts}</div>`;
        card.querySelectorAll(".gOpt").forEach((b) => { b.onclick = () => choose(b.dataset.id); });
        flash("gAlarm");
      }
      const left = Math.max(0, (G.deadline - now) / 1000);
      $("gSecs").textContent = `${Math.ceil(left)} s to decide`;
      $("gBar").style.width = `${Math.min(100, (left / G.promptMax) * 100)}%`;
      return;
    }
    const note = G.note && now < G.noteUntil ? G.note : null;
    const line = lastLine();
    const html = `<p class="gWait">${esc(note || line)}</p>`;
    delete card.dataset.beat;
    if (card.dataset.wait !== html) { card.dataset.wait = html; card.innerHTML = html; }
  }

  function lastLine() {
    for (let i = S.events.length - 1; i >= 0; i--) {
      const t = speak(S.events[i]);
      if (t) return t;
    }
    return "Watching the city. Your first call is coming.";
  }

  function renderFlight(f) {
    const h = $("gFly");
    h.hidden = !f;
    if (!f) return;
    const s = f.suspect || S.state?.suspect;
    if (s) G.lastSeen = s;
    const aim = s || G.lastSeen;
    const dir = f.steer || (aim && [aim[0] - f.pos[0], aim[1] - f.pos[1]]);
    const ang = dir ? (Math.atan2(dir[0], dir[1]) * 180) / Math.PI : 0;
    $("gBat").style.width = `${f.battery}%`;
    $("gBatN").textContent = `${Math.round(f.battery)}%`;
    $("gArrow").style.transform = `rotate(${ang.toFixed(0)}deg)`;
    $("gArrow").style.visibility = aim ? "visible" : "hidden";
    $("gDist").textContent = f.distance_km != null ? `${f.distance_km.toFixed(2)} km` : "out of sight";
    $("gDistL").textContent = f.distance_km != null ? "to the scooter" : "fly back to its last position";
    $("gDrone").textContent = f.drone;
    $("gBlocked").hidden = !f.blocked;
  }

  // --- cinematic camera: picks a shot from what the sim is showing right now
  function aimCamera(v) {
    const c = S.city, s = S.state;
    if (!c.direct || G.phase !== "play") return;
    if (v?.flight) { c.chaseCam(v.flight.drone); return; }
    c.chaseCam(null);
    if (!s) return;
    const open = Object.values(s.incidents || {}).filter((i) => i.status === "open");
    if (open.length > G.incidents) flash("gAlarm");
    G.incidents = Math.max(G.incidents, open.length);
    const newest = open.filter((i) => i.drones.length || i.units.length).at(-1);
    const tracker = Object.values(s.drones).find((d) => d.state === "airborne" && d.mission === "track");
    const red = s.zones?.find((z) => z.kind === "red");
    if (v?.beat === "trap_route" && red) {
      c.direct({ x: (red.rect.x[0] + red.rect.x[1]) / 2 - 0.5, y: (red.rect.y[0] + red.rect.y[1]) / 2, dist: 4.4, polar: 1.02, azim: 0.75 });
    } else if (tracker) {
      c.direct({ follow: tracker.id, dist: 4.2, polar: 0.95, azim: 0.35 });
    } else if (newest) {
      c.direct({ x: newest.pos[0], y: newest.pos[1], dist: 4, polar: 0.92, azim: -0.3 });
    } else {
      c.direct({ x: 3, y: 3, dist: 9.4, polar: 0.78, azim: 0 });
    }
  }

  // --- stick
  function stick() {
    let x = 0, y = 0;
    for (const k of G.keys) { x += KEYS[k][0]; y += KEYS[k][1]; }
    const len = Math.hypot(x, y) || 1;
    return [x / len, y / len];
  }
  async function pushStick(force = false) {
    if (!G.view?.flight) return;
    const [dx, dy] = stick();
    const tag = `${dx.toFixed(3)},${dy.toFixed(3)}`;
    const now = performance.now();
    if (!force && tag === G.sent) return;
    G.sent = tag;
    G.sentAt = now;
    const r = await send("game/fly", { dx, dy });
    if (r.status === 200 && r.body.flight) { G.view.flight = r.body.flight; apply(G.view); }
  }
  function releaseKeys() { G.keys.clear(); G.sent = "0,0"; }
  addEventListener("keydown", (e) => {
    if (KEYS[e.code] && G.view?.flight) {
      e.preventDefault();
      if (!e.repeat) { G.keys.add(e.code); pushStick(); }
    } else if (/^Digit[1-9]$/.test(e.code) && G.view?.beat) {
      const o = G.view.options[Number(e.code.slice(5)) - 1];
      if (o) choose(o.id);
    }
  });
  addEventListener("keyup", (e) => {
    if (KEYS[e.code] && G.keys.delete(e.code)) pushStick();
  });
  addEventListener("blur", () => { if (G.keys.size) { G.keys.clear(); pushStick(); } });

  // --- ops toggle
  $("gOps").onclick = () => {
    const on = document.body.classList.toggle("ops");
    $("gOps").setAttribute("aria-pressed", String(on));
    dispatchEvent(new Event("resize"));
  };

  // --- loops
  async function tick() {
    try {
      const r = await send("game");
      if (r.status === 200) {
        if (G.phase !== "ending" && G.phase !== "end" || !r.body.done) {
          if (G.phase === "end" && r.body.step !== "idle" && !r.body.done) { $("gEnd").hidden = true; document.body.classList.remove("gMenu"); G.phase = "play"; }
          apply(r.body);
        }
      } else if (r.status === 403 && G.phase === "start") {
        showStart("This link has no valid game key. Use the URL printed by run_game.sh.");
      }
    } catch { /* the sim is restarting: the next poll retries */ }
    if (G.view?.flight && G.keys.size && performance.now() - G.sentAt > 4000) pushStick(true);
    setTimeout(tick, G.view?.flight ? 200 : 400);
  }

  (async () => {
    showStart();
    S.city.setAutoOrbit?.(true);
    await tick();
    setInterval(render, 100); // HUD only; the city draws itself on every frame
  })();
}
