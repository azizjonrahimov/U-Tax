// D39 S3–S9: what the public event log says about tonight (LANDING-BRIEF §4). Pure: fed the public
// events (types-filtered) and the cases /api/state lists (flagged cases are never among them).

export const DEPTS = ["cos", "intake", "tax", "qa", "acct", "mkt", "hr"] as const;
export type Dept = (typeof DEPTS)[number];
export const DEPT_NAME: Record<Dept, string> = { cos: "Chief of Staff", intake: "Intake", tax: "Tax", qa: "QA", acct: "Accounting", mkt: "Marketing", hr: "HR" };

/** The run of one real case, inbox to memo, with the seconds each step took (S3). */
export const RUN_STEPS = ["email.received", "facts.extracted", "law.searched", "tax.computed", "qa.passed", "memo.sent"] as const;
export interface RunStep {
  type: (typeof RUN_STEPS)[number];
  /** Seconds since the step before (the first: 0); null while the step hasn't happened. */
  secs: number | null;
}

export interface LogStats {
  memos: number;
  /** tool.called per department (S5 iso bars, S7 shares). */
  actions: Record<Dept, number>;
  totalActions: number;
  failedActions: number;
  recoveries: number;
  /** tool.called per hour of the log's newest 12 hours, oldest first (S8 heatmap). */
  perHour: number[];
  run: { caseName: string; steps: RunStep[] } | null;
  /** S9: the newest public artifacts, as text (masked by the gateway; flagged cases left out). */
  latestMemo: { subject: string; ts: string } | null;
  latestPost: { text: string; channel: string; ts: string } | null;
  latestQa: { line: string; ok: boolean; ts: string } | null;
  /** S9 mini charts: memos per hour, posts per hour, QA pass rate (0..1, null without checks). */
  memosPerHour: number[];
  postsPerHour: number[];
  qaPassRate: number | null;
}

type Obj = Record<string, unknown>;
const isObj = (v: unknown): v is Obj => typeof v === "object" && v !== null && !Array.isArray(v);
const text = (v: unknown, max: number) => (typeof v === "string" ? v.replace(/\s+/g, " ").trim().slice(0, max) : "");

/** Public event types the landing reads (the gateway filters before its limit). */
export const LANDING_TYPES = ["memo.sent", "payment.sent", "tool.called", "naive.recovered", "email.received", "facts.extracted", "law.searched", "tax.computed", "qa.passed", "qa.failed", "post.published"];

interface Ev {
  id: number;
  type: string;
  actor: string;
  caseId: string | null;
  status: string;
  t: number;
  ts: string;
  p: Obj;
}

function hours(evs: Ev[], newest: number, n = 12): number[] {
  const out = new Array<number>(n).fill(0);
  for (const e of evs) {
    const back = Math.floor((newest - e.t) / 3_600_000);
    if (back >= 0 && back < n) out[n - 1 - back] += 1;
  }
  return out;
}

export function logStats(events: readonly unknown[], publicCases: ReadonlySet<string>, caseNames: ReadonlyMap<string, string>): LogStats {
  const evs: Ev[] = [];
  for (const e of events) {
    if (!isObj(e) || typeof e.id !== "number" || typeof e.type !== "string" || typeof e.ts !== "string") continue;
    const t = Date.parse(e.ts);
    if (Number.isNaN(t)) continue;
    const caseId = typeof e.case_id === "string" ? e.case_id : null;
    // A case /api/state doesn't list publicly (flagged) never shows anywhere on the landing.
    if (caseId !== null && !publicCases.has(caseId)) continue;
    evs.push({ id: e.id, type: e.type, actor: typeof e.actor === "string" ? e.actor : "", caseId, status: typeof e.status === "string" ? e.status : "", t, ts: e.ts, p: isObj(e.payload) ? e.payload : {} });
  }
  evs.sort((a, b) => a.id - b.id);
  const newest = evs.length ? Math.max(...evs.map((e) => e.t)) : 0;

  const actions = Object.fromEntries(DEPTS.map((d) => [d, 0])) as Record<Dept, number>;
  const tools = evs.filter((e) => e.type === "tool.called");
  let failedActions = 0;
  for (const e of tools) {
    if ((DEPTS as readonly string[]).includes(e.actor)) actions[e.actor as Dept] += 1;
    if (e.status === "failed") failedActions += 1;
  }

  const memos = evs.filter((e) => e.type === "memo.sent" && e.status === "ok");
  const posts = evs.filter((e) => e.type === "post.published" && e.status === "ok");
  const qa = evs.filter((e) => e.type === "qa.passed" || e.type === "qa.failed");

  // S3: the newest case that reached its memo, each step's first time.
  let run: LogStats["run"] = null;
  const lastMemo = memos.at(-1);
  if (lastMemo?.caseId) {
    const mine = evs.filter((e) => e.caseId === lastMemo.caseId && e.id <= lastMemo.id);
    let prev: number | null = null;
    const steps = RUN_STEPS.map((type): RunStep => {
      const hit = mine.find((e) => e.type === type);
      if (!hit) return { type, secs: null };
      const secs = prev === null ? 0 : Math.max(0, Math.round((hit.t - prev) / 1000));
      prev = hit.t;
      return { type, secs };
    });
    run = { caseName: caseNames.get(lastMemo.caseId) ?? "a client", steps };
  }

  const lastPost = posts.at(-1);
  const lastQa = qa.at(-1);
  const qaLine = (e: Ev) => {
    const checks = Array.isArray(e.p.checks) ? e.p.checks.filter(isObj) : [];
    const c = checks.find((x) => x.ok === (e.type === "qa.passed")) ?? checks[0];
    return c ? text(`${text(c.field, 40)}: ${text(c.note, 120)}`, 160) : e.type === "qa.passed" ? "all checks passed" : "a check failed";
  };
  const checksAll = qa.flatMap((e) => (Array.isArray(e.p.checks) ? e.p.checks.filter(isObj) : []));
  return {
    memos: memos.length,
    actions,
    totalActions: tools.length,
    failedActions,
    recoveries: evs.filter((e) => e.type === "naive.recovered").length,
    perHour: hours(tools, newest),
    run,
    latestMemo: lastMemo ? { subject: text(lastMemo.p.subject, 120), ts: lastMemo.ts } : null,
    latestPost: lastPost ? { text: text(lastPost.p.text, 220), channel: text(lastPost.p.channel, 20).toLowerCase(), ts: lastPost.ts } : null,
    latestQa: lastQa ? { line: qaLine(lastQa), ok: lastQa.type === "qa.passed", ts: lastQa.ts } : null,
    memosPerHour: hours(memos, newest),
    postsPerHour: hours(posts, newest),
    qaPassRate: checksAll.length ? checksAll.filter((c) => c.ok === true).length / checksAll.length : null,
  };
}
