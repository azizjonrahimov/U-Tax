import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { describe, expect, it } from "vitest";
import { parseJsonl, type UTaxEvent } from "./events";
import { humanTouches, railFromEvents, sumUsd } from "./rail";
import { applyStep, applySteps, emptyScreen } from "./screen";
import { buildScript, type CursorStep } from "./script";
import { MOVE_MS, movesCursor, stepHoldMs } from "./timing";

const FIXTURE = readFileSync(fileURLToPath(new URL("../../public/fixtures/events.sample.jsonl", import.meta.url)), "utf8");
const { events } = parseJsonl(FIXTURE);
const quiet = { log: () => {}, onGap: () => {} };
const { steps } = buildScript(events, quiet);
const upTo = (id: number) => applySteps(emptyScreen(), steps.filter((s) => s.eventId <= id));

describe("screen fold over the fixture script", () => {
  const end = applySteps(emptyScreen(), steps);

  it("is pure: folding twice gives the same screen, and the input is untouched", () => {
    const start = emptyScreen();
    const frozen = JSON.stringify(start);
    expect(applySteps(start, steps)).toEqual(end);
    expect(JSON.stringify(start)).toBe(frozen);
  });

  it("the sheet for Team #7 ends with the real numbers, QA stamp and the winner marked", () => {
    const sheet = end.cases.case_7.sheet;
    expect(sheet.cells.authorized_shares.text).toBe("10,000,000");
    expect(sheet.cells.gross_assets.text).toBe("$500,000");
    expect(sheet.cells.stated_par).toMatchObject({ text: "$0.00001", assumed: true });
    expect(sheet.cells.authorized_method).toMatchObject({ text: "$85,165", mark: "lose" });
    expect(sheet.cells.assumed_par_method).toMatchObject({ text: "$400", mark: "win" });
    expect(sheet.cells.total).toMatchObject({ text: "$450", mark: "win", tick: "engine re-run matches" });
    expect(sheet.qa).toBe("✓ QA");
  });

  it("mail keeps each case's own email and compose box", () => {
    const m7 = end.cases.case_7.mail;
    expect(m7.from).toBe("founder@team7.dev");
    expect(m7.quotes.authorized_shares).toBe("10,000,000");
    expect(m7.compose).toMatchObject({ sent: "✓ Sent · 41 s after last fact", pressed: true });
    expect(m7.compose?.text).toContain("Educational estimate — not tax advice.");
    const m12 = end.cases.case_12.mail;
    expect(m12.subject).toBe("tax help");
    expect(m12.compose?.sent).toBe("✓ Sent · asked for 4 facts");
    expect(end.mailCase).toBe("case_12");
  });

  it("rail: cases with statuses, the touches log, trust and the brief", () => {
    expect(end.caseOrder).toEqual(["case_7", "case_12"]);
    expect(end.caseRows.case_7).toMatchObject({ name: "Team #7", status: "call booked" });
    expect(end.caseRows.case_12).toMatchObject({ name: "Team #12", status: "new" });
    expect(end.rail.trust).toEqual({ email: "AUTO" });
    expect(end.rail.pending).toEqual([]);
    // The touches log lists human touches by kind and action type only (never free-text notes).
    expect(end.rail.log.map((l) => [l.kind, l.text])).toEqual([
      ["operating", "operating"],
      ["training", "training · email"],
    ]);
    expect(end.calendar.blocks.map((b) => b.text)).toEqual(["Team #7 · 15-min review"]);
    expect(end.search.results).toHaveLength(1);
    expect(upTo(19).phone.check).toEqual({ ok: true, text: "Memo checked on a cloud phone" });
    // A later screen call replaces the phone pane (event 24).
    expect(end.phone).toEqual({ dial: "5 min" });
  });

  it("an approval is pending until its touch arrives", () => {
    expect(upTo(14).rail.pending).toEqual([{ approvalId: "apr_1", text: "email approval" }]);
    expect(upTo(15).rail.pending).toEqual([]);
  });

  it("switch steps move the case-scoped panes back to the right case", () => {
    const a: CursorStep = { key: "1:0", eventId: 1, ts: "t", caseId: "case_a", pane: "sheet", target: "sheet", agent: null, action: "switch", text: "Team A" };
    const s = applyStep({ ...emptyScreen(), sheetCase: "case_b" }, a);
    expect(s.sheetCase).toBe("case_a");
    expect(s.cases.case_a.name).toBe("Team A");
  });

  it("case.flagged chips accumulate on the case row without duplicates", () => {
    const flag = (eventId: number, text: string): CursorStep => ({
      key: `${eventId}:0`, eventId, ts: "t", caseId: "c", pane: "rail", target: "rail.cases", agent: "qa", action: "flag", text,
    });
    const s = applySteps(emptyScreen(), [flag(1, "possible injection"), flag(2, "possible injection"), flag(3, "unrealistic inputs")]);
    expect(s.caseRows.c.flags).toEqual(["possible injection", "unrealistic inputs"]);
    expect(s.caseOrder).toEqual(["c"]);
  });

  it("a BLOCKED card sits on the case row until the status moves on", () => {
    const step = (eventId: number, action: CursorStep["action"], text: string): CursorStep => ({
      key: `${eventId}:0`, eventId, ts: "t", caseId: "c", pane: "rail", target: "rail.cases", agent: null, action, text,
    });
    let s = applyStep(emptyScreen(), step(1, "block", "QA failed: total mismatch"));
    expect(s.caseRows.c).toMatchObject({ status: "blocked", blocked: "QA failed: total mismatch" });
    s = applyStep(s, step(2, "status", "intake pending"));
    expect(s.caseRows.c.blocked).toBeUndefined();
  });
});

