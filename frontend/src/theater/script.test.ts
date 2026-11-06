import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { describe, expect, it, vi } from "vitest";
import { parseJsonl, type UTaxEvent } from "./events";
import {
  buildScript,
  initialState,
  KNOWN_TYPES,
  stepsForEvent,
  type CursorStep,
  type SkippedEvent,
} from "./script";

const read = (rel: string) => readFileSync(fileURLToPath(new URL(rel, import.meta.url)), "utf8");
const FIXTURE = read("../../public/fixtures/events.sample.jsonl");
const events = parseJsonl(FIXTURE);
const quiet = { log: () => {} };

const ev = (id: number, type: string, payload: Record<string, unknown>, extra: Partial<UTaxEvent> = {}): UTaxEvent => ({
  id,
  ts: `2026-11-07T21:00:${String(id).padStart(2, "0")}.000Z`,
  type,
  actor: "intake",
  case_id: "case_1",
  provider: "local",
  status: "ok",
  payload,
  tokens_in: null,
  tokens_out: null,
  cost_usd: null,
  ...extra,
});

const stepsOf = (steps: CursorStep[], type: string) => {
  const ids = new Set(events.filter((e) => e.type === type).map((e) => e.id));
  return steps.filter((s) => ids.has(s.eventId));
};

describe("fixture", () => {
  it("matches contracts/events.sample.jsonl (re-copy it when the contract changes)", () => {
    const norm = (s: string) => s.replace(/\r\n/g, "\n").trim();
    expect(norm(FIXTURE)).toBe(norm(read("../../../contracts/events.sample.jsonl")));
  });

  it("uses only event types the theater knows", () => {
    for (const e of events) expect(KNOWN_TYPES).toContain(e.type);
  });
});

