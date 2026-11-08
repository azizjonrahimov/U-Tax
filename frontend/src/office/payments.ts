// D41 F50 (runbook §E): the firm's payment ledger from GET /api/payments (contract v1.20, B52).
// Pure parser (tests pin it). Testnet tokens are never dollars; a Stripe test-mode copy is a "mirror".

export interface PayRow {
  id: number;
  ts: string;
  direction: "in" | "out";
  kind: string;
  counterparty: string;
  jobId: string | null;
  amount: string;
  token: string;
  rail: string;
  status: string;
  tx: string | null;
  explorer: string | null;
  stripe: { id: string; mode: string; url: string | null } | null;
}
export interface Ledger {
  rows: PayRow[];
  contractorsPaid: { amount: string; token: string } | null;
  agentSales: { amount: string; token: string } | null;
  clientsPaidUsd: string | null;
}

type P = Record<string, unknown>;
const obj = (v: unknown): P => (typeof v === "object" && v !== null && !Array.isArray(v) ? (v as P) : {});
const str = (v: unknown, max = 80) => (typeof v === "string" ? v.slice(0, max) : "");
const amount = (v: unknown) => (typeof v === "string" && /^\d{1,12}(\.\d{1,18})?$/.test(v) ? v : typeof v === "number" && Number.isFinite(v) && v >= 0 ? String(v) : null);
/** Only https links leave the page (an explorer or the Stripe dashboard). */
const https = (v: unknown) => {
  if (typeof v !== "string") return null;
  try {
    const u = new URL(v);
    return u.protocol === "https:" ? u.toString() : null;
  } catch {
    return null;
  }
};
const total = (v: unknown) => {
  const t = obj(v);
  const a = amount(t.amount);
  return a !== null ? { amount: a, token: str(t.token, 16) || "pathUSD" } : null;
};

export function parseLedger(raw: unknown): Ledger | null {
  const r = obj(raw);
  if (!Array.isArray(r.data)) return null;
  const rows: PayRow[] = [];
  for (const x of r.data.slice(0, 100)) {
    const o = obj(x);
    const a = amount(o.amount);
    if (typeof o.id !== "number" || a === null) continue;
    const s = obj(o.stripe);
    rows.push({
      id: o.id,
      ts: str(o.ts, 40),
      direction: o.direction === "in" ? "in" : "out",
      kind: str(o.kind, 24),
      counterparty: str(o.counterparty, 40) || "unknown",
      jobId: str(o.job_id, 40) || null,
      amount: a,
      token: str(o.token, 16) || "pathUSD",
      rail: str(o.rail, 24),
      status: str(o.status, 16) || "ok",
      tx: str(o.tx_hash, 80) || null,
      explorer: https(o.explorer_url),
      stripe: str(s.id) ? { id: str(s.id), mode: str(s.mode, 16) || "mirror", url: https(s.dashboard_url) } : null,
    });
  }
  const t = obj(r.totals);
  return { rows, contractorsPaid: total(t.contractors_paid), agentSales: total(t.agent_sales), clientsPaidUsd: amount(t.clients_paid_usd) };
}

/** "0x02f0…270a" */
export const shortTx = (tx: string) => (tx.length > 14 ? `${tx.slice(0, 6)}…${tx.slice(-4)}` : tx);
/** The rail as people say it; testnet stays visible in the words. */
export const railName = (rail: string) => (rail === "tempo-testnet" ? "Tempo testnet" : rail === "stripe" ? "Stripe" : rail || "—");
/** An amount with its unit: USD as dollars, a token always with its name (never "$"). */
export const money = (amount: string, token: string) => (token.toUpperCase() === "USD" ? `$${Number(amount).toFixed(2)}` : `${Number(amount).toFixed(2)} ${token}`);
