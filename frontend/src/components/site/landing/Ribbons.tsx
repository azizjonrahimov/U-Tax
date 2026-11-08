"use client";

// D39 light ribbons, ported from docs/designs/landing/hero-prototype.html: additive-blended bezier
// strands with a soft glow, pinching to a bright point between the hero text and its card, drifting
// slowly. Canvas 2D only. LANDING-BRIEF §5: DPR capped at 2; paused when the tab is hidden or the
// canvas is off-screen; drawn once under reduced motion; half the strands if frames run slow.

import { useEffect, useRef } from "react";

const STRANDS = 120;

/** lime → yellow-green → a little cyan (the prototype's palette). */
const color = (hue: number, a: number) => (hue < 0.72 ? `rgba(190,242,67,${a})` : hue < 0.9 ? `rgba(225,255,140,${a})` : `rgba(124,199,255,${a})`);

export function Ribbons({ className, dim = false }: { className?: string; dim?: boolean }) {
  const ref = useRef<HTMLCanvasElement>(null);

  useEffect(() => {
    const c = ref.current;
    const x = c?.getContext("2d");
    if (!c || !x) return;
    const reduce = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
    // Seeded once per mount (the prototype's Math.random), so the bundle keeps its shape while it drifts.
    let seeds = Array.from({ length: STRANDS }, (_, i) => ({ o: i / STRANDS, s: Math.random() * 6.28, hue: Math.random() }));
    let w = 0;
    let h = 0;
    let t = 0;
    let raf = 0;
    let visible = true;
    let slow = 0;
    let last = 0;

    const size = () => {
      const dpr = Math.min(window.devicePixelRatio || 1, 2);
      w = c.clientWidth;
      h = c.clientHeight;
      c.width = Math.max(1, Math.round(w * dpr));
      c.height = Math.max(1, Math.round(h * dpr));
      x.setTransform(dpr, 0, 0, dpr, 0, 0);
    };

    const frame = () => {
      x.globalCompositeOperation = "source-over";
      x.clearRect(0, 0, w, h);
      x.globalCompositeOperation = "lighter";
      const cx = w * 0.5;
      const cy = h * 0.74; // the focus where the strands pinch together
      // the soft wide beam under the strands
      x.save();
      x.filter = "blur(28px)";
      x.lineWidth = 60;
      x.strokeStyle = "rgba(190,242,67,.10)";
      x.beginPath();
      x.moveTo(w * 1.05, h * 0.18);
      x.bezierCurveTo(w * 0.62, h * 0.05, cx - 40, cy - 30, cx - 20, cy);
      x.bezierCurveTo(cx + 10, cy + 60, w * 0.55, h * 0.98, w * 0.7, h * 1.05);
      x.stroke();
      x.restore();
      for (const s of seeds) {
        const spread = (s.o - 0.5) * 1.25;
        const wob = Math.sin(t * 0.0006 + s.s) * 26;
        const p0x = w * 1.05;
        const p0y = h * (0.02 + s.o * 0.45) + wob;
        const p1x = w * 0.62;
        const p1y = h * 0.05 + spread * 140 + wob;
        const p2x = cx - 40 + spread * 40;
        const p2y = cy - 30 + spread * 120;
        const p3x = cx + 30 + spread * 30;
        const p3y = cy + 40 + spread * 60;
        const p4x = w * 0.7;
        const p4y = h * 1.05 + spread * 80;
        x.beginPath();
        x.moveTo(p0x, p0y);
        x.bezierCurveTo(p1x, p1y, p2x, p2y, (p2x + p3x) / 2 - 30, (p2y + p3y) / 2);
        x.bezierCurveTo(p3x - 60, p3y, p4x - 120, p4y - 80, p4x, p4y);
        const core = Math.pow(1 - Math.abs(spread) * 2, 2);
        const a = 0.08 + 0.5 * core;
        x.strokeStyle = color(s.hue, a);
        x.lineWidth = 0.6 + 1.4 * core;
        x.shadowBlur = 16;
        x.shadowColor = color(s.hue, 0.8);
        x.stroke();
      }
      // the bright pinch point
      const g = x.createRadialGradient(cx, cy, 0, cx, cy, 120);
      g.addColorStop(0, "rgba(235,255,170,.45)");
      g.addColorStop(1, "rgba(190,242,67,0)");
      x.fillStyle = g;
      x.shadowBlur = 0;
      x.fillRect(cx - 200, cy - 200, 400, 400);
    };

    const loop = (now: number) => {
      raf = 0;
      if (!visible || document.hidden) return;
      // Frame-to-frame time (GPU and compositing included) and the draw itself: a frame over 20 ms
      // counts as slow (30 in a row: half the strands); one over 120 ms halves them at once. Still
      // slow at the fewest strands (a weak phone): draw once and stop, a still image beats a stall.
      const gap = last ? now - last : 0;
      last = now;
      t = now;
      const t0 = performance.now();
      frame();
      const draw = performance.now() - t0;
      slow = gap > 20 || draw > 20 ? slow + 1 : 0;
      if ((slow >= 30 || gap > 120 || draw > 120) && seeds.length > 30) {
        seeds = seeds.filter((_, i) => i % 2 === 0);
        slow = 0;
      } else if (slow >= 30) {
        raf = 0;
        return;
      }
      raf = requestAnimationFrame(loop);
    };
    const start = () => {
      if (!reduce && !raf && visible && !document.hidden) raf = requestAnimationFrame(loop);
    };

    size();
    if (reduce) frame();
    else start();
    const onResize = () => {
      size();
      if (reduce) frame();
    };
    const onVisibility = () => {
      last = 0;
      start();
    };
    const io = new IntersectionObserver(([e]) => {
      visible = e.isIntersecting;
      last = 0; // a pause while off-screen is not a slow frame
      start();
    });
    io.observe(c);
    window.addEventListener("resize", onResize);
    document.addEventListener("visibilitychange", onVisibility);
    return () => {
      if (raf) cancelAnimationFrame(raf);
      io.disconnect();
      window.removeEventListener("resize", onResize);
      document.removeEventListener("visibilitychange", onVisibility);
    };
  }, []);

  return <canvas ref={ref} className={`ribbons${dim ? " dim" : ""}${className ? ` ${className}` : ""}`} aria-hidden="true" />;
}
