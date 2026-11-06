import { describe, expect, it } from "vitest";
import { formatCents, formatInt, formatUsd, normalizeDecimal } from "./format";

describe("formatCents", () => {
  it.each([
    [0, "$0"],
    [5000, "$50"],
    [45000, "$450"],
    [8516500, "$85,165"],
    [50000000, "$500,000"],
    [195767500, "$1,957,675"],
    [8516550, "$85,165.50"],
    [7, "$0.07"],
    [-1234, "-$12.34"],
  ])("%i → %s", (cents, shown) => {
    expect(formatCents(cents)).toBe(shown);
  });

  it("refuses non-integer cents", () => {
    expect(() => formatCents(12.5)).toThrow(RangeError);
  });
});

describe("formatInt", () => {
  it.each([
    [0, "0"],
    [999, "999"],
    [1000, "1,000"],
    [10000000, "10,000,000"],
    [-8000000, "-8,000,000"],
  ])("%i → %s", (n, shown) => {
    expect(formatInt(n)).toBe(shown);
  });
});

describe("formatUsd", () => {
  it.each([
    ["0.0034", "$0.0034"],
    ["11.38", "$11.38"],
    ["9.1", "$9.10"],
    ["9.10", "$9.10"],
    ["1.500", "$1.50"],
    ["0.4100", "$0.41"],
    ["1234", "$1,234.00"],
    ["0", "$0.00"],
    ["-0.00", "$0.00"],
    ["1E-7", "$0.0000001"],
    ["3e-05", "$0.00003"],
    ["1.5E3", "$1,500.00"],
    ["2.50E+1", "$25.00"],
  ])("%s → %s", (decimal, shown) => {
    expect(formatUsd(decimal)).toBe(shown);
  });

  it("refuses non-decimal strings", () => {
    expect(() => formatUsd("1e")).toThrow(RangeError);
    expect(() => formatUsd("abc")).toThrow(RangeError);
    expect(() => formatUsd("$5")).toThrow(RangeError);
  });
});

describe("normalizeDecimal", () => {
  it.each([
    [" 1E-7 ", "0.0000001"],
    ["2.50", "2.5"],
    ["-0.00", "0"],
    ["0011.380", "11.38"],
    ["-3", "-3"],
  ])("%s → %s", (decimal, canonical) => {
    expect(normalizeDecimal(decimal)).toBe(canonical);
  });
});