describe("review pass: rail and screen hardening", () => {
  const ev = (id: number, type: string, payload: Record<string, unknown>, extra: Partial<UTaxEvent> = {}): UTaxEvent => ({
    id, ts: "2026-11-07T21:00:00.000Z", type, actor: "system", case_id: null, provider: null, status: "ok", payload,
    tokens_in: null, tokens_out: null, cost_usd: null, ...extra,
  });
  const fold = (evs: UTaxEvent[]) => applySteps(emptyScreen(), buildScript(evs, quiet).steps);

  it.each([
    ["touch.training", "allow"],
    ["touch.training", "deny"],
    ["touch.operating", "allow"],
    ["touch.operating", "deny"],
    ["touch.engineering", "allow"],
    ["touch.engineering", "deny"],
  ])("%s (%s) settles its pending approval, logs its kind and leaves trust alone", (type, decision) => {
    const s = fold([
      ev(1, "approval.requested", { approval_id: "apr_9", action_type: "email", summary: "First memo" }, { case_id: "case_1" }),
      ev(2, type, { note: `${decision}: memo`, approval_id: "apr_9" }, { actor: "ceo" }),
    ]);
    expect(s.rail.pending).toEqual([]);
    expect(s.rail.log.map((l) => l.kind)).toEqual([type.slice(6)]);
    expect(s.rail.trust).toEqual({});
  });

  it("v1.8: a touch given by voice shows a mic on the rail log; click, typed and older touches don't", () => {
    const skipped: string[] = [];
    const evs = [
      ev(1, "approval.requested", { approval_id: "apr_1", action_type: "email", summary: "memo" }, { case_id: "case_1" }),
      ev(2, "ceo.proposed", { proposal_id: "prop_1", tool: "approve", args: { approval: "apr_1" }, readback: "Approve it. Say confirm.", expires_at: "2026-11-07T21:01:00Z", source: "realtime" }, { actor: "ceo" }),
      ev(3, "touch.training", { note: "allow", approval_id: "apr_1", channel: "voice" }, { actor: "ceo" }),
      ev(4, "ceo.confirmed", { proposal_id: "prop_1", via: "voice", heard: "yes do it" }, { actor: "ceo" }),
      ev(5, "touch.operating", { note: "pause", approval_id: null, channel: "click" }, { actor: "ceo" }),
      ev(6, "touch.operating", { note: "x", approval_id: null }, { actor: "ceo" }),
      ev(7, "touch.operating", { note: "y", approval_id: null, channel: 7 }, { actor: "ceo" }),
      ev(8, "ceo.cancelled", { proposal_id: "prop_2", via: "click", heard: "", reason: "expired" }, { actor: "ceo" }),
    ];
    const s = applySteps(emptyScreen(), buildScript(evs, { ...quiet, log: (k) => skipped.push(`${k.eventId}:${k.reason}`) }).steps);
    expect(s.rail.log.map((l) => [l.eventId, l.voice === true])).toEqual([[3, true], [5, false], [6, false], [7, false]]);
    expect(skipped).toEqual([]); // the proposal events are known: no console noise, no steps
  });

  it("v1.9: naive.confirm draws a rail line (the firm vetting its own tools, no click) and counts them", () => {
    const skipped: string[] = [];
    const evs = [
      ev(1, "naive.confirm", { session_id: "s1", agent: "cos", tool: "board_write", decision: "allow", read_people: false }),
      ev(2, "naive.confirm", { session_id: "s2", agent: "mkt", tool: "utax.post_social", decision: "allow", read_people: false }),
      ev(3, "naive.confirm", { session_id: "s3", agent: "agt_unknown", tool: "web_fetch<script>", decision: "deny", read_people: true }),
      ev(4, "naive.confirm", { session_id: "s4", agent: "cos", tool: "x", decision: "maybe" }), // malformed: skipped, never drawn
    ];
    const s = applySteps(emptyScreen(), buildScript(evs, { ...quiet, log: (k) => skipped.push(`${k.eventId}:${k.reason}`) }).steps);
    expect(s.rail.naive).toEqual({ text: "Naïve check: web_fetchscript denied for an agent", allowed: false, allowedCount: 2, deniedCount: 1 });
    expect(skipped).toEqual(["4:bad_payload"]);
    const first = applySteps(emptyScreen(), buildScript(evs.slice(0, 1), quiet).steps);
    expect(first.rail.naive?.text).toBe("Naïve check: board_write allowed for Chief of Staff");
    expect(first.rail.log).toEqual([]); // not a human touch: it never moves the touches count
  });

  it("a repeated approval request replaces its row instead of duplicating it", () => {
    const req = (id: number) => ev(id, "approval.requested", { approval_id: "apr_1", action_type: "email", summary: `v${id}` });
    expect(fold([req(1), req(2)]).rail.pending).toEqual([{ approvalId: "apr_1", text: "email approval" }]);
  });

  it("a failure note stays on its pane until the next real step there", () => {
    const failed = ev(1, "post.published", {}, { actor: "mkt", status: "failed" });
    const other = ev(2, "law.searched", { query: "q", results: [] }, { actor: "tax", case_id: "c" });
    const post = ev(3, "post.published", { channel: "x", text: "hi", url: null, image_url: null }, { actor: "mkt" });
    expect(fold([failed, other]).failure).toMatchObject({ pane: "social", eventId: 1 });
    expect(fold([failed, other, post]).failure).toBeUndefined();
  });

  it("prototype-like case ids are plain keys, never Object.prototype", () => {
    for (const id of ["constructor", "__proto__", "toString"]) {
      const s = fold([
        ev(1, "email.received", { masked_name: "Team X", from_masked: "a@b", subject: "s", body: "b" }, { actor: "intake", case_id: id }),
        ev(2, "tax.computed", {
          authorized_method_cents: 100, assumed_par_method_cents: 50, chosen: "assumed_par", annual_report_fee_cents: 0,
          fee_source: "s", total_cents: 50, due_date: "d", form5472_flag: false, rnd_flag: false, engine_version: "1",
        }, { actor: "tax", case_id: id }),
      ]);
      expect(Object.hasOwn(s.cases, id)).toBe(true);
      expect(s.cases[id].sheet.cells.total.text).toBe("$0.50");
    }
  });

  it("candidate names never reach the screen; hiring is counts plus an awaiting flag", () => {
    const name = "Jane Candidate";
    const hr = (id: number, type: string) =>
      ev(id, type, { candidate_id: "cand_7", name_masked: name, note: `${name} note` }, { actor: type === "hr.declined" ? "ceo" : "hr" });
    const evs = [hr(1, "hr.applicant"), hr(2, "hr.questionnaire_sent"), hr(3, "hr.shortlisted")];
    expect(railFromEvents(evs).hiring).toMatchObject({ shortlisted: 1, awaiting: 1, declined: 0 });
    const all = [...evs, hr(4, "hr.declined"), hr(5, "hr.decision_mailed")];
    const { steps } = buildScript(all, quiet);
    expect(JSON.stringify(steps)).not.toContain("Jane");
    expect(JSON.stringify(applySteps(emptyScreen(), steps))).not.toContain("Jane");
    expect(railFromEvents(all).hiring).toMatchObject({ shortlisted: 1, awaiting: 0, declined: 1 });
  });

  it("rail counts each event once, counts the cost of failed calls, and counts /talk clients", () => {
    const touch = events.find((e) => e.type === "touch.training")!;
    expect(railFromEvents([...events, touch]).touches.training).toBe(1);
    const failed = ev(99, "cost.booked", { provider: "openrouter", model: "m", estimated: true }, { status: "failed", cost_usd: "0.01" });
    expect(railFromEvents([...events, failed]).money.spent_usd).toBe("0.0134");
    // v1.7.4: a cost shown on another event is display only, never summed (it is booked by its own cost.booked).
    const bought = ev(101, "tool.bought", { tool: "Monid", amount_usd: "1.50", provider: "monid" }, { cost_usd: "1.50" });
    expect(railFromEvents([...events, bought]).money.spent_usd).toBe("0.0034");
    const talk = ev(100, "voice.command", { text: "hi", channel: "voice", speaker: "customer", case_id: "case_9" }, { actor: "intake", case_id: "case_9" });
    expect(railFromEvents([...events, talk]).firm.clients).toBe(3);
  });

  it("free text from notes, summaries and transcripts never reaches the screen", () => {
    const secret = "decline Candidate B";
    const s = fold([
      ev(1, "approval.requested", { approval_id: "apr_2", action_type: "email", summary: `declined mail: Candidate B` }),
      ev(2, "touch.operating", { note: secret, approval_id: "apr_2" }, { actor: "ceo" }),
      ev(3, "touch.operating", { note: secret, approval_id: null }, { actor: "ceo" }),
      ev(4, "screen.call", { candidate_id: "cand_9", transcript_excerpt: "Hi, I am Candidate B", seconds: 300 }, { actor: "hr" }),
    ]);
    const shown = JSON.stringify(s);
    expect(shown).not.toContain("Candidate B");
    expect(shown).not.toContain("cand_9");
    expect(s.rail.log.map((l) => l.text)).toEqual(["operating · email", "operating"]);
    expect(s.phone.dial).toBe("5 min");
  });

  it("hiring counts each candidate once per stage, and stages only move forward", () => {
    const hr = (id: number, type: string, cand: string) => ev(id, type, { candidate_id: cand, name_masked: "X", note: "n" }, { actor: "hr" });
    const evs = [
      hr(1, "hr.applicant", "c1"),
      hr(2, "hr.applicant", "c2"),
      hr(3, "hr.shortlisted", "c1"),
      hr(4, "hr.applicant", "c1"), // questionnaire reply re-sends hr.applicant
      hr(5, "hr.declined", "c2"),
    ];
    expect(railFromEvents(evs).hiring).toEqual({ applicants: 2, screened: 0, shortlisted: 1, hired: 0, declined: 1, awaiting: 1 });
  });

  it("an absurd tax value can't break the money panel", () => {
    const tax = (id: number, total: number) => ev(id, "tax.computed", {
      authorized_method_cents: 100, assumed_par_method_cents: 50, chosen: "assumed_par", annual_report_fee_cents: 0,
      fee_source: "s", total_cents: total, due_date: "d", form5472_flag: false, rnd_flag: false, engine_version: "1",
    }, { case_id: "c" });
    const memo = ev(3, "memo.sent", { to_masked: "a", subject: "s", body: "b", message_id: "m", seconds_since_last_fact: 1 }, { case_id: "c" });
    expect(Number.isSafeInteger(railFromEvents([tax(1, -Number.MAX_SAFE_INTEGER), memo]).money.tax_saved_cents)).toBe(true);
    expect(buildScript([tax(1, -5)], quiet).skipped).toMatchObject([{ reason: "bad_payload", detail: "total_cents: negative" }]);
  });

  it("the touches log keeps the last 12 lines", () => {
    const touches = Array.from({ length: 13 }, (_, i) => ev(i + 1, "touch.operating", { note: `n${i + 1}`, approval_id: null }, { actor: "ceo" }));
    const log = fold(touches).rail.log;
    expect(log).toHaveLength(12);
    expect(log[0].eventId).toBe(2);
  });
});

