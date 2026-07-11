"""Lotto cohort spread-haircut analysis.

Question: How much of the lotto edge in each cohort gets given back to
bid-ask spread, broken out by liquidity tier?

Method:
  1. Load existing backtest CSVs (which use B-S mid pricing, NO spread).
  2. Assign each ticker a spread% of premium from EMPIRICAL kill-sheet data
     where available, otherwise a tier-proxy assumption (clearly flagged).
  3. Compute haircut in R-units:
       slippage_R = spread_$ / R = spread_$ / (0.5 * P_entry)
                  = 2 * (spread_$ / P_entry) = 2 * spread_pct
     (crossing the full bid-ask round-trip vs. theoretical mid)
  4. net_R = R_multiple - slippage_R per trade.
  5. Aggregate per ticker, per tier, per cohort.
  6. Sensitivity: scale spread% by 0.5x, 1.0x, 1.5x, 2.0x — find breakeven.

Empirical spread anchors (from ~/.trading-dashboard/kill_sheets, May 2026):
  AAPL  3.4% (n=1, OI 8307)
  NVDA  2.8% (n=1, OI 30701)
  PYPL 10.5% (n=1, OI 329)
  CPRT 12.2% (n=1, OI 891)
  WULF 16.7% (n=7, OI 216)   ← strongest journal anchor
  CTRE 45.5% (n=2, OI 333)
  HBM   5.3% (n=1, OI 2875)
  TQQQ excluded (n=1 record at 95.9% — likely stale/extended-hours snapshot)

Tier-proxy assumptions for tickers WITHOUT journal data are based on
typical option-OI bands at the lotto-relevant 0.20-delta / 10-DTE strike.
These are estimates — sweep them with --multiplier to see sensitivity.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import pandas as pd

SCRIPTS = Path(__file__).resolve().parent

# ─── Empirical spread % anchors (from kill-sheet journal) ──────────────────
EMPIRICAL_SPREAD_PCT: dict[str, float] = {
    "AAPL": 3.4,
    "NVDA": 2.8,
    "HBM": 5.3,
    "PYPL": 10.5,
    "CPRT": 12.2,
    "WULF": 16.7,
    "CTRE": 45.5,
    # TQQQ excluded — single record at 95.9% is implausible (stale snapshot)
}

# ─── Tier-proxy spread % for tickers WITHOUT journal data ──────────────────
# Centered on the empirical clusters: tight (~3%), medium (~8%), wide (~15%),
# very wide (~22%). Each tier is an OI-driven SWAG, not a measurement.
TIER_TIGHT = 3.0    # Mag 7 + major ETFs (1¢ options on $200+ premiums)
TIER_LIQUID = 5.0   # Liquid single stocks + leveraged ETFs (small abs spread)
TIER_MEDIUM = 8.0   # Active single stocks, mid OI
TIER_WIDE = 15.0    # Mid-cap volatile, OI in the low hundreds
TIER_VWIDE = 22.0   # Small-cap thin OI

TIER_LABELS = {
    TIER_TIGHT: "TIGHT (~3%)",
    TIER_LIQUID: "LIQUID (~5%)",
    TIER_MEDIUM: "MEDIUM (~8%)",
    TIER_WIDE: "WIDE (~15%)",
    TIER_VWIDE: "V.WIDE (~22%)",
}

PROXY_SPREAD_PCT: dict[str, float] = {
    # ── Mag 7 (tightest, even non-AAPL/NVDA defaults to tight cluster) ──
    "MSFT": TIER_TIGHT, "GOOGL": TIER_TIGHT, "GOOG": TIER_TIGHT,
    "AMZN": TIER_TIGHT, "META": TIER_TIGHT, "TSLA": TIER_TIGHT,
    "AMD": TIER_TIGHT,
    # ── Major ETFs ──
    "QQQ": TIER_TIGHT, "SPY": TIER_TIGHT, "IWM": TIER_TIGHT,
    "GLD": TIER_TIGHT, "SLV": TIER_TIGHT, "USO": TIER_LIQUID,
    "GDX": TIER_LIQUID, "GDXJ": TIER_LIQUID, "XLE": TIER_LIQUID,
    "XLF": TIER_LIQUID, "DIA": TIER_TIGHT, "SMH": TIER_LIQUID,
    "EEM": TIER_LIQUID, "EFA": TIER_LIQUID, "EWZ": TIER_LIQUID,
    "FXI": TIER_LIQUID, "HYG": TIER_LIQUID, "IBB": TIER_LIQUID,
    "IGV": TIER_MEDIUM, "IYR": TIER_LIQUID, "JETS": TIER_MEDIUM,
    "KRE": TIER_LIQUID, "LQD": TIER_LIQUID, "TLT": TIER_LIQUID,
    "UNG": TIER_MEDIUM, "SOXX": TIER_LIQUID, "ARKK": TIER_LIQUID,
    "BITO": TIER_MEDIUM,
    # ── Leveraged ETFs ──
    "TQQQ": TIER_LIQUID, "SQQQ": TIER_LIQUID, "SOXL": TIER_LIQUID,
    "SOXS": TIER_LIQUID, "TNA": TIER_LIQUID, "UPRO": TIER_LIQUID,
    # ── Liquid single stocks (post-Mag 7, big options activity) ──
    "AVGO": TIER_LIQUID, "COIN": TIER_LIQUID, "PLTR": TIER_LIQUID,
    "MU": TIER_MEDIUM, "ARM": TIER_MEDIUM, "PANW": TIER_MEDIUM,
    "CRWD": TIER_MEDIUM, "ASML": TIER_MEDIUM, "MELI": TIER_MEDIUM,
    "PDD": TIER_MEDIUM, "ABNB": TIER_MEDIUM, "ADBE": TIER_MEDIUM,
    "ADI": TIER_MEDIUM, "AMAT": TIER_MEDIUM, "BKNG": TIER_MEDIUM,
    "CDNS": TIER_MEDIUM, "CMCSA": TIER_MEDIUM, "COST": TIER_LIQUID,
    "CSCO": TIER_LIQUID, "ADP": TIER_MEDIUM, "AEP": TIER_MEDIUM,
    "AZN": TIER_MEDIUM, "BIIB": TIER_MEDIUM, "BKR": TIER_MEDIUM,
    "CCEP": TIER_WIDE, "CDW": TIER_MEDIUM, "CEG": TIER_MEDIUM,
    "CHTR": TIER_MEDIUM, "CSGP": TIER_WIDE, "CSX": TIER_MEDIUM,
    "AMGN": TIER_MEDIUM, "ADSK": TIER_MEDIUM,
    # ── Mid-cap volatile single stocks ──
    "MSTR": TIER_WIDE, "IONQ": TIER_WIDE, "DDOG": TIER_WIDE,
    "MDB": TIER_WIDE, "MRVL": TIER_MEDIUM, "SMCI": TIER_WIDE,
    "TTD": TIER_WIDE, "ZS": TIER_WIDE, "LULU": TIER_WIDE,
    "F": TIER_MEDIUM, "SOFI": TIER_MEDIUM, "HOOD": TIER_MEDIUM,
    "DKNG": TIER_MEDIUM, "MARA": TIER_WIDE, "RIOT": TIER_WIDE,
    "AFRM": TIER_WIDE, "AMC": TIER_WIDE, "LCID": TIER_WIDE,
    "NIO": TIER_WIDE, "GME": TIER_WIDE,
    # ── Small-cap thin OI ──
    "ACHR": TIER_VWIDE, "BBAI": TIER_VWIDE, "CIFR": TIER_VWIDE,
    "CLSK": TIER_VWIDE, "JOBY": TIER_VWIDE, "RDW": TIER_VWIDE,
    "RGTI": TIER_VWIDE, "SOUN": TIER_VWIDE,
}


def spread_pct_for(ticker: str) -> tuple[float, str]:
    """Return (spread%, source) for a ticker. Source ∈ {'empirical','proxy','default'}."""
    t = ticker.upper()
    if t in EMPIRICAL_SPREAD_PCT:
        return EMPIRICAL_SPREAD_PCT[t], "empirical"
    if t in PROXY_SPREAD_PCT:
        return PROXY_SPREAD_PCT[t], "proxy"
    return TIER_MEDIUM, "default"


def tier_for_pct(pct: float) -> str:
    if pct <= 4: return "TIGHT (≤4%)"
    if pct <= 7: return "LIQUID (4-7%)"
    if pct <= 11: return "MEDIUM (7-11%)"
    if pct <= 18: return "WIDE (11-18%)"
    return "V.WIDE (>18%)"


def apply_haircut(df: pd.DataFrame, multiplier: float = 1.0) -> pd.DataFrame:
    df = df.copy()
    pct_map = {t: spread_pct_for(t)[0] * multiplier for t in df["ticker"].unique()}
    src_map = {t: spread_pct_for(t)[1] for t in df["ticker"].unique()}
    df["spread_pct"] = df["ticker"].map(pct_map)
    df["spread_src"] = df["ticker"].map(src_map)
    df["tier"] = df["spread_pct"].apply(tier_for_pct)
    # slippage_R = 2 * (spread_pct / 100). Round-trip cost = full bid-ask
    # when paying ask on entry and selling bid on exit vs B-S mid.
    df["slippage_R"] = 2.0 * df["spread_pct"] / 100.0
    df["net_R"] = df["R_multiple"] - df["slippage_R"]
    return df


def cohort_stats(R: pd.Series) -> dict:
    R = R.dropna()
    if R.empty:
        return {"n": 0}
    wins = R[R > 0]
    losses = R[R < 0]
    pf = (wins.sum() / abs(losses.sum())) if losses.sum() < 0 else float("inf")
    return {
        "n": int(R.count()),
        "wr": float((R > 0).mean()) * 100,
        "avgR": float(R.mean()),
        "medR": float(R.median()),
        "pf": float(pf),
    }


def print_cohort(label: str, df: pd.DataFrame, *, multiplier: float = 1.0) -> None:
    print(f"\n══ {label} — spread haircut at {multiplier:.2f}x ══")
    haircut = apply_haircut(df, multiplier=multiplier)
    raw = cohort_stats(haircut["R_multiple"])
    net = cohort_stats(haircut["net_R"])
    print(f"  GROSS (no spread):  n={raw['n']:<4} WR={raw['wr']:5.1f}%  "
          f"avgR={raw['avgR']:+.3f}  PF={raw['pf']:.2f}")
    print(f"  NET   (post-spread): n={net['n']:<4} WR={net['wr']:5.1f}%  "
          f"avgR={net['avgR']:+.3f}  PF={net['pf']:.2f}")
    print(f"  Edge given back to spread: {raw['avgR'] - net['avgR']:+.3f} R/trade")

    print("\n  ── Per liquidity tier ──")
    print(f"  {'Tier':<18}{'N':>5}{'WR%':>7}{'gross R':>10}{'slip R':>9}{'net R':>9}{'gross PF':>10}{'net PF':>9}")
    for tier in ["TIGHT (≤4%)", "LIQUID (4-7%)", "MEDIUM (7-11%)", "WIDE (11-18%)", "V.WIDE (>18%)"]:
        sub = haircut[haircut["tier"] == tier]
        if sub.empty: continue
        s_raw = cohort_stats(sub["R_multiple"])
        s_net = cohort_stats(sub["net_R"])
        slip = sub["slippage_R"].mean()
        print(f"  {tier:<18}{s_raw['n']:>5}{s_raw['wr']:>7.1f}{s_raw['avgR']:>10.3f}"
              f"{slip:>9.3f}{s_net['avgR']:>9.3f}{s_raw['pf']:>10.2f}{s_net['pf']:>9.2f}")

    print("\n  ── Per ticker (sorted by NET edge) ──")
    by_t = (haircut.groupby("ticker")
            .agg(n=("R_multiple", "count"),
                 wr=("R_multiple", lambda s: (s > 0).mean() * 100),
                 gross_R=("R_multiple", "mean"),
                 slip_R=("slippage_R", "mean"),
                 net_R=("net_R", "mean"),
                 spread_pct=("spread_pct", "first"),
                 spread_src=("spread_src", "first"),
                 tier=("tier", "first"))
            .reset_index()
            .sort_values("net_R", ascending=False))
    print(f"  {'Ticker':<8}{'src':<10}{'tier':<18}{'N':>5}{'WR%':>7}"
          f"{'spr%':>7}{'gross R':>10}{'slip R':>9}{'net R':>9}")
    for _, r in by_t.iterrows():
        print(f"  {r['ticker']:<8}{r['spread_src']:<10}{r['tier']:<18}"
              f"{int(r['n']):>5}{r['wr']:>7.1f}{r['spread_pct']:>7.1f}"
              f"{r['gross_R']:>10.3f}{r['slip_R']:>9.3f}{r['net_R']:>9.3f}")


def sensitivity_sweep(label: str, df: pd.DataFrame) -> None:
    print(f"\n══ {label} — sensitivity sweep (per-tier breakeven spread multiplier) ══")
    print(f"  {'Multiplier':<12}{'Aggregate PF':>14}{'Aggregate avgR':>16}")
    for mult in [0.0, 0.5, 0.75, 1.0, 1.25, 1.5, 2.0, 2.5, 3.0]:
        haircut = apply_haircut(df, multiplier=mult)
        net = cohort_stats(haircut["net_R"])
        print(f"  {mult:<12.2f}{net['pf']:>14.2f}{net['avgR']:>16.3f}")


def exit_reason_breakdown(label: str, df: pd.DataFrame, multiplier: float = 1.0) -> None:
    print(f"\n══ {label} — slippage by exit reason ══")
    haircut = apply_haircut(df, multiplier=multiplier)
    grp = (haircut.groupby("exit_reason")
           .agg(n=("R_multiple", "count"),
                gross=("R_multiple", "mean"),
                slip=("slippage_R", "mean"),
                net=("net_R", "mean"))
           .reset_index().sort_values("n", ascending=False))
    print(f"  {'Reason':<22}{'N':>5}{'gross R':>10}{'slip R':>9}{'net R':>9}")
    for _, r in grp.iterrows():
        print(f"  {r['exit_reason']:<22}{int(r['n']):>5}"
              f"{r['gross']:>10.3f}{r['slip']:>9.3f}{r['net']:>9.3f}")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--cohorts", nargs="+", default=[
        "lotto_options_backtest_2y_v2.csv",
        "lotto_focused_10_30_universe_2y.csv",
        "lotto_etf_universe_2y.csv",
    ], help="CSVs in scripts/ to analyze")
    ap.add_argument("--multiplier", type=float, default=1.0,
                    help="Scale empirical/proxy spreads by this factor")
    args = ap.parse_args()

    for csv_name in args.cohorts:
        path = SCRIPTS / csv_name
        if not path.exists():
            print(f"Skipping {csv_name}: not found")
            continue
        df = pd.read_csv(path)
        label = csv_name.replace(".csv", "")
        print_cohort(label, df, multiplier=args.multiplier)
        exit_reason_breakdown(label, df, multiplier=args.multiplier)
        sensitivity_sweep(label, df)
        print("\n" + "=" * 80)
    return 0


if __name__ == "__main__":
    sys.exit(main())
