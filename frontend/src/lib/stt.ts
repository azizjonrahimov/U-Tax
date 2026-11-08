// D36 (contract v1.16.1 §5c): reliable transcripts from the gateway's STT. The mic is recorded
// (MediaRecorder), cut into utterances by a simple level detector (speech starts, then a pause
// ends it, 30 s at most), and each utterance is POSTed as the raw body to /api/stt. The result
// comes back as a final, exactly like the browser recognizer's, so every caller's ear logic (loop
// guards, echo filter, hands-free) is unchanged. A 503 (no STT, no key, the spend cap, an outage)
// or a 429 ends this recognizer with an error the caller answers by using the browser recognizer.

import { API_URL } from "./api";
import type { Recognition, RecognitionCtor } from "./recognition";

export type SttPurpose = "ceo" | "talk";
/** The gateway's limits: one utterance ≤ 30 s and ≤ 1 MB. */
export const STT_MAX_SECONDS = 30;
const STT_MAX_BYTES = 1_000_000;
/** Errors this recognizer reports (besides the browser's own "not-allowed"). */
export const STT_UNAVAILABLE = "stt-unavailable";
export const STT_LIMITED = "stt-rate-limited";

export class SttError extends Error {
  constructor(
    readonly status: number,
    message: string,
  ) {
    super(message);
  }
}

/** One utterance → its text. Throws SttError (503: use the browser recognizer; 429: limited). */
export async function transcribe(audio: Blob, seconds: number, purpose: SttPurpose, token: string | null, fetchImpl: typeof fetch = fetch): Promise<string> {
  if (audio.size > STT_MAX_BYTES) throw new SttError(413, "too long");
  const s = Math.max(0, Math.min(STT_MAX_SECONDS, Math.ceil(seconds)));
  const headers: Record<string, string> = { "Content-Type": (audio.type || "audio/webm").split(";")[0] };
  if (purpose === "ceo" && token) headers.Authorization = `Bearer ${token}`;
  let r: Response;
  try {
    r = await fetchImpl(`${API_URL}/api/stt?purpose=${purpose}&seconds=${s}`, { method: "POST", headers, body: audio, cache: "no-store", signal: AbortSignal.timeout(20_000) });
  } catch {
    throw new SttError(0, "network");
  }
  if (!r.ok) throw new SttError(r.status, `HTTP ${r.status}`);
  const body = (await r.json().catch(() => null)) as { text?: unknown } | null;
  return typeof body?.text === "string" ? body.text.trim() : "";
}

/** The recording format: the first one this browser supports (all are on the gateway's list). */
export function recordingType(isSupported: (t: string) => boolean): string | null {
  for (const t of ["audio/webm;codecs=opus", "audio/webm", "audio/ogg;codecs=opus", "audio/mp4"]) if (isSupported(t)) return t;
  return null;
}

/** Whether this browser can record utterances for the gateway's STT. */
export function canRecord(): boolean {
  return (
    typeof window !== "undefined" &&
    typeof MediaRecorder !== "undefined" &&
    typeof AudioContext !== "undefined" &&
    !!navigator.mediaDevices?.getUserMedia &&
    recordingType((t) => MediaRecorder.isTypeSupported(t)) !== null
  );
}

/**
 * The utterance detector, pure: fed one level reading (RMS, 0..1) every `stepMs`, it says when
 * speech starts and when a pause (or the 30 s cap) ends it. The threshold follows the room's
 * noise floor, so a quiet office and a busy one both work.
 */
export class LevelGate {
  private floor = 0.01;
  private above = 0;
  private below = 0;
  private speechMs = 0;
  speaking = false;
  constructor(
    private readonly stepMs = 50,
    private readonly onsetMs = 150,
    private readonly pauseMs = 900,
    private readonly maxMs = STT_MAX_SECONDS * 1000 - 500,
  ) {}
  /** "start", "end" or null for this reading. */
  step(rms: number): "start" | "end" | null {
    if (!this.speaking) this.floor = this.floor * 0.95 + Math.min(rms, 0.05) * 0.05;
    const threshold = Math.max(0.015, this.floor * 3);
    if (!this.speaking) {
      this.above = rms > threshold ? this.above + this.stepMs : 0;
      if (this.above >= this.onsetMs) {
        this.speaking = true;
        this.below = 0;
        this.speechMs = this.above;
        return "start";
      }
      return null;
    }
    this.speechMs += this.stepMs;
    this.below = rms > threshold ? 0 : this.below + this.stepMs;
    if (this.below >= this.pauseMs || this.speechMs >= this.maxMs) {
      this.speaking = false;
      this.above = 0;
      return "end";
    }
    return null;
  }
}

