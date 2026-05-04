"""Trading Dashboard scan CLI.

Usage:
    python -m scan SPY QQQ IWM
    python -m scan --help

Runs the indicator stack (MA Ribbon, Stochastic, SQN Regime) against the most
recent daily bars for each ticker via yfinance, prints a summary table, and
persists the raw results to ~/.trading-dashboard/scans/YYYY-MM-DD.json.
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd

from data.crypto_loader import is_crypto_symbol, load_crypto_bars
from data.yfinance_loader import load_bars
from events import log_flag, log_resolved, log_shadow_trade
from indicators import DEFAULT_PLUGINS_DIR, load_plugins
from indicators.ma_ribbon import MARibbon
from indicators.sqn_regime import (
    SQN_20_BANDS,
    SQNRegime,
    diagnose_sqn_pair,
)
from indicators.stochastic import Stochastic


SCANS_DIR = Path.home() / ".trading-dashboard" / "scans"

# qqq-gld-focus skill: SPY for regime context + QQQ + GLD for setups.
# Per ~/.claude/skills/user/qqq-gld-focus/SKILL.md "Sunday Scan Workflow".
FOCUS_SCAN_TICKERS: tuple[str, ...] = ("SPY", "QQQ", "GLD")
FOCUS_ALLOWED: frozenset[str] = frozenset(FOCUS_SCAN_TICKERS)

# Signals that are "actionable" enough to be worth flagging for later resolution
ACTIONABLE_SIGNALS = {
    "bull_cross_oversold",
    "bear_cross_overbought",
    "bullish_divergence",
    "bearish_divergence",
    "bull_continuation",
    "bear_continuation",
}


def _safe(value: Any) -> Any:
    if value is None:
        return None
    if isinstance(value, float):
        if value != value:  # NaN
            return None
        return round(value, 4)
    if pd.isna(value):
        return None
    return value


def scan_ticker(
    ticker: str,
    period: str | None = None,
    timeframe: str = "1d",
) -> dict[str, Any]:
    if is_crypto_symbol(ticker):
        # Crypto.com Exchange caps count at 300; for daily that's ~10mo —
        # enough for full MA200 warmup (200 bars) plus signal history.
        bars = load_crypto_bars(ticker, timeframe=timeframe, count=300)
    else:
        bars = load_bars(ticker, period=period, interval=timeframe)
    if bars.empty:
        raise ValueError(f"No bars for {ticker} timeframe={timeframe}")

    ma = MARibbon().compute(bars)
    stoch = Stochastic().compute(bars)
    sqn_100 = SQNRegime().compute(bars)
    sqn_20 = SQNRegime(lookback=20, bands=SQN_20_BANDS, name="sqn_regime_20").compute(bars)

    latest = bars.index[-1]
    ma_last = ma.loc[latest]
    stoch_last = stoch.loc[latest]
    sqn_100_last = sqn_100.loc[latest]
    sqn_20_last = sqn_20.loc[latest]

    sqn_100_regime = _safe(sqn_100_last["regime"])
    sqn_20_regime = _safe(sqn_20_last["regime"])
    sqn_20_value = _safe(sqn_20_last["sqn_value"])
    diagnostic = diagnose_sqn_pair(sqn_100_regime, sqn_20_regime, sqn_20_value)

    return {
        "ticker": ticker,
        "timeframe": timeframe,
        "bar_date": latest.strftime("%Y-%m-%d"),
        "close": _safe(float(bars["close"].iloc[-1])),
        "ma_ribbon": {
            "ma_10": _safe(ma_last["ma_10"]),
            "ma_20": _safe(ma_last["ma_20"]),
            "ma_50": _safe(ma_last["ma_50"]),
            "ma_200": _safe(ma_last["ma_200"]),
            "stack_state": _safe(ma_last["stack_state"]),
        },
        "stochastic": {
            "k": _safe(stoch_last["k"]),
            "d": _safe(stoch_last["d"]),
            "zone": _safe(stoch_last["zone"]),
            "signal": _safe(stoch_last["signal"]),
        },
        "sqn": {
            "sqn_value": _safe(sqn_100_last["sqn_value"]),
            "regime": sqn_100_regime,
            "sqn_20_value": sqn_20_value,
            "regime_20": sqn_20_regime,
            "diagnostic": diagnostic,
        },
    }


def compute_multi_tf(
    ticker: str,
    timeframes: tuple[str, ...] = ("1d", "1wk", "4h"),
) -> dict[str, dict[str, Any] | dict[str, str]]:
    """Run scan_ticker across multiple timeframes for a single ticker.

    Returns dict keyed by timeframe. If a timeframe fetch fails (e.g. yfinance
    refused intraday history for the symbol), that key carries an error dict
    instead of crashing the whole call.
    """
    results: dict[str, Any] = {}
    for tf in timeframes:
        try:
            results[tf] = scan_ticker(ticker, timeframe=tf)
        except Exception as exc:
            results[tf] = {"ticker": ticker.upper(), "timeframe": tf, "error": str(exc)}
    return results


def format_table(rows: list[dict[str, Any]]) -> str:
    header = (
        f"{'Ticker':<8}{'MA Stack':<18}{'Stoch K/D':<14}{'Zone':<12}"
        f"{'Signal':<24}{'SQN':<8}{'Regime':<14}{'SQN20':<8}{'Regime20':<14}"
    )
    sep = "─" * len(header)
    lines = [header, sep]
    for r in rows:
        if "error" in r:
            lines.append(f"{r['ticker']:<8}ERROR: {r['error']}")
            continue
        stoch_kd = (
            f"{r['stochastic']['k']:.1f}/{r['stochastic']['d']:.1f}"
            if r["stochastic"]["k"] is not None and r["stochastic"]["d"] is not None
            else "n/a"
        )
        sqn_val = (
            f"{r['sqn']['sqn_value']:.2f}"
            if r["sqn"]["sqn_value"] is not None
            else "n/a"
        )
        sqn_20_val = (
            f"{r['sqn'].get('sqn_20_value'):.2f}"
            if r["sqn"].get("sqn_20_value") is not None
            else "n/a"
        )
        lines.append(
            f"{r['ticker']:<8}"
            f"{str(r['ma_ribbon']['stack_state'] or 'n/a'):<18}"
            f"{stoch_kd:<14}"
            f"{str(r['stochastic']['zone'] or 'n/a'):<12}"
            f"{str(r['stochastic']['signal'] or 'n/a'):<24}"
            f"{sqn_val:<8}"
            f"{str(r['sqn']['regime'] or 'n/a'):<14}"
            f"{sqn_20_val:<8}"
            f"{str(r['sqn'].get('regime_20') or 'n/a'):<14}"
        )
    return "\n".join(lines)


def persist_scan(rows: list[dict[str, Any]], scans_dir: Path = SCANS_DIR) -> Path:
    scans_dir.mkdir(parents=True, exist_ok=True)
    now = datetime.now(timezone.utc)
    path = scans_dir / f"{now.strftime('%Y-%m-%d')}.json"
    payload = {
        "scan_time_utc": now.isoformat(),
        "tickers": {r["ticker"]: r for r in rows if "error" not in r},
        "errors": {r["ticker"]: r["error"] for r in rows if "error" in r},
    }
    path.write_text(json.dumps(payload, indent=2, default=str))
    return path


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="scan",
        description=(
            "Run the indicator stack (MA Ribbon, Stochastic, SQN Regime) "
            "on one or more tickers and print a summary table. Also supports "
            "shadow-trade and resolved-flag logging for discipline instrumentation."
        ),
    )
    p.add_argument(
        "tickers",
        nargs="*",
        help="One or more ticker symbols (e.g. SPY QQQ IWM)",
    )
    p.add_argument(
        "--period",
        default="2y",
        help="yfinance period string for bar history (default: 2y)",
    )
    p.add_argument(
        "--no-persist",
        action="store_true",
        help="Skip writing the JSON scan to ~/.trading-dashboard/scans/",
    )
    p.add_argument(
        "--shadow-trade",
        metavar="TICKER",
        help="Log a shadow-trade event (trade taken outside the dashboard) and exit.",
    )
    p.add_argument(
        "--mark-resolved",
        metavar="TICKER",
        help="Log a resolved event for a previously flagged ticker and exit.",
    )
    p.add_argument(
        "--note",
        help="Optional note attached to --shadow-trade or --mark-resolved.",
    )
    p.add_argument(
        "--list-plugins",
        action="store_true",
        help=f"List indicator plugins discovered in {DEFAULT_PLUGINS_DIR} and exit.",
    )
    p.add_argument(
        "--focus",
        action="store_true",
        help=(
            "qqq-gld-focus mode: with no tickers, scans "
            f"{', '.join(FOCUS_SCAN_TICKERS)}. With explicit tickers, only "
            "those three are allowed. See ~/.claude/skills/user/qqq-gld-focus/."
        ),
    )
    return p


def _emit_flags(rows: list[dict[str, Any]]) -> int:
    flagged = 0
    for r in rows:
        if "error" in r:
            continue
        signal = (r.get("stochastic") or {}).get("signal")
        if signal in ACTIONABLE_SIGNALS:
            log_flag(
                r["ticker"],
                payload={
                    "bar_date": r["bar_date"],
                    "stack_state": r["ma_ribbon"]["stack_state"],
                    "stoch_signal": signal,
                    "stoch_zone": r["stochastic"]["zone"],
                    "sqn_regime": r["sqn"]["regime"],
                },
            )
            flagged += 1
    return flagged


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    # Short-circuit modes
    if args.list_plugins:
        plugins = load_plugins(strict=False)
        if not plugins:
            print(f"No plugins found in {DEFAULT_PLUGINS_DIR}")
            print("To author one, drop a *.py file there with an `INDICATOR` attribute "
                  "or an `Indicator` class implementing IndicatorProtocol.")
        else:
            print(f"Plugins in {DEFAULT_PLUGINS_DIR}:")
            for name, ind in plugins.items():
                print(f"  - {name}: inputs={list(ind.inputs)}")
        return 0
    if args.shadow_trade:
        event = log_shadow_trade(args.shadow_trade, note=args.note)
        print(f"Logged shadow_trade for {event['ticker']} at {event['ts']}")
        return 0
    if args.mark_resolved:
        event = log_resolved(args.mark_resolved, note=args.note)
        print(f"Logged resolved for {event['ticker']} at {event['ts']}")
        return 0

    if args.focus:
        if not args.tickers:
            tickers = list(FOCUS_SCAN_TICKERS)
        else:
            tickers = [t.upper() for t in args.tickers]
            foreign = [t for t in tickers if t not in FOCUS_ALLOWED]
            if foreign:
                parser.error(
                    f"--focus restricts tickers to {', '.join(FOCUS_SCAN_TICKERS)}; "
                    f"got {', '.join(foreign)}"
                )
    else:
        if not args.tickers:
            parser.error(
                "must specify one or more tickers, or use --shadow-trade / "
                "--mark-resolved / --focus"
            )
        tickers = list(args.tickers)

    rows: list[dict[str, Any]] = []
    any_error = False
    for ticker in tickers:
        try:
            rows.append(scan_ticker(ticker.upper(), period=args.period))
        except Exception as exc:
            any_error = True
            rows.append({"ticker": ticker.upper(), "error": str(exc)})

    print(f"Trading Dashboard Scan — {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    print(format_table(rows))

    flagged = _emit_flags(rows)
    if flagged:
        print(f"\nLogged {flagged} flag event(s) to ~/.trading-dashboard/events.jsonl")

    if not args.no_persist:
        try:
            path = persist_scan(rows)
            print(f"Saved: {path}")
        except Exception as exc:
            print(f"\n⚠ Failed to persist scan: {exc}", file=sys.stderr)
            any_error = True

    return 1 if any_error else 0


if __name__ == "__main__":
    raise SystemExit(main())
