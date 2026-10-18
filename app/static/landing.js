// CarbonTrail landing — a product-page scroll story.
// The hero renders a machined steel bolt, the product our demo factory exports. The journey (journey.js)
// and "how it works" (paper.js) have their own canvases; this file also runs the sections below them.
import * as THREE from "/vendor/three.module.min.js";
import { RoomEnvironment } from "/vendor/RoomEnvironment.js";

const $ = (s, el = document) => el.querySelector(s);
const $$ = (s, el = document) => [...el.querySelectorAll(s)];
const reduceMotion = matchMedia("(prefers-reduced-motion: reduce)").matches;
const clamp = (v, a = 0, b = 1) => Math.min(b, Math.max(a, v));
const smooth = (a, b, v) => { const t = clamp((v - a) / (b - a)); return t * t * (3 - 2 * t); };
const mix = (a, b, t) => a + (b - a) * t;

const stage = $("#stage"), hero = $("#hero");

/* ------------------------------------------------------------------ */
/* Scroll state                                                         */
/* ------------------------------------------------------------------ */
const S = { heroK: 0, shift: 0, onStage: true };
function readScroll() {
  const hr = hero.getBoundingClientRect();
  S.heroK = clamp(-hr.top / Math.max(1, hr.height));     // 0 → 1 while the hero scrolls away
  S.shift = Math.min(0, hr.top);                          // the canvas is fixed: the bolt rides up with the hero
  S.onStage = hr.bottom > 0;                              // the journey covers everything after the hero
  document.body.classList.toggle("past-stage", !S.onStage);
}
addEventListener("scroll", readScroll, { passive: true });
addEventListener("resize", readScroll);
readScroll();

/* ------------------------------------------------------------------ */
/* Sections after the stage                                             */
/* ------------------------------------------------------------------ */
const io = new IntersectionObserver((entries) => entries.forEach((e) => {
  if (e.isIntersecting) { e.target.classList.add("in"); io.unobserve(e.target); }
}), { rootMargin: "0px 0px -10% 0px" });
$$(".reveal").forEach((el) => io.observe(el));

/* proof counter: 6.13 (EU default) → 3.25 (own data + the mill's figure) */
const proofEl = $("#proof-count");
if (!reduceMotion) proofEl.textContent = "6.13";
new IntersectionObserver((entries, obs) => entries.forEach((e) => {
  if (!e.isIntersecting) return;
  obs.disconnect();
  if (reduceMotion) { proofEl.textContent = "3.25"; return; }
  const t0 = performance.now() + 500;
  const step = (now) => {
    const k = clamp((now - t0) / 1800);
    proofEl.textContent = mix(6.13, 3.25, 1 - Math.pow(1 - k, 3)).toFixed(2);
    if (k < 1) requestAnimationFrame(step);
  };
  requestAnimationFrame(step);
}), { threshold: 0.6 }).observe(proofEl);

/* gallery: dots, arrows */
const gal = $("#gallery"), dots = $("#dots");
if (gal) {
  const cards = $$(".g-card", gal);
  dots.innerHTML = cards.map(() => "<span></span>").join("");
  const dotEls = $$("span", dots);
  const update = () => {
    const w = cards[0].offsetWidth + 20;
    const i = Math.round(gal.scrollLeft / w);
    dotEls.forEach((d, j) => d.classList.toggle("on", j === Math.min(i, cards.length - 1)));
  };
  gal.addEventListener("scroll", update, { passive: true });
  update();
  const go = (dir) => gal.scrollBy({ left: dir * (cards[0].offsetWidth + 20), behavior: reduceMotion ? "auto" : "smooth" });
  $("#gal-prev").onclick = () => go(-1);
  $("#gal-next").onclick = () => go(1);
}

/* tilt on the evidence mock-up */
const tiltWrap = $("#tilt");
if (tiltWrap && !reduceMotion && matchMedia("(hover: hover)").matches) {
  const tilt = $(".tilt", tiltWrap);
  tiltWrap.addEventListener("pointermove", (e) => {
    const r = tiltWrap.getBoundingClientRect();
    const x = (e.clientX - r.left) / r.width - 0.5, y = (e.clientY - r.top) / r.height - 0.5;
    tilt.style.setProperty("--ry", `${(-14 + x * 14).toFixed(2)}deg`);
    tilt.style.setProperty("--rx", `${(6 - y * 10).toFixed(2)}deg`);
  });
  tiltWrap.addEventListener("pointerleave", () => { tilt.style.removeProperty("--ry"); tilt.style.removeProperty("--rx"); });
}

/* ------------------------------------------------------------------ */
/* WebGL                                                                */
/* ------------------------------------------------------------------ */
let renderer = null;
try {
  renderer = new THREE.WebGLRenderer({ canvas: $("#scene"), antialias: true, alpha: true, powerPreference: "high-performance" });
} catch (err) {
  document.documentElement.classList.add("no-webgl");
}
if (renderer) startScene(renderer);

