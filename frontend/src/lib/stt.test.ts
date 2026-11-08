// D36 (contract v1.16.1): the gateway's STT — utterances cut by level, sent as the raw body.

import { describe, expect, it } from "vitest";
import { LevelGate, recordingType, SttError, transcribe } from "./stt";

const feed = (gate: LevelGate, rms: number, ms: number) => {
  const out: ("start" | "end")[] = [];
  for (let t = 0; t < ms; t += 50) {
    const r = gate.step(rms, 50);
    if (r) out.push(r);
  }
  return out;
};

describe("D36 utterance detector", () => {
  it("speech starts after 120 ms above the room's level and ends after a 700 ms pause", () => {
    const g = new LevelGate();
    expect(feed(g, 0.003, 1000)).toEqual([]); // a quiet room
    expect(feed(g, 0.2, 100)).toEqual([]); // a click is not speech
    expect(feed(g, 0.003, 200)).toEqual([]);
    expect(feed(g, 0.2, 1500)).toEqual(["start"]);
    expect(feed(g, 0.003, 650)).toEqual([]); // a short breath keeps the utterance
    expect(feed(g, 0.2, 500)).toEqual([]);
    expect(feed(g, 0.003, 800)).toEqual(["end"]);
    expect(g.voicedMs).toBeGreaterThanOrEqual(1900);
  });

  it("a soft voice still counts (the bar follows a quiet room down)", () => {
    const g = new LevelGate();
    expect(feed(g, 0.001, 2000)).toEqual([]);
    expect(feed(g, 0.012, 300)).toEqual(["start"]);
  });

  it("a noisy room raises the bar (its hum isn't speech), and keeps adapting during speech", () => {
    const g = new LevelGate();
    expect(feed(g, 0.03, 5000)).toEqual([]); // steady fan noise
    expect(feed(g, 0.25, 400)).toEqual(["start"]);
  });

  it("time comes from the clock, not the number of readings (a throttled tab)", () => {
    const g = new LevelGate();
    expect(g.step(0.003, 1000)).toBeNull();
    expect(g.step(0.2, 1000)).toBe("start"); // one slow reading covers the onset
  });
});

describe("D36 transcribe", () => {
  const audio = new Blob(["x".repeat(100)], { type: "audio/webm;codecs=opus" });
  it("sends the recording as the raw body, with its type, purpose and seconds; the CEO's Bearer only for ceo", async () => {
    const calls: { url: string; init: RequestInit }[] = [];
    const f = (async (url: string, init: RequestInit) => {
      calls.push({ url, init });
      return new Response(JSON.stringify({ text: " hire 2 people " }), { status: 200 });
    }) as unknown as typeof fetch;
    expect(await transcribe(audio, 3.2, "ceo", "s.live", f)).toBe("hire 2 people");
    expect(calls[0].url).toMatch(/\/api\/stt\?purpose=ceo&seconds=4$/);
    expect(calls[0].init.body).toBe(audio);
    expect(calls[0].init.headers).toEqual({ "Content-Type": "audio/webm", Authorization: "Bearer s.live" });
    await transcribe(audio, 99, "talk", "s.live", f);
    await transcribe(audio, 0.1, "talk", null, f);
    expect(calls[2].url).toMatch(/seconds=1$/); // never declares 0 seconds
    expect(calls[1].url).toMatch(/purpose=talk&seconds=30$/);
    expect(calls[1].init.headers).toEqual({ "Content-Type": "audio/webm" }); // public: never the session
  });

  it("503 / 429 / outages come back as SttError with the status (the caller switches ears)", async () => {
    for (const status of [503, 429, 401]) {
      const f = (async () => new Response("{}", { status })) as unknown as typeof fetch;
      await expect(transcribe(audio, 1, "ceo", "s", f)).rejects.toMatchObject({ status });
    }
    const down = (async () => {
      throw new TypeError("network");
    }) as unknown as typeof fetch;
    await expect(transcribe(audio, 1, "ceo", "s", down)).rejects.toBeInstanceOf(SttError);
    const big = new Blob([new Uint8Array(1_100_000)], { type: "audio/webm" });
    await expect(transcribe(big, 30, "ceo", "s", down)).rejects.toMatchObject({ status: 413 });
  });

  it("records in the first format the browser supports", () => {
    expect(recordingType((t) => t === "audio/mp4")).toBe("audio/mp4");
    expect(recordingType((t) => t.startsWith("audio/webm"))).toBe("audio/webm;codecs=opus");
    expect(recordingType(() => false)).toBeNull();
  });
});
