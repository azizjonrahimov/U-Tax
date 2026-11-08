// D42 "Firm Orb": U-Tax's one signature visual (original work, Night Lime).
// A living point-cloud sphere = the firm. Idle it breathes; every firm event sends a ripple in
// the acting department's color across it; while the CEO talks it follows the voice level.
// Framework-free: createFirmOrb(canvas, opts) → { pulse(hex), setLevel(0..1), setMode(m), dispose() }.
import * as THREE from "three";

const NOISE = /* glsl */ `
vec4 permute(vec4 x){return mod(((x*34.0)+1.0)*x,289.0);}
vec4 taylorInvSqrt(vec4 r){return 1.79284291400159-0.85373472095314*r;}
float snoise(vec3 v){const vec2 C=vec2(1.0/6.0,1.0/3.0);const vec4 D=vec4(0.0,0.5,1.0,2.0);
vec3 i=floor(v+dot(v,C.yyy));vec3 x0=v-i+dot(i,C.xxx);vec3 g=step(x0.yzx,x0.xyz);vec3 l=1.0-g;
vec3 i1=min(g.xyz,l.zxy);vec3 i2=max(g.xyz,l.zxy);vec3 x1=x0-i1+C.xxx;vec3 x2=x0-i2+2.0*C.xxx;
vec3 x3=x0-1.0+3.0*C.xxx;i=mod(i,289.0);vec4 p=permute(permute(permute(i.z+vec4(0.0,i1.z,i2.z,1.0))
+i.y+vec4(0.0,i1.y,i2.y,1.0))+i.x+vec4(0.0,i1.x,i2.x,1.0));float n_=1.0/7.0;vec3 ns=n_*D.wyz-D.xzx;
vec4 j=p-49.0*floor(p*ns.z*ns.z);vec4 x_=floor(j*ns.z);vec4 y_=floor(j-7.0*x_);vec4 x=x_*ns.x+ns.yyyy;
vec4 y=y_*ns.x+ns.yyyy;vec4 h=1.0-abs(x)-abs(y);vec4 b0=vec4(x.xy,y.xy);vec4 b1=vec4(x.zw,y.zw);
vec4 s0=floor(b0)*2.0+1.0;vec4 s1=floor(b1)*2.0+1.0;vec4 sh=-step(h,vec4(0.0));
vec4 a0=b0.xzyw+s0.xzyw*sh.xxyy;vec4 a1=b1.xzyw+s1.xzyw*sh.zzww;vec3 p0=vec3(a0.xy,h.x);
vec3 p1=vec3(a0.zw,h.y);vec3 p2=vec3(a1.xy,h.z);vec3 p3=vec3(a1.zw,h.w);
vec4 norm=taylorInvSqrt(vec4(dot(p0,p0),dot(p1,p1),dot(p2,p2),dot(p3,p3)));p0*=norm.x;p1*=norm.y;
p2*=norm.z;p3*=norm.w;vec4 m=max(0.6-vec4(dot(x0,x0),dot(x1,x1),dot(x2,x2),dot(x3,x3)),0.0);m=m*m;
return 42.0*dot(m*m,vec4(dot(p0,x0),dot(p1,x1),dot(p2,x2),dot(p3,x3)));}`;

const VERT = /* glsl */ `
uniform float uTime, uLevel, uAmp, uSize, uPixel;
uniform vec4 uRipple[4];      // xyz = origin on the sphere, w = age in seconds (<0 = off)
uniform vec3 uRippleColor[4];
attribute float aSeed;
varying vec3 vColor; varying float vAlpha;
${NOISE}
void main(){
  vec3 p = position;
  float n = snoise(p*1.25 + vec3(0.0, uTime*0.18, uTime*0.11));
  float n2 = snoise(p*4.0 - uTime*0.3) * 0.25;
  float disp = (n + n2) * (uAmp + uLevel*0.22);
  vec3 col = vec3(0.745, 0.949, 0.263);                // #BEF243
  float glow = 0.0;
  for (int i = 0; i < 4; i++) {
    float age = uRipple[i].w;
    if (age < 0.0) continue;
    float d = acos(clamp(dot(normalize(p), uRipple[i].xyz), -1.0, 1.0));   // 0..pi along the surface
    float front = age * 1.6;
    float ring = exp(-pow((d - front) * 5.0, 2.0)) * exp(-age * 0.7);
    disp += ring * 0.22;
    col = mix(col, uRippleColor[i], clamp(ring * 2.2, 0.0, 1.0));
    glow += ring;
  }
  p += normalize(position) * disp;
  vec4 mv = modelViewMatrix * vec4(p, 1.0);
  gl_Position = projectionMatrix * mv;
  float facing = dot(normalize(normalMatrix * normalize(position)), vec3(0.0, 0.0, 1.0));
  vAlpha = (0.22 + 0.78 * smoothstep(-0.3, 0.8, facing)) * (0.65 + 0.35 * aSeed) + glow * 0.9;
  vColor = col * (0.85 + 0.45 * n) + glow * 0.5;
  gl_PointSize = uSize * uPixel * (0.6 + aSeed * 0.8 + glow * 1.4) * (3.2 / -mv.z);
}`;

const FRAG = /* glsl */ `
varying vec3 vColor; varying float vAlpha;
void main(){
  vec2 c = gl_PointCoord - 0.5; float r = dot(c, c);
  if (r > 0.25) discard;
  float soft = smoothstep(0.25, 0.0, r);
  gl_FragColor = vec4(vColor, vAlpha * soft);
}`;

