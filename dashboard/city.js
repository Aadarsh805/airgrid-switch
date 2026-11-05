// Live 3D city (DECISIONS L52, L53). World geometry comes only from /world, live data only from
// the state frames. Everything else here is display: glide, rotors, bobbing, trails, camera.
// Nothing in this file feeds back to the sim, and it computes no coverage, ETA or violation.
import * as THREE from "three";
import { OrbitControls } from "three/addons/controls/OrbitControls.js";
import { GLTFLoader } from "three/addons/loaders/GLTFLoader.js";

const C = {
  bg: 0x0d141b, land: 0x131e28, panel: 0x16222d, hairline: 0x31485c, text: 0xe8eef4,
  muted: 0x8fa3b5, sky: 0x57b7e8, green: 0x2fa36b, amber: 0xd99a26, red: 0xc8452c, gold: 0xc6a15b,
};
const CRUISE = 0.46; // display altitude in km-scale scene units, not a sim value
const JUMP_KM = 1.2; // a larger step between frames is a skip: snap, don't glide
const TRAIL_MIN = 4; // trail fades over this many sim-minutes, so it freezes on pause
const LOOP_PERIOD_MIN = 14; // sim-minutes per display lap of a patrol loop
const CHASE = ["track", "relay", "hold"];
const HOME_DIST = 9.4;
const CHASE_DIST = 6;
// Decorative side streets (not /world geometry): gaps between lots where ground units drive.
const SIDE_X = [1.5, 3, 4.5];
const SIDE_Y = [2.25, 4];
const ROOFS = [0x3f5365, 0x4a4f58, 0x57493f, 0x3c5a5c, 0x5e5a52, 0x2f4152];
const WORD = {
  patrol: "patrol", incident: "on scene", perimeter: "perimeter", corridor: "corridor", hold: "HOLD",
  track: "tracking", relay: "relay", returning: "going home", search: "search", roam: "patrol",
};

