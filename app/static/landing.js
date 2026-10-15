// CarbonTrail landing: scroll story UI + a WebGL scene (a forged steel bolt, rising carbon,
// the factory's paperwork being read, and a "verified" seal). Three.js is vendored under /vendor.
import * as THREE from "/vendor/three.module.min.js";
import { RoomEnvironment } from "/vendor/RoomEnvironment.js";

const $ = (s, el = document) => el.querySelector(s);
const $$ = (s, el = document) => [...el.querySelectorAll(s)];
const reduceMotion = matchMedia("(prefers-reduced-motion: reduce)").matches;
const story = $("#story");
const chapters = $$(".chapter");
const LAST = chapters.length - 1;

const clamp = (v, a = 0, b = 1) => Math.min(b, Math.max(a, v));
const smooth = (a, b, v) => { const t = clamp((v - a) / (b - a)); return t * t * (3 - 2 * t); };
const mix = (a, b, t) => a + (b - a) * t;
/* piecewise-linear keyframes over chapter position c (0..LAST) */
const key = (arr, c) => { const i = clamp(Math.floor(c), 0, arr.length - 2); return mix(arr[i], arr[i + 1], clamp(c - i)); };

/* ------------------------------------------------------------------ */
/* Story UI (works without WebGL)                                      */
/* ------------------------------------------------------------------ */
let target = 0;          // scroll position in chapters
let storyVisible = true; // false once the content sheet covers the scene
const proofEl = $("#proof-count");
const pipeSteps = $$(".pipeline li");
let proofShown = false;

function readScroll() {
  const r = story.getBoundingClientRect();
  const total = Math.max(1, story.offsetHeight - innerHeight);
  target = clamp(-r.top / total) * LAST;
  storyVisible = r.bottom > -innerHeight * 0.1;
  const mid = innerHeight * 0.5;
  for (const ch of chapters) {
    const cr = ch.getBoundingClientRect();
    ch.classList.toggle("on", cr.top <= mid + innerHeight * 0.08 && cr.bottom >= mid - innerHeight * 0.08);
  }
  const lit = Math.round(clamp((target - 2.45) / 0.55) * 4);
  pipeSteps.forEach((li, i) => li.classList.toggle("lit", i < lit));
  const on = chapters[LAST].classList.contains("on");
  if (on && !proofShown) { proofShown = true; countTo(proofEl, 5.36, 3.25, 1600); }
  if (!on && proofShown && target < LAST - 0.8) { proofShown = false; proofEl.textContent = "5.36"; }
}

function countTo(el, from, to, ms) {
  if (reduceMotion) { el.textContent = to.toFixed(2); return; }
  const t0 = performance.now();
  const step = (t) => {
    const k = clamp((t - t0) / ms);
    const e = 1 - Math.pow(1 - k, 3);
    el.textContent = mix(from, to, e).toFixed(2);
    if (k < 1 && proofShown) requestAnimationFrame(step);
  };
  setTimeout(() => requestAnimationFrame(step), 450);
}

addEventListener("scroll", readScroll, { passive: true });
addEventListener("resize", readScroll);
readScroll();

/* reveal on scroll for the sections after the story */
const io = new IntersectionObserver((entries) => entries.forEach((e) => {
  if (e.isIntersecting) { e.target.classList.add("in"); io.unobserve(e.target); }
}), { rootMargin: "0px 0px -12% 0px" });
$$(".reveal").forEach((el) => io.observe(el));

/* 3D tilt on the evidence mock-up */
const tiltWrap = $("#tilt");
if (tiltWrap && !reduceMotion && matchMedia("(hover: hover)").matches) {
  const tilt = $(".tilt", tiltWrap);
  tiltWrap.addEventListener("pointermove", (e) => {
    const r = tiltWrap.getBoundingClientRect();
    const x = (e.clientX - r.left) / r.width - 0.5, y = (e.clientY - r.top) / r.height - 0.5;
    tilt.style.setProperty("--ry", `${(-16 + x * 14).toFixed(2)}deg`);
    tilt.style.setProperty("--rx", `${(8 - y * 10).toFixed(2)}deg`);
  });
  tiltWrap.addEventListener("pointerleave", () => { tilt.style.removeProperty("--ry"); tilt.style.removeProperty("--rx"); });
}

