"""Measure production lotto-verdict BUY signal rate on real 2H bars.

The daily-bar lotto proxy in `backtest_strategies.py` understates the live
signal rate because daily Stoch crosses from <30 are rare in trending
markets — on 2H bars those crosses fire several times per week. Aaron's
orchestrator targets 2-4 lotto trades/week on QQQ + GLD; the question
is whether the *signal* rate from the production `lotto_verdict()` clears
that target on real 2H data, and if not, which gate is the binding
constraint.

The script:
1. Pulls daily + 2H bars for QQQ + GLD (yfinance hourly, resampled).
2. At each 2H bar, attaches the parent daily bar's MA stack, SQN(100),
   and SQN(20) values.
3. Calls `lotto_verdict()` with each 2H bar's Stoch signal/zone.
4. Counts "buy" verdicts; reports per-ticker signals/week.
5. Runs a gate-sensitivity sweep: relax one gate at a time, recount.

Usage (from repo root):
    PYTHONPATH=src python3 scripts/measure_lotto_signal_rate.py
    PYTHONPATH=src python3 scripts/measure_lotto_signal_rate.py \
        --tickers QQQ,GLD --json scripts/measure_lotto_output.json
"""
from __future__ import annotations

import argparse
import json
import sys
import warnings
from dataclasses import dataclass, asdict, field
from pathlib import Path

import pandas as pd

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "src"))

from data.yfinance_loader import load_bars  # noqa: E402
from indicators.ma_ribbon import MARibbon  # noqa: E402
from indicators.stochastic import Stochastic  # noqa: E402
from indicators.sqn_regime import SQN_100_BANDS, SQN_20_BANDS, SQNRegime  # noqa: E402
from scan_verdict import lotto_verdict  # noqa: E402

warnings.filterwarnings("ignore", category=FutureWarning)


def _close_col(df: pd.DataFrame) -> str:
    return "close" if "close" in df.columns else "Close"


def _normalize_ohlc(bars: pd.DataFrame) -> pd.DataFrame:
    """Force lowercase OHLC columns regardless of source casing."""
    close_col = _close_col(bars)
    return pd.DataFrame(
        {
            "open": bars[
                bars.columns[bars.columns.str.lower().str.startswith("open")][0]
            ].astype(float),
            "high": bars[
                bars.columns[bars.columns.str.lower().str.startswith("high")][0]
            ].astype(float),
            "low": bars[
                bars.columns[bars.columns.str.lower().str.startswith("low")][0]
            ].astype(float),
            "close": bars[close_col].astype(float),
        },
        index=bars.index,
    )


def compute_daily_indicators(daily: pd.DataFrame) -> pd.DataFrame:
    """MA stack + SQN(100) + SQN(20) on daily bars — feeds the parent-TF
    filter for the 2H lotto verdict."""
    df = _normalize_ohlc(daily)
    ribbon = MARibbon(periods=(10, 20, 50, 200)).compute(df)
    df["stack_state"] = ribbon["stack_state"]
    sqn100 = SQNRegime(lookback=100, bands=SQN_100_BANDS).compute(df)
    df["sqn_regime"] = sqn100["regime"]
    sqn20 = SQNRegime(lookback=20, bands=SQN_20_BANDS).compute(df)
    df["sqn20_value"] = sqn20["sqn_value"]
    return df


def compute_2h_stoch(bars_2h: pd.DataFrame) -> pd.DataFrame:
    """Stoch zone + signal on 2H bars — the lotto trigger TF."""
    df = _normalize_ohlc(bars_2h)
    stoch = Stochastic(length=14, smooth_k=7, smooth_d=7).compute(df)
    df["stoch_zone"] = stoch["zone"]
    df["stoch_signal"] = stoch["signal"]
    return df


