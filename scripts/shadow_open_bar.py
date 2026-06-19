"""Shadow log for the 09:30 ET open 2h-bar lotto signals (tracked, NOT drafted).

Modeled 2026-06-19: adding an open-bar window to the live routine is marginal on
a tiny sample (~1 unique signal/month, edge unproven). Rather than pay +33%
scans/drafts, this CI-only job accumulates the open-bar signals so the call can
be revisited with real n. Runs post-close in GitHub Actions (Yahoo reachable
there) and appends to the shadow-log branch. NOT wired into the claude routine.

Each run replays the REAL lotto scanner truncated to each of today's four 2h
bars (09:30 / 11:30 / 13:30 / 15:30 ET) and records the 09:30 actionable setups,
flagging which are UNIQUE to the open bar (gone by the 11:30 live window).
Forward returns are NOT stored here — they're derivable from price history at
review time (see openbar_model.py).
"""
import argparse
import csv
import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, "src")
sys.path.insert(0, "scripts")

OPEN_TIME = "09:30"
LATER_TIMES = {"11:30", "13:30", "15:30"}


def build_cache(datadir: str | None):
    """Fetch 1d+2h bars for the lotto universe. From CSVs if --datadir given
    (test/offline), else live via the repo's yfinance loader (CI default)."""
    from lotto_cloud_scan import NASDAQ_50, GUARD_TICKER
    tickers = sorted(set(NASDAQ_50) | {GUARD_TICKER})
    cache, errors = {}, []
    if datadir:
        d = Path(datadir)
        for t in tickers:
            for iv in ("1d", "2h"):
                f = d / f"{t}__{iv}.csv"
                if f.exists():
                    cache[(t, iv)] = pd.read_csv(f, index_col=0, parse_dates=True)
                else:
                    errors.append(f"{t} {iv}: missing")
    else:
        from data.yfinance_loader import load_bars as yf
        for t in tickers:
            for iv in ("1d", "2h"):
                try:
                    cache[(t, iv)] = yf(t, interval=iv)
                except Exception as e:
                    errors.append(f"{t} {iv}: {e}")
    return cache, errors, NASDAQ_50, GUARD_TICKER


def append_rows(path: Path, header: list[str], rows: list[dict]):
    new = not path.exists()
    with path.open("a", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=header)
        if new:
            w.writeheader()
        for r in rows:
            w.writerow(r)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--logdir", required=True, help="dir holding the shadow-log CSVs")
    ap.add_argument("--datadir", help="read CSVs from here instead of fetching (test)")
    args = ap.parse_args()

    cache, errors, NASDAQ_50, GUARD = build_cache(args.datadir)

    import scan
    _T = {"now": None}

    def _trunc(ticker, period=None, interval="1d"):
        df = cache.get((ticker.upper(), interval))
        if df is None:
            raise FileNotFoundError(f"{ticker} {interval}")
        out = df[df.index <= _T["now"]].tail(360)
        if out.empty:
            raise ValueError("empty")
        return out

    scan.load_bars = _trunc
    from lotto.scanner import scan_lotto_watchlist

    q2h = cache.get((GUARD, "2h"))
    if q2h is None or q2h.empty:
        print("DATA FETCH FAILED: no guard 2h bars; nothing logged.")
        return 1
    session = max(q2h.index).date()
    bars = {t.strftime("%H:%M"): t for t in q2h.index
            if t.date() == session and t.strftime("%H:%M") in (LATER_TIMES | {OPEN_TIME})}
    if OPEN_TIME not in bars:
        print(f"No {OPEN_TIME} ET bar for session {session}; nothing logged.")
        return 0

    def actionable_at(T):
        _T["now"] = T
        try:
            res = scan_lotto_watchlist(tickers=NASDAQ_50)
        except Exception as e:
            print(f"scan error @ {T}: {e}")
            return []
        return [(s.ticker, s.direction, s.why_now) for s in res.actionable_setups]

    open_sigs = actionable_at(bars[OPEN_TIME])
    later_keys = set()
    for tm in LATER_TIMES:
        if tm in bars:
            later_keys |= {(tk, d) for tk, d, _ in actionable_at(bars[tm])}

    logdir = Path(args.logdir)
    logdir.mkdir(parents=True, exist_ok=True)
    sig_rows = []
    n_unique = 0
    for tk, d, why in open_sigs:
        uniq = (tk, d) not in later_keys
        n_unique += int(uniq)
        sig_rows.append(dict(date=session.isoformat(), ticker=tk, dir=d,
                             open_unique=uniq, why_now=why))
    append_rows(logdir / "shadow_open_bar_log.csv",
                ["date", "ticker", "dir", "open_unique", "why_now"], sig_rows)
    append_rows(logdir / "shadow_summary.csv",
                ["date", "n_open", "n_open_unique", "n_later", "n_data_errors"],
                [dict(date=session.isoformat(), n_open=len(open_sigs),
                      n_open_unique=n_unique, n_later=len(later_keys),
                      n_data_errors=len(errors))])
    print(f"session {session}: {len(open_sigs)} open-bar signals "
          f"({n_unique} open-unique), {len(later_keys)} later-window, "
          f"{len(errors)} data errors -> logged.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
