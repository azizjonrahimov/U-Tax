// D42 "Immersive firm" landing scene (original work for U-Tax; three.js r169).
// Copied from docs/designs/immersive/immersive.js; types added, the logic unchanged. Additions
// (runbook P1.6, P1b, P2): a phone profile, the page's next/font family names, card faces that
// redraw with live captions, a card picker for clicks, and a pause when the hero is off-screen.
// One sticky WebGL canvas driven by scroll progress p (0..1) through five acts:
//  A0 0.00–0.18  the glass U emblem inside an iridescent ring, lime/gold dust drifting
//  A1 0.18–0.34  the emblem bursts into a particle storm (department colors)
//  A2 0.34–0.80  the camera flies along an arc of floating iridescent glass cards (live proof)
//  A3 0.80–1.00  the particles re-gather into the emblem; CTA
// createImmersive(canvas, { cards }) → { setProgress(p), pulse(hex), dispose(), ... }
import * as THREE from "three";
import { EffectComposer } from "three/examples/jsm/postprocessing/EffectComposer.js";
import { RenderPass } from "three/examples/jsm/postprocessing/RenderPass.js";
import { UnrealBloomPass } from "three/examples/jsm/postprocessing/UnrealBloomPass.js";
import { OutputPass } from "three/examples/jsm/postprocessing/OutputPass.js";
import { RoomEnvironment } from "three/examples/jsm/environments/RoomEnvironment.js";
import { makeGlassSculpture, makeGarden, makeShardSpine, type Prop } from "./models";

export interface CardFace {
  title: string;
  caption: string;
  hue: number;
}
export interface Fonts {
  /** CSS font-family lists (next/font hashes the family names, so the page passes its own). */
  display: string;
  sans: string;
  mono: string;
}
export interface ImmersiveOptions {
  cards?: CardFace[];
  /** < 768 px: 4,000 points, bloom off, DPR 1.5, no parallax, no gardens, one spine, lighter glass. */
  mobile?: boolean;
  fonts?: Partial<Fonts>;
}
export interface Immersive {
  setProgress(x: number): void;
  pulse(hex: string): void;
  jump(x: number): void;
  /** Redraw one card face (a live caption); the texture is re-uploaded. */
  setCard(i: number, face: CardFace): void;
  /** The index of the visible card under a viewport point, or -1. */
  cardAt(clientX: number, clientY: number): number;
  /** Stop/restart the frame loop (the hero scrolled off-screen, or the tab is hidden). */
  setRunning(on: boolean): void;
  dispose(): void;
}

const DEFAULT_FONTS: Fonts = { display: "Doto, 'Geist Mono', monospace", sans: "Geist, Inter, Arial, sans-serif", mono: "'Geist Mono', monospace" };

const SEATS = ["#FF4D3D", "#4DA3FF", "#FFB547", "#3DDC84", "#C77DFF", "#FF6FB5", "#2EE6D6"];
const smooth = (a: number, b: number, x: number) => { const t = Math.min(1, Math.max(0, (x - a) / (b - a))); return t * t * (3 - 2 * t); };

/** Points sampled from the letter U (so the emblem is ours, not a copy of anyone's mark). */
function emblemPoints(n: number, fonts: Fonts) {
  const c = document.createElement("canvas"); c.width = c.height = 256;
  const g = c.getContext("2d")!;
  g.fillStyle = "#fff"; g.font = `700 210px ${fonts.sans}`; g.textAlign = "center"; g.textBaseline = "middle";
  g.fillText("U", 128, 140);
  const d = g.getImageData(0, 0, 256, 256).data, hits: [number, number][] = [];
  for (let y = 0; y < 256; y += 2) for (let x = 0; x < 256; x += 2) if (d[(y * 256 + x) * 4 + 3] > 128) hits.push([x, y]);
  if (!hits.length) hits.push([128, 140]);
  const out = new Float32Array(n * 3);
  for (let i = 0; i < n; i++) {
    const [x, y] = hits[(Math.random() * hits.length) | 0];
    out.set([(x - 128) / 128 * 0.62 + (Math.random() - .5) * .01, -(y - 140) / 128 * 0.62, (Math.random() - .5) * 0.12], i * 3);
  }
  return out;
}