def attach_parent_daily(bars_2h: pd.DataFrame, daily: pd.DataFrame) -> pd.DataFrame:
    """For each 2H bar, find the most recent COMPLETED daily bar (the one
    that ended on or before the 2H bar's date) and copy its stack_state,
    sqn_regime, sqn20_value. This matches the live scanner's behavior:
    the daily filter uses the last closed daily bar.
    """
    daily_keyed = daily.copy()
    daily_keyed.index = daily_keyed.index.normalize()
    bars_2h = bars_2h.copy()
    bars_2h["date_key"] = pd.to_datetime(bars_2h.index).normalize()
    merged = bars_2h.merge(
        daily_keyed[["stack_state", "sqn_regime", "sqn20_value"]],
        how="left", left_on="date_key", right_index=True,
    )
    merged.index = bars_2h.index
    # Forward-fill across intraday bars where the date_key match misses
    # (timezone edge cases on the first bar of a session).
    for col in ("stack_state", "sqn_regime", "sqn20_value"):
        merged[col] = merged[col].ffill()
    return merged.drop(columns=["date_key"])


# ─────────────────────────────────────────────────────────────────────────
# Gate variants — relax one rule at a time to find the binding constraint.
# Each variant is a function `(verdict_args) -> 'buy' | 'wait' | 'no_go'`.
# ─────────────────────────────────────────────────────────────────────────


def _verdict_raw(args: dict) -> str:
    return lotto_verdict(**args).verdict


def _classify(
    args: dict,
    *,
    relax_chop: bool = False,
    relax_chase: bool = False,
    relax_bear_volatile: bool = False,
    relax_h2_zone: bool = False,
    relax_h2_signal: bool = False,
    h2_signal_extra: set[str] | None = None,
) -> str:
    """Replicate lotto_verdict() with one gate optionally relaxed.

    Long-side only — that's where Aaron's 2-4/wk target lives. Short-side
    is structurally rare on QQQ + GLD because SQN(100) Bear is rare.
    """
    direction = args["direction"]
    if direction != "long":
        # No relaxation defined for shorts — just use production.
        return _verdict_raw(args)

    stack = args["daily_stack"]
    sqn100 = args["sqn_100_regime"]
    sqn20 = args["sqn_20_value"]
    h2_sig = args["h2_signal"]
    h2_zone = args["h2_zone"]

    # Gate 1: daily stack chop
    if stack in ("chop", "tangled", None) and not relax_chop:
        return "no_go"
    # Gate 2: regime conflict
    if sqn100 == "strong_bear":
        return "no_go"
    # Gate 3: bear volatile
    if (
        sqn100 == "bear"
        and sqn20 is not None
        and sqn20 < -1.9
        and not relax_bear_volatile
    ):
        return "no_go"
    # Gate 4: chase guard
    if sqn20 is not None and sqn20 > 2.5 and not relax_chase:
        return "no_go"
    # Gate 5: daily stack direction
    if stack in ("full_bear", "bear_developing"):
        return "no_go"

    # Trigger: 2H signal + zone
    long_signals = {"bull_cross_oversold", "bull_continuation"}
    if h2_signal_extra:
        long_signals = long_signals | h2_signal_extra
    ok_signal = h2_sig in long_signals if not relax_h2_signal else h2_sig is not None
    ok_zone = h2_zone in ("oversold", "mid") if not relax_h2_zone else True
    if ok_signal and ok_zone:
        return "buy"
    return "wait"


# ─────────────────────────────────────────────────────────────────────────
# Per-ticker run
# ─────────────────────────────────────────────────────────────────────────


@dataclass
class TickerResult:
    ticker: str
    sample_start: str
    sample_end: str
    weeks: float
    n_2h_bars: int
    counts: dict[str, int] = field(default_factory=dict)
    per_week: dict[str, float] = field(default_factory=dict)


