"use client";

// D37: the artifact a department is making, drawn live in its window: an email being written and
// sent, a post being typed and published, a workpaper filling cell by cell, a memo writing section
// by section, the tax engine's two methods, a calendar invite. Text only; the parent re-mounts a
// view (key = the artifact's key) when a new artifact starts, so it types from the beginning.

import { useEffect, useState } from "react";
import type { Activity, Artifact } from "@/office/artifact";

const reduced = () => typeof window !== "undefined" && window.matchMedia("(prefers-reduced-motion: reduce)").matches;

/** How much of `total` (characters or rows) is shown: it catches up with what arrived, a little at a time. */
function useReveal(total: number, perTick: (left: number) => number, ms: number): number {
  const [shown, setShown] = useState(0);
  useEffect(() => {
    if (shown >= total) return;
    const t = setTimeout(() => setShown((s) => (reduced() ? total : Math.min(total, s + perTick(total - s)))), ms);
    return () => clearTimeout(t);
  }, [shown, total, perTick, ms]);
  return Math.min(shown, total);
}
const chars = (left: number) => Math.max(2, Math.ceil(left / 30));
const oneRow = () => 1;

export function ActivityPill({ act }: { act: Activity | null }) {
  if (!act) return null;
  const text = act.kind === "writing" ? "writing…" : act.kind === "tool" && act.tool ? `using ${act.tool}` : "thinking…";
  return <span className={`act ${act.kind}`}>{text}</span>;
}

export function ArtifactView({ art }: { art: Artifact }) {
  switch (art.kind) {
    case "mail":
      return <Mail art={art} />;
    case "post":
      return <Post art={art} />;
    case "sheet":
      return <Sheet art={art} />;
    case "doc":
      return <Doc art={art} />;
    case "calc":
      return <Calc art={art} />;
    case "invite":
      return <Invite art={art} />;
  }
}

function Mail({ art }: { art: Extract<Artifact, { kind: "mail" }> }) {
  const n = useReveal(art.body.length, chars, 24);
  const typing = n < art.body.length;
  return (
    <div className="art mail">
      <div className="mail-head">
        <div>
          <span className="k">To</span> <span className="v">{art.to || "…"}</span>
        </div>
        <div>
          <span className="k">Subject</span> <span className="v">{art.subject || (art.state === "drafting" ? "Writing…" : "")}</span>
        </div>
      </div>
      <div className="mail-body">
        {art.body.slice(0, n)}
        {(typing || art.state === "drafting") && <span className="caret" aria-hidden />}
      </div>
      {art.attachments.length > 0 && (
        <div className="attach" aria-label="Attachments">
          {art.attachments.map((f) => (
            <span key={f} className="file">
              📎 {f}
            </span>
          ))}
        </div>
      )}
      <div className="art-foot">
        <span className={`send${art.state === "done" && !typing ? " sent" : ""}`}>{art.state === "done" && !typing ? "Sent ✓" : "Send"}</span>
      </div>
    </div>
  );
}

const CHANNEL: Readonly<Record<string, string>> = { x: "X", twitter: "X", linkedin: "LinkedIn" };

function Post({ art }: { art: Extract<Artifact, { kind: "post" }> }) {
  const n = useReveal(art.text.length, chars, 28);
  const typing = n < art.text.length;
  const on = CHANNEL[art.channel] ?? "";
  return (
    <div className="art post">
      <div className="post-tabs" aria-hidden>
        {["X", "LinkedIn"].map((c) => (
          <span key={c} className={c === on ? "on" : ""}>
            {c}
          </span>
        ))}
      </div>
      <div className="post-box">
        {art.text.slice(0, n)}
        {(typing || art.state === "drafting") && <span className="caret" aria-hidden />}
        {!art.text && art.state === "drafting" && <span className="faint">Writing the post…</span>}
      </div>
      {art.image && (
        // eslint-disable-next-line @next/next/no-img-element -- the gateway's own ad image, checked by the feed
        <img className="post-img" src={art.image} alt="The post's image" referrerPolicy="no-referrer" onError={(e) => (e.currentTarget.hidden = true)} />
      )}
      <div className="art-foot">
        <span className={`send${art.state === "done" && !typing ? " sent" : ""}`}>{art.state === "done" && !typing ? `Posted${on ? ` on ${on}` : ""} ✓` : "Post"}</span>
      </div>
    </div>
  );
}

