"""Free-range scan universes + known ETF set.

Universe lists are frozen snapshots, not live API pulls. Reasons:
1. Index composition changes infrequently — staleness is bounded.
2. yfinance has no constituent-list endpoint; live pulls would mean scraping.
3. A reviewable in-repo list keeps the scan deterministic across sessions.

When an index rebalances, update its SNAPSHOT_DATE and the list. Source URLs
per constant below.

ETF identification matters because the price-band filter ($15-50 single stocks)
explicitly exempts ETFs per orchestrator rule (any price acceptable for ETFs).
KNOWN_ETFS is conservative — only adds tickers we'd actively consider trading.
"""
from __future__ import annotations

from typing import Literal


# ─── NASDAQ 100 ──────────────────────────────────────────────────────────────
# Refresh source: https://www.nasdaq.com/market-activity/quotes/nasdaq-ndx-index
# or the iShares QQQ holdings page.
NASDAQ_100_SNAPSHOT_DATE = "2026-04-01"

# Nasdaq 100 constituents, snapshot-dated above. Sourced from the index
# composition list as of that date. Symbols match yfinance conventions
# (uppercase, no exchange suffix).
NASDAQ_100: tuple[str, ...] = (
    "AAPL", "ABNB", "ADBE", "ADI", "ADP", "ADSK", "AEP", "AMAT", "AMD", "AMGN",
    "AMZN", "ANSS", "ARM", "ASML", "AVGO", "AZN", "BIIB", "BKNG", "BKR", "CCEP",
    "CDNS", "CDW", "CEG", "CHTR", "CMCSA", "COST", "CPRT", "CRWD", "CSCO", "CSGP",
    "CSX", "CTAS", "CTSH", "DASH", "DDOG", "DLTR", "DXCM", "EA", "EXC", "FANG",
    "FAST", "FTNT", "GEHC", "GFS", "GILD", "GOOG", "GOOGL", "HON", "IDXX", "ILMN",
    "INTC", "INTU", "ISRG", "KDP", "KHC", "KLAC", "LIN", "LRCX", "LULU", "MAR",
    "MCHP", "MDB", "MDLZ", "MELI", "META", "MNST", "MRNA", "MRVL", "MSFT", "MU",
    "NFLX", "NVDA", "NXPI", "ODFL", "ON", "ORLY", "PANW", "PAYX", "PCAR", "PDD",
    "PEP", "PYPL", "QCOM", "REGN", "ROP", "ROST", "SBUX", "SMCI", "SNPS", "TEAM",
    "TMUS", "TSLA", "TTD", "TTWO", "TXN", "VRSK", "VRTX", "WBD", "WDAY", "XEL", "ZS",
)


# ─── S&P 500 Top 50 by market cap ────────────────────────────────────────────
# Refresh source: https://disfold.com/stock-index/sp-500/companies/
# (cross-check against slickcharts.com/sp500 — page is 403 for WebFetch but
# accessible in a browser).
SP500_TOP_50_SNAPSHOT_DATE = "2026-01-01"

# Top 50 S&P 500 by market cap as of the snapshot date. yfinance convention:
# Berkshire Hathaway Class B is "BRK-B" (dash, not dot). Heavy overlap with
# NASDAQ_100 is expected — the lotto scanner already de-duplicates against the
# baseline (QQQ + GLD) and user-submitted tickers, not across universes; the
# user picks one universe per scan via the dropdown.
SP500_TOP_50: tuple[str, ...] = (
    "NVDA", "AAPL", "GOOG", "GOOGL", "MSFT", "AMZN", "AVGO", "META", "TSLA", "BRK-B",
    "LLY", "WMT", "JPM", "V", "ORCL", "XOM", "MA", "JNJ", "BAC", "ABBV",
    "NFLX", "COST", "AMD", "MU", "HD", "GE", "PG", "CVX", "WFC", "UNH",
    "CSCO", "KO", "MS", "CAT", "GS", "IBM", "MRK", "AXP", "RTX", "PM",
    "CRM", "LRCX", "TMUS", "TMO", "C", "MCD", "ABT", "AMAT", "ISRG", "LIN",
)


# ─── Russell 2000 Top 50 by IWM weight ───────────────────────────────────────
# Refresh source: https://www.bestetf.net/etf/IWM/holdings/
# (iShares IWM holdings page is 403 for WebFetch; bestetf.net mirrors it.)
RUSSELL_2000_TOP_50_SNAPSHOT_DATE = "2026-05-09"

# Top 50 IWM holdings by weight, filtered to real equity tickers. yfinance
# convention applied:
#   - CDE.NE → CDE (Coeur Mining, primary US listing)
#   - MOG.A → MOG-A (Moog Class A, yfinance dash convention)
#   - XTSLA (BlackRock cash sweep position, not a stock) is excluded —
#     list contains 49 actual equities. Constant name keeps "_TOP_50"
#     because it tracks the top-50-by-weight slice; the cash filter is
#     incidental.
RUSSELL_2000_TOP_50: tuple[str, ...] = (
    "BE", "CRDO", "STRL", "FN", "CDE", "SITM", "NXT", "SATS", "IONQ", "TTMI",
    "MOD", "RMBS", "AEIS", "SANM", "DY", "VIAV", "HL", "DOCN", "GH", "SMTC",
    "FORM", "BBIO", "ARWR", "AAOI", "KTOS", "HUT", "SPXC", "APLD", "ENSG", "PL",
    "UMBF", "GTLS", "AGX", "MDGL", "AXSM", "FCFS", "SNEX", "MOG-A", "AHR", "CTRE",
    "CYTK", "OKLO", "POWL", "ESE", "PRAX", "ONB", "RIOT", "WULF", "FLR",
)