/* ------------------------------------------------------------------ */
/* WebGL scene                                                         */
/* ------------------------------------------------------------------ */
const canvas = $("#scene");
let renderer = null;
try {
  renderer = new THREE.WebGLRenderer({ canvas, antialias: true, alpha: true, powerPreference: "high-performance" });
} catch (err) {
  document.documentElement.classList.add("no-webgl");
}

if (renderer) startScene(renderer);

function startScene(renderer) {
  const small = () => innerWidth < 860;
  renderer.setPixelRatio(Math.min(devicePixelRatio || 1, small() ? 1.5 : 2));
  renderer.setClearColor(0x000000, 0);
  renderer.toneMapping = THREE.ACESFilmicToneMapping;
  renderer.toneMappingExposure = 1.0;
  renderer.outputColorSpace = THREE.SRGBColorSpace;

  const scene = new THREE.Scene();
  const pmrem = new THREE.PMREMGenerator(renderer);
  scene.environment = pmrem.fromScene(new RoomEnvironment(), 0.04).texture;

  const camera = new THREE.PerspectiveCamera(32, innerWidth / innerHeight, 0.1, 100);
  camera.position.set(0, 0, 10);

  /* lights: a white key, a molten rim (carbon) and a green rim (proof) */
  const keyLight = new THREE.DirectionalLight(0xffffff, 1.5);
  keyLight.position.set(4, 6, 7);
  const rimHeat = new THREE.DirectionalLight(0xff5a1f, 0);
  rimHeat.position.set(-5, -2, -4);
  const rimProof = new THREE.DirectionalLight(0x3be19a, 0);
  rimProof.position.set(5, 3, -4);
  scene.add(keyLight, rimHeat, rimProof, new THREE.HemisphereLight(0x9fb3b8, 0x0b1012, 0.35));

  /* anchor (scroll-driven position) > parallax (pointer) > tilt > spin > bolt */
  const anchor = new THREE.Group();
  const parallax = new THREE.Group();
  const tilt = new THREE.Group();
  const spin = new THREE.Group();
  anchor.add(parallax); parallax.add(tilt); tilt.add(spin);
  scene.add(anchor);

  const heat = { value: 0.3 };
  spin.add(buildBolt(heat));

  /* verified seal: a thin ring that closes around the bolt at the end */
  const sealMat = new THREE.MeshBasicMaterial({ color: 0x3be19a, transparent: true, opacity: 0, toneMapped: false });
  const seal = new THREE.Mesh(new THREE.TorusGeometry(1.75, 0.014, 8, 220), sealMat);
  seal.rotation.x = Math.PI / 2.7;
  const seal2 = new THREE.Mesh(new THREE.TorusGeometry(2.05, 0.006, 6, 220), sealMat.clone());
  seal2.rotation.x = Math.PI / 3.1; seal2.rotation.y = 0.25;
  tilt.add(seal, seal2);

  /* rising carbon */
  const carbon = buildCarbon(small() ? 520 : 1100);
  anchor.add(carbon.points);

  /* the factory's paperwork */
  const papers = buildPapers(["02_lpg_invoice_apr.jpg", "07_production_register_q1.jpg", "05_diesel_dg_statement.jpg", "09_export_invoice_pl.jpg", "03_lpg_invoice_may.jpg"]);
  papers.forEach((p) => scene.add(p.mesh));

  /* pointer parallax */
  let px = 0, py = 0, tx = 0, ty = 0;
  addEventListener("pointermove", (e) => { tx = e.clientX / innerWidth - 0.5; ty = e.clientY / innerHeight - 0.5; }, { passive: true });

  function resize() {
    renderer.setSize(innerWidth, innerHeight, false);
    camera.aspect = innerWidth / innerHeight;
    camera.updateProjectionMatrix();
  }
  addEventListener("resize", resize);
  resize();

  let cur = target, running = true, last = performance.now(), time = 0;
  const v = new THREE.Vector3(), tmp = new THREE.Vector3();

  function frame(now) {
    if (!running) return;
    requestAnimationFrame(frame);
    const dt = Math.min(0.05, (now - last) / 1000); last = now;
    if (!storyVisible || document.hidden) return;
    time += reduceMotion ? 0 : dt;
    cur = reduceMotion ? target : cur + (target - cur) * (1 - Math.pow(0.0015, dt));
    const c = cur;
    const mobile = small();

    /* layout per chapter */
    const ax = mobile ? 0 : key([2.55, 2.75, 2.75, 2.75, 2.6], c);
    const ay = mobile ? key([1.68, 1.72, 1.72, 1.72, 1.68], c) : key([-0.1, -0.05, -0.1, -0.1, -0.05], c);
    const az = key([-0.4, 0.3, -0.6, -0.6, -0.1], c);
    anchor.position.set(ax, ay, az);
    anchor.scale.setScalar(mobile ? 0.46 : 0.82);
    camera.position.z = key([10, 9.3, 10.4, 10.4, 9.6], c);

    px += (tx - px) * 0.05; py += (ty - py) * 0.05;
    parallax.rotation.set(py * 0.25, px * 0.45, 0);
    tilt.rotation.set(key([0.28, 0.18, 0.35, 0.35, 0.22], c), 0, key([-0.55, -0.95, -0.35, -0.35, -0.5], c));
    spin.rotation.y = time * 0.32 + c * 1.4;

    /* heat (carbon) → cool proof */
    heat.value = key([0.1, 0.85, 0.55, 0.25, 0.0], c);
    rimHeat.intensity = key([0.5, 3.0, 1.8, 0.8, 0.0], c);
    rimProof.intensity = key([0.6, 0.0, 0.0, 0.8, 3.4], c);
    keyLight.intensity = key([1.5, 1.2, 1.4, 1.5, 1.7], c);

    carbon.uniforms.uTime.value = time;
    carbon.uniforms.uOpacity.value = key([0.45, 1.0, 0.65, 0.3, 0.0], c);

    const sealK = smooth(3.45, 4.0, c);
    sealMat.opacity = sealK * 0.9;
    seal.scale.setScalar(mix(1.5, 1, sealK));
    seal2.material.opacity = sealK * 0.45;
    seal.rotation.z = time * 0.4; seal2.rotation.z = -time * 0.25;

    /* papers: arrive → orbit and get read → absorbed into the bolt */
    const arrive = smooth(1.25, 2.15, c);
    const absorb = smooth(3.3, 3.95, c);
    const scan = smooth(2.45, 3.2, c);
    anchor.updateMatrixWorld();
    const centre = v.setFromMatrixPosition(anchor.matrixWorld);
    const ring = mobile ? 1.7 : 2.15;
    papers.forEach((p, i) => {
      const n = papers.length;
      const th = (i / n) * Math.PI * 2 + time * 0.12 + c * 0.6;
      const orbit = tmp.set(Math.cos(th) * ring, Math.sin(th * 2) * 0.35 + (i - 2) * 0.32, Math.sin(th) * 1.3).add(centre);
      const pos = p.start.clone().lerp(orbit, arrive).lerp(centre, absorb);
      p.mesh.position.copy(pos);
      p.mesh.lookAt(camera.position);
      p.mesh.rotateZ(p.roll * (1 - arrive * 0.6));
      const s = (mobile ? 0.7 : 1) * mix(1, 0.04, absorb);
      p.mesh.scale.set(p.w * s, p.h * s, 1);
      p.uniforms.uOpacity.value = arrive * (1 - smooth(0.7, 1, absorb)) * (p.ready ? 1 : 0);
      p.uniforms.uScan.value = clamp(scan * 1.7 - i * 0.16);
      p.mesh.visible = p.uniforms.uOpacity.value > 0.002;
    });

    renderer.render(scene, camera);
  }
  requestAnimationFrame(frame);

  /* stop everything once the user opens the workspace */
  document.addEventListener("ct:workspace", () => {
    running = false;
    renderer.dispose();
  }, { once: true });
}

