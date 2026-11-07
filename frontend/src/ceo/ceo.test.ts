import { describe, expect, it } from "vitest";
import type { UTaxEvent } from "@/theater/events";
import {
  canDecide,
  ceoError,
  consoleFromState,
  emptyLog,
  foldLog,
  orderBody,
  parseBrief,
  readLastSeen,
  seedPending,
  MAX_ORDER_CHARS,
} from "./ceo";

const ev = (id: number, type: string, payload: Record<string, unknown>, extra: Partial<UTaxEvent> = {}): UTaxEvent =>
  ({
    id, ts: "2026-11-07T09:00:00Z", type, actor: "system", case_id: null, status: "ok", provider: null,
    payload, model: null, tokens_in: null, tokens_out: null, cost_usd: null, ...extra,
  }) as UTaxEvent;

describe("login brief (GET /api/ceo/brief)", () => {
  it("reads the brief, its pending approvals and where it left off", () => {
    expect(
      parseBrief({
        brief_event_id: 41, text: "Good morning.", counts: {}, last_event_id: 40,
        pending_approvals: [{ approval_id: "apr_1", action_type: "email", summary: "First memo: Team #1" }, { bogus: 1 }],
      }),
    ).toEqual({ briefEventId: 41, text: "Good morning.", lastEventId: 40, pending: [{ approvalId: "apr_1", actionType: "email", summary: "First memo: Team #1", caseId: null }] });
    expect(parseBrief({ nope: true })).toBeNull();
    expect(parseBrief({ text: "x", last_event_id: "40" })?.lastEventId).toBeNull();
  });

  it.each([["57", 57], [null, 0], ["", 0], ["-3", 0], ["5.5", 0], ["9999999999999999999", 0]])("lastSeen %j → %i", (raw, n) =>
    expect(readLastSeen(raw)).toBe(n),
  );
});

describe("admin /api/state", () => {
  const state = {
    last_event_id: 90,
    cases: [{ case_id: "case_3", masked_name: "Team #3", status: "BLOCKED", flagged: true }, { nope: 1 }],
    trust: { email: "AUTO", social_post: "PAUSED", calendar_invite: "ASK", voice_screen: "weird", rogue: "AUTO" },
    hiring: { candidates: [{ candidate_id: "cand_1", name_masked: "Candidate A.", stage: "screened", credentials: "CPA" }, { stage: "x" }] },
  };

  it("maps cases, trust (known types and states only) and candidates", () => {
    expect(consoleFromState(state)).toEqual({
      lastEventId: 90,
      cases: [{ caseId: "case_3", name: "Team #3", status: "BLOCKED", flagged: true }],
      trust: { email: "AUTO", social_post: "PAUSED", calendar_invite: "ASK" },
      candidates: [{ candidateId: "cand_1", name: "Candidate A.", stage: "screened", credentials: "CPA" }],
    });
    expect(consoleFromState(null)).toBeNull();
  });

  it("every screened candidate gets Hire/Decline (not only shortlisted); decided ones don't", () => {
    expect(["applicant", "screened", "shortlisted", "hired", "declined"].filter(canDecide)).toEqual(["screened", "shortlisted"]);
  });
});

describe("the log: pending approvals and blocked reasons", () => {
  it("an approval is pending until a touch carries its id; replays are ignored", () => {
    let v = seedPending(emptyLog(), [{ approvalId: "apr_0", actionType: "email", summary: "old", caseId: null }]);
    v = foldLog(v, [
      ev(1, "approval.requested", { approval_id: "apr_1", action_type: "email", summary: "First memo" }, { case_id: "case_1" }),
      ev(2, "case.blocked", { reason: "qa failed twice", needs: "human" }, { case_id: "case_2" }),
    ]);
    expect([...v.pending.keys()]).toEqual(["apr_0", "apr_1"]);
    expect(v.pending.get("apr_1")?.caseId).toBe("case_1");
    expect(v.blockedReason.get("case_2")).toBe("qa failed twice");
    v = foldLog(v, [ev(3, "touch.training", { note: "email → AUTO", approval_id: "apr_1" }), ev(4, "touch.operating", { note: "denied", approval_id: "apr_0" })]);
    expect(v.pending.size).toBe(0);
    // A flip to AUTO supersedes the other approvals of that type (the gateway emits nothing per approval).
    const flip = foldLog(seedPending(v, [{ approvalId: "apr_2", actionType: "email", summary: "", caseId: null }, { approvalId: "apr_6", actionType: "calendar_invite", summary: "", caseId: null }]), [
      ev(5, "trust.changed", { action_type: "email", from: "ASK", to: "AUTO", reason: "approved" }),
    ]);
    expect([...flip.pending.keys()]).toEqual(["apr_6"]);
    const again = foldLog(v, [ev(1, "approval.requested", { approval_id: "apr_1", action_type: "email", summary: "x" })]);
    expect(again.pending.size).toBe(0); // id 1 is behind lastId
    expect(foldLog(v, [ev(9, "approval.requested", { approval_id: "apr_9" }, { status: "failed" })]).pending.size).toBe(0);
  });
});

describe("actions", () => {
  it("a CEO order is typed, trimmed and capped", () => {
    expect(orderBody("  stop posting  ")).toEqual({ text: "stop posting", channel: "typed", speaker: "ceo" });
    expect(orderBody("x".repeat(MAX_ORDER_CHARS + 9)).text).toHaveLength(MAX_ORDER_CHARS);
  });

  it.each([
    [401, "unauthorized", "", /token was rejected/],
    [0, "network_error", "", /Can't reach the gateway/],
    [409, "wrong_status", "case_3 is READY", /already moved on/],
    [400, "no_facts", "", /Use Re-ask/],
    [400, "bad_input", "decision must be hire or decline", /decision must be/],
  ])("%i %s reads as a sentence", (status, code, message, re) => expect(ceoError(status, code, message)).toMatch(re));
});
