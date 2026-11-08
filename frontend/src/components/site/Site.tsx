"use client";

// D32 client pages: the landing page, the client sign-up and the CEO sign-in. Light paper
// (DESIGN.md): clients reach these on a laptop. Every figure is real: the hero's math is the
// firm's first real case; the live line reads /api/state with the public theater token.

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useEffect, useId, useRef, useState, type CSSProperties, type ReactNode } from "react";
import { apiFetch, ApiError, API_URL } from "@/lib/api";
import { TOKEN_KEY } from "@/ceo/ceo";
import { numbersFromState, stateUrl } from "@/theater/live";
import { formatCents } from "@/theater/format";
import { friendlyError } from "@/talk/talk";
import { SEAT_NAME, SEAT_ROLE, SEATS } from "@/office/workspace";
import "./site.css";

const SEAT_COLOR: Readonly<Record<string, string>> = { cos: "#e5372a", intake: "#2f7fd6", tax: "#c27a00", qa: "#1f9d5a", acct: "#9b4fd6", mkt: "#d6407f", hr: "#0f9b91" };
const DISCLAIMER = "Educational estimate — not tax advice.";

function Top() {
  return (
    <header className="top">
      <div className="wrap">
        <Link href="/" className="brand" aria-label="U-Tax home">
          <span className="mark" aria-hidden>
            U
          </span>
          U-Tax
        </Link>
        <nav aria-label="Site">
          <Link href="/#how" className="hide-sm">
            How it works
          </Link>
          <Link href="/office" className="hide-sm">
            Watch the firm work
          </Link>
          <Link href="/login">CEO sign-in</Link>
          <Link href="/signup" className="btn primary">
            Get my free checkup
          </Link>
        </nav>
      </div>
    </header>
  );
}

function Foot() {
  return (
    <footer className="foot">
      <div className="wrap">
        <span>
          <strong>{DISCLAIMER}</strong> U-Tax is a firm run by AI agents. A human CEO approves what leaves the firm.
        </span>
        <nav aria-label="Footer">
          <Link href="/talk">Talk to U-Tax</Link>
          <Link href="/office">The office</Link>
        </nav>
      </div>
    </footer>
  );
}

function Shell({ children }: { children: ReactNode }) {
  return (
    <div className="site">
      <a href="#main" className="skip">
        Skip to content
      </a>
      <Top />
      {children}
      <Foot />
    </div>
  );
}

/** The firm's live numbers for the landing page (public, read-only); null until they load or if the gateway is away. */
function useLiveSavings() {
  const [live, setLive] = useState<{ saved: number; clients: number } | null>(null);
  useEffect(() => {
    if (!process.env.NEXT_PUBLIC_API_URL) return;
    let alive = true;
    const load = async () => {
      try {
        const r = await fetch(stateUrl({ base: API_URL, token: process.env.NEXT_PUBLIC_THEATER_TOKEN ?? "" }), { cache: "no-store", signal: AbortSignal.timeout(8000) });
        const n = r.ok ? numbersFromState(await r.json()) : null;
        if (alive && n) setLive({ saved: n.money.tax_saved_cents, clients: n.firm.clients });
      } catch {
        /* the line just stays hidden */
      }
    };
    void load();
    const t = setInterval(() => void load(), 15_000);
    return () => {
      alive = false;
      clearInterval(t);
    };
  }, []);
  return live;
}

