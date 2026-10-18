// CarbonTrail — "how it works": paper to proof.
// A photographed LPG invoice from the demo factory is read (scan + highlight), checked alongside three other
// documents, turned into an exact calculation, traced back to its line, and packed into the verifier's files.
// Real sample documents, flattened from the photos in /samples, are the textures.
import * as THREE from "/vendor/three.module.min.js";

const $ = (s, el = document) => el.querySelector(s);
const $$ = (s, el = document) => [...el.querySelectorAll(s)];
const section = $("#how");
const canvas = $("#how-canvas");
if (section && canvas) init();

function init() {
  const reduceMotion = matchMedia("(prefers-reduced-motion: reduce)").matches;
  const snap = reduceMotion || /[?&]snap\b/.test(location.search);
  const clamp = (v, a = 0, b = 1) => Math.min(b, Math.max(a, v));
  const ease = (t) => t * t * (3 - 2 * t);
  const seg = (p, a, b) => ease(clamp((p - a) / (b - a)));
  const mix = (a, b, t) => a + (b - a) * t;
  const small = () => innerWidth < 820;

  const steps = $$(".h-step", section);
  const prog = $$("#h-progress li", section);
  const tags = Object.fromEntries($$("[data-anchor]", section).map((el) => [el.dataset.anchor, el]));

  /* story stages: intro, read, check, calculate, trace, prove */
  const STAGES = [0, 0.1, 0.3, 0.5, 0.66, 0.84];
  const stageAt = (p) => STAGES.reduce((s, start, i) => (p >= start ? i : s), 0);
  let lastStage = -1;
  function setStage(p) {
    const s = stageAt(p);
    if (s === lastStage) return;
    steps.forEach((el, i) => el.classList.toggle("on", i === s));
    prog.forEach((el, i) => { el.classList.toggle("on", i === s - 1); el.classList.toggle("done", i < s - 1); });
    lastStage = s;
  }
  const readP = () => {
    const r = section.getBoundingClientRect();
    return { p: clamp(-r.top / Math.max(1, r.height - innerHeight)), visible: r.top < innerHeight && r.bottom > 0 };
  };

  let renderer = null;
  try {
    renderer = new THREE.WebGLRenderer({ canvas, antialias: true, powerPreference: "high-performance" });
  } catch (err) {
    steps.forEach((el) => el.classList.add("on"));
    return;
  }
  renderer.setPixelRatio(Math.min(devicePixelRatio || 1, small() ? 1.6 : 2));
  renderer.setClearColor(0x000000, 1);
  renderer.outputColorSpace = THREE.SRGBColorSpace;
  renderer.toneMapping = THREE.ACESFilmicToneMapping;
  renderer.toneMappingExposure = 1.0;

  const scene = new THREE.Scene();
  const camera = new THREE.PerspectiveCamera(34, 1, 0.1, 100);
  scene.add(new THREE.HemisphereLight(0xffffff, 0x222222, 1.15));
  const key = new THREE.DirectionalLight(0xffffff, 1.5); key.position.set(-4, 6, 9);
  const warm = new THREE.DirectionalLight(0xffc89a, 0.5); warm.position.set(6, -2, 4);
  scene.add(key, warm);

  /* soft glow on the floor of the stage */
  const glowTex = radial("rgba(59,225,154,0.22)", "rgba(59,225,154,0)");
  const glow = new THREE.Mesh(new THREE.PlaneGeometry(14, 14), new THREE.MeshBasicMaterial({ map: glowTex, transparent: true, depthWrite: false, toneMapped: false }));
  glow.position.set(0, 0, -3);
  scene.add(glow);

  /* ---------------- the documents ---------------- */
  const loader = new THREE.TextureLoader();
  const PAPERS = {
    lpg: { src: "/img/paper_lpg.jpg", w: 2.2, h: 3.168, hi: [0.05, 0.302, 0.945, 0.338], color: 0xFFD54A },
    wire: { src: "/img/wire_rod_statement.jpg", w: 2.2, h: 3.111, hi: [0.555, 0.318, 0.93, 0.35], color: 0x3BE19A },
    reg: { src: "/img/paper_register.jpg", w: 3.1, h: 2.325, hi: [0.405, 0.305, 0.545, 0.39], color: 0xF2B544 },
    diesel: { src: "/img/paper_diesel.jpg", w: 2.0, h: 3.09, hi: [0.03, 0.455, 0.63, 0.5], color: 0x8E8E93 },
  };
  const papers = {};
  for (const [id, cfg] of Object.entries(PAPERS)) {
    const g = new THREE.Group();
    const tex = loader.load(cfg.src);
    tex.colorSpace = THREE.SRGBColorSpace;
    tex.anisotropy = Math.min(8, renderer.capabilities.getMaxAnisotropy());
    const front = new THREE.Mesh(new THREE.PlaneGeometry(cfg.w, cfg.h), new THREE.MeshStandardMaterial({ map: tex, roughness: 0.9, metalness: 0, transparent: true }));
    const back = new THREE.Mesh(new THREE.PlaneGeometry(cfg.w, cfg.h), new THREE.MeshStandardMaterial({ color: 0xdedbd2, roughness: 1, transparent: true }));
    back.rotation.y = Math.PI;
    const [u0, v0, u1, v1] = cfg.hi;
    const hiMat = new THREE.MeshBasicMaterial({ color: cfg.color, transparent: true, opacity: 0, depthWrite: false, toneMapped: false });
    const hi = new THREE.Mesh(new THREE.PlaneGeometry((u1 - u0) * cfg.w, (v1 - v0) * cfg.h), hiMat);
    hi.position.set(((u0 + u1) / 2 - 0.5) * cfg.w, (0.5 - (v0 + v1) / 2) * cfg.h, 0.004);
    g.add(front, back, hi);
    scene.add(g);
    const local = (u, v) => new THREE.Vector3((u - 0.5) * cfg.w, (0.5 - v) * cfg.h, 0.01);
    papers[id] = { g, cfg, front, back, hiMat, local };
  }

  /* scan beam across the invoice */
  const beamTex = linear(["rgba(59,225,154,0)", "rgba(59,225,154,0.95)", "rgba(59,225,154,0)"]);
  const beam = new THREE.Mesh(new THREE.PlaneGeometry(PAPERS.lpg.w * 1.1, 0.34), new THREE.MeshBasicMaterial({ map: beamTex, transparent: true, opacity: 0, depthWrite: false, blending: THREE.AdditiveBlending, toneMapped: false }));
  beam.position.z = 0.02;
  papers.lpg.g.add(beam);

  /* the thread from the number back to its line */
  const threadMat = new THREE.MeshBasicMaterial({ color: 0xFFD54A, transparent: true, opacity: 0.95, toneMapped: false });
  let thread = null;
  const SEGS = 90;
  function setThread(from, to, k) {
    if (thread) { scene.remove(thread); thread.geometry.dispose(); thread = null; }
    if (k <= 0) return;
    const mid = from.clone().lerp(to, 0.5).add(new THREE.Vector3(0, 0.9, 0.8));
    const geo = new THREE.TubeGeometry(new THREE.QuadraticBezierCurve3(from, mid, to), SEGS, 0.022, 8, false);
    geo.setDrawRange(0, Math.floor(k * SEGS) * 8 * 6);
    thread = new THREE.Mesh(geo, threadMat);
    scene.add(thread);
  }

  /* ---------------- choreography ---------------- */
  // keyframes per document: [p, x, y, z, rx, ry, rz, scale, dim, opacity]
  const K = {
    lpg: [
      [0.0, 0.25, 0, 0, -0.1, -0.42, 0.05, 1, 0, 1], [0.08, 0.25, 0, 0, -0.1, -0.42, 0.05, 1, 0, 1],
      [0.14, 0, 0, 1.1, 0, -0.08, 0, 1.12, 0, 1], [0.29, 0, 0, 1.1, 0, -0.08, 0, 1.12, 0, 1],
      [0.36, -1.65, 1.2, 0, 0, 0.06, -0.02, 0.62, 0, 1], [0.49, -1.65, 1.2, 0, 0, 0.06, -0.02, 0.62, 0, 1],
      [0.56, -1.65, 1.2, -2.2, 0, 0.06, -0.02, 0.62, 0.7, 1], [0.66, -1.65, 1.2, -2.2, 0, 0.06, -0.02, 0.62, 0.7, 1],
      [0.72, -1.25, -0.1, 0.9, 0, 0.22, 0, 0.92, 0, 1], [0.83, -1.25, -0.1, 0.9, 0, 0.22, 0, 0.92, 0, 1],
      [0.92, 0, 0.25, 0, -1.0, 0, 0.08, 0.78, 0, 1], [1.0, 0, 0.25, 0, -1.0, 0, 0.08, 0.78, 0, 1],
    ],
    wire: [
      [0.0, 0.25, 0, -0.8, 0, -0.42, 0, 0.9, 0, 0], [0.3, 0.25, 0, -0.8, 0, -0.42, 0, 0.9, 0, 0],
      [0.37, 1.65, 1.2, 0, 0, -0.06, 0.02, 0.62, 0, 1], [0.49, 1.65, 1.2, 0, 0, -0.06, 0.02, 0.62, 0, 1],
      [0.56, 1.65, 1.2, -2.2, 0, -0.06, 0.02, 0.62, 0.7, 1], [0.83, 1.65, 1.2, -2.2, 0, -0.06, 0.02, 0.62, 0.7, 1],
      [0.92, 0, 0.36, -0.08, -1.0, 0, -0.05, 0.78, 0, 1], [1.0, 0, 0.36, -0.08, -1.0, 0, -0.05, 0.78, 0, 1],
    ],
    reg: [
      [0.0, 0.25, 0, -0.8, 0, -0.42, 0, 0.9, 0, 0], [0.31, 0.25, 0, -0.8, 0, -0.42, 0, 0.9, 0, 0],
      [0.38, -1.55, -1.3, 0, 0, 0.05, 0.015, 0.62, 0, 1], [0.49, -1.55, -1.3, 0, 0, 0.05, 0.015, 0.62, 0, 1],
      [0.56, -1.55, -1.3, -2.2, 0, 0.05, 0.015, 0.62, 0.7, 1], [0.83, -1.55, -1.3, -2.2, 0, 0.05, 0.015, 0.62, 0.7, 1],
      [0.92, 0, 0.14, 0.08, -1.0, 0, 0.12, 0.64, 0, 1], [1.0, 0, 0.14, 0.08, -1.0, 0, 0.12, 0.64, 0, 1],
    ],
    diesel: [
      [0.0, 0.25, 0, -0.8, 0, -0.42, 0, 0.9, 0, 0], [0.32, 0.25, 0, -0.8, 0, -0.42, 0, 0.9, 0, 0],
      [0.39, 1.6, -1.35, 0, 0, -0.05, -0.02, 0.62, 0, 1], [0.49, 1.6, -1.35, 0, 0, -0.05, -0.02, 0.62, 0, 1],
      [0.56, 1.6, -1.35, -2.2, 0, -0.05, -0.02, 0.62, 0.85, 0.7], [0.83, 1.6, -1.35, -2.2, 0, -0.05, -0.02, 0.62, 0.85, 0.7],
      [0.92, 0.9, -2.2, -1.5, -1.05, 0, 0, 0.42, 0.9, 0], [1.0, 0.9, -2.2, -1.5, -1.05, 0, 0, 0.42, 0.9, 0],
    ],
  };
  const pose = (frames, p) => {
    let i = 0; while (i < frames.length - 2 && p > frames[i + 1][0]) i++;
    const a = frames[i], b = frames[i + 1];
    const t = ease(clamp((p - a[0]) / Math.max(1e-6, b[0] - a[0])));
    return a.map((v, j) => (j === 0 ? p : mix(v, b[j], t)));
  };
  const white = new THREE.Color(0xffffff), dimC = new THREE.Color(0x3a3a3c);
  function layout(P, time) {
    for (const [id, pr] of Object.entries(papers)) {
      const [, x, y, z, rx, ry, rz, s, dim, op] = pose(K[id], P);
      const bob = reduceMotion ? 0 : Math.sin(time * 0.8 + x) * 0.03;
      pr.g.position.set(x, y + bob, z);
      pr.g.rotation.set(rx, ry + (id === "lpg" && P < 0.1 && !reduceMotion ? Math.sin(time * 0.5) * 0.06 : 0), rz);
      pr.g.scale.setScalar(s);
      pr.front.material.color.copy(white).lerp(dimC, dim);
      pr.back.material.color.set(0xdedbd2).lerp(dimC, dim);
      pr.front.material.opacity = pr.back.material.opacity = op;
      pr.g.visible = op > 0.01;
    }
    // highlights
    const lpgHi = seg(P, 0.19, 0.23) * (1 - seg(P, 0.85, 0.9));
    const pulse = P > 0.7 && P < 0.84 && !reduceMotion ? 0.25 * Math.sin(time * 5) : 0;
    papers.lpg.hiMat.opacity = 0.38 * lpgHi * (P > 0.5 && P < 0.68 ? 0.4 : 1) + pulse * lpgHi;
    const chk = (a) => seg(P, a, a + 0.03) * (1 - seg(P, 0.5, 0.54));
    papers.wire.hiMat.opacity = 0.38 * chk(0.4);
    papers.reg.hiMat.opacity = 0.45 * chk(0.43);
    papers.diesel.hiMat.opacity = 0.3 * chk(0.46);
    // scan beam: top to bottom of the table, then fades
    const sweep = clamp((P - 0.13) / 0.09);
    beam.material.opacity = (P > 0.13 && P < 0.23) ? Math.sin(sweep * Math.PI) * 0.9 : 0;
    beam.position.y = mix(PAPERS.lpg.h * 0.45, -PAPERS.lpg.h * 0.05, sweep);
    glow.material.opacity = 0.6 + 0.4 * seg(P, 0.86, 0.95);
  }

  /* ---------------- camera ---------------- */
  function resize() {
    const w = section.clientWidth, h = $(".how-pin", section).clientHeight || innerHeight;
    renderer.setSize(w, h, false);
    camera.aspect = w / h;
    camera.fov = small() ? 50 : 34;
    if (small()) camera.setViewOffset(w, h, 0, h * 0.2, w, h);
    else camera.setViewOffset(w, h, -w * 0.2, 0, w, h);
    camera.updateProjectionMatrix();
  }
  addEventListener("resize", resize);
  resize();

  /* ---------------- labels ---------------- */
  const v = new THREE.Vector3();
  const toScreen = (world) => {
    v.copy(world).project(camera);
    return { x: (v.x + 1) / 2 * canvas.clientWidth, y: (1 - v.y) / 2 * canvas.clientHeight, ok: v.z < 1 };
  };
  // how: "above" = centred above the point, "below" = centred under it, "right" = to its right, "center" = centred on it
  const place = (el, world, show, how = "above") => {
    if (!show) { el.classList.remove("on"); return; }
    const s = toScreen(world);
    if (!s.ok) { el.classList.remove("on"); return; }
    const W = canvas.clientWidth, H = canvas.clientHeight, ew = el.offsetWidth, eh = el.offsetHeight;
    let x = s.x - ew / 2, y = s.y - eh - 8;
    if (how === "right") { x = s.x + 18; y = s.y - eh / 2; }
    if (how === "center") { y = s.y - eh / 2; }
    if (how === "below") { y = s.y + 10; }
    x = Math.min(Math.max(x, 12), W - ew - 12);
    y = Math.min(Math.max(y, 12), H - eh - 80);
    el.style.transform = `translate(${x.toFixed(1)}px, ${y.toFixed(1)}px)`;
    el.classList.add("on");
  };
  const world = (id, u, v2) => papers[id].g.localToWorld(papers[id].local(u, v2));
  const calcAnchor = new THREE.Vector3();

  /* ---------------- loop ---------------- */
  let time = 0, last = performance.now(), pCur = readP().p, running = true;
  function frame(now) {
    if (!running) return;
    requestAnimationFrame(frame);
    const dt = Math.min(0.05, (now - last) / 1000); last = now;
    const { p, visible } = readP();
    if (!visible || document.hidden) return;
    time += reduceMotion ? 0 : dt;
    pCur = snap ? p : pCur + (p - pCur) * (1 - Math.pow(0.004, dt));
    const P = Math.abs(pCur - p) < 0.0005 ? p : pCur;
    setStage(P);
    layout(P, time);

    const m = small() ? 1.25 : 1;
    camera.position.set(Math.sin(time * 0.15) * 0.25, 0.1 + Math.sin(time * 0.2) * 0.1, mix(9.2, 10.2, seg(P, 0.3, 0.38)) * m);
    camera.lookAt(0, 0, 0);
    scene.updateMatrixWorld();

    // labels
    const lpg = papers.lpg.cfg.hi;
    place(tags.read, world("lpg", lpg[2], (lpg[1] + lpg[3]) / 2), P > 0.225 && P < 0.31, small() ? "above" : "right");
    place(tags["b-lpg"], world("lpg", 0.5, 0), P > 0.385 && P < 0.505);
    place(tags["b-wire"], world("wire", 0.5, 0), P > 0.405 && P < 0.505);
    place(tags["b-reg"], world("reg", 0.5, 0), P > 0.435 && P < 0.505);
    place(tags["b-diesel"], world("diesel", 0.5, 0), P > 0.465 && P < 0.505);
    // the calculation card sits centre stage, then steps right while the thread is drawn
    calcAnchor.set(mix(0.3, 2.0, seg(P, 0.66, 0.72)), mix(0.1, 0.45, seg(P, 0.66, 0.72)), 1.4);
    place(tags.calc, calcAnchor, P > 0.535 && P < 0.84, "center");
    // thread from the card's left edge to the invoice line
    const k = seg(P, 0.72, 0.79) * (1 - seg(P, 0.84, 0.87));
    if (k > 0 && !small()) {
      // the card's left edge, at the card's depth: shift its anchor left in screen space and unproject
      const halfW = tags.calc.offsetWidth / canvas.clientWidth;          // half the card width, in NDC units
      const from = calcAnchor.clone().project(camera);
      from.x -= halfW; from.unproject(camera);
      setThread(from, world("lpg", lpg[2], (lpg[1] + lpg[3]) / 2), k);
    } else setThread(null, null, 0);
    place(tags.trace, world("lpg", 0.3, lpg[3]), P > 0.76 && P < 0.84, "below");
    if (small()) {   // phones: the two files stack under the papers
      place(tags.xlsx, new THREE.Vector3(0, -1.05, 0.6), P > 0.9, "center");
      place(tags.pdf, new THREE.Vector3(0, -2.7, 0.6), P > 0.92, "center");
    } else {
      place(tags.xlsx, new THREE.Vector3(-1.45, -1.55, 0.6), P > 0.9, "center");
      place(tags.pdf, new THREE.Vector3(1.45, -1.55, 0.6), P > 0.92, "center");
    }

    renderer.render(scene, camera);
  }
  requestAnimationFrame(frame);

  document.addEventListener("ct:workspace", () => { running = false; renderer.dispose(); }, { once: true });

  /* ---------------- helpers ---------------- */
  function radial(inner, outer) {
    const c = document.createElement("canvas"); c.width = c.height = 256;
    const x = c.getContext("2d"), g = x.createRadialGradient(128, 128, 0, 128, 128, 128);
    g.addColorStop(0, inner); g.addColorStop(1, outer);
    x.fillStyle = g; x.fillRect(0, 0, 256, 256);
    const t = new THREE.CanvasTexture(c); t.colorSpace = THREE.SRGBColorSpace; return t;
  }
  function linear(stops) {
    const c = document.createElement("canvas"); c.width = 4; c.height = 128;
    const x = c.getContext("2d"), g = x.createLinearGradient(0, 0, 0, 128);
    stops.forEach((s, i) => g.addColorStop(i / (stops.length - 1), s));
    x.fillStyle = g; x.fillRect(0, 0, 4, 128);
    const t = new THREE.CanvasTexture(c); t.colorSpace = THREE.SRGBColorSpace; return t;
  }
}
