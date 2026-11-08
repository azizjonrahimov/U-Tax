import { describe, expect, it } from "vitest";
import { BASE_CARDS, CARD_KEYS } from "./cards";
import { EMPTY_LOG, foldEvents, glassFaces, paidCaption, stateNumbers, tickText } from "./live";

const ev = (id: number, type: string, actor: string, payload: Record<string, unknown> = {}, status = "ok") => ({
  id,
  type,
  actor,
  status,
  ts: "2026-11-08T14:02:00Z",
  payload,
});
const NO_STATE = { clients: null, touches: null, lastId: null };

describe("D42 P2 glass captions", () => {
  it("without live data every card keeps its neutral caption (never an invented number)", () => {
    const faces = glassFaces(EMPTY_LOG, NO_STATE, null);
    expect(faces.map((f) => f.caption)).toEqual(CARD_KEYS.map((k) => BASE_CARDS[k].caption));
    for (const k of ["form", "ledger", "inbox", "paid", "agents"] as const) expect(BASE_CARDS[k].caption).not.toMatch(/\d/);
  });

  it("fills each card from the firm's own reads", () => {
    const log = foldEvents(EMPTY_LOG, [
      ev(1, "memo.sent", "acct"),
      ev(2, "client.asked", "intake"),
      ev(3, "tax.return_computed", "tax", { jurisdiction: "California", form: "100", amount_usd: 800, status: "ok" }),
      ev(4, "tax.return_computed", "tax", { jurisdiction: "Federal", form: "1120", amount_usd: 12345, status: "ok" }),
      ev(5, "sheet.cell", "acct", { book: "b", n: 3, of: 42, sheet: "P&L", cell: "B7", label: "Net income" }),
      ev(6, "client.followed_up", "acct", {}, "failed"),
    ]);
    expect(log.mails).toBe(2);
    expect(log.after).toBe(6);
    const st = stateNumbers({ last_event_id: 6, firm: { agents: 7, clients: 3 }, touches: { training: 4, operating: 2, engineering: 1 } });
    const paid = paidCaption({ data: [{ direction: "in", amount: "49.00", token: "USD", rail: "stripe", status: "ok" }, { direction: "out", kind: "contractor", amount: "0.50", token: "pathUSD", rail: "tempo-testnet", status: "ok" }] });
    const [form, ledger, inbox, paidFace, agents] = glassFaces(log, st, paid).map((f) => f.caption);
    expect(form).toBe("federal return · line 31 · $12,345");
    expect(ledger).toBe("42 cells · formulas on screen");
    expect(inbox).toBe("2 client emails answered");
    expect(paidFace).toBe("0.50 pathUSD · tempo testnet");
    expect(agents).toBe("3 clients · 7 human touches");
  });

  it("ignores a federal return held for human review, failed payments and malformed rows", () => {
    const log = foldEvents(EMPTY_LOG, [ev(1, "tax.return_computed", "tax", { jurisdiction: "Federal", amount_usd: 99, status: "human_review" }), null, { id: "x" }]);
    expect(log.federalUsd).toBeNull();
    expect(paidCaption({ data: [{ direction: "out", amount: "0.50", token: "pathUSD", rail: "tempo-testnet", status: "failed" }] })).toBeNull();
    expect(paidCaption("nope")).toBeNull();
    expect(stateNumbers({ firm: {}, touches: { training: 1 } })).toEqual({ clients: null, touches: null, lastId: null });
  });

  it("the ticker shows the newest public event, colored by its department", () => {
    const log = foldEvents(EMPTY_LOG, [ev(9, "form.step", "tax", { doc: "Form 1120", n: 31, of: 40, label: "line 31" }), ev(7, "memo.sent", "acct")]);
    expect(log.newest?.id).toBe(9);
    expect(log.newest?.text).toMatch(/^Tax filled Form 1120 line 31 · \d\d:\d\d$/);
    expect(log.newest?.color).toBe("#FFB547");
    expect(tickText("unknown.type", "tax", {})).toBeNull();
  });

  it("folds incrementally (only the new rows are read on each poll)", () => {
    const a = foldEvents(EMPTY_LOG, [ev(1, "memo.sent", "acct")]);
    const b = foldEvents(a, [ev(2, "memo.sent", "acct")]);
    expect(b.mails).toBe(2);
    expect(b.after).toBe(2);
    expect(a.mails).toBe(1);
  });
});