const smooth = (rate, dt) => 1 - Math.exp(-rate * dt);
const hashStr = (s) => [...s].reduce((h, c) => (Math.imul(h, 31) + c.charCodeAt(0)) >>> 0, 7);
function rng(seed) { // mulberry32
  let a = seed >>> 0;
  return () => {
    a = (a + 0x6d2b79f5) >>> 0;
    let t = a;
    t = Math.imul(t ^ (t >>> 15), t | 1);
    t ^= t + Math.imul(t ^ (t >>> 7), t | 61);
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}

function hatchTexture(hex, alpha) {
  const c = document.createElement("canvas");
  c.width = c.height = 64;
  const g = c.getContext("2d");
  g.strokeStyle = hex; g.globalAlpha = alpha; g.lineWidth = 12;
  for (let i = -64; i < 128; i += 32) { g.beginPath(); g.moveTo(i, 64); g.lineTo(i + 64, 0); g.stroke(); }
  const t = new THREE.CanvasTexture(c);
  t.wrapS = t.wrapT = THREE.RepeatWrapping;
  t.colorSpace = THREE.SRGBColorSpace;
  return t;
}

function windowTexture(lit) {
  const c = document.createElement("canvas");
  c.width = c.height = 128;
  const g = c.getContext("2d");
  g.fillStyle = lit ? "#000" : "#fff";
  g.fillRect(0, 0, 128, 128);
  const r = rng(11);
  for (let y = 8; y < 120; y += 18) {
    for (let x = 8; x < 120; x += 18) {
      const on = r() < 0.34;
      g.fillStyle = lit ? (on ? "#ffcf8a" : "#000") : on ? "#ffdf9e" : "#5a6672";
      g.fillRect(x, y, 10, 10);
    }
  }
  const t = new THREE.CanvasTexture(c);
  t.colorSpace = THREE.SRGBColorSpace;
  return t;
}

// The shared seeded plan of the district: every lot is a building, park or car park. Both the
// ground texture and placeBuildings read it, so they always line up with /world.
function planLots(world) {
  const [W, H] = world.district.size_km;
  const J = Object.values(world.junctions);
  const xs = [...new Set([0, W, ...J.map((p) => p[0]), ...SIDE_X])].sort((a, b) => a - b);
  const ys = [...new Set([0, H, ...J.map((p) => p[1]), ...SIDE_Y])].sort((a, b) => a - b);
  const blocked = (x0, x1, y0, y1) =>
    world.zones.some((z) => x1 > z.rect.x[0] - 0.05 && x0 < z.rect.x[1] + 0.05 && y1 > z.rect.y[0] - 0.05 && y0 < z.rect.y[1] + 0.05) ||
    Object.values(world.docks).some((d) => {
      const m = d.kind === "hub" ? 0.3 : 0.13;
      return x1 > d.at[0] - m && x0 < d.at[0] + m && y1 > d.at[1] - m && y0 < d.at[1] + m;
    }) ||
    (world.stations || []).some((s) => x1 > s.at[0] - 0.36 && x0 < s.at[0] + 0.36 && y1 > s.at[1] - 0.26 && y0 < s.at[1] + 0.26);
  const patch = (x, y) => { // coarse patches, so parks read as parks and not as stray lots
    const v = rng(Math.floor(x / 0.62) * 7919 + Math.floor(y / 0.62) * 104729 + 3)();
    return v < 0.15 ? "park" : v < 0.26 ? "parking" : "building";
  };
  const lots = [];
  const inset = 0.075;
  for (let i = 0; i + 1 < xs.length; i++) {
    for (let j = 0; j + 1 < ys.length; j++) {
      const r = rng(Math.round(xs[i] * 1000) * 7919 + Math.round(ys[j] * 1000));
      const x1 = xs[i + 1] - inset, y1 = ys[j + 1] - inset;
      for (let x = xs[i] + inset; x < x1 - 0.1;) {
        const w = Math.min(0.14 + r() * 0.16, x1 - x);
        for (let y = ys[j] + inset; y < y1 - 0.1;) {
          const d = Math.min(0.14 + r() * 0.16, y1 - y);
          const free = !blocked(x, x + w, y, y + d);
          const kind = free ? patch(x + w / 2, y + d / 2) : "plaza";
          const centre = 1 - Math.hypot(x - W / 2, y - H / 2) / (W * 0.72);
          lots.push({ kind, x: x + w / 2, y: y + d / 2, w: w - 0.03, d: d - 0.03, h: 0.05 + r() * r() * 0.22 * (0.5 + centre), tone: r(), empty: r() < 0.1 });
          y += d;
        }
        x += w;
      }
    }
  }
  return { lots, xs, ys };
}

// Kenney CC0 models (see vendor/CREDITS.md). Every model is one mesh sharing one colormap, so each
// becomes one InstancedMesh. Any load failure leaves the seeded boxes in place.
const BLOCKS = [..."abcdefghijklmn"].map((c) => `low-detail-building-${c}`).concat("low-detail-building-wide-a", "low-detail-building-wide-b");
const TOWERS = [..."abcde"].map((c) => `building-skyscraper-${c}`);
async function loadModels() {
  const loader = new GLTFLoader();
  const get = async (path) => {
    const g = await loader.loadAsync(path);
    let mesh = null;
    g.scene.traverse((o) => { if (o.isMesh && !mesh) mesh = o; });
    mesh.geometry.computeBoundingBox();
    return mesh;
  };
  const [blocks, towers, lamp] = await Promise.all([
    Promise.all(BLOCKS.map((n) => get(`vendor/kenney/commercial/${n}.glb`))),
    Promise.all(TOWERS.map((n) => get(`vendor/kenney/commercial/${n}.glb`))),
    get("vendor/kenney/roads/light-square.glb"),
  ]);
  return { blocks, towers, lamp };
}

function eveningMaterial(mesh) {
  const m = mesh.material.clone();
  m.color.setRGB(0.3, 0.34, 0.42);
  m.emissive.setRGB(1, 0.78, 0.5);
  m.emissiveMap = m.map;
  m.emissiveIntensity = 0.06;
  m.roughness = 0.9;
  return m;
}

function placeInstanced(models, lots, W, H) {
  const group = new THREE.Group();
  const buckets = new Map();
  for (const l of lots) {
    const pool = l.h > 0.2 ? models.towers : models.blocks;
    const mesh = pool[Math.floor(l.tone * 9973) % pool.length];
    if (!buckets.has(mesh)) buckets.set(mesh, []);
    buckets.get(mesh).push(l);
  }
  const q = new THREE.Quaternion(), up = new THREE.Vector3(0, 1, 0), m = new THREE.Matrix4(), color = new THREE.Color();
  for (const [mesh, items] of buckets) {
    const size = mesh.geometry.boundingBox.getSize(new THREE.Vector3());
    const im = new THREE.InstancedMesh(mesh.geometry, eveningMaterial(mesh), items.length);
    items.forEach((l, i) => {
      const quarter = Math.floor(l.tone * 7919) % 4;
      const swap = quarter % 2 === 1;
      const sx = swap ? size.z : size.x, sz = swap ? size.x : size.z;
      const scale = Math.min(l.w / sx, l.d / sz);
      const sy = Math.min(scale, (l.h > 0.2 ? 0.34 : 0.05 + l.h) / size.y);
      q.setFromAxisAngle(up, quarter * Math.PI / 2);
      m.compose(new THREE.Vector3(l.x - W / 2, 0, H / 2 - l.y), q, new THREE.Vector3(scale, sy, scale));
      im.setMatrixAt(i, m);
      im.setColorAt(i, color.set(ROOFS[Math.floor(l.tone * 41) % ROOFS.length]).lerp(new THREE.Color(0xffffff), 0.72));
    });
    im.castShadow = im.receiveShadow = true;
    group.add(im);
  }
  return group;
}

function placeLamps(world, lamp) {
  const [W, H] = world.district.size_km;
  const ys = [...new Set(Object.values(world.junctions).map((p) => p[1]))];
  const spots = [];
  for (const y of ys) {
    for (let x = 0.15; x < W; x += 0.6) for (const o of [-0.052, 0.052]) spots.push([x, y + o]);
  }
  const im = new THREE.InstancedMesh(lamp.geometry, eveningMaterial(lamp), spots.length);
  const m = new THREE.Matrix4();
  spots.forEach(([x, y], i) => { m.makeScale(0.2, 0.2, 0.2).setPosition(x - W / 2, 0, H / 2 - y); im.setMatrixAt(i, m); });
  return im;
}

// Buildings are decoration, seeded from coordinates so every load is the same city. All building
// generation stays behind this one function; with no models it draws the seeded boxes.
export function placeBuildings(world, models = null) {
  const [W, H] = world.district.size_km;
  const lots = planLots(world).lots.filter((l) => l.kind === "building" && !l.empty);
  if (models) return placeInstanced(models, lots, W, H);
  const box = new THREE.BoxGeometry(1, 1, 1);
  box.translate(0, 0.5, 0);
  const side = new THREE.MeshStandardMaterial({ map: windowTexture(false), emissive: 0xffffff, emissiveIntensity: 0.55, emissiveMap: windowTexture(true), roughness: 0.85 });
  const roof = new THREE.MeshStandardMaterial({ color: 0xffffff, roughness: 0.95 });
  const mesh = new THREE.InstancedMesh(box, [side, side, roof, roof, side, side], lots.length);
  const m = new THREE.Matrix4(), color = new THREE.Color();
  lots.forEach((b, i) => {
    m.makeScale(b.w, b.h, b.d).setPosition(b.x - W / 2, 0, H / 2 - b.y);
    mesh.setMatrixAt(i, m);
    mesh.setColorAt(i, color.set(ROOFS[Math.floor(b.tone * ROOFS.length)]));
  });
  mesh.castShadow = mesh.receiveShadow = true;
  const group = new THREE.Group();
  group.add(mesh);
  return group;
}

// Evening-aerial ground, generated and seeded, aligned 1:1 with /world.
function groundTexture(world) {
  const S = 4096;
  const c = document.createElement("canvas");
  c.width = c.height = S;
  const g = c.getContext("2d");
  const [W, H] = world.district.size_km;
  const K = S / W;
  const X = (x) => x * K, Y = (y) => (H - y) * K;
  g.fillStyle = "#1a2530"; g.fillRect(0, 0, S, S);
  const r = rng(4242);
  const rect = (l, fill, grow = 0) => {
    g.fillStyle = fill;
    g.fillRect(X(l.x - l.w / 2 - grow), Y(l.y + l.d / 2 + grow), (l.w + grow * 2) * K, (l.d + grow * 2) * K);
  };
  const { lots } = planLots(world);
  for (const l of lots) {
    if (l.kind === "park") {
      rect(l, "#1d3528", 0.016);
      const n = Math.round(l.w * l.d * 900);
      for (let i = 0; i < n; i++) {
        const x = X(l.x + (r() - 0.5) * l.w), y = Y(l.y + (r() - 0.5) * l.d), rad = 7 + r() * 11;
        g.fillStyle = "rgba(6,12,10,.45)";
        g.beginPath(); g.arc(x + 4, y + 5, rad, 0, 7); g.fill();
        g.fillStyle = ["#2a5238", "#23472f", "#335f42"][Math.floor(r() * 3)];
        g.beginPath(); g.arc(x, y, rad, 0, 7); g.fill();
      }
    } else if (l.kind === "parking") {
      rect(l, "#2b3540");
      g.strokeStyle = "#6c7a87"; g.lineWidth = 2.5;
      const x0 = X(l.x - l.w / 2), x1 = X(l.x + l.w / 2), y0 = Y(l.y + l.d / 2), y1 = Y(l.y - l.d / 2);
      for (let x = x0 + 8; x < x1; x += 15) {
        for (const [a, b] of [[y0 + 4, y0 + 30], [y1 - 30, y1 - 4]]) {
          g.beginPath(); g.moveTo(x, a); g.lineTo(x, b); g.stroke();
          if (r() < 0.5) {
            g.fillStyle = ["#7f8d99", "#8a4f45", "#44627c", "#c0c6cc"][Math.floor(r() * 4)];
            g.fillRect(x + 3, a + 3, 9, 20);
          }
        }
      }
    } else if (l.kind === "plaza") {
      rect(l, "#222e39");
    } else {
      rect(l, "#212c37", 0.012); // pavement around a building
    }
  }
  const J = world.junctions;
  const xs = [...new Set(Object.values(J).map((p) => p[0]))];
  const ys = [...new Set(Object.values(J).map((p) => p[1]))];
  const line = (x0, y0, x1, y1, col, w, dash = []) => {
    g.strokeStyle = col; g.lineWidth = w; g.setLineDash(dash);
    g.beginPath(); g.moveTo(X(x0), Y(y0)); g.lineTo(X(x1), Y(y1)); g.stroke();
  };
  for (const x of SIDE_X) { line(x, 0, x, H, "#2c3743", 34); line(x, 0, x, H, "#6c7a87", 2, [14, 22]); }
  for (const y of SIDE_Y) { line(0, y, W, y, "#2c3743", 34); line(0, y, W, y, "#6c7a87", 2, [14, 22]); }
  for (const x of xs) line(x, 0, x, H, "#343f4a", 62);
  for (const y of ys) line(0, y, W, y, "#3d4955", 84);
  for (const x of xs) {
    for (const o of [-27, 27]) line(x + o / K, 0, x + o / K, H, "#7c8996", 2.5);
    line(x, 0, x, H, "#c9b67a", 3, [26, 30]);
  }
  for (const y of ys) {
    for (const o of [-38, 38]) line(0, y + o / K, W, y + o / K, "#7c8996", 2.5);
    line(0, y - 3 / K, W, y - 3 / K, "#c9a24f", 3);
    line(0, y + 3 / K, W, y + 3 / K, "#c9a24f", 3);
    for (const o of [-20, 20]) line(0, y + o / K, W, y + o / K, "#8e9aa6", 2.5, [20, 26]);
  }
  g.setLineDash([]);
  for (const x of xs) {
    for (const y of ys) { // junction boxes with zebra crossings
      g.fillStyle = "#3d4955"; g.fillRect(X(x) - 32, Y(y) - 43, 64, 86);
      g.fillStyle = "#aab4be";
      for (let i = -28; i < 28; i += 11) {
        g.fillRect(X(x) + i, Y(y) - 58, 6, 15); g.fillRect(X(x) + i, Y(y) + 43, 6, 15);
      }
      for (let i = -38; i < 38; i += 11) {
        g.fillRect(X(x) - 48, Y(y) + i, 15, 6); g.fillRect(X(x) + 33, Y(y) + i, 15, 6);
      }
    }
  }
  for (const y of ys) { // warm street-light pools along the arterials
    for (let x = 0.15; x < W; x += 0.3) {
      for (const o of [-52, 52]) {
        const px = X(x), py = Y(y) + o;
        const glow = g.createRadialGradient(px, py, 1, px, py, 38);
        glow.addColorStop(0, "rgba(255,196,110,.34)");
        glow.addColorStop(1, "rgba(255,196,110,0)");
        g.fillStyle = glow; g.fillRect(px - 38, py - 38, 76, 76);
      }
    }
  }
  for (const s of Object.values(world.sectors)) {
    if (s.x[0] > 0) line(s.x[0], s.y[0], s.x[0], s.y[1], "#5b7084", 3, [10, 26]);
  }
  g.setLineDash([]);
  g.textBaseline = "middle";
  g.font = "600 60px system-ui, sans-serif";
  const label = (text, x, y, col) => {
    g.lineWidth = 8; g.strokeStyle = "rgba(13,20,27,.85)"; g.strokeText(text, x, y);
    g.fillStyle = col; g.fillText(text, x, y);
  };
  for (const [id, p] of Object.entries(J)) label(id, X(p[0]) - 130, Y(p[1]) - 92, "#B8C6D2");
  for (const [id, d] of Object.entries(world.docks)) {
    if (d.kind !== "hub") label(id, X(d.at[0]) + 56, Y(d.at[1]) + 92, "#B8C6D2");
    else label(id, X(d.at[0]) - 80, Y(d.at[1]) + 200, "#B8C6D2");
  }
  g.font = "700 76px system-ui, sans-serif";
  for (const [id, s] of Object.entries(world.sectors)) label(id, X(s.x[0]) + 36, Y(s.y[1]) + 62, "#7C93A8");
  const t = new THREE.CanvasTexture(c);
  t.colorSpace = THREE.SRGBColorSpace;
  t.anisotropy = 8;
  return t;
}

// A flat ribbon of the last positions. Fades with sim time; a null entry breaks the ribbon.
class Trail {
  constructor(scene, hex, max, width, fade = true) {
    this.max = max; this.width = width; this.fade = fade;
    this.pts = [];
    this.color = new THREE.Color(hex);
    const g = new THREE.BufferGeometry();
    this.pos = new THREE.BufferAttribute(new Float32Array(max * 6), 3);
    this.col = new THREE.BufferAttribute(new Float32Array(max * 6), 3);
    g.setAttribute("position", this.pos);
    g.setAttribute("color", this.col);
    const idx = [];
    for (let i = 0; i + 1 < max; i++) { const a = i * 2; idx.push(a, a + 1, a + 2, a + 1, a + 3, a + 2); }
    g.setIndex(idx);
    this.mesh = new THREE.Mesh(g, new THREE.MeshBasicMaterial({ vertexColors: true, transparent: true, blending: THREE.AdditiveBlending, depthWrite: false, side: THREE.DoubleSide }));
    this.mesh.frustumCulled = false;
    scene.add(this.mesh);
  }

  add(x, y, z, t) {
    const last = this.pts.at(-1);
    if (last && Math.hypot(last.x - x, last.z - z) + Math.abs(last.y - y) < 0.018) return;
    this.pts.push({ x, y, z, t });
    if (this.pts.length > this.max) this.pts.shift();
  }

  gap() { if (this.pts.at(-1)) this.pts.push(null); }

  clear() { this.pts = []; this.draw(0); }

  draw(now) {
    if (this.fade) while (this.pts.length && (!this.pts[0] || now - this.pts[0].t > TRAIL_MIN)) this.pts.shift();
    const n = this.pts.length;
    for (let i = 0; i < this.max; i++) {
      const p = i < n ? this.pts[i] : null;
      const q = p ? this.pts[i + 1] || this.pts[i - 1] || p : null;
      if (!p) {
        for (const k of [i * 2, i * 2 + 1]) { this.pos.setXYZ(k, 0, -9, 0); this.col.setXYZ(k, 0, 0, 0); }
        continue;
      }
      let dx = q.x - p.x, dz = q.z - p.z;
      const len = Math.hypot(dx, dz) || 1;
      dx = (dx / len) * this.width; dz = (dz / len) * this.width;
      this.pos.setXYZ(i * 2, p.x - dz, p.y, p.z + dx);
      this.pos.setXYZ(i * 2 + 1, p.x + dz, p.y, p.z - dx);
      const a = this.fade ? Math.max(0, 1 - (now - p.t) / TRAIL_MIN) * 0.8 : 0.9;
      for (const k of [i * 2, i * 2 + 1]) this.col.setXYZ(k, this.color.r * a, this.color.g * a, this.color.b * a);
    }
    this.pos.needsUpdate = this.col.needsUpdate = true;
  }
}

const hull = new THREE.MeshStandardMaterial({ color: 0xc9d6e2, roughness: 0.45, metalness: 0.3 });
const dark = new THREE.MeshStandardMaterial({ color: 0x28333d, roughness: 0.6 });
const bladeMat = new THREE.MeshBasicMaterial({ color: C.text, transparent: true, opacity: 0.75 });
const discMat = new THREE.MeshBasicMaterial({ color: C.sky, transparent: true, opacity: 0.16, side: THREE.DoubleSide, depthWrite: false });

function makeQuad() {
  const g = new THREE.Group();
  const body = new THREE.Mesh(new THREE.BoxGeometry(0.07, 0.024, 0.09), hull);
  const nose = new THREE.Mesh(new THREE.SphereGeometry(0.012, 8, 6), new THREE.MeshBasicMaterial({ color: C.sky }));
  nose.position.set(0, 0, 0.05);
  g.add(body, nose);
  const rotors = [];
  for (const [sx, sz] of [[1, 1], [1, -1], [-1, 1], [-1, -1]]) {
    const arm = new THREE.Mesh(new THREE.BoxGeometry(0.11, 0.008, 0.01), dark);
    arm.position.set(sx * 0.045, 0, sz * 0.045);
    arm.rotation.y = -sx * sz * Math.PI / 4;
    const hub = new THREE.Group();
    hub.position.set(sx * 0.08, 0.016, sz * 0.08);
    const blade = new THREE.Mesh(new THREE.BoxGeometry(0.1, 0.002, 0.012), bladeMat);
    const blade2 = blade.clone(); blade2.rotation.y = Math.PI / 2;
    const disc = new THREE.Mesh(new THREE.CircleGeometry(0.052, 18), discMat);
    disc.rotation.x = -Math.PI / 2;
    hub.add(blade, blade2);
    hub.userData.disc = disc;
    disc.position.copy(hub.position);
    g.add(arm, hub, disc);
    rotors.push(hub);
  }
  const ring = new THREE.Mesh(new THREE.TorusGeometry(0.15, 0.008, 8, 44), new THREE.MeshBasicMaterial({ color: C.sky }));
  ring.rotation.x = Math.PI / 2;
  g.add(ring);
  return { g, rotors, ring };
}

function makeVehicle(hex, len = 0.08) {
  const g = new THREE.Group();
  const mat = new THREE.MeshStandardMaterial({ color: hex, roughness: 0.5 });
  const body = new THREE.Mesh(new THREE.BoxGeometry(len, 0.02, 0.038), mat);
  body.position.y = 0.016;
  const cab = new THREE.Mesh(new THREE.BoxGeometry(len * 0.5, 0.016, 0.032), mat);
  cab.position.set(-len * 0.05, 0.034, 0);
  const lamp = new THREE.Mesh(new THREE.BoxGeometry(0.012, 0.008, 0.03), new THREE.MeshBasicMaterial({ color: C.muted }));
  lamp.position.set(-len * 0.05, 0.046, 0);
  g.add(body, cab, lamp);
  g.userData.lamp = lamp;
  return g;
}

export class City {
  constructor(host, labelHost, world, opts = {}) {
    this.cityMode = !!opts.cityMode; // vehicles and incidents come from cityfx.js instead
    const probe = document.createElement("canvas");
    if (!probe.getContext("webgl2") && !probe.getContext("webgl")) throw new Error("no WebGL context");
    this.host = host; this.labelHost = labelHost; this.world = world;
    [this.W, this.H] = world.district.size_km;
    this.px = (x) => x - this.W / 2;
    this.pz = (y) => this.H / 2 - y;
    this.renderer = new THREE.WebGLRenderer({ antialias: true, powerPreference: "high-performance" });
    this.renderer.setPixelRatio(Math.min(devicePixelRatio, 2));
    this.renderer.toneMapping = THREE.ACESFilmicToneMapping;
    this.renderer.toneMappingExposure = 1.15;
    this.renderer.shadowMap.enabled = true;
    this.renderer.shadowMap.type = THREE.PCFSoftShadowMap;
    this.renderer.shadowMap.autoUpdate = false;
    host.prepend(this.renderer.domElement);
    this.scene = new THREE.Scene();
    this.scene.background = new THREE.Color(C.bg);
    this.scene.fog = new THREE.Fog(C.bg, 13, 26);
    this.camera = new THREE.PerspectiveCamera(36, 1, 0.05, 80);
    this.home = new THREE.Vector3(0, 0, 0.1);
    this.camera.position.set(0, 6.6, 6.7);
    this.controls = new OrbitControls(this.camera, this.renderer.domElement);
    this.controls.target.copy(this.home);
    Object.assign(this.controls, { enableDamping: true, dampingFactor: 0.08, minDistance: 2.5, maxDistance: 15, maxPolarAngle: 1.36, autoRotateSpeed: 0.18 });
    this.tags = new Map();
    this.buildStatic();
    this.reset();
    this.clock = new THREE.Clock();
    this.fps = 0; this.frames = 0; this.fpsT = performance.now();
    this.resize();
    new ResizeObserver(() => this.resize()).observe(host);
    this.renderer.domElement.addEventListener("webglcontextlost", (e) => { e.preventDefault(); this.onLost?.(); });
    this.renderer.setAnimationLoop(() => this.frame());
  }

  resize() {
    const w = this.host.clientWidth, h = this.host.clientHeight;
    if (!w || !h) return;
    this.renderer.setSize(w, h);
    this.camera.aspect = w / h;
    this.camera.updateProjectionMatrix();
  }

  // Game mode camera. A shot is {x, y (km), dist, polar, azim} or {follow: droneId, ...}; null
  // gives the camera back to the mouse. chaseCam sits behind a drone, north up the screen.
  direct(shot) { this.shot = shot; if (!shot && !this.chaseId) this.controls.enabled = true; }

  chaseCam(id) { this.chaseId = id; if (!id && !this.shot) this.controls.enabled = true; }

  setAutoOrbit(on) { this.controls.autoRotate = on; }

  directCamera(dt) {
    const c = this.controls;
    const k = smooth(this.chaseId ? 3.2 : 1.3, dt);
    const look = new THREE.Vector3(), pos = new THREE.Vector3();
    const chase = this.chaseId && this.drones.get(this.chaseId);
    if (chase) {
      const d = chase.g.position;
      look.set(d.x, 0.12, d.z - 0.75);
      pos.set(d.x, d.y + 0.95, d.z + 1.45);
    } else {
      const s = this.shot;
      const f = s.follow && this.drones.get(s.follow);
      if (f) look.set(f.g.position.x, 0, f.g.position.z);
      else look.set(this.px(s.x ?? this.W / 2), 0, this.pz(s.y ?? this.H / 2));
      pos.setFromSphericalCoords(s.dist, s.polar, s.azim).add(look);
    }
    c.enabled = false;
    c.autoRotate = false;
    c.target.lerp(look, k);
    this.camera.position.lerp(pos, k);
  }

  toggleOrbit() { this.controls.autoRotate = !this.controls.autoRotate; return this.controls.autoRotate; }

  buildStatic() {
    const { scene, world, W, H } = this;
    scene.add(new THREE.HemisphereLight(0xa9c1d6, 0x0d141b, 1.7));
    const sun = new THREE.DirectionalLight(0xffd9b0, 2.1); // low evening light
    sun.position.set(-5, 6, 4);
    sun.castShadow = true;
    sun.shadow.mapSize.set(2048, 2048);
    Object.assign(sun.shadow.camera, { left: -5, right: 5, top: 5, bottom: -5, near: 1, far: 18 });
    sun.shadow.radius = 4;
    scene.add(sun);
    const under = new THREE.Mesh(new THREE.PlaneGeometry(70, 70), new THREE.MeshBasicMaterial({ color: 0x0a1016 }));
    under.rotation.x = -Math.PI / 2; under.position.y = -0.02;
    scene.add(under);
    const ground = new THREE.Mesh(new THREE.PlaneGeometry(W, H), new THREE.MeshStandardMaterial({ map: groundTexture(world), roughness: 1 }));
    ground.rotation.x = -Math.PI / 2;
    ground.receiveShadow = true;
    scene.add(ground);
    this.buildings = placeBuildings(world);
    scene.add(this.buildings);
    this.modelState = "boxes";
    loadModels().then((models) => {
      const next = placeBuildings(world, models);
      scene.remove(this.buildings);
      this.buildings = next;
      scene.add(next, placeLamps(world, models.lamp));
      this.modelState = "kenney";
      this.shadowDone = false;
    }).catch((e) => { this.modelState = `boxes (${e.message})`; });
    const J = world.junctions;
    this.loops = {};
    for (const [id, loop] of Object.entries(world.loops)) {
      const pts = loop.junctions.map((j) => J[j]);
      const a = pts[0], b = pts.at(-1);
      const len = Math.hypot(b[0] - a[0], b[1] - a[1]);
      const ribbon = new THREE.Mesh(new THREE.PlaneGeometry(len, 0.05), new THREE.MeshBasicMaterial({ color: C.sky, transparent: true, opacity: 0.1, depthWrite: false }));
      ribbon.rotation.x = -Math.PI / 2;
      ribbon.position.set(this.px((a[0] + b[0]) / 2), 0.012, this.pz((a[1] + b[1]) / 2));
      scene.add(ribbon);
      const c = loop.center;
      const r = Math.max(...pts.map((p) => Math.hypot(p[0] - c[0], p[1] - c[1])));
      const ring = new THREE.Group();
      const edge = new THREE.Mesh(new THREE.RingGeometry(r - 0.03, r, 96), new THREE.MeshBasicMaterial({ color: C.sky, transparent: true, opacity: 0.3, depthWrite: false }));
      const fill = new THREE.Mesh(new THREE.CircleGeometry(r, 96), new THREE.MeshBasicMaterial({ color: C.sky, transparent: true, opacity: 0.035, depthWrite: false }));
      edge.rotation.x = fill.rotation.x = -Math.PI / 2;
      ring.add(edge, fill);
      ring.position.y = 0.014;
      scene.add(ring);
      // display flight path: along the loop's own junction line, seen from the loop centre
      this.loops[id] = { ribbon, ring, a: (len / 2) * 0.86, ux: (b[0] - a[0]) / len, uy: (b[1] - a[1]) / len };
    }
    this.buildDocks();
    this.zones = new Map();
    this.buildSectors();
    this.suspect = makeVehicle(C.red, 0.11);
    this.suspect.scale.setScalar(1.25);
    scene.add(this.suspect);
    this.beam = new THREE.Mesh(new THREE.CylinderGeometry(0.008, 0.008, 1, 8), new THREE.MeshBasicMaterial({ color: C.sky, transparent: true, opacity: 0.85 }));
    this.beam2 = this.beam.clone();
    this.pulse = new THREE.Mesh(new THREE.SphereGeometry(0.05, 16, 12), new THREE.MeshBasicMaterial({ color: C.text }));
    this.link = new THREE.Mesh(new THREE.CylinderGeometry(0.014, 0.014, 1, 8), new THREE.MeshBasicMaterial({ color: C.gold, transparent: true, opacity: 0.8 }));
    scene.add(this.beam, this.beam2, this.pulse, this.link);
    this.suspectTrail = new Trail(scene, C.red, 260, 0.016, false);
  }

  // Docks are closed boxes: one unit per slot, a hinged two-part lid and one status dot. The drone
  // stays hidden inside until it launches. Lids and hiding are display only.
  buildDocks() {
    this.docks = new Map();
    const W = 0.11;
    const body = new THREE.MeshStandardMaterial({ color: 0x3a4b5a, roughness: 0.7 });
    const lid = new THREE.MeshStandardMaterial({ color: 0x566a7c, roughness: 0.55 });
    const well = new THREE.MeshBasicMaterial({ color: 0x0a1016 });
    const dotGeo = new THREE.SphereGeometry(0.016, 10, 8);
    const boxGeo = new THREE.BoxGeometry(W, 0.05, W);
    const wellGeo = new THREE.BoxGeometry(W - 0.016, 0.002, W - 0.016);
    const lidGeo = new THREE.BoxGeometry(W / 2, 0.006, W);
    lidGeo.translate(W / 4, 0, 0); // hinged at its outer edge
    for (const [id, d] of Object.entries(this.world.docks)) {
      const g = new THREE.Group();
      g.position.set(this.px(d.at[0]), 0, this.pz(d.at[1]));
      const hub = d.kind === "hub";
      const top = hub ? 0.15 : 0.1;
      if (hub) { // a compact rooftop unit
        const b = new THREE.Mesh(new THREE.BoxGeometry(0.15 * d.slots + 0.05, top - 0.05, 0.19), new THREE.MeshStandardMaterial({ color: 0x33475a, roughness: 0.8 }));
        b.position.y = (top - 0.05) / 2; b.castShadow = true;
        g.add(b);
      } else {
        const pole = new THREE.Mesh(new THREE.CylinderGeometry(0.012, 0.018, top - 0.05, 8), new THREE.MeshStandardMaterial({ color: C.muted }));
        pole.position.y = (top - 0.05) / 2;
        g.add(pole);
      }
      const units = [];
      for (let i = 0; i < d.slots; i++) {
        const dx = hub ? (i - (d.slots - 1) / 2) * 0.15 : 0;
        const u = new THREE.Group();
        u.position.set(dx, top, 0);
        const box = new THREE.Mesh(boxGeo, body);
        box.position.y = -0.026; box.castShadow = true;
        const inside = new THREE.Mesh(wellGeo, well);
        inside.position.y = 0.0;
        const left = new THREE.Mesh(lidGeo, lid), right = new THREE.Mesh(lidGeo, lid);
        left.position.set(-W / 2, 0.004, 0);
        right.position.set(W / 2, 0.004, 0);
        right.rotation.y = Math.PI;
        const dot = new THREE.Mesh(dotGeo, new THREE.MeshBasicMaterial({ color: 0x44525f }));
        dot.position.set(0, -0.02, W / 2 + 0.004);
        u.add(box, inside, left, right, dot);
        g.add(u);
        units.push({ left, right, dot, open: 0, want: false, pulse: false, top, x: g.position.x + dx, z: g.position.z });
      }
      this.scene.add(g);
      this.docks.set(id, { g, units, top, hub, slots: d.slots, at: d.at });
    }
  }

  buildSectors() {
    this.sectors = new Map();
    for (const [id, s] of Object.entries(this.world.sectors)) {
      const w = s.x[1] - s.x[0], d = s.y[1] - s.y[0];
      const g = new THREE.Group();
      g.position.set(this.px(s.x[0] + w / 2), 0, this.pz(s.y[0] + d / 2));
      const dim = new THREE.Mesh(new THREE.BoxGeometry(w, 0.56, d), new THREE.MeshBasicMaterial({ color: C.bg, transparent: true, opacity: 0.62, depthWrite: false }));
      dim.position.y = 0.28;
      const edge = new THREE.LineSegments(new THREE.EdgesGeometry(dim.geometry), new THREE.LineBasicMaterial({ color: C.amber, transparent: true, opacity: 0.6 }));
      edge.position.y = 0.28;
      g.add(dim, edge);
      const r = rng(hashStr(id));
      const streaks = [];
      const geo = new THREE.BoxGeometry(0.34, 0.006, 0.006);
      const mat = new THREE.MeshBasicMaterial({ color: C.amber, transparent: true, opacity: 0.75 });
      for (let i = 0; i < 16; i++) {
        const k = new THREE.Mesh(geo, mat);
        k.position.set(0, 0.2 + r() * 0.5, (r() - 0.5) * d * 0.92);
        k.userData = { p: r(), v: 0.16 + r() * 0.12, w };
        g.add(k);
        streaks.push(k);
      }
      g.visible = false;
      this.scene.add(g);
      this.sectors.set(id, { g, streaks, s });
    }
  }

  zoneMesh(z) {
    const [x0, x1] = z.rect.x, [y0, y1] = z.rect.y;
    const w = x1 - x0, d = y1 - y0;
    const red = z.kind !== "no_record";
    const hex = red ? "#C8452C" : "#D99A26";
    const h = z.kind === "red" ? 0.62 : z.kind === "vip" ? 0.5 : 0.32;
    const tex = hatchTexture(hex, red ? 0.75 : 0.28);
    tex.repeat.set(Math.max(w, d) * 4, h * 4);
    const mat = new THREE.MeshBasicMaterial({ color: 0xffffff, map: tex, transparent: true, opacity: 0.85, depthWrite: false, side: THREE.DoubleSide });
    const geo = new THREE.BoxGeometry(w, h, d);
    const g = new THREE.Group();
    g.position.set(this.px(x0 + w / 2), h / 2, this.pz(y0 + d / 2));
    const veil = new THREE.Mesh(geo, new THREE.MeshBasicMaterial({ color: red ? C.red : C.amber, transparent: true, opacity: 0.14, depthWrite: false }));
    g.add(veil, new THREE.Mesh(geo, mat), new THREE.LineSegments(new THREE.EdgesGeometry(geo), new THREE.LineBasicMaterial({ color: red ? C.red : C.amber })));
    this.scene.add(g);
    return { g, h };
  }

  // --- labels: DOM so text stays crisp and never below 12 px
  tag(key, cls) {
    let t = this.tags.get(key);
    if (!t) {
      const el = document.createElement("div");
      this.labelHost.append(el);
      t = { el, v: new THREE.Vector3(), text: null, cls: null, on: false };
      this.tags.set(key, t);
    }
    if (t.cls !== cls) { t.el.className = `tag3 ${cls}`; t.cls = cls; }
    return t;
  }

  label(key, cls, text, x, y, z) {
    const t = this.tag(key, cls);
    if (t.text !== text) { t.el.textContent = text; t.text = text; }
    t.v.set(x, y, z);
    t.on = true;
    t.el.hidden = false;
  }

  unlabel(key) { const t = this.tags.get(key); if (t?.on) { t.on = false; t.el.hidden = true; } }

  placeLabels() {
    const w = this.host.clientWidth, h = this.host.clientHeight;
    const v = new THREE.Vector3();
    for (const t of this.tags.values()) {
      if (!t.on) continue;
      v.copy(t.v).project(this.camera);
      if (v.z > 1) { t.el.style.visibility = "hidden"; continue; }
      t.el.style.visibility = "";
      t.el.style.transform = `translate(${((v.x + 1) / 2 * w).toFixed(1)}px,${((1 - v.y) / 2 * h).toFixed(1)}px) translate(-50%,-100%)`;
    }
  }

  // --- state
  reset() {
    for (const r of this.drones?.values() || []) { this.scene.remove(r.g, r.blob); r.trail.mesh.removeFromParent(); }
    for (const r of this.units?.values() || []) this.scene.remove(r.g);
    for (const r of this.incidents?.values() || []) this.scene.remove(r.g);
    for (const key of this.tags.keys()) this.unlabel(key);
    this.drones = new Map(); this.units = new Map(); this.incidents = new Map();
    this.state = null; this.simT = null; this.showT = null;
    this.handoff = null; this.tickMs = 200; this.lastTick = 0;
    this.suspectMover = null;
    this.suspectTrail.clear();
    this.suspect.visible = false;
    this.chasing = false; this.returning = false;
    this.frozen = false;
  }

  mover(pos) { return { from: pos, to: pos, cur: pos, t0: 0, dur: 1 }; }

  moveTo(m, pos, now, quiet) {
    if (quiet || Math.hypot(m.cur[0] - pos[0], m.cur[1] - pos[1]) > JUMP_KM) {
      Object.assign(m, { from: pos, to: pos, cur: pos });
    } else if (m.to[0] !== pos[0] || m.to[1] !== pos[1]) {
      Object.assign(m, { from: m.cur, to: pos, t0: now, dur: Math.min(this.tickMs * 1.15, 1100) });
    }
  }

  step(m, now) {
    const k = Math.min(1, (now - m.t0) / m.dur);
    if (k >= 1) m.cur = m.to;
    else m.cur = [m.from[0] + (m.to[0] - m.from[0]) * k, m.from[1] + (m.to[1] - m.from[1]) * k];
  }

  // The sim finishes the shift the moment the suspect is caught; hold the scene on the catch.
  thaw(s) { this.frozen = false; if (s) this.update(s, true); }

  update(s, quiet = false) {
    if (this.frozen) return;
    const now = performance.now();
    if (this.lastTick) {
      const gap = now - this.lastTick;
      if (gap < 1500) this.tickMs = this.tickMs * 0.7 + gap * 0.3;
    }
    this.lastTick = now;
    this.state = s;
    this.simT = s.t;
    if (quiet || this.showT == null || Math.abs(s.t - this.showT) > 3) this.showT = s.t;
    for (const d of Object.values(s.drones)) this.syncDrone(d, s, now, quiet);
    if (!this.cityMode) for (const u of s.units || []) this.syncUnit(u, now, quiet);
    this.syncSuspect(s, now, quiet);
    this.syncDocks(s);
    this.syncZones(s);
    this.syncCoverage(s);
    this.syncWeather(s);
    if (!this.cityMode) this.syncIncidents(s);
    if (quiet) this.settleCamera();
  }

  slotPos(d, s) {
    const k = this.docks.get(d.slot);
    if (!k) return { pos: d.pos, y: 0.14 };
    const i = Math.max(0, s.docks[d.slot].slot_state.findIndex((p) => p.drone === d.id));
    const dx = k.hub ? (i - (k.slots - 1) / 2) * 0.15 : 0;
    return { pos: [k.at[0] + dx, k.at[1]], y: k.top + 0.004, unit: k.units[i] };
  }

  syncDrone(d, s, now, quiet) {
    let r = this.drones.get(d.id);
    const air = d.state === "airborne";
    const pad = air ? null : this.slotPos(d, s);
    const pos = air ? d.pos : pad.pos;
    if (!r) {
      const q = makeQuad();
      const blob = new THREE.Mesh(new THREE.CircleGeometry(0.1, 20), new THREE.MeshBasicMaterial({ color: 0x000000, transparent: true, opacity: 0.4, depthWrite: false }));
      blob.rotation.x = -Math.PI / 2;
      this.scene.add(q.g, blob);
      const seed = hashStr(d.id);
      r = { ...q, blob, m: this.mover(pos), alt: air ? CRUISE : pad.y, padY: pad?.y ?? 0.14, amt: 0, yaw: 0, tilt: 0, lastX: null, lastZ: null, phase: (seed % 100) / 100 * Math.PI * 2, lift: (seed % 7) * 0.012, trail: new Trail(this.scene, C.sky, 90, 0.011) };
      this.drones.set(d.id, r);
    }
    r.d = d;
    if (pad) { r.padY = pad.y; r.unit = pad.unit; }
    const booked = air && d.booked_slot ? this.docks.get(d.booked_slot) : null;
    if (booked) { // the lid that opens for the landing
      const i = s.docks[d.booked_slot].slot_state.findIndex((p) => p.status === "RESERVED" && (p.drone === d.id || p.drone == null));
      r.unit = booked.units[Math.max(0, i)];
      r.padY = booked.top + 0.004;
    }
    this.moveTo(r.m, pos, now, quiet);
    const patrol = air && d.mission === "patrol" && d.on_station && d.loop;
    r.patrol = patrol ? d.loop : null;
    r.circle = this.cityMode && air && d.mission === "incident" && d.on_station;
    if (quiet) {
      r.amt = patrol || r.circle ? 1 : 0;
      r.alt = this.altFor(r, d);
      r.trail.clear();
    }
    const col = d.fault || d.out_of_service ? C.red : d.manual ? C.sky : d.mission === "hold" || d.held ? C.amber : d.mission === "returning" ? C.gold : air ? C.sky : C.green;
    r.ring.material.color.setHex(col);
    r.ring.visible = air;
  }

  altFor(r, d) {
    if (d.state !== "airborne") return r.padY;
    const cruise = CRUISE + r.lift;
    const dock = d.booked_slot ? this.docks.get(d.booked_slot) : null;
    if (!dock) return cruise;
    const km = Math.hypot(d.pos[0] - dock.at[0], d.pos[1] - dock.at[1]);
    return dock.top + 0.004 + (cruise - dock.top) * Math.min(1, km / 0.45); // descend into the box
  }

  syncUnit(u, now, quiet) {
    let r = this.units.get(u.id);
    if (!r) {
      const g = makeVehicle(u.kind === "ambulance" ? 0xf2f5f7 : u.kind === "patrol_car" ? 0x9db4c8 : 0xd8c9a0);
      this.scene.add(g);
      r = { g, m: this.mover(u.pos), yaw: 0 };
      this.units.set(u.id, r);
    }
    r.u = u;
    this.moveTo(r.m, u.pos, now, quiet);
  }

  // Served only while the API sends it; a gap in the data stays a gap in the ribbon.
  syncSuspect(s, now, quiet) {
    const p = s.suspect;
    if (p) {
      if (!this.suspectMover) this.suspectMover = this.mover(p);
      this.moveTo(this.suspectMover, p, now, quiet);
      this.suspectTrail.add(this.px(p[0]), 0.03, this.pz(p[1]), s.t);
    } else {
      this.suspectMover = null;
      this.suspectTrail.gap();
    }
    this.suspect.visible = !!p;
    if (!p) this.unlabel("suspect");
    if (!p && this.suspectTrail.pts.length && !Object.values(s.drones).some((d) => CHASE.includes(d.mission))) this.suspectTrail.clear();
    this.suspectTrail.draw(s.t);
  }

  syncDocks(s) {
    for (const k of Object.values(s.docks)) {
      const r = this.docks.get(k.id);
      if (!r) continue;
      const up = k.online && !k.restricted;
      k.slot_state.forEach((p, i) => {
        const u = r.units[i];
        const drone = p.drone && s.drones[p.drone];
        const ready = drone && (this.fx?.labelOf(drone.id) ? this.fx.labelOf(drone.id) === "Ready" : drone.battery >= 100);
        const hex = !up ? C.red : p.status === "RESERVED" ? C.sky : !drone ? 0x44525f : ready ? C.green : C.amber;
        u.dot.material.color.setHex(hex);
        u.pulse = up && !!drone && !ready; // charging also pulses, so colour isn't the only signal
        if (!u.pulse) u.dot.scale.setScalar(drone || !up ? 1.2 : 0.7);
      });
      if (up) this.unlabel(`dock:${k.id}`);
      else this.label(`dock:${k.id}`, "bad", `✖ ${k.id} OFFLINE`, this.px(k.at[0]), r.top + 0.1, this.pz(k.at[1]));
    }
  }

  syncZones(s) {
    for (const z of s.zones || []) {
      let r = this.zones.get(z.id);
      if (!r && z.active) { r = this.zoneMesh(z); this.zones.set(z.id, r); }
      if (r) r.g.visible = z.active;
      const key = `zone:${z.id}`;
      if (!z.active) { this.unlabel(key); continue; }
      const text = z.kind === "red" ? `⛔ NO ENTRY · ${z.id}` : z.kind === "no_record" ? (this.cityMode ? "NO-RECORD zone" : "NO-RECORD · hospital") : `⛔ ${z.id}${z.until ? ` until ${z.until}` : ""}`;
      const cx = (z.rect.x[0] + z.rect.x[1]) / 2, cy = (z.rect.y[0] + z.rect.y[1]) / 2;
      this.label(key, z.kind === "no_record" ? "amber" : "bad", text, this.px(cx), r.h + 0.06, this.pz(cy));
    }
  }

  // Same condition the sim counts as coverage, read from the drone fields in /state.
  syncCoverage(s) {
    if (this.cityMode) { // roaming drones: no patrol loops or coverage rings in this view
      for (const l of Object.values(this.loops)) { l.ring.visible = false; l.ribbon.material.opacity = 0; }
      return;
    }
    const covered = new Map();
    for (const d of Object.values(s.drones)) {
      if (d.state === "airborne" && d.mission === "patrol" && d.on_station && d.loop) covered.set(d.loop, d);
    }
    for (const [id, l] of Object.entries(this.loops)) {
      const d = covered.get(id);
      l.ring.visible = !!d;
      l.ribbon.material.opacity = d ? 0.32 : 0.06;
      if (d) { l.ring.position.x = this.px(d.pos[0]); l.ring.position.z = this.pz(d.pos[1]); }
      const key = `cover:${id}`;
      if (d) this.unlabel(key);
      else this.label(key, "muted", `${id}: NO COVER`, l.ribbon.position.x - 1.35, 0.07, l.ribbon.position.z);
    }
  }

  syncWeather(s) {
    for (const [id, r] of this.sectors) {
      const w = s.weather?.[id];
      r.g.visible = !!w?.grounded;
      const key = `wx:${id}`;
      if (!w?.grounded) { this.unlabel(key); continue; }
      const m = w.until_min, until = m != null ? ` until ${String(Math.floor(m / 60)).padStart(2, "0")}:${String(Math.floor(m % 60)).padStart(2, "0")}` : "";
      this.label(key, "amber big", `⚠ GROUNDED · wind ${w.wind_ms} m/s${until}`, r.g.position.x, 0.62, r.g.position.z);
    }
  }

  syncIncidents(s) {
    for (const i of Object.values(s.incidents || {})) {
      let r = this.incidents.get(i.id);
      if (!r) {
        const g = new THREE.Group();
        g.position.set(this.px(i.pos[0]), 0, this.pz(i.pos[1]));
        const mk = (rad, op) => new THREE.Mesh(new THREE.CylinderGeometry(rad, rad, 2.4, 20, 1, true), new THREE.MeshBasicMaterial({ color: C.red, transparent: true, opacity: op, blending: THREE.AdditiveBlending, depthWrite: false, side: THREE.DoubleSide }));
        const outer = mk(0.11, 0.3), inner = mk(0.035, 0.85);
        outer.position.y = inner.position.y = 1.2;
        const wave = new THREE.Mesh(new THREE.RingGeometry(0.1, 0.13, 48), new THREE.MeshBasicMaterial({ color: C.red, transparent: true, depthWrite: false, side: THREE.DoubleSide }));
        wave.rotation.x = -Math.PI / 2; wave.position.y = 0.02;
        const f = i.geofence_km;
        const fence = new THREE.Mesh(new THREE.RingGeometry(f - 0.015, f, 64), new THREE.MeshBasicMaterial({ color: C.red, transparent: true, opacity: 0.45, depthWrite: false }));
        fence.rotation.x = -Math.PI / 2; fence.position.y = 0.016;
        g.add(outer, inner, wave, fence);
        this.scene.add(g);
        r = { g, outer, wave };
        this.incidents.set(i.id, r);
      }
      const open = i.status === "open";
      r.open = open;
      // an open incident with nothing assigned stays on the rail, not as a beacon over the city
      r.g.visible = open && (i.drones.length > 0 || i.units.length > 0);
      const key = `inc:${i.id}`;
      if (r.g.visible) this.label(key, "inc", `⚠ ${i.id} ${i.kind}`, r.g.position.x, 1.5, r.g.position.z);
      else this.unlabel(key);
    }
  }

  // Timeline events the scene acts on. The handoff expires by sim time, so it shows when paused.
  event(e) {
    if (e.type === "game_catch") this.frozen = true;
    if (e.type === "handoff") this.handoff = { from: e.payload.from, to: e.payload.to, gap: e.payload.gap_s, t: e.t };
  }

  placeBeam(b, a, c) {
    const dx = c.x - a.x, dy = c.y - a.y, dz = c.z - a.z;
    const len = Math.hypot(dx, dy, dz);
    b.visible = len > 0.01;
    b.position.set((a.x + c.x) / 2, (a.y + c.y) / 2, (a.z + c.z) / 2);
    b.scale.set(1, len, 1);
    b.quaternion.setFromUnitVectors(new THREE.Vector3(0, 1, 0), new THREE.Vector3(dx, dy, dz).normalize());
  }

  // --- frame
  frame() {
    const dt = Math.min(0.1, this.clock.getDelta());
    const now = performance.now();
    const wall = now / 1000;
    if (this.state) {
      this.showT += (this.simT - this.showT) * smooth(6, dt);
      this.animDrones(dt, now, wall);
      this.animUnits(dt, now, wall);
      this.animChase(wall);
      this.animFx(wall, dt);
      this.fx?.frame(dt, now, wall);
      if (this.chaseId || this.shot) this.directCamera(dt);
      else this.followChase(dt);
    }
    this.controls.update();
    if (!this.shadowDone) { this.renderer.shadowMap.needsUpdate = true; this.shadowDone = true; }
    this.renderer.render(this.scene, this.camera);
    if (this.state) this.placeLabels();
    this.frames += 1;
    if (now - this.fpsT > 1000) { this.fps = Math.round(this.frames * 1000 / (now - this.fpsT)); this.frames = 0; this.fpsT = now; this.adapt(); }
  }

  // Weak GPU: trade resolution for smooth motion, one step per second, down to 70%.
  adapt() {
    const pr = this.renderer.getPixelRatio();
    if (this.fps >= 32 || pr <= 0.71 || document.hidden) return;
    this.renderer.setPixelRatio(Math.max(0.7, pr - 0.1));
    this.resize();
  }

  animDrones(dt, now, wall) {
    for (const [id, r] of this.drones) {
      const d = r.d;
      this.step(r.m, now);
      // DISPLAY-ONLY patrol flight. The sim holds a patrol drone at its loop centre; here it is
      // drawn flying a racetrack along that loop's junction line, driven by sim time so it stops
      // on pause. Battery, coverage and every number stay the sim's; nothing feeds back.
      r.amt += ((r.patrol || r.circle ? 1 : 0) - r.amt) * smooth(0.9, dt);
      let x = r.m.cur[0], y = r.m.cur[1];
      const loop = this.loops[r.patrol || d.loop];
      if (loop && r.amt > 0.001) {
        const th = (this.showT / LOOP_PERIOD_MIN) * Math.PI * 2 + r.phase;
        const along = Math.cos(th) * loop.a, across = Math.sin(th) * 0.2;
        x += r.amt * (loop.ux * along - loop.uy * across);
        y += r.amt * (loop.uy * along + loop.ux * across);
      }
      else if (!loop && r.amt > 0.001) { // city mode, also display-only: circling the scene
        const th = (this.showT / 3) * Math.PI * 2 + r.phase;
        x += r.amt * 0.24 * Math.cos(th);
        y += r.amt * 0.24 * Math.sin(th);
      }
      const px = this.px(x), pz = this.pz(y);
      const air = d.state === "airborne";
      const lid = r.unit;
      if (!(air && lid && lid.open < 0.8 && r.alt < lid.top + 0.06)) { // the lid opens first
        r.alt += (this.altFor(r, d) - r.alt) * smooth(1.7, dt);
      }
      const flying = air || r.alt > r.padY + 0.012;
      if (lid && (air ? Math.hypot(px - lid.x, pz - lid.z) < 0.55 && r.alt < lid.top + 0.3 : flying)) lid.want = true;
      const shown = flying && (!lid || lid.open > 0.5 || r.alt > lid.top + 0.12);
      r.g.visible = shown;
      const bob = flying ? Math.sin(wall * 1.9 + r.phase) * 0.012 * Math.min(1, (r.alt - r.padY) / 0.1) : 0;
      r.g.position.set(px, r.alt + bob, pz);
      if (r.lastX != null) {
        const vx = (px - r.lastX) / Math.max(dt, 0.001), vz = (pz - r.lastZ) / Math.max(dt, 0.001);
        const v = Math.hypot(vx, vz);
        if (v > 0.015) {
          let diff = Math.atan2(vx, vz) - r.yaw;
          diff = Math.atan2(Math.sin(diff), Math.cos(diff));
          r.yaw += diff * smooth(4, dt);
        }
        r.tilt += (Math.min(0.32, v * 0.9) - r.tilt) * smooth(4, dt);
      }
      r.lastX = px; r.lastZ = pz;
      r.g.rotation.set(r.tilt, r.yaw, 0, "YXZ");
      const rate = flying ? 42 : 0;
      for (const hub of r.rotors) { hub.rotation.y += rate * dt; hub.userData.disc.visible = flying; }
      r.blob.position.set(px, 0.018, pz);
      r.blob.visible = shown;
      r.blob.material.opacity = 0.4 - Math.min(0.2, r.alt * 0.3);
      if (shown) r.trail.add(px, r.alt, pz, this.showT);
      r.trail.draw(this.showT);
      if (d.state === "airborne") {
        const word = this.fx?.labelOf(id) ?? (d.manual ? "YOU" : WORD[d.mission] ?? d.mission ?? "");
        const rec = d.recording ? " · ● REC" : "";
        const cls = d.manual ? "" : d.mission === "hold" ? "amber" : d.fault ? "bad" : "";
        const quiet = this.cityMode && d.mission === "roam"; // calm drones: just id and battery
        if (shown) this.label(`d:${id}`, `drone ${cls}`, `${id}  ${Math.round(d.battery)}%${quiet ? "" : `  ${word}${rec}`}`, px, r.alt + 0.2, pz);
        else this.unlabel(`d:${id}`);
      } else this.unlabel(`d:${id}`);
    }
    for (const k of this.docks.values()) {
      for (const u of k.units) {
        u.open += ((u.want ? 1 : 0) - u.open) * smooth(5, dt);
        u.want = false;
        const a = u.open * 1.9; // hinges at the outer edges
        u.left.rotation.z = a;
        u.right.rotation.z = -a;
        if (u.pulse) u.dot.scale.setScalar(1 + 0.3 * Math.sin(wall * 3));
      }
    }
  }

  animUnits(dt, now, wall) {
    for (const [id, r] of this.units) {
      this.step(r.m, now);
      const x = this.px(r.m.cur[0]), z = this.pz(r.m.cur[1]);
      if (r.lx != null && Math.hypot(x - r.lx, z - r.lz) > 1e-4) r.yaw = Math.atan2(-(z - r.lz), x - r.lx);
      r.lx = x; r.lz = z;
      r.g.position.set(x, 0.008, z);
      r.g.rotation.y = r.yaw;
      const busy = r.u.status === "en_route" || r.u.status === "on_scene";
      r.g.userData.lamp.material.color.setHex(busy ? (Math.floor(wall * 4) % 2 ? C.sky : C.red) : 0x44525f);
      if (r.u.status === "en_route") {
        this.label(`u:${id}`, "muted", `${id.replace(/_/g, " ")}${r.u.eta_min != null ? ` · ${Math.ceil(r.u.eta_min)} min` : ""}`, x, 0.1, z);
      } else this.unlabel(`u:${id}`);
    }
  }

  animChase(wall) {
    const s = this.state;
    if (this.suspectMover) {
      this.step(this.suspectMover, performance.now());
      const [x, y] = this.suspectMover.cur;
      const px = this.px(x), pz = this.pz(y);
      if (this.susX != null && Math.hypot(px - this.susX, pz - this.susZ) > 1e-4) this.suspect.rotation.y = Math.atan2(-(pz - this.susZ), px - this.susX);
      this.susX = px; this.susZ = pz;
      this.suspect.position.set(px, 0.01, pz);
      this.label("suspect", "sus", "SUSPECT VEHICLE", px, 0.12, pz);
    }
    // track lines: tracker to target, only while the API serves the target
    const trackers = s.suspect ? Object.values(s.drones).filter((d) => d.state === "airborne" && d.mission === "track") : [];
    [this.beam, this.beam2].forEach((b, i) => {
      const r = trackers[i] && this.drones.get(trackers[i].id);
      b.visible = !!r;
      if (r) this.placeBeam(b, r.g.position, this.suspect.position);
    });
    // handoff: a visible pulse from the outgoing drone to the relief, then the label clears
    const h = this.handoff;
    const a = h && this.drones.get(h.from), b = h && this.drones.get(h.to);
    if (a && b && this.showT < h.t + 1.2) {
      this.placeBeam(this.link, a.g.position, b.g.position);
      this.pulse.visible = true;
      this.pulse.position.lerpVectors(a.g.position, b.g.position, (wall * 0.9) % 1);
      this.label("handoff", "gold big", `HANDOFF ${h.from} → ${h.to}${h.gap === 0 ? " · gap 0 s" : ""}`, (a.g.position.x + b.g.position.x) / 2, a.g.position.y + 0.42, (a.g.position.z + b.g.position.z) / 2);
    } else {
      this.pulse.visible = this.link.visible = false;
      this.unlabel("handoff");
    }
  }

  animFx(wall, dt) {
    for (const r of this.incidents.values()) {
      if (!r.g.visible) continue;
      const p = (wall * 0.55) % 1;
      r.wave.scale.setScalar(1 + p * 5);
      r.wave.material.opacity = 0.75 * (1 - p);
      r.outer.material.opacity = 0.2 + 0.16 * Math.sin(wall * 3);
    }
    for (const r of this.sectors.values()) {
      if (!r.g.visible) continue;
      for (const k of r.streaks) {
        const u = k.userData;
        u.p = (u.p + u.v * dt) % 1;
        k.position.x = (u.p - 0.5) * u.w;
      }
    }
  }

  chaseFocus() {
    const s = this.state;
    const pts = Object.values(s.drones).filter((d) => d.state === "airborne" && CHASE.includes(d.mission)).map((d) => d.pos);
    if (!pts.length) return null;
    if (s.suspect) pts.push(s.suspect);
    const n = pts.length;
    const x = pts.reduce((a, p) => a + p[0], 0) / n, y = pts.reduce((a, p) => a + p[1], 0) / n;
    return new THREE.Vector3(this.px(x), 0, this.pz(y));
  }

  moveCamera(target, dist, k) {
    const c = this.controls, offset = this.camera.position.clone().sub(c.target);
    c.target.add(target.clone().sub(c.target).multiplyScalar(k));
    offset.setLength(offset.length() + (dist - offset.length()) * k);
    this.camera.position.copy(c.target).add(offset);
  }

  // gentle follow while a track is live, then back to the district view once
  followChase(dt) {
    const focus = this.chaseFocus();
    if (focus) {
      this.chasing = true; this.returning = false;
      this.moveCamera(focus.multiplyScalar(0.7), CHASE_DIST, smooth(0.5, dt));
    } else if (this.chasing) {
      this.chasing = false; this.returning = true;
    }
    if (this.returning) {
      this.moveCamera(this.home, HOME_DIST, smooth(0.6, dt));
      if (this.controls.target.distanceTo(this.home) < 0.02) this.returning = false;
    }
  }

  settleCamera() {
    const focus = this.chaseFocus();
    if (focus) { this.chasing = true; this.moveCamera(focus.multiplyScalar(0.7), CHASE_DIST, 1); }
    else if (this.chasing) { this.chasing = false; this.moveCamera(this.home, HOME_DIST, 1); }
    this.returning = false;
  }
}
