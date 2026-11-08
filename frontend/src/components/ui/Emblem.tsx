// D42 P3: U-Tax's own emblem, a static SVG for dense UIs (no WebGL): an iridescent ring (violet →
// cyan → lime) around a "U" drawn in dots. Original work; nothing from any reference site.

import { useId } from "react";

/** The dots of the U: down the left side, round the bottom, up the right (a 24×24 grid, centred). */
const U_DOTS: [number, number][] = (() => {
  const out: [number, number][] = [];
  for (let y = 6.5; y <= 13.5; y += 1.75) out.push([8, y], [16, y]);
  for (let a = 0; a <= 180; a += 30) {
    const r = (a * Math.PI) / 180;
    out.push([12 - 4 * Math.cos(r), 13.5 + 4 * Math.sin(r)]);
  }
  return out;
})();

export function Emblem({ size = 30, className, title }: { size?: number; className?: string; title?: string }) {
  const id = useId().replace(/:/g, "");
  return (
    <svg className={`emblem${className ? ` ${className}` : ""}`} viewBox="0 0 24 24" width={size} height={size} role={title ? "img" : undefined} aria-label={title} aria-hidden={title ? undefined : true} focusable="false">
      <defs>
        <linearGradient id={`em-${id}`} x1="0" y1="0" x2="1" y2="1">
          <stop offset="0" stopColor="#8b5cf6" />
          <stop offset=".35" stopColor="#2ee6d6" />
          <stop offset=".7" stopColor="#bef243" />
          <stop offset="1" stopColor="#f2f3ee" />
        </linearGradient>
      </defs>
      <circle cx="12" cy="12" r="10.6" fill="rgba(5,5,5,.6)" stroke={`url(#em-${id})`} strokeWidth="1.5" />
      {U_DOTS.map(([x, y], i) => (
        <circle key={i} cx={x.toFixed(2)} cy={y.toFixed(2)} r=".95" fill="#bef243" />
      ))}
    </svg>
  );
}
