"use client";

// D35 (contract v1.15 §6, task 39): the CEO downloads a case's real files (the verified
// workpaper .xlsx and memo .docx) from GET /api/docs/<case_id>/<file_name>. The session rides
// the Authorization header and the file is saved from a blob: never a ?token= link that lands
// in a log. The firm's books stay in the firm (no route serves them).

import { useRef, useState } from "react";
import { API_URL } from "@/lib/api";
import { readSession, writeSession } from "@/ceo/session";

/** Only names the gateway can serve (its own check, mirrored so no request is wasted). */
export const docName = (caseId: string | null, file: string | null | undefined): boolean =>
  !!caseId && /^case_[0-9]{1,6}$/.test(caseId) && !!file && /^[A-Za-z0-9_]{1,80}\.(xlsx|docx)$/.test(file);

export type DownloadResult = { ok: true } | { ok: false; message: string; expired?: true };

/** Fetch the file with the session and save it under its own name. */
export async function downloadDoc(session: string, caseId: string, file: string, fetchImpl: typeof fetch = fetch): Promise<DownloadResult> {
  if (!docName(caseId, file)) return { ok: false, message: "That file can't be downloaded." };
  let r: Response;
  try {
    r = await fetchImpl(`${API_URL}/api/docs/${encodeURIComponent(caseId)}/${encodeURIComponent(file)}`, {
      headers: { Authorization: `Bearer ${session}` },
      cache: "no-store",
      signal: AbortSignal.timeout(30_000),
    });
  } catch {
    return { ok: false, message: "Can't reach the firm right now. Try again in a moment." };
  }
  if (r.status === 401 || r.status === 403) {
    // The session is dead at the gateway: sign this tab out (unless it already signed in again,
    // when a late answer for the old session must not end the new one).
    const mine = readSession()?.session === session;
    if (mine) writeSession(null);
    return { ok: false, message: "Your sign-in expired. Sign in again to download.", ...(mine ? { expired: true as const } : {}) };
  }
  if (r.status === 404) return { ok: false, message: "That file isn't there (a newer version may have replaced it)." };
  if (!r.ok) return { ok: false, message: `The firm couldn't send the file (HTTP ${r.status}).` };
  let blob: Blob;
  try {
    blob = await r.blob();
  } catch {
    return { ok: false, message: "The download was interrupted. Try again." };
  }
  const url = URL.createObjectURL(blob);
  try {
    const a = document.createElement("a");
    a.href = url;
    a.download = file;
    a.rel = "noopener";
    document.body.appendChild(a);
    a.click();
    a.remove();
  } finally {
    // Long enough for the browser to start the save.
    setTimeout(() => URL.revokeObjectURL(url), 30_000);
  }
  return { ok: true };
}

/**
 * One file's download button: busy while it downloads, then the error to show (or none).
 * `onExpired`: the session died at the gateway (the caller signs out with that reason).
 */
export function useDownload(session: string | null, caseId: string | null, file: string, onExpired?: (why: string) => void) {
  const [state, setState] = useState<{ busy: boolean; error: string | null }>({ busy: false, error: null });
  const busy = useRef(false); // a double click starts one download
  const start = async () => {
    if (!session || !caseId || busy.current) return;
    busy.current = true;
    setState({ busy: true, error: null });
    let error: string | null = "The download didn't finish. Try again.";
    try {
      const r = await downloadDoc(session, caseId, file);
      error = r.ok ? null : r.message;
      if (!r.ok && r.expired) onExpired?.(r.message);
    } catch {
      /* the save itself failed: the default error says so */
    } finally {
      busy.current = false;
      setState({ busy: false, error });
    }
  };
  return { ...state, start };
}
