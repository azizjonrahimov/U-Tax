// D36 (contract v1.16.1 §5c): reliable transcripts from the gateway's STT. The mic is recorded
// (MediaRecorder) continuously while the ear is open, so every utterance starts with a short
// pre-roll (the first syllable is never cut: "stop posting" must not become "posting"). A level
// detector that follows the room's noise floor marks where speech starts and where a pause ends
// it; each utterance is POSTed as the raw body to /api/stt. The text comes back as a final,
// exactly like the browser recognizer's, so every caller's ear logic (loop guards, echo filter,
// hands-free) is unchanged.
//
// Errors the caller answers (it never loses the session's voice over one bad clip):
// - STT_UNAVAILABLE: a 503 (no STT, no key, the spend cap): use the browser recognizer.
// - STT_LIMITED: a 429, or the network failing twice: fall back for a little while.
// - STT_UNREADABLE: the gateway couldn't read that clip (400/413/415): "say it again", stay here.
// - STT_NOISY: the room never goes quiet (a TV, music): stop sending it, the caller falls back.
// - "no-speech": a tap with nothing said (the browser recognizer's own quiet error).
// - "audio-capture" / "not-allowed": the mic is gone or refused.

import { API_URL } from "./api";
import type { Recognition, RecognitionCtor } from "./recognition";

export type SttPurpose = "ceo" | "talk";
/** The gateway's limits: one utterance ≤ 30 s and ≤ 1 MB. */
export const STT_MAX_SECONDS = 30;
const STT_MAX_BYTES = 1_000_000;
export const STT_UNAVAILABLE = "stt-unavailable";
export const STT_LIMITED = "stt-rate-limited";
export const STT_UNREADABLE = "stt-unreadable";
export const STT_NOISY = "stt-noisy";

export class SttError extends Error {
  constructor(
    readonly status: number,
    message: string,
  ) {
    super(message);
  }
}

/** A timeout signal, also where AbortSignal.timeout doesn't exist (Safari before 16). */
function timeoutSignal(ms: number, outer?: AbortSignal): AbortSignal {
  const ac = new AbortController();
  const t = setTimeout(() => ac.abort(), ms);
  outer?.addEventListener("abort", () => {
    clearTimeout(t);
    ac.abort();
  });
  return ac.signal;
}

