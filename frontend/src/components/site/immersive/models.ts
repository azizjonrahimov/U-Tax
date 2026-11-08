// D42 procedural 3D models for the immersive landing (original work, no downloaded assets).
// Copied from docs/designs/immersive/models.js; types added, logic unchanged except the phone
// option on the sculpture (runbook P1b: transmission 0.6, thickness 0.6 on phones).
// Each maker returns a THREE.Object3D with an `update(t)` method the scene calls every frame.
import * as THREE from "three";

export type Prop = THREE.Group & { update: (t: number) => void };

const SEATS = ["#FF4D3D", "#4DA3FF", "#FFB547", "#3DDC84", "#C77DFF", "#FF6FB5", "#2EE6D6"];

/** Glass sculpture: a twisted knot of clear, iridescent glass with a lime heart light inside. */
export function makeGlassSculpture(lite = false): Prop {
  const g = new THREE.Group() as Prop;
  const knot = new THREE.Mesh(
    new THREE.TorusKnotGeometry(0.62, 0.2, 260, 40, 2, 5),
    new THREE.MeshPhysicalMaterial({ color: "#ffffff", metalness: 0, roughness: 0.04, transmission: lite ? 0.6 : 1,
      thickness: lite ? 0.6 : 1.2, ior: 1.45, iridescence: 1, iridescenceIOR: 1.35, iridescenceThicknessRange: [180, 700],
      clearcoat: 1, attenuationColor: "#c9b8ff", attenuationDistance: 2.4, envMapIntensity: 1.6 }));
  g.add(knot);
  const heart = new THREE.Mesh(new THREE.IcosahedronGeometry(0.16, 3),
    new THREE.MeshBasicMaterial({ color: "#BEF243" }));
  g.add(heart);
  const light = new THREE.PointLight("#BEF243", 6, 4); g.add(light);
  // pedestal: a thin dark disc with a lime rim
  const ped = new THREE.Mesh(new THREE.CylinderGeometry(0.9, 1.0, 0.08, 96),
    new THREE.MeshPhysicalMaterial({ color: "#0d0b14", metalness: 0.6, roughness: 0.35, clearcoat: 1 }));
  ped.position.y = -1.05; g.add(ped);
  const rim = new THREE.Mesh(new THREE.TorusGeometry(0.98, 0.008, 8, 160), new THREE.MeshBasicMaterial({ color: "#BEF243" }));
  rim.rotation.x = Math.PI / 2; rim.position.y = -1.0; g.add(rim);
  g.update = (t) => { knot.rotation.set(t * 0.21, t * 0.33, 0); heart.scale.setScalar(1 + Math.sin(t * 2.2) * 0.12); };
  return g;
}

/** One petal: a cupped, tapered curved surface (built per vertex). */
function petalGeometry(len = 0.42, wid = 0.16) {
  const geo = new THREE.PlaneGeometry(wid, len, 8, 16);
  const p = geo.attributes.position;
  for (let i = 0; i < p.count; i++) {
    const x = p.getX(i), y = p.getY(i) + len / 2;         // 0 at the base
    const v = y / len;
    const taper = Math.sin(Math.PI * Math.min(1, v * 0.95 + 0.05)) * (1 - 0.35 * v);
    p.setX(i, x * taper * 1.6);
    p.setZ(i, -(x * x) * 6 * (0.4 + v) + v * v * 0.18);    // cupped, tip curls out
    p.setY(i, y);
  }
  geo.computeVertexNormals();
  return geo;
}

