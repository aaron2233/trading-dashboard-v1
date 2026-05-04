"""Weekly-trend scanner — Sunday-scan workflow over a watchlist on the weekly TF.

Implements steps 1-3 of the workflow in
`~/.claude/skills/user/weekly-trend-trader/SKILL.md`:

    1. Regime read on benchmark (default SPY)
    2. Scan each watchlist ticker on the weekly chart — MA stack, Stoch cross, regime
    3. Rank setups: regime alignment > Stoch location > MA stack clarity

Output is per-ticker `WeeklySetup` snapshots tagged with a confluence rating.
The frontend renders these for the user; pre-writing kill sheets (step 4) and
setting alerts (step 5) are user actions launched from the snapshot rows.

Penny stocks (close < $5) get `vehicle="shares"` per the skill's account
constraint — sub-$5 names have illiquid option chains.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable, Literal


PENNY_STOCK_THRESHOLD: float = 5.0

# Confluence states — each maps to the skill's named entry quality
Confluence = Literal[
    "high_conviction_long",     # full bull stack + Stoch %K cross above %D from <30
    "high_conviction_short",    # full bear stack + Stoch %K cross below %D from >70
    "continuation_long",        # full bull stack + Stoch reset 40-60 turning up
    "continuation_short",       # full bear stack + Stoch reset 40-60 turning down
    "compression",              # MAs converging — pending breakout, wait
    "chop",                     # MAs tangled — no trade, ever
    "no_setup",                 # bias unclear, no actionable signal
]

Direction = Literal["long", "short", "none"]
Vehicle = Literal["shares", "options"]


@dataclass
class WeeklySetup:
    """One ticker's weekly-TF read + confluence rating."""

    ticker: str
    bar_date: str | None
    close: float | None
    is_penny_stock: bool
    suggested_vehicle: Vehicle    # "shares" if penny stock, "options" otherwise
    ma_stack_state: str | None    # full_bull / bull_developing / compression / chop / etc.
    stoch_k: float | None
    stoch_d: float | None
    stoch_zone: str | None
    stoch_signal: str | None
    sqn_100_regime: str | None    # benchmark regime (e.g. SPY)
    confluence: Confluence
    direction: Direction
    rank_score: int               # higher = better; used for top-N ordering
    why_now: str                  # one-line summary for the snapshot card
    blockers: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class WeeklyScanResult:
    scan_time_utc: str
    benchmark: str
    benchmark_regime: str | None
    setups: list[WeeklySetup] = field(default_factory=list)
    top_setups: list[WeeklySetup] = field(default_factory=list)  # top 3 by rank
    errors: dict[str, str] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "scan_time_utc": self.scan_time_utc,
            "benchmark": self.benchmark,
            "benchmark_regime": self.benchmark_regime,
            "setups": [s.to_dict() for s in self.setups],
            "top_setups": [s.to_dict() for s in self.top_setups],
            "errors": dict(self.errors),
        }


# ─────────────────────────────────────────────────────────────────────────
# Confluence classification
# ─────────────────────────────────────────────────────────────────────────


def _is_high_conviction_long_signal(stoch_k: float | None, stoch_d: float | None,
                                    stoch_signal: str | None) -> bool:
    """Bullish reversal: Stoch %K cross above %D from <30 (oversold zone)."""
    if stoch_signal == "bull_cross_oversold":
        return True
    if stoch_k is None or stoch_d is None:
        return False
    return stoch_k > stoch_d and stoch_d < 30 and stoch_k < 40


def _is_high_conviction_short_signal(stoch_k: float | None, stoch_d: float | None,
                                     stoch_signal: str | None) -> bool:
    """Bearish reversal: Stoch %K cross below %D from >70 (overbought zone)."""
    if stoch_signal == "bear_cross_overbought":
        return True
    if stoch_k is None or stoch_d is None:
        return False
    return stoch_k < stoch_d and stoch_d > 70 and stoch_k > 60


def _is_continuation_long(stoch_k: float | None, stoch_d: float | None,
                          stoch_signal: str | None) -> bool:
    """Stoch reset 40-60 turning back up — momentum continuation in trend."""
    if stoch_signal == "bull_continuation":
        return True
    if stoch_k is None or stoch_d is None:
        return False
    return stoch_k > stoch_d and 40 <= stoch_k <= 70


