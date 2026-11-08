"use client";

// D39 S6 (lime band), S10 (pricing), S11 (final CTA) and S12 (footer). Copy is LANDING-BRIEF §4;
// every number is a fixed fact of the product ($85,165 → $450, $49, 0.50 pathUSD) or live.

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useEffect, useRef, useState, type FormEvent } from "react";
import { Ribbons } from "./Ribbons";
import type { Firm } from "./firm";

/** Fires once when the element is 15% visible (LANDING-BRIEF §3.5); at once under reduced motion. */
export function useSeen<T extends Element>(): [React.RefObject<T | null>, boolean] {
  const ref = useRef<T>(null);
  const [seen, setSeen] = useState(false);
  useEffect(() => {
    const el = ref.current;
    if (!el || seen) return;
    const io = new IntersectionObserver(
      ([e]) => {
        if (e.isIntersecting) {
          setSeen(true);
          io.disconnect();
        }
      },
      { threshold: 0.15 },
    );
    io.observe(el);
    return () => io.disconnect();
  }, [seen]);
  return [ref, seen];
}

/** S6: the bill we see most, words landing one by one. */
export function LimeBand() {
  const [ref, seen] = useSeen<HTMLElement>();
  const words = ["$85,165", "→", "$450."];
  return (
    <section ref={ref} className={`band${seen ? " in" : ""}`} aria-label="The bill we see most: $85,165 becomes $450.">
      <p className="band-kick mono">[ the bill we see most ]</p>
      <p className="band-line" aria-hidden="true">
        {words.map((w, i) => (
          <span key={i} className={`bw${i === 0 ? " struck" : ""}`} style={{ transitionDelay: `${i * 120}ms` }}>
            {w}
          </span>
        ))}
      </p>
      <span className="band-tile" aria-hidden="true">
        U
      </span>
      <p className="band-small">Same company. Same shares. The other legal method.</p>
    </section>
  );
}

const PLANS = [
  {
    name: "free check",
    price: "$0",
    per: "per case",
    rows: ["the memo and the workpaper", "both legal methods, checked twice", "educational estimate, not tax advice"],
    cta: { href: "/signup", label: "Get your free check" },
  },
  {
    name: "filing help",
    price: "$49",
    per: "per case",
    rows: ["the filing worksheet", "a 20-minute call", "you file it yourself; U-Tax files nothing"],
    cta: { href: "/signup", label: "Start with the free check" },
    pick: true,
  },
  {
    name: "for agents",
    price: "0.50",
    per: "pathUSD per check · testnet",
    rows: ["the franchise-tax check over HTTP", "paid by machine (MPP)", "no case, no email, no personal data"],
    cta: { href: "/office", label: "Watch the agent market" },
  },
] as const;

/** S10: three plans, Filing help highlighted (no usage claims). */
export function Pricing() {
  return (
    <section id="pricing" className="sect pricing" aria-labelledby="price-h">
      <div className="sect-head">
        <span className="label">
          <span className="ldot" aria-hidden="true" />
          pricing
        </span>
        <h2 id="price-h">
          You pay for <em>answers</em>, not seats.
        </h2>
        <p className="sect-p">The check is free. Help with the filing is one flat price. Other AI agents can buy the same check, paid by machine.</p>
      </div>
      <div className="plans">
        {PLANS.map((p) => (
          <article key={p.name} className={`card plan${"pick" in p ? " pick" : ""}`} aria-label={`${p.name}: ${p.price} ${p.per}`}>
            <div className="plan-top">
              <span className="mono plan-name">{p.name}</span>
              {"pick" in p && <span className="mono chip-lime">recommended</span>}
            </div>
            <p className="plan-price">{p.price}</p>
            <p className="mono plan-per">{p.per}</p>
            <ul className="plan-rows">
              {p.rows.map((r) => (
                <li key={r}>
                  <span className="tick" aria-hidden="true">
                    ✓
                  </span>
                  {r}
                </li>
              ))}
            </ul>
            <Link href={p.cta.href} className={`btn plan-btn ${"pick" in p ? "btn-lime sweep" : "btn-ghost"}`}>
              {p.cta.label}
            </Link>
          </article>
        ))}
      </div>
    </section>
  );
}

/** S11: the dark brand card and the lime card with the email field (opens the real signup, prefilled). */
export function FinalCta() {
  const router = useRouter();
  const [email, setEmail] = useState("");
  const go = (e: FormEvent) => {
    e.preventDefault();
    const v = email.trim();
    router.push(v ? `/signup?email=${encodeURIComponent(v.slice(0, 200))}` : "/signup");
  };
  return (
    <section className="sect final" aria-label="Start your free check">
      <Ribbons dim className="final-ribbons" />
      <div className="final-grid">
        <div className="card final-brand">
          <span className="mark big" aria-hidden="true">
            U
          </span>
          <p className="final-say">An accounting firm run by agents, with every step on the record.</p>
          <p className="mono final-mono">built at the YC Naïve hackathon</p>
        </div>
        <div className="final-lime">
          <p className="mono final-kick">what now</p>
          <h2>
            Bring the bill you <em>dread</em> most.
          </h2>
          <p className="final-p">Send your Delaware numbers. If U-Tax can&apos;t check them tonight, it will tell you so.</p>
          <form className="final-form" onSubmit={go}>
            <label htmlFor="final-email" className="sr-only">
              Your email
            </label>
            <input id="final-email" type="email" autoComplete="email" placeholder="you@startup.com" value={email} onChange={(e) => setEmail(e.target.value)} />
            <button type="submit" className="btn btn-dark">
              Start free check
            </button>
          </form>
        </div>
      </div>
    </section>
  );
}

/** S12: the mono status row (live), three columns, the legal line. */
export function Footer({ firm, memos }: { firm: Firm; memos: number | null }) {
  const open = firm.open === true;
  return (
    <footer className="foot">
      <div className="foot-status mono" role="status">
        <span>
          <span className={`sdot${open ? "" : " off"}`} aria-hidden="true" /> {open ? "firm open" : "firm resting"}
        </span>
        <span>
          <span className={`sdot${open ? "" : " off"}`} aria-hidden="true" /> {memos === null ? "—" : memos} memos sent tonight
        </span>
        <span>
          <span className="sdot" aria-hidden="true" /> educational estimates only
        </span>
      </div>
      <div className="foot-cols">
        <div>
          <p className="mono foot-h">product</p>
          <Link href="/signup">Free check</Link>
          <Link href="/office">The firm, live</Link>
          <a href="#pricing">Pricing</a>
        </div>
        <div>
          <p className="mono foot-h">company</p>
          <a href="#how">About</a>
          <a href="#product">Built at YC</a>
        </div>
        <div>
          <p className="mono foot-h">legal</p>
          <span>Not tax advice</span>
          <span>No SSN or EIN, ever</span>
        </div>
      </div>
      <p className="mono foot-legal">© 2026 U-Tax · U-Tax files nothing; estimates are educational</p>
    </footer>
  );
}
