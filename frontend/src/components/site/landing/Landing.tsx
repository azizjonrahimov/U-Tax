"use client";

// D39 "Night Lime" landing (LANDING-BRIEF.md). D42: the hero is the immersive scene
// (../immersive, docs/designs/D42-RUNBOOK.md §P); every number is live from the firm's public reads
// (firm.ts) or a fixed fact of the product (the worked example $85,165 / $400 / $450 / $50, the
// $49 filing help, 0.50 pathUSD, 10/hr, 7 departments). Offline, live values show "—".

import Link from "next/link";
import dynamic from "next/dynamic";
import { ImmersivePoster } from "../immersive/Acts";
import { FinalCta, Footer, LimeBand, Pricing } from "./Closing";
import { useFirm, type Firm } from "./firm";
import { Bento, HowItWorks, TwoCards, Wrote } from "./Middle";
import "./landing.css";
import "./sections.css";

// D42 P1: the immersive hero replaces the D39 hero (S1). three.js loads only in the browser, after
// first paint; the server (and a slow network) shows the poster with the same headline and CTA.
const ImmersiveHero = dynamic(() => import("../immersive/Immersive"), { ssr: false, loading: () => <ImmersivePoster /> });

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

export function Landing() {
  const firm = useFirm();
  return (
    <div className="nl">
      <a href="#main" className="skip">
        Skip to content
      </a>
      {/* The nav stays sticky for the whole page; the immersive hero slides up under it. */}
      <header className="navbar">
        <div className="wrap">
          <Nav firm={firm} />
        </div>
      </header>
      <main id="main">
        <ImmersiveHero />
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
