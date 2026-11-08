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
// - STT_RECORD_FAILED: this browser couldn't record for the STT (an audio context that won't run,
//   a recorder error): the caller falls back to the browser recognizer (not a dead mic).
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
export const STT_RECORD_FAILED = "stt-record-failed";

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
 * - The first 600 ms learn the room from its quietest readings, so speech that starts right
 *   away is never learned as "the room".
 * - `voicedMs`: how long the current utterance was above the bar (a cough isn't words);
 *   `loudMs`: how long anything was above the bar since the last reset (a forced cut sends a
 *   clip that held speech even if no onset was declared yet).
 */
export class LevelGate {
  private floor = Infinity;
  private warm = 0;
  private above = 0;
  private below = 0;
  speaking = false;
  voicedMs = 0;
  loudMs = 0;
  constructor(
    private readonly onsetMs = 120,
    private readonly pauseMs = 700,
  ) {}
  /** A clip was cut: the next utterance starts fresh (what was learned about the room stays). */
  reset() {
    this.speaking = false;
    this.above = 0;
    this.below = 0;
    this.voicedMs = 0;
    this.loudMs = 0;
  }
  private bar() {
    return Math.max(0.008, (Number.isFinite(this.floor) ? this.floor : 0.003) * 3);
  }
  /** "start", "end" or null for this reading. */
  step(rms: number, dtMs: number): "start" | "end" | null {
    const dt = Math.max(0, Math.min(dtMs, 250));
    const level = Math.min(rms, 0.08);
    if (this.warm < 600) {
      this.warm += Math.max(0, dtMs);
      this.floor = Math.min(this.floor, level); // the room is its quietest moment, never the voice
      if (rms > this.bar()) {
        this.loudMs += dt;
        this.above += dt; // speech already under way counts toward the onset
      } else this.above = 0;
      return null;
    }
    this.floor = this.speaking ? this.floor * 0.998 + level * 0.002 : this.floor * 0.95 + level * 0.05;
    const loud = rms > this.bar();
    if (loud) this.loudMs += dt;
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
/** A forced cut (the CEO let go) sends a clip that was loud at least this long, onset or not. */
const MIN_LOUD_MS = 150;
/** A tap with nothing said ends here (the browser recognizer's "no-speech"). */
const NO_SPEECH_MS = 8000;
/** This many clips in a row that ran to the cap (or came back empty): the room never goes quiet. */
const NOISY_IN_A_ROW = 4;
/** An audio context that won't start within this long won't start (outside a user gesture). */
const RESUME_MS = 1500;

type Clip = { rec: MediaRecorder; chunks: Blob[]; at: number };

/**
 * v1.18.4: the gateway books an upload at max(declared seconds, its size at a floor bitrate: 6 kbps
 * for webm/opus, 8 kbps for mp4), so speech is recorded at 8 kbps (an honest Opus clip books ~1.3x
 * its length, not ~5x). A browser that won't take the bitrate records at its default (it may clamp
 * it silently: Safari's AAC is not measured yet).
 */
const SPEECH_BPS = 8000;
function recorder(stream: MediaStream, type: string): MediaRecorder {
  try {
    return new MediaRecorder(stream, { ...(type ? { mimeType: type } : {}), audioBitsPerSecond: SPEECH_BPS });
  } catch {
    return new MediaRecorder(stream, type ? { mimeType: type } : undefined);
  }
}

/** A recorder whose audio isn't wanted: its handlers off, stopped. */
function drop(c: Clip) {
  c.rec.ondataavailable = null;
  c.rec.onstop = null;
  c.rec.onerror = null;
  try {
    if (c.rec.state !== "inactive") c.rec.stop();
  } catch {
    /* already gone */
  }
}

/**
 * A Recognition (the browser recognizer's shape) backed by the gateway's STT. Beyond the shape:
 * `server` (callers close it with stop(), which still delivers what was said) and `onmicclosed`
 * (the mic is released and only uploads remain: a new ear may open while this one delivers).
 */
export function serverRecognition(purpose: SttPurpose, token: () => string | null): RecognitionCtor {
  return class ServerRecognition implements Recognition {
    readonly server = true;
    lang = "en-US";
    continuous = false;
    interimResults = false;
    onresult: Recognition["onresult"] = null;
    onend: (() => void) | null = null;
    onerror: ((e: { error: string }) => void) | null = null;
    onmicclosed: (() => void) | null = null;
    /** D41 F47: an utterance's onset (the level gate's "start"), like the browser recognizer's own onspeechstart. */
    onspeechstart: (() => void) | null = null;
    private stream: MediaStream | null = null;
    private ctx: AudioContext | null = null;
    private timer: ReturnType<typeof setInterval> | null = null;
    private clip: Clip | null = null;
    private spare: Clip | null = null;
    private gate = new LevelGate();
    private heardSpeech = false;
    private opened = 0;
    private results: (ArrayLike<{ transcript: string }> & { isFinal: boolean })[] = [];
    private ended = false;
    private closing = false;
    private micClosed = false;
    private pending = 0;
    private uploads = new AbortController();
    private noisy = 0;
    /** In-order delivery: each sent clip gets a number; results go out in that order. */
    private nextSeq = 0;
    private nextOut = 0;
    private done = new Map<number, string | null>();

    start(): void {
      void this.open().catch(() => this.fail(STT_RECORD_FAILED));
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
      this.dropAll();
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
      // resume() can wait forever outside a user gesture: give it a moment, then fall back.
      await Promise.race([ctx.resume().catch(() => {}), new Promise((r) => setTimeout(r, RESUME_MS))]);
      if (this.ended || this.closing || !this.stream) return this.releaseMic();
      if (ctx.state !== "running") return this.fail(STT_RECORD_FAILED);
      const src = ctx.createMediaStreamSource(this.stream);
      const an = ctx.createAnalyser();
      an.fftSize = 1024;
      src.connect(an);
      const buf = new Float32Array(an.fftSize);
      this.opened = performance.now();
      if (!this.startClip()) return;
      let last = performance.now();
      this.timer = setInterval(() => {
        try {
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
            try {
              this.onspeechstart?.();
            } catch {
              /* a listener's error never stops the recorder */
            }
          } else if (t === "end") this.cut(false);
          else if (this.gate.speaking && this.clip && now - this.clip.at >= CLIP_MAX_MS) this.cut(false, true);
          else if (!this.gate.speaking) this.rotate(now);
          if (!this.continuous && !this.heardSpeech && this.gate.loudMs < MIN_LOUD_MS && now - this.opened >= NO_SPEECH_MS) this.fail("no-speech");
        } catch {
          this.fail(STT_RECORD_FAILED);
        }
      }, 50);
    }

    private newClip(): Clip | null {
      if (!this.stream || this.ended || this.closing) return null;
      try {
        const type = recordingType((t) => MediaRecorder.isTypeSupported(t)) ?? "";
        const rec = recorder(this.stream, type);
        const c: Clip = { rec, chunks: [], at: performance.now() };
        rec.ondataavailable = (e) => e.data.size && c.chunks.push(e.data);
        rec.onerror = () => this.fail(STT_RECORD_FAILED);
        rec.start();
        return c;
      } catch {
        this.fail(STT_RECORD_FAILED);
        return null;
      }
    }

    /** A new clip (whatever was recording, silence only, is dropped). */
    private startClip(): boolean {
      this.dropAll();
      this.clip = this.newClip();
      return !!this.clip;
    }

    /** While it's quiet: keep the clip's silence short, with a newer recorder always overlapping. */
    private rotate(now: number) {
      if (!this.clip) return;
      if (!this.spare) {
        if (now - this.clip.at >= ROTATE_MS) this.spare = this.newClip();
        return;
      }
      if (now - this.spare.at >= ROTATE_MS) {
        drop(this.clip);
        this.clip = this.spare;
        this.spare = null;
        this.gate.loudMs = 0; // the loudness tally describes the clip being kept
      }
    }

    private dropSpare() {
      if (this.spare) drop(this.spare);
      this.spare = null;
    }
    private dropAll() {
      this.dropSpare();
      if (this.clip) drop(this.clip);
      this.clip = null;
    }

    /**
     * The current clip ends: send it if it holds speech (`force`: the CEO let go, so send what was
     * said even if no onset was declared yet), then (still listening) start the next one.
     */
    private cut(force: boolean, capped = false) {
      const c = this.clip;
      if (!c) return;
      this.dropSpare();
      this.clip = null;
      const speech = (this.heardSpeech && (force || this.gate.voicedMs >= MIN_VOICED_MS)) || (force && this.gate.loudMs >= MIN_LOUD_MS);
      const seconds = (performance.now() - c.at) / 1000;
      if (speech) {
        // Counted from now: the ear doesn't end before this clip has been sent.
        const seq = this.nextSeq++;
        this.pending += 1;
        const go = () => {
          this.pending -= 1;
          if (this.ended) return;
          void this.send(new Blob(c.chunks, { type: c.rec.mimeType || "audio/webm" }), seconds, capped, seq);
        };
        if (c.rec.state === "inactive") go(); // it already stopped by itself: its audio is all here
        else {
          c.rec.onstop = go;
          c.rec.stop();
        }
      } else drop(c);
      this.gate.reset();
      this.heardSpeech = false;
      if (force && !speech && !this.continuous) this.onerror?.({ error: "no-speech" });
      // A tap ends with its utterance (one actually sent; a cough keeps it listening); hands-free goes on.
      if (!this.continuous && speech) this.closing = true;
      if (this.closing) {
        this.releaseMic();
        this.finishIfIdle();
      } else this.startClip();
    }

    private async send(audio: Blob, seconds: number, capped: boolean, seq: number) {
      this.pending += 1;
      let text: string | null = null;
      try {
        try {
          text = await transcribe(audio, seconds, purpose, token(), fetch, this.uploads.signal);
        } catch (e) {
          // One network blip: try once more before falling back.
          if (!(e instanceof SttError) || e.status !== 0 || this.uploads.signal.aborted) throw e;
          text = await transcribe(audio, seconds, purpose, token(), fetch, this.uploads.signal);
        }
        this.noisy = capped || !text ? this.noisy + 1 : 0;
      } catch (e) {
        if (!this.ended && !this.uploads.signal.aborted) {
          const status = e instanceof SttError ? e.status : 0;
          if (status === 400 || status === 413 || status === 415) this.onerror?.({ error: STT_UNREADABLE }); // that clip only
          else {
            this.deliver(seq, null);
            this.pending -= 1;
            this.fail(status === 503 ? STT_UNAVAILABLE : status === 401 ? "not-allowed-stt" : STT_LIMITED);
            return;
          }
        }
      }
      this.deliver(seq, text);
      this.pending -= 1;
      if (this.noisy >= NOISY_IN_A_ROW) return this.fail(STT_NOISY);
      this.finishIfIdle();
    }

    /** Results go out in the order the clips were spoken (an upload can finish out of order). */
    private deliver(seq: number, text: string | null) {
      this.done.set(seq, text);
      while (this.done.has(this.nextOut)) {
        const t = this.done.get(this.nextOut) ?? null;
        this.done.delete(this.nextOut);
        this.nextOut += 1;
        if (t && !this.ended) {
          const i = this.results.length;
          this.results.push(Object.assign([{ transcript: t }], { isFinal: true }));
          this.onresult?.({ resultIndex: i, results: this.results });
        }
      }
    }

    private finishIfIdle() {
      if (this.closing && !this.clip && this.pending === 0) this.end();
    }
    private fail(error: string) {
      if (this.ended) return;
      this.onerror?.({ error });
      this.uploads.abort();
      this.dropAll();
      this.end();
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
      if (!this.micClosed) {
        this.micClosed = true;
        this.onmicclosed?.();
      }
    }
  };
}