describe("timing", () => {
  it("every fixture step has a finite, bounded hold", () => {
    for (const s of steps) {
      const ms = stepHoldMs(s);
      expect(ms).toBeGreaterThanOrEqual(0);
      expect(ms).toBeLessThanOrEqual(6000);
    }
  });

  it("the whole fixture plays in about a minute at 1× and a few seconds at 16×", () => {
    const total = steps.reduce((t, s) => t + stepHoldMs(s) + (movesCursor(s) ? MOVE_MS : 0), 0);
    expect(total).toBeGreaterThan(30_000);
    expect(total).toBeLessThan(120_000);
    expect(total / 16).toBeLessThan(8_000);
  });

  it("camera moves and system updates don't move a cursor", () => {
    expect(steps.filter((s) => s.action === "focus").every((s) => !movesCursor(s))).toBe(true);
    expect(steps.filter((s) => s.agent === null).every((s) => !movesCursor(s))).toBe(true);
  });
});

describe("rail numbers (fixture projection of /api/state)", () => {
  it("projects the fixture: 2 clients, 2 human touches, saved = 85,165 − 400 (fee excluded, v1.4.1)", () => {
    const n = railFromEvents(events);
    expect(n.firm).toEqual({ agents: 7, clients: 2 });
    expect(n.touches).toEqual({ training: 1, operating: 1, engineering: 0 });
    expect(humanTouches(n)).toBe(2);
    expect(n.money.tax_saved_cents).toBe(8516500 - (45000 - 5000));
    expect(n.money.spent_usd).toBe("0.0034"); // v1.7.4: the one cost.booked; post/search/call/brief costs are display only
    expect(n.hiring).toEqual({ applicants: 1, screened: 0, shortlisted: 1, hired: 0, declined: 0, awaiting: 1 });
    expect(n.queue).toEqual({ waiting: 0, blocked: 0 });
  });

  it("nothing is saved before the memo is sent", () => {
    expect(railFromEvents(events.filter((e) => e.id < 17)).money.tax_saved_cents).toBe(0);
  });

  it("events the script skipped never move a number", () => {
    const bad: UTaxEvent = { ...events[3], id: 99, case_id: "case_x" };
    expect(railFromEvents([...events, bad], new Set([99])).firm.clients).toBe(2);
    expect(railFromEvents([...events, bad]).firm.clients).toBe(3);
  });

  it("sumUsd adds decimal strings exactly", () => {
    expect(sumUsd(["0.1", "0.2"])).toBe("0.3");
    expect(sumUsd(["0.0021", "0.0034", "1E-7", "oops"])).toBe("0.0055001");
    expect(sumUsd([])).toBe("0");
  });
});
