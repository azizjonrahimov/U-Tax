"use client";

// D39 S3–S5 and S7–S9 (LANDING-BRIEF §4). Every visual is a product surface drawn from the public
// log (stats.ts) or, for S4, a labelled illustration of a real flow. Offline: "—" and no bars.

import { useEffect, useState } from "react";
import { formatCents } from "@/theater/format";
import { useSeen } from "./Closing";
import type { Firm } from "./firm";
import { DEPT_NAME, DEPTS, RUN_STEPS, type Dept } from "./stats";

const STEP_NAME: Record<(typeof RUN_STEPS)[number], string> = {
  "email.received": "email_received",
  "facts.extracted": "facts_extracted",
  "law.searched": "law_searched",
  "tax.computed": "tax_computed",
  "qa.passed": "qa_passed",
  "memo.sent": "memo_sent",
};
// DESIGN.md seat-* (wayfinding, unchanged by D39).
const SEAT_COLOR: Record<Dept, string> = { cos: "#FF4D3D", intake: "#4DA3FF", tax: "#FFB547", qa: "#3DDC84", acct: "#C77DFF", mkt: "#FF6FB5", hr: "#2EE6D6" };

/** A number that counts up once when seen (900 ms, tabular figures); final at once under reduced motion. */
function useCount(target: number | null, seen: boolean): number | null {
  const [n, setN] = useState<number | null>(null);
  useEffect(() => {
    if (target === null || !seen) return;
    const instant = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
    let raf = 0;
    const t0 = performance.now();
    const tick = (now: number) => {
      const k = instant ? 1 : Math.min(1, (now - t0) / 900);
      setN(Math.round(target * (1 - Math.pow(1 - k, 3))));
      if (k < 1) raf = requestAnimationFrame(tick);
    };
    raf = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(raf);
  }, [target, seen]);
  return target === null ? null : n;
}

function Head({ label, title, p, id }: { label: string; title: React.ReactNode; p: string; id: string }) {
  return (
    <div className="sect-head rv">
      <span className="label">
        <span className="ldot" aria-hidden="true" />
        {label}
      </span>
      <h2 id={id}>{title}</h2>
      <p className="sect-p">{p}</p>
    </div>
  );
}