export function Landing() {
  const live = useLiveSavings();
  return (
    <Shell>
      <main id="main">
        <section className="hero">
          <div className="wrap">
            <div>
              <h1>Delaware may have billed you for the wrong number.</h1>
              <p className="lead">
                Delaware&rsquo;s franchise-tax notice uses the authorized shares method, and for most startups that is the expensive one. U-Tax
                runs both legal methods on your numbers, has QA re-check every figure, and emails you the estimate. Free.
              </p>
              <div className="ctas">
                <Link href="/signup" className="btn primary">
                  Get my free checkup
                </Link>
                <Link href="/talk" className="btn secondary">
                  Talk to U-Tax
                </Link>
              </div>
              {live && (live.saved > 0 || live.clients > 0) && (
                <p className="live" aria-live="polite">
                  <span className="dot" aria-hidden />
                  {live.saved > 0 ? (
                    <>
                      Live from the firm: <span className="mono">{formatCents(live.saved)}</span> saved for {live.clients} {live.clients === 1 ? "client" : "clients"} so far
                    </>
                  ) : (
                    <>
                      Live from the firm: {live.clients} {live.clients === 1 ? "client" : "clients"} being worked right now
                    </>
                  )}
                </p>
              )}
            </div>
            <div className="math" aria-label="A real case">
              <h2>A real case from our first night</h2>
              <p className="sub">10,000,000 authorized shares, 8,000,000 issued, $500,000 in gross assets.</p>
              <div className="was mono" aria-label="Delaware's notice: $85,165">
                $85,165
              </div>
              <div className="now mono" aria-label="What they owed: $450">
                → $450
              </div>
              <dl className="ledger">
                <div>
                  <dt>Authorized shares method</dt>
                  <dd className="mono">$85,165</dd>
                </div>
                <div>
                  <dt>Assumed par value method</dt>
                  <dd className="mono">$400</dd>
                </div>
                <div>
                  <dt>Annual report fee</dt>
                  <dd className="mono">$50</dd>
                </div>
                <div className="total">
                  <dt>Owed with the lower method</dt>
                  <dd className="mono">$450</dd>
                </div>
              </dl>
              <p className="note">{DISCLAIMER} Your numbers decide which method is lower.</p>
            </div>
          </div>
        </section>

        <section className="how" id="how">
          <div className="wrap">
            <h2 className="sec">Seven departments work your case, and every step is on the record.</h2>
            <p className="sec-lead">
              Each department is an AI agent with one job. They hand your case along the line below, and the CEO approves anything that leaves the
              firm.
            </p>
            <ol className="pipe">
              {SEATS.map((seat) => (
                <li key={seat} style={{ "--c": SEAT_COLOR[seat] } as CSSProperties}>
                  <b>
                    <i aria-hidden />
                    {SEAT_NAME[seat]}
                  </b>
                  <span>{SEAT_ROLE[seat]}</span>
                </li>
              ))}
            </ol>
            <div className="after">
              <Link href="/office">Watch them work live →</Link>
              <span className="note">Every move in the office is drawn from the firm&rsquo;s action log.</span>
            </div>
          </div>
        </section>

        <section className="get">
          <div className="wrap">
            <div>
              <h2 className="sec">What lands in your inbox</h2>
              <p className="sec-lead">A short memo you can check line by line, usually within minutes of your last reply.</p>
              <Link href="/signup" className="btn primary">
                Get my free checkup
              </Link>
            </div>
            <ul>
              <li>
                <b>Both methods, computed.</b> The authorized shares method and the assumed par value method, side by side.
              </li>
              <li>
                <b>The lower amount, with the fee.</b> What you&rsquo;d owe with the method that applies, plus the $50 annual report fee.
              </li>
              <li>
                <b>Every number re-checked.</b> QA re-runs the engine before anything is sent; a mismatch stops the memo.
              </li>
              <li>
                <b>The due date and what to file.</b> Including a flag when Form 5472 may apply.
              </li>
            </ul>
          </div>
        </section>
      </main>
    </Shell>
  );
}

// ------------------------------------------------------------------ client sign-up (§5a)

/** v1.12.2 §5a: always 200 {ok: true}; the answer never says whether this email is already a client. */
interface SignupOk {
  ok: true;
}

