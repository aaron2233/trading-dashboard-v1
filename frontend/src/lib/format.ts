/** Currency formatting — the ONE implementation behind every dollar display.
 * Views must not re-implement this: five near-identical copies drifted apart
 * before consolidation (locale, sign handling, precision). */

/** Cents precision; `sign` renders an explicit "+" on positive values (P&L). */
export function fmtUsd(n: number | null | undefined, sign = false): string {
  if (n === null || n === undefined) return "—";
  return n.toLocaleString("en-US", {
    style: "currency",
    currency: "USD",
    minimumFractionDigits: 2,
    maximumFractionDigits: 2,
    signDisplay: sign ? "exceptZero" : "auto",
  });
}

/** Whole dollars — compact banner/summary rows where cents are noise. */
export function fmtUsdWhole(n: number | null | undefined): string {
  if (n === null || n === undefined) return "—";
  return n.toLocaleString("en-US", {
    style: "currency",
    currency: "USD",
    maximumFractionDigits: 0,
  });
}