/** S3 + S4 + S5: how it works. */
export function HowItWorks({ firm }: { firm: Firm }) {
  const [ref, seen] = useSeen<HTMLElement>();
  const log = firm.log;
  const run = log?.run ?? null;
  const max = Math.max(1, ...(run?.steps.map((s) => s.secs ?? 0) ?? [1]));
  const actions = log ? DEPTS.map((d) => ({ d, n: log.actions[d] })) : null;
  const top = actions ? Math.max(0, ...actions.map((a) => a.n)) : 0;
  return (
    <section id="how" ref={ref} className={`sect${seen ? " in" : ""}`} aria-labelledby="how-h">
      <Head id="how-h" label="how it works" title={<>Most of tax is <em>paperwork</em>.</>} p="Read the email. Find the numbers. Do the math twice. Write the memo. U-Tax takes the paperwork and leaves you the decision." />

      <div className="feat rv">
        <div className="feat-copy">
          <span className="mono num">01</span>
          <h3>One case, every step</h3>
          <p>A case moves from inbox to memo on its own. If a number is missing, Intake asks; if QA disagrees, nothing is sent.</p>
        </div>
        <div className="card feat-vis timeline" role="img" aria-label={run ? `${run.caseName}'s case, inbox to memo: ${run.steps.map((s) => `${STEP_NAME[s.type]} ${s.secs ?? "pending"}${s.secs !== null ? " seconds" : ""}`).join(", ")}.` : "A case timeline (no case yet tonight)."}>
          <div className="tl-head mono">
            <span>{run ? run.caseName.toLowerCase() : "—"}</span>
            <span className="live-chip">from the event log</span>
          </div>
          {RUN_STEPS.map((type, i) => {
            const s = run?.steps[i];
            const done = s?.secs !== null && s !== undefined;
            return (
              <div className="tl-row mono" key={type}>
                <span className={`tl-ic${done ? " ok" : ""}`} aria-hidden="true">
                  {done ? "✓" : "·"}
                </span>
                <span className="tl-name">{STEP_NAME[type]}</span>
                <span className="tl-bar">
                  <span className={done ? "fill" : "fill pend"} style={{ width: seen && done ? `${Math.max(4, ((s?.secs ?? 0) / max) * 100)}%` : "0%" }} />
                </span>
                <span className="tl-ms">{done ? `${s?.secs} s` : "—"}</span>
              </div>
            );
          })}
        </div>
      </div>

      <div className="feat flip rv">
        <div className="feat-copy">
          <span className="mono num">02</span>
          <h3>Approvals where the CEO already is</h3>
          <p>The first send of every kind waits for a spoken yes. After that it runs on its own, inside the limits the CEO set.</p>
        </div>
        <div className="card feat-vis approval" role="img" aria-label="An illustration of an approval: the first LinkedIn post of the night waits for the CEO's yes; after that, posts run on their own within 10 an hour.">
          <div className="ap-head mono">
            <span># ceo</span>
            <span className="live-chip">how approvals look</span>
          </div>
          <p className="ap-bot">
            <span className="mark sm" aria-hidden="true">
              U
            </span>
            <b>U-Tax</b> First LinkedIn post of the night. Send it?
          </p>
          <dl className="ap-rows mono">
            <div>
              <dt>action</dt>
              <dd>social_post</dd>
            </div>
            <div>
              <dt>rule</dt>
              <dd>first one needs a human</dd>
            </div>
            <div>
              <dt>then</dt>
              <dd>AUTO within 10/hr</dd>
            </div>
          </dl>
          <div className="ap-btns" aria-hidden="true">
            <span className="btn btn-lime sm">Approve</span>
            <span className="btn btn-ghost sm">Hold</span>
            <span className="mono ap-wait">waiting 2 min</span>
          </div>
        </div>
      </div>

      <div className="feat rv">
        <div className="feat-copy">
          <span className="mono num">03</span>
          <h3>Every number is on the record</h3>
          <p>Every step is an event: who did it, when, and what it cost. Replay any case before you trust it with money.</p>
        </div>
        <div className="card feat-vis iso" role="img" aria-label={actions ? `Actions per department tonight: ${actions.map((a) => `${DEPT_NAME[a.d]} ${a.n}`).join(", ")}.` : "Actions per department (the firm is resting)."}>
          <svg viewBox="0 0 420 220" className="iso-svg" aria-hidden="true">
            {(actions ?? DEPTS.map((d) => ({ d, n: 0 }))).map((a, i) => {
              const h = actions && top > 0 ? 18 + (a.n / top) * 130 : 6;
              const x = 30 + i * 54;
              const base = 180;
              const hi = actions !== null && top > 0 && i === actions.findIndex((x) => x.n === top); // one lime bar, like the reference
              const grow = seen ? h : 4;
              return (
                <g key={a.d} className={hi ? "hi" : undefined}>
                  <path className="face-l" d={`M${x} ${base} l18 9 v${-grow} l-18 -9 z`} />
                  <path className="face-r" d={`M${x + 18} ${base + 9} l18 -9 v${-grow} l-18 9 z`} />
                  <path className="face-t" d={`M${x} ${base - grow} l18 9 l18 -9 l-18 -9 z`} />
                  <text x={x + 18} y={210} textAnchor="middle" className="iso-lbl">
                    {a.d}
                  </text>
                </g>
              );
            })}
          </svg>
          <p className="mono iso-cap">actions tonight · {log ? log.totalActions : "—"} total</p>
        </div>
      </div>
    </section>
  );
}

const HUB_NODES = [
  ["AM", "agentmail"],
  ["ST", "stripe"],
  ["HS", "hubspot"],
  ["OR", "openrouter"],
  ["AP", "apify"],
  ["MR", "mobilerun"],
] as const;

