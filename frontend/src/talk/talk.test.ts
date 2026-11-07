import { describe, expect, it } from "vitest";
import type { UTaxEvent } from "@/theater/events";
import {
  addTurn,
  canSend,
  caseDone,
  dropPending,
  fillTurn,
  pendingTurns,
  reserveTurn,
  customerChars,
  emptyProgress,
  followCase,
  friendlyError,
  isEmail,
  MAX_CUSTOMER_CHARS,
  MAX_TURN_CHARS,
  MAX_TURNS,
  nextTypedQuestion,
  parseVoiceSession,
  progressLine,
  realtimeSignal,
  REALTIME_SDP_URL,
  TYPED_SCRIPT,
  voiceBody,
  type Turn,
} from "./talk";

const ev = (id: number, type: string, payload: Record<string, unknown>, extra: Partial<UTaxEvent> = {}): UTaxEvent =>
  ({
    id, ts: "2026-11-07T09:00:00Z", type, actor: "intake", case_id: "case_7", status: "ok", provider: null,
    payload, model: null, tokens_in: null, tokens_out: null, cost_usd: null, ...extra,
  }) as UTaxEvent;

describe("turns and the request body (contract §6e)", () => {
  it("trims, collapses whitespace and ignores empty turns", () => {
    let t: Turn[] = [];
    t = addTurn(t, "agent", "  Is it a   Delaware corp? ").turns;
    t = addTurn(t, "customer", "   ").turns;
    t = addTurn(t, "customer", "yes,\n an Inc.").turns;
    expect(t).toEqual([{ speaker: "agent", text: "Is it a Delaware corp?" }, { speaker: "customer", text: "yes, an Inc." }]);
  });

  it("clips a long turn instead of dropping it", () => {
    const { turns } = addTurn([], "customer", "x".repeat(MAX_TURN_CHARS + 50));
    expect(Array.from(turns[0].text)).toHaveLength(MAX_TURN_CHARS);
    expect(turns[0].text.endsWith("…")).toBe(true);
  });

  it("keeps the customer transcript under the gateway's 4000-character cap", () => {
    let t: Turn[] = [];
    let full = false;
    for (let i = 0; i < 6 && !full; i++) ({ turns: t, full } = addTurn(t, "customer", "y".repeat(900)));
    expect(full).toBe(true);
    expect(customerChars(t)).toBeLessThanOrEqual(MAX_CUSTOMER_CHARS);
    expect(addTurn(t, "customer", "one more").turns).toHaveLength(t.length); // nothing past the cap
    expect(addTurn(t, "agent", "Thanks!").turns).toHaveLength(t.length + 1); // the agent can still speak
  });

  it("never removes a turn (positions stay put); past the cap only new agent lines are skipped", () => {
    let t = addTurn([], "customer", "yes, Delaware Inc").turns;
    for (let i = 0; i < MAX_TURNS + 6; i++) t = addTurn(t, i % 2 ? "customer" : "agent", `t${i}`).turns;
    expect(t[0].text).toBe("yes, Delaware Inc");
    expect(t.filter((x) => x.speaker === "customer")).toHaveLength(1 + (MAX_TURNS + 6) / 2); // every answer kept
    expect(t.at(-1)).toEqual({ speaker: "customer", text: `t${MAX_TURNS + 5}` });
    expect(addTurn(t, "agent", "one more question").turns).toHaveLength(t.length);
  });

  it("stops reserving when transcripts stop arriving, and drops unfilled slots at hang-up", () => {
    let t: Turn[] = [];
    for (let i = 0; i < 6; i++) t = reserveTurn(t, `it_${i}`);
    expect(pendingTurns(t)).toBe(3);
    t = fillTurn(t, "it_0", "Delaware Inc").turns;
    expect(dropPending(t)).toEqual([{ speaker: "customer", text: "Delaware Inc", id: "it_0" }]);
  });

  it("voice: a reserved slot keeps the customer's place until the transcript lands (a bare No. keeps its question)", () => {
    let t = addTurn([], "agent", "Any foreign owner with 25% or more?").turns;
    t = reserveTurn(t, "item_1");
    t = addTurn(t, "agent", "And the par value?").turns; // the agent's next line finishes first
    expect(pendingTurns(t)).toBe(1);
    expect(canSend(t, "a@b.co")).toBe(false); // nothing said yet
    t = fillTurn(t, "item_1", "No.").turns;
    expect(t.map((x) => x.text)).toEqual(["Any foreign owner with 25% or more?", "No.", "And the par value?"]);
    expect(voiceBody(t, "a@b.co", "voice", "r").turns.every((x) => !("id" in x))).toBe(true);
  });

  it("voice: an empty or failed transcript removes its slot; one without a slot is appended", () => {
    let t = reserveTurn([], "item_9");
    expect(fillTurn(t, "item_9", "  ").turns).toEqual([]);
    t = fillTurn([], "item_2", "10M authorized").turns;
    expect(t).toEqual([{ speaker: "customer", text: "10M authorized", id: "item_2" }]);
    expect(voiceBody(reserveTurn(t, "item_3"), "a@b.co", "voice", "r").turns).toEqual([{ speaker: "customer", text: "10M authorized" }]);
  });

  it("sends labelled turns, the typed email and the page's request id; never sends without both", () => {
    const t: Turn[] = [{ speaker: "agent", text: "Shares?" }, { speaker: "customer", text: "10M authorized" }];
    expect(voiceBody(t, "  ceo@acme.dev ", "voice", "r1")).toEqual({
      turns: t, channel: "voice", speaker: "customer", reply_email: "ceo@acme.dev", request_id: "r1",
    });
    expect(canSend(t, "ceo@acme.dev")).toBe(true);
    expect(canSend(t, "not an email")).toBe(false);
    expect(canSend([{ speaker: "agent", text: "Hi" }], "ceo@acme.dev")).toBe(false);
  });

  it.each([
    ["a@b.co", true], ["first.last+tax@startup.io", true], ["a@b", false], ["a b@c.d", false], ["", false], ["@b.co", false],
  ])("isEmail(%j) → %s", (s, ok) => expect(isEmail(s)).toBe(ok));
});

