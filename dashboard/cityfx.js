// City mode 3D layer (docs/city-mode.md, L56): stations, emergency vehicles, scene effects and
// progress rings. Positions, stages, progress and labels all come from GET /city. Lane offsets,
// parked spacing, flames, smoke and camera moves are display only and never feed back.
import * as THREE from "three";

const C = { red: 0xc8452c, sky: 0x57b7e8, amber: 0xd99a26, text: 0xe8eef4 };
const FOCUS_MS = 7000; // how long the camera stays at a new incident
const NOTE_MS = 6000;
const DISPERSE_S = 4;
const ROLE = { incident_lead: "incident lead", duty_doctor: "duty doctor" };
const JAM_COLORS = [0x8a2f26, 0xb8bfc6, 0x44627c, 0x7f8d99, 0xc9a24f, 0x3c5a5c];
const MOVING = ["en_route", "returning", "transporting", "blocked"];
const SIREN = ["en_route", "working", "transporting", "blocked"];
const LANE_KM = 0.032;
const PARK_KM = 0.085;
const STAGE = {
  alert: "Drone on the way", responding: "Help is on the way", working: "Crew at work",
  care_call: "Crew treating patients", transporting: "Ambulance heading to the Hospital",
  blocked: "Ambulance stopped by traffic",
  watching: "Drone watching", holding: "Drone staying a little longer",
  unfound: "Fire burning · patrols searching",
};
const ICON = { fire: "🔥", accident: "💥" };
const STATION_ICON = { fire: "🚒", hospital: "🏥", police: "🚓" };
const LIGHTS = {
  fire_engine: [0xff3b1f, 0xffb020], ambulance: [0xff3b3b, 0xffffff], police_car: [0x3b7bff, 0xff3b3b],
};

const std = (color, extra = {}) => new THREE.MeshStandardMaterial({ color, roughness: 0.7, ...extra });
function box(w, h, d, mat, x = 0, y = h / 2, z = 0, parent = null) {
  const m = new THREE.Mesh(new THREE.BoxGeometry(w, h, d), mat);
  m.position.set(x, y, z);
  m.castShadow = true;
  parent?.add(m);
  return m;
}

// Each station gets its own silhouette, so it reads from the full-district camera.
function fireStation() {
  const g = new THREE.Group();
  const brick = std(0xa83a28), trim = std(0x5e2118), door = std(0xe8eef4, { emissive: 0xffc27a, emissiveIntensity: 0.35 });
  box(0.5, 0.13, 0.3, brick, 0, 0.065, 0, g);
  box(0.52, 0.016, 0.32, trim, 0, 0.138, 0, g);
  for (const x of [-0.15, 0, 0.15]) box(0.11, 0.09, 0.006, door, x, 0.05, 0.152, g);
  box(0.1, 0.3, 0.1, brick, -0.3, 0.15, -0.06, g); // hose tower
  box(0.12, 0.016, 0.12, trim, -0.3, 0.306, -0.06, g);
  return g;
}
function hospital() {
  const g = new THREE.Group();
  const white = std(0xdfe6ea), glass = std(0x7fb4d6, { emissive: 0x7fb4d6, emissiveIntensity: 0.3 }), red = new THREE.MeshBasicMaterial({ color: 0xe0392b });
  box(0.56, 0.16, 0.24, white, 0, 0.08, 0.04, g);
  box(0.24, 0.27, 0.24, white, 0.12, 0.135, -0.1, g);
  box(0.57, 0.03, 0.245, glass, 0, 0.1, 0.04, g);
  box(0.15, 0.008, 0.045, red, 0.12, 0.274, -0.1, g); // roof cross
  box(0.045, 0.008, 0.15, red, 0.12, 0.274, -0.1, g);
  const pad = new THREE.Mesh(new THREE.CylinderGeometry(0.075, 0.075, 0.006, 24), std(0x4b5a66));
  pad.position.set(-0.16, 0.163, 0.04);
  g.add(pad);
  return g;
}
function policeStation() {
  const g = new THREE.Group();
  const navy = std(0x24415f), stone = std(0xc4cbd2), light = new THREE.MeshBasicMaterial({ color: C.sky });
  box(0.42, 0.2, 0.28, navy, 0, 0.1, 0, g);
  box(0.46, 0.02, 0.32, stone, 0, 0.21, 0, g);
  box(0.46, 0.05, 0.05, stone, 0, 0.17, 0.17, g);
  for (const x of [-0.17, -0.06, 0.06, 0.17]) box(0.02, 0.15, 0.02, stone, x, 0.075, 0.17, g); // columns
  box(0.14, 0.05, 0.1, navy, 0, 0.245, 0, g);
  box(0.05, 0.02, 0.05, light, 0, 0.28, 0, g); // blue lamp
  return g;
}
const BUILD = { fire: fireStation, hospital, police: policeStation };