# Curated high-vol single-stock + leveraged-ETF watchlist for lotto.
# Lotto edge is a high-vol-single-stock effect (2026-05-16 backtests: 2,803
# trades, 168 tickers — broad/diversified universes PF 0.75-0.89, skip).
# Keep this list tight and refresh from forward results, not "more tickers".
#
# 2026-07-17 rotation (scripts/lotto_universe_review.py, quarterly process):
# singles are IN-BAND ONLY ($10-50) — names that drift above the band are
# unbuyable under the R1 $150 cap and rotate out on price alone (the prior
# list had 18 of 21 singles above the $50 cap, so the live scan could act on
# almost none of its backtested edge). Kept incumbents IONQ/TTD + promoted
# PL/RGTI/APLD/CDE/CIFR/OKLO/WULF/VIAV (trailing-2y harness: PF >= 1.3,
# n >= 8, ~100% of simulated contracts within the R1 cap). The 19 rotated-out
# names (incl. SMCI, dropped on PF 0.29/n=11) are SHADOW_ROTATED in the
# review script and stay in every quarterly backtest as the control cohort.
# Next review: 2026-10-15.
# See [[project-lotto-g4-trigger-bar]] memory for the underlying analysis.
# Lives here (not lotto/scanner.py) so it can be a named universe without
# an import cycle (lotto.scanner -> free_range.filters -> this module);
# lotto re-exports it, so `from lotto import LOTTO_HIGH_VOL_WATCHLIST`
# still works for the cloud scripts.
LOTTO_HIGH_VOL_WATCHLIST: tuple[str, ...] = (
    # Mag 7 (LOTTO_MAG7_PRICE_EXEMPT — band-exempt for lotto)
    "AAPL", "AMZN", "GOOGL", "META", "MSFT", "NVDA", "TSLA",
    # In-band singles (2026-07-17 rotation; see block comment above)
    "APLD", "CDE", "CIFR", "IONQ", "OKLO", "PL", "RGTI", "TTD", "VIAV", "WULF",
    # Leveraged / high-vol ETFs (preserve lotto edge, G4 no-op)
    "TQQQ", "SQQQ", "SOXL", "SOXS", "TNA", "UPRO", "ARKK", "BITO",
)


UniverseName = Literal[
    "nasdaq_100", "sp500_top_50", "russell_2000_top_50", "lotto_high_vol",
]

UNIVERSES: dict[str, tuple[str, ...]] = {
    "nasdaq_100": NASDAQ_100,
    "sp500_top_50": SP500_TOP_50,
    "russell_2000_top_50": RUSSELL_2000_TOP_50,
    "lotto_high_vol": LOTTO_HIGH_VOL_WATCHLIST,
}


# Tickers we'd consider trading that bypass the $15-50 single-stock price band.
# Conservative list — large-cap, liquid index/sector ETFs only. Single-stock
# tickers above $50 (e.g. NVDA, META, GOOG) are NOT here — they get filtered
# out of free-range scan by price band, which is the intended behavior per
# orchestrator rules ($15-50 keeps premium reasonable for cash-account longs).
KNOWN_ETFS: frozenset[str] = frozenset({
    # Broad market
    "SPY", "QQQ", "IWM", "DIA", "VTI", "VOO", "MDY",
    # Sector SPDRs
    "XLK", "XLE", "XLF", "XLV", "XLY", "XLP", "XLI", "XLB", "XLU", "XLRE", "XLC",
    # Commodity / safe-haven
    "GLD", "SLV", "GDX", "GDXJ", "USO", "UNG",
    # Bonds / treasuries
    "TLT", "IEF", "SHY", "LQD", "HYG",
    # Volatility / crypto-adjacent
    "VXX", "UVXY", "BITO", "IBIT",
    # International / region
    "EEM", "EFA", "FXI", "EWZ", "INDA",
    # Tech subsector (often used Tier 4 satellite)
    "SMH", "SOXX", "ARKK", "TQQQ", "SQQQ",
    # Leveraged index (lotto high-vol watchlist; band-exempt like all ETFs)
    "SOXL", "SOXS", "TNA", "UPRO",
})


def is_etf(ticker: str) -> bool:
    """True if `ticker` is in the dashboard's known-ETF set.

    Used by the price-band filter to exempt ETFs from the $15-50 cap.
    Ticker comparison is case-insensitive.
    """
    return ticker.upper() in KNOWN_ETFS


def free_range_universe(
    exclude: frozenset[str] | None = None,
    *,
    universe: str = "nasdaq_100",
) -> tuple[str, ...]:
    """Return the candidate universe for free-range scan.

    `universe` selects which constituent list to draw from. Defaults to
    "nasdaq_100" — the original behavior. Other valid values: "sp500_top_50",
    "russell_2000_top_50". Unknown names raise ValueError so callers fail
    loud rather than silently fall back.

    `exclude` removes tickers already covered upstream (typically QQQ + GLD
    baseline + user-submitted) so they're not re-evaluated. Comparison is
    case-insensitive; result preserves the source-list ordering.
    """
    try:
        source = UNIVERSES[universe]
    except KeyError as exc:
        raise ValueError(
            f"Unknown universe '{universe}'. Valid: {sorted(UNIVERSES)}"
        ) from exc
    if not exclude:
        return source
    excl_u = frozenset(t.upper() for t in exclude)
    return tuple(t for t in source if t.upper() not in excl_u)
