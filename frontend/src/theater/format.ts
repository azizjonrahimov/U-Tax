// Display formatting. Amounts are integer cents on the wire; no float math here.

const group = (digits: string): string =>
  digits.replace(/\B(?=(\d{3})+(?!\d))/g, ",");

/** 8516500 → "$85,165"; 8516550 → "$85,165.50"; -1234 → "-$12.34". */
export function formatCents(cents: number): string {
  if (!Number.isSafeInteger(cents)) throw new RangeError(`not integer cents: ${cents}`);
  const sign = cents < 0 ? "-" : "";
  const abs = Math.abs(cents);
  const dollars = Math.floor(abs / 100);
  const rem = abs % 100;
  const frac = rem === 0 ? "" : `.${String(rem).padStart(2, "0")}`;
  return `${sign}$${group(String(dollars))}${frac}`;
}

/** 10000000 → "10,000,000". */
export function formatInt(n: number): string {
  if (!Number.isSafeInteger(n)) throw new RangeError(`not an integer: ${n}`);
  return (n < 0 ? "-" : "") + group(String(Math.abs(n)));
}

/** Decimal USD string → display: "0.0034" → "$0.0034", "11.38" → "$11.38", "9.1" → "$9.10". */
export function formatUsd(decimal: string): string {
  const m = /^(-?)(\d+)(?:\.(\d+))?$/.exec(decimal.trim());
  if (!m) throw new RangeError(`not a decimal string: ${decimal}`);
  const [, sign, whole, frac = ""] = m;
  const shown = frac.replace(/0+$/, "").padEnd(2, "0");
  return `${sign}$${group(whole.replace(/^0+(?=\d)/, ""))}.${shown}`;
}
