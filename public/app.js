/* Weaver UI. Everything drawn here is driven by backend events (live over SSE, or a
   recorded events.jsonl replayed with time compression). No data is invented client side. */
(() => {
'use strict';

const Q = new URLSearchParams(location.search);
const REPLAY = Q.get('replay');
const SPEED = parseFloat(Q.get('speed') || '1');
const LEAD = parseFloat(Q.get('lead') || (REPLAY ? '5' : '0'));      // seconds of title before replay starts
const OUTRO = Q.get('outro') ? parseFloat(Q.get('outro')) : null;   // screen time of the closing line
const CAPTURE = Q.has('capture');                                    // frames are stepped from outside

const $ = (id) => document.getElementById(id);
const canvas = $('web');
const ctx = canvas.getContext('2d');
const TAU = Math.PI * 2;

/* ------------------------------------------------------------ clock */
let now = 0;                       // seconds on the UI clock
const ease = (x) => (x <= 0 ? 0 : x >= 1 ? 1 : x * x * (3 - 2 * x));
const easeOut = (x) => (x <= 0 ? 0 : x >= 1 ? 1 : 1 - Math.pow(1 - x, 3));
const clamp = (x, a, b) => Math.max(a, Math.min(b, x));

function rng(seed) {
  let a = seed >>> 0;
  return () => {
    a |= 0; a = (a + 0x6d2b79f5) | 0;
    let t = Math.imul(a ^ (a >>> 15), 1 | a);
    t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t;
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}

/* ------------------------------------------------------------ DOM fades on the UI clock */
const fades = [];
function fade(el, from, to, dur, delay = 0, rise = 0) {
  for (let i = fades.length - 1; i >= 0; i--) if (fades[i].el === el) fades.splice(i, 1);
  fades.push({ el, from, to, dur, t0: now + delay, rise });
  el.style.opacity = from;
}
function stepFades() {
  for (let i = fades.length - 1; i >= 0; i--) {
    const f = fades[i];
    const p = ease((now - f.t0) / f.dur);
    f.el.style.opacity = (f.from + (f.to - f.from) * p).toFixed(3);
    if (f.rise) f.el.style.transform = p < 1 ? `translateY(${((1 - p) * f.rise).toFixed(2)}px)` : '';
    if (p >= 1) fades.splice(i, 1);
  }
}

/* ------------------------------------------------------------ web geometry */
let W = 0, H = 0, U = 1, DPR = 1;
let web = null;                    // geometry built from the repo tree
const strands = new Map();         // path -> strand (one file each)
const ripples = [];
let born = 0.25;                   // when the web starts to be spun on screen

function resize() {
  DPR = CAPTURE ? 1 : Math.min(2, window.devicePixelRatio || 1);
  W = window.innerWidth; H = window.innerHeight; U = H / 1080;
  canvas.width = W * DPR; canvas.height = H * DPR;
  if (web) buildWeb(web.tree);
}

function buildWeb(tree) {
  const rand = rng(20261009);
  const dirs = tree.slice().sort((a, b) => (a.dir === '.' ? -1 : b.dir === '.' ? 1 : a.dir.localeCompare(b.dir)));
  const N = Math.max(dirs.length, 7);
  const maxFiles = Math.max(...dirs.map((d) => d.files.length), 1);
  const K = Math.max(9, maxFiles + 3);
  const cx = W * 0.335, cy = H * 0.512;
  const R = Math.min(H * 0.378, W * 0.22);
  const step = TAU / N;
  const radials = [];
  for (let i = 0; i < N; i++) {
    const a = -Math.PI / 2 - step * 0.35 + i * step + (rand() - 0.5) * step * 0.22;
    const s = 0.93 + rand() * 0.14;
    radials.push({ a, s, ux: Math.cos(a) * 1.07, uy: Math.sin(a), dir: dirs[i] ? dirs[i].dir : null });
  }
  const nodes = [];                // nodes[i][k] = base position
  for (let i = 0; i < N; i++) {
    nodes.push([]);
    for (let k = 0; k < K; k++) {
      const r = R * (0.2 + 0.8 * Math.pow(k / (K - 1), 0.92)) * radials[i].s * (1 + (rand() - 0.5) * 0.045);
      nodes[i].push({ x: cx + radials[i].ux * r, y: cy + radials[i].uy * r });
    }
    const ro = R * 1.11 * radials[i].s;
    radials[i].end = { x: cx + radials[i].ux * ro, y: cy + radials[i].uy * ro };
  }
  const segs = [];                 // ring segment between radial i and i+1 on ring k
  for (let k = 0; k < K; k++) for (let i = 0; i < N; i++) {
    segs.push({ i, k, j: (i + 1) % N, sag: 0.05 + rand() * 0.07, order: k * N + i, file: null, phase: rand() * TAU });
  }
  const seg = (i, k) => segs[k * N + i];
  const old = new Map(strands); strands.clear();
  dirs.forEach((d, i) => {
    const f = d.files.length;
    d.files.forEach((name, j) => {
      const k = Math.round(K - 1 - (j * (K - 3)) / Math.max(f, 1));
      const path = d.dir === '.' ? name : d.dir + '/' + name;
      const s = seg(i, clamp(k, 2, K - 1));
      s.file = path; s.name = name;
      const prev = old.get(path) || {};
      strands.set(path, Object.assign({ torn: false, tornAt: 0, fresh: -99, label: false, held: false, spin: null }, prev, { seg: s }));
    });
  });
  // hub: a small irregular spiral
  const hub = [];
  for (let q = 0; q <= 90; q++) {
    const t = q / 90, a = t * TAU * 3.4, r = R * (0.012 + 0.105 * t) * (1 + (rand() - 0.5) * 0.08);
    hub.push({ x: cx + Math.cos(a) * r * 1.07, y: cy + Math.sin(a) * r });
  }
  // anchors: a few radials run on to the edge of the screen
  const anchors = [];
  for (let i = 0; i < N; i++) {
    // threads that run on to the edge of the screen, kept clear of the text
    const ux = Math.cos(radials[i].a), uy = Math.sin(radials[i].a);
    const keep = (Math.abs(ux) < 0.62 && !(ux < -0.3 && uy < 0)) || (ux < -0.8 && uy < 0.3);
    if (!keep) continue;
    const e = radials[i].end, L = Math.max(W, H);
    anchors.push({ from: e, to: { x: e.x + Math.cos(radials[i].a + (rand() - 0.5) * 0.16) * L, y: e.y + Math.sin(radials[i].a + (rand() - 0.5) * 0.16) * L } });
  }
  web = { tree, dirs, N, K, cx, cy, R, radials, nodes, segs, seg, hub, anchors };
  if (spider.node == null) { spider.node = 'c'; spider.x = cx; spider.y = cy; plantFeet(); }
  else { const p = nodePos(spider.node); spider.x = p.x; spider.y = p.y; plantFeet(); }
}

/* displacement field: slow ambient sway plus damped ripples from every pluck */
function disp(x, y, out) {
  let dx = Math.sin(now * 0.55 + x * 0.008 + y * 0.004) * 1.1 * U;
  let dy = Math.cos(now * 0.47 + y * 0.009 - x * 0.003) * 1.1 * U;
  let glow = 0;
  for (const r of ripples) {
    const ex = x - r.x, ey = y - r.y, d = Math.hypot(ex, ey) || 1;
    const tt = now - r.t0 - d / r.v;
    if (tt <= 0) continue;
    const env = Math.exp(-tt * r.decay) * Math.exp(-d / r.reach);
    const a = r.amp * env * Math.sin(tt * r.w);
    dx += (-ey / d) * a * 0.75 + (ex / d) * a * 0.5;
    dy += (ex / d) * a * 0.75 + (ey / d) * a * 0.5;
    glow += env * r.glow * Math.exp(-tt * 2.2);
  }
  out.x = x + dx; out.y = y + dy; out.g = glow;
  return out;
}
const P = (pt) => disp(pt.x, pt.y, { x: 0, y: 0, g: 0 });

function nodePos(id) {
  if (id === 'c') return { x: web.cx, y: web.cy };
  const [i, k] = id.split(':').map(Number);
  return web.nodes[i][k];
}

function ripple(x, y, amp, glow = 1) {
  ripples.push({ x, y, t0: now, v: 620 * U, decay: 1.9, reach: 520 * U, amp: amp * U, w: TAU * 4.2, glow });
}

/* ------------------------------------------------------------ path finding on the web */
function route(from, to) {
  const { N, K } = web;
  const ids = ['c'];
  for (let i = 0; i < N; i++) for (let k = 0; k < K; k++) ids.push(i + ':' + k);
  const dist = new Map(ids.map((id) => [id, Infinity])), prev = new Map(), done = new Set();
  dist.set(from, 0);
  const nbrs = (id) => {
    if (id === 'c') return Array.from({ length: N }, (_, i) => i + ':0');
    const [i, k] = id.split(':').map(Number), out = [];
    if (k === 0) out.push('c'); else out.push(i + ':' + (k - 1));
    if (k < K - 1) out.push(i + ':' + (k + 1));
    const a = web.seg(i, k), b = web.seg((i + N - 1) % N, k);
    if (!(a.file && strands.get(a.file).torn)) out.push(((i + 1) % N) + ':' + k);
    if (!(b.file && strands.get(b.file).torn)) out.push(((i + N - 1) % N) + ':' + k);
    return out;
  };
  for (;;) {
    let best = null, bd = Infinity;
    for (const id of ids) if (!done.has(id) && dist.get(id) < bd) { bd = dist.get(id); best = id; }
    if (best == null || best === to) break;
    done.add(best);
    const p = nodePos(best);
    for (const n of nbrs(best)) {
      const q = nodePos(n), nd = bd + Math.hypot(p.x - q.x, p.y - q.y);
      if (nd < dist.get(n)) { dist.set(n, nd); prev.set(n, best); }
    }
  }
  const path = [to];
  while (path[0] !== from && prev.has(path[0])) path.unshift(prev.get(path[0]));
  return { ids: path, len: dist.get(to) };
}

/* ------------------------------------------------------------ the spider */
const spider = { node: null, x: 0, y: 0, heading: -Math.PI / 2, legs: [], queue: [], act: null, idleSince: 0, speed: 0 };
const LEG_DEF = [[0.55, 36], [1.15, 31], [1.95, 30], [2.6, 37]];   // angle from heading, reach

function plantFeet() {
  spider.legs = [];
  for (let side = -1; side <= 1; side += 2) LEG_DEF.forEach(([a, reach], n) => {
    const leg = { side, n, a: a * side, reach: reach * U, x: 0, y: 0, step: null, group: (n + (side > 0 ? 1 : 0)) % 2 };
    const r = restPos(leg, leg.group ? 5 * U : 0);
    leg.x = r.x; leg.y = r.y;
    spider.legs.push(leg);
  });
}
function restPos(leg, lead) {
  const a = spider.heading + leg.a;
  return {
    x: spider.x + Math.cos(a) * leg.reach * 0.86 + Math.cos(spider.heading) * lead,
    y: spider.y + Math.sin(a) * leg.reach * 0.86 + Math.sin(spider.heading) * lead,
  };
}
function stepLegs(dt) {
  const moving = spider.speed > 4 * U;
  const groupBusy = [false, false];
  for (const leg of spider.legs) if (leg.step) groupBusy[leg.group] = true;
  for (const leg of spider.legs) {
    if (leg.step) {
      leg.step.p += dt / leg.step.dur;
      const p = ease(leg.step.p);
      leg.x = leg.step.fx + (leg.step.tx - leg.step.fx) * p;
      leg.y = leg.step.fy + (leg.step.ty - leg.step.fy) * p;
      if (leg.step.p >= 1) leg.step = null;
      continue;
    }
    const target = restPos(leg, moving ? 10 * U : 0);
    const d = Math.hypot(target.x - leg.x, target.y - leg.y);
    const far = d > leg.reach * 0.62;
    if ((d > (moving ? 12 : 4) * U && !groupBusy[1 - leg.group]) || far) {
      leg.step = { fx: leg.x, fy: leg.y, tx: target.x, ty: target.y, p: 0, dur: far ? 0.05 : 0.085 };
      groupBusy[leg.group] = true;
    }
  }
}

function enqueue(a) { spider.queue.push(a); }

function polyAt(pts, s) {                       // point at arc length s along a polyline
  for (let i = 1; i < pts.length; i++) {
    const d = Math.hypot(pts[i].x - pts[i - 1].x, pts[i].y - pts[i - 1].y);
    if (s <= d || i === pts.length - 1) {
      const t = d ? clamp(s / d, 0, 1) : 0;
      return { x: pts[i - 1].x + (pts[i].x - pts[i - 1].x) * t, y: pts[i - 1].y + (pts[i].y - pts[i - 1].y) * t };
    }
    s -= d;
  }
  return pts[pts.length - 1];
}

function startAction(a) {
  const hurry = spider.queue.length > 2 ? 1.6 : 1;
  a.t0 = now;
  if (a.type === 'walk' || a.type === 'home') {
    const to = a.type === 'home' ? 'c' : nearEnd(a.strand);
    const r = route(spider.node, to);
    a.pts = r.ids.map(nodePos); a.len = r.len; a.to = to;
    a.dur = a.len < 1 ? 0 : clamp(a.len / (560 * U * hurry), 0.28, 1.5);
  } else if (a.type === 'spin') {
    const s = a.strand.seg, ends = [s.i + ':' + s.k, s.j + ':' + s.k];
    const from = spider.node === ends[1] ? ends[1] : ends[0];
    a.from = from; a.to = from === ends[0] ? ends[1] : ends[0];
    a.pts = [nodePos(a.from), nodePos(a.to)];
    a.len = Math.hypot(a.pts[1].x - a.pts[0].x, a.pts[1].y - a.pts[0].y);
    a.dur = clamp(a.len / (300 * U * hurry), 0.45, 0.95);
    a.strand.spin = a;
  } else if (a.type === 'pluck') {
    a.dur = 0.42; a.fired = false;
  } else if (a.type === 'snap') {
    a.dur = 0.3;
    tearStrand(a.strand, 0);
  } else a.dur = 0;
  if (a.onStart) a.onStart();
}
function nearEnd(strand) {
  const s = strand.seg, A = s.i + ':' + s.k, B = s.j + ':' + s.k;
  return route(spider.node, A).len <= route(spider.node, B).len ? A : B;
}

function stepSpider(dt) {
  const px = spider.x, py = spider.y;
  for (let guard = 0; guard < 20; guard++) {
    if (!spider.act) {
      if (!spider.queue.length) {
        if (spider.node !== 'c' && now - spider.idleSince > 2.6) enqueue({ type: 'home' });
        else break;
      }
      spider.act = spider.queue.shift();
      startAction(spider.act);
    }
    const a = spider.act, p = a.dur ? (now - a.t0) / a.dur : 1;
    if (a.pts) {
      const q = polyAt(a.pts, ease(p) * a.len);
      spider.x = q.x; spider.y = q.y;
    }
    if (a.type === 'pluck' && !a.fired && p > 0.45) {
      a.fired = true;
      const s = a.strand.seg, A = web.nodes[s.i][s.k], B = web.nodes[s.j][s.k];
      ripple((A.x + B.x) / 2, (A.y + B.y) / 2, 15, 1.8);
    }
    if (p < 1) break;
    if (a.type === 'spin') { a.strand.torn = false; a.strand.spin = null; a.strand.fresh = now; }
    if (a.to) spider.node = a.to;
    if (a.onEnd) a.onEnd();
    spider.act = null; spider.idleSince = now;
  }
  const vx = spider.x - px, vy = spider.y - py, v = Math.hypot(vx, vy);
  spider.speed = dt > 0 ? v / dt : 0;
  if (v > 0.05) {
    let d = Math.atan2(vy, vx) - spider.heading;
    d = Math.atan2(Math.sin(d), Math.cos(d));
    spider.heading += d * Math.min(1, dt * 14);
  }
  stepLegs(dt);
}

function drawSpider() {
  const U0 = U; U = U * 1.0;
  const o = P(spider), ox = o.x - spider.x, oy = o.y - spider.y;
  const h = spider.heading, c = Math.cos(h), s = Math.sin(h);
  let recoil = 0;
  if (spider.act && spider.act.type === 'pluck') {
    const p = (now - spider.act.t0) / spider.act.dur;
    recoil = (p < 0.45 ? -easeOut(p / 0.45) : -(1 - easeOut((p - 0.45) / 0.3))) * 4 * U;
  }
  const bx = o.x + c * recoil, by = o.y + s * recoil;
  ctx.strokeStyle = '#fff'; ctx.fillStyle = '#fff'; ctx.lineCap = 'round'; ctx.lineJoin = 'round';
  ctx.lineWidth = 1.5 * U;
  for (const leg of spider.legs) {
    const hx = bx + c * 4 * U + Math.cos(h + leg.a) * 3.6 * U, hy = by + s * 4 * U + Math.sin(h + leg.a) * 3.6 * U;
    const fx = leg.x + ox, fy = leg.y + oy;
    const dx = fx - hx, dy = fy - hy, d = Math.hypot(dx, dy) || 1, l = leg.reach * 0.6;
    const lift = Math.sqrt(Math.max(0, l * l - Math.min(d, 2 * l - 0.01) * Math.min(d, 2 * l - 0.01) / 4));
    const bend = (leg.n < 2 ? 1 : -1) * leg.side;       // front pairs bow forward, back pairs backward
    const kx = hx + dx / 2 + (dy / d) * lift * bend, ky = hy + dy / 2 - (dx / d) * lift * bend;
    ctx.beginPath(); ctx.moveTo(hx, hy); ctx.quadraticCurveTo(kx, ky, fx, fy); ctx.stroke();
  }
  ctx.beginPath(); ctx.ellipse(bx - c * 8.5 * U, by - s * 8.5 * U, 10 * U, 7.4 * U, h, 0, TAU); ctx.fill();   // abdomen
  ctx.beginPath(); ctx.ellipse(bx + c * 4 * U, by + s * 4 * U, 5.4 * U, 4.6 * U, h, 0, TAU); ctx.fill(); // head
  U = U0;
}

/* ------------------------------------------------------------ strands */
function tearStrand(strand, delay) {
  strand.torn = true; strand.tornAt = now + delay; strand.label = true; strand.spin = null;
}

function drawLoose(A, B, tau, phase, alpha) {
  const dx = B.x - A.x, dy = B.y - A.y, L = Math.hypot(dx, dy) * 0.43;
  const a0 = Math.atan2(dy, dx), ag = Math.PI / 2;
  let da = a0 - ag; da = Math.atan2(Math.sin(da), Math.cos(da));
  const th = ag + da * Math.exp(-1.5 * tau) * Math.cos(5.2 * tau) + 0.09 * Math.sin(now * 1.25 + phase) + 0.05 * Math.sin(now * 2.1 + phase * 2);
  const mid = ag + da * Math.exp(-1.0 * tau) * Math.cos(5.2 * tau - 0.9) * 0.9;
  const ex = A.x + Math.cos(th) * L, ey = A.y + Math.sin(th) * L;
  const qx = A.x + Math.cos(mid) * L * 0.5 + Math.cos(th + 1.57) * L * 0.1, qy = A.y + Math.sin(mid) * L * 0.5 + Math.sin(th + 1.57) * L * 0.1;
  ctx.globalAlpha = alpha;
  ctx.beginPath(); ctx.moveTo(A.x, A.y); ctx.quadraticCurveTo(qx, qy, ex, ey); ctx.stroke();
}

function curve(A, B, sag) {
  const mx = (A.x + B.x) / 2, my = (A.y + B.y) / 2;
  const qx = mx + (web.cx - mx) * sag, qy = my + (web.cy - my) * sag;
  ctx.beginPath(); ctx.moveTo(A.x, A.y); ctx.quadraticCurveTo(qx, qy, B.x, B.y); ctx.stroke();
}

function draw() {
  ctx.setTransform(DPR, 0, 0, DPR, 0, 0);
  ctx.fillStyle = '#000'; ctx.fillRect(0, 0, W, H);
  if (!web) return;
  const { N, K, radials, nodes, segs } = web;
  const grow = clamp((now - born) / 2.7, 0, 1);              // the web is spun on screen once
  const gr = ease(clamp(grow / 0.5, 0, 1)), gs = clamp((grow - 0.3) / 0.7, 0, 1);
  ctx.strokeStyle = '#fff'; ctx.lineCap = 'round';
  const pos = nodes.map((row) => row.map(P));
  const C = P({ x: web.cx, y: web.cy });

  // anchors and frame
  ctx.lineWidth = 1 * U; ctx.globalAlpha = 0.26 * ease(clamp((grow - 0.45) / 0.3, 0, 1));
  for (const a of web.anchors) { const f = P(a.from); ctx.beginPath(); ctx.moveTo(f.x, f.y); ctx.lineTo(a.to.x, a.to.y); ctx.stroke(); }
  ctx.globalAlpha = 0.34 * gs;
  for (let i = 0; i < N; i++) { const a = P(radials[i].end), b = P(radials[(i + 1) % N].end); const m = { x: (a.x + b.x) / 2, y: (a.y + b.y) / 2 }; const sx = web.cx, sy = web.cy; ctx.beginPath(); ctx.moveTo(a.x, a.y); ctx.quadraticCurveTo(m.x + (sx - m.x) * 0.05, m.y + (sy - m.y) * 0.05, b.x, b.y); ctx.stroke(); }

  // radials
  for (let i = 0; i < N; i++) {
    const end = P(radials[i].end);
    const pts = [C, ...pos[i], end];
    let total = 0; const ds = [];
    for (let q = 1; q < pts.length; q++) { const d = Math.hypot(pts[q].x - pts[q - 1].x, pts[q].y - pts[q - 1].y); ds.push(d); total += d; }
    let left = total * gr, g = 0;
    ctx.beginPath(); ctx.moveTo(C.x, C.y);
    for (let q = 1; q < pts.length && left > 0; q++) {
      const t = Math.min(1, left / ds[q - 1]);
      ctx.lineTo(pts[q - 1].x + (pts[q].x - pts[q - 1].x) * t, pts[q - 1].y + (pts[q].y - pts[q - 1].y) * t);
      left -= ds[q - 1]; g = Math.max(g, pts[q].g || 0);
    }
    ctx.globalAlpha = clamp(0.5 + g * 0.5, 0, 1); ctx.lineWidth = 1 * U; ctx.stroke();
  }

  // hub
  ctx.globalAlpha = 0.5 * gr; ctx.lineWidth = 0.9 * U; ctx.beginPath();
  web.hub.forEach((h, q) => { const p = P(h); if (q) ctx.lineTo(p.x, p.y); else ctx.moveTo(p.x, p.y); });
  ctx.stroke();

  // ring strands
  const total = segs.length;
  for (const s of segs) {
    if (s.order / total > gs) continue;
    const A = pos[s.i][s.k], B = pos[s.j][s.k];
    const st = s.file ? strands.get(s.file) : null;
    const g = Math.max(A.g, B.g);
    ctx.lineWidth = 1 * U;
    if (st && st.torn && now >= st.tornAt) {
      const tau = now - st.tornAt;
      let fadeOut = 1;
      if (st.spin) {
        const p = clamp((now - st.spin.t0) / st.spin.dur, 0, 1);
        fadeOut = 1 - ease(p);
        const from = P(nodePos(st.spin.from)), sp = P(spider);
        ctx.globalAlpha = 1; ctx.lineWidth = 1.3 * U; curve(from, sp, s.sag * p);
        ctx.lineWidth = 1 * U;
      }
      ctx.lineWidth = 1.6 * U;
      drawLoose(A, B, tau, s.phase, fadeOut);
      drawLoose(B, A, tau, s.phase + 2, fadeOut);
    } else {
      const fresh = st ? clamp(1 - (now - st.fresh) / 3.5, 0, 1) : 0;
      ctx.globalAlpha = clamp((s.file ? 0.66 : 0.3) + g * 0.6 + fresh * 0.34, 0, 1);
      ctx.lineWidth = (1 + fresh * 0.55 + Math.min(g, 1) * 0.3) * U;
      curve(A, B, s.sag);
    }
  }
  ctx.globalAlpha = 1;

  // labels: directory names at the end of each radial thread, file names on strands that matter
  ctx.fillStyle = '#fff'; ctx.textBaseline = 'middle';
  ctx.font = `italic ${14.5 * U}px "CMU Serif", serif`;
  for (let i = 0; i < N; i++) {
    const r = radials[i]; if (!r.dir) continue;
    const e = P(r.end), lx = e.x + r.ux * 13 * U, ly = e.y + r.uy * 13 * U;
    ctx.globalAlpha = 0.56 * gs;
    ctx.textAlign = r.ux > 0.25 ? 'left' : r.ux < -0.25 ? 'right' : 'center';
    ctx.fillText(r.dir === '.' ? 'root/' : r.dir + '/', lx, ly + (Math.abs(r.ux) < 0.25 ? r.uy * 6 * U : 0));
  }
  ctx.font = `italic ${13.5 * U}px "CMU Serif", serif`; ctx.textAlign = 'center';
  for (const st of strands.values()) {
    if (!st.label || now < st.tornAt) continue;
    const s = st.seg, A = pos[s.i][s.k], B = pos[s.j][s.k];
    let ang = Math.atan2(B.y - A.y, B.x - A.x);
    const mx = (A.x + B.x) / 2, my = (A.y + B.y) / 2;
    const ox = mx - web.cx, oy = my - web.cy, od = Math.hypot(ox, oy) || 1;
    if (ang > Math.PI / 2 || ang < -Math.PI / 2) ang += Math.PI;
    ctx.save(); ctx.translate(mx + (ox / od) * 12 * U, my + (oy / od) * 12 * U); ctx.rotate(ang);
    ctx.globalAlpha = clamp((now - st.tornAt) / 0.5, 0, 1) * (st.torn ? 1 : 0.55);
    const text = st.held ? s.name + ' (held for a human)' : s.name;
    const w = ctx.measureText(text).width;
    ctx.fillStyle = '#000'; ctx.globalAlpha *= 1; ctx.fillRect(-w / 2 - 3 * U, -8 * U, w + 6 * U, 16 * U);
    ctx.fillStyle = '#fff'; ctx.fillText(text, 0, 0);
    ctx.restore();
  }
  ctx.globalAlpha = 1;
  if (grow > 0.05) drawSpider();
}

/* ------------------------------------------------------------ ledger and numbers */
const tears = new Map();           // id -> { ev, strand, li, state }
const mendTimes = [];
let nOpen = 0, nMended = 0;

function setNums() {
  $('n-open').textContent = nOpen;
  $('n-mended').textContent = nMended;
  if (mendTimes.length) {
    const s = mendTimes.slice().sort((a, b) => a - b), m = s.length % 2 ? s[(s.length - 1) / 2] : (s[s.length / 2 - 1] + s[s.length / 2]) / 2;
    $('n-median').textContent = m.toFixed(1) + ' s';
  }
}
const esc = (s) => s.replace(/[&<>]/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;' }[c]));

function pickDiff(diff) {
  const real = (l) => l.slice(1).trim() && !/^[-+]\s*(import |from \S+ import )/.test(l);
  let del = diff.filter((l) => l[0] === '-' && real(l)), add = diff.filter((l) => l[0] === '+' && real(l));
  if (!del.length && !add.length) { del = diff.filter((l) => l[0] === '-'); add = diff.filter((l) => l[0] === '+'); }
  del = del.slice(0, add.length >= 2 ? 1 : 2);
  add = add.slice(0, 3 - del.length);
  return [...del, ...add];
}

function ledgerEntry(t, ev) {
  const li = t.li || document.createElement('li');
  const more = t.ev.rules.length > 1 ? ` (+${t.ev.rules.length - 1})` : '';
  const lines = pickDiff(ev.diff);
  li.className = 'pending';
  li.innerHTML =
    `<div class="l1"><span class="rule">${esc(t.ev.rule)}${more}</span><span class="where">${esc(t.ev.path)}:${t.ev.lines.join(', ')}</span></div>` +
    `<pre>${lines.map((l) => `<span class="${l[0] === '-' ? 'del' : 'add'}">${esc(l[0] + ' ' + l.slice(1).trim())}</span>`).join('')}</pre>` +
    `<div class="ver"><span class="steps">verifying</span><span class="meta">${[ev.backend, ev.attempt > 1 ? 'attempt ' + ev.attempt : ''].filter(Boolean).join(' · ')}</span></div>`;
  if (!t.li) {
    t.li = li; $('ledger').appendChild(li); fade(li, 0, 1, 0.5, 0, 6);
    const box = $('ledger');
    while (box.scrollHeight > box.clientHeight + 2 && box.children.length > 1) box.removeChild(box.firstChild);
  }
  t.steps = [];
}

/* ------------------------------------------------------------ events */
const status = (s) => { $('status').textContent = s; };
let commitShownAt = -99;

function handle(ev, instant) {
  switch (ev.type) {
    case 'init':
      status(`watching ./${ev.repo} with ${ev.semgrep ? 'Semgrep ' + ev.semgrep : 'Semgrep'}`);
      $('engine').textContent = `Semgrep ${ev.semgrep || ''}` + (ev.rule_count ? ` · ${ev.rule_count} registry rules` : '');
      break;
    case 'tree':
      buildWeb(ev.tree);
      break;
    case 'scan_start':
      status(ev.reason === 'commit' ? 'Semgrep is rescanning after the commit' : ev.reason === 'edit' ? 'Semgrep is rescanning after an edit' : 'Semgrep is scanning the repo');
      break;
    case 'scan_done':
      status(`Semgrep: ${ev.findings} finding${ev.findings === 1 ? '' : 's'} in ${ev.files} file${ev.files === 1 ? '' : 's'}, ${ev.seconds.toFixed(1)} s` + (ev.findings ? '' : '. Web intact.'));
      handle.stagger = 0;
      break;
    case 'tear': {
      const strand = strands.get(ev.path); if (!strand) break;
      const delay = instant ? 0 : (handle.stagger || 0) * 0.3; handle.stagger = (handle.stagger || 0) + 1;
      tears.set(ev.id, { ev, strand, li: null, steps: [] });
      tearStrand(strand, delay); strand.held = false;
      const fire = () => { nOpen++; setNums(); const s = strand.seg, A = web.nodes[s.i][s.k], B = web.nodes[s.j][s.k]; ripple((A.x + B.x) / 2, (A.y + B.y) / 2, 2.6, 0.25); };
      if (delay) later.push({ at: now + delay, fn: fire }); else fire();
      break;
    }
    case 'mend_start': {
      const t = tears.get(ev.id); if (!t) break;
      enqueue({ type: 'walk', strand: t.strand });
      break;
    }
    case 'patch': {
      const t = tears.get(ev.id); if (!t) break;
      enqueue({ type: 'walk', strand: t.strand });
      enqueue({ type: 'spin', strand: t.strand, onStart: () => ledgerEntry(t, ev) });
      break;
    }
    case 'verify': {
      const t = tears.get(ev.id); if (!t) break;
      enqueue({ type: 'dom', onStart: () => { t.steps.push(ev.detail); if (t.li) t.li.querySelector('.steps').textContent = t.steps.join(' · '); } });
      break;
    }
    case 'rollback': {
      const t = tears.get(ev.id); if (!t) break;
      enqueue({ type: 'snap', strand: t.strand, onStart: () => { if (t.li) t.li.querySelector('.steps').textContent = t.steps.join(' · ') + ' · rolled back'; } });
      break;
    }
    case 'mended': {
      const t = tears.get(ev.id); if (!t) break;
      enqueue({ type: 'pluck', strand: t.strand, onStart: () => {
        nOpen--; nMended++; mendTimes.push(ev.seconds); setNums();
        if (t.li) { t.li.className = 'mended'; t.li.querySelector('.steps').textContent = ev.verify; t.li.querySelector('.meta').textContent = [ev.backend, ev.ledger === 's3' ? 'ledger: s3' : '', `${ev.seconds.toFixed(1)} s`, ev.commit].filter(Boolean).join(' · '); }
        if (nOpen === 0) status('Web intact. Still watching.');
      } });
      break;
    }
    case 'rule_written': {
      const t = tears.get(ev.id); if (!t) break;
      enqueue({ type: 'dom', onStart: () => {
        if (!t.li || t.li.querySelector('.note')) return;
        const n = document.createElement('div'); n.className = 'note'; n.textContent = `rule written: ${ev.file}`;
        t.li.appendChild(n); fade(n, 0, 0.8, 0.5);
        const box = $('ledger');
        while (box.scrollHeight > box.clientHeight + 2 && box.children.length > 1) box.removeChild(box.firstChild);
      } });
      break;
    }
    case 'held': {
      const t = tears.get(ev.id); if (!t) break;
      enqueue({ type: 'dom', onStart: () => {
        t.strand.held = true; if (!t.strand.torn) tearStrand(t.strand, 0);
        if (t.li) { t.li.className = 'held'; t.li.querySelector('.steps').textContent = 'held for a human: ' + ev.reason; t.li.querySelector('.meta').textContent = 'rolled back'; }
      } });
      break;
    }
    case 'commit': {
      const el = $('commit');
      el.innerHTML = `<span>new commit by ${esc(ev.author)}</span><code>${esc(ev.sha)} ${esc(ev.subject)}</code>`;
      fade(el, 0, 1, 0.5); commitShownAt = now;
      break;
    }
    case 'error':
      status('error: ' + ev.message);
      break;
  }
}
const later = [];

/* ------------------------------------------------------------ feeds */
const INTRO = Q.has('intro') ? parseFloat(Q.get('intro') || '6') : 0;   // seconds of terminal card before the web
let termLines = [];
function intro() {                        // the command a user types, then the lines the CLI really printed
  if (!INTRO) return;
  const el = $('intro');
  if (now >= INTRO) { el.style.display = 'none'; return; }
  el.style.display = 'block';
  el.style.opacity = clamp(now / 0.35, 0, 1) * clamp((INTRO - now) / 0.3, 0, 1);
  const typed = (s, t0, cps) => s.slice(0, Math.max(0, Math.floor((now - t0) * cps)));
  const c1 = 'cd my-app', c2 = 'weaver watch .';
  const t1 = 0.7, t2 = 1.75, tOut = 3.15;
  const cur = (on) => (on && Math.floor(now * 2.5) % 2 === 0 ? '\u2588' : '');
  let html = '$ ' + esc(typed(c1, t1, 16)) + cur(now < t2 - 0.25);
  if (now >= t2 - 0.25) html += '\n$ ' + esc(typed(c2, t2, 16)) + cur(now < tOut);
  termLines.forEach((l, i) => { if (now >= tOut + i * 0.38) html += `\n<span class="out">${esc(l)}</span>`; });
  $('term').innerHTML = html;
}
const BROLL = 3.7;                       // seconds the benchmark page stays up before the score
let benchOK = null;
let replayEvents = null, replayT0 = 0, replayIdx = 0, outroDone = false;

function pump() {
  intro();
  for (let i = later.length - 1; i >= 0; i--) if (now >= later[i].at) { later[i].fn(); later.splice(i, 1); }
  if (replayEvents) {
    while (replayIdx < replayEvents.length && LEAD + (replayEvents[replayIdx].t - replayT0) / SPEED <= now) handle(replayEvents[replayIdx++], false);
    if (!outroDone && OUTRO != null && now >= OUTRO) {
      outroDone = true;
      if (benchOK !== null) { fade($('film'), 0, 1, 0.45); later.push({ at: now + BROLL, fn: () => { fade($('broll'), 1, 0, 0.3); fade($('score'), 0, 1, 0.45, 0.3); } }); }
    }
  }
  if (outroDone) { const q = clamp((now - OUTRO) / (BROLL + 0.3), 0, 1); $('broll-img').style.transform = `translateY(${-(q * q * (3 - 2 * q)) * 46.5}%) scale(${1 + 0.03 * q})`; }
  if (commitShownAt > 0 && !outroDone && now - commitShownAt > 9) { fade($('commit'), 1, 0, 0.8); commitShownAt = -99; }
}

let lastFrame = null;
function frame(t) {                       // t in seconds on the UI clock
  const dt = lastFrame == null ? 0 : Math.min(0.1, t - lastFrame);
  lastFrame = t;
  const sub = Math.max(1, Math.round(dt / 0.0167));
  for (let i = 1; i <= sub; i++) { now = t - dt + (dt * i) / sub; pump(); stepSpider(dt / sub); }
  now = t;
  for (let i = ripples.length - 1; i >= 0; i--) if (now - ripples[i].t0 > 5) ripples.splice(i, 1);
  stepFades();
  draw();
}

async function boot() {
  await Promise.all([
    document.fonts.load('16px "CMU Serif"'), document.fonts.load('italic 16px "CMU Serif"'),
    document.fonts.load('16px "CMU Typewriter Text"'),
  ]);
  resize(); window.addEventListener('resize', resize);
  $('film').style.opacity = 0; $('commit').style.opacity = 0;
  if (REPLAY) {
    const text = await (await fetch('/replay/' + encodeURIComponent(REPLAY))).text();
    replayEvents = text.trim().split('\n').map((l) => JSON.parse(l));
    replayT0 = replayEvents.find((e) => e.type === 'scan_start').t;
    while (replayIdx < replayEvents.length && replayEvents[replayIdx].t <= replayT0 && replayEvents[replayIdx].type !== 'scan_start') handle(replayEvents[replayIdx++], true);
    $('corner').textContent = `recorded run, replayed at ${SPEED}x`;
    if (INTRO) try { termLines = (await (await fetch('/replay/terminal.txt')).text()).trim().split('\n').slice(0, 3); } catch (e) { termLines = []; }
    try {                                   // independent check: numbers come straight from bench/results.json
      const b = (await (await fetch('/bench/results.json')).json()).summary;
      const pct = (a, n) => Math.round((100 * a) / n);
      let versus = '';
      try {                                 // measured baseline: Semgrep autofix alone on the same cases
        const base = (await (await fetch('/bench/baseline.json')).json()).summary;
        if (base.cases === b.flagged_in_benchmark_category) versus =
          `<p class="versus"><span class="dim">Semgrep autofix alone</span><span class="dim r">${base.fixed} / ${base.cases}</span><span class="dim r">${pct(base.fixed, base.cases)}%</span>` +
          `<span>Weaver</span><span class="r">${b.mended_in_benchmark_category} / ${b.flagged_in_benchmark_category}</span><span class="r">${pct(b.mended_in_benchmark_category, b.flagged_in_benchmark_category)}%</span></p>`;
      } catch (e) { versus = ''; }
      benchOK = b.held_in_benchmark_category === 0 && b.mended_in_benchmark_category === b.flagged_in_benchmark_category;
      $('score').innerHTML =
        `<p class="tag">Weaver. Semgrep finds it. Weaver mends it.</p>` +
        `<p class="big"><span>${b.mended_in_benchmark_category} / ${b.flagged_in_benchmark_category}</span><small>${pct(b.mended_in_benchmark_category, b.flagged_in_benchmark_category)}%</small></p>` +
        `<p class="what">${benchOK ? 'Every vulnerability' : 'Vulnerabilities'} Semgrep found in our OWASP Benchmark sample, mended.</p>` +
        (b.held_in_benchmark_category ? `<p class="heldline">The ${b.held_in_benchmark_category} it could not verify ${b.held_in_benchmark_category === 1 ? 'was' : 'were'} rolled back and held for a human.</p>` : '') +
        versus +
        `<p class="limit">${b.sampled}-case sample. ${b.flagged_other_issue_only + b.not_flagged} that Semgrep missed stay open. Mended = rescan clean and compiles.</p>`;
    } catch (e) { benchOK = null; }
  } else {
    const opened = Date.now() / 1000;
    const es = new EventSource('/events');
    es.onmessage = (m) => { const ev = JSON.parse(m.data); const old = ev.t < opened - 1.5; if (old) fastForward(ev); else handle(ev, false); };
    $('corner').textContent = 'live';
  }
  if (CAPTURE) {
    window.__frame = (ms) => frame(ms / 1000);
    frame(0);
  } else {
    const start = performance.now();
    const loop = (ts) => { frame((ts - start) / 1000); requestAnimationFrame(loop); };
    requestAnimationFrame(loop);
  }
  window.__ready = true;
}

/* events that happened before this page was opened are applied without animation */
function fastForward(ev) {
  handle(ev, true);
  while (spider.queue.length) {
    const a = spider.queue.shift();
    if (a.onStart) a.onStart();
    if (a.type === 'spin') { a.strand.torn = false; a.strand.spin = null; }
    if (a.type === 'snap') tearStrand(a.strand, 0);
    if (a.onEnd) a.onEnd();
  }
  spider.idleSince = now;
}

boot();
})();
