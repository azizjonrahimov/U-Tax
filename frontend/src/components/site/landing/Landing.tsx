"use client";

// D39 "Night Lime" landing (LANDING-BRIEF.md). The hero is a port of
// docs/designs/landing/hero-prototype.html; every number is live from the firm's public reads
// (firm.ts) or a fixed fact of the product (the worked example $85,165 / $400 / $450 / $50, the
// $49 filing help, 0.50 pathUSD, 10/hr, 7 departments). Offline, live values show "—".

import Link from "next/link";
import { Ribbons } from "./Ribbons";
import { FinalCta, Footer, LimeBand, Pricing } from "./Closing";
import { useFirm, type Firm } from "./firm";
import { Bento, HowItWorks, TwoCards, Wrote } from "./Middle";
import "./landing.css";
import "./sections.css";

function Nav({ firm }: { firm: Firm }) {
  const open = firm.open === true;
  return (
    <nav className="top" aria-label="Main">
      <Link href="/" className="brand" aria-label="U-Tax home">
        <span className="mark" aria-hidden="true">
          U
        </span>
        U-Tax
      </Link>
      <div className="links">
        <a href="#product">Product</a>
        <a href="#how">How it works</a>
        <Link href="/office">The firm, live</Link>
        <a href="#pricing">Pricing</a>
      </div>
      <span className={`status${open ? "" : " resting"}`} role="status">
        <span className="dot" aria-hidden="true" />
        <span>{open ? `firm open · ${firm.agents ?? 7} agents` : firm.open === false ? "firm resting" : "—"}</span>
      </span>
      <Link href="/signup" className="btn btn-lime">
        Start free check
      </Link>
    </nav>
  );
}

function HeroCard({ firm }: { firm: Firm }) {
  const c = firm.latest;
  const running = firm.open === true && c !== null && !c.done;
  const runs = firm.runs;
  return (
    <div className="card flow reveal d3" role="figure" aria-label={c ? `The latest U-Tax case, ${c.name}: ${c.math ?? "math pending"}, ${c.done ? "memo sent" : "running"}.` : "The latest U-Tax case (the firm is resting)."}>
      <div className="flow-head">
        <span>Delaware franchise tax</span>
        <span className="m">5 steps / 1 branch</span>
        <span className={`run${firm.open === true ? "" : " off"}`}>{firm.open === false ? "resting" : firm.open === null || c === null ? "—" : running ? "running" : "done"}</span>
      </div>
      <div className="canvas" aria-hidden="true">
        <svg className="edges" viewBox="0 0 100 250" preserveAspectRatio="none">
          <path vectorEffect="non-scaling-stroke" d="M29 125 C36 125 35 55 42 55" />
          <path vectorEffect="non-scaling-stroke" d="M29 125 C36 125 35 195 42 195" className="dim" />
          <path vectorEffect="non-scaling-stroke" d="M75 55 C79 55 77 118 81 118" />
          <path vectorEffect="non-scaling-stroke" d="M75 195 C79 195 77 132 81 132" className="dim" />
        </svg>
        <div className={`node${c ? " done" : ""}`} style={{ left: 0, top: 98, width: "29%" }}>
          <b>Email received</b>
          <span>intake</span>
        </div>
        <span className="tag" style={{ left: "29%", top: 66 }}>
          min()
        </span>
        <span className="tag" style={{ left: "29%", top: 174 }}>
          if 25%+
        </span>
        <div className={`node${c?.math ? " done" : ""}`} style={{ left: "42%", top: 28, width: "33%" }}>
          <b>{c?.math ?? "—"}</b>
          <span>tax · engine</span>
        </div>
        <div className="node" style={{ left: "42%", top: 168, width: "33%" }}>
          <b>Form 5472 flag</b>
          <span>tax · rule</span>
        </div>
        <div className={`node${running ? " wait" : c?.done ? " done" : ""}`} style={{ left: "81%", top: 98, width: "19%" }}>
          <b>QA</b>
          <span>then memo</span>
        </div>
      </div>
      <div className="runs">
        <div style={{ marginBottom: 6 }}>last memos</div>
        {runs === null ? (
          <div className="row">
            <i />
            <span>{firm.open === false ? "the firm is resting" : "—"}</span>
          </div>
        ) : runs.length === 0 ? (
          <div className="row">
            <i />
            <span>no memo yet tonight</span>
          </div>
        ) : (
          runs.map((r) => (
            <div className="row" key={r.key}>
              <i />
              <span className="t">{r.time}</span>
              <span>{r.text}</span>
              <span className="ms">{r.aside}</span>
            </div>
          ))
        )}
      </div>
    </div>
  );
}

function Hero({ firm }: { firm: Firm }) {
  const badge = firm.open === true && firm.checked !== null && firm.checked > 0 ? `live tonight / ${firm.checked} ${firm.checked === 1 ? "startup" : "startups"} checked` : "open beta / delaware franchise tax";
  return (
    <section className="hero" aria-labelledby="hero-h">
      <div>
        <span className="badge reveal d1">
          <span className="dot" aria-hidden="true" />
          <span>{badge}</span>
        </span>
        <h1 id="hero-h" className="reveal d2">
          The tax firm runs
          <br />
          <em>itself</em>.
        </h1>
        <p className="sub reveal d3">Seven AI departments find the client, check the math twice and send the memo. One human signs off when it matters.</p>
        <div className="ctas reveal d4">
          <Link href="/signup" className="btn btn-lime">
            Get your free check
          </Link>
          <Link href="/office" className="btn btn-ghost">
            <span aria-hidden="true">▶&nbsp;</span> Watch the firm work, live
          </Link>
        </div>
        <p className="fine reveal d5">free / educational estimate, not tax advice / no SSN, ever</p>
      </div>
      <HeroCard firm={firm} />
    </section>
  );
}

export function Landing() {
  const firm = useFirm();
  return (
    <div className="nl">
      <a href="#main" className="skip">
        Skip to content
      </a>
      {/* The nav stays sticky for the whole page; the hero slides up under it (the ribbons run behind it). */}
      <header className="navbar">
        <div className="wrap">
          <Nav firm={firm} />
        </div>
      </header>
      <main id="main">
        <div className="page">
          <Ribbons />
          <div className="dots" aria-hidden="true" />
          <div className="vignette" aria-hidden="true" />
          <div className="wrap">
            <Hero firm={firm} />
          </div>
        </div>
        <HowItWorks firm={firm} />
        <LimeBand />
        <TwoCards firm={firm} />
        <Bento firm={firm} />
        <Wrote firm={firm} />
        <Pricing />
        <FinalCta />
      </main>
      <Footer firm={firm} memos={firm.log ? firm.log.memos : null} />
    </div>
  );
}
