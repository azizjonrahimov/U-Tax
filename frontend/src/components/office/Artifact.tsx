"use client";

// D37: the artifact a department is making, drawn live in its window: the email being written and
// sent, the post being typed and published, the case's documents being built (the workpaper's
// cells filling in, the memo writing section by section, the books row), the tax engine's two
// methods, a calendar invite. Text only. The parent re-mounts a view (key = the artifact's key)
// when a new artifact starts, so it plays from the beginning; the same artifact growing keeps
// playing from where it was.

import { useEffect, useState } from "react";
import { current, type Activity, type Artifact, type Book, type Desk } from "@/office/artifact";

let reducedMotion: boolean | null = null;
const reduced = () => {
  if (reducedMotion === null) reducedMotion = typeof window !== "undefined" && window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  return reducedMotion;
};

/** How much of `total` (characters, cells, sections) is shown: it catches up with what arrived, a little at a time. */
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
/** A workbook plays in a few seconds however many cells it has. */
const cellsTick = (left: number) => Math.max(1, Math.ceil(left / 10));

export function ActivityPill({ act, now }: { act: Activity | null; now: number }) {
  if (!act || !current(act.ts, now)) return null;
  const text = act.kind === "writing" ? "writing…" : act.kind === "tool" && act.tool ? `using ${act.tool}` : "thinking…";
  return <span className={`act ${act.kind}`}>{text}</span>;
}

/** The agent's own words about the work (its notes), while recent: never shown as an email. */
export function AgentNotes({ desk, now, who }: { desk: Desk; now: number; who: string }) {
  const n = desk.notes;
  if (!n || !current(n.ts, now)) return null;
  return (
    <p className="notes" aria-live="polite">
      <span className="notes-who">{who}&rsquo;s notes</span> {n.text}
    </p>
  );
}

export function ArtifactView({ art }: { art: Artifact }) {
  switch (art.kind) {
    case "mail":
      return <Mail art={art} />;
    case "post":
      return <Post art={art} />;
    case "docs":
      return <Docs art={art} />;
    case "calc":
      return <Calc art={art} />;
    case "invite":
      return <Invite art={art} />;
  }
}

function Mail({ art }: { art: Extract<Artifact, { kind: "mail" }> }) {
  const n = useReveal(art.body.length, chars, 24);
  const typing = n < art.body.length;
  const composing = art.state === "drafting";
  return (
    <div className="art mail">
      <div className="mail-head">
        <div>
          <span className="k">To</span> <span className="v">{art.to || "…"}</span>
        </div>
        <div>
          <span className="k">Subject</span> <span className="v">{art.subject || (composing ? "Composing…" : "")}</span>
        </div>
      </div>
      <div className="mail-body">
        {composing && !art.body ? <span className="faint">Composing from the firm&rsquo;s template…</span> : art.body.slice(0, n)}
        {(typing || composing) && <span className="caret" aria-hidden />}
      </div>
      {art.attachments.length > 0 && !typing && (
        <div className="attach" aria-label="Attachments">
          {art.attachments.map((f) => (
            <span key={f} className="file">
              📎 {f}
            </span>
          ))}
        </div>
      )}
      <div className="art-foot">
        <span className={`send${art.state === "done" && !typing ? " sent" : art.state === "failed" ? " failed" : ""}`}>{art.state === "failed" ? "Not sent" : art.state === "done" && !typing ? "Sent ✓" : "Send"}</span>
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
        {art.text ? art.text.slice(0, n) : <span className="faint">Writing the post…</span>}
        {(typing || art.state === "drafting") && <span className="caret" aria-hidden />}
      </div>
      {art.image && !typing && (
        // eslint-disable-next-line @next/next/no-img-element -- the gateway's own ad image, checked by the feed
        <img className="post-img" src={art.image} alt="The post's image" referrerPolicy="no-referrer" onError={(e) => (e.currentTarget.hidden = true)} />
      )}
      <div className="art-foot">
        <span className={`send${art.state === "done" && !typing ? " sent" : art.state === "failed" ? " failed" : ""}`}>{art.state === "failed" ? "Not posted" : art.state === "done" && !typing ? `Posted${on ? ` on ${on}` : ""} ✓` : "Post"}</span>
      </div>
    </div>
  );
}

const DOC_LABEL = { workpaper: "Workpaper", memo: "Memo", books: "Books" } as const;
const MAX_GRID_ROWS = 24;
const MAX_GRID_COLS = 9;
/** A value that is a number (money, a count, a percentage): right-aligned (labels like "1. Total gross assets" aren't). */
const NUMERIC = /^-?\(?\$?[\d,]+(\.\d+)?\)?%?$/;