function makeVehicle(kind) {
  const g = new THREE.Group();
  const long = kind === "fire_engine" ? 0.16 : kind === "ambulance" ? 0.12 : 0.1;
  const body = std(kind === "fire_engine" ? 0xd1341f : kind === "ambulance" ? 0xf2f5f7 : 0x1f3556);
  box(long, 0.032, 0.056, body, 0, 0.026, 0, g);
  box(long * 0.34, 0.03, 0.05, std(kind === "police_car" ? 0xe8eef4 : 0x2a3947), long * 0.2, 0.056, 0, g);
  if (kind === "fire_engine") box(long * 0.62, 0.008, 0.016, std(0xc4cbd2), -long * 0.14, 0.05, 0, g); // ladder
  if (kind === "ambulance") box(long * 0.98, 0.009, 0.058, new THREE.MeshBasicMaterial({ color: 0xd1341f }), 0, 0.03, 0, g);
  const [a, b] = LIGHTS[kind];
  const lamp = (hex, z) => {
    const m = new THREE.Mesh(new THREE.BoxGeometry(0.016, 0.012, 0.018), new THREE.MeshBasicMaterial({ color: hex }));
    m.position.set(long * 0.2, 0.078, z);
    g.add(m);
    return m;
  };
  const lamps = [lamp(a, -0.014), lamp(b, 0.014)];
  g.scale.setScalar(1.35);
  return { g, lamps };
}

function softTexture() {
  const c = document.createElement("canvas");
  c.width = c.height = 64;
  const x = c.getContext("2d");
  const g = x.createRadialGradient(32, 32, 2, 32, 32, 32);
  g.addColorStop(0, "rgba(255,255,255,1)");
  g.addColorStop(1, "rgba(255,255,255,0)");
  x.fillStyle = g;
  x.fillRect(0, 0, 64, 64);
  return new THREE.CanvasTexture(c);
}