describe("buildScript on the happy-path fixture", () => {
  const { steps, skipped } = buildScript(events, quiet);

  it("skips nothing", () => {
    expect(skipped).toEqual([]);
  });

  it("is deterministic: same events, same script, whatever the input order", () => {
    const shuffled = [...events].reverse();
    expect(buildScript(events, quiet)).toEqual({ steps, skipped });
    expect(buildScript(shuffled, quiet)).toEqual({ steps, skipped });
  });

  it("every step comes from a real event (premise 7)", () => {
    const ids = new Set(events.map((e) => e.id));
    for (const s of steps) {
      expect(ids.has(s.eventId)).toBe(true);
      expect(s.ts).toBe(events.find((e) => e.id === s.eventId)!.ts);
    }
  });

  it("steps stay in event order and keys are unique", () => {
    const ids = steps.map((s) => s.eventId);
    expect(ids).toEqual([...ids].sort((a, b) => a - b));
    expect(new Set(steps.map((s) => s.key)).size).toBe(steps.length);
  });

  it("has the prefix property, so live mode can append", () => {
    for (let n = 0; n <= events.length; n++) {
      const partial = buildScript(events.slice(0, n), quiet).steps;
      expect(partial).toEqual(steps.slice(0, partial.length));
    }
  });

  it("moves the camera only when the pane changes, never to the rail", () => {
    const focus = steps.filter((s) => s.action === "focus");
    expect(focus.length).toBeGreaterThan(0);
    for (const f of focus) {
      expect(f.pane).not.toBe("rail");
      expect(f.agent).toBeNull();
    }
    let camera: string | null = null;
    for (const s of steps) {
      if (s.action === "focus") {
        expect(s.pane).not.toBe(camera);
        camera = s.pane;
      } else if (s.pane !== "rail") {
        expect(s.pane).toBe(camera);
      }
    }
  });

  it("email.received: Intake opens the message; the case appears on the rail", () => {
    const s = stepsOf(steps, "email.received").filter((x) => x.caseId === "case_7");
    expect(s.find((x) => x.action === "open")).toMatchObject({
      pane: "mail",
      target: "mail.message",
      agent: "intake",
      meta: { from: "founder@team7.dev", masked_name: "Team #7" },
    });
    expect(s.find((x) => x.pane === "rail")).toMatchObject({ target: "rail.cases", action: "appear", text: "Team #7", agent: null });
  });

  it("facts.extracted: highlights each quote, then drags it into its cell", () => {
    const s = stepsOf(steps, "facts.extracted");
    const highlights = s.filter((x) => x.action === "highlight");
    expect(highlights.map((x) => [x.target, x.text])).toEqual([
      ["mail.quote.incorporated_delaware", "We're a Delaware C-corp"],
      ["mail.quote.authorized_shares", "10,000,000"],
      ["mail.quote.issued_shares", "8,000,000"],
      ["mail.quote.gross_assets", "$500k"],
      ["mail.quote.foreign_owner_25", "No foreign owners"],
    ]);
    const drags = s.filter((x) => x.action === "drag");
    expect(drags.map((x) => [x.from, x.target, x.text])).toEqual([
      ["mail.quote.incorporated_delaware", "sheet.cell.incorporated_delaware", "Yes"],
      ["mail.quote.authorized_shares", "sheet.cell.authorized_shares", "10,000,000"],
      ["mail.quote.issued_shares", "sheet.cell.issued_shares", "8,000,000"],
      ["mail.quote.gross_assets", "sheet.cell.gross_assets", "$500,000"],
      ["mail.quote.foreign_owner_25", "sheet.cell.foreign_owner_25", "No"],
    ]);
    expect(s.find((x) => x.target === "sheet.cell.stated_par")).toMatchObject({
      action: "fill",
      text: "$0.00001",
      meta: { assumed: true },
    });
    for (const x of s.filter((y) => y.action !== "focus")) expect(x.agent ?? "intake").toBe("intake");
  });

  it("law.searched: Tax types the query and the result card appears", () => {
    const s = stepsOf(steps, "law.searched");
    expect(s.find((x) => x.action === "type")).toMatchObject({
      pane: "search",
      agent: "tax",
      target: "search.query",
      text: "delaware franchise tax assumed par value method",
    });
    expect(s.find((x) => x.target === "search.result.0")?.text).toBe(
      "8 Del. C. § 503 · assumed par value capital method · min $400, max $200,000",
    );
  });

  it("tax.computed: Tax fills both methods, the lower one wins", () => {
    const s = stepsOf(steps, "tax.computed");
    const fills = Object.fromEntries(s.filter((x) => x.action === "fill").map((x) => [x.target, x.text]));
    expect(fills).toEqual({
      "sheet.cell.authorized_method": "$85,165",
      "sheet.cell.assumed_par_method": "$400",
      "sheet.cell.annual_report_fee": "$50",
      "sheet.cell.total": "$450",
    });
    const marks = s.filter((x) => x.action === "mark").map((x) => [x.target, x.text]);
    expect(marks).toContainEqual(["sheet.cell.assumed_par_method", "win"]);
    expect(marks).toContainEqual(["sheet.cell.authorized_method", "lose"]);
    expect(s.some((x) => x.target.startsWith("sheet.flag."))).toBe(false);
  });

  it("qa.passed: QA ticks each checked cell and stamps the sheet", () => {
    const s = stepsOf(steps, "qa.passed");
    expect(s.filter((x) => x.action === "tick").map((x) => x.target)).toEqual([
      "sheet.cell.authorized_shares",
      "sheet.cell.issued_shares",
      "sheet.cell.gross_assets",
      "sheet.cell.total",
    ]);
    expect(s.every((x) => x.agent === "qa" || x.agent === null)).toBe(true);
    expect(s.at(-1)).toMatchObject({ target: "sheet.qa", text: "✓ QA" });
  });

  it("memo.sent: Accounting types the real memo text and clicks Send", () => {
    const memo = events.find((e) => e.type === "memo.sent")!;
    const s = stepsOf(steps, "memo.sent").filter((x) => x.action !== "focus");
    expect(s.map((x) => [x.target, x.action])).toEqual([
      ["mail.compose", "appear"],
      ["mail.memo", "type"],
      ["mail.send", "click"],
      ["mail.sent", "appear"],
    ]);
    expect(s[1].text).toBe((memo.payload as { body: string }).body);
    expect(s[1].agent).toBe("acct");
    expect(s[3].text).toBe("✓ Sent · 41 s after last fact");
  });

  it("call.booked: Intake drags a 15-min block named after the masked case", () => {
    expect(stepsOf(steps, "call.booked").find((x) => x.action === "drag")).toMatchObject({
      pane: "calendar",
      target: "calendar.block",
      agent: "intake",
      text: "Team #7 · 15-min review",
      meta: { start: "2026-11-08T17:30:00.000Z", minutes: 15 },
    });
  });

  it("touch.* puts the white CEO cursor on the rail; system updates have no cursor", () => {
    for (const s of stepsOf(steps, "touch.training").concat(stepsOf(steps, "touch.operating"))) {
      expect(s).toMatchObject({ pane: "rail", target: "rail.touches", agent: "ceo", action: "count" });
    }
    for (const type of ["trust.changed", "case.status", "approval.requested", "cost.booked"]) {
      for (const s of stepsOf(steps, type)) expect(s.agent).toBeNull();
    }
    expect(stepsOf(steps, "trust.changed")[0].text).toBe("email → AUTO");
    expect(stepsOf(steps, "cost.booked")[0].text).toBe("$0.0034");
  });

  it("facts.missing marks the missing cells; client.asked types the re-ask", () => {
    const missing = stepsOf(steps, "facts.missing").filter((x) => x.action === "fill");
    expect(missing.map((x) => x.target)).toEqual([
      "sheet.cell.authorized_shares",
      "sheet.cell.issued_shares",
      "sheet.cell.gross_assets",
      "sheet.cell.foreign_owner_25",
    ]);
    expect(missing.every((x) => x.text === "?" && x.caseId === "case_12")).toBe(true);
    expect(stepsOf(steps, "client.asked").at(-1)?.text).toBe("✓ Sent · asked for 4 facts");
  });

  it("brief.generated: the Chief of Staff says the brief (audio is fetched by event id)", () => {
    expect(stepsOf(steps, "brief.generated")[0]).toMatchObject({
      target: "rail.brief",
      agent: "cos",
      action: "say",
      meta: { brief_event_id: 29 },
    });
  });
});

