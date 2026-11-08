"use client";

// D42 P3: the Firm Orb (docs/designs/immersive/firm-orb.js, copied as is) as a React component.
// three.js loads only here, on demand. No WebGL → a static lime sphere; reduced motion → one still
// frame (firm-orb.js handles it). Props drive it: `mode`, and a ripple whenever `pulseKey` changes.

import { useEffect, useRef, useState } from "react";
import type { FirmOrbApi } from "./firm-orb";

type Mode = "idle" | "listening" | "speaking" | "working";

export function FirmOrb({ className, mode = "idle", pulseKey, pulseColor = "#BEF243", points = 4000, size = 2.2 }: { className?: string; mode?: Mode; pulseKey?: string | number | null; pulseColor?: string; points?: number; size?: number }) {
  const canvas = useRef<HTMLCanvasElement>(null);
  const orb = useRef<FirmOrbApi | null>(null);
  const [failed, setFailed] = useState(false);
  const modeRef = useRef(mode);

  useEffect(() => {
    const c = canvas.current;
    if (!c) return;
    let alive = true;
    import("./firm-orb.js")
      .then((m) => {
        if (!alive) return;
        try {
          orb.current = m.createFirmOrb(c, { points, size, interactive: false });
          orb.current.setMode(modeRef.current);
        } catch {
          setFailed(true); // no WebGL: the static sphere
        }
      })
      .catch(() => alive && setFailed(true));
    return () => {
      alive = false;
      orb.current?.dispose();
      orb.current = null;
    };
  }, [points, size]);

  useEffect(() => {
    modeRef.current = mode;
    orb.current?.setMode(mode);
  }, [mode]);

  useEffect(() => {
    if (pulseKey === undefined || pulseKey === null) return;
    orb.current?.pulse(pulseColor);
  }, [pulseKey, pulseColor]);

  return failed ? <span className={`firm-orb still${className ? ` ${className}` : ""}`} aria-hidden="true" /> : <canvas ref={canvas} className={`firm-orb${className ? ` ${className}` : ""}`} aria-hidden="true" />;
}
