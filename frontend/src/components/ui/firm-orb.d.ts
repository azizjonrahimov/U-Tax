// Types for the verbatim copy of docs/designs/immersive/firm-orb.js (D42 P3).
export interface FirmOrbApi {
  pulse(hex?: string): void;
  setLevel(x: number): void;
  setMode(m: "idle" | "listening" | "speaking" | "working"): void;
  dispose(): void;
}
export function createFirmOrb(canvas: HTMLCanvasElement, opts?: { points?: number; size?: number; amp?: number; interactive?: boolean }): FirmOrbApi;