export class CityFx {
  constructor(city, stations) {
    this.city = city;
    this.px = city.px; this.pz = city.pz;
    this.soft = softTexture();
    this.ys = new Set(Object.values(city.world.junctions).map((p) => p[1]));
    this.view = null;
    this.labels = new Map();
    this.vehicles = new Map();
    this.scenes = new Map();
    this.focus = null; this.focusUntil = 0;
    this.notes = []; this.seq = null;
    this.jams = new Map();
    this.rooms = new Map(); // incident -> room, the bot's rule: first free incident room
    // the soft line from the escort drone to its ambulance
    this.links = [0, 1].map(() => {
      const l = new THREE.Mesh(new THREE.CylinderGeometry(0.009, 0.009, 1, 8), new THREE.MeshBasicMaterial({ color: C.sky, transparent: true, opacity: 0.45, blending: THREE.AdditiveBlending, depthWrite: false }));
      l.visible = false;
      city.scene.add(l);
      return l;
    });
    // simulated thermal sensor: a cone from each investigating drone down to the street
    this.cones = [0, 1, 2].map(() => {
      const geo = new THREE.ConeGeometry(1, 1, 24, 1, true);
      geo.translate(0, -0.5, 0); // apex at the drone
      const c = new THREE.Mesh(geo, new THREE.MeshBasicMaterial({ color: C.amber, transparent: true, opacity: 0.2, blending: THREE.AdditiveBlending, depthWrite: false, side: THREE.DoubleSide }));
      const ring = new THREE.Mesh(new THREE.RingGeometry(0.9, 1, 40), new THREE.MeshBasicMaterial({ color: C.amber, transparent: true, depthWrite: false, side: THREE.DoubleSide }));
      ring.rotation.x = -Math.PI / 2;
      c.visible = ring.visible = false;
      city.scene.add(c, ring);
      return { c, ring };
    });
    this.hospital = stations.find((s) => s.kind === "hospital")?.at ?? null;
    this.pollMs = 220; this.lastPoll = 0;
    // a fixed pool: adding real lights later would recompile every material and hitch
    this.lamps = [0, 1, 2].map(() => {
      const l = new THREE.PointLight(0xff8a3c, 0, 2.6, 1.6);
      city.scene.add(l);
      return l;
    });
    this.stationTags = [];
    for (const s of stations) {
      const g = (BUILD[s.kind] || BUILD.police)();
      g.position.set(this.px(s.at[0]), 0, this.pz(s.at[1]));
      // face the road the vehicles park on
      g.rotation.y = Math.atan2(this.px(s.road[0]) - g.position.x, this.pz(s.road[1]) - g.position.z);
      city.scene.add(g);
      this.stationTags.push([`st:${s.id}`, `${STATION_ICON[s.kind] || ""} ${s.name}`, g.position.x, g.position.z]);
    }
    city.shadowDone = false;
  }

  labelOf(id) { return this.labels.get(id); }

  update(v) {
    const now = performance.now();
    if (this.lastPoll) this.pollMs = this.pollMs * 0.7 + Math.min(now - this.lastPoll, 800) * 0.3;
    this.lastPoll = now;
    this.view = v;
    this.announce(v, now);
    for (const [key, text, x, z] of this.stationTags) this.city.label(key, "station", text, x, 0.44, z);
    this.labels = new Map(v.drones.map((d) => [d.id, d.label]));
    v.vehicles.forEach((x, i) => this.syncVehicle(x, i, now));
    this.syncRooms(v);
    this.syncJams(v, now);
    // fires burning that no drone has found yet (operator view only, L63): flames, no beacon
    const unfound = (v.unfound || []).map((u) => ({ id: u.id, kind: "fire", at: u.at, stage: "unfound", name: "Not reported yet", since_s: u.since_s }));
    const all = [...v.incidents, ...unfound];
    // once a drone confirms it, the same burning spot becomes the incident: keep its scene
    for (const inc of v.incidents) {
      if (this.scenes.has(inc.id) || !inc.found_by) continue;
      const key = [...this.scenes.keys()].find((k) => k.startsWith("unfound-") && String(this.scenes.get(k).inc.at) === String(inc.at));
      if (key) { const s = this.scenes.get(key); this.scenes.delete(key); const t = this.city.tags.get(`p:${key}`); this.city.tags.delete(`p:${key}`); if (t) this.city.tags.set(`p:${inc.id}`, t); this.scenes.set(inc.id, s); }
    }
    const live = new Set(all.map((i) => i.id));
    for (const [id, s] of this.scenes) if (!live.has(id)) this.dropScene(id, s);
    for (const inc of all) {
      if (!this.scenes.has(inc.id)) {
        this.scenes.set(inc.id, this.makeScene(inc));
        this.focus = inc.at;
        this.focusUntil = now + FOCUS_MS;
      }
      this.syncScene(this.scenes.get(inc.id), inc);
    }
  }