/** A card face drawn on a 2D canvas: iridescent gradient + dot-matrix title + mono caption. */
function drawCard(c: HTMLCanvasElement, { title, caption, hue }: CardFace, fonts: Fonts) {
  const g = c.getContext("2d")!;
  g.clearRect(0, 0, 1024, 640);
  const gr = g.createLinearGradient(0, 0, 1024, 640);
  gr.addColorStop(0, `hsl(${hue},85%,62%)`); gr.addColorStop(.45, `hsl(${(hue + 70) % 360},80%,45%)`);
  gr.addColorStop(1, `hsl(${(hue + 160) % 360},90%,30%)`);
  g.fillStyle = gr; g.fillRect(0, 0, 1024, 640);
  for (let i = 0; i < 26; i++) {           // soft blobs
    const x = Math.random() * 1024, y = Math.random() * 640, r = 60 + Math.random() * 220;
    const b = g.createRadialGradient(x, y, 0, x, y, r);
    b.addColorStop(0, `hsla(${(hue + Math.random() * 200) % 360},95%,70%,.55)`); b.addColorStop(1, "hsla(0,0%,0%,0)");
    g.fillStyle = b; g.fillRect(0, 0, 1024, 640);
  }
  g.fillStyle = "rgba(5,5,5,.28)"; g.fillRect(0, 0, 1024, 640);
  g.fillStyle = "#F2F3EE"; g.font = `800 92px ${fonts.display}`; g.textAlign = "center";
  title.split("\n").forEach((l, i, a) => g.fillText(l, 512, 330 - (a.length - 1) * 50 + i * 100));
  g.font = `500 30px ${fonts.mono}`; g.fillStyle = "rgba(242,243,238,.85)";
  g.fillText(caption, 512, 560, 940);
}

function cardTexture(face: CardFace, fonts: Fonts) {
  const c = document.createElement("canvas"); c.width = 1024; c.height = 640;
  drawCard(c, face, fonts);
  const t = new THREE.CanvasTexture(c); t.colorSpace = THREE.SRGBColorSpace; t.anisotropy = 4;
  return t;
}

function roundedCard(w: number, h: number, r: number) {
  const s = new THREE.Shape(), x = -w / 2, y = -h / 2;
  s.moveTo(x + r, y); s.lineTo(x + w - r, y); s.quadraticCurveTo(x + w, y, x + w, y + r);
  s.lineTo(x + w, y + h - r); s.quadraticCurveTo(x + w, y + h, x + w - r, y + h);
  s.lineTo(x + r, y + h); s.quadraticCurveTo(x, y + h, x, y + h - r); s.lineTo(x, y + r); s.quadraticCurveTo(x, y, x + r, y);
  const geo = new THREE.ExtrudeGeometry(s, { depth: 0.04, bevelEnabled: true, bevelSize: 0.02, bevelThickness: 0.02, bevelSegments: 4, curveSegments: 12 });
  geo.center();
  // planar UVs on the front face
  const p = geo.attributes.position, uv = geo.attributes.uv;
  for (let i = 0; i < p.count; i++) uv.setXY(i, (p.getX(i) + w / 2) / w, (p.getY(i) + h / 2) / h);
  return geo;
}

type CardMesh = THREE.Mesh<THREE.ExtrudeGeometry, THREE.MeshPhysicalMaterial>;

