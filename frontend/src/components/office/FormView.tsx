"use client";

// D42 T3 (runbook §T3): the Tax desk's "Form" view. The draft Form 1120 page, with each value typed
// onto its field as form.step events arrive (450 ms apart): the cursor glides to the field, the
// value types in (ink blue, right-aligned, mono), a lime ring flashes, the page scrolls to keep the
// field in the middle, and a page change crossfades. Always a DRAFT: the firm never files a return.

import { useEffect, useRef, useState, type CSSProperties } from "react";
import { originOf } from "@/theater/live";
import { lineText, type FormDoc } from "@/office/forms";
import { TIMING, usePlayback } from "./DocPlayback";

const GATEWAY_ORIGIN = originOf(process.env.NEXT_PUBLIC_API_URL);

/**
 * The page image. A gateway page (/assets/forms/…) is admin/session only: it is fetched with the
 * CEO's session as a Bearer (never in a URL) and shown from a blob URL. A local fixture loads as is.
 */
function usePageImage(path: string | undefined, session: string | null): string | null {
  const [blob, setBlob] = useState<{ path: string; url: string } | null>(null);
  const gateway = !!path && path.startsWith("/assets/");
  useEffect(() => {
    if (!path || !gateway || !GATEWAY_ORIGIN || !session) return;
    const ac = new AbortController();
    let url: string | null = null;
    fetch(`${GATEWAY_ORIGIN}${path}`, { headers: { Authorization: `Bearer ${session}` }, cache: "no-store", signal: ac.signal })
      .then((r) => (r.ok ? r.blob() : Promise.reject(new Error(`HTTP ${r.status}`))))
      .then((b) => {
        if (!b.type.startsWith("image/")) return;
        url = URL.createObjectURL(b);
        setBlob({ path, url });
      })
      .catch(() => {
        /* no image: the lines list still shows */
      });
    return () => {
      ac.abort();
      if (url) URL.revokeObjectURL(url);
    };
  }, [path, gateway, session]);
  if (!path) return null;
  if (!gateway) return path;
  return blob && blob.path === path ? blob.url : null;
}

export function FormView({ form, client, base, session }: { form: FormDoc; client: string; base: number; session: string | null }) {
  const { shown, playing, reduce } = usePlayback(form.fields.length, TIMING.formStepMs, base);
  const scroll = useRef<HTMLDivElement>(null);
  const page = useRef<HTMLDivElement>(null);
  const on = form.fields.slice(0, shown);
  const active = playing !== null ? form.fields[playing - 1] : undefined;
  const pageNo = active?.page ?? on.at(-1)?.page ?? form.fields[0]?.page ?? 0;
  const img = usePageImage(form.images[pageNo], session);
  const drawable = img !== null && form.fields.some((f) => f.rect);

  // Keep the field being filled in the middle third of the window.
  useEffect(() => {
    if (!active?.rect || !scroll.current || !page.current) return;
    const h = page.current.getBoundingClientRect().height;
    const y = ((active.rect[1] + active.rect[3]) / 2) * h;
    scroll.current.scrollTo({ top: Math.max(0, y - scroll.current.clientHeight / 2), behavior: reduce ? "auto" : "smooth" });
  }, [active, reduce]);

  return (
    <section className="win fv glass" aria-label={`Form 1120 draft for ${client}`}>
      <div className="win-bar">
        <span className="lights" aria-hidden>
          <i />
          <i />
          <i />
        </span>
        <div className="tabs" aria-hidden>
          <span className="tab on">Form 1120</span>
        </div>
      </div>
      <div className="win-addr">
        <span className="lock" aria-hidden>
          ⌁
        </span>
        <span className="mono addr">irs.gov/pub/irs-pdf/f1120.pdf · draft for {client}</span>
        <span className="badge needs_approval">DRAFT · estimate for review · not filed</span>
      </div>
      <div className="fv-body">
        {drawable ? (
          <div className="fv-scroll" ref={scroll}>
            <div className="fv-page" key={pageNo} ref={page}>
              {/* eslint-disable-next-line @next/next/no-img-element -- the firm's own rendered form page */}
              <img src={img} alt={`Form 1120, page ${pageNo + 1} (draft)`} draggable={false} />
              <div className="fv-layer" aria-hidden>
                {on
                  .filter((f) => f.page === pageNo && f.rect)
                  .map((f) => {
                    const [x0, y0, x1, y1] = f.rect!;
                    const now = active?.n === f.n;
                    return (
                      <span
                        key={f.n}
                        className={now ? "fv-val now" : "fv-val"}
                        style={{ left: `${x0 * 100}%`, top: `${y0 * 100}%`, width: `${(x1 - x0) * 100}%`, height: `${(y1 - y0) * 100}%`, "--n": Math.max(1, f.value.length) } as CSSProperties}
                      >
                        <span className="fv-text">{f.value}</span>
                      </span>
                    );
                  })}
                {active?.rect && !reduce && (
                  <div className="agent fv-agent" style={{ left: `${((active.rect[0] + active.rect[2]) / 2) * 100}%`, top: `${((active.rect[1] + active.rect[3]) / 2) * 100}%` }}>
                    <svg width="18" height="20" viewBox="0 0 18 20">
                      <path d="M1 1 L16 9 L9 11 L6 18 Z" />
                    </svg>
                    <span className="who">Tax</span>
                  </div>
                )}
              </div>
            </div>
          </div>
        ) : (
          <div className="fv-nodraw">
            <p>The form itself shows to signed-in readers; here are the lines being filled.</p>
          </div>
        )}
        <aside className="fv-lines" aria-label="Lines filled">
          <h2>
            Lines · {shown} of {form.of}
          </h2>
          <ol>
            {on.map((f) => {
              const t = lineText(f);
              return (
                <li key={f.n} className={f.key === "31" ? "lime" : active?.n === f.n ? "now" : undefined}>
                  <span>{t.label}</span>
                  {t.value && <b className="mono">{t.value}</b>}
                </li>
              );
            })}
          </ol>
        </aside>
      </div>
    </section>
  );
}