/** A Recognition (the browser recognizer's shape) backed by the gateway's STT. */
export function serverRecognition(purpose: SttPurpose, token: () => string | null): RecognitionCtor {
  return class ServerRecognition implements Recognition {
    lang = "en-US";
    continuous = false;
    interimResults = false;
    onresult: Recognition["onresult"] = null;
    onend: (() => void) | null = null;
    onerror: ((e: { error: string }) => void) | null = null;
    private stream: MediaStream | null = null;
    private ctx: AudioContext | null = null;
    private timer: ReturnType<typeof setInterval> | null = null;
    private rec: MediaRecorder | null = null;
    private chunks: Blob[] = [];
    private startedAt = 0;
    private results: (ArrayLike<{ transcript: string }> & { isFinal: boolean })[] = [];
    private ended = false;
    private stopping = false;
    private pending = 0;

    start(): void {
      void this.open();
    }
    /** Finish the utterance being spoken (it is still sent), then end. */
    stop(): void {
      this.stopping = true;
      if (this.rec && this.rec.state === "recording") this.rec.stop();
      else this.finishIfIdle();
    }
    /** Drop everything and end now. */
    abort(): void {
      this.chunks = [];
      if (this.rec && this.rec.state === "recording") {
        this.rec.ondataavailable = null;
        this.rec.onstop = null;
        this.rec.stop();
      }
      this.end();
    }

    private async open() {
      try {
        this.stream = await navigator.mediaDevices.getUserMedia({ audio: { echoCancellation: true, noiseSuppression: true, autoGainControl: true } });
      } catch (e) {
        this.fail((e as { name?: string })?.name === "NotAllowedError" ? "not-allowed" : "audio-capture");
        return;
      }
      if (this.ended) return this.release();
      const ctx = new AudioContext();
      this.ctx = ctx;
      const src = ctx.createMediaStreamSource(this.stream);
      const an = ctx.createAnalyser();
      an.fftSize = 1024;
      src.connect(an);
      const buf = new Float32Array(an.fftSize);
      const gate = new LevelGate();
      this.timer = setInterval(() => {
        an.getFloatTimeDomainData(buf);
        let sum = 0;
        for (const v of buf) sum += v * v;
        const t = gate.step(Math.sqrt(sum / buf.length));
        if (t === "start") this.record();
        else if (t === "end" && this.rec?.state === "recording") this.rec.stop();
      }, 50);
    }

    private record() {
      if (!this.stream || this.stopping || this.ended) return;
      const type = recordingType((t) => MediaRecorder.isTypeSupported(t)) ?? "";
      const rec = new MediaRecorder(this.stream, type ? { mimeType: type } : undefined);
      this.rec = rec;
      this.chunks = [];
      this.startedAt = performance.now();
      rec.ondataavailable = (e) => e.data.size && this.chunks.push(e.data);
      rec.onstop = () => {
        const seconds = (performance.now() - this.startedAt) / 1000;
        const audio = new Blob(this.chunks, { type: rec.mimeType || type });
        this.chunks = [];
        this.rec = null;
        // Too short to be words (a cough, a click): nothing to send.
        if (seconds >= 0.4) void this.send(audio, seconds);
        if (!this.continuous) this.stopping = true;
        this.finishIfIdle();
      };
      rec.start();
    }

    private async send(audio: Blob, seconds: number) {
      this.pending += 1;
      try {
        const text = await transcribe(audio, seconds, purpose, token());
        if (text && !this.ended) {
          const i = this.results.length;
          this.results.push(Object.assign([{ transcript: text }], { isFinal: true }));
          this.onresult?.({ resultIndex: i, results: this.results });
        }
      } catch (e) {
        const status = e instanceof SttError ? e.status : 0;
        // 503: no STT here (the caller switches to the browser recognizer); 429: limited for now.
        this.fail(status === 429 ? STT_LIMITED : status === 401 ? "not-allowed-stt" : STT_UNAVAILABLE);
        return;
      } finally {
        this.pending -= 1;
      }
      this.finishIfIdle();
    }

    private finishIfIdle() {
      if (this.stopping && !this.rec && this.pending === 0) this.end();
    }
    private fail(error: string) {
      if (this.ended) return;
      this.onerror?.({ error });
      this.end();
    }
    private end() {
      if (this.ended) return;
      this.ended = true;
      this.release();
      this.onend?.();
    }
    private release() {
      if (this.timer) clearInterval(this.timer);
      this.timer = null;
      this.stream?.getTracks().forEach((t) => t.stop());
      this.stream = null;
      void this.ctx?.close().catch(() => {});
      this.ctx = null;
    }
  };
}
