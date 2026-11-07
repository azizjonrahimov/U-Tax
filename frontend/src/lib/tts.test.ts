import { describe, expect, it } from "vitest";
import { lineLimitMs, Speaker, TTS_DOWN_MS, TTS_MAX, ttsChunks, type SpeakerDeps } from "./tts";

const flush = () => new Promise((r) => setTimeout(r, 0));

/** A fake browser: records what was fetched and played; playback ends when the test says so. */
function fakeDeps(status = 200, type = "audio/mpeg") {
  const calls: { url: string; init: RequestInit }[] = [];
  const played: string[] = [];
  const spoken: string[] = [];
  const pending: ((ok: boolean) => void)[] = [];
  let clock = 1_000;
  const deps: SpeakerDeps = {
    fetch: (async (url: string, init: RequestInit) => {
      calls.push({ url, init });
      return new Response(status === 200 ? new Blob([JSON.parse(String(init.body)).text]) : null, { status, headers: { "content-type": type } });
    }) as unknown as typeof fetch,
    playBlob: (blob) =>
      new Promise((resolve) => {
        void blob.text().then((t) => played.push(t));
        pending.push(resolve);
      }),
    synth: (text) =>
      new Promise((resolve) => {
        spoken.push(text);
        pending.push(resolve);
      }),
    now: () => clock,
    setTimeout: () => 0,
    clearTimeout: () => {},
  };
  return { deps, calls, played, spoken, pending, tick: (ms: number) => (clock += ms) };
}

describe("ttsChunks", () => {
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

  it("/talk sends no token", async () => {
    const f = fakeDeps();
    new Speaker({ purpose: "talk", api: "/api", deps: f.deps }).say("What is your company called?");
    await flush();
    expect((f.calls[0].init.headers as Record<string, string>).Authorization).toBeUndefined();
    expect(JSON.parse(String(f.calls[0].init.body)).purpose).toBe("talk");
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

  it("nothing to say: done at once, as heard (the screen shows it)", () => {
    const f = fakeDeps();
    let heard: boolean | null = null;
    new Speaker({ purpose: "ceo", api: "/api", deps: f.deps }).say("   ", (p) => (heard = p));
    expect(heard).toBe(true);
    expect(f.calls).toHaveLength(0);
  });
});
