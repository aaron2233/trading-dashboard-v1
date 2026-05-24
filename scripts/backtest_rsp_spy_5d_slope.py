"""Backtest a 2-ticker ratio 5-day slope breadth indicator.

Pulls daily closes for a numerator + denominator (default RSP / SPY since RSP
inception 2003-04-24), computes the 5-trading-day slope of the ratio, and
evaluates how well that signal forecasts forward SPY drawdowns at multiple
horizons. Outputs precision/recall tables at candidate amber/red thresholds
so the defaults in ``src/regime_health/thresholds.py`` can be calibrated
against history rather than literature.

Usage (from repo root):
    PYTHONPATH=src python3 scripts/backtest_rsp_spy_5d_slope.py
    PYTHONPATH=src python3 scripts/backtest_rsp_spy_5d_slope.py --json out.json
    PYTHONPATH=src python3 scripts/backtest_rsp_spy_5d_slope.py \
        --numerator XLY --denominator XLP --json scripts/xly_xlp.json

Notes:
- Uses ``data.yfinance_loader.load_bars`` so the data path is identical to the
  live tier3_breadth reader.
- Forward windows: 5 / 10 / 20 / 60 trading days.
- Drawdown definition: peak-to-trough max drawdown of SPY close within the
  forward window starting from t+1. A "drawdown event" at horizon H is
  forward_max_drawdown <= -drawdown_floor (e.g. -5%, -10%).
- Precision = P(drawdown event | signal red).
- Recall    = P(signal red in lead-up | drawdown event), where lead-up = the
  N=20 trading days BEFORE the drawdown window starts.
"""
from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass, asdict
from pathlib import Path

import numpy as np
import pandas as pd

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "src"))

from data.yfinance_loader import load_bars  # noqa: E402

THRESHOLDS_PCT = [-0.5, -1.0, -1.5, -2.0, -2.5, -3.0]
HORIZONS = [5, 10, 20, 60]
DRAWDOWN_FLOORS = [0.03, 0.05, 0.10]  # 3%, 5%, 10% forward drawdown floors


@dataclass
class ThresholdRow:
    threshold_pct: float
    horizon_days: int
    drawdown_floor_pct: float
    n_signal_days: int
    n_event_days: int
    true_positives: int
    false_positives: int
    precision: float
    base_rate: float
    lift_vs_base: float


def _close_col(df: pd.DataFrame) -> str:
    return "close" if "close" in df.columns else "Close"


def fetch_history(numerator: str = "RSP", denominator: str = "SPY") -> pd.DataFrame:
    """Returns a frame indexed by date with columns: num, den, spy, ratio, slope_5d_pct.

    `spy` is always SPY closes (used as the forward-drawdown reference, even
    when num/den != RSP/SPY). When num or den == 'SPY' it reuses that series.
    """
    num = load_bars(numerator, period="max", interval="1d")
    den = load_bars(denominator, period="max", interval="1d")
    if num is None or den is None or num.empty or den.empty:
        raise RuntimeError(f"Empty bars from yfinance for {numerator} or {denominator}")
    num_s = num[_close_col(num)].astype(float).rename("num")
    den_s = den[_close_col(den)].astype(float).rename("den")
    if numerator == "SPY":
        spy_s = num_s.rename("spy")
    elif denominator == "SPY":
        spy_s = den_s.rename("spy")
    else:
        spy = load_bars("SPY", period="max", interval="1d")
        if spy is None or spy.empty:
            raise RuntimeError("Empty bars from yfinance for SPY (drawdown reference)")
        spy_s = spy[_close_col(spy)].astype(float).rename("spy")
    df = pd.concat([num_s, den_s, spy_s], axis=1).dropna().sort_index()
    df["ratio"] = df["num"] / df["den"]
    df["slope_5d_pct"] = (df["ratio"] / df["ratio"].shift(5) - 1.0) * 100.0
    return df


def forward_drawdown(closes: pd.Series, horizon: int) -> pd.Series:
    """For each day t, max drawdown (negative number) of close over (t+1, t+horizon].

    Computed as min(close in window) / close[t] - 1. Returns 0 if window
    has no draw (close strictly rose).
    """
    fwd_min = closes.shift(-1).rolling(horizon, min_periods=1).min().shift(
        -(horizon - 1)
    )
    dd = fwd_min / closes - 1.0
    dd = dd.where(dd < 0.0, 0.0)
    return dd


def evaluate(
    df: pd.DataFrame,
    threshold_pct: float,
    horizon_days: int,
    drawdown_floor: float,
) -> ThresholdRow:
    fwd_dd = forward_drawdown(df["spy"], horizon_days)
    valid = df["slope_5d_pct"].notna() & fwd_dd.notna()
    sub = pd.DataFrame(
        {"signal": df.loc[valid, "slope_5d_pct"], "fwd_dd": fwd_dd.loc[valid]}
    )
    is_signal = sub["signal"] <= threshold_pct
    is_event = sub["fwd_dd"] <= -drawdown_floor
    n_signal = int(is_signal.sum())
    n_event = int(is_event.sum())
    tp = int((is_signal & is_event).sum())
    fp = int((is_signal & ~is_event).sum())
    precision = tp / n_signal if n_signal else float("nan")
    base_rate = n_event / len(sub) if len(sub) else float("nan")
    lift = precision / base_rate if base_rate else float("nan")
    return ThresholdRow(
        threshold_pct=threshold_pct,
        horizon_days=horizon_days,
        drawdown_floor_pct=drawdown_floor * 100,
        n_signal_days=n_signal,
        n_event_days=n_event,
        true_positives=tp,
        false_positives=fp,
        precision=precision,
        base_rate=base_rate,
        lift_vs_base=lift,
    )