/** S7: one gateway, and where tonight's work went. */
export function TwoCards({ firm }: { firm: Firm }) {
  const [ref, seen] = useSeen<HTMLElement>();
  const log = firm.log;
  const total = log?.totalActions ?? 0;
  const shares = log && total > 0 ? DEPTS.map((d) => ({ d, pct: Math.round((log.actions[d] / total) * 100) })).sort((a, b) => b.pct - a.pct) : null;
  return (
    <section id="product" ref={ref} className={`sect${seen ? " in" : ""}`} aria-label="The gateway and tonight's work">
      <div className="two rv">
        <div className="card two-card">
          <h3>One gateway, every tool</h3>
          <p>Agents never hold a key. Every email, post, payment and booking goes through one gateway that checks it first.</p>
          <div className="hub" role="img" aria-label="The U-Tax gateway in the middle; every tool connects only through it.">
            <svg viewBox="0 0 360 200" aria-hidden="true">
              {HUB_NODES.map((_, i) => {
                const a = (Math.PI * 2 * i) / HUB_NODES.length - Math.PI / 2;
                return <line key={i} x1={180} y1={100} x2={180 + Math.cos(a) * 130} y2={100 + Math.sin(a) * 78} className="hub-line" />;
              })}
              <circle r="3" className="hub-dot">
                <animateMotion dur="2.4s" repeatCount="indefinite" path="M180 100 L310 100" />
              </circle>
            </svg>
            <span className="hub-u" aria-hidden="true">
              U
            </span>
            {HUB_NODES.map(([k, name], i) => {
              const a = (Math.PI * 2 * i) / HUB_NODES.length - Math.PI / 2;
              return (
                <span key={k} className="hub-node mono" title={name} style={{ left: `${50 + (Math.cos(a) * 130 * 100) / 360}%`, top: `${50 + (Math.sin(a) * 78 * 100) / 200}%` }}>
                  {k}
                </span>
              );
            })}
          </div>
          <p className="mono foot-note">🔒 keys stay in the gateway</p>
        </div>
        <div className="card two-card">
          <h3>Where tonight&apos;s work went</h3>
          <p>Share of every tool call, by department.</p>
          <ul className="shares" aria-label={shares ? `Share of tonight's tool calls: ${shares.map((s) => `${DEPT_NAME[s.d]} ${s.pct}%`).join(", ")}.` : "No tool calls yet (or the firm is resting)."}>
            {(shares ?? DEPTS.map((d) => ({ d, pct: 0 }))).map((s, i) => (
              <li key={s.d} className="mono">
                <span className="sh-name">{s.d}</span>
                <span className="sh-bar">
                  <span className={i === 0 && shares ? "fill" : "fill gray"} style={{ width: seen && shares ? `${s.pct}%` : "0%" }} />
                </span>
                <span className="sh-pct">{shares ? `${s.pct}%` : "—"}</span>
              </li>
            ))}
          </ul>
          <p className="mono foot-note">counted from the event log</p>
        </div>
      </div>
    </section>
  );
}

function Ring({ pct, seen }: { pct: number | null; seen: boolean }) {
  const n = useCount(pct, seen);
  const r = 46;
  const c = 2 * Math.PI * r;
  return (
    <div className="ring" role="img" aria-label={pct === null ? "Steps with no human: not available." : `${pct}% of tonight's steps needed no human.`}>
      <svg viewBox="0 0 120 120" aria-hidden="true">
        <circle cx="60" cy="60" r={r} className="ring-bg" />
        <circle cx="60" cy="60" r={r} className="ring-fg" strokeDasharray={c} strokeDashoffset={c - (c * (seen && pct !== null ? pct : 0)) / 100} />
      </svg>
      <span className="ring-n">{n === null ? "—" : `${n}%`}</span>
    </div>
  );
}

/** S8: the bento, all live. */
export function Bento({ firm }: { firm: Firm }) {
  const [ref, seen] = useSeen<HTMLElement>();
  const log = firm.log;
  const noHuman = log && log.totalActions > 0 && firm.operating !== null ? Math.max(0, Math.round((1 - firm.operating / log.totalActions) * 100)) : null;
  const saved = useCount(firm.savedCents, seen);
  const hours = log?.perHour ?? null;
  const hmax = hours ? Math.max(1, ...hours) : 1;
  const showSine = log !== null && log.recoveries + log.failedActions > 0;
  const wave = (amp: number, phase: number) => Array.from({ length: 41 }, (_, i) => `${i === 0 ? "M" : "L"}${i * 7} ${50 - Math.sin(i / 4 + phase) * amp}`).join(" ");
  return (
    <section ref={ref} className={`sect${seen ? " in" : ""}`} aria-labelledby="bento-h">
      <Head id="bento-h" label="tonight, live" title={<>The firm, by the <em>numbers</em>.</>} p="Counted from the gateway's own log while you read this. If the firm is resting, you'll see a dash, not a guess." />
      <div className="bento rv">
        <div className="card tile">
          <span className="live-chip">live</span>
          <Ring pct={noHuman} seen={seen} />
          <p className="tile-cap">of tonight&apos;s steps needed no human</p>
        </div>
        <div className="card tile big-num">
          <span className="live-chip">live</span>
          <p className="tile-n">{saved === null ? "—" : formatCents(saved)}</p>
          <p className="tile-cap">franchise tax saved for clients, by the other legal method</p>
        </div>
        <div className="card tile">
          <span className="live-chip">live</span>
          <div className="seats" aria-hidden="true">
            {DEPTS.map((d) => (
              <span key={d} className="seat-dot" style={{ background: SEAT_COLOR[d] }} title={DEPT_NAME[d]} />
            ))}
          </div>
          <p className="tile-cap">7 departments, 1 human</p>
        </div>
        {showSine && log && (
          <div className="card tile wide" role="img" aria-label={`Tonight: ${log.recoveries} recoveries and ${log.failedActions} failed tool calls.`}>
            <span className="live-chip">live</span>
            <svg viewBox="0 0 280 100" className="sine" aria-hidden="true">
              <path d={wave(26, 0)} className="sine-lime" />
              <path d={wave(14, 1.6)} className="sine-gray" />
            </svg>
            <p className="tile-cap mono">
              {log.recoveries} recovered · {log.failedActions} failed calls
            </p>
          </div>
        )}
        <div className="card tile wide" role="img" aria-label={hours ? `Tool calls per hour, the last 12 hours: ${hours.join(", ")}.` : "Activity per hour: not available."}>
          <span className="live-chip">live</span>
          <div className="heat" aria-hidden="true">
            {(hours ?? new Array<number>(12).fill(0)).map((n, i) => (
              <span key={i} className={hours && n === hmax && n > 0 ? "hi" : undefined} style={{ opacity: hours ? 0.18 + (n / hmax) * 0.82 : 0.12 }} />
            ))}
          </div>
          <p className="tile-cap mono">actions per hour · the last 12 hours</p>
        </div>
      </div>
    </section>
  );
}