/** Crystal flower: rings of iridescent petals around a glowing core, colored by a department. */
export function makeFlower(hex = "#FF6FB5", size = 1): Prop {
  const g = new THREE.Group() as Prop;
  const petalMat = new THREE.MeshPhysicalMaterial({ color: hex, metalness: 0.1, roughness: 0.22,
    transmission: 0.35, thickness: 0.3, iridescence: 1, iridescenceIOR: 1.7, clearcoat: 1,
    side: THREE.DoubleSide, emissive: hex, emissiveIntensity: 0.32, envMapIntensity: 1.5 });
  const rings: [number, number, number, number][] = [[7, 0.0, 0.42, 1.0], [6, 0.35, 0.34, 0.75], [5, 0.7, 0.24, 0.45]];
  const geos = rings.map(([, , len]) => petalGeometry(len, len * 0.42));
  rings.forEach(([n, off, , tilt], r) => {
    for (let i = 0; i < n; i++) {
      const pet = new THREE.Mesh(geos[r], petalMat);
      const a = (i / n) * Math.PI * 2 + off;
      pet.rotation.order = "YXZ"; pet.rotation.y = a; pet.rotation.x = -tilt;
      g.add(pet);
    }
  });
  const core = new THREE.Mesh(new THREE.IcosahedronGeometry(0.07, 2), new THREE.MeshBasicMaterial({ color: "#fff6c8" }));
  core.position.y = 0.03; g.add(core);
  // pollen: tiny glowing points
  const N = 60, pos = new Float32Array(N * 3);
  for (let i = 0; i < N; i++) { const v = new THREE.Vector3().randomDirection().multiplyScalar(0.05 + Math.random() * 0.12); pos.set([v.x, Math.abs(v.y) + 0.05, v.z], i * 3); }
  const pg = new THREE.BufferGeometry(); pg.setAttribute("position", new THREE.BufferAttribute(pos, 3));
  g.add(new THREE.Points(pg, new THREE.PointsMaterial({ size: 0.012, color: "#fff2a8", transparent: true, opacity: .9, blending: THREE.AdditiveBlending, depthWrite: false })));
  g.scale.setScalar(size);
  const seed = Math.random() * 10;
  g.update = (t) => { g.rotation.y = seed + t * 0.15; g.position.y = (g.userData.y as number) + Math.sin(t * 0.9 + seed) * 0.04; };
  return g;
}

/** Chrome spine: a tall column of twisted iridescent shards (like a crystal growing upward). */
export function makeShardSpine(count = 26, height = 2.6): Prop {
  const g = new THREE.Group() as Prop;
  const shape = new THREE.Shape(); shape.moveTo(0, 0); shape.lineTo(0.22, 0.06); shape.lineTo(0.3, 0.0); shape.lineTo(0.2, -0.05); shape.lineTo(0, 0);
  const geo = new THREE.ExtrudeGeometry(shape, { depth: 0.03, bevelEnabled: true, bevelSize: 0.012, bevelThickness: 0.012, bevelSegments: 2 });
  geo.center();
  const mat = new THREE.MeshPhysicalMaterial({ color: "#e6e0ff", metalness: 1, roughness: 0.1, iridescence: 1,
    iridescenceIOR: 2.0, iridescenceThicknessRange: [300, 900], clearcoat: 1, envMapIntensity: 2 });
  const mesh = new THREE.InstancedMesh(geo, mat, count);
  const m = new THREE.Matrix4(), q = new THREE.Quaternion(), e = new THREE.Euler(), s = new THREE.Vector3(), p = new THREE.Vector3();
  for (let i = 0; i < count; i++) {
    const v = i / count, a = v * Math.PI * 7;
    p.set(Math.cos(a) * 0.12, v * height - height / 2, Math.sin(a) * 0.12);
    e.set(Math.random() * 0.6, a + Math.PI / 2, (Math.random() - 0.5) * 0.9);
    q.setFromEuler(e); s.setScalar(0.7 + Math.random() * 0.9 * (1 - v * 0.4));
    m.compose(p, q, s); mesh.setMatrixAt(i, m);
  }
  g.add(mesh);
  g.update = (t) => { g.rotation.y = t * 0.25; };
  return g;
}

/** A small garden: flowers in department colors scattered around a point. */
export function makeGarden(center: THREE.Vector3, n = 3, spread = 0.6): Prop {
  const g = new THREE.Group() as Prop;
  for (let i = 0; i < n; i++) {
    const f = makeFlower(SEATS[(Math.random() * SEATS.length) | 0], 0.9 + Math.random() * 0.8);
    f.position.set(center.x + (Math.random() - 0.5) * spread, center.y + (Math.random() - 0.5) * spread * 0.6, center.z + (Math.random() - 0.5) * spread);
    f.userData.y = f.position.y;
    f.rotation.x = -0.9 + Math.random() * 0.5;
    g.add(f);
  }
  g.update = (t) => g.children.forEach((c) => (c as Prop).update(t));
  return g;
}
