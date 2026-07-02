"""Read pre-staged OHLCV CSVs (cloud-routine egress workaround).

The scheduled cloud routines can't reach Yahoo Finance (the sandbox egress
allowlist blocks query1/query2.finance.yahoo.com, and the claude.ai
Capabilities egress setting does not propagate to cloud containers). yfinance
DOES work from GitHub Actions, so a GitHub Action stages the bars both routines
need and force-pushes them to the ``cloud-data`` branch; the routine clones
that branch (GitHub is on the allowlist) and points ``STAGED_DATA_DIR`` here.

CSVs are produced by ``scripts/stage_market_data.py`` using the same
``data.yfinance_loader`` the live path uses, so the on-disk shape matches
exactly: lowercase open/high/low/close/volume columns, naive datetime index.
"""
from __future__ import annotations

from pathlib import Path

import pandas as pd


def load_bars(
    ticker: str,
    period: str | None = None,  # accepted for dispatcher parity; intentionally unused
    interval: str = "1d",
    *,
    staged_dir: str,
) -> pd.DataFrame:
    """Load staged bars for ``(ticker, interval)`` from ``staged_dir``.

    ``period`` is ignored: the staged CSV holds the full fetched window and the
    indicators use trailing lookbacks, so extra leading history is harmless.

    Raises ``FileNotFoundError`` when the ticker/interval wasn't staged and
    ``ValueError`` on an empty file — both surface upstream as a data failure
    (loud) rather than a silent empty result, matching the live loader's
    contract.
    """
    path = Path(staged_dir) / f"{ticker.upper()}__{interval}.csv"
    if not path.exists():
        raise FileNotFoundError(f"no staged bars: {path}")
    df = pd.read_csv(path, index_col=0, parse_dates=True)
    if df.empty:
        raise ValueError(f"empty staged bars: {path}")
    return df