  // A short floating note over the scene for the assessment outcome. Only events new to this
  // page: reloading mid-incident doesn't replay old notes.
  announce(v, now) {
    const newest = v.events.at(-1)?.seq ?? this.seq ?? 0;
    const fresh = this.seq == null ? [] : v.events.filter((e) => e.seq > this.seq);
    this.seq = Math.max(this.seq ?? 0, newest);
    for (const e of fresh) {
      if (e.type === "fire_confirmed") {
        const d = v.drones.find((x) => x.id === e.payload?.drone);
        const inc = v.incidents.find((i) => i.found_by?.drone === e.payload?.drone);
        const at = inc?.at || d?.pos;
        if (at) this.notes.push({ key: `n:${e.seq}`, text: `🔥 Fire confirmed by ${e.payload.drone} on patrol`, ok: false, at, y: 1.75, until: now + NOTE_MS });
      }
      if (e.type === "patient_delivered" && this.hospital) {
        this.notes.push({ key: `n:${e.seq}`, text: "🏥 Patient delivered", ok: true, at: this.hospital, y: 0.78, until: now + NOTE_MS });
      }
      if (e.type !== "ambulance_requested" && e.type !== "ambulance_not_needed") continue;
      const inc = v.incidents.find((i) => i.id === e.payload?.incident);
      if (!inc) continue;
      const n = e.payload.injuries;
      const text = e.type === "ambulance_requested" ? `🚑 Ambulance requested · ${n} injured` : "✓ Nobody hurt · no ambulance needed";
      this.notes.push({ key: `n:${inc.id}`, text, ok: e.type !== "ambulance_requested", at: inc.at, until: now + NOTE_MS });
    }
    this.notes = this.notes.filter((n) => {
      const live = now < n.until;
      if (live) this.city.label(n.key, n.ok ? "note ok" : "note", n.text, this.px(n.at[0]), n.y ?? 1.5, this.pz(n.at[1]));
      else this.city.unlabel(n.key);
      return live;
    });
  }

  syncRooms(v) {
    const live = new Set(v.incidents.map((i) => i.id));
    for (const id of this.rooms.keys()) if (!live.has(id)) this.rooms.delete(id);
    for (const i of v.incidents) {
      if (this.rooms.has(i.id)) continue;
      const used = [...this.rooms.values()];
      const count = (r) => used.filter((x) => x === r).length;
      this.rooms.set(i.id, count("#incident-1") <= count("#incident-2") ? "#incident-1" : "#incident-2");
    }
  }

  // --- traffic: stopped cars at the junction the sim reports, gone when it says clear
  makeJam(t, v) {
    const g = new THREE.Group();
    const [jx, jy] = t.at;
    g.position.set(this.px(jx), 0, this.pz(jy));
    this.city.scene.add(g);
    const esc = v.incidents.find((i) => i.escort?.blocked_at?.[0] === jx && i.escort?.blocked_at?.[1] === jy)?.escort;
    const amb = esc && v.vehicles.find((x) => x.id === esc.ambulance);
    const cars = [];
    let n = 0;
    for (const [ax, az] of [[1, 0], [-1, 0], [0, 1], [0, -1]]) {
      // the arm the ambulance comes down keeps one car, so it's seen stopping behind the queue
      const facing = amb && (ax ? Math.sign(amb.pos[0] - jx) === ax : Math.sign(jy - amb.pos[1]) === az);
      for (let k = 0; k < (facing ? 1 : 3); k++) {
        const c = new THREE.Group();
        box(0.075, 0.022, 0.038, std(JAM_COLORS[n++ % JAM_COLORS.length]), 0, 0.019, 0, c);
        box(0.036, 0.016, 0.032, std(0x2a3947), -0.006, 0.036, 0, c);
        const d = 0.07 + k * 0.095, lane = (k % 2 ? 1 : -1) * 0.02;
        c.position.set(ax * d - az * lane, 0.006, az * d + ax * lane);
        c.rotation.y = az ? Math.PI / 2 : 0;
        c.userData = { ax, az };
        g.add(c);
        cars.push(c);
      }
    }
    const tag = this.city.tag(`j:${t.at}`, "prog jam");
    tag.el.innerHTML = `<span class="pRing"><i></i></span><span class="pText"><b></b><small></small></span>`;
    tag.v.set(g.position.x, 0.42, g.position.z);
    tag.on = true;
    tag.el.hidden = false;
    return { g, cars, tag, title: tag.el.querySelector("b"), sub: tag.el.querySelector("small"), ring: tag.el.querySelector(".pRing"), gone: 0 };
  }