def percentile_table(df: pd.DataFrame) -> dict[str, float]:
    s = df["slope_5d_pct"].dropna()
    pcts = [1, 5, 10, 25, 50, 75, 90, 95, 99]
    return {f"p{p:02d}": float(np.percentile(s, p)) for p in pcts}


def render_text(
    rows: list[ThresholdRow],
    pct: dict[str, float],
    df: pd.DataFrame,
    label: str = "RSP/SPY",
) -> str:
    out = []
    out.append("=" * 80)
    out.append(f"{label} 5-day slope — threshold backtest")
    out.append("=" * 80)
    out.append(f"Sample: {df.index.min().date()} → {df.index.max().date()}")
    out.append(f"Trading days with signal: {df['slope_5d_pct'].notna().sum()}")
    out.append("")
    out.append("Slope distribution (percentiles, %):")
    for k in ["p01", "p05", "p10", "p25", "p50", "p75", "p90", "p95", "p99"]:
        out.append(f"  {k}: {pct[k]:+.3f}")
    out.append("")
    out.append(
        "Per-threshold forecasting performance vs. forward SPY drawdowns:"
    )
    out.append("(precision = P(drawdown ≥ floor | signal triggered))")
    out.append("(lift     = precision / base rate; >1.0 = signal adds info)")
    out.append("")
    by_floor: dict[float, list[ThresholdRow]] = {}
    for r in rows:
        by_floor.setdefault(r.drawdown_floor_pct, []).append(r)
    for floor in sorted(by_floor):
        out.append(f"--- Forward drawdown floor: {floor:.0f}% ---")
        out.append(
            f"{'thresh':>7} {'horiz':>6} {'sig#':>6} {'evt#':>6} "
            f"{'TP':>5} {'FP':>5} {'prec':>7} {'base':>7} {'lift':>6}"
        )
        for r in sorted(
            by_floor[floor], key=lambda x: (x.horizon_days, x.threshold_pct)
        ):
            out.append(
                f"{r.threshold_pct:>+6.1f}% {r.horizon_days:>5}d "
                f"{r.n_signal_days:>6d} {r.n_event_days:>6d} "
                f"{r.true_positives:>5d} {r.false_positives:>5d} "
                f"{r.precision:>6.1%} {r.base_rate:>6.1%} "
                f"{r.lift_vs_base:>5.2f}x"
            )
        out.append("")
    return "\n".join(out)


def recommend(rows: list[ThresholdRow]) -> dict:
    """Pick amber/red thresholds optimized for the 20d horizon × 5% drawdown
    floor combo (mid-horizon, meaningful drawdown). Heuristic: pick the
    largest (least-negative) threshold that delivers ≥1.5x lift for amber,
    and the threshold that delivers ≥2.5x lift for red — preferring more
    samples (n_signal) when ties.
    """
    target = [
        r for r in rows if r.horizon_days == 20 and r.drawdown_floor_pct == 5.0
    ]
    target = sorted(target, key=lambda r: r.threshold_pct, reverse=True)
    amber = next(
        (r for r in target if r.lift_vs_base >= 1.5 and r.n_signal_days >= 20),
        None,
    )
    red = next(
        (r for r in target if r.lift_vs_base >= 2.5 and r.n_signal_days >= 10),
        None,
    )
    return {
        "horizon_days": 20,
        "drawdown_floor_pct": 5.0,
        "amber_recommended": asdict(amber) if amber else None,
        "red_recommended": asdict(red) if red else None,
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--json", type=Path, help="Optional path to write JSON output.")
    ap.add_argument("--numerator", default="RSP", help="Ratio numerator ticker (default RSP)")
    ap.add_argument("--denominator", default="SPY", help="Ratio denominator ticker (default SPY)")
    args = ap.parse_args()

    df = fetch_history(args.numerator, args.denominator)
    rows: list[ThresholdRow] = []
    for floor in DRAWDOWN_FLOORS:
        for horizon in HORIZONS:
            for thresh in THRESHOLDS_PCT:
                rows.append(evaluate(df, thresh, horizon, floor))
    pct = percentile_table(df)
    rec = recommend(rows)

    label = f"{args.numerator}/{args.denominator}"
    text = render_text(rows, pct, df, label=label)
    print(text)
    print()
    print("RECOMMENDED CALIBRATION (20d horizon, 5% drawdown floor):")
    print(json.dumps(rec, indent=2, default=str))

    if args.json:
        out = {
            "sample_start": str(df.index.min().date()),
            "sample_end": str(df.index.max().date()),
            "rows": [asdict(r) for r in rows],
            "percentiles": pct,
            "recommendation": rec,
        }
        args.json.write_text(json.dumps(out, indent=2, default=str))
        print(f"\nWrote JSON to: {args.json}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
