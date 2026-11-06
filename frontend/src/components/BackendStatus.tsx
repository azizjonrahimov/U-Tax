"use client";

import { useEffect, useState } from "react";
import { getHealth } from "@/lib/api";

type Status =
  | { kind: "loading" }
  | { kind: "ok"; version: string }
  | { kind: "down" };

const POLL_MS = 10_000;

export function BackendStatus() {
  const [status, setStatus] = useState<Status>({ kind: "loading" });

  useEffect(() => {
    let cancelled = false;
    let controller: AbortController | undefined;

    async function check() {
      controller?.abort();
      controller = new AbortController();
      const timeout = setTimeout(() => controller?.abort(), 5_000);
      try {
        const health = await getHealth({ signal: controller.signal });
        if (!cancelled) setStatus({ kind: "ok", version: health.version });
      } catch {
        if (!cancelled) setStatus({ kind: "down" });
      } finally {
        clearTimeout(timeout);
      }
    }

    check();
    const interval = setInterval(check, POLL_MS);
    return () => {
      cancelled = true;
      clearInterval(interval);
      controller?.abort();
    };
  }, []);

  const styles = {
    loading: "bg-zinc-100 text-zinc-600 dark:bg-zinc-800 dark:text-zinc-300",
    ok: "bg-emerald-50 text-emerald-700 dark:bg-emerald-950 dark:text-emerald-300",
    down: "bg-rose-50 text-rose-700 dark:bg-rose-950 dark:text-rose-300",
  }[status.kind];

  const dot = {
    loading: "bg-zinc-400 animate-pulse",
    ok: "bg-emerald-500",
    down: "bg-rose-500",
  }[status.kind];

  const label =
    status.kind === "loading"
      ? "Checking backend…"
      : status.kind === "ok"
        ? `Backend ok · v${status.version}`
        : "Backend down";

  return (
    <span
      role="status"
      aria-live="polite"
      data-status={status.kind}
      className={`inline-flex items-center gap-2 rounded-full px-3 py-1 text-sm font-medium ${styles}`}
    >
      <span aria-hidden className={`h-2 w-2 rounded-full ${dot}`} />
      {label}
    </span>
  );
}