def _is_continuation_short(stoch_k: float | None, stoch_d: float | None,
                           stoch_signal: str | None) -> bool:
    if stoch_signal == "bear_continuation":
        return True
    if stoch_k is None or stoch_d is None:
        return False
    return stoch_k < stoch_d and 30 <= stoch_k <= 60


def classify_confluence(
    ma_stack_state: str | None,
    stoch_k: float | None,
    stoch_d: float | None,
    stoch_signal: str | None,
    sqn_regime: str | None,
) -> tuple[Confluence, Direction, list[str]]:
    """Return (confluence, direction, blockers) for one ticker's weekly read.

    Skill rules:
    - chop/tangled MAs → no trade, ever
    - compression → pending breakout, wait (no direction yet)
    - high-conviction = full stack + Stoch reversal cross from extreme
    - continuation = full stack + Stoch mid-zone in trend direction
    """
    blockers: list[str] = []

    if ma_stack_state is None:
        return "no_setup", "none", ["MA stack state unknown"]

    state = ma_stack_state.lower()

    if state in ("chop", "tangled"):
        return "chop", "none", ["MA tangle — no trend, no trade"]

    if state == "compression":
        return "compression", "none", ["Compression — pending breakout, wait for direction"]

    # Bullish patterns
    if state in ("full_bull", "bull_developing"):
        # Counter-trend regime is a blocker — flag but still classify
        if sqn_regime in ("strong_bear", "bear"):
            blockers.append(
                f"SQN(100) {sqn_regime} opposes long bias — counter-trend, requires thesis"
            )
        if _is_high_conviction_long_signal(stoch_k, stoch_d, stoch_signal):
            return "high_conviction_long", "long", blockers
        if _is_continuation_long(stoch_k, stoch_d, stoch_signal):
            return "continuation_long", "long", blockers
        return "no_setup", "long", blockers + ["Bullish stack but no Stoch trigger"]

    # Bearish patterns
    if state in ("full_bear", "bear_developing"):
        if sqn_regime in ("strong_bull", "bull"):
            blockers.append(
                f"SQN(100) {sqn_regime} opposes short bias — counter-trend, requires thesis"
            )
        if _is_high_conviction_short_signal(stoch_k, stoch_d, stoch_signal):
            return "high_conviction_short", "short", blockers
        if _is_continuation_short(stoch_k, stoch_d, stoch_signal):
            return "continuation_short", "short", blockers
        return "no_setup", "short", blockers + ["Bearish stack but no Stoch trigger"]

    return "no_setup", "none", [f"Unknown stack state: {state}"]


# ─────────────────────────────────────────────────────────────────────────
# Ranking
# ─────────────────────────────────────────────────────────────────────────


# Skill ranking: regime alignment > Stoch location > MA clarity
_CONFLUENCE_BASE_SCORE: dict[Confluence, int] = {
    "high_conviction_long":  70,
    "high_conviction_short": 70,
    "continuation_long":     50,
    "continuation_short":    50,
    "compression":           20,
    "no_setup":              10,
    "chop":                   0,
}


def _regime_alignment_bonus(direction: Direction, regime: str | None) -> int:
    """+30 with-trend, -20 counter-trend, 0 neutral."""
    if regime is None or direction == "none":
        return 0
    bull = regime in ("bull", "strong_bull")
    bear = regime in ("bear", "strong_bear")
    if direction == "long" and bull:
        return 30
    if direction == "short" and bear:
        return 30
    if direction == "long" and bear:
        return -20
    if direction == "short" and bull:
        return -20
    return 0


def _ma_clarity_bonus(stack_state: str | None) -> int:
    """+10 full stack, +5 developing, 0 elsewhere."""
    if not stack_state:
        return 0
    if stack_state in ("full_bull", "full_bear"):
        return 10
    if stack_state in ("bull_developing", "bear_developing"):
        return 5
    return 0


def _rank_score(setup: WeeklySetup) -> int:
    base = _CONFLUENCE_BASE_SCORE.get(setup.confluence, 0)
    regime_bonus = _regime_alignment_bonus(setup.direction, setup.sqn_100_regime)
    clarity = _ma_clarity_bonus(setup.ma_stack_state)
    return base + regime_bonus + clarity


