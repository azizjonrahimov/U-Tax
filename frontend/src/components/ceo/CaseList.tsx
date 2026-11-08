"use client";

// F1: the firm's cases in the CEO console, each with its status and its verified documents to
// download, so a case's files stay reachable after the Accounting desk has moved on. Which cases
// and which files: src/ceo/caseRows.ts (only files the gateway serves; the books stay in the firm).

import type { CaseRow } from "@/ceo/ceo";
import { caseRows, fileKind, statusWord } from "@/ceo/caseRows";
import { useDownload } from "@/office/docs";

function Download({ token, caseId, name, file, onExpired }: { token: string; caseId: string; name: string; file: string; onExpired: (why: string) => void }) {
  const dl = useDownload(token, caseId, file, onExpired);
  return (
    <span className="dl">
      <button className="secondary" disabled={dl.busy} aria-label={`Download ${name}'s ${fileKind(file)}`} title={`Download ${file}`} onClick={dl.start}>
        {dl.busy ? "Downloading…" : <><span aria-hidden="true">⬇ </span>{fileKind(file)}</>}
      </button>
      {dl.error && (
        <span className="error" role="alert">
          {dl.error}
        </span>
      )}
    </span>
  );
}

export function CaseList({ cases, served, token, onExpired }: { cases: readonly CaseRow[]; served: Record<string, string[]>; token: string; onExpired: (why: string) => void }) {
  const rows = caseRows(cases, served);
  if (!rows.length) return null;
  return (
    <section aria-labelledby="cases-h" className="cases">
      <h2 id="cases-h">Cases</h2>
      <ul>
        {rows.map((c) => (
          <li key={c.caseId} className="case-row" aria-labelledby={`case-${c.caseId}`}>
            <div className="case-head">
              <strong id={`case-${c.caseId}`}>{c.name}</strong>
              <span className={`case-status${c.flagged ? " flagged" : ""}`}>{c.flagged ? "Flagged" : statusWord(c.status)}</span>
            </div>
            {c.files.length > 0 ? (
              <div className="case-docs">
                <span className="dim">Verified ✓</span>
                {c.files.map((f) => (
                  <Download key={f} token={token} caseId={c.caseId} name={c.name} file={f} onExpired={onExpired} />
                ))}
              </div>
            ) : (
              <p className="dim case-none">{c.flagged ? "Documents hidden: flagged by QA." : "No verified documents yet."}</p>
            )}
          </li>
        ))}
      </ul>
    </section>
  );
}