/* ------------------------------------------------------------------ */
/* Geometry                                                            */
/* ------------------------------------------------------------------ */
function buildBolt(heat) {
  const g = new THREE.Group();
  const steel = new THREE.MeshStandardMaterial({ color: 0xa3adb1, metalness: 1, roughness: 0.27, envMapIntensity: 1.15 });
  steel.onBeforeCompile = (sh) => {
    sh.uniforms.uHeat = heat;
    sh.uniforms.uHeatColor = { value: new THREE.Color(0xff5a1f) };
    sh.fragmentShader = sh.fragmentShader
      .replace("#include <common>", "#include <common>\nuniform float uHeat;\nuniform vec3 uHeatColor;")
      .replace("#include <emissivemap_fragment>", `#include <emissivemap_fragment>
        float ctFres = pow(1.0 - abs(dot(normalize(normal), normalize(vViewPosition))), 2.4);
        totalEmissiveRadiance += uHeatColor * uHeat * (0.12 + 1.9 * ctFres);`);
  };

  /* hex head with a chamfer */
  const hex = new THREE.Shape();
  for (let i = 0; i < 6; i++) {
    const a = (i / 6) * Math.PI * 2 + Math.PI / 6;
    const x = Math.cos(a) * 1.0, y = Math.sin(a) * 1.0;
    i ? hex.lineTo(x, y) : hex.moveTo(x, y);
  }
  hex.closePath();
  const headGeo = new THREE.ExtrudeGeometry(hex, { depth: 0.5, bevelEnabled: true, bevelThickness: 0.07, bevelSize: 0.07, bevelSegments: 3, curveSegments: 1 });
  headGeo.rotateX(-Math.PI / 2);
  headGeo.translate(0, 1.62, 0);
  g.add(new THREE.Mesh(headGeo, steel));

  const washer = new THREE.Mesh(new THREE.CylinderGeometry(0.9, 0.92, 0.09, 96), steel);
  washer.position.y = 1.53;
  g.add(washer);

  const shaft = new THREE.Mesh(new THREE.CylinderGeometry(0.44, 0.44, 3.0, 96), steel);
  g.add(shaft);

  class Helix extends THREE.Curve {
    getPoint(t, target = new THREE.Vector3()) {
      const turns = 15, a = t * turns * Math.PI * 2;
      return target.set(Math.cos(a) * 0.455, 1.18 - t * 2.62, Math.sin(a) * 0.455);
    }
  }
  const thread = new THREE.Mesh(new THREE.TubeGeometry(new Helix(), 1500, 0.058, 8, false), steel);
  g.add(thread);

  const tip = new THREE.Mesh(new THREE.CylinderGeometry(0.44, 0.33, 0.16, 96), steel);
  tip.position.y = -1.58;
  g.add(tip);

  g.position.y = -0.2;
  return g;
}

