// Replay of a recorded run from static files. Frames due at or before the replay time are the
// only ones applied, so the page never shows more than the run had reached.
// Prefers <name>.wire.jsonl (recorded API frames); falls back to the lean golden frames.

function leanToState(f, prev) {
  const drones = {}, docks = {};
  for (const [id, d] of Object.entries(f.drones)) {
    drones[id] = { id, state: d.state, battery: d.bat, pos: d.pos, mission: d.mission, pilot: d.pilot, slot: d.slot, loop: d.loop, on_station: d.mission === "patrol", incident: null, recording: false };
  }
  for (const [id, k] of Object.entries(f.docks)) {
    const w = prev.world.docks[id];
    const states = Array.from({ length: w.slots }, (_, i) => ({ slot: i, drone: k.occ[i] ?? null, status: k.occ[i] ? "OCCUPIED" : i - k.occ.length < k.res.length ? "RESERVED" : "FREE" }));
    docks[id] = { id, kind: w.kind, at: w.at, slots: w.slots, online: k.online, restricted: null, slot_state: states };
  }
  const weather = Object.fromEntries(Object.keys(prev.world.sectors).map((s) => [s, { grounded: f.grounded.includes(s), wind_ms: null, until_min: null }]));
  return { t: f.t, clock: f.clock, drones, docks, weather, suspect: f.suspect, grounded: f.grounded };
}

function fromLean(rows, world) {
  const frames = [];
  const log = rows.at(-1)?.log ?? [];
  const body = rows.filter((r) => r.drones);
  const zones = world.zones.map((z) => ({ id: z.id, kind: z.kind, rect: z.rect, active: z.active ?? true, until: null }));
  frames.push({ f: "snapshot", t: body[0].t, d: { scenario: "FRIDAY-RUSH", mode: "replay", done: false, zones, pilots: {}, incidents: {}, units: [], kpi: {}, ...leanToState(body[0], { world }) } });
  log.forEach(([t, text], i) => frames.push({ f: "event", t, d: { seq: i, t, clock: fmt(t), type: "log", text, timeline: true } }));
  body.slice(1).forEach((r) => frames.push({ f: "tick", t: r.t, d: { ...leanToState(r, { world }), done: r === body.at(-1) } }));
  return frames.sort((a, b) => a.t - b.t);
}
const fmt = (m) => `${String(Math.floor(m / 60)).padStart(2, "0")}:${String(Math.floor(m % 60)).padStart(2, "0")}:${String(Math.round((m % 1) * 60) % 60).padStart(2, "0")}`;

const parse = (text) => text.split("\n").filter(Boolean).map((l) => JSON.parse(l));

export class Replay {
  static minutes(hhmm) {
    const [h, m, s = 0] = String(hhmm).split(":").map(Number);
    return String(hhmm).includes(":") ? h * 60 + m + s / 60 : Number(hhmm);
  }

  static async load(src, apply, changed) {
    const wire = src.replace(/\.jsonl$/, ".wire.jsonl");
    let frames;
    const w = await fetch(wire);
    if (w.ok) {
      frames = parse(await w.text());
    } else {
      const dir = src.slice(0, src.lastIndexOf("/") + 1);
      const world = await (await fetch(`${dir}world.json`)).json();
      const r = await fetch(src);
      if (!r.ok) throw new Error(`${src}: HTTP ${r.status}`);
      frames = fromLean(parse(await r.text()), world);
    }
    return new Replay(frames, apply, changed);
  }

  constructor(frames, apply, changed) {
    this.frames = frames;
    this.apply = apply;
    this.changed = changed;
    this.start = frames[0].t;
    this.end = frames.at(-1).t;
    this.rate = 1;
    this.playing = false;
    this.seeking = false;
  }

  begin() {
    this.i = 0;
    this.t = this.start;
    this.drain(true);
    this.changed();
    let last = performance.now();
    const step = (now) => {
      const dt = Math.min(0.25, (now - last) / 1000);
      last = now;
      if (this.playing) {
        this.t = Math.min(this.end, this.t + this.rate * dt);
        this.drain(false);
        if (this.t >= this.end) { this.playing = false; this.changed(); }
      }
      requestAnimationFrame(step);
    };
    requestAnimationFrame(step);
  }

  drain(quiet) {
    while (this.i < this.frames.length && this.frames[this.i].t <= this.t + 1e-6) {
      this.apply(this.frames[this.i], quiet);
      this.i += 1;
    }
  }

  play() { if (this.t >= this.end) this.seek(this.start); this.playing = true; }

  pause() { this.playing = false; }

  seek(t) {
    this.seeking = true;
    if (t < this.t) this.i = 0;
    this.t = Math.max(this.start, Math.min(this.end, t));
    this.drain(true);
    this.seeking = false;
    this.changed();
  }

  // next timeline event after the replay time
  skip() {
    const next = this.frames.find((f, n) => n >= this.i && f.f === "event" && f.d.timeline);
    if (next) this.seek(next.t);
  }
}
