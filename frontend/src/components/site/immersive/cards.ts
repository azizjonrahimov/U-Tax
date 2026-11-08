// D42 P1/P2: the five glass cards (our own artifacts) and where a click on each one goes.
// The base captions are neutral on purpose: no number appears on a card unless it came from the
// firm's live public reads (live.ts).

import type { CardFace } from "./scene";

export const CARD_KEYS = ["form", "ledger", "inbox", "paid", "agents"] as const;
export type CardKey = (typeof CARD_KEYS)[number];

export const BASE_CARDS: Record<CardKey, CardFace> = {
  form: { title: "FORM\n1120", caption: "federal return · filled live · draft", hue: 265 },
  ledger: { title: "LIVE\nLEDGER", caption: "excel · formulas · every cell on screen", hue: 160 },
  inbox: { title: "INBOX", caption: "client email · answered live", hue: 210 },
  paid: { title: "PAID\nAGENT", caption: "contractor payments · tempo testnet", hue: 300 },
  agents: { title: "7 AGENTS", caption: "one firm · one human CEO", hue: 85 },
};

/** Runbook P2.3: each card opens its place in the office. */
export const CARD_LINKS: Record<CardKey, { href: string; label: string }> = {
  form: { href: "/office?d=tax&tab=form", label: "Form 1120, filled live (the tax desk)" },
  ledger: { href: "/office?d=acct&tab=excel", label: "The live ledger (the accounting desk)" },
  inbox: { href: "/office?d=intake&tab=inbox", label: "The client inbox (the intake desk)" },
  paid: { href: "/office?d=acct&tab=payments", label: "Contractor payments (the accounting desk)" },
  agents: { href: "/office", label: "The seven agents (the office)" },
};

export const baseFaces = (): CardFace[] => CARD_KEYS.map((k) => ({ ...BASE_CARDS[k] }));
