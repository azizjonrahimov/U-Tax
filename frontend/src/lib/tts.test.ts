import { describe, expect, it } from "vitest";
import { lineLimitMs, Speaker, TTS_DOWN_MS, TTS_MAX, ttsChunks, type SpeakerDeps } from "./tts";

const flush = () => new Promise((r) => setTimeout(r, 0));

/** A fake browser: records what was fetched and played; playback ends when the test says so. */
function fakeDeps(status: number | "throw" = 200, type = "audio/mpeg") {
  const calls: { url: string; init: RequestInit }[] = [];
  const played: string[] = [];
  const spoken: string[] = [];
  const pending: ((ok: boolean | "refused") => void)[] = [];
  const timers: (() => void)[] = [];
  const signals: AbortSignal[] = [];
  let clock = 1_000;
  const deps: SpeakerDeps = {
    fetch: (async (url: string, init: RequestInit) => {
      calls.push({ url, init });
      if (status === "throw") throw new TypeError("network down");
      const body = JSON.parse(String(init.body));
      return new Response(status === 200 ? new Blob([body.text ?? body.phrase]) : null, { status, headers: { "content-type": type } });
    }) as unknown as typeof fetch,
    playBlob: (blob, stop) =>
      new Promise((resolve) => {
        signals.push(stop);
        void blob.text().then((t) => played.push(t));
        pending.push(resolve);
      }),
    synth: (text, stop) =>
      new Promise((resolve) => {
        signals.push(stop);
        spoken.push(text);
        pending.push((ok) => resolve(ok === true));
      }),
    now: () => clock,
    setTimeout: (fn) => timers.push(fn),
    clearTimeout: () => {},
  };
  return { deps, calls, played, spoken, pending, timers, signals, tick: (ms: number) => (clock += ms) };
}
const settle = async () => {
  for (let i = 0; i < 4; i++) await flush();
};

describe("ttsChunks", () => {
  it("splits without a regex lookbehind (older Safari can't parse one; this ships on /talk)", () => {
    expect(ttsChunks("One. Two! Three?", 8)).toEqual(["One.", "Two!", "Three?"]);
    expect(ttsChunks("No end mark here", 600)).toEqual(["No end mark here"]);
  });

  it("keeps short text whole and splits long text at sentences, never past the limit", () => {
    expect(ttsChunks("  Hello   there. ", 600)).toEqual(["Hello there."]);
    expect(ttsChunks("", 600)).toEqual([]);
    const long = Array.from({ length: 40 }, (_, i) => `Sentence number ${i} is here.`).join(" ");
    const parts = ttsChunks(long, TTS_MAX.talk);
    expect(parts.length).toBeGreaterThan(1);
    expect(parts.every((p) => p.length <= TTS_MAX.talk)).toBe(true);
    expect(parts.join(" ")).toBe(long);
    const run = "word ".repeat(400).trim(); // one sentence over the limit: cut at a space
    expect(ttsChunks(run, 600).every((p) => p.length <= 600 && !p.startsWith(" "))).toBe(true);
  });

  it("a stall limit grows with the words", () => {
    expect(lineLimitMs("one two three")).toBe(2000 + 3 * 450);
    expect(lineLimitMs("x ".repeat(10_000))).toBe(120_000);
  });
});

