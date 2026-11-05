// City mode screen (docs/city-mode.md, L56): the drone monitor team's view. Shows what GET /city
// serves; its only writes are start and the two simulate inputs. Decisions belong to people in
// Discord, so nothing here posts to /city/decide.
import { CityFx } from "./cityfx.js";

const $ = (id) => document.getElementById(id);
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]);
const POLL_MS = 200;
// the sim's report line carries coordinates for the agents; people get the place name
const plain = (s) => {
  const t = String(s ?? "").replace(/ at \(\d[\d.]*, \d[\d.]*\)/, "");
  return t.charAt(0).toUpperCase() + t.slice(1);
};

export function startCityMode({ key, S, API, stations }) {
  const U = window.airgridCity = { view: null, since: 0, fx: null, failed: false };
  document.body.classList.add("cityView");
  if (S.city.scene) {
    U.fx = new CityFx(S.city, stations);
    S.city.fx = U.fx;
  }
  const send = async (path, body) => {
    const r = await fetch(API + path, {
      method: "POST", headers: { "X-Game-Key": key, "content-type": "application/json" }, body: JSON.stringify(body),
    });
    return { status: r.status, body: await r.json().catch(() => ({})) };
  };
  const problem = (msg) => { $("cLine").textContent = msg; };

  function render(v) {
    $("cClock").textContent = v.clock_label;
    const c = v.counters;
    $("cHandled").textContent = c.handled;
    $("cAvg").textContent = c.avg_response_s != null ? `${c.avg_response_s} s` : "—";
    $("cUp").textContent = c.drones_up;
    const line = plain(v.feed.at(-1)) || "The city is calm.";
    if ($("cLine").textContent !== line && !U.failed) $("cLine").textContent = line;
  }

  async function trigger(kind, btn) {
    btn.disabled = true;
    const r = await send("city/trigger", { kind });
    if (r.status !== 200) problem(r.body.reason || "That didn't go through.");
    setTimeout(() => { btn.disabled = false; }, 900);
  }
  $("cFire").onclick = (e) => trigger("fire", e.currentTarget);
  $("cCrash").onclick = (e) => trigger("accident", e.currentTarget);
  $("cRandom").onclick = (e) => trigger("random_fire", e.currentTarget); // hidden until a drone finds it

  async function poll() {
    try {
      const r = await fetch(`${API}city?since=${U.since}`);
      if (r.ok) {
        const v = await r.json();
        U.view = v;
        if (v.events.length) U.since = v.events.at(-1).seq;
        if (v.started) { U.fx?.update(v); render(v); }
      }
    } catch { /* the sim is restarting: the next poll retries */ }
    setTimeout(poll, POLL_MS);
  }

  (async () => {
    const first = await (await fetch(`${API}city`)).json();
    if (!first.started) {
      const r = await send("city/start", {});
      if (r.status !== 200) {
        U.failed = true;
        problem(r.status === 403 ? "This link has no valid key. Use the URL the launcher printed." : `Could not start the city (HTTP ${r.status}).`);
      }
    }
    poll();
  })();
}
