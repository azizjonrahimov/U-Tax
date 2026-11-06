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

/**
 * Normalize a decimal string, including exponent form ("1E-7", which Python's
 * str(Decimal) produces for tiny values), to plain digits: [sign, whole, frac].
 * String arithmetic only, so no float rounding.
 */
export function splitDecimal(decimal: string): [sign: "" | "-", whole: string, frac: string] {
  const m = /^([+-]?)(\d+)(?:\.(\d*))?(?:[eE]([+-]?\d+))?$/.exec(decimal.trim());
  if (!m) throw new RangeError(`not a decimal string: ${decimal}`);
  const [, sign, intPart, fracPart = "", expPart = "0"] = m;
  const exp = Number(expPart);
  if (!Number.isSafeInteger(exp) || Math.abs(exp) > 30) throw new RangeError(`exponent out of range: ${decimal}`);
  let digits = intPart + fracPart;
  let point = intPart.length + exp; // position of the decimal point in `digits`
  if (point < 0) {
    digits = "0".repeat(-point) + digits;
    point = 0;
  } else if (point > digits.length) {
    digits = digits + "0".repeat(point - digits.length);
  }
  const whole = digits.slice(0, point).replace(/^0+(?=\d)/, "") || "0";
  const frac = digits.slice(point).replace(/0+$/, "");
  const isZero = /^0*$/.test(whole + frac);
  return [sign === "-" && !isZero ? "-" : "", whole, frac];
}

/** Canonical fixed-point form: " 1E-7 " → "0.0000001", "2.50" → "2.5", "-0.00" → "0". */
export function normalizeDecimal(decimal: string): string {
  const [sign, whole, frac] = splitDecimal(decimal);
  return `${sign}${whole}${frac ? `.${frac}` : ""}`;
}

/** Decimal USD string → display: "0.0034" → "$0.0034", "11.38" → "$11.38", "1E-7" → "$0.0000001". */
export function formatUsd(decimal: string): string {
  const [sign, whole, frac] = splitDecimal(decimal);
  return `${sign}$${group(whole)}.${frac.padEnd(2, "0")}`;
}
