"""Lotto signal history across a ticker list — clustered to trade events
with forward returns and CSV export.

Reuses the production `lotto_verdict()` and indicator pipeline from
`measure_lotto_signal_rate.py` so the trigger logic stays in lockstep with
what the live dashboard uses.

Clustering rule
---------------
Multiple 2H signal fires on the same ticker + direction within `CLUSTER_GAP_DAYS`
trading days are folded into one trade event. The earliest fire is the
entry; subsequent fires inside the window are absorbed (with a fires_in_cluster
counter). This avoids overcounting consecutive same-bar / next-bar re-fires
that you would not take as separate trades in practice.

Forward returns
---------------
For each cluster entry, close-to-close % return at +1d / +3d / +5d trading
days on the underlying. For SHORT signals the sign is flipped so positive
always means "your direction worked." This is a *directional* proxy, not
options P&L (no premium / delta / theta modeling).

Usage:
    PYTHONPATH=src .venv/bin/python scripts/lotto_signal_history.py \
        --tickers SMH,GLD,USO,SLV,TQQQ,TSLA,IONQ,NVDA,AAPL,MSFT,IWM \
        --start 2026-01-01 --end 2026-05-11 \
        --csv scripts/lotto_signal_history.csv
"""
from __future__ import annotations

import argparse
import sys
import warnings
from pathlib import Path

import pandas as pd

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "src"))
sys.path.insert(0, str(REPO / "scripts"))

warnings.filterwarnings("ignore", category=FutureWarning)
warnings.filterwarnings("ignore", category=UserWarning)

from measure_lotto_signal_rate import (  # noqa: E402
    compute_daily_indicators, compute_2h_stoch, attach_parent_daily,
)
from data.yfinance_loader import load_bars  # noqa: E402
from scan_verdict import lotto_verdict  # noqa: E402


CLUSTER_GAP_DAYS = 3   # trading-day gap; tighter than this = same trade
FWD_HORIZONS = [1, 3, 5]   # trading days for forward-return checks


# ─── Signal scan ─────────────────────────────────────────────────────────────