function buildCarbon(count) {
  const geo = new THREE.BufferGeometry();
  const seeds = new Float32Array(count * 4);
  for (let i = 0; i < seeds.length; i++) seeds[i] = Math.random();
  geo.setAttribute("position", new THREE.BufferAttribute(new Float32Array(count * 3), 3));
  geo.setAttribute("aSeed", new THREE.BufferAttribute(seeds, 4));
  const uniforms = { uTime: { value: 0 }, uOpacity: { value: 0.4 }, uSize: { value: 44 * Math.min(devicePixelRatio || 1, 2) } };
  const mat = new THREE.ShaderMaterial({
    uniforms, transparent: true, depthWrite: false, blending: THREE.AdditiveBlending,
    vertexShader: `
      uniform float uTime; uniform float uSize; uniform float uOpacity;
      attribute vec4 aSeed; varying float vA; varying float vHot;
      void main() {
        float life = fract(aSeed.w + uTime * (0.045 + aSeed.z * 0.05));
        float ang = aSeed.x * 6.2831853 + uTime * 0.25 * (aSeed.z - 0.5);
        float r = 0.55 + aSeed.y * 1.7 + life * 0.9;
        vec3 p = vec3(cos(ang) * r, -2.3 + life * 5.2, sin(ang) * r);
        vec4 mv = modelViewMatrix * vec4(p, 1.0);
        gl_Position = projectionMatrix * mv;
        gl_PointSize = uSize * (0.35 + aSeed.z) / -mv.z;
        vA = uOpacity * smoothstep(0.0, 0.12, life) * (1.0 - smoothstep(0.55, 1.0, life));
        vHot = 1.0 - life;
      }`,
    fragmentShader: `
      varying float vA; varying float vHot;
      void main() {
        float d = length(gl_PointCoord - 0.5);
        float a = smoothstep(0.5, 0.0, d) * vA;
        vec3 hot = vec3(1.0, 0.42, 0.17), smoke = vec3(0.55, 0.6, 0.62);
        gl_FragColor = vec4(mix(smoke, hot, vHot * vHot) * a, a);
      }`,
  });
  return { points: new THREE.Points(geo, mat), uniforms };
}

