// Speech output for /ceo and /talk (contract v1.10 §5c, design D30): POST /api/tts → audio/mpeg
// (ElevenLabs at the gateway), one line at a time. On a 503 (no voice key) or any failure the
// line is read by the browser's speechSynthesis instead. Listening stays with the pages
// (browser SpeechRecognition); they mute it while this plays (plus the echo tail).

export type TtsPurpose = "ceo" | "talk";

/** The gateway's text limits per purpose (§5c); longer text is split at sentences. */
export const TTS_MAX: Readonly<Record<TtsPurpose, number>> = { ceo: 1500, talk: 600 };
/** After a 503 the gateway isn't asked again for this long (the fallback voice is used). */
export const TTS_DOWN_MS = 60_000;
const FETCH_MS = 10_000;
/** A line that hasn't ended after ~this per word (plus 2 s) is treated as stalled. */
const MS_PER_WORD = 450;
const MAX_LINE_MS = 120_000;

/** Split text into pieces of at most `max` characters, at sentence ends where possible. */
export function ttsChunks(text: string, max: number): string[] {
  const clean = text.replace(/\s+/g, " ").trim();
  if (!clean) return [];
  if (clean.length <= max) return [clean];
  const out: string[] = [];
  let cur = "";
  for (const sentence of clean.split(/(?<=[.!?])\s+/)) {
    for (let piece = sentence; piece; ) {
      const room = max - (cur ? cur.length + 1 : 0);
      if (piece.length <= room) {
        cur = cur ? `${cur} ${piece}` : piece;
        piece = "";
      } else if (cur) {
        out.push(cur);
        cur = "";
      } else {
        // One sentence longer than the limit: cut at the last space that fits.
        const cut = piece.lastIndexOf(" ", max) > 0 ? piece.lastIndexOf(" ", max) : max;
        out.push(piece.slice(0, cut).trim());
        piece = piece.slice(cut).trim();
      }
    }
  }
  if (cur) out.push(cur);
  return out;
}

/** The stall limit for a line of text. */
export const lineLimitMs = (text: string) => Math.min(MAX_LINE_MS, 2000 + text.split(/\s+/).length * MS_PER_WORD);

/** What a Speaker needs from the browser (injected, so the queue is testable without one). */
export interface SpeakerDeps {
  fetch: typeof fetch;
  /** Play an MP3 blob; resolves true when it ended, false when it failed or was stopped. */
  playBlob: (blob: Blob, stop: AbortSignal) => Promise<boolean>;
  /** Read text with the browser's own voice; same contract. Null when there is none. */
  synth: ((text: string, stop: AbortSignal) => Promise<boolean>) | null;
  now: () => number;
  setTimeout: (fn: () => void, ms: number) => unknown;
  clearTimeout: (t: unknown) => void;
}

export interface SpeakerOptions {
  purpose: TtsPurpose;
  /** The gateway's base URL (…/api). */
  api: string;
  /** The admin token (purpose "ceo"); never in a URL. */
  token?: () => string | null;
  /** Something started or stopped playing (the page mutes its listening meanwhile). */
  onAudio?: (on: boolean) => void;
  deps?: Partial<SpeakerDeps>;
}

interface Line {
  text: string;
  /** Called once with whether every piece actually played to the end. */
  onDone?: (played: boolean) => void;
  pieces: string[];
  played: boolean;
}

/** Plays lines one at a time: the gateway's voice first, the browser's on a 503 or failure. */
export class Speaker {
  private queue: Line[] = [];
  private current: { line: Line; stop: AbortController } | null = null;
  private downUntil = 0;
  private readonly deps: SpeakerDeps;

  constructor(private readonly opts: SpeakerOptions) {
    this.deps = { ...browserDeps(), ...opts.deps };
  }

  /** Lines queued or playing. */
  get busy() {
    return this.current !== null || this.queue.length > 0;
  }

  /** Queue a line. `onDone(played)` runs once it finished (played: heard to the end, every piece). */
  say(text: string, onDone?: (played: boolean) => void) {
    const pieces = ttsChunks(text, TTS_MAX[this.opts.purpose]);
    if (!pieces.length) {
      onDone?.(true);
      return;
    }
    this.queue.push({ text, onDone, pieces, played: true });
    if (!this.current) {
      this.opts.onAudio?.(true);
      void this.pump();
    }
  }

  /** Stop now: the line playing and every queued one end as not heard. */
  stop() {
    const lines = [...(this.current ? [this.current.line] : []), ...this.queue];
    this.queue = [];
    if (this.current) {
      this.current.line.played = false;
      this.current.stop.abort();
    }
    this.current = null;
    for (const l of lines) l.onDone?.(false);
    if (lines.length) this.opts.onAudio?.(false);
  }

