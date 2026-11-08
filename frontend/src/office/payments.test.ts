// D41 F50: the payment ledger parser (runbook §E, B52's shape).

import { describe, expect, it } from "vitest";
import { money, parseLedger, railName, shortTx } from "./payments";

const SAMPLE = {
  data: [
    {
      id: 379,
      ts: "2026-11-08T14:06:10Z",
      direction: "out",
      kind: "contractor",
      counterparty: "PixelAgent",
      job_id: "job_2",
      order_id: "order_406",
      task_no: 1,
      amount: "0.50",
      token: "pathUSD",
      rail: "tempo-testnet",
      status: "ok",
      tx_hash: "0x02f0aa11bb22cc33dd44ee55ff66270a",
      explorer_url: "https://explore.tempo.xyz/tx/0x02f0aa11bb22cc33dd44ee55ff66270a",
      stripe: { id: "pi_1", mode: "mirror", dashboard_url: "https://dashboard.stripe.com/test/payments/pi_1" },
    },
    { id: 380, ts: "2026-11-08T14:07:00Z", direction: "in", kind: "agent_sale", counterparty: "0x12…ab", amount: "0.50", token: "pathUSD", rail: "tempo-testnet", status: "ok", explorer_url: "javascript:alert(1)" },
    { id: 381, ts: "2026-11-08T14:08:00Z", direction: "in", kind: "client", counterparty: "Team #1", amount: "49.00", token: "USD", rail: "stripe", status: "ok" },
    { id: 382, amount: "lots" },
  ],
  totals: { contractors_paid: { amount: "1.50", token: "pathUSD" }, agent_sales: { amount: "0.50", token: "pathUSD" }, clients_paid_usd: "0.00" },
};

describe("F50 payment ledger", () => {
  it("reads the rows and totals; drops a malformed row; only https links", () => {
    const l = parseLedger(SAMPLE)!;
    expect(l.rows.map((r) => r.id)).toEqual([379, 380, 381]);
    expect(l.rows[0]).toMatchObject({ counterparty: "PixelAgent", jobId: "job_2", explorer: "https://explore.tempo.xyz/tx/0x02f0aa11bb22cc33dd44ee55ff66270a", stripe: { id: "pi_1", mode: "mirror" } });
    expect(l.rows[1].explorer).toBeNull();
    expect(l.contractorsPaid).toEqual({ amount: "1.50", token: "pathUSD" });
    expect(l.clientsPaidUsd).toBe("0.00");
    expect(parseLedger({ error: "x" })).toBeNull();
  });

  it("testnet tokens are never dollars", () => {
    expect(money("0.50", "pathUSD")).toBe("0.50 pathUSD");
    expect(money("49", "USD")).toBe("$49.00");
    expect(railName("tempo-testnet")).toBe("Tempo testnet");
    expect(shortTx("0x02f0aa11bb22cc33dd44ee55ff66270a")).toBe("0x02f0…270a");
  });
});
