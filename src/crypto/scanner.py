"""Crypto setup aggregator — multi-TF MA Ribbon + Stoch + SQN per trading-edge skill.

Per `~/.claude/skills/user/trading-edge/SKILL.md` (crypto section): the same
indicator stack runs symbol-agnostic on crypto bars. This module composes the
multi-TF read (Weekly, Daily, 4H, 2H) the skill prescribes, applies the
disagreement-resolution matrix, and surfaces a confluence rating + live
ticker data for the dashboard's CryptoView.

Order-book and execution data live with the brokerage UI — same anti-stale
discipline as the options-input pivot. This module focuses on price-action
analysis where the dashboard has authoritative source-of-truth.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable, Literal


CRYPTO_TIMEFRAMES: tuple[str, ...] = ("1wk", "1d", "4h", "2h")

# Confluence rating per the trading-edge cross-TF resolution matrix.
Confluence = Literal[
    "high_conviction_long",
    "high_conviction_short",
    "medium_conviction_long",
    "medium_conviction_short",
    "counter_weekly",         # Daily setup against Weekly bias
    "wait",                   # alignment exists but trigger TF hasn't fired
    "skip_chop",              # Daily chop = no trade (hard rule)
    "skip_no_setup",          # nothing actionable
]
Direction = Literal["long", "short", "none"]


@dataclass
class CryptoTicker:
    instrument_name: str
    last_price: float | None
    bid: float | None
    ask: float | None
    change_24h_pct: float | None
    high_24h: float | None
    low_24h: float | None
    volume_24h: float | None
    source_timestamp_ms: int | None


@dataclass
class CryptoTimeframeRead:
    timeframe: str
    error: str | None
    bar_date: str | None
    close: float | None
    ma_stack_state: str | None
    stoch_k: float | None
    stoch_d: float | None
    stoch_zone: str | None
    stoch_signal: str | None
    sqn_regime: str | None
    sqn_value: float | None


@dataclass
class CryptoSetup:
    symbol: str
    scan_time_utc: str
    ticker: CryptoTicker | None
    reads: dict[str, CryptoTimeframeRead] = field(default_factory=dict)
    confluence: Confluence = "skip_no_setup"
    direction: Direction = "none"
    why_now: str = ""
    blockers: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "symbol": self.symbol,
            "scan_time_utc": self.scan_time_utc,
            "ticker": asdict(self.ticker) if self.ticker else None,
            "reads": {tf: asdict(r) for tf, r in self.reads.items()},
            "confluence": self.confluence,
            "direction": self.direction,
            "why_now": self.why_now,
            "blockers": list(self.blockers),
            "notes": list(self.notes),
        }


# ─────────────────────────────────────────────────────────────────────────
# TF read building
# ─────────────────────────────────────────────────────────────────────────


def _build_read(timeframe: str, scan_row: dict[str, Any]) -> CryptoTimeframeRead:
    ma = scan_row.get("ma_ribbon") or {}
    stoch = scan_row.get("stochastic") or {}
    sqn = scan_row.get("sqn") or {}
    return CryptoTimeframeRead(
        timeframe=timeframe,
        error=None,
        bar_date=scan_row.get("bar_date"),
        close=scan_row.get("close"),
        ma_stack_state=ma.get("stack_state"),
        stoch_k=stoch.get("k"),
        stoch_d=stoch.get("d"),
        stoch_zone=stoch.get("zone"),
        stoch_signal=stoch.get("signal"),
        sqn_regime=sqn.get("regime"),
        sqn_value=sqn.get("sqn_value"),
    )


def _error_read(timeframe: str, message: str) -> CryptoTimeframeRead:
    return CryptoTimeframeRead(
        timeframe=timeframe, error=message,
        bar_date=None, close=None,
        ma_stack_state=None, stoch_k=None, stoch_d=None,
        stoch_zone=None, stoch_signal=None,
        sqn_regime=None, sqn_value=None,
    )


# ─────────────────────────────────────────────────────────────────────────
# Confluence classification — trading-edge cross-TF matrix
# ─────────────────────────────────────────────────────────────────────────


_BULL_STACKS = {"full_bull", "bull_developing"}
_BEAR_STACKS = {"full_bear", "bear_developing"}
_BULL_SIGNALS = {"bull_cross_oversold", "bull_continuation", "bullish_divergence"}
_BEAR_SIGNALS = {"bear_cross_overbought", "bear_continuation", "bearish_divergence"}


def _stack_bias(state: str | None) -> Direction:
    if state in _BULL_STACKS:
        return "long"
    if state in _BEAR_STACKS:
        return "short"
    return "none"


def _stoch_cross(direction: Direction, signal: str | None,
                 k: float | None, d: float | None) -> bool:
    """Did the Stoch fire a cross aligned with `direction`?"""
    if signal:
        if direction == "long" and signal in _BULL_SIGNALS:
            return True
        if direction == "short" and signal in _BEAR_SIGNALS:
            return True
    # Fallback to %K vs %D (same-bar approximation)
    if k is None or d is None:
        return False
    if direction == "long":
        return k > d
    if direction == "short":
        return k < d
    return False


def classify_crypto_confluence(
    weekly: CryptoTimeframeRead,
    daily: CryptoTimeframeRead,
    four_h: CryptoTimeframeRead,
    two_h: CryptoTimeframeRead,
) -> tuple[Confluence, Direction, str, list[str]]:
    """Apply the trading-edge cross-TF matrix.

    Returns (confluence, direction, why_now, blockers).

    Hard rule: Daily chop / no MA stack → skip, regardless of other TFs.
    """
    blockers: list[str] = []

    # Daily is the direction filter — its state is load-bearing
    daily_state = (daily.ma_stack_state or "").lower()
    if daily_state in ("chop", "tangled") or daily.error:
        return ("skip_chop", "none",
                "Daily MA chop / unavailable — no trade (hard rule)",
                ["Daily MA stack unusable"] if daily.error else
                ["Daily MA chop — no trend, no edge"])

    daily_bias = _stack_bias(daily.ma_stack_state)
    if daily_bias == "none":
        return ("skip_no_setup", "none",
                f"Daily stack {daily.ma_stack_state or '?'} — wait for clarity",
                blockers)

    # Trigger TF — 2H Stoch cross primary, 4H Stoch fallback
    trigger_fired = _stoch_cross(daily_bias, two_h.stoch_signal, two_h.stoch_k, two_h.stoch_d)
    trigger_tf = "2H"
    if not trigger_fired and two_h.error and not four_h.error:
        trigger_fired = _stoch_cross(daily_bias, four_h.stoch_signal,
                                     four_h.stoch_k, four_h.stoch_d)
        trigger_tf = "4H (2H unusable)"
        blockers.append("2H read unusable — using 4H Stoch as fallback")

    # Weekly context
    weekly_bias = _stack_bias(weekly.ma_stack_state)
    weekly_aligned = (weekly_bias == daily_bias) or weekly_bias == "none"
    weekly_opposes = (weekly_bias != "none") and (weekly_bias != daily_bias)

    # 4H context (when both 4H and 2H usable, 4H is the swing-context check)
    four_h_bias = _stack_bias(four_h.ma_stack_state)
    four_h_state = (four_h.ma_stack_state or "").lower()
    four_h_aligned = (four_h_bias == daily_bias) or four_h_bias == "none"
    four_h_opposes = (four_h_bias != "none") and (four_h_bias != daily_bias)
    four_h_chop = four_h_state in ("chop", "tangled", "compression")

    # Build why_now first — used in early returns
    bias_label = daily_bias.upper()
    why_parts = [
        f"{bias_label} bias",
        f"Daily {daily.ma_stack_state}",
    ]
    if trigger_fired:
        why_parts.append(f"{trigger_tf} Stoch cross")
    else:
        why_parts.append(f"{trigger_tf} no cross yet")
    if weekly.ma_stack_state:
        why_parts.append(f"Weekly {weekly.ma_stack_state}")
    why_now = " · ".join(why_parts)

    # Matrix application

    # Daily full stack + opposing 4H = mean-reversion trap → skip
    if daily.ma_stack_state == "full_bull" and four_h_opposes and trigger_fired:
        return ("skip_no_setup", daily_bias,
                why_now,
                ["4H stack opposes Daily — likely mean-reversion trap, skip"])
    if daily.ma_stack_state == "full_bear" and four_h_opposes and trigger_fired:
        return ("skip_no_setup", daily_bias,
                why_now,
                ["4H stack opposes Daily — likely mean-reversion trap, skip"])

    # Counter-Weekly (Weekly opposes Daily): half-size flag
    if weekly_opposes and trigger_fired:
        return ("counter_weekly", daily_bias,
                why_now,
                blockers + [
                    f"Weekly {weekly.ma_stack_state} opposes Daily {daily.ma_stack_state} — "
                    "half size, no LEAPS/long holds, target Daily structure only",
                ])

    # No trigger yet → wait
    if not trigger_fired:
        return ("wait", daily_bias,
                why_now,
                blockers + ["Trigger TF Stoch hasn't fired — set alert"])

    # 4H chop with Daily full stack → require 4H clarification
    if daily.ma_stack_state in ("full_bull", "full_bear") and four_h_chop:
        return ("wait", daily_bias,
                why_now,
                blockers + [
                    f"4H is {four_h.ma_stack_state} — wait for 4H MA clarification "
                    "(2 closes) before entry"
                ])

    # Aligned + full stack + trigger fired → high conviction
    if (daily.ma_stack_state in ("full_bull", "full_bear")
        and weekly_aligned and four_h_aligned):
        confluence: Confluence = (
            "high_conviction_long" if daily_bias == "long" else "high_conviction_short"
        )
        return (confluence, daily_bias, why_now, blockers)

    # Developing stack with alignment → medium conviction
    if (daily.ma_stack_state in ("bull_developing", "bear_developing")
        and weekly_aligned and four_h_aligned):
        confluence = (
            "medium_conviction_long" if daily_bias == "long" else "medium_conviction_short"
        )
        return (confluence, daily_bias, why_now,
                blockers + ["Developing stack — enter 1-2% size, not full conviction"])

    # Default: directional but unclassified — call it medium
    confluence = (
        "medium_conviction_long" if daily_bias == "long" else "medium_conviction_short"
    )
    return (confluence, daily_bias, why_now, blockers)


# ─────────────────────────────────────────────────────────────────────────
# Top-level scan
# ─────────────────────────────────────────────────────────────────────────


def scan_crypto_setup(
    symbol: str,
    *,
    scan_fn: Callable[[str, str], dict[str, Any]] | None = None,
    ticker_fn: Callable[[str], dict[str, Any]] | None = None,
) -> CryptoSetup:
    """Run multi-TF crypto setup scan.

    `scan_fn(symbol, timeframe)` defaults to scan_ticker from src/scan.py
    `ticker_fn(symbol)` defaults to fetch_ticker from data.crypto_loader
    Both are injected for tests so we don't hit the live API.
    """
    if scan_fn is None:
        from scan import scan_ticker
        def scan_fn(s: str, tf: str) -> dict[str, Any]:
            return scan_ticker(s, timeframe=tf)
    if ticker_fn is None:
        from data.crypto_loader import fetch_ticker
        ticker_fn = fetch_ticker

    notes: list[str] = []

    # Live ticker data
    ticker: CryptoTicker | None = None
    try:
        td = ticker_fn(symbol)
        ticker = CryptoTicker(
            instrument_name=td.get("instrument_name") or symbol,
            last_price=td.get("last_price"),
            bid=td.get("bid"),
            ask=td.get("ask"),
            change_24h_pct=td.get("change_24h_pct"),
            high_24h=td.get("high_24h"),
            low_24h=td.get("low_24h"),
            volume_24h=td.get("volume_24h"),
            source_timestamp_ms=td.get("source_timestamp_ms"),
        )
    except Exception as exc:
        notes.append(f"Ticker fetch failed: {exc}")

    # Multi-TF reads
    reads: dict[str, CryptoTimeframeRead] = {}
    for tf in CRYPTO_TIMEFRAMES:
        try:
            row = scan_fn(symbol, tf)
            reads[tf] = _build_read(tf, row)
        except Exception as exc:
            reads[tf] = _error_read(tf, str(exc))

    confluence, direction, why_now, blockers = classify_crypto_confluence(
        reads["1wk"], reads["1d"], reads["4h"], reads["2h"],
    )

    return CryptoSetup(
        symbol=symbol.upper(),
        scan_time_utc=datetime.now(timezone.utc).isoformat(),
        ticker=ticker,
        reads=reads,
        confluence=confluence,
        direction=direction,
        why_now=why_now,
        blockers=blockers,
        notes=notes,
    )


# Common pairs for the dashboard's quick-pick buttons. Curated to the most
# liquid USDT pairs the user is most likely to scan. Frontend can override
# via the full /instruments listing.
COMMON_PAIRS: tuple[str, ...] = (
    "BTC_USDT",
    "ETH_USDT",
    "SOL_USDT",
    "AVAX_USDT",
    "MATIC_USDT",
    "LINK_USDT",
    "DOT_USDT",
    "ADA_USDT",
)