  syncJams(v, now) {
    const live = new Set(v.traffic.map((t) => String(t.at)));
    for (const [key, j] of this.jams) if (!live.has(key)) this.dropJam(key, j);
    for (const t of v.traffic) {
      const key = String(t.at);
      if (!this.jams.has(key)) {
        if (t.status === "cleared") continue; // cleared before this page saw it
        this.jams.set(key, this.makeJam(t, v));
        this.focus = t.at;
        this.focusUntil = now + FOCUS_MS;
      }
      const j = this.jams.get(key);
      j.status = t.status;
      const clearing = t.status === "clearing";
      const title = clearing ? `Clearing traffic ${t.progress_pct}%` : "⛔ Ambulance blocked";
      if (j.title.textContent !== title) j.title.textContent = title;
      const sub = clearing ? "Police at the junction" : "Police on the way";
      if (j.sub.textContent !== sub) j.sub.textContent = sub;
      j.ring.style.setProperty("--p", clearing ? t.progress_pct : 0);
      j.tag.el.classList.toggle("clearing", clearing);
      j.tag.el.hidden = t.status === "cleared";
    }
  }

  dropJam(key, j) {
    this.city.scene.remove(j.g);
    j.tag.on = false;
    j.tag.el.remove();
    this.city.tags.delete(`j:${key}`);
    this.jams.delete(key);
  }

  animJams(dt) {
    for (const j of this.jams.values()) {
      if (j.status !== "cleared" || j.gone >= DISPERSE_S) continue;
      j.gone += dt; // cars pull away down their own roads, then the group is empty
      const step = 0.3 * dt * Math.min(1, j.gone * 1.5);
      for (const c of j.cars) {
        c.position.x += c.userData.ax * step;
        c.position.z += c.userData.az * step;
        c.visible = j.gone < DISPERSE_S;
      }
    }
  }

  // Only the drone and its confidence are served while it searches; the spot stays unknown here.
  animSensors(wall) {
    const list = this.view.detections || [];
    const live = new Set();
    this.cones.forEach((k, i) => {
      const h = list[i];
      const r = h && this.city.drones.get(h.drone);
      k.c.visible = k.ring.visible = !!(r && r.g.visible);
      if (!k.c.visible) return;
      const p = r.g.position, pulse = (wall * 1.4) % 1;
      const wide = 0.16 + 0.1 * (h.confidence_pct / 100);
      k.c.position.copy(p);
      k.c.scale.set(wide, p.y, wide);
      k.c.material.opacity = 0.12 + 0.1 * Math.sin(wall * 6);
      k.ring.position.set(p.x, 0.02, p.z);
      k.ring.scale.setScalar(wide * (0.4 + pulse));
      k.ring.material.opacity = 0.8 * (1 - pulse);
      const key = `h:${h.drone}`;
      live.add(key);
      this.city.label(key, "heat", `Possible heat source · ${h.confidence_pct}% · simulated sensor`, p.x, p.y + 0.5 + 0.04 * Math.sin(wall * 2), p.z);
    });
    for (const key of this.city.tags.keys()) if (key.startsWith("h:") && !live.has(key)) this.city.unlabel(key);
  }

  animEscorts() {
    const list = this.view.incidents.filter((i) => i.escort?.drone && i.escort.status !== "delivered");
    this.links.forEach((l, i) => {
      const e = list[i]?.escort;
      const v = e && this.vehicles.get(e.ambulance);
      const d = e && this.city.drones.get(e.drone);
      l.visible = !!(v && d);
      if (v && d) this.city.placeBeam(l, d.g.position, v.g.position);
    });
  }