function startScene(renderer) {
  const small = () => innerWidth < 820;
  renderer.setPixelRatio(Math.min(devicePixelRatio || 1, small() ? 1.75 : 2));
  renderer.setClearColor(0x000000, 0);
  renderer.toneMapping = THREE.ACESFilmicToneMapping;
  renderer.toneMappingExposure = 1.05;
  renderer.outputColorSpace = THREE.SRGBColorSpace;

  const scene = new THREE.Scene();
  const pmrem = new THREE.PMREMGenerator(renderer);
  scene.environment = pmrem.fromScene(new RoomEnvironment(), 0.04).texture;

  const camera = new THREE.PerspectiveCamera(32, innerWidth / innerHeight, 0.1, 100);
  camera.position.set(0, 0, 10);

  const key = new THREE.DirectionalLight(0xffffff, 1.6); key.position.set(3, 6, 8);
  const rimL = new THREE.DirectionalLight(0xbfd6ff, 2.4); rimL.position.set(-6, 2, -5);
  const rimR = new THREE.DirectionalLight(0xffb38a, 1.8); rimR.position.set(6, -1, -5);
  scene.add(key, rimL, rimR, new THREE.HemisphereLight(0xdfe6ea, 0x050505, 0.35));

  /* anchor (scroll layout) > tilt > spin > parts */
  const anchor = new THREE.Group(), tilt = new THREE.Group(), spin = new THREE.Group();
  anchor.add(tilt); tilt.add(spin); scene.add(anchor);
  const parts = buildBolt();
  parts.forEach((p) => tilt.add(p.group));

  let px = 0, py = 0, tx = 0, ty = 0;
  addEventListener("pointermove", (e) => { tx = e.clientX / innerWidth - 0.5; ty = e.clientY / innerHeight - 0.5; }, { passive: true });

  const resize = () => {
    renderer.setSize(innerWidth, innerHeight, false);
    camera.aspect = innerWidth / innerHeight;
    camera.updateProjectionMatrix();
  };
  addEventListener("resize", resize);
  resize();

  let running = true, last = performance.now(), time = 0;
  const cur = { heroK: S.heroK };

  function frame(now) {
    if (!running) return;
    requestAnimationFrame(frame);
    const dt = Math.min(0.05, (now - last) / 1000); last = now;
    if (!S.onStage || document.hidden) return;
    time += reduceMotion ? 0 : dt;
    const f = reduceMotion ? 1 : 1 - Math.pow(0.002, dt);
    cur.heroK += (S.heroK - cur.heroK) * f;
    const h = cur.heroK, mobile = small();

    /* a product shot lying under the hero text; it turns a little as the page scrolls */
    anchor.position.set(0, mobile ? -1.75 : -1.45, 0);
    camera.setViewOffset(innerWidth, innerHeight, 0, -S.shift, innerWidth, innerHeight);
    anchor.scale.setScalar(mobile ? 0.55 : 0.82);
    camera.position.z = 10;
    px += (tx - px) * 0.05; py += (ty - py) * 0.05;
    tilt.rotation.set(0.25 + py * 0.12, px * 0.25, -1.22);
    const spinY = time * 0.35 + h * Math.PI * 0.8;
    parts.forEach((p) => { p.group.rotation.y = spinY; });

    renderer.render(scene, camera);
  }
  requestAnimationFrame(frame);

  document.addEventListener("ct:workspace", () => { running = false; renderer.dispose(); }, { once: true });
}

/* ------------------------------------------------------------------ */
/* The bolt, in five parts                                              */
/* ------------------------------------------------------------------ */
function buildBolt() {
  const steel = new THREE.MeshStandardMaterial({ color: 0xbcc4c9, metalness: 1, roughness: 0.24, envMapIntensity: 1.25 });
  const darker = new THREE.MeshStandardMaterial({ color: 0x9aa3a8, metalness: 1, roughness: 0.32, envMapIntensity: 1.1 });
  const part = (...meshes) => { const g = new THREE.Group(); meshes.forEach((m) => g.add(m)); return { group: g }; };

  /* head: hexagon with a chamfer */
  const hex = new THREE.Shape();
  for (let i = 0; i < 6; i++) {
    const a = (i / 6) * Math.PI * 2 + Math.PI / 6;
    i ? hex.lineTo(Math.cos(a), Math.sin(a)) : hex.moveTo(Math.cos(a), Math.sin(a));
  }
  hex.closePath();
  const headGeo = new THREE.ExtrudeGeometry(hex, { depth: 0.5, bevelEnabled: true, bevelThickness: 0.07, bevelSize: 0.07, bevelSegments: 3, curveSegments: 1 });
  headGeo.rotateX(-Math.PI / 2);
  headGeo.translate(0, 1.62 - 0.2, 0);
  const head = part(new THREE.Mesh(headGeo, steel));

  const washerMesh = new THREE.Mesh(new THREE.CylinderGeometry(0.9, 0.92, 0.09, 96), darker);
  washerMesh.position.y = 1.53 - 0.2;
  const washer = part(washerMesh);

  const shaftMesh = new THREE.Mesh(new THREE.CylinderGeometry(0.42, 0.42, 3.0, 96), darker);
  shaftMesh.position.y = -0.2;
  const shaft = part(shaftMesh);

  class Helix extends THREE.Curve {
    getPoint(t, target = new THREE.Vector3()) {
      const a = t * 15 * Math.PI * 2;
      return target.set(Math.cos(a) * 0.455, 1.18 - t * 2.62 - 0.2, Math.sin(a) * 0.455);
    }
  }
  const thread = part(new THREE.Mesh(new THREE.TubeGeometry(new Helix(), 1500, 0.058, 8, false), steel));

  const tipMesh = new THREE.Mesh(new THREE.CylinderGeometry(0.42, 0.31, 0.16, 96), steel);
  tipMesh.position.y = -1.58 - 0.2;
  const tip = part(tipMesh);

    return [head, washer, shaft, thread, tip];
}