describe("Speaker", () => {
  it("speaks with the gateway's voice: POST /api/tts {text, purpose}, admin token only as a Bearer header", async () => {
    const f = fakeDeps();
    const audio: boolean[] = [];
    const s = new Speaker({ purpose: "ceo", api: "https://gw.example/api", token: () => "tok", onAudio: (on) => audio.push(on), deps: f.deps });
    let heard: boolean | null = null;
    s.say("Approve the memo. Say confirm.", (p) => (heard = p));
    await flush();
    expect(f.calls[0].url).toBe("https://gw.example/api/tts");
    expect(JSON.parse(String(f.calls[0].init.body))).toEqual({ text: "Approve the memo. Say confirm.", purpose: "ceo" });
    expect((f.calls[0].init.headers as Record<string, string>).Authorization).toBe("Bearer tok");
    expect(f.calls[0].url).not.toContain("tok");
    await flush();
    expect(f.played).toEqual(["Approve the memo. Say confirm."]);
    expect(s.busy).toBe(true);
    f.pending.shift()!(true);
    await flush();
    await flush();
    expect(heard).toBe(true);
    expect(audio).toEqual([true, false]);
    expect(s.busy).toBe(false);
  });

  it("/talk sends a registry key and numbers, never free text, and no token (v1.10.2)", async () => {
    const f = fakeDeps();
    new Speaker({ purpose: "talk", api: "/api", deps: f.deps }).say("Your estimated tax is $450.", undefined, { phrase: "estimate.authorized", args: { total_cents: 45000 } });
    await flush();
    expect((f.calls[0].init.headers as Record<string, string>).Authorization).toBeUndefined();
    expect(JSON.parse(String(f.calls[0].init.body))).toEqual({ purpose: "talk", phrase: "estimate.authorized", args: { total_cents: 45000 } });
  });

  it("/talk without a registry key never reaches the gateway: the browser's voice reads it", async () => {
    const f = fakeDeps();
    new Speaker({ purpose: "talk", api: "/api", deps: f.deps }).say("A human at U-Tax is taking a look.");
    await settle();
    expect(f.calls).toHaveLength(0);
    expect(f.spoken).toEqual(["A human at U-Tax is taking a look."]);
  });

  it("on 503 the browser reads it, and the gateway isn't asked again for a minute", async () => {
    const f = fakeDeps(503, "application/json");
    const s = new Speaker({ purpose: "ceo", api: "/api", deps: f.deps });
    s.say("First line.");
    await flush();
    await flush();
    expect(f.spoken).toEqual(["First line."]);
    f.pending.shift()!(true);
    await flush();
    s.say("Second line.");
    await flush();
    await flush();
    expect(f.calls).toHaveLength(1); // resting
    expect(f.spoken).toEqual(["First line.", "Second line."]);
    f.pending.shift()!(true);
    await flush();
    f.tick(TTS_DOWN_MS);
    s.say("Third line.");
    await flush();
    expect(f.calls).toHaveLength(2); // asked again after the rest
  });

  it("an answer that isn't audio falls back to the browser's voice", async () => {
    const f = fakeDeps(200, "text/html");
    new Speaker({ purpose: "talk", api: "/api", deps: f.deps }).say("Hello.");
    await flush();
    await flush();
    expect(f.played).toEqual([]);
    expect(f.spoken).toEqual(["Hello."]);
  });

  it("plays lines one after another, never over each other", async () => {
    const f = fakeDeps();
    const s = new Speaker({ purpose: "ceo", api: "/api", deps: f.deps });
    s.say("One.");
    s.say("Two.");
    await flush();
    await flush();
    expect(f.calls).toHaveLength(1);
    f.pending.shift()!(true);
    await flush();
    await flush();
    expect(f.calls).toHaveLength(2);
  });

  it("a line that didn't play to the end is reported as not heard; stop() ends everything as not heard", async () => {
    const f = fakeDeps();
    const audio: boolean[] = [];
    const s = new Speaker({ purpose: "ceo", api: "/api", onAudio: (on) => audio.push(on), deps: f.deps });
    const results: boolean[] = [];
    s.say("Cut off.", (p) => results.push(p));
    await flush();
    await flush();
    f.pending.shift()!(false);
    await flush();
    await flush();
    s.say("A.", (p) => results.push(p));
    s.say("B.", (p) => results.push(p));
    await flush();
    s.stop();
    expect(results).toEqual([false, false, false]);
    expect(s.busy).toBe(false);
    expect(audio.at(-1)).toBe(false);
  });

  it.each([
    ["a network failure", "throw" as const, 2],
    ["a 401 (a stale token)", 401, 2],
    ["a 500", 500, 2],
    ["a 429 (the talk budget is spent): rests like a 503", 429, 1],
  ])("on %s the browser reads it", async (_why, status, callsAfterTwoLines) => {
    const f = fakeDeps(status);
    const s = new Speaker({ purpose: "ceo", api: "/api", deps: f.deps });
    const heard: boolean[] = [];
    s.say("One.", (p) => heard.push(p));
    await settle();
    expect(f.spoken).toEqual(["One."]);
    f.pending.shift()!(true);
    await settle();
    expect(heard).toEqual([true]);
    s.say("Two.");
    await settle();
    expect(f.calls).toHaveLength(callsAfterTwoLines);
  });

  it("a stalled piece is silenced, counts as not heard, and the queue moves on", async () => {
    const f = fakeDeps();
    const s = new Speaker({ purpose: "ceo", api: "/api", deps: f.deps });
    const heard: boolean[] = [];
    s.say("Stalls.", (p) => heard.push(p));
    s.say("Next.");
    await settle();
    f.timers[0](); // the watchdog
    await settle();
    expect(heard).toEqual([false]);
    expect(f.signals[0].aborted).toBe(true);
    expect(f.calls).toHaveLength(2);
  });

  it("the browser refusing to play (autoplay rules) falls back to its own voice", async () => {
    const f = fakeDeps();
    const s = new Speaker({ purpose: "talk", api: "/api", deps: f.deps });
    const heard: boolean[] = [];
    s.say("Hello.", (p) => heard.push(p), { phrase: "question.1" });
    await settle();
    f.pending.shift()!("refused");
    await settle();
    expect(f.spoken).toEqual(["Hello."]);
    f.pending.shift()!(true);
    await settle();
    expect(heard).toEqual([true]);
  });

  it("stop then say: the stopped line's late end never touches the new one", async () => {
    const f = fakeDeps();
    const audio: boolean[] = [];
    const s = new Speaker({ purpose: "ceo", api: "/api", onAudio: (on) => audio.push(on), deps: f.deps });
    const heard: string[] = [];
    s.say("One.", (p) => heard.push(`one:${p}`));
    await settle();
    s.stop();
    s.say("Two.", (p) => heard.push(`two:${p}`));
    await settle();
    f.pending.shift()!(true); // One's audio ends late
    await settle();
    expect(heard).toEqual(["one:false"]);
    expect(s.busy).toBe(true);
    expect(f.calls).toHaveLength(2);
    f.pending.shift()!(true);
    await settle();
    expect(heard).toEqual(["one:false", "two:true"]);
    expect(audio).toEqual([true, false, true, false]);
  });

  it("a long line plays its pieces in order; one failed piece makes the line not heard", async () => {
    const f = fakeDeps();
    const s = new Speaker({ purpose: "ceo", api: "/api", deps: f.deps });
    const long = Array.from({ length: 30 }, (_, i) => `This is sentence ${i} of the long readback.`).join(" ");
    let heard: boolean | null = null;
    s.say(long, (p) => (heard = p));
    await settle();
    f.pending.shift()!(false);
    await settle();
    expect(f.calls.length).toBe(2); // the next piece still plays
    while (f.pending.length) {
      f.pending.shift()!(true);
      await settle();
    }
    expect(f.calls.length).toBeGreaterThanOrEqual(3);
    expect(heard).toBe(false);
    expect(f.calls.every((c) => JSON.parse(String(c.init.body)).text.length <= 600)).toBe(true);
  });

  it("a say() made from inside onDone queues behind the next line instead of running two at once", async () => {
    const f = fakeDeps();
    const s = new Speaker({ purpose: "ceo", api: "/api", deps: f.deps });
    s.say("A.", () => s.say("C."));
    s.say("B.");
    await settle();
    f.pending.shift()!(true); // A ends; B starts; C queues
    await settle();
    expect(f.pending).toHaveLength(1); // only B plays
    f.pending.shift()!(true);
    await settle();
    expect(f.calls.map((c) => JSON.parse(String(c.init.body)).text)).toEqual(["A.", "B.", "C."]);
  });

  it("no voice at all (no gateway, no browser voice): done at once, as heard (the screen shows it)", async () => {
    const f = fakeDeps(503);
    const s = new Speaker({ purpose: "ceo", api: "/api", deps: { ...f.deps, synth: null } });
    let heard: boolean | null = null;
    s.say("Hello.", (p) => (heard = p));
    await settle();
    expect(heard).toBe(true);
  });

  it("nothing to say: done at once, as heard (the screen shows it)", () => {
    const f = fakeDeps();
    let heard: boolean | null = null;
    new Speaker({ purpose: "ceo", api: "/api", deps: f.deps }).say("   ", (p) => (heard = p));
    expect(heard).toBe(true);
    expect(f.calls).toHaveLength(0);
  });
});
