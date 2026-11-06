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
    // The touches log lists human touches only; voice shows as a caption, trust as the AUTO count.
    expect(end.rail.log.map((l) => [l.kind, l.text])).toEqual([
      ["operating", 'voice: "open the firm"'],
      ["training", "email → AUTO"],
    ]);
    expect(end.rail.brief?.eventId).toBe(29);
    expect(end.calendar.blocks.map((b) => b.text)).toEqual(["Team #7 · 15-min review"]);
    expect(end.search.results).toHaveLength(1);
    expect(upTo(19).phone.check).toEqual({ ok: true, text: "Memo renders on phone" });
    // A later screen call replaces the phone pane (event 24).
    expect(end.phone).toMatchObject({ dial: "cand_1", transcript: expect.stringContaining("run by an AI agent") });
  });

  it("an approval is pending until its touch arrives", () => {
    expect(upTo(14).rail.pending).toEqual([{ approvalId: "apr_1", text: "First memo email: Team #7, $85,165 → $450" }]);
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
    expect(n.money.spent_usd).toBe("0.4224"); // 0.0021 + 0.0034 + 0.0017 + 0.4100 + 0.0052
    expect(n.hiring).toEqual({ applicants: 1, screened: 0, shortlisted: 1, hired: 0 });
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