  // --- vehicles
  syncVehicle(x, i, now) {
    let r = this.vehicles.get(x.id);
    if (!r) {
      const mesh = makeVehicle(x.kind);
      this.city.scene.add(mesh.g);
      const along = this.ys.has(x.pos[1]);
      r = { ...mesh, m: { from: x.pos, to: x.pos, cur: x.pos, t0: 0, dur: 1 }, yaw: along ? 0 : Math.PI / 2, side: i % 2 ? 1 : -1, along, park: 1, lane: 0 };
      this.vehicles.set(x.id, r);
    }
    r.x = x;
    const m = r.m;
    if (m.to[0] !== x.pos[0] || m.to[1] !== x.pos[1]) {
      Object.assign(m, { from: m.cur, to: x.pos, t0: now, dur: Math.min(this.pollMs * 1.25, 900) });
    }
  }

  animVehicles(dt, now, wall) {
    for (const r of this.vehicles.values()) {
      const m = r.m, s = r.x.state;
      const k = Math.min(1, (now - m.t0) / m.dur);
      m.cur = k >= 1 ? m.to : [m.from[0] + (m.to[0] - m.from[0]) * k, m.from[1] + (m.to[1] - m.from[1]) * k];
      const dx = m.to[0] - m.from[0], dy = m.to[1] - m.from[1];
      if (Math.hypot(dx, dy) > 1e-4 && k < 1) {
        let d = Math.atan2(dy, dx) - r.yaw;
        d = Math.atan2(Math.sin(d), Math.cos(d));
        r.yaw += d * (1 - Math.exp(-7 * dt));
      }
      const blend = 1 - Math.exp(-3 * dt);
      r.park += ((s === "parked" ? 1 : 0) - r.park) * blend;
      r.lane += ((MOVING.includes(s) ? 1 : 0) - r.lane) * blend;
      const ox = (r.along ? r.side * PARK_KM : 0) * r.park + Math.sin(r.yaw) * LANE_KM * r.lane;
      const oy = (r.along ? 0 : r.side * PARK_KM) * r.park - Math.cos(r.yaw) * LANE_KM * r.lane;
      r.g.position.set(this.px(m.cur[0] + ox), 0.006, this.pz(m.cur[1] + oy));
      r.g.rotation.y = r.yaw;
      const on = SIREN.includes(s);
      const beat = Math.floor(wall * (s === "working" ? 3 : 7)) % 2;
      r.lamps.forEach((l, i) => { l.visible = on && beat === i; });
      if (s === "en_route" || s === "transporting") {
        const kind = r.x.id.replace(/_\d+$/, "").replace(/_/g, " ");
        this.city.label(`v:${r.x.id}`, "muted", s === "transporting" ? `${kind} · patient on board` : kind, r.g.position.x, 0.17, r.g.position.z);
      } else this.city.unlabel(`v:${r.x.id}`);
    }
  }