/** The case's documents, played back in the order they were built: cells, then sections, then the books row. */
function Docs({ art }: { art: Extract<Artifact, { kind: "docs" }> }) {
  const n = useReveal(art.units.length, cellsTick, 140);
  const shown = art.units.slice(0, n);
  const done = n >= art.units.length;
  const lastWork = [...shown].reverse().find((u) => u.doc === "workpaper");
  // Played through: rest on the workpaper's computation (the books row is one line of the firm's ledger).
  const at = done && lastWork ? lastWork : (shown[shown.length - 1] ?? art.units[0]);
  const doc = at?.doc ?? "workpaper";
  const present = (["workpaper", "memo", "books"] as const).filter((d) => art[d]);
  return (
    <div className="art docs">
      <div className="doc-tabs" aria-hidden>
        {present.map((d) => (
          <span key={d} className={d === doc ? "on" : ""}>
            {art[d]?.file ?? DOC_LABEL[d]}
            {/* ticked once its own playback has finished, not before */}
            {art[d]?.verified === true && shown.filter((u) => u.doc === d).length === art.units.filter((u) => u.doc === d).length && (done || d !== doc) ? " ✓" : ""}
          </span>
        ))}
      </div>
      {doc === "memo" ? (
        <article className="page-doc">
          {(art.memo?.sections ?? []).slice(0, shown.filter((u) => u.doc === "memo").length).map((s, i, list) => (
            <section key={i} className={i === list.length - 1 ? "fresh" : undefined}>
              <h3>{s}</h3>
            </section>
          ))}
          {!done && <span className="caret" aria-hidden />}
        </article>
      ) : (
        <Grid book={art[doc]} sheet={at?.sheet} revealed={shown.filter((u) => u.doc === doc)} last={at} />
      )}
      {done && art[doc]?.verified !== null && art[doc]?.verified !== undefined && (
        <span className={`stamp${art[doc]?.verified ? "" : " bad"}`}>{art[doc]?.verified ? "Verified ✓" : "Check failed"}</span>
      )}
    </div>
  );
}

const colIndex = (letters: string) => [...letters].reduce((a, ch) => a * 26 + ch.charCodeAt(0) - 64, 0) - 1;

function Grid({ book, sheet, revealed, last: lastCell }: { book: Book | null; sheet: string | undefined; revealed: { sheet?: string; cell?: string }[]; last: { cell?: string } | undefined }) {
  if (!book) return null;
  const tab = sheet ?? book.sheets[0] ?? "Sheet1";
  const cells = book.cells[tab];
  const sheetTabs = (
    <div className="sheet-tabs">
      {(book.sheets.length ? book.sheets : [tab]).map((t) => (
        <span key={t} className={t === tab ? "on" : ""}>
          {t}
        </span>
      ))}
    </div>
  );
  if (!cells) {
    // A public reader: one line per build step on its tab.
    const lines = book.lines.filter((l) => l.tab === tab).slice(0, revealed.filter((u) => u.sheet === tab).length);
    return (
      <div className="sheet">
        <table>
          <tbody>
            {lines.map((l, i) => (
              <tr key={i} className={i === lines.length - 1 ? "fresh" : undefined}>
                <th className="mono">{i + 1}</th>
                <td>{l.text}</td>
              </tr>
            ))}
          </tbody>
        </table>
        {sheetTabs}
      </div>
    );
  }
  const shown = new Set(revealed.filter((u) => u.sheet === tab && u.cell).map((u) => u.cell as string));
  let minRow = Infinity;
  let maxRow = 1;
  let maxCol = 1;
  for (const ref of Object.keys(cells)) {
    const m = /^([A-Z]{1,2})([0-9]+)$/.exec(ref);
    if (!m) continue;
    minRow = Math.min(minRow, Number(m[2]));
    maxRow = Math.max(maxRow, Number(m[2]));
    maxCol = Math.max(maxCol, colIndex(m[1]) + 1);
  }
  // The rows written, up to MAX_GRID_ROWS ending at the newest one (a ledger row can be row 400).
  const last = Math.min(maxRow, Math.max(minRow === Infinity ? 1 : minRow, maxRow));
  const first = Math.max(minRow === Infinity ? 1 : Math.min(minRow, last), last - MAX_GRID_ROWS + 1);
  const rowNums = Array.from({ length: last - first + 1 }, (_, i) => first + i);
  const cols = Math.min(maxCol, MAX_GRID_COLS);
  const letters = Array.from({ length: cols }, (_, i) => String.fromCharCode(65 + i));
  return (
    <div className="sheet">
      <table>
        <thead>
          <tr>
            <th aria-hidden />
            {letters.map((l) => (
              <th key={l}>{l}</th>
            ))}
          </tr>
        </thead>
        <tbody>
          {rowNums.map((row) => (
            <tr key={row}>
              <th className="mono">{row}</th>
              {letters.map((l, ci) => {
                const ref = `${l}${row}`;
                const v = shown.has(ref) ? (cells[ref] ?? "") : "";
                return (
                  <td key={ref} className={`${ci > 0 && NUMERIC.test(v) ? "mono num" : ""}${lastCell?.cell === ref ? " fresh" : ""}`}>
                    {v}
                  </td>
                );
              })}
            </tr>
          ))}
        </tbody>
      </table>
      {sheetTabs}
    </div>
  );
}

function Calc({ art }: { art: Extract<Artifact, { kind: "calc" }> }) {
  const n = useReveal(art.lines.length, () => 1, 700);
  return (
    <div className="art calc">
      <div className="sheet-file mono">utax · tax engine</div>
      {art.lines.length === 0 && <p className="faint">Running both methods…</p>}
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
