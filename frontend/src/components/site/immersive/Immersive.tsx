"use client";

// D42 P1: the immersive hero. A sticky WebGL canvas inside a 600vh section; the section's own
// scroll drives the scene (scene.ts) and switches the acts. Loaded with next/dynamic (ssr: false):
// the server renders the poster (Acts.tsx). No WebGL 2 → the poster; reduced motion → a still frame
// with the acts in a normal column; phones → the light profile (scene.ts).

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useEffect, useRef, useState } from "react";
import { ACTS, ImmersivePoster, PillNav } from "./Acts";
import { CARD_KEYS, CARD_LINKS, baseFaces } from "./cards";
import { useGlass } from "./live";
import { createImmersive, type CardFace, type Fonts, type Immersive as Scene } from "./scene";

function hasWebGL2(): boolean {
  try {
    const c = document.createElement("canvas");
    const gl = c.getContext("webgl2");
    if (!gl) return false;
    gl.getExtension("WEBGL_lose_context")?.loseContext();
    return true;
  } catch {
    return false;
  }
}

/** next/font hashes family names; the page's own CSS variables carry the real ones. */
function pageFonts(): Fonts {
  const css = getComputedStyle(document.documentElement);
  const v = (name: string, fallback: string) => css.getPropertyValue(name).trim() || fallback;
  return {
    display: `${v("--font-doto", "Doto")}, 'Geist Mono', monospace`,
    sans: `${v("--font-geist", "Geist")}, Inter, Arial, sans-serif`,
    mono: `${v("--font-geist-mono", "'Geist Mono'")}, monospace`,
  };
}

async function fontsReady(f: Fonts) {
  const loads = Promise.all([document.fonts.load(`800 92px ${f.display}`), document.fonts.load(`700 210px ${f.sans}`), document.fonts.load(`500 30px ${f.mono}`)]);
  // a slow font never holds the hero back for long: the canvas draws with the fallback face
  await Promise.race([loads, new Promise((r) => setTimeout(r, 2500))]).catch(() => undefined);
}

type Mode = "webgl" | "poster";

export default function Immersive() {
  const router = useRouter();
  const sectionRef = useRef<HTMLElement>(null);
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const actRefs = useRef<(HTMLDivElement | null)[]>([]);
  const sceneRef = useRef<Scene | null>(null);
  // client-only (next/dynamic, ssr: false), so the WebGL 2 probe can run in the initializer
  const [mode, setMode] = useState<Mode>(() => (typeof window !== "undefined" && hasWebGL2() ? "webgl" : "poster"));
  const glass = useGlass();
  const facesRef = useRef<CardFace[] | null>(null);
  /** The captions now on the glass (a face is redrawn only when its caption changed). */
  const appliedRef = useRef<string[]>([]);
  const pulsedRef = useRef<number | null>(null);
  const [reduced] = useState(() => typeof window !== "undefined" && window.matchMedia("(prefers-reduced-motion: reduce)").matches);

  useEffect(() => {
    const canvas = canvasRef.current;
    const section = sectionRef.current;
    if (mode !== "webgl" || !canvas || !section) return;
    let alive = true;
    let s: Scene | null = null;
    let io: IntersectionObserver | null = null;
    const mobile = window.matchMedia("(max-width: 767px)").matches;

    const showActs = (x: number) =>
      ACTS.forEach((a, i) => {
        const el = actRefs.current[i];
        if (!el) return;
        const on = reduced || (x >= a.from && x < a.to);
        el.classList.toggle("on", on);
        el.inert = !on;
      });
    const progress = () => {
      const r = section.getBoundingClientRect();
      const span = r.height - window.innerHeight;
      return span > 0 ? Math.min(1, Math.max(0, -r.top / span)) : 0;
    };
    const onScroll = () => {
      if (reduced) return;
      const x = progress();
      s?.setProgress(x);
      showActs(x);
    };
    const onMove = (e: PointerEvent) => {
      if (!s) return;
      canvas.style.cursor = s.cardAt(e.clientX, e.clientY) >= 0 ? "pointer" : "";
    };
    const onClick = (e: MouseEvent) => {
      const i = s ? s.cardAt(e.clientX, e.clientY) : -1;
      if (i >= 0) router.push(CARD_LINKS[CARD_KEYS[i]].href);
    };

    showActs(reduced ? 0 : progress());
    (async () => {
      const fonts = pageFonts();
      await fontsReady(fonts);
      if (!alive) return;
      try {
        const faces = facesRef.current ?? baseFaces();
        s = createImmersive(canvas, { cards: faces, mobile, fonts });
        appliedRef.current = faces.map((f) => f.caption);
      } catch {
        setMode("poster");
        return;
      }
      sceneRef.current = s;
      onScroll();
      io = new IntersectionObserver(([entry]) => s?.setRunning(entry.isIntersecting));
      io.observe(section);
    })();

    window.addEventListener("scroll", onScroll, { passive: true });
    window.addEventListener("resize", onScroll, { passive: true });
    canvas.addEventListener("pointermove", onMove, { passive: true });
    canvas.addEventListener("click", onClick);
    return () => {
      alive = false;
      window.removeEventListener("scroll", onScroll);
      window.removeEventListener("resize", onScroll);
      canvas.removeEventListener("pointermove", onMove);
      canvas.removeEventListener("click", onClick);
      io?.disconnect();
      s?.dispose();
      sceneRef.current = null;
    };
  }, [mode, reduced, router]);

  // P2.1: live captions on the glass, redrawn when they change (the poll runs every 30 s)
  useEffect(() => {
    facesRef.current = glass.faces; // a scene still waiting on its fonts starts with these
    const s = sceneRef.current;
    if (!s || !glass.faces) return;
    glass.faces.forEach((f, i) => {
      if (appliedRef.current[i] === f.caption) return;
      s.setCard(i, f);
      appliedRef.current[i] = f.caption;
    });
  }, [glass.faces]);

  // P2.2: a new public event flashes the key light in its department's colour (not the first read)
  useEffect(() => {
    const t = glass.ticker;
    if (!t) return;
    if (pulsedRef.current !== null && t.id > pulsedRef.current) sceneRef.current?.pulse(t.color);
    pulsedRef.current = t.id;
  }, [glass.ticker]);

  if (mode === "poster") return <ImmersivePoster />;

  return (
    <section ref={sectionRef} className={`imm${reduced ? " imm-reduced" : ""}`} aria-labelledby="hero-h">
      <div className="imm-stage">
        <canvas ref={canvasRef} className="imm-canvas" aria-hidden="true" />
        <div className="imm-scrim" aria-hidden="true" />
        <PillNav />
        {ACTS.map((a, i) => (
          <div
            key={a.from}
            ref={(el) => {
              actRefs.current[i] = el;
            }}
            className={`imm-act${i === 0 || reduced ? " on" : ""}`}
          >
            {a.body}
          </div>
        ))}
        <Link href="/office" className={`imm-ticker${glass.ticker ? "" : " off"}`} aria-live="polite">
          <i aria-hidden="true" />
          <span>{glass.ticker ? `LIVE · ${glass.ticker.text}` : glass.open === false ? "the firm is resting · open the office" : "the firm, live · open the office"}</span>
        </Link>
      </div>
    </section>
  );
}