export function Signup() {
  const ids = { name: useId(), company: useId(), email: useId(), err: useId() };
  const [form, setForm] = useState({ name: "", company: "", email: "" });
  const [touched, setTouched] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [done, setDone] = useState(false);
  /** One id per form, made on the first submit, so a retry after a network error never opens a second case. */
  const requestId = useRef<string | null>(null);

  const emailOk = /^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(form.email.trim());
  const missing = { name: !form.name.trim(), company: !form.company.trim(), email: !emailOk };
  const valid = !missing.name && !missing.company && !missing.email;

  const submit = async () => {
    setTouched(true);
    if (!valid || busy) return;
    setBusy(true);
    setError(null);
    requestId.current ??= typeof crypto !== "undefined" && "randomUUID" in crypto ? crypto.randomUUID() : `r${Date.now()}${Math.random().toString(36).slice(2)}`;
    try {
      await apiFetch<SignupOk>("/signup", {
        method: "POST",
        body: JSON.stringify({ name: form.name.trim(), company: form.company.trim(), email: form.email.trim(), request_id: requestId.current }),
      });
      setDone(true);
    } catch (e) {
      setError(e instanceof ApiError ? friendlyError(e.status, e.code, e.message) : "Something went wrong. Please try again.");
    } finally {
      setBusy(false);
    }
  };

  const field = (k: keyof typeof form, label: string, type: string, auto: string, hint?: string) => {
    const bad = touched && missing[k];
    return (
      <div className="field">
        <label htmlFor={ids[k]}>{label}</label>
        <input
          id={ids[k]}
          type={type}
          autoComplete={auto}
          value={form[k]}
          onChange={(e) => setForm((f) => ({ ...f, [k]: e.target.value }))}
          aria-invalid={bad || undefined}
          aria-describedby={bad ? `${ids[k]}-e` : undefined}
          maxLength={200}
          required
        />
        {hint && !bad && <p className="hint">{hint}</p>}
        {bad && (
          <p className="err" id={`${ids[k]}-e`}>
            {k === "email" ? "Enter an email address we can reply to." : k === "name" ? "Enter your name." : "Enter your company's name."}
          </p>
        )}
      </div>
    );
  };

  return (
    <Shell>
      <main id="main" className="form-page">
        <div className="card">
          {done ? (
            <div className="done-box" role="status">
              <h1>Thanks. Check your email.</h1>
              <p className="lead">Look for your free checkup request: one email asking for the numbers we need. Reply to it, and the firm takes it from there.</p>
              <p className="alt">
                Prefer to answer out loud? <Link href="/talk">Talk to U-Tax</Link>. Want to watch your case move? <Link href="/office">Open the office</Link>.
              </p>
            </div>
          ) : (
            <form
              noValidate
              onSubmit={(e) => {
                e.preventDefault();
                void submit();
              }}
            >
              <h1>Get your free checkup</h1>
              <p className="lead">Three fields. We email you to ask for your numbers, then send an estimate you can check line by line.</p>
              {field("name", "Your name", "text", "name")}
              {field("company", "Company", "text", "organization", "Your Delaware corporation's name.")}
              {field("email", "Email", "email", "email", "We reply here, and only about this case.")}
              {error && (
                <p className="form-err" role="alert" id={ids.err}>
                  {error}
                </p>
              )}
              <button type="submit" className="btn primary" disabled={busy}>
                {busy ? "Opening your case…" : "Get my free checkup"}
              </button>
              <p className="alt">{DISCLAIMER}</p>
            </form>
          )}
        </div>
      </main>
    </Shell>
  );
}

// ------------------------------------------------------------------ CEO sign-in

export function Login() {
  const router = useRouter();
  const id = useId();
  const [token, setToken] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const submit = async () => {
    const t = token.trim();
    if (!t || busy) return;
    setBusy(true);
    setError(null);
    try {
      // The admin token's check, as the CEO console does it: the token rides the header, never a URL.
      const r = await fetch(`${API_URL}/api/state`, { headers: { Authorization: `Bearer ${t}` }, cache: "no-store", signal: AbortSignal.timeout(10_000) });
      if (r.status === 401 || r.status === 403) {
        setError("That isn't the admin token.");
        return;
      }
      if (!r.ok) throw new Error(`HTTP ${r.status}`);
      try {
        sessionStorage.setItem(TOKEN_KEY, t); // this tab only, as on /ceo
      } catch {
        /* storage off: /ceo asks again */
      }
      setToken("");
      router.push("/office");
    } catch {
      setError("Can't reach the firm right now. Try again in a moment.");
    } finally {
      setBusy(false);
    }
  };

  return (
    <Shell>
      <main id="main" className="form-page">
        <div className="card">
          <form
            onSubmit={(e) => {
              e.preventDefault();
              void submit();
            }}
          >
            <h1>CEO sign-in</h1>
            <p className="lead">The admin token stays in this browser tab only, and goes to the firm in a header, never in a link.</p>
            {/* a hidden username lets password managers file the token under the firm */}
            <input type="text" name="username" autoComplete="username" value="ceo" readOnly hidden />
            <div className="field">
              <label htmlFor={id}>Admin token</label>
              <input id={id} type="password" autoComplete="current-password" value={token} onChange={(e) => setToken(e.target.value)} aria-invalid={error ? true : undefined} aria-describedby={error ? `${id}-e` : undefined} />
              {error && (
                <p className="err" id={`${id}-e`} role="alert">
                  {error}
                </p>
              )}
            </div>
            <button type="submit" className="btn primary" disabled={busy || !token.trim()}>
              {busy ? "Checking…" : "Sign in to the office"}
            </button>
            <p className="alt">
              A client? <Link href="/signup">Get your free checkup</Link> instead.
            </p>
          </form>
        </div>
      </main>
    </Shell>
  );
}