export function createImmersive(canvas: HTMLCanvasElement, opts: ImmersiveOptions = {}): Immersive {
  const reduced = matchMedia("(prefers-reduced-motion: reduce)").matches;
  const mobile = opts.mobile === true;
  const fonts: Fonts = { ...DEFAULT_FONTS, ...opts.fonts };
  const renderer = new THREE.WebGLRenderer({ canvas, antialias: true, powerPreference: "high-performance" });
  renderer.setPixelRatio(Math.min(devicePixelRatio || 1, mobile ? 1.5 : 1.75));
  renderer.toneMapping = THREE.ACESFilmicToneMapping; renderer.toneMappingExposure = 1.05;
  const scene = new THREE.Scene(); scene.background = new THREE.Color("#050505");
  scene.fog = new THREE.FogExp2("#07040d", 0.085);
  const pmrem = new THREE.PMREMGenerator(renderer);
  const envRT = pmrem.fromScene(new RoomEnvironment(), 0.04);
  scene.environment = envRT.texture;
  const camera = new THREE.PerspectiveCamera(40, 1, 0.05, 100);

  // violet stage light + lime key
  scene.add(new THREE.AmbientLight("#2a1846", 1.2));
  const key = new THREE.PointLight("#BEF243", 14, 12); key.position.set(1.5, 1.2, 3); scene.add(key);
  const rim = new THREE.PointLight("#8b5cf6", 60, 20); rim.position.set(-3, 2, -2); scene.add(rim);

  // ---- emblem: iridescent ring + particle U
  const ring = new THREE.Mesh(new THREE.TorusGeometry(0.95, 0.075, 48, 160),
    new THREE.MeshPhysicalMaterial({ color: "#d8d2ff", metalness: 1, roughness: 0.12, iridescence: 1,
      iridescenceIOR: 1.8, iridescenceThicknessRange: [250, 800], clearcoat: 1, envMapIntensity: 1.1, transparent: true }));
  scene.add(ring);
  const N = mobile ? 4000 : 9000, home = emblemPoints(N, fonts), burst = new Float32Array(N * 3), cols = new Float32Array(N * 3);
  const col = new THREE.Color();
  for (let i = 0; i < N; i++) {
    const v = new THREE.Vector3().randomDirection().multiplyScalar(1.5 + Math.random() * 5);
    burst.set([v.x, v.y * 0.7, v.z - 1.5], i * 3);
    col.set(Math.random() < 0.55 ? "#BEF243" : SEATS[(Math.random() * 7) | 0]);
    cols.set([col.r, col.g, col.b], i * 3);
  }
  const pGeo = new THREE.BufferGeometry();
  pGeo.setAttribute("position", new THREE.BufferAttribute(home.slice(), 3));
  pGeo.setAttribute("color", new THREE.BufferAttribute(cols, 3));
  const pMat = new THREE.PointsMaterial({ size: 0.018, vertexColors: true, transparent: true, opacity: 0.95,
    blending: THREE.AdditiveBlending, depthWrite: false, sizeAttenuation: true });
  const particles = new THREE.Points(pGeo, pMat); scene.add(particles);

  // ambient dust (always there)
  const D = 2500, dust = new Float32Array(D * 3);
  for (let i = 0; i < D; i++) dust.set([(Math.random() - .5) * 14, (Math.random() - .5) * 8, -Math.random() * 16 + 2], i * 3);
  const dGeo = new THREE.BufferGeometry(); dGeo.setAttribute("position", new THREE.BufferAttribute(dust, 3));
  scene.add(new THREE.Points(dGeo, new THREE.PointsMaterial({ size: 0.012, color: "#d9c27a", transparent: true, opacity: .55, blending: THREE.AdditiveBlending, depthWrite: false })));

  // ---- glass cards along an arc (phones: a narrower, single-column path)
  const cards: CardMesh[] = (opts.cards ?? []).map((c, i) => {
    const m: CardMesh = new THREE.Mesh(roundedCard(1.6, 1.0, 0.09), new THREE.MeshPhysicalMaterial({
      map: cardTexture(c, fonts), roughness: 0.18, metalness: 0.1, clearcoat: 1, clearcoatRoughness: 0.05,
      iridescence: 0.8, iridescenceIOR: 1.5, envMapIntensity: 1.4, emissive: "#ffffff", emissiveIntensity: 0.0 }));
    m.material.emissiveMap = m.material.map; m.material.emissiveIntensity = 0.32;
    const side = i % 2 ? 1 : -1;                    // cards float past on both sides
    m.position.set(mobile ? side * 0.6 : side * (1.15 + (i % 3) * 0.12), (i % 2 ? 0.3 : -0.24), -6.5 - i * 2.0);
    m.material.transparent = true;
    m.rotation.set(0, -side * (mobile ? 0.25 : 0.42), side * -0.05);
    m.userData = { base: m.position.clone(), phase: i * 1.3, index: i };
    scene.add(m); return m;
  });

  // ---- procedural 3D models (D42): gardens by the cards, a chrome spine, the glass sculpture at the end
  const props: Prop[] = [];
  if (!mobile) cards.forEach((c, i) => {
    const side = Math.sign(c.position.x) || 1;
    const g = makeGarden(new THREE.Vector3(c.position.x + side * 0.55, c.position.y - 0.45, c.position.z + 0.25), 3 + (i % 2), 0.9);
    scene.add(g); props.push(g);
  });
  if (cards.length) {
    const spine = makeShardSpine(); spine.position.set(Math.sign(cards[0].position.x) * 2.25, 0.3, cards[0].position.z - 0.6);
    scene.add(spine); props.push(spine);
    if (!mobile) {
      const spine2 = makeShardSpine(20, 2.0); spine2.position.set(-2.3, 0.4, cards[0].position.z - 4.4);
      scene.add(spine2); props.push(spine2);
    }
  }
  const sculpture = makeGlassSculpture(mobile); sculpture.position.set(0.55, 0.05, -17.6); sculpture.scale.setScalar(0.85); scene.add(sculpture); props.push(sculpture);
  const stage = new THREE.PointLight("#8b5cf6", 40, 8); stage.position.set(-1.6, 1.8, -15); scene.add(stage);

  const composer = new EffectComposer(renderer);
  composer.addPass(new RenderPass(scene, camera));
  const bloom = mobile ? null : new UnrealBloomPass(new THREE.Vector2(1, 1), 0.5, 0.5, 0.62);
  if (bloom) composer.addPass(bloom);
  composer.addPass(new OutputPass());

  const resize = () => {
    const w = canvas.clientWidth, h = canvas.clientHeight;
    if (!w || !h) return;
    renderer.setSize(w, h, false); composer.setSize(w, h); bloom?.setSize(w, h);
    camera.aspect = w / h; camera.updateProjectionMatrix();
    if (reduced) frame(0);
  };

  let p = 0, target = 0, t = 0, raf = 0, running = true;
  const mouse = new THREE.Vector2();
  const onMove = (e: PointerEvent) => mouse.set(e.clientX / innerWidth - .5, e.clientY / innerHeight - .5);
  if (!mobile) addEventListener("pointermove", onMove, { passive: true });
  const pos = pGeo.attributes.position.array as Float32Array;

  function frame(dt: number) {
    t += dt; p += (target - p) * Math.min(1, dt * 4);
    const out = smooth(0.16, 0.34, p) * (1 - smooth(0.82, 0.98, p));   // 0 = emblem, 1 = storm
    for (let i = 0; i < N * 3; i += 3) {
      const w = Math.sin(t * 0.8 + i) * 0.004;
      pos[i] = home[i] + (burst[i] - home[i]) * out + w;
      pos[i + 1] = home[i + 1] + (burst[i + 1] - home[i + 1]) * out + Math.cos(t * 0.7 + i) * 0.004;
      pos[i + 2] = home[i + 2] + (burst[i + 2] - home[i + 2]) * out;
    }
    pGeo.attributes.position.needsUpdate = true;
    ring.rotation.set(Math.sin(t * .4) * .25 + mouse.y * .4, t * .35 + mouse.x * .6, 0);
    ring.scale.setScalar(1 + out * 1.4); ring.material.opacity = 1 - out; ring.visible = out < 0.98;
    particles.rotation.y = mouse.x * .25 + out * t * .05;
    // camera path: front of the emblem → along the card arc → back to the emblem
    const fly = smooth(0.32, 0.8, p), back = smooth(0.82, 1.0, p);
    const z = THREE.MathUtils.lerp(4.2, -13.0, fly);
    camera.position.set(Math.sin(fly * Math.PI * 2) * 0.25 + mouse.x * .3, -mouse.y * .2, THREE.MathUtils.lerp(z, 4.2, back));
    camera.lookAt(mouse.x * .4, 0, camera.position.z - 5);
    particles.position.z = THREE.MathUtils.lerp(0, -12.5, fly) * (1 - back);   // the storm travels with us
    ring.position.z = particles.position.z;
    const cardsIn = smooth(0.3, 0.42, p) * (1 - smooth(0.84, 0.95, p));
    cards.forEach((c) => {
      c.material.opacity = cardsIn; c.visible = cardsIn > 0.01;
      c.position.y = (c.userData.base as THREE.Vector3).y + Math.sin(t * .8 + (c.userData.phase as number)) * .06;
      c.rotation.y += Math.sin(t * .5 + (c.userData.phase as number)) * .0008;
    });
    props.forEach((o) => { o.update(t); if (o !== sculpture) o.visible = cardsIn > 0.01; });
    sculpture.visible = smooth(0.55, 0.7, p) * (1 - smooth(0.86, 0.95, p)) > 0.01;
    if (bloom) bloom.strength = 0.45 + out * 0.25;
    composer.render();
  }
  const ro = new ResizeObserver(resize); ro.observe(canvas); resize();
  let last = performance.now();
  const loop = () => { const now = performance.now(); frame(Math.min((now - last) / 1000, 0.05)); last = now; raf = requestAnimationFrame(loop); };
  const start = () => { cancelAnimationFrame(raf); last = performance.now(); raf = requestAnimationFrame(loop); };
  const onVisibility = () => { if (document.hidden) cancelAnimationFrame(raf); else if (running && !reduced) start(); };
  document.addEventListener("visibilitychange", onVisibility);
  if (reduced) frame(0); else loop();

  const ray = new THREE.Raycaster(), ndc = new THREE.Vector2();
  return {
    setProgress(x) { target = Math.max(0, Math.min(1, x)); if (reduced) { p = target; frame(0); } },
    /** a live firm event: flash the ring and nudge the key light to the department's color */
    pulse(hex) { key.color.set(hex); setTimeout(() => key.color.set("#BEF243"), 900); },
    jump(x) { p = target = x; },
    setCard(i, face) {
      const c = cards[i]; const map = c?.material.map as THREE.CanvasTexture | null | undefined;
      if (!map) return;
      drawCard(map.image as HTMLCanvasElement, face, fonts); map.needsUpdate = true;
      if (reduced) frame(0);
    },
    cardAt(clientX, clientY) {
      const r = canvas.getBoundingClientRect();
      if (!r.width || !r.height) return -1;
      ndc.set(((clientX - r.left) / r.width) * 2 - 1, -((clientY - r.top) / r.height) * 2 + 1);
      ray.setFromCamera(ndc, camera);
      const hit = ray.intersectObjects(cards.filter((c) => c.visible && c.material.opacity > 0.3), false)[0];
      return hit ? (hit.object.userData.index as number) : -1;
    },
    setRunning(on) {
      if (on === running) return;
      running = on;
      if (reduced) return;
      if (on && !document.hidden) start(); else cancelAnimationFrame(raf);
    },
    dispose() {
      cancelAnimationFrame(raf); ro.disconnect(); removeEventListener("pointermove", onMove);
      document.removeEventListener("visibilitychange", onVisibility);
      scene.traverse((o) => {
        const m = o as THREE.Mesh;
        m.geometry?.dispose();
        const mats = Array.isArray(m.material) ? m.material : m.material ? [m.material] : [];
        mats.forEach((mat) => { (mat as THREE.MeshPhysicalMaterial).map?.dispose(); mat.dispose(); });
      });
      envRT.dispose(); composer.dispose(); renderer.dispose(); pmrem.dispose();
    },
  };
}