function buildPapers(files) {
  const loader = new THREE.TextureLoader();
  const geo = new THREE.PlaneGeometry(1, 1);
  const starts = [[8.5, 4.2, -3], [8.5, -4.2, -2], [3.5, -6.5, 1], [9.5, 0.8, -4], [4.5, 6.8, -5]];
  return files.map((f, i) => {
    const uniforms = { map: { value: null }, uOpacity: { value: 0 }, uScan: { value: 0 }, uTint: { value: new THREE.Color(0x3be19a) } };
    const mat = new THREE.ShaderMaterial({
      uniforms, transparent: true, depthWrite: false, side: THREE.DoubleSide, toneMapped: false,
      vertexShader: `varying vec2 vUv; void main() { vUv = uv; gl_Position = projectionMatrix * modelViewMatrix * vec4(position, 1.0); }`,
      fragmentShader: `
        uniform sampler2D map; uniform float uOpacity; uniform float uScan; uniform vec3 uTint; varying vec2 vUv;
        void main() {
          vec3 col = texture2D(map, vUv).rgb * 0.92;
          float line = 1.0 - uScan;
          float read = step(line, vUv.y) * step(0.001, uScan);
          col = mix(col, col * 0.6 + uTint * 0.22, read * 0.6);
          float glow = exp(-pow((vUv.y - line) * 55.0, 2.0)) * step(0.001, uScan) * step(uScan, 0.999);
          col += uTint * glow * 1.4;
          float edge = max(max(step(vUv.x, 0.012), step(0.988, vUv.x)), max(step(vUv.y, 0.01), step(0.99, vUv.y)));
          col = mix(col, uTint, edge * step(0.999, uScan));
          gl_FragColor = vec4(col, uOpacity);
          #include <colorspace_fragment>
        }`,
    });
    const mesh = new THREE.Mesh(geo, mat);
    mesh.visible = false;
    const p = { mesh, uniforms, start: new THREE.Vector3(...starts[i % starts.length]), roll: (i % 2 ? 1 : -1) * (0.12 + 0.05 * i), w: 1.5, h: 1.9, ready: false };
    loader.load(`/samples/${f}`, (tex) => {
      tex.colorSpace = THREE.SRGBColorSpace;
      tex.anisotropy = 4;
      uniforms.map.value = tex;
      const img = tex.image;
      const aspect = img.height / img.width;
      p.w = aspect > 1 ? 1.3 : 1.75;
      p.h = p.w * aspect;
      p.ready = true;
    });
    return p;
  });
}