describe("typed fallback script", () => {
  it("asks one question per answer, in the voice prompt's order, then stops", () => {
    let t: Turn[] = [];
    for (let i = 0; i < TYPED_SCRIPT.length; i++) {
      const q = nextTypedQuestion(t);
      expect(q).toBe(TYPED_SCRIPT[i]);
      t = addTurn(t, "agent", q!).turns;
      expect(nextTypedQuestion(t)).toBeNull(); // waits for the answer
      t = addTurn(t, "customer", `answer ${i}`).turns;
    }
    expect(nextTypedQuestion(t)).toBeNull();
  });
});

describe("voice session (contract §5, v1.7.5)", () => {
  it("uses the gateway's realtime_url, adding the model when it is missing", () => {
    const s = parseVoiceSession({ client_secret: "ek_1", model: "gpt-realtime", expires_at: "x", realtime_url: "https://api.openai.com/v1/realtime/calls" });
    expect(s).toEqual({ clientSecret: "ek_1", model: "gpt-realtime", sdpUrl: "https://api.openai.com/v1/realtime/calls?model=gpt-realtime" });
  });

  it("falls back to the beta endpoint when realtime_url is absent", () => {
    expect(parseVoiceSession({ client_secret: "ek_1", model: "gpt-realtime" })?.sdpUrl).toBe(`${REALTIME_SDP_URL}?model=gpt-realtime`);
  });

  it.each([
    [{ client_secret: "ek", model: "m", realtime_url: "https://evil.example/v1/realtime" }],
    [{ client_secret: "ek", model: "m", realtime_url: "http://api.openai.com/v1/realtime" }],
    [{ client_secret: "ek", model: "m", realtime_url: "https://user:pw@api.openai.com/v1" }],
    [{ client_secret: "", model: "m" }],
    [{ client_secret: "ek", model: "bad model/../x" }],
    [null],
  ])("refuses an unusable or off-OpenAI session %j", (raw) => expect(parseVoiceSession(raw)).toBeNull());
});

describe("realtime events → page", () => {
  it("maps transcripts (beta and GA names) and speaking states", () => {
    expect(realtimeSignal({ type: "conversation.item.input_audio_transcription.completed", transcript: "Yes, an Inc." })).toEqual({ kind: "turn", speaker: "customer", text: "Yes, an Inc." });
    expect(realtimeSignal({ type: "input_audio_buffer.committed", item_id: "it_1" })).toEqual({ kind: "reserve", id: "it_1" });
    expect(realtimeSignal({ type: "conversation.item.input_audio_transcription.completed", item_id: "it_1", transcript: "No." })).toEqual({ kind: "fill", id: "it_1", text: "No." });
    expect(realtimeSignal({ type: "conversation.item.input_audio_transcription.failed", item_id: "it_1" })).toEqual({ kind: "fill", id: "it_1", text: "", failed: true });
    expect(realtimeSignal({ type: "response.audio_transcript.done", transcript: "Great." })).toEqual({ kind: "turn", speaker: "agent", text: "Great." });
    expect(realtimeSignal({ type: "response.output_audio_transcript.done", transcript: "Great." })?.kind).toBe("turn");
    expect(realtimeSignal({ type: "input_audio_buffer.speech_started" })).toEqual({ kind: "state", state: "listening" });
    expect(realtimeSignal({ type: "response.done" })).toEqual({ kind: "state", state: "idle" });
    expect(realtimeSignal({ type: "error", error: { message: "rate limit" } })).toEqual({ kind: "error", message: "rate limit" });
  });

  it("ignores empty transcripts, unknown events and junk", () => {
    expect(realtimeSignal({ type: "conversation.item.input_audio_transcription.completed", transcript: "  " })).toBeNull();
    expect(realtimeSignal({ type: "session.created" })).toBeNull();
    expect(realtimeSignal("x")).toBeNull();
    expect(realtimeSignal(null)).toBeNull();
  });
});