describe("edge cases", () => {
  it("zero events → empty script", () => {
    expect(buildScript([], quiet)).toEqual({ steps: [], skipped: [] });
  });

  it("unknown types are skipped and logged, never animated", () => {
    const log = vi.fn<(s: SkippedEvent) => void>();
    const { steps, skipped } = buildScript(
      [ev(1, "email.received", { masked_name: "Team #1", from_masked: "a@b.dev", subject: "hi", body: "hi" }), ev(2, "ufo.landed", {})],
      { log },
    );
    expect(skipped).toEqual([{ eventId: 2, type: "ufo.landed", reason: "unknown_type" }]);
    expect(log).toHaveBeenCalledOnce();
    expect(log).toHaveBeenCalledWith({ eventId: 2, type: "ufo.landed", reason: "unknown_type" });
    expect(steps.some((s) => s.eventId === 2)).toBe(false);
  });

  it("logs to console.warn by default", () => {
    const warn = vi.spyOn(console, "warn").mockImplementation(() => {});
    buildScript([ev(1, "ufo.landed", {})]);
    expect(warn).toHaveBeenCalledOnce();
    warn.mockRestore();
  });

  it("a malformed payload is skipped without stopping later events", () => {
    const { steps, skipped } = buildScript(
      [ev(1, "facts.extracted", {}), ev(2, "case.status", { from: "NEW", to: "INTAKE_PENDING" }, { actor: "system" })],
      quiet,
    );
    expect(skipped).toEqual([{ eventId: 1, type: "facts.extracted", reason: "bad_payload" }]);
    expect(steps).toHaveLength(1);
    expect(steps[0]).toMatchObject({ eventId: 2, action: "status", text: "intake pending" });
  });

  it("duplicate ids (SSE replay after reconnect) collapse to one", () => {
    const e = ev(5, "case.status", { from: "NEW", to: "READY" }, { actor: "system" });
    expect(buildScript([e, e, { ...e }], quiet).steps).toHaveLength(1);
  });

  it("status skipped → no steps; status failed → one fail step on the event's pane", () => {
    const { steps, skipped } = buildScript(
      [
        ev(1, "law.searched", { query: "q", results: [] }, { actor: "tax", status: "skipped" }),
        ev(2, "memo.sent", {}, { actor: "acct", status: "failed" }),
      ],
      quiet,
    );
    expect(skipped).toEqual([{ eventId: 1, type: "law.searched", reason: "status_skipped" }]);
    expect(steps.map((s) => [s.pane, s.action, s.agent])).toEqual([
      ["mail", "focus", null],
      ["mail", "fail", "acct"],
    ]);
  });

  it("qa.failed: QA circles the mismatch; case.blocked raises the red card", () => {
    const { steps } = buildScript(
      [
        ev(1, "qa.failed", { checks: [{ field: "issued_shares", ok: true, note: "ok" }, { field: "total", ok: false, note: "engine re-run differs" }] }, { actor: "qa" }),
        ev(2, "case.blocked", { reason: "QA failed: total mismatch", needs: "human" }, { actor: "system" }),
      ],
      quiet,
    );
    expect(steps.filter((s) => s.action !== "focus").map((s) => [s.target, s.action, s.text])).toEqual([
      ["sheet.cell.issued_shares", "tick", "ok"],
      ["sheet.cell.total", "circle", "engine re-run differs"],
      ["sheet.qa", "appear", "✗ QA"],
      ["rail.cases", "block", "QA failed: total mismatch"],
    ]);
  });

  it("tax.computed with flags raises the 5472 and R&D notes", () => {
    const { steps } = buildScript(
      [
        ev(1, "tax.computed", {
          authorized_method_cents: 100,
          assumed_par_method_cents: 200,
          chosen: "authorized",
          annual_report_fee_cents: 5000,
          fee_source: "snapshot",
          total_cents: 5100,
          due_date: "2027-03-01",
          form5472_flag: true,
          rnd_flag: true,
          engine_version: "1",
        }, { actor: "tax" }),
      ],
      quiet,
    );
    const marks = steps.filter((s) => s.action === "mark").map((s) => [s.target, s.text]);
    expect(marks).toContainEqual(["sheet.cell.authorized_method", "win"]);
    expect(steps.map((s) => s.target)).toEqual(expect.arrayContaining(["sheet.flag.form5472", "sheet.flag.rnd"]));
  });

  it("v1.2 voice.command from a /talk customer and ceo.notified map to the rail", () => {
    const { steps } = buildScript(
      [
        ev(1, "voice.command", { text: "we have 10M shares", channel: "voice", speaker: "customer", case_id: "case_9" }, { case_id: "case_9" }),
        ev(2, "ceo.notified", { channel: "email", subject: "Approval needed", summary: "First memo", reason: "approval" }, { actor: "cos", case_id: null }),
      ],
      quiet,
    );
    expect(steps[0]).toMatchObject({ target: "rail.voice", agent: "intake", action: "say", meta: { speaker: "customer", case_id: "case_9" } });
    expect(steps[1]).toMatchObject({ target: "rail.notify", agent: "cos", text: "Approval needed", meta: { reason: "approval" } });
  });

  it("call.booked before any email falls back to the case id", () => {
    const { steps } = buildScript([ev(1, "call.booked", { start: "x", minutes: 15, invitee_masked: "m" })], quiet);
    expect(steps.find((s) => s.action === "drag")?.text).toBe("case_1 · 15-min review");
  });

  it("stepsForEvent never mutates the state it is given", () => {
    const state = initialState();
    const frozen = JSON.stringify(state);
    stepsForEvent(state, ev(1, "email.received", { masked_name: "Team #1", from_masked: "a", subject: "s", body: "b" }));
    expect(JSON.stringify(state)).toBe(frozen);
  });
});
