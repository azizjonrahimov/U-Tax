// D41 F49: the firm's email as threads (runbook §D.2).

import { describe, expect, it } from "vitest";
import { foldMail, unanswered, type MailEvent } from "./mail";

const ev = (id: number, type: string, payload: Record<string, unknown>, case_id: string | null = "case_1"): MailEvent => ({
  id,
  ts: `2026-11-08T0${Math.floor(id / 60)}:${String(id % 60).padStart(2, "0")}:00.000Z`,
  type,
  case_id,
  provider: type.startsWith("payment") ? "stripe" : "agentmail",
  status: "ok",
  payload,
});

const CASE_1: MailEvent[] = [
  ev(4, "email.received", { masked_name: "Team #1", from_masked: "f***@team1.dev", subject: "franchise tax?", body: "We got a big bill." }),
  ev(9, "tool.called", { tool: "extract_facts" }),
  ev(21, "client.asked", { missing: ["issued_shares"], body: "Could you send your issued shares?" }),
  ev(23, "email.received", { masked_name: "Team #1", subject: "Re: numbers", body: "8,000,000 issued." }),
  ev(50, "memo.sent", { to_masked: "f***@team1.dev", subject: "Your Delaware franchise tax checkup", body: "Estimated $450.", message_id: "m1", memo_ref: "UTX-1" }),
  ev(51, "payment.link_created", { link_id: "plink_1", amount_cents: 4900, currency: "usd" }),
  ev(208, "email.received", { masked_name: "Team #1", subject: "Re: memo", body: "Thanks!" }),
  ev(304, "client.followed_up", { kind: "memo_check" }),
];

describe("F49 mail threads", () => {
  it("the case_1 log is one thread with 7 messages, in order, newest thread first", () => {
    const other = ev(400, "email.received", { masked_name: "Team #9", subject: "hi", body: "hello" }, "case_9");
    const t = foldMail([], [...CASE_1, other]);
    expect(t.map((x) => x.caseId)).toEqual(["case_9", "case_1"]);
    const c1 = t[1];
    expect(c1.name).toBe("Team #1");
    expect(c1.messages.map((m) => m.id)).toEqual([4, 21, 23, 50, 51, 208, 304]);
    expect(c1.messages.map((m) => m.dir)).toEqual(["in", "out", "in", "out", "out", "in", "out"]);
    expect(c1.messages[1].label).toBe("asked for the missing numbers");
    expect(c1.messages[4].body).toBe("$49 filing-help payment link (Stripe)");
    expect(unanswered(c1)).toBe(false);
    expect(unanswered(t[0])).toBe(true);
  });

  it("is idempotent and the same folded at once or one by one; bodies are capped", () => {
    const once = foldMail([], CASE_1);
    const each = CASE_1.reduce((s, e) => foldMail(s, [e]), foldMail([], []));
    expect(each).toEqual(once);
    expect(foldMail(once, CASE_1)).toEqual(once);
    const long = foldMail([], [ev(1, "email.received", { subject: "x", body: "a".repeat(2000) })]);
    expect(long[0].messages[0].body.length).toBe(600);
  });

  it("marks a flagged case (its bodies are never shown) and reads the sent artifact when present", () => {
    const t = foldMail([], [...CASE_1, ev(500, "case.flagged", { kind: "possible_injection" })]);
    expect(t[0].flagged).toBe(true);
    const s = foldMail([], [ev(7, "memo.sent", { subject: "s", body: "theater body", sent: { subject: "Real subject", body: "Hi Ana, …" } })]);
    expect(s[0].messages[0]).toMatchObject({ subject: "Real subject", body: "Hi Ana, …" });
  });
});