# ─────────────────────────────────────────────────────────────────────────
# Why-now copy
# ─────────────────────────────────────────────────────────────────────────


def _why_now(confluence: Confluence, stack: str | None,
             stoch_signal: str | None, regime: str | None) -> str:
    if confluence == "chop":
        return "Chop — sit out"
    if confluence == "compression":
        return "MAs compressing — pending breakout, set alerts and wait"
    if confluence == "no_setup":
        return f"{stack or '?'} stack, no Stoch trigger yet"
    parts = [confluence.replace("_", " ").upper()]
    if stoch_signal:
        parts.append(stoch_signal.replace("_", " "))
    parts.append(f"{stack or '?'} stack")
    if regime:
        parts.append(f"SQN(100) {regime}")
    return " · ".join(parts)


# ─────────────────────────────────────────────────────────────────────────
# Top-level scan
# ─────────────────────────────────────────────────────────────────────────


def scan_weekly_watchlist(
    tickers: list[str],
    *,
    benchmark: str = "SPY",
    scan_fn: Callable[[str, str], dict[str, Any]] | None = None,
    top_n: int = 3,
) -> WeeklyScanResult:
    """Run weekly-TF scan across a watchlist + benchmark regime read.

    `scan_fn` is `scan_ticker(ticker, timeframe)` from src/scan.py — injected
    for tests so we don't hit yfinance. Production callers leave it None.
    Returns per-ticker setups + top-N ranked.
    """
    if scan_fn is None:
        from scan import scan_ticker
        # Adapter so callers can pass a clean (ticker, timeframe) signature
        def scan_fn(ticker: str, timeframe: str) -> dict[str, Any]:
            return scan_ticker(ticker, timeframe=timeframe)

    errors: dict[str, str] = {}

    # Benchmark regime — comes from the SQN(100) on the benchmark on its own
    # daily read (per skill, regime is broad-market, not weekly chart of the
    # ticker itself). scan_ticker default timeframe is "1d".
    benchmark_regime: str | None = None
    try:
        bench_row = scan_fn(benchmark.upper(), "1d")
        benchmark_regime = (bench_row.get("sqn") or {}).get("regime")
    except Exception as exc:
        errors[benchmark.upper()] = f"benchmark regime read failed: {exc}"

    setups: list[WeeklySetup] = []
    seen: set[str] = set()
    for raw in tickers:
        ticker = raw.strip().upper()
        if not ticker or ticker in seen:
            continue
        seen.add(ticker)
        try:
            row = scan_fn(ticker, "1wk")
        except Exception as exc:
            errors[ticker] = str(exc)
            continue

        ma = row.get("ma_ribbon") or {}
        stoch = row.get("stochastic") or {}
        stack = ma.get("stack_state")
        k = stoch.get("k")
        d = stoch.get("d")
        signal = stoch.get("signal")
        zone = stoch.get("zone")
        close = row.get("close")

        confluence, direction, blockers = classify_confluence(
            stack, k, d, signal, benchmark_regime,
        )
        is_penny = close is not None and close < PENNY_STOCK_THRESHOLD
        vehicle: Vehicle = "shares" if is_penny else "options"

        setup = WeeklySetup(
            ticker=ticker,
            bar_date=row.get("bar_date"),
            close=close,
            is_penny_stock=is_penny,
            suggested_vehicle=vehicle,
            ma_stack_state=stack,
            stoch_k=k, stoch_d=d, stoch_zone=zone, stoch_signal=signal,
            sqn_100_regime=benchmark_regime,
            confluence=confluence,
            direction=direction,
            rank_score=0,  # filled below
            why_now=_why_now(confluence, stack, signal, benchmark_regime),
            blockers=blockers,
        )
        setup.rank_score = _rank_score(setup)
        setups.append(setup)

    # Rank: highest score first, ties broken by ticker for determinism
    setups.sort(key=lambda s: (-s.rank_score, s.ticker))
    top = [s for s in setups if s.confluence not in ("chop", "compression", "no_setup")][:top_n]

    return WeeklyScanResult(
        scan_time_utc=datetime.now(timezone.utc).isoformat(),
        benchmark=benchmark.upper(),
        benchmark_regime=benchmark_regime,
        setups=setups,
        top_setups=top,
        errors=errors,
    )
