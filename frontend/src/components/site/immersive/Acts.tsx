// D42 P1: the immersive hero's text (the acts of docs/designs/immersive/immersive.html) and the
// poster, the static hero the server renders (and every browser without WebGL 2 keeps).
// No three.js here: this file is safe to render on the server.

import Link from "next/link";
import type { ReactNode } from "react";
import { CARD_KEYS, CARD_LINKS } from "./cards";
import "./immersive.css";

export interface Act {
  from: number;
  to: number;
  body: ReactNode;
}

const Cta = ({ second }: { second: "office" | "ceo" }) => (
  <div className="imm-cta">
    <Link href="/signup" className="imm-b imm-b1">
      Free tax checkup
    </Link>
    {second === "office" ? (
      <Link href="/office" className="imm-b imm-b2">
        Watch the office <span aria-hidden="true">↗</span>
      </Link>
    ) : (
      <Link href="/ceo" className="imm-b imm-b2">
        Open the CEO console <span aria-hidden="true">↗</span>
      </Link>
    )}
  </div>
);

export const ACT_ZERO = (
  <>
    <h1 id="hero-h" className="dot-display imm-dot">
      An AI-run
      <br />
      tax firm
    </h1>
    <p className="imm-cap">
      Seven agents answer the email, fill the forms, keep the books and pay their contractors. <em>Live.</em>
    </p>
    <Cta second="office" />
    <p className="imm-fine">free / educational estimate, not tax advice / no SSN, ever</p>
  </>
);

/** The scroll story: progress windows from immersive.html. */
export const ACTS: Act[] = [
  { from: 0, to: 0.17, body: ACT_ZERO },
  {
    from: 0.2,
    to: 0.36,
    body: (
      <>
        <h2 className="dot-display imm-dot">
          Every dot
          <br />
          is a move
        </h2>
        <p className="imm-cap">An email answered, a line filled, a payment sent. Colored by the department that did it.</p>
      </>
    ),
  },
  {
    from: 0.4,
    to: 0.68,
    body: (
      <>
        <h2 className="dot-display imm-dot">
          The work,
          <br />
          on glass
        </h2>
        <p className="imm-cap">Real artifacts from the action log: the federal return, the live ledger, the inbox, the receipts. Click a card to open it in the office.</p>
        <ul className="imm-sr">
          {CARD_KEYS.map((k) => (
            <li key={k}>
              <Link href={CARD_LINKS[k].href}>{CARD_LINKS[k].label}</Link>
            </li>
          ))}
        </ul>
      </>
    ),
  },
  {
    from: 0.7,
    to: 0.82,
    body: (
      <>
        <h2 className="dot-display imm-dot">
          Built to
          <br />
          be checked
        </h2>
        <p className="imm-cap">
          Every number comes from the engine, is checked by QA, and is <em>shown, line by line.</em>
        </p>
      </>
    ),
  },
  {
    from: 0.84,
    to: 1.01,
    body: (
      <>
        <h2 className="dot-display imm-dot">
          One firm.
          <br />
          One human.
        </h2>
        <p className="imm-cap">
          You give the order by voice. <em>The firm does the rest.</em>
        </p>
        <Cta second="ceo" />
      </>
    ),
  },
];

/** Glass pill nav (runbook P3.6): OFFICE —— CEO CONSOLE. */
export const PillNav = () => (
  <nav className="imm-pill" aria-label="The firm">
    <Link href="/office">Office</Link>
    <i aria-hidden="true" />
    <Link href="/ceo">CEO console</Link>
  </nav>
);

/** The static hero: the poster frame (our emblem) with act 0 over it. */
export function ImmersivePoster() {
  return (
    <section className="imm imm-static" aria-labelledby="hero-h">
      <div className="imm-stage">
        {/* eslint-disable-next-line @next/next/no-img-element -- a fixed-size background poster, no layout to optimise */}
        <img className="imm-poster" src="/immersive/poster.webp" alt="" aria-hidden="true" />
        <div className="imm-scrim" aria-hidden="true" />
        <PillNav />
        <div className="imm-act on">{ACT_ZERO}</div>
      </div>
    </section>
  );
}
