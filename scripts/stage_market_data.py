"""Stage OHLCV bars to CSV for the cloud routines (egress workaround).

The scheduled cloud routines can't reach Yahoo Finance, but yfinance works
fine from GitHub Actions runners. So this script — run in CI — fetches the
bars both routines need, and the workflow publishes them to the ``cloud-data``
branch. The routines clone that branch and read the CSVs via the
``STAGED_DATA_DIR`` loader path instead of hitting Yahoo directly. See
``src/data/staged_loader.py`` and the project-cloud-routine-egress-allowlist
note.

Uses the SAME loader the routines use (``data.yfinance_loader.load_bars``) so
each CSV is byte-identical to a live fetch (lowercase ohlcv columns, naive ET
datetime index). One file per (ticker, interval): ``<TICKER>__<interval>.csv``.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

# Make src/ importable when run from the repo root.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from data.yfinance_loader import load_bars  # noqa: E402
from lotto import LOTTO_HIGH_VOL_WATCHLIST  # noqa: E402

# Daily bars: lotto universe (curated high-vol watchlist — same source as
# scripts/lotto_cloud_scan.py UNIVERSE) + guard + beat-market tickers.
LOTTO_UNIVERSE = list(LOTTO_HIGH_VOL_WATCHLIST)
GUARD = ["QQQ"]  # lotto fresh-bar guard ticker
BEAT_MARKET = ["QQQ", "SPY", "QQQM", "NVDA", "QLD", "MU", "META", "ETH-USD", "BTC-USD"]

DAILY_TICKERS = sorted(set(LOTTO_UNIVERSE) | set(GUARD) | set(BEAT_MARKET))
H2_TICKERS = sorted(set(LOTTO_UNIVERSE) | set(GUARD))


def stage(out_dir: Path) -> int:
    out_dir.mkdir(parents=True, exist_ok=True)
    jobs = [(t, "1d") for t in DAILY_TICKERS] + [(t, "2h") for t in H2_TICKERS]
    ok = failed = 0
    for ticker, interval in jobs:
        try:
            bars = load_bars(ticker, interval=interval)
        except Exception as exc:  # one illiquid name shouldn't fail the batch
            failed += 1
            print(f"FAIL {ticker} {interval}: {exc}", file=sys.stderr)
            continue
        bars.to_csv(out_dir / f"{ticker}__{interval}.csv")
        ok += 1
        print(f"ok   {ticker} {interval} ({len(bars)} bars)")
    print(
        f"\nstaged {ok} files, {failed} failures "
        f"({len(DAILY_TICKERS)} daily + {len(H2_TICKERS)} 2h requested)"
    )
    # Fail the CI job only if NOTHING staged (total outage); tolerate the odd
    # per-ticker miss so a single delisting doesn't block the whole run.
    return 1 if ok == 0 else 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--out", required=True, help="output directory for CSVs")
    return stage(Path(ap.parse_args().out))


if __name__ == "__main__":
    raise SystemExit(main())