describe("following the case", () => {
  const tax = ev(12, "tax.computed", {
    authorized_method_cents: 8516500, assumed_par_method_cents: 45000, chosen: "assumed_par", annual_report_fee_cents: 5000,
    fee_source: "s", total_cents: 45000, due_date: "2027-03-01", form5472_flag: false, rnd_flag: false, engine_version: "1",
  }, { actor: "tax" });

  it("moves forward through the stages and reads the number back", () => {
    let p = emptyProgress("case_7");
    p = followCase(p, [ev(10, "voice.command", {}), ev(11, "facts.extracted", { facts: {}, quotes: {}, assumed: [] }), tax]);
    expect(p).toMatchObject({ stage: "computed", totalCents: 45000, method: "assumed_par", lastId: 12 });
    expect(progressLine(p)).toBe("Your estimated Delaware franchise tax is $450, using the assumed par value method. QA is checking it, then the memo goes out.");
    p = followCase(p, [ev(13, "qa.passed", { checks: [] }, { actor: "qa" })]);
    expect(progressLine(p)).toMatch(/QA checked every number\. The memo is going out now\.$/);
    p = followCase(p, [ev(14, "approval.requested", { approval_id: "apr_1", action_type: "email", summary: "s" }, { actor: "system" })]);
    expect(progressLine(p)).toMatch(/U-Tax's CEO approves this memo, then it goes out\.$/);
    p = followCase(p, [ev(15, "memo.sent", {}, { actor: "acct" })]);
    expect(p.awaitingCeo).toBe(false);
    expect(p.stage).toBe("sent");
    expect(progressLine(p)).toMatch(/The memo is in your inbox\.$/);
  });

  it("ignores other cases, replays, failed events and a bad total", () => {
    let p = followCase(emptyProgress("case_7"), [tax]);
    p = followCase(p, [{ ...tax, id: 12 }, { ...tax, id: 13, case_id: "case_8" }]);
    expect(p.lastId).toBe(12);
    const bad = followCase(emptyProgress("case_7"), [ev(5, "tax.computed", { total_cents: -1 }), ev(6, "qa.passed", {}, { status: "failed" })]);
    expect(bad).toMatchObject({ stage: "received", totalCents: null });
  });

  it("never moves the stage back (a client reply after the memo)", () => {
    const p = followCase(emptyProgress("case_7"), [ev(1, "qa.passed", { checks: [] }), ev(2, "memo.sent", {}), ev(3, "facts.extracted", { facts: {}, quotes: {}, assumed: [] })]);
    expect(p.stage).toBe("sent");
    expect(caseDone(p)).toBe(true);
  });

  it("a QA re-ask after the computation shows the question, not the estimate", () => {
    let p = followCase(emptyProgress("case_7"), [ev(11, "facts.extracted", { facts: {}, quotes: {}, assumed: [] }), tax]);
    p = followCase(p, [ev(13, "qa.failed", { checks: [] }, { actor: "qa" }), ev(14, "facts.missing", { missing: ["issued_shares"], asks_so_far: 0 })]);
    expect(progressLine(p)).toBe("The firm emailed you one question: issued shares.");
    // The re-ask mail's approval is not the memo's sign-off.
    p = followCase(p, [ev(15, "approval.requested", { approval_id: "a", action_type: "email", summary: "s" }, { actor: "system" })]);
    expect(p.awaitingCeo).toBe(false);
  });

  it("says when the firm needs more facts, is not eligible, or waits on a human", () => {
    const asked = followCase(emptyProgress("case_7"), [ev(3, "client.asked", { missing: ["issued_shares", "gross_assets_cents"], body: "x" })]);
    expect(progressLine(asked)).toBe("The firm emailed you one question: issued shares, total assets.");
    expect(progressLine(followCase(emptyProgress("case_7"), [ev(3, "case.not_eligible", { reason: "llc" })]))).toMatch(/^This checkup is for Delaware corporations/);
    const blocked = followCase(emptyProgress("case_7"), [ev(3, "case.blocked", { reason: "r", needs: "human" })]);
    expect(progressLine(blocked)).toMatch(/A human at U-Tax/);
    expect(followCase(blocked, [ev(4, "case.status", { from: "BLOCKED", to: "READY" })]).blocked).toBe(false);
  });
});

describe("errors read as sentences", () => {
  it.each([
    [429, "rate_limited", /try again in a minute/],
    [400, "too_long", /five questions/],
    [400, "bad_input", /email address/],
    [0, "network_error", /Can't reach U-Tax/],
    [500, "internal", /Something went wrong/],
  ])("%i %s", (status, code, re) => expect(friendlyError(status, code)).toMatch(re));
});
