// CarbonTrail — "the journey": one container of bolts from a factory in Ludhiana to a buyer in Germany.
// Scroll-driven 3D diorama in a clay style. The EU border check is the climax: amber on EU defaults,
// green once CarbonTrail's actual, verifier-ready data travels with the goods.
import * as THREE from "/vendor/three.module.min.js";

const $ = (s, el = document) => el.querySelector(s);
const $$ = (s, el = document) => [...el.querySelectorAll(s)];
const section = $("#journey");
const canvas = $("#journey-canvas");
if (section && canvas) init();

function init() {
  const reduceMotion = matchMedia("(prefers-reduced-motion: reduce)").matches;
  const snap = reduceMotion || /[?&]snap\b/.test(location.search);   // ?snap: no smoothing (used for screenshots)
  const clamp = (v, a = 0, b = 1) => Math.min(b, Math.max(a, v));
  const ease = (t) => t * t * (3 - 2 * t);
  const seg = (p, a, b) => ease(clamp((p - a) / (b - a)));
  const mix = (a, b, t) => a + (b - a) * t;

  const steps = $$(".j-step", section);
  const prog = $$("#j-progress li", section);
  const tags = Object.fromEntries($$("[data-anchor]", section).map((el) => [el.dataset.anchor, el]));
  const bill = tags.gate, billEur = $("#j-bill-eur"), billNote = $("#j-bill-note");
  const carbonTag = tags.container;

  /* stage of the story for a scroll position p (0..1) */
  const STAGES = [0, 0.1, 0.3, 0.66, 0.8, 0.89];
  const stageAt = (p) => STAGES.reduce((s, start, i) => (p >= start ? i : s), 0);
  let lastStage = -1, lastVerified = null;
  function setStage(p) {
    const s = stageAt(p);
    if (s !== lastStage) {
      steps.forEach((el, i) => el.classList.toggle("on", i === s));
      prog.forEach((el, i) => { el.classList.toggle("on", i === s); el.classList.toggle("done", i < s); });
      lastStage = s;
    }
    // container label: undocumented at the factory, EU default at the border, actual data once the proof arrives
    const verified = p >= 0.82;
    const tagState = verified ? 2 : p >= 0.66 ? 1 : 0;
    if (tagState !== lastVerified) {
      bill.classList.toggle("verified", verified);
      carbonTag.classList.toggle("verified", verified);
      carbonTag.classList.toggle("assumed", tagState === 1);
      billEur.textContent = verified ? "€39,162" : "€73,865";
      billNote.textContent = verified ? "Gross · actual data, ready for the verifier" : "Gross · EU default + 10% mark-up";
      carbonTag.innerHTML = ["Carbon inside: <b>not yet documented</b>", "<b>6.13</b> tCO2e/t · EU default + 10%", "<b>3.25</b> tCO2e/t · actual data"][tagState];
      lastVerified = tagState;
    }
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

  const small = () => innerWidth < 820;
  renderer.setPixelRatio(Math.min(devicePixelRatio || 1, small() ? 1.6 : 2));
  renderer.setClearColor(0xF5F5F7, 1);
  renderer.outputColorSpace = THREE.SRGBColorSpace;
  renderer.toneMapping = THREE.ACESFilmicToneMapping;
  renderer.toneMappingExposure = 1.0;
  renderer.shadowMap.enabled = true;
  renderer.shadowMap.type = THREE.PCFSoftShadowMap;

  const scene = new THREE.Scene();
  scene.fog = new THREE.Fog(0xF5F5F7, 55, 135);
  const camera = new THREE.PerspectiveCamera(34, 1, 0.5, 400);

  const hemi = new THREE.HemisphereLight(0xffffff, 0xcfd6dc, 1.6);
  const sun = new THREE.DirectionalLight(0xffffff, 2.2);
  sun.castShadow = true;
  sun.shadow.mapSize.set(2048, 2048);
  Object.assign(sun.shadow.camera, { left: -28, right: 28, top: 28, bottom: -28, near: 1, far: 90 });
  sun.shadow.bias = -0.0004;
  sun.shadow.normalBias = 0.02;
  scene.add(hemi, sun, sun.target);

  /* ---------------- materials ---------------- */
  const std = (color, o = {}) => new THREE.MeshStandardMaterial({ color, roughness: 0.82, metalness: 0, ...o });
  const M = {
    clay: std(0xE9EBED), clayMid: std(0xD6DADE), clayDark: std(0xB9BFC5), ink: std(0x2A2E33, { roughness: 0.6 }),
    india: std(0xECE7DF), eu: std(0xE8ECEF), road: std(0xD7D2CA), roadEU: std(0xD3D8DD),
    sea: std(0xB9D2E1, { roughness: 0.32, metalness: 0.05 }), glass: std(0x3A4752, { roughness: 0.2, metalness: 0.2 }),
    hullStripe: std(0x39424A), door: std(0x30363C),
  };
  const ribs = ribTexture();
  const containerMat = new THREE.MeshStandardMaterial({ color: 0xFF6A2B, map: ribs, roughness: 0.6, emissive: 0xFF4A10, emissiveIntensity: 0.06 });
  const otherColors = [0x9FB3C8, 0xD9C6A8, 0xB4C8B2, 0xC9CED4, 0x8F9BB0, 0xE0B9A8];

  const add = (geo, mat, x = 0, y = 0, z = 0, parent = scene) => {
    const m = new THREE.Mesh(geo, mat);
    m.position.set(x, y, z);
    m.castShadow = true; m.receiveShadow = true;
    parent.add(m);
    return m;
  };
  const box = (w, h, d, mat, x, y, z, parent) => add(new THREE.BoxGeometry(w, h, d), mat, x, y, z, parent);

  /* ---------------- world ---------------- */
  const sea = add(new THREE.PlaneGeometry(600, 600), M.sea, 10, -0.32, 0);
  sea.rotation.x = -Math.PI / 2; sea.castShadow = false;
  box(70, 2, 70, M.india, -39, -1, -32.5).castShadow = false;        // India: x -74..-4, z -67.5..2.5
  box(70, 2, 70, M.eu, 61, -1, -32.5).castShadow = false;            // EU: x 26..96
  box(24, 0.04, 2.6, M.road, -17, 0.02, 0).castShadow = false;
  box(26, 0.04, 2.6, M.roadEU, 41, 0.02, 0).castShadow = false;
  box(70, 0.25, 0.25, M.clayDark, -39, 0.12, 2.45).castShadow = false; // quay edges
  box(70, 0.25, 0.25, M.clayDark, 61, 0.12, 2.45).castShadow = false;

  /* factory */
  const FACTORY = new THREE.Vector3(-34, 0, -6);
  box(11, 3.4, 7.5, M.clay, FACTORY.x, 1.7, FACTORY.z);
  const tooth = new THREE.Shape(); tooth.moveTo(0, 0); tooth.lineTo(2.75, 0); tooth.lineTo(0, 1.5); tooth.closePath();
  for (let i = 0; i < 4; i++) {
    const g = new THREE.ExtrudeGeometry(tooth, { depth: 7.5, bevelEnabled: false });
    g.translate(FACTORY.x - 5.5 + i * 2.75, 3.4, FACTORY.z - 3.75);
    add(g, i % 2 ? M.clayMid : M.clay);
  }
  add(new THREE.CylinderGeometry(0.42, 0.55, 8, 24), M.clayMid, FACTORY.x + 4.2, 4, FACTORY.z - 2.8);
  box(3, 2.2, 0.1, M.door, FACTORY.x + 2.5, 1.1, FACTORY.z + 3.76).castShadow = false;
  for (let i = 0; i < 3; i++) box(1.4, 0.7, 0.1, M.glass, FACTORY.x - 4 + i * 2, 2.3, FACTORY.z + 3.76).castShadow = false;
  const smoke = buildSmoke(new THREE.Vector3(FACTORY.x + 4.2, 8.1, FACTORY.z - 2.8));
  scene.add(smoke.points);

  /* ports: one crane each side */
  const craneA = buildCrane(-8), craneB = buildCrane(31);

  /* EU border gate + warehouse */
  const GATE_X = 41;
  const gateMat = std(0x2A2E33);
  box(0.45, 4.6, 0.45, gateMat, GATE_X, 2.3, -2);
  box(0.45, 4.6, 0.45, gateMat, GATE_X, 2.3, 2);
  box(0.6, 0.6, 4.6, gateMat, GATE_X, 4.6, 0);
  const scanMat = new THREE.MeshBasicMaterial({ color: 0xF2B544, transparent: true, opacity: 0.0, depthWrite: false, side: THREE.DoubleSide });
  const scanPlane = new THREE.Mesh(new THREE.PlaneGeometry(3.6, 4.2), scanMat);
  scanPlane.rotation.y = Math.PI / 2; scanPlane.position.set(GATE_X, 2.2, 0); scene.add(scanPlane);
  const sweepMat = new THREE.MeshBasicMaterial({ color: 0x3BE19A, transparent: true, opacity: 0, depthWrite: false, side: THREE.DoubleSide });
  const sweep = new THREE.Mesh(new THREE.PlaneGeometry(3.4, 3.2), sweepMat);
  sweep.rotation.y = Math.PI / 2; scene.add(sweep);
  const gateLight = new THREE.PointLight(0xF2B544, 0, 14, 2); gateLight.position.set(GATE_X, 4, 0); scene.add(gateLight);
  const WAREHOUSE = new THREE.Vector3(55, 0, -0.5);
  box(12, 5, 11, M.clay, WAREHOUSE.x, 2.5, WAREHOUSE.z);
  box(0.2, 0.6, 11.4, M.clayDark, WAREHOUSE.x - 6, 5.2, WAREHOUSE.z);
  box(0.1, 3.4, 3.2, M.door, WAREHOUSE.x - 6.02, 1.7, 0).castShadow = false;

  /* scenery: a few warehouses and container stacks near both ports */
  [[-20, -10, 7, 3, 6], [-12, -14, 5, 2.4, 5], [20 + 26 - 8, -12, 6, 2.8, 6], [64, -14, 8, 3.2, 7]].forEach(([x, z, w, h, d]) => box(w, h, d, M.clayMid, x, h / 2, z));
  // two stacks side by side at each port, with a gap between them, so every container reads as its own box
  [[-15, -5], [-15, -6.5], [35, -6], [35, -7.5]].forEach(([x, z], i) => {
    for (let t = 0; t < 2; t++) {
      const col = otherColors[(i * 2 + t) % otherColors.length];
      const side = new THREE.MeshStandardMaterial({ color: col, map: ribs, roughness: 0.7 });
      const end = new THREE.MeshStandardMaterial({ color: new THREE.Color(col).multiplyScalar(0.78), roughness: 0.75 });
      const c = box(4.8, 1.26, 1.26, side, x, 0.63 + t * 1.3, z);
      c.material = [end, end, side, side, side, side];
    }
  });

  /* the trail: the container's route, drawn as it travels (it turns green once verified) */
  const ROUTE = [[-27.4, 0], [-8, 0], [-8, 5.6], [31, 5.6], [31, 0], [41, 0], [51, 0]];
  const routePath = new THREE.CurvePath();
  for (let i = 0; i < ROUTE.length - 1; i++) {
    const y = (i === 2) ? -0.12 : 0.16;
    routePath.add(new THREE.LineCurve3(new THREE.Vector3(ROUTE[i][0], y, ROUTE[i][1]), new THREE.Vector3(ROUTE[i + 1][0], y, ROUTE[i + 1][1])));
  }
  const trailGeo = new THREE.TubeGeometry(routePath, 900, 0.16, 6, false);
  const trailMat = new THREE.MeshBasicMaterial({ color: 0xFF6A2B, toneMapped: false });
  const trail = new THREE.Mesh(trailGeo, trailMat);
  trail.renderOrder = 2;
  scene.add(trail);
  const SEGS = [19.4, 5.6, 39, 5.6, 10, 10];
  const TOTAL = SEGS.reduce((a, b) => a + b, 0);
  const trailK = (p) => {
    const parts = [[0.1, 0.28], [0.285, 0.36], [0.37, 0.64], [0.645, 0.715], [0.72, 0.79], [0.885, 0.985]];
    let d = 0;
    parts.forEach(([a, b], i) => { d += SEGS[i] * seg(p, a, b); });
    return d / TOTAL;
  };

  /* trucks, ship, our container */
  const truck1 = buildTruck(), truck2 = buildTruck();
  const ship = buildShip();
  const container = box(4.8, 1.3, 1.3, containerMat, 0, 0, 0);
  container.material = [containerMat, containerMat, std(0xF27A45), std(0xF27A45), containerMat, containerMat];

  /* the factory's paperwork, flying into the container */
  const loader = new THREE.TextureLoader();
  const papers = ["02_lpg_invoice_apr.jpg", "07_production_register_apr-jun.jpg", "05_diesel_dg_statement.jpg"].map((f, i) => {
    const mat = new THREE.MeshBasicMaterial({ transparent: true, opacity: 0, side: THREE.DoubleSide, depthWrite: false, toneMapped: false });
    loader.load(`/samples/${f}`, (t) => { t.colorSpace = THREE.SRGBColorSpace; mat.map = t; mat.needsUpdate = true; });
    const m = new THREE.Mesh(new THREE.PlaneGeometry(1.6, 1.15), mat);
    m.userData.off = new THREE.Vector3(-5 + i * 2.2, 6 + i * 0.8, 5 - i);
    scene.add(m);
    return m;
  });

  /* ---------------- timeline ---------------- */
  const BED = 1.6;                    // container centre height on a truck
  const SLOT = new THREE.Vector3(3.5, 3.7, 0); // container slot on the ship, relative to ship origin (y in world)
  const SHIP_Z = 5.6;
  const C = new THREE.Vector3();
  function layout(p, time) {
    /* truck 1: yard → India quay */
    const t1x = mix(-27, -7.6, seg(p, 0.1, 0.28));
    truck1.position.set(t1x, 0, 0);
    /* ship: India dock → EU dock */
    const shipX = mix(mix(-11.5, 27.5, seg(p, 0.37, 0.64)), 80, seg(p, 0.72, 0.82));
    const bob = reduceMotion ? 0 : Math.sin(time * 1.2) * 0.05;
    ship.position.set(shipX, -0.25 + bob, SHIP_Z);
    ship.rotation.z = reduceMotion ? 0 : Math.sin(time * 0.9) * 0.008;
    /* truck 2: EU quay → gate → warehouse */
    const t2x = mix(mix(31.4, GATE_X + 0.4, seg(p, 0.72, 0.79)), 51.5, seg(p, 0.885, 0.985));
    truck2.position.set(t2x, 0, 0);

    /* container */
    const dockX = mix(-11.5, 27.5, seg(p, 0.37, 0.64));
    const onShip = new THREE.Vector3(dockX + SLOT.x, SLOT.y + bob, SHIP_Z);
    if (p < 0.28) C.set(t1x - 0.4, BED, 0);
    else if (p < 0.37) {                               // crane A: up, across, down
      const up = seg(p, 0.285, 0.31), across = seg(p, 0.31, 0.335), down = seg(p, 0.335, 0.36);
      C.set(-8, mix(mix(BED, 7, up), onShip.y, down), mix(0, SHIP_Z, across));
    } else if (p < 0.64) C.copy(onShip);
    else if (p < 0.72) {                               // crane B
      const up = seg(p, 0.645, 0.665), across = seg(p, 0.665, 0.69), down = seg(p, 0.69, 0.715);
      C.set(31, mix(mix(onShip.y, 7, up), BED, down), mix(SHIP_Z, 0, across));
    } else C.set(t2x - 0.4, BED, 0);
    container.position.copy(C);

    /* cranes follow the container while it hangs */
    craneA.set(p >= 0.28 && p < 0.37 ? C : null, p < 0.28 ? 0 : p < 0.37 ? null : SHIP_Z);
    craneB.set(p >= 0.64 && p < 0.72 ? C : null, p < 0.64 ? SHIP_Z : p < 0.72 ? null : 0);

    /* paperwork */
    papers.forEach((m, i) => {
      const k = seg(p, 0.12 + i * 0.03, 0.22 + i * 0.03);
      const start = C.clone().add(m.userData.off);
      m.position.lerpVectors(start, C, k);
      m.position.y += Math.sin(k * Math.PI) * 1.2;
      m.material.opacity = k > 0 && k < 1 ? Math.min(1, k * 4, (1 - k) * 5) : 0;
      m.rotation.set(-0.3 + k * 0.3, 0.4 - k * 0.4 + i * 0.2, (1 - k) * (i - 1) * 0.5);
      m.scale.setScalar(mix(1, 0.25, k));
    });

    /* border check */
    const atGate = seg(p, 0.75, 0.79);
    const scanK = clamp((p - 0.8) / 0.07);
    const verified = p >= 0.82;
    scanMat.color.set(verified ? 0x3BE19A : 0xF2B544);
    scanMat.opacity = 0.16 * atGate * (1 - seg(p, 0.9, 0.95));
    gateLight.color.set(verified ? 0x3BE19A : 0xF2B544);
    gateLight.intensity = 40 * atGate * (1 - seg(p, 0.92, 0.97));
    sweep.position.set(C.x - 2.6 + scanK * 5.2, 2.0, 0);
    sweepMat.opacity = scanK > 0 && scanK < 1 ? 0.55 : 0;
    containerMat.emissiveIntensity = mix(0.06, 0.0, seg(p, 0.8, 0.86));

    smoke.uniforms.uTime.value = time;
    const k = trailK(p);
    trailGeo.setDrawRange(0, Math.floor(k * trailGeo.index.count / 6) * 6);
    trailMat.color.set(p >= 0.82 ? 0x3BE19A : 0xFF6A2B);
    return C;
  }

  /* ---------------- camera path ---------------- */
  const CAM = [ // p, offset x, y, z (from the container)
    [0.0, -14, 22, 24], [0.1, -12, 19, 21], [0.28, -12, 19, 21], [0.33, -12, 22, 24], [0.42, -12, 30, 34],
    [0.55, -10, 32, 38], [0.64, -12, 22, 24], [0.72, -4, 18, 21], [0.79, 8, 14, 18], [0.88, 8, 14, 18], [1.0, -2, 20, 26],
  ];
  const kf = (p) => {
    let i = 0; while (i < CAM.length - 2 && p > CAM[i + 1][0]) i++;
    const [p0, ...a] = CAM[i], [p1, ...b] = CAM[i + 1];
    const t = ease(clamp((p - p0) / (p1 - p0)));
    return new THREE.Vector3(mix(a[0], b[0], t), mix(a[1], b[1], t), mix(a[2], b[2], t));
  };
  const target = new THREE.Vector3(), camPos = new THREE.Vector3();
  let first = true;

  function resize() {
    const w = section.clientWidth, h = $(".journey-pin", section).clientHeight || innerHeight;
    renderer.setSize(w, h, false);
    camera.aspect = w / h;
    if (small()) camera.setViewOffset(w, h, 0, h * 0.14, w, h);
    else camera.setViewOffset(w, h, -w * 0.17, 0, w, h);
    camera.fov = small() ? 44 : 34;
    camera.updateProjectionMatrix();
  }
  addEventListener("resize", resize);
  resize();

  /* ---------------- labels ---------------- */
  const v = new THREE.Vector3();
  const placeTag = (el, world, show) => {
    if (!show) { el.classList.remove("on"); return; }
    v.copy(world).project(camera);
    const w = canvas.clientWidth, h = canvas.clientHeight;
    if (v.z > 1) { el.classList.remove("on"); return; }
    const x = (v.x + 1) / 2 * w, y = (1 - v.y) / 2 * h;
    const tx = Math.min(Math.max(x - el.offsetWidth / 2, 12), w - el.offsetWidth - 12);
    el.style.transform = `translate(${tx.toFixed(1)}px, ${(y - el.offsetHeight).toFixed(1)}px)`;
    el.classList.add("on");
  };

  let time = 0, last = performance.now(), pCur = readP().p;
  function frame(now) {
    requestAnimationFrame(frame);
    const dt = Math.min(0.05, (now - last) / 1000); last = now;
    const { p, visible } = readP();
    if (!visible || document.hidden) return;
    time += reduceMotion ? 0 : dt;
    pCur = snap ? p : pCur + (p - pCur) * (1 - Math.pow(0.004, dt));
    const P = Math.abs(pCur - p) < 0.0005 ? p : pCur;
    setStage(P);
    const c = layout(P, time);

    /* camera: follow the container, open on the factory */
    const focus = c.clone();
    const onFactory = 1 - seg(P, 0.04, 0.14);
    focus.lerp(new THREE.Vector3(FACTORY.x + 3, 1.5, FACTORY.z + 2), onFactory * 0.35);
    if (first) { target.copy(focus); }
    const follow = snap ? 1 : 1 - Math.pow(0.02, dt);
    target.lerp(focus, first ? 1 : follow);
    camPos.copy(target).add(kf(P).multiplyScalar(small() ? 1.3 : 1));
    if (first) camera.position.copy(camPos); else camera.position.lerp(camPos, follow);
    camera.lookAt(target);
    first = false;

    sun.position.copy(target).add(new THREE.Vector3(-12, 22, 14));
    sun.target.position.copy(target);

    renderer.render(scene, camera);

    placeTag(tags.factory, new THREE.Vector3(FACTORY.x, 6.2, FACTORY.z), P < 0.16);
    placeTag(tags.container, c.clone().add(new THREE.Vector3(0, 1.1, 0)), P > 0.015 && P < 0.97 && !(P > 0.28 && P < 0.37) && !(P > 0.64 && P < 0.72));
    placeTag(tags.sea, new THREE.Vector3(c.x + 6, 0.4, SHIP_Z + 4), P > 0.42 && P < 0.6);
    placeTag(bill, new THREE.Vector3(GATE_X, 6.4, 0), P > 0.745 && P < 0.9);
    placeTag(tags.warehouse, new THREE.Vector3(WAREHOUSE.x, 6.4, WAREHOUSE.z), P > 0.9);
  }
  requestAnimationFrame(frame);
  document.addEventListener("ct:workspace", () => renderer.dispose(), { once: true });

  /* ================= builders ================= */
  function buildTruck() {
    const g = new THREE.Group();
    box(6, 0.32, 1.5, M.clayDark, 0, 0.78, 0, g);
    box(1.7, 1.75, 1.7, M.clay, 3.55, 1.5, 0, g);
    box(0.06, 0.65, 1.4, M.glass, 4.41, 1.85, 0, g).castShadow = false;
    box(0.5, 0.3, 1.6, M.clayMid, 2.45, 0.95, 0, g);
    [-2.2, -1.2, 2.0, 3.6].forEach((x) => [-0.72, 0.72].forEach((z) => {
      const w = add(new THREE.CylinderGeometry(0.42, 0.42, 0.32, 20), M.ink, x, 0.42, z, g);
      w.rotation.x = Math.PI / 2;
    }));
    scene.add(g);
    return g;
  }

  function buildShip() {
    const g = new THREE.Group();
    const s = new THREE.Shape();
    s.moveTo(-7.2, 0); s.lineTo(6.2, 0); s.quadraticCurveTo(7.6, 0.6, 8.2, 2.0); s.lineTo(-7.4, 2.0); s.lineTo(-7.2, 0);
    const hull = new THREE.ExtrudeGeometry(s, { depth: 4, bevelEnabled: false });
    hull.translate(0, 0, -2);
    add(hull, M.clay, 0, 0, 0, g);
    box(15.2, 0.35, 4.04, M.hullStripe, 0.2, 0.18, 0, g);
    box(2, 3, 3.6, M.clay, -5.8, 3.5, 0, g);
    box(2.1, 0.5, 3.8, M.glass, -5.8, 4.4, 0, g);
    add(new THREE.CylinderGeometry(0.35, 0.4, 1.6, 16), M.clayDark, -6.4, 5.6, 0, g);
    let n = 0;
    [-1.5, 3.5].forEach((bx) => [-1.35, 0, 1.35].forEach((bz) => [0, 1].forEach((tier) => {
      if (bx === 3.5 && bz === 0 && tier === 1) return; // our container's slot
      const m = new THREE.MeshStandardMaterial({ color: otherColors[n++ % otherColors.length], map: ribs, roughness: 0.7 });
      box(4.8, 1.3, 1.3, m, bx, 2.0 + 0.65 + tier * 1.3, bz, g);
    })));
    scene.add(g);
    return g;
  }

  function buildCrane(x) {
    const g = new THREE.Group(); g.position.x = x;
    const legM = M.clayMid;
    [[-1.7, -1.6], [1.7, -1.6], [-1.7, 1.2], [1.7, 1.2]].forEach(([dx, dz]) => box(0.35, 8.6, 0.35, legM, dx, 4.3, dz, g));
    box(3.8, 0.35, 0.35, legM, 0, 3.2, -1.6, g); box(3.8, 0.35, 0.35, legM, 0, 3.2, 1.2, g);
    // portal frame on top of the legs: cross beams and side girders that carry the boom
    box(3.9, 0.5, 0.5, legM, 0, 8.55, -1.6, g); box(3.9, 0.5, 0.5, legM, 0, 8.55, 1.2, g);
    box(0.4, 0.5, 3.3, legM, -1.7, 8.55, -0.2, g); box(0.4, 0.5, 3.3, legM, 1.7, 8.55, -0.2, g);
    box(0.5, 0.55, 15, M.clay, -0.9, 9.05, 2.5, g); box(0.5, 0.55, 15, M.clay, 0.9, 9.05, 2.5, g);
    box(2.3, 1.6, 2, M.clay, 0, 10.1, -3.2, g);
    const trolley = box(2.3, 0.45, 1.2, M.clayDark, 0, 8.55, 0, g);
    const cable = box(0.06, 1, 0.06, M.ink, 0, 7, 0, g); cable.castShadow = false;
    const spreader = box(4.9, 0.16, 1.4, M.ink, 0, 2.4, 0, g);
    scene.add(g);
    return {
      set(c, restZ) {
        const z = c ? c.z : restZ ?? 0;
        const hookY = c ? c.y + 0.73 : 7.2;
        trolley.position.z = z; spreader.position.set(0, hookY, z);
        const len = 8.33 - hookY; cable.scale.y = Math.max(0.05, len); cable.position.set(0, hookY + len / 2, z);
      },
    };
  }

  function buildSmoke(origin) {
    const count = small() ? 60 : 110;
    const geo = new THREE.BufferGeometry();
    const seeds = new Float32Array(count * 3);
    for (let i = 0; i < seeds.length; i++) seeds[i] = Math.random();
    geo.setAttribute("position", new THREE.BufferAttribute(new Float32Array(count * 3), 3));
    geo.setAttribute("aSeed", new THREE.BufferAttribute(seeds, 3));
    const uniforms = { uTime: { value: 0 }, uScale: { value: Math.min(devicePixelRatio || 1, 2) } };
    const mat = new THREE.ShaderMaterial({
      uniforms, transparent: true, depthWrite: false,
      vertexShader: `uniform float uTime; uniform float uScale; attribute vec3 aSeed; varying float vA; varying float vT;
        void main() {
          float life = fract(aSeed.x + uTime * 0.12);
          vec3 p = vec3(${origin.x.toFixed(2)}, ${origin.y.toFixed(2)}, ${origin.z.toFixed(2)});
          p += vec3(life * 3.0 + (aSeed.y - 0.5) * life * 2.0, life * 6.0, (aSeed.z - 0.5) * life * 2.0);
          vec4 mv = modelViewMatrix * vec4(p, 1.0);
          gl_Position = projectionMatrix * mv;
          gl_PointSize = uScale * (120.0 + life * 420.0) / -mv.z;
          vA = (1.0 - life) * smoothstep(0.0, 0.08, life) * 0.55; vT = life;
        }`,
      fragmentShader: `varying float vA; varying float vT;
        void main() { float d = length(gl_PointCoord - 0.5); float a = smoothstep(0.5, 0.1, d) * vA;
          vec3 c = mix(vec3(1.0, 0.55, 0.3), vec3(0.62, 0.66, 0.69), smoothstep(0.0, 0.35, vT));
          gl_FragColor = vec4(c, a); }`,
    });
    const points = new THREE.Points(geo, mat);
    points.frustumCulled = false;
    return { points, uniforms };
  }

  function ribTexture() {
    const c = document.createElement("canvas"); c.width = 256; c.height = 64;
    const x = c.getContext("2d");
    x.fillStyle = "#ffffff"; x.fillRect(0, 0, 256, 64);
    for (let i = 0; i < 256; i += 8) { x.fillStyle = "rgba(0,0,0,0.10)"; x.fillRect(i, 0, 3, 64); }
    x.fillStyle = "rgba(0,0,0,0.12)"; x.fillRect(0, 0, 256, 3); x.fillRect(0, 61, 256, 3);
    const t = new THREE.CanvasTexture(c);
    t.colorSpace = THREE.SRGBColorSpace;
    t.anisotropy = 4;
    return t;
  }
}