function Spark({ values, kind }: { values: number[]; kind: "line" | "ticks" }) {
  const max = Math.max(1, ...values);
  if (kind === "ticks")
    return (
      <svg viewBox="0 0 120 24" className="spark" aria-hidden="true">
        {values.map((v, i) => (
          <rect key={i} x={i * 10} y={24 - (v / max) * 22 - 2} width="5" height={(v / max) * 22 + 2} rx="1" />
        ))}
      </svg>
    );
  return (
    <svg viewBox="0 0 120 24" className="spark" aria-hidden="true">
      <path d={values.map((v, i) => `${i ? "L" : "M"}${(i * 120) / Math.max(1, values.length - 1)} ${22 - (v / max) * 20}`).join(" ")} />
    </svg>
  );
}

/** S9: what the agents actually wrote (real public artifacts; no people, no quotes from humans). */
export function Wrote({ firm }: { firm: Firm }) {
  const [ref, seen] = useSeen<HTMLElement>();
  const log = firm.log;
  const time = (ts: string) => new Date(ts).toLocaleTimeString("en-GB", { hour: "2-digit", minute: "2-digit", hour12: false });
  const cols: { dept: Dept; head: string; chart: React.ReactNode; body: string | null; ts: string | null }[] = [
    { dept: "acct", head: "memos over time", chart: <Spark values={log?.memosPerHour ?? []} kind="line" />, body: log?.latestMemo?.subject ? `Memo: ${log.latestMemo.subject}` : null, ts: log?.latestMemo?.ts ?? null },
    { dept: "mkt", head: "posts tonight", chart: <Spark values={log?.postsPerHour ?? []} kind="ticks" />, body: log?.latestPost?.text ?? null, ts: log?.latestPost?.ts ?? null },
    {
      dept: "qa",
      head: `qa pass rate${log?.qaPassRate !== null && log?.qaPassRate !== undefined ? ` · ${Math.round(log.qaPassRate * 100)}%` : ""}`,
      chart: (
        <span className="prog" aria-hidden="true">
          <span style={{ width: seen && log?.qaPassRate != null ? `${Math.round(log.qaPassRate * 100)}%` : "0%" }} />
        </span>
      ),
      body: log?.latestQa?.line ?? null,
      ts: log?.latestQa?.ts ?? null,
    },
  ];
  return (
    <section ref={ref} className={`sect${seen ? " in" : ""}`} aria-labelledby="wrote-h">
      <Head id="wrote-h" label="from the log" title={<>What the agents <em>actually</em> wrote.</>} p="No testimonials. These are the newest things the departments produced tonight, straight from the public log." />
      <div className="wrote rv">
        {cols.map((c) => (
          <figure key={c.dept} className="card wcol">
            <p className="mono w-head">{c.head}</p>
            {c.chart}
            <span className="mono live-chip">from the event log</span>
            <blockquote className="w-body">{c.body ?? "—"}</blockquote>
            <figcaption className="mono w-by">
              <span className="seat-dot sm" style={{ background: SEAT_COLOR[c.dept] }} aria-hidden="true" /> {DEPT_NAME[c.dept]}
              {c.ts ? ` · ${time(c.ts)}` : ""}
            </figcaption>
          </figure>
        ))}
      </div>
    </section>
  );
}