def run_one(ticker: str) -> TickerResult | None:
    print(f"Loading {ticker} (daily + 2H)...")
    daily_raw = load_bars(ticker, period="max", interval="1d")
    bars_2h_raw = load_bars(ticker, period="2y", interval="2h")
    if daily_raw is None or daily_raw.empty:
        print(f"  {ticker}: no daily data")
        return None
    if bars_2h_raw is None or bars_2h_raw.empty:
        print(f"  {ticker}: no 2H data")
        return None

    daily = compute_daily_indicators(daily_raw)
    bars_2h = compute_2h_stoch(bars_2h_raw)
    merged = attach_parent_daily(bars_2h, daily)

    # Drop the indicator warmup period — first 200 daily bars (MA-200) and
    # first 100 for SQN. Since the 2H sample starts ~2y back and daily
    # goes back to ticker inception, those windows are always warm by the
    # time 2H bars start. But we still need to drop 2H bars where the
    # parent stack_state is NaN (e.g., very first session after splice).
    merged = merged.dropna(
        subset=["stack_state", "sqn_regime", "stoch_zone"], how="any",
    )
    n_bars = len(merged)
    if n_bars == 0:
        print(f"  {ticker}: no usable 2H bars after warmup")
        return None

    sample_start = merged.index[0].date()
    sample_end = merged.index[-1].date()
    days = (merged.index[-1] - merged.index[0]).days
    weeks = max(days / 7.0, 1e-6)

    # Count signals under production + each relaxation variant. LONG only.
    # "+div" variants add bullish_divergence to the trigger whitelist — that's
    # a real bullish stoch signal type that the current LONG_TRIGGERS misses.
    variants = {
        "production": dict(),
        "add_bull_divergence": dict(h2_signal_extra={"bullish_divergence"}),
        "relax_chop": dict(relax_chop=True),
        "relax_chop+div": dict(
            relax_chop=True, h2_signal_extra={"bullish_divergence"},
        ),
        "relax_chase": dict(relax_chase=True),
        "relax_bear_volatile": dict(relax_bear_volatile=True),
        "relax_h2_zone": dict(relax_h2_zone=True),
        "relax_h2_signal_any": dict(relax_h2_signal=True),
        "relax_all": dict(
            relax_chop=True, relax_chase=True, relax_bear_volatile=True,
            relax_h2_zone=True, relax_h2_signal=True,
        ),
    }
    counts = {name: 0 for name in variants}
    counts["any_2h_signal_present"] = 0
    counts["daily_stack_supports_long"] = 0
    counts["regime_clean_long"] = 0

    for i in range(n_bars):
        row = merged.iloc[i]
        stack_raw = row.get("stack_state")
        regime_raw = row.get("sqn_regime")
        zone_raw = row.get("stoch_zone")
        sig_raw = row.get("stoch_signal")
        sqn20 = row.get("sqn20_value")
        stack = None if pd.isna(stack_raw) else str(stack_raw)
        regime = None if pd.isna(regime_raw) else str(regime_raw)
        zone = None if pd.isna(zone_raw) else str(zone_raw)
        sig = None if pd.isna(sig_raw) else str(sig_raw)
        sqn20_v = None if pd.isna(sqn20) else float(sqn20)

        args = dict(
            daily_stack=stack, sqn_100_regime=regime, sqn_20_value=sqn20_v,
            h2_signal=sig, h2_zone=zone, direction="long",
        )
        # Diagnostic counters
        if sig in {
            "bull_cross_oversold", "bull_continuation",
            "bullish_divergence", "bear_cross_overbought",
            "bear_continuation", "bearish_divergence",
        }:
            counts["any_2h_signal_present"] += 1
        if stack in ("full_bull", "bull_developing", "compression"):
            counts["daily_stack_supports_long"] += 1
        if regime != "strong_bear" and not (
            regime == "bear" and sqn20_v is not None and sqn20_v < -1.9
        ):
            if sqn20_v is None or sqn20_v <= 2.5:
                counts["regime_clean_long"] += 1

        for name, kwargs in variants.items():
            if name == "production":
                if _verdict_raw(args) == "buy":
                    counts[name] += 1
            else:
                if _classify(args, **kwargs) == "buy":
                    counts[name] += 1

    per_week = {k: v / weeks for k, v in counts.items()}
    return TickerResult(
        ticker=ticker,
        sample_start=str(sample_start),
        sample_end=str(sample_end),
        weeks=weeks,
        n_2h_bars=n_bars,
        counts=counts,
        per_week=per_week,
    )