  private async pump(): Promise<void> {
    const line = this.queue.shift();
    if (!line) {
      this.opts.onAudio?.(false);
      return;
    }
    const stop = new AbortController();
    this.current = { line, stop };
    for (const piece of line.pieces) {
      if (stop.signal.aborted) break;
      const ok = await this.withWatchdog(piece, stop);
      if (!ok) line.played = false;
    }
    if (this.current?.line !== line) return; // stopped meanwhile (stop() already reported it)
    this.current = null;
    line.onDone?.(line.played);
    void this.pump();
  }

  /** One piece, never longer than its stall limit (a stalled voice is silenced and counts as not heard). */
  private withWatchdog(piece: string, stop: AbortController): Promise<boolean> {
    return new Promise((resolve) => {
      let settled = false;
      const pieceStop = new AbortController();
      const onOuter = () => pieceStop.abort();
      stop.signal.addEventListener("abort", onOuter);
      const finish = (ok: boolean) => {
        if (settled) return;
        settled = true;
        this.deps.clearTimeout(timer);
        stop.signal.removeEventListener("abort", onOuter);
        resolve(ok);
      };
      const timer = this.deps.setTimeout(() => {
        pieceStop.abort();
        finish(false);
      }, lineLimitMs(piece) + FETCH_MS);
      this.speakPiece(piece, pieceStop.signal).then(finish, () => finish(false));
    });
  }

  private async speakPiece(text: string, stop: AbortSignal): Promise<boolean> {
    if (this.deps.now() >= this.downUntil) {
      const blob = await this.fetchTts(text, stop);
      if (stop.aborted) return false;
      if (blob) return this.deps.playBlob(blob, stop);
    }
    return this.deps.synth ? this.deps.synth(text, stop) : true; // nothing can speak: the screen says it
  }

  /** POST /api/tts. Null on any failure; a 503 also rests the gateway voice for a minute. */
  private async fetchTts(text: string, stop: AbortSignal): Promise<Blob | null> {
    const headers: Record<string, string> = { "Content-Type": "application/json" };
    const token = this.opts.token?.();
    if (token) headers.Authorization = `Bearer ${token}`;
    try {
      const r = await this.deps.fetch(`${this.opts.api}/tts`, {
        method: "POST",
        headers,
        body: JSON.stringify({ text, purpose: this.opts.purpose }),
        cache: "no-store",
        signal: AbortSignal.any([stop, AbortSignal.timeout(FETCH_MS)]),
      });
      if (r.status === 503) this.downUntil = this.deps.now() + TTS_DOWN_MS;
      if (!r.ok) return null;
      const type = r.headers.get("content-type") ?? "";
      if (!/^audio\//.test(type)) return null;
      return await r.blob();
    } catch {
      return null;
    }
  }
}

/** The real browser: an Audio element for the MP3, speechSynthesis for the fallback. */
function browserDeps(): SpeakerDeps {
  const hasWindow = typeof window !== "undefined";
  return {
    fetch: (...a) => fetch(...a),
    playBlob: (blob, stop) =>
      new Promise((resolve) => {
        const url = URL.createObjectURL(blob);
        const a = new Audio(url);
        const end = (ok: boolean) => {
          a.onended = a.onerror = null;
          a.pause();
          URL.revokeObjectURL(url);
          resolve(ok);
        };
        a.onended = () => end(true);
        a.onerror = () => end(false);
        stop.addEventListener("abort", () => end(false), { once: true });
        a.play().catch(() => end(false)); // autoplay refused: not heard
      }),
    synth:
      hasWindow && "speechSynthesis" in window
        ? (text, stop) =>
            new Promise((resolve) => {
              const u = new SpeechSynthesisUtterance(text);
              let done = false;
              const end = (ok: boolean) => {
                if (done) return;
                done = true;
                resolve(ok);
              };
              u.onend = () => end(true);
              u.onerror = () => end(false);
              stop.addEventListener(
                "abort",
                () => {
                  if (window.speechSynthesis.speaking) window.speechSynthesis.cancel();
                  end(false);
                },
                { once: true },
              );
              window.speechSynthesis.speak(u);
            })
        : null,
    now: () => Date.now(),
    setTimeout: (fn, ms) => setTimeout(fn, ms),
    clearTimeout: (t) => clearTimeout(t as ReturnType<typeof setTimeout>),
  };
}