  // --- scenes
  makeScene(inc) {
    const g = new THREE.Group();
    const road = this.ys.has(inc.at[1]);
    g.position.set(this.px(inc.at[0]), 0, this.pz(inc.at[1]));
    this.city.scene.add(g);
    const add = (geo, mat, y = 0) => { const n = new THREE.Mesh(geo, mat); n.position.y = y; g.add(n); return n; };
    const beacon = add(new THREE.CylinderGeometry(0.05, 0.05, 2, 16, 1, true), new THREE.MeshBasicMaterial({ color: C.red, transparent: true, opacity: 0.4, blending: THREE.AdditiveBlending, depthWrite: false, side: THREE.DoubleSide }), 1);
    const wave = add(new THREE.RingGeometry(0.1, 0.125, 48), new THREE.MeshBasicMaterial({ color: C.red, transparent: true, depthWrite: false, side: THREE.DoubleSide }), 0.02);
    wave.rotation.x = -Math.PI / 2;
    const s = { g, beacon, wave, inc, kind: inc.kind, flames: [], smoke: [], props: new THREE.Group(), heat: 1 };
    g.add(s.props);
    const sideX = road ? 0 : 0.17, sideZ = road ? -0.17 : 0; // beside the road, not on it
    const smokeAt = new THREE.Vector3(inc.kind === "fire" ? sideX : 0, 0.08, inc.kind === "fire" ? sideZ : 0);
    if (inc.kind === "fire") {
      box(0.2, 0.12, 0.18, std(0x2a2522), sideX, 0.06, sideZ, s.props); // the burning shop
      const geo = new THREE.ConeGeometry(0.035, 0.15, 7);
      for (let i = 0; i < 11; i++) {
        const mat = new THREE.MeshBasicMaterial({ color: i % 3 ? 0xff7a1c : 0xffd24a, transparent: true, opacity: 0.92, blending: THREE.AdditiveBlending, depthWrite: false });
        const f = new THREE.Mesh(geo, mat);
        f.position.set(sideX + (i - 5) * 0.017, 0.14 + (i % 3) * 0.012, sideZ + ((i * 37) % 11 - 5) * 0.014);
        f.userData.seed = i * 1.7;
        g.add(f);
        s.flames.push(f);
      }
    } else {
      const car = (hex, x, z, yaw, roll) => {
        const c = new THREE.Group();
        box(0.1, 0.028, 0.05, std(hex), 0, 0.022, 0, c);
        box(0.05, 0.024, 0.044, std(0x2a3947), -0.008, 0.046, 0, c);
        c.position.set(x, 0.006, z); c.rotation.set(roll, yaw, roll * 0.6);
        c.scale.setScalar(1.25);
        s.props.add(c);
      };
      car(0xb8bfc6, -0.05, -0.02, 0.5, 0.12);
      car(0x8a2f26, 0.06, 0.03, 2.1, -0.16);
      const cone = new THREE.ConeGeometry(0.011, 0.03, 8), orange = std(0xff7a1c);
      for (const [x, z] of [[-0.17, 0.05], [-0.13, -0.09], [0.15, -0.07], [0.18, 0.08]]) {
        const n = new THREE.Mesh(cone, orange);
        n.position.set(x, 0.016, z);
        s.props.add(n);
      }
    }
    const count = inc.kind === "fire" ? 18 : 6;
    for (let i = 0; i < count; i++) {
      const sp = new THREE.Sprite(new THREE.SpriteMaterial({ map: this.soft, color: 0x7d7b78, transparent: true, opacity: 0, depthWrite: false, fog: false }));
      sp.position.copy(smokeAt);
      sp.userData = { p: i / count, x: smokeAt.x, z: smokeAt.z, r: (i * 53 % 17) / 17 - 0.5 };
      g.add(sp);
      s.smoke.push(sp);
    }
    // the floating status: progress ring, plain label, one small recording line
    const t = this.city.tag(`p:${inc.id}`, "prog");
    t.el.innerHTML = `<span class="pRing"><i></i></span><span class="pText"><b></b><small></small></span>`;
    t.text = null;
    s.tag = t;
    s.title = t.el.querySelector("b");
    s.sub = t.el.querySelector("small");
    s.ring = t.el.querySelector(".pRing");
    t.v.set(g.position.x, 0.82, g.position.z);
    t.on = true;
    t.el.hidden = false;
    return s;
  }