def render(results: list[TickerResult]) -> str:
    out: list[str] = []
    out.append("=" * 100)
    out.append(
        "Lotto BUY signal rate on real 2H bars — production vs relaxed gates"
    )
    out.append("=" * 100)
    out.append("")
    out.append(
        "Aaron's orchestrator target: 2-4 lotto trades/week (across the lotto book)."
    )
    out.append(
        "This script measures signals/week from production `lotto_verdict()` "
        "on LONG direction"
    )
    out.append("(short signals are structurally rare on QQQ+GLD because SQN(100) "
               "Bear is rare).")
    out.append("")

    for r in results:
        out.append(f"━━━ {r.ticker} ━━━")
        out.append(
            f"  Sample: {r.sample_start} → {r.sample_end}  "
            f"({r.weeks:.1f} weeks, {r.n_2h_bars} 2H bars)"
        )
        out.append("")
        out.append("  Diagnostic counters (LONG only):")
        out.append(
            f"    2H Stoch signal present (any kind): "
            f"{r.counts['any_2h_signal_present']:>5d}  "
            f"({r.per_week['any_2h_signal_present']:5.2f}/wk)"
        )
        out.append(
            f"    Daily stack supports long:         "
            f"{r.counts['daily_stack_supports_long']:>5d}  "
            f"({r.per_week['daily_stack_supports_long']:5.2f}/wk)"
        )
        out.append(
            f"    Regime clean for long:             "
            f"{r.counts['regime_clean_long']:>5d}  "
            f"({r.per_week['regime_clean_long']:5.2f}/wk)"
        )
        out.append("")
        out.append("  BUY signal counts (LONG):")
        for name in (
            "production", "add_bull_divergence",
            "relax_chop", "relax_chop+div",
            "relax_chase", "relax_bear_volatile", "relax_h2_zone",
            "relax_h2_signal_any", "relax_all",
        ):
            count = r.counts[name]
            per_wk = r.per_week[name]
            tag = "  ← production" if name == "production" else ""
            out.append(
                f"    {name:>22}: n={count:>4d}  "
                f"{per_wk:>5.2f}/wk{tag}"
            )
        out.append("")

    # Cross-ticker summary
    if len(results) > 1:
        out.append("━━━ Cross-ticker (sum across watchlist) ━━━")
        for name in (
            "production", "add_bull_divergence",
            "relax_chop", "relax_chop+div",
            "relax_chase", "relax_bear_volatile", "relax_h2_zone",
            "relax_h2_signal_any", "relax_all",
        ):
            total = sum(r.counts[name] for r in results)
            avg_per_wk = sum(r.per_week[name] for r in results)
            out.append(
                f"    {name:>22}: total={total:>4d}  "
                f"combined {avg_per_wk:>5.2f}/wk"
            )

    return "\n".join(out)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument(
        "--tickers", default="QQQ,GLD",
        help="Comma-separated tickers (default QQQ,GLD — the lotto default watchlist)",
    )
    ap.add_argument("--json", type=Path, help="Optional JSON output path")
    args = ap.parse_args()
    tickers = [t.strip() for t in args.tickers.split(",") if t.strip()]

    results: list[TickerResult] = []
    for tk in tickers:
        r = run_one(tk)
        if r is not None:
            results.append(r)

    print()
    text = render(results)
    print(text)

    if args.json:
        args.json.write_text(
            json.dumps({"results": [asdict(r) for r in results]}, indent=2)
        )
        print(f"\nWrote JSON to: {args.json}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
