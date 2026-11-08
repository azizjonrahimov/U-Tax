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
  outdatedApprovals,
  approveReleases,
  logStillMatches,
  readableSummary,
  briefParagraphs,
  supersededBy,
  logWasReset,
  briefSince,
  MAX_ORDER_CHARS,
  type Approval,
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
    let v = foldLog(emptyLog(), [
      ev(1, "approval.requested", { approval_id: "apr_0", action_type: "email", summary: "old" }),
    ]);
    v = foldLog({ ...v, lastId: 0 }, [
      ev(1, "approval.requested", { approval_id: "apr_1", action_type: "email", summary: "First memo" }, { case_id: "case_1" }),
      ev(2, "case.blocked", { reason: "qa failed twice", needs: "human" }, { case_id: "case_2" }),
    ]);
    expect([...v.pending.keys()]).toEqual(["apr_0", "apr_1"]);
    expect(v.pending.get("apr_1")?.caseId).toBe("case_1");
    expect(v.blockedReason.get("case_2")).toBe("qa failed twice");
    v = foldLog(v, [ev(3, "touch.training", { note: "email → AUTO", approval_id: "apr_1" }), ev(4, "touch.operating", { note: "denied", approval_id: "apr_0" })]);
    expect(v.pending.size).toBe(0);
    // A flip to AUTO (from ASK or PAUSED) supersedes the other approvals of that type; PAUSED/ASK keep them.
    const two = foldLog(v, [
      ev(5, "approval.requested", { approval_id: "apr_2", action_type: "email", summary: "" }),
      ev(6, "approval.requested", { approval_id: "apr_6", action_type: "calendar_invite", summary: "" }),
    ]);
    expect([...foldLog(two, [ev(7, "trust.changed", { action_type: "email", from: "ASK", to: "AUTO" })]).pending.keys()]).toEqual(["apr_6"]);
    expect([...foldLog(two, [ev(7, "trust.changed", { action_type: "email", from: "PAUSED", to: "AUTO" })]).pending.keys()]).toEqual(["apr_6"]);
    for (const to of ["PAUSED", "ASK"]) expect(foldLog(two, [ev(7, "trust.changed", { action_type: "email", from: "AUTO", to })]).pending.size).toBe(2);
    expect(foldLog(two, [ev(7, "trust.changed", { action_type: "email", to: "AUTO" }, { status: "failed" })]).pending.size).toBe(2);
    const again = foldLog(v, [ev(1, "approval.requested", { approval_id: "apr_1", action_type: "email", summary: "x" })]);
    expect(again.pending.size).toBe(0); // id 1 is behind lastId
    expect(foldLog(v, [ev(9, "approval.requested", { approval_id: "apr_9" }, { status: "failed" })]).pending.size).toBe(0);
  });
});

describe("what the CEO is shown", () => {
  const a = (approvalId: string, caseId: string | null, actionType = "email"): Approval => ({ approvalId, caseId, actionType, summary: "" });

  it("only the newest approval per case and kind is current; older ones never get Approve", () => {
    expect([...outdatedApprovals([a("apr_1", "case_1"), a("apr_2", "case_2"), a("apr_3", "case_1"), a("apr_4", null), a("apr_5", null), a("apr_6", "case_1", "calendar_invite")])]).toEqual(["apr_1"]);
  });

  it("reads ISO times in summaries as local times and leaves the rest alone", () => {
    const out = readableSummary("Call for Team #2 at 2026-11-07T17:00:00.000Z, $450 → $400", "en-US");
    expect(out).not.toMatch(/2026-11-07T/);
    expect(out).toMatch(/^Call for Team #2 at .+, \$450 → \$400$/);
    expect(readableSummary("no times here")).toBe("no times here");
  });

  it("reads the newer approval out of a 409 superseded (v1.7.8)", () => {
    expect(supersededBy("apr_3 is out of date: decide apr_5 instead")).toBe("apr_5");
    expect(supersededBy("apr_3 is out of date: the case changed; the crew asks again if it still applies")).toBeNull();
    expect(supersededBy("")).toBeNull();
  });

  it("reads the brief as paragraphs, joining the gateway's wrapped lines", () => {
    expect(briefParagraphs("Welcome back. Since you were last here, the firm served 3 new clients and sent\n0 memos.\n5 approvals waiting on you.\n\nThat's the brief.")).toEqual([
      "Welcome back. Since you were last here, the firm served 3 new clients and sent 0 memos. 5 approvals waiting on you.",
      "That's the brief.",
    ]);
  });

  it("notices a gateway reset and briefs from the start after one", () => {
    expect(logWasReset(12, 90)).toBe(true);
    expect(logWasReset(90, 90)).toBe(false);
    expect(logWasReset(null, 90)).toBe(false);
    expect(briefSince(91, 12)).toBe(0);
    expect(briefSince(40, 90)).toBe(40);
    expect(briefSince(40, null)).toBe(40);
  });
});

describe("before an Approve that releases more than one", () => {
  const a = (approvalId: string, caseId: string | null, actionType = "email"): Approval => ({ approvalId, caseId, actionType, summary: "" });
  const pending = [a("apr_1", "case_1"), a("apr_2", "case_2"), a("apr_3", "case_1"), a("apr_4", null, "social_post")];
  const outdated = outdatedApprovals(pending);

  it("counts the other waiting approvals of that kind (and the outdated ones) while the kind still asks", () => {
    expect(approveReleases(pending, pending[1], "ASK", outdated)).toEqual({ others: 2, outdated: 1 });
    expect(approveReleases(pending, pending[1], undefined, outdated)).toEqual({ others: 2, outdated: 1 });
    expect(approveReleases(pending, pending[3], "ASK", outdated)).toBeNull(); // alone of its kind
    expect(approveReleases(pending, pending[1], "PAUSED", outdated)).toBeNull(); // nothing flips
  });

  it("the log still matches only while the event at lastId has the same id and ts", () => {
    const v = foldLog(emptyLog(), [ev(7, "case.status", {})]);
    expect(logStillMatches(v, { data: [{ id: 7, ts: "2026-11-07T09:00:00Z" }] })).toBe(true);
    expect(logStillMatches(v, { data: [{ id: 7, ts: "2026-11-08T01:00:00Z" }] })).toBe(false); // reset, regrown past 7
    expect(logStillMatches(v, { data: [] })).toBe(false); // reset, shorter
    expect(logStillMatches(emptyLog(), { data: [] })).toBe(true);
  });
});

describe("actions", () => {
  it("a CEO order is typed, trimmed and capped", () => {
    expect(orderBody("  stop posting  ")).toEqual({ text: "stop posting", channel: "typed", speaker: "ceo" });
    expect(orderBody("x".repeat(MAX_ORDER_CHARS + 9)).text).toHaveLength(MAX_ORDER_CHARS);
  });

  it.each([
    [401, "unauthorized", "", /sign-in expired or was ended/],
    [0, "network_error", "", /Can't reach the gateway/],
    [409, "wrong_status", "case_3 is READY", /already moved on/],
    [400, "no_facts", "", /Use Re-ask/],
    [400, "bad_input", "decision must be hire or decline", /decision must be/],
    [404, "not_found", "no approval apr_9", /That item is gone/],
    [409, "superseded", "apr_3 was superseded", /outdated/],
    [429, "", "", /Too many requests/],
    [429, "rate_limited", "", /Too many requests/],
    [0, "timeout", "", /taking too long/],
    [500, "boom", "", /The gateway refused that/],
  ])("%i %s reads as a sentence", (status, code, message, re) => expect(ceoError(status, code, message)).toMatch(re));
});