  syncScene(s, inc) {
    s.inc = inc;
    const working = inc.stage === "working" || inc.stage === "care_call";
    const line = working && inc.progress_label ? inc.progress_label : STAGE[inc.stage] || inc.stage;
    const ask = inc.decision;
    const room = this.rooms.get(inc.id) || "#incident-1"; // every call is in the incident room (L63)
    const title = ask ? `◆ Waiting for the ${ROLE[ask.role] || "team"} in ${room}` : `${ICON[inc.kind] || ""} ${line}`;
    s.tag.el.classList.toggle("wait", !!ask);
    if (s.title.textContent !== title) s.title.textContent = title;
    const rec = inc.drone && this.view.drones.find((d) => d.id === inc.drone)?.recording;
    const name = inc.name || inc.scene;
    const found = inc.found_by ? ` · found by ${inc.found_by.drone} on patrol` : "";
    const place = `${name}${found}${rec ? ` · ● recording at scene (${inc.drone})` : ""}`;
    const sub = ask ? `${name}: ${ask.prompt}` : place;
    if (s.sub.textContent !== sub) s.sub.textContent = sub;
    s.ring.style.setProperty("--p", working ? inc.progress_pct : inc.stage === "watching" || inc.stage === "holding" ? 100 : 0);
    s.ring.classList.toggle("idle", !working);
    s.heat = working ? Math.max(0.08, 1 - inc.progress_pct / 100) : ["alert", "responding", "unfound"].includes(inc.stage) ? 1 : 0;
    s.unfound = inc.stage === "unfound";
    s.props.visible = inc.kind === "fire" || !["watching", "holding"].includes(inc.stage);
  }

  dropScene(id, s) {
    this.city.scene.remove(s.g);
    s.tag.on = false;
    s.tag.el.hidden = true;
    s.tag.el.remove();
    this.city.tags.delete(`p:${id}`);
    this.scenes.delete(id);
  }

  animScenes(dt, wall) {
    let n = 0;
    for (const l of this.lamps) l.intensity = 0;
    for (const s of this.scenes.values()) {
      const alert = s.heat >= 1;
      s.beacon.visible = alert && !s.unfound; // nobody has reported it: no alert beacon yet
      s.beacon.material.opacity = 0.26 + 0.16 * Math.sin(wall * 4);
      const p = (wall * 0.6) % 1;
      s.wave.visible = s.heat > 0 && !s.unfound;
      s.wave.scale.setScalar(1 + p * 4.5);
      s.wave.material.opacity = 0.7 * (1 - p);
      for (const f of s.flames) {
        const q = f.userData.seed + wall * 9;
        const size = s.heat * (0.8 + 0.35 * Math.sin(q));
        f.visible = s.heat > 0.02;
        f.scale.set(s.heat + 0.15, Math.max(0.05, size * 1.5), s.heat + 0.15);
      }
      const dense = s.kind === "fire" ? 0.15 + s.heat * 0.6 : 0.3 * s.heat;
      for (const sp of s.smoke) {
        const u = sp.userData;
        u.p = (u.p + dt * 0.16) % 1;
        sp.position.set(u.x + u.r * 0.2 * u.p + u.p * 0.14, 0.12 + u.p * (s.kind === "fire" ? 0.95 : 0.4), u.z + u.r * 0.14 * u.p);
        const w = 0.09 + u.p * (s.kind === "fire" ? 0.42 : 0.2);
        sp.scale.set(w, w, 1);
        sp.material.opacity = dense * Math.sin(Math.PI * u.p);
        sp.visible = dense > 0.01 && s.inc.stage !== "holding";
      }
      if (s.kind === "fire" && n < this.lamps.length) {
        const l = this.lamps[n++];
        l.position.set(s.g.position.x, 0.26, s.g.position.z);
        l.intensity = 2.4 * s.heat * (0.85 + 0.15 * Math.sin(wall * 13));
      }
    }
  }

  // calm slow drift; a brief move to a new scene, then back
  animCamera(now, wall) {
    const drift = 0.2 * Math.sin(wall / 16);
    if (this.focus && now < this.focusUntil) {
      this.city.direct({ x: this.focus[0], y: this.focus[1], dist: 4.8, polar: 0.92, azim: drift });
    } else {
      this.focus = null;
      this.city.direct({ x: 3, y: 3, dist: 9.4, polar: 0.78 + 0.03 * Math.sin(wall / 23), azim: drift });
    }
  }

  frame(dt, now, wall) {
    if (!this.view) return;
    this.animVehicles(dt, now, wall);
    this.animJams(dt);
    this.animEscorts();
    this.animSensors(wall);
    this.animScenes(dt, wall);
    this.animCamera(now, wall);
  }
}