const COLS = "ABCDEFGH";

function Sheet({ art }: { art: Extract<Artifact, { kind: "sheet" }> }) {
  const n = useReveal(art.rows.length, oneRow, 380);
  const tab = art.rows[Math.max(0, n - 1)]?.tab ?? art.tabs[0] ?? "Sheet1";
  const rows = art.rows.slice(0, n).filter((r) => r.tab === tab);
  return (
    <div className="art sheet">
      <div className="sheet-file mono">{art.file}</div>
      <table>
        <thead>
          <tr>
            <th aria-hidden />
            <th>{COLS[0]}</th>
            <th>{COLS[1]}</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((r, i) => (
            <tr key={i} className={i === rows.length - 1 ? "fresh" : undefined}>
              <th className="mono">{i + 1}</th>
              <td>{r.label}</td>
              <td className="mono num">{r.value}</td>
            </tr>
          ))}
          {Array.from({ length: Math.max(0, 4 - rows.length) }, (_, i) => (
            <tr key={`e${i}`} className="empty-row" aria-hidden>
              <th className="mono">{rows.length + i + 1}</th>
              <td />
              <td />
            </tr>
          ))}
        </tbody>
      </table>
      <div className="sheet-tabs">
        {(art.tabs.length ? art.tabs : ["Sheet1"]).map((t) => (
          <span key={t} className={t === tab ? "on" : ""}>
            {t}
          </span>
        ))}
      </div>
      <Stamp verified={art.verified} done={art.state === "done" && n >= art.rows.length} />
    </div>
  );
}

function Doc({ art }: { art: Extract<Artifact, { kind: "doc" }> }) {
  const n = useReveal(art.sections.length, oneRow, 600);
  return (
    <div className="art doc">
      <div className="sheet-file mono">{art.file}</div>
      <article className="page-doc">
        {art.sections.slice(0, n).map((s, i) => (
          <section key={i} className={i === n - 1 ? "fresh" : undefined}>
            <h3>{s.title}</h3>
            {s.text && <p>{s.text}</p>}
          </section>
        ))}
        {art.state === "drafting" && <span className="caret" aria-hidden />}
      </article>
      <Stamp verified={art.verified} done={art.state === "done" && n >= art.sections.length} />
    </div>
  );
}

function Stamp({ verified, done }: { verified: boolean | null; done: boolean }) {
  if (!done || verified === null) return null;
  return <span className={`stamp${verified ? "" : " bad"}`}>{verified ? "Verified ✓" : "Check failed"}</span>;
}

function Calc({ art }: { art: Extract<Artifact, { kind: "calc" }> }) {
  const n = useReveal(art.lines.length, oneRow, 700);
  return (
    <div className="art calc">
      <div className="sheet-file mono">utax · tax engine</div>
      <dl>
        {art.lines.slice(0, n).map((l) => (
          <div key={l.label} className={l.chosen ? "chosen" : undefined}>
            <dt>{l.label}</dt>
            <dd className="mono">{l.value}</dd>
          </div>
        ))}
      </dl>
      {n >= art.lines.length && art.lines.some((l) => l.chosen) && <p className="faint">The lower method is the one filed.</p>}
    </div>
  );
}

function Invite({ art }: { art: Extract<Artifact, { kind: "invite" }> }) {
  return (
    <div className="art invite">
      <div className="sheet-file mono">Calendar · invite</div>
      <div className="cal">
        <span className="cal-day" aria-hidden>
          📅
        </span>
        <div>
          <strong>Call with {art.with || "…"}</strong>
          <p>{art.minutes ? `${art.minutes} minutes` : "Finding a slot…"}</p>
        </div>
      </div>
      <div className="art-foot">
        <span className={`send${art.state === "done" ? " sent" : ""}`}>{art.state === "done" ? "Invite sent ✓" : "Send invite"}</span>
      </div>
    </div>
  );
}