export function createFirmOrb(canvas, opts = {}) {
  const count = opts.points ?? 9000;
  const reduced = typeof matchMedia !== "undefined" && matchMedia("(prefers-reduced-motion: reduce)").matches;
  const renderer = new THREE.WebGLRenderer({ canvas, antialias: false, alpha: true, powerPreference: "low-power" });
  const pixel = Math.min(window.devicePixelRatio || 1, 2);
  renderer.setPixelRatio(pixel);
  const scene = new THREE.Scene();
  const camera = new THREE.PerspectiveCamera(35, 1, 0.1, 50);
  camera.position.z = 4.2;

  // Fibonacci sphere: even points, no poles
  const pos = new Float32Array(count * 3), seed = new Float32Array(count);
  const ga = Math.PI * (3 - Math.sqrt(5));
  for (let i = 0; i < count; i++) {
    const y = 1 - (i / (count - 1)) * 2, r = Math.sqrt(1 - y * y), t = ga * i;
    pos.set([Math.cos(t) * r, y, Math.sin(t) * r], i * 3);
    seed[i] = Math.random();
  }
  const geo = new THREE.BufferGeometry();
  geo.setAttribute("position", new THREE.BufferAttribute(pos, 3));
  geo.setAttribute("aSeed", new THREE.BufferAttribute(seed, 1));
  const uniforms = {
    uTime: { value: 0 }, uLevel: { value: 0 }, uAmp: { value: opts.amp ?? 0.08 },
    uSize: { value: opts.size ?? 2.2 }, uPixel: { value: pixel },
    uRipple: { value: [0, 1, 2, 3].map(() => new THREE.Vector4(0, 1, 0, -1)) },
    uRippleColor: { value: [0, 1, 2, 3].map(() => new THREE.Color("#BEF243")) },
  };
  const mat = new THREE.ShaderMaterial({ vertexShader: VERT, fragmentShader: FRAG, uniforms,
    transparent: true, depthWrite: false, blending: THREE.AdditiveBlending });
  const orb = new THREE.Points(geo, mat);
  scene.add(orb);

  let mode = "idle", level = 0, targetLevel = 0, next = 0, raf = 0, visible = true;
  const mouse = { x: 0, y: 0 };
  const onMove = (e) => { mouse.x = e.clientX / innerWidth - 0.5; mouse.y = e.clientY / innerHeight - 0.5; };
  if (opts.interactive !== false) addEventListener("pointermove", onMove, { passive: true });

  const resize = () => {
    const w = canvas.clientWidth || 300, h = canvas.clientHeight || 300;
    renderer.setSize(w, h, false); camera.aspect = w / h; camera.updateProjectionMatrix();
  };
  const ro = new ResizeObserver(resize); ro.observe(canvas); resize();
  const io = new IntersectionObserver(([e]) => { visible = e.isIntersecting; if (visible) loop(); }, { threshold: 0 });
  io.observe(canvas);

  const clock = new THREE.Clock();
  function frame() {
    const dt = Math.min(clock.getDelta(), 0.05);
    uniforms.uTime.value += dt * (mode === "working" ? 1.8 : 1);
    level += (targetLevel - level) * Math.min(1, dt * 10);
    uniforms.uLevel.value = level;
    for (const r of uniforms.uRipple.value) if (r.w >= 0) { r.w += dt; if (r.w > 3.2) r.w = -1; }
    orb.rotation.y += dt * (mode === "speaking" ? 0.35 : 0.12);
    orb.rotation.x += ((mouse.y * 0.5) - orb.rotation.x) * 0.04;
    orb.rotation.z += ((-mouse.x * 0.3) - orb.rotation.z) * 0.04;
    renderer.render(scene, camera);
  }
  function loop() {
    cancelAnimationFrame(raf);
    const tick = () => { if (!visible || document.hidden) return; frame(); raf = requestAnimationFrame(tick); };
    if (reduced) { frame(); return; }   // one still frame
    raf = requestAnimationFrame(tick);
  }
  const onVis = () => !document.hidden && loop();
  document.addEventListener("visibilitychange", onVis);
  loop();

  return {
    /** A firm event: a ripple in the department's color from a random point facing the viewer. */
    pulse(hex = "#BEF243") {
      const r = uniforms.uRipple.value[next], c = uniforms.uRippleColor.value[next];
      const a = Math.random() * Math.PI * 2, z = 0.55 + Math.random() * 0.4, s = Math.sqrt(1 - z * z);
      const v = new THREE.Vector3(Math.cos(a) * s, Math.sin(a) * s, z).applyEuler(new THREE.Euler(-orb.rotation.x, -orb.rotation.y, -orb.rotation.z));
      r.set(v.x, v.y, v.z, 0); c.set(hex); next = (next + 1) % 4;
      if (reduced) frame();
    },
    setLevel(x) { targetLevel = Math.max(0, Math.min(1, x)); },
    setMode(m) { mode = m; uniforms.uAmp.value = m === "listening" ? 0.12 : m === "working" ? 0.1 : (opts.amp ?? 0.08); },
    dispose() {
      cancelAnimationFrame(raf); ro.disconnect(); io.disconnect();
      removeEventListener("pointermove", onMove); document.removeEventListener("visibilitychange", onVis);
      geo.dispose(); mat.dispose(); renderer.dispose();
    },
  };
}
