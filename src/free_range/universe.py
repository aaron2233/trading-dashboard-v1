"""Free-range scan universe — Nasdaq 100 constituents + known ETF set.

The Nasdaq 100 list is a frozen snapshot, not a live API pull. Reasons:
1. Composition only changes a few times per year — staleness is bounded.
2. yfinance has no constituent-list endpoint; live pulls would mean scraping.
3. A reviewable in-repo list keeps the scan deterministic across sessions.

When the index rebalances, update SNAPSHOT_DATE and the list. To regenerate
manually, see https://www.nasdaq.com/market-activity/quotes/nasdaq-ndx-index
or the iShares QQQ holdings page.

ETF identification matters because the price-band filter ($15-50 single stocks)
explicitly exempts ETFs per orchestrator rule (any price acceptable for ETFs).
KNOWN_ETFS is conservative — only adds tickers we'd actively consider trading.
"""
from __future__ import annotations


# Snapshot date — refresh quarterly or on known rebalance.
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
})


def is_etf(ticker: str) -> bool:
    """True if `ticker` is in the dashboard's known-ETF set.

    Used by the price-band filter to exempt ETFs from the $15-50 cap.
    Ticker comparison is case-insensitive.
    """
    return ticker.upper() in KNOWN_ETFS


def free_range_universe(exclude: frozenset[str] | None = None) -> tuple[str, ...]:
    """Return the candidate universe for free-range scan.

    Defaults to NASDAQ_100. `exclude` removes tickers already covered upstream
    (typically QQQ + GLD baseline + user-submitted) so they're not re-evaluated.
    Comparison is case-insensitive; result preserves NASDAQ_100 ordering.
    """
    if not exclude:
        return NASDAQ_100
    excl_u = frozenset(t.upper() for t in exclude)
    return tuple(t for t in NASDAQ_100 if t.upper() not in excl_u)