def _fires(ticker: str, start: str, end: str) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Return (fires_df, daily_close_series). fires_df rows = every 2H bar
    where production lotto_verdict() == 'buy' in [start, end]."""
    daily_raw = load_bars(ticker, period="max", interval="1d")
    bars_2h_raw = load_bars(ticker, period="2y", interval="2h")
    if daily_raw is None or daily_raw.empty or bars_2h_raw is None or bars_2h_raw.empty:
        return pd.DataFrame(), pd.Series(dtype=float)

    daily = compute_daily_indicators(daily_raw)
    bars_2h = compute_2h_stoch(bars_2h_raw)
    merged = attach_parent_daily(bars_2h, daily).dropna(
        subset=["stack_state", "sqn_regime", "stoch_zone"], how="any"
    )

    idx = merged.index
    if hasattr(idx, "tz") and idx.tz is not None:
        merged = merged.loc[
            (idx >= pd.Timestamp(start, tz=idx.tz))
            & (idx <= pd.Timestamp(end, tz=idx.tz))
        ]
    else:
        merged = merged.loc[start:end]

    close_col = "close" if "close" in merged.columns else "Close"
    daily_close_col = "close" if "close" in daily.columns else "Close"
    daily_close = daily[daily_close_col]
    # Normalize daily index to date so forward-return lookups work cleanly
    daily_close.index = pd.to_datetime(daily_close.index).normalize()
    daily_close = daily_close[~daily_close.index.duplicated(keep="last")].sort_index()

    rows: list[dict] = []
    for ts, row in merged.iterrows():
        stack = None if pd.isna(row["stack_state"]) else str(row["stack_state"])
        regime = None if pd.isna(row["sqn_regime"]) else str(row["sqn_regime"])
        zone = None if pd.isna(row["stoch_zone"]) else str(row["stoch_zone"])
        sig = None if pd.isna(row["stoch_signal"]) else str(row["stoch_signal"])
        sqn20 = row.get("sqn20_value")
        sqn20_v = None if pd.isna(sqn20) else float(sqn20)

        for direction in ("long", "short"):
            args = dict(
                daily_stack=stack, sqn_100_regime=regime,
                sqn_20_value=sqn20_v, h2_signal=sig, h2_zone=zone,
                direction=direction,
            )
            if lotto_verdict(**args).verdict == "buy":
                rows.append({
                    "ticker": ticker,
                    "direction": direction,
                    "timestamp": ts,
                    "entry_close": float(row[close_col]),
                    "stack": stack,
                    "regime": regime,
                    "sqn20": sqn20_v,
                    "stoch_sig": sig,
                    "stoch_zone": zone,
                })

    return pd.DataFrame(rows), daily_close


# ─── Clustering ──────────────────────────────────────────────────────────────


def _trading_days_between(daily_idx: pd.DatetimeIndex, a: pd.Timestamp, b: pd.Timestamp) -> int:
    """Count trading days strictly between a and b (exclusive). Uses the
    daily index as the trading-day calendar for the underlying."""
    if a > b:
        a, b = b, a
    mask = (daily_idx > a.normalize()) & (daily_idx <= b.normalize())
    return int(mask.sum())


def cluster(fires: pd.DataFrame, daily_close: pd.Series, gap_days: int = CLUSTER_GAP_DAYS) -> pd.DataFrame:
    """Fold consecutive same-(ticker, direction) fires within `gap_days`
    trading days into one trade event. The first fire is the entry."""
    if fires.empty:
        return fires
    fires = fires.sort_values(["ticker", "direction", "timestamp"]).reset_index(drop=True)
    daily_idx = daily_close.index

    events: list[dict] = []
    cur: dict | None = None
    for _, r in fires.iterrows():
        if (
            cur is not None
            and r["ticker"] == cur["ticker"]
            and r["direction"] == cur["direction"]
            and _trading_days_between(daily_idx, cur["last_ts"], r["timestamp"]) <= gap_days
        ):
            cur["fires_in_cluster"] += 1
            cur["last_ts"] = r["timestamp"]
        else:
            if cur is not None:
                events.append(cur)
            cur = {
                "ticker": r["ticker"], "direction": r["direction"],
                "entry_ts": r["timestamp"], "last_ts": r["timestamp"],
                "entry_close": r["entry_close"], "stack": r["stack"],
                "regime": r["regime"], "sqn20": r["sqn20"],
                "stoch_sig": r["stoch_sig"], "stoch_zone": r["stoch_zone"],
                "fires_in_cluster": 1,
            }
    if cur is not None:
        events.append(cur)
    return pd.DataFrame(events)


# ─── Forward returns ─────────────────────────────────────────────────────────


def _fwd_return(daily_close: pd.Series, entry_ts: pd.Timestamp, n_days: int, direction: str) -> float | None:
    """Close-to-close return n trading days after entry. Sign-flipped for
    shorts so positive always means the directional thesis worked."""
    if daily_close.empty:
        return None
    entry_date = entry_ts.normalize()
    # Find the entry date's daily close. If entry_ts is intraday, the parent
    # daily bar's close is the same calendar date — but yfinance daily bars
    # are dated at the session open boundary depending on TZ. Use searchsorted
    # to locate "the daily bar whose date == entry_date.date()", falling back
    # to the nearest prior bar if exact match missing.
    idx = daily_close.index
    pos = idx.searchsorted(entry_date)
    if pos >= len(idx):
        return None
    # If exact match exists at `pos`, that's the entry bar; otherwise use pos-1
    if pos < len(idx) and idx[pos].normalize() == entry_date:
        entry_pos = pos
    elif pos > 0:
        entry_pos = pos - 1
    else:
        return None
    fwd_pos = entry_pos + n_days
    if fwd_pos >= len(daily_close):
        return None
    entry_px = float(daily_close.iloc[entry_pos])
    fwd_px = float(daily_close.iloc[fwd_pos])
    raw = (fwd_px - entry_px) / entry_px
    return raw if direction == "long" else -raw


def add_forward_returns(events: pd.DataFrame, ticker_to_close: dict[str, pd.Series]) -> pd.DataFrame:
    if events.empty:
        return events
    out = events.copy()
    for n in FWD_HORIZONS:
        out[f"fwd_{n}d_pct"] = out.apply(
            lambda r: _fwd_return(
                ticker_to_close.get(r["ticker"], pd.Series(dtype=float)),
                r["entry_ts"], n, r["direction"],
            ),
            axis=1,
        )
    return out


# ─── Main ────────────────────────────────────────────────────────────────────


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--tickers", required=True,
                    help="Comma-separated ticker list")
    ap.add_argument("--start", required=True)
    ap.add_argument("--end", required=True)
    ap.add_argument("--csv", type=Path, default=None,
                    help="Write trade-event CSV to this path")
    ap.add_argument("--gap-days", type=int, default=CLUSTER_GAP_DAYS)
    args = ap.parse_args()

    tickers = [t.strip().upper() for t in args.tickers.split(",") if t.strip()]
    all_events: list[pd.DataFrame] = []
    ticker_to_close: dict[str, pd.Series] = {}

    for t in tickers:
        print(f"Scanning {t}...")
        fires, daily_close = _fires(t, args.start, args.end)
        ticker_to_close[t] = daily_close
        if fires.empty:
            print(f"  {t}: no fires")
            continue
        events = cluster(fires, daily_close, gap_days=args.gap_days)
        events = add_forward_returns(events, {t: daily_close})
        all_events.append(events)
        print(f"  {t}: {len(fires)} fires -> {len(events)} trade events")

    if not all_events:
        print("No events.")
        return 0

    df = pd.concat(all_events, ignore_index=True)
    df = df.sort_values("entry_ts").reset_index(drop=True)

    # ── Per-ticker summary ──
    print("\n══ Trade events per ticker (after clustering) ══")
    summary = (
        df.groupby(["ticker", "direction"])
        .agg(
            n_trades=("entry_ts", "count"),
            fires_total=("fires_in_cluster", "sum"),
            **{f"hit_rate_{n}d": (f"fwd_{n}d_pct", lambda s: (s > 0).mean()) for n in FWD_HORIZONS},
            **{f"avg_{n}d_pct": (f"fwd_{n}d_pct", "mean") for n in FWD_HORIZONS},
        )
        .reset_index()
    )
    # Pretty-print
    for _, r in summary.iterrows():
        line = (
            f"  {r['ticker']:<6} {r['direction']:<5} "
            f"trades={int(r['n_trades']):<3} (clustered from {int(r['fires_total'])} fires)  "
        )
        for n in FWD_HORIZONS:
            avg = r[f"avg_{n}d_pct"]
            hr = r[f"hit_rate_{n}d"]
            line += f"+{n}d: {avg*100:+5.2f}% hit={hr*100:4.0f}%  "
        print(line)

    # ── Aggregate ──
    print(f"\nTotal trade events: {len(df)}  (clustered from "
          f"{int(df['fires_in_cluster'].sum())} raw 2H fires)")
    for n in FWD_HORIZONS:
        col = f"fwd_{n}d_pct"
        valid = df[col].dropna()
        if valid.empty:
            continue
        hr = (valid > 0).mean()
        avg = valid.mean()
        med = valid.median()
        print(f"  +{n}d  avg {avg*100:+.2f}%  median {med*100:+.2f}%  hit {hr*100:.0f}%  (n={len(valid)})")

    # ── CSV export ──
    if args.csv:
        out = df.copy()
        out["entry_ts"] = out["entry_ts"].dt.strftime("%Y-%m-%d %H:%M")
        out = out.drop(columns=["last_ts"], errors="ignore")
        for n in FWD_HORIZONS:
            out[f"fwd_{n}d_pct"] = out[f"fwd_{n}d_pct"].apply(
                lambda v: round(v * 100, 3) if v is not None and pd.notna(v) else None
            )
        out.to_csv(args.csv, index=False)
        print(f"\nWrote CSV: {args.csv}  ({len(out)} rows)")

    return 0


if __name__ == "__main__":
    sys.exit(main())