/** One utterance → its text. Throws SttError (its status: 503, 429, 400/413/415, 0 for the network). */
export async function transcribe(audio: Blob, seconds: number, purpose: SttPurpose, token: string | null, fetchImpl: typeof fetch = fetch, signal?: AbortSignal): Promise<string> {
  if (audio.size > STT_MAX_BYTES) throw new SttError(413, "too long");
  const s = Math.max(1, Math.min(STT_MAX_SECONDS, Math.ceil(seconds)));
  const headers: Record<string, string> = { "Content-Type": (audio.type || "audio/webm").split(";")[0] };
  if (purpose === "ceo" && token) headers.Authorization = `Bearer ${token}`;
  let r: Response;
  try {
    // A spoken STOP can't wait on a slow provider: 10 s, then the caller tells the CEO.
    r = await fetchImpl(`${API_URL}/api/stt?purpose=${purpose}&seconds=${s}`, { method: "POST", headers, body: audio, cache: "no-store", signal: timeoutSignal(10_000, signal) });
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
 * The utterance detector, pure: fed a level reading (RMS, 0..1) and the time since the last one,
 * it says when speech starts and when a pause ends it. The threshold follows the room's noise
 * floor (slowly, even while speaking, so a TV turned up mid-sentence doesn't hold it open).
 * `voicedMs`: how long the last utterance was above the threshold (a cough isn't words).
 */
export class LevelGate {
  private floor = 0.005;
  /** The first moments only learn the room (a fan or a TV is never mistaken for speech). */
  private warm = 0;
  private above = 0;
  private below = 0;
  speaking = false;
  voicedMs = 0;
  constructor(
    private readonly onsetMs = 120,
    private readonly pauseMs = 700,
  ) {}
  /** A clip was cut: the next utterance starts fresh (what was learned about the room stays). */
  reset() {
    this.speaking = false;
    this.above = 0;
    this.below = 0;
  }
  /** "start", "end" or null for this reading. */
  step(rms: number, dtMs: number): "start" | "end" | null {
    const dt = Math.max(0, Math.min(dtMs, 250));
    if (this.warm < 600) {
      this.warm += Math.max(0, dtMs);
      this.floor = this.floor * 0.7 + Math.min(rms, 0.08) * 0.3;
      return null;
    }
    this.floor = this.speaking ? this.floor * 0.998 + Math.min(rms, 0.08) * 0.002 : this.floor * 0.95 + Math.min(rms, 0.08) * 0.05;
    const threshold = Math.max(0.008, this.floor * 3);
    const loud = rms > threshold;
    if (!this.speaking) {
      this.above = loud ? this.above + dt : 0;
      if (this.above >= this.onsetMs) {
        this.speaking = true;
        this.below = 0;
        this.voicedMs = this.above;
        return "start";
      }
      return null;
    }
    if (loud) {
      this.below = 0;
      this.voicedMs += dt;
    } else this.below += dt;
    if (this.below >= this.pauseMs) {
      this.speaking = false;
      this.above = 0;
      return "end";
    }
    return null;
  }
}

/**
 * Pre-roll: two recorders overlap while it's quiet, a new one every ROTATE_MS, and the older one
 * becomes the utterance's clip at onset, so every clip starts at least this long before the
 * speech (a restart can never land right on the first syllable).
 */
const ROTATE_MS = 1500;
/** One clip, pre-roll included, never runs past this (the gateway's 30 s, with margin). */
const CLIP_MAX_MS = 28_000;
/** Shorter than this voiced is a cough or a click: not sent. */
const MIN_VOICED_MS = 200;
/** A tap with nothing said ends here (the browser recognizer's "no-speech"). */
const NO_SPEECH_MS = 8000;
/** This many clips in a row that ran to the cap (or came back empty): the room never goes quiet. */
const NOISY_IN_A_ROW = 4;

/** A recorder whose audio isn't wanted: its handlers off, stopped. */
function drop(rec: MediaRecorder) {
  rec.ondataavailable = null;
  rec.onstop = null;
  rec.onerror = null;
  if (rec.state !== "inactive") rec.stop();
}

/** A Recognition (the browser recognizer's shape) backed by the gateway's STT. */
export function serverRecognition(purpose: SttPurpose, token: () => string | null): RecognitionCtor {
  return class ServerRecognition implements Recognition {
    /** Callers close this ear with stop() (it still delivers what was said), never abort(). */
    readonly server = true;
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
    private clipStart = 0;
    /** The newer of the two overlapping recorders while it's quiet. */
    private spare: { rec: MediaRecorder; at: number } | null = null;
    private gate = new LevelGate();
    private heardSpeech = false;
    private opened = 0;
    private results: (ArrayLike<{ transcript: string }> & { isFinal: boolean })[] = [];
    private ended = false;
    private closing = false;
    private pending = 0;
    private uploads = new AbortController();
    private noisy = 0;
    private netFails = 0;

    start(): void {
      void this.open();
    }
    /** Close the mic now; what was already said is still sent and delivered, then end. */
    stop(): void {
      if (this.closing || this.ended) return;
      this.closing = true;
      this.cut(true);
      this.releaseMic();
      this.finishIfIdle();
    }
    /** Drop everything (recording and uploads) and end now. */
    abort(): void {
      this.uploads.abort();
      this.detachRecorder();
      this.end();
    }

    private async open() {
      try {
        this.stream = await navigator.mediaDevices.getUserMedia({ audio: { echoCancellation: true, noiseSuppression: true, autoGainControl: true } });
      } catch (e) {
        this.fail((e as { name?: string })?.name === "NotAllowedError" ? "not-allowed" : "audio-capture");
        return;
      }
      if (this.ended || this.closing) return this.releaseMic();
      // A mic unplugged or taken by the system: say so (the caller restarts or falls back).
      for (const t of this.stream.getAudioTracks()) t.onended = () => this.fail("audio-capture");
      const ctx = new AudioContext();
      this.ctx = ctx;
      try {
        await ctx.resume();
      } catch {
        /* checked below */
      }
      if (ctx.state !== "running") return this.fail("audio-capture"); // a suspended context hears nothing
      if (this.ended || this.closing || !this.stream) return this.releaseMic();
      const src = ctx.createMediaStreamSource(this.stream);
      const an = ctx.createAnalyser();
      an.fftSize = 1024;
      src.connect(an);
      const buf = new Float32Array(an.fftSize);
      this.opened = performance.now();
      this.startClip();
      let last = performance.now();
      this.timer = setInterval(() => {
        const now = performance.now();
        an.getFloatTimeDomainData(buf);
        let sum = 0;
        for (const v of buf) sum += v * v;
        const t = this.gate.step(Math.sqrt(sum / buf.length), now - last);
        last = now;
        if (t === "start") {
          // The older recorder (with its pre-roll) is this utterance's clip; the newer one goes.
          this.heardSpeech = true;
          this.dropSpare();
        } else if (t === "end") this.cut(false);
        else if (this.gate.speaking && now - this.clipStart >= CLIP_MAX_MS) this.cut(false, true);
        else if (!this.gate.speaking) this.rotate(now);
        if (!this.continuous && !this.heardSpeech && now - this.opened >= NO_SPEECH_MS) this.fail("no-speech");
      }, 50);
    }

    private newRecorder(): MediaRecorder | null {
      if (!this.stream || this.ended || this.closing) return null;
      const type = recordingType((t) => MediaRecorder.isTypeSupported(t)) ?? "";
      const rec = new MediaRecorder(this.stream, type ? { mimeType: type } : undefined);
      const chunks: Blob[] = [];
      rec.ondataavailable = (e) => e.data.size && chunks.push(e.data);
      rec.onerror = () => this.fail("audio-capture");
      (rec as MediaRecorder & { chunks?: Blob[] }).chunks = chunks;
      rec.start();
      return rec;
    }

    /** A new clip (whatever was recording, silence only, is dropped). */
    private startClip() {
      this.detachRecorder();
      const rec = this.newRecorder();
      if (!rec) return;
      this.rec = rec;
      this.clipStart = performance.now();
    }

    /** While it's quiet: keep the clip's silence short, with a newer recorder always overlapping. */
    private rotate(now: number) {
      if (!this.rec) return;
      if (!this.spare) {
        if (now - this.clipStart >= ROTATE_MS) {
          const rec = this.newRecorder();
          if (rec) this.spare = { rec, at: now };
        }
        return;
      }
      if (now - this.spare.at >= ROTATE_MS) {
        drop(this.rec);
        this.rec = this.spare.rec;
        this.clipStart = this.spare.at;
        this.spare = null;
      }
    }

    private dropSpare() {
      if (this.spare) drop(this.spare.rec);
      this.spare = null;
    }

    /**
     * The current clip ends: send it if it holds speech (`force`: the CEO let go, send whatever
     * was said), then (still listening) start the next one.
     */
    private cut(force: boolean, capped = false) {
      const rec = this.rec;
      if (!rec) return;
      this.dropSpare();
      const speech = this.heardSpeech && (force || this.gate.voicedMs >= MIN_VOICED_MS);
      const chunks = (rec as MediaRecorder & { chunks?: Blob[] }).chunks ?? [];
      const seconds = (performance.now() - this.clipStart) / 1000;
      this.rec = null;
      if (speech) {
        // Counted from now: the ear doesn't end before this clip has been sent.
        this.pending += 1;
        rec.onstop = () => {
          this.pending -= 1;
          if (this.ended) return;
          void this.send(new Blob(chunks, { type: rec.mimeType || "audio/webm" }), seconds, capped);
        };
      } else {
        rec.ondataavailable = null;
        rec.onstop = null;
      }
      if (rec.state !== "inactive") rec.stop();
      this.gate.reset();
      const hadSpeech = this.heardSpeech;
      this.heardSpeech = false;
      if (force && !hadSpeech && !this.continuous) {
        this.onerror?.({ error: "no-speech" });
      }
      // A tap ends with its utterance; hands-free keeps listening.
      if (!this.continuous && hadSpeech) this.closing = true;
      if (this.closing) {
        this.releaseMic();
        this.finishIfIdle();
      } else this.startClip();
    }

    private async send(audio: Blob, seconds: number, capped: boolean) {
      this.pending += 1;
      try {
        let text = "";
        try {
          text = await transcribe(audio, seconds, purpose, token(), fetch, this.uploads.signal);
        } catch (e) {
          // One network blip: try once more before falling back.
          if (!(e instanceof SttError) || e.status !== 0 || this.uploads.signal.aborted) throw e;
          text = await transcribe(audio, seconds, purpose, token(), fetch, this.uploads.signal);
        }
        this.netFails = 0;
        this.noisy = capped || !text ? this.noisy + 1 : 0;
        if (text && !this.ended) {
          const i = this.results.length;
          this.results.push(Object.assign([{ transcript: text }], { isFinal: true }));
          this.onresult?.({ resultIndex: i, results: this.results });
        }
        if (this.noisy >= NOISY_IN_A_ROW) return this.fail(STT_NOISY);
      } catch (e) {
        if (this.ended || this.uploads.signal.aborted) return;
        const status = e instanceof SttError ? e.status : 0;
        if (status === 400 || status === 413 || status === 415) this.onerror?.({ error: STT_UNREADABLE }); // that clip only
        else this.fail(status === 503 ? STT_UNAVAILABLE : status === 401 ? "not-allowed-stt" : STT_LIMITED);
      } finally {
        this.pending -= 1;
        this.finishIfIdle();
      }
    }

    private finishIfIdle() {
      if (this.closing && !this.rec && this.pending === 0) this.end();
    }
    private fail(error: string) {
      if (this.ended) return;
      this.onerror?.({ error });
      this.uploads.abort();
      this.detachRecorder();
      this.end();
    }
    private detachRecorder() {
      const rec = this.rec;
      this.rec = null;
      this.dropSpare();
      if (rec) drop(rec);
    }
    private end() {
      if (this.ended) return;
      this.ended = true;
      this.releaseMic();
      this.onend?.();
    }
    private releaseMic() {
      if (this.timer) clearInterval(this.timer);
      this.timer = null;
      this.stream?.getTracks().forEach((t) => {
        t.onended = null;
        t.stop();
      });
      this.stream = null;
      void this.ctx?.close().catch(() => {});
      this.ctx = null;
    }
  };
}
