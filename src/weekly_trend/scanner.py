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

# Track A (19/39 weekly cross) blocked tickers per backtest 2026-05-09.
# These had net-negative avg R on the 19/39 cross signal in 2014-2026 data.
# Source: ~/.claude/skills/user/weekly-trend-trader/references/19-39-cross-backtest.md
TRACK_A_BLOCKED_TICKERS: frozenset[str] = frozenset({
    "QQQ", "GLD", "SPY", "AMZN", "NFLX", "AMD", "TSLA"
})

# Confluence states — each maps to the skill's named entry quality
Confluence = Literal[
    "high_conviction_long",     # Track B: full bull stack + Stoch %K cross above %D from <30
    "high_conviction_short",    # Track B: full bear stack + Stoch %K cross below %D from >70
    "continuation_long",        # Track B: full bull stack + Stoch reset 40-60 turning up
    "continuation_short",       # Track B: full bear stack + Stoch reset 40-60 turning down
    "track_a_cross_long",       # Track A: 19/39 weekly bullish cross (early entry)
    "track_a_cross_short",      # Track A: 19/39 weekly bearish cross (early entry)
    "compression",              # MAs converging — pending breakout, wait
    "chop",                     # MAs tangled — no trade, ever
    "no_setup",                 # bias unclear, no actionable signal
]

Direction = Literal["long", "short", "none"]
Vehicle = Literal["shares", "options"]


@dataclass
class TrackASignal:
    """19/39 weekly MA cross state for a ticker.

    A `cross_up` fires the bar where 19WMA crosses above 39WMA on weekly close.
    `above` / `below` is the steady-state read (no fresh cross this week).
    `none` = insufficient data.
    """
    state: Literal["cross_up", "cross_down", "above", "below", "none"]
    ma_19: float | None
    ma_39: float | None
    asset_blocked: bool   # ticker is in TRACK_A_BLOCKED_TICKERS

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


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
    # Weekly-trend action verdict — populated for setups with a directional
    # bias (long or short). None for chop/compression/no_setup confluence.
    action_verdict: dict[str, Any] | None = None
    # Track A (19/39 weekly cross) signal. Populated when raw weekly bars are
    # available. None when bars_fn is unavailable or returned <40 bars.
    track_a: TrackASignal | None = None
    # ─── Unified verdict + entry/stop fields (shared across all scans) ───
    verdict: str = "wait"          # buy | wait | no_go
    verdict_reason: str = ""
    entry_price: float | None = None
    stop_price: float | None = None
    target_price: float | None = None
    suggested_dte: str | None = None     # "120-180 DTE" / "365+ DTE LEAPS" etc.
    suggested_delta: str | None = None   # "0.50-0.65" / "0.75-0.90" etc.
    # Concrete dollar strike — BS-derived from spot + HV at the midpoint
    # delta target for the current track (Track A LEAPS: 0.825; Track B
    # 120-180 DTE: 0.575). None when scan didn't provide HV.
    suggested_strike: float | None = None
    # When the scan was driven by a universe sweep (nasdaq_100 / sp500_top_50
    # / russell_2000_top_50), this records which universe surfaced the
    # ticker. None for explicit ticker lists or the legacy per-ticker scan.
    # Mirrors LottoSetup.source_universe so the UI can group by index.
    source_universe: str | None = None

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
# Track A — 19/39 weekly MA cross detection
# ─────────────────────────────────────────────────────────────────────────


def detect_track_a_signal(weekly_bars: Any, ticker: str) -> TrackASignal:
    """Detect 19/39 weekly MA cross from a weekly DataFrame with `close`.

    Returns a TrackASignal with state ∈ {cross_up, cross_down, above, below, none}.
    Pure-pandas; no I/O. Caller passes weekly bars (e.g., from yfinance with
    interval="1wk") and ticker for asset-blocked classification.
    """
    asset_blocked = ticker.upper() in TRACK_A_BLOCKED_TICKERS

    try:
        import pandas as pd  # noqa: F401 — type guard
        if weekly_bars is None or len(weekly_bars) < 40:
            return TrackASignal(state="none", ma_19=None, ma_39=None,
                                asset_blocked=asset_blocked)
        closes = weekly_bars["close"]
        ma_19_series = closes.rolling(19).mean()
        ma_39_series = closes.rolling(39).mean()
        if ma_19_series.iloc[-1] is None or ma_39_series.iloc[-1] is None:
            return TrackASignal(state="none", ma_19=None, ma_39=None,
                                asset_blocked=asset_blocked)
        ma_19_now = float(ma_19_series.iloc[-1])
        ma_39_now = float(ma_39_series.iloc[-1])
        ma_19_prev = float(ma_19_series.iloc[-2])
        ma_39_prev = float(ma_39_series.iloc[-2])

        if ma_19_prev <= ma_39_prev and ma_19_now > ma_39_now:
            state: Any = "cross_up"
        elif ma_19_prev >= ma_39_prev and ma_19_now < ma_39_now:
            state = "cross_down"
        elif ma_19_now > ma_39_now:
            state = "above"
        elif ma_19_now < ma_39_now:
            state = "below"
        else:
            state = "none"
        return TrackASignal(
            state=state, ma_19=ma_19_now, ma_39=ma_39_now,
            asset_blocked=asset_blocked,
        )
    except Exception:
        return TrackASignal(state="none", ma_19=None, ma_39=None,
                            asset_blocked=asset_blocked)


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
    "track_a_cross_long":    60,  # early-entry; ranks below high-conviction Track B
    "track_a_cross_short":   60,
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
             stoch_signal: str | None, regime: str | None,
             track_a: TrackASignal | None = None) -> str:
    if confluence == "chop":
        return "Chop — sit out"
    if confluence == "compression":
        return "MAs compressing — pending breakout, set alerts and wait"
    if confluence == "no_setup":
        return f"{stack or '?'} stack, no Stoch trigger yet"
    if confluence in ("track_a_cross_long", "track_a_cross_short"):
        direction_word = "BULL" if confluence == "track_a_cross_long" else "BEAR"
        parts = [f"TRACK A {direction_word} CROSS (19/39 weekly)"]
        if track_a and track_a.ma_19 is not None and track_a.ma_39 is not None:
            parts.append(f"19WMA ${track_a.ma_19:.2f} · 39WMA ${track_a.ma_39:.2f}")
        if track_a and track_a.asset_blocked:
            parts.append("⚠ asset blocked for Track A — use Track B instead")
        if regime:
            parts.append(f"SQN(100) {regime}")
        return " · ".join(parts)
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
    tickers: list[str] | None = None,
    *,
    benchmark: str = "SPY",
    scan_fn: Callable[[str, str], dict[str, Any]] | None = None,
    bars_fn: Callable[[str], Any] | None = None,
    top_n: int = 3,
    universe: list[str] | None = None,
) -> WeeklyScanResult:
    """Run weekly-TF scan across a watchlist + benchmark regime read.

    Two modes:
      - Explicit `tickers` (per-ticker scan): scans exactly those names.
        Each setup's `source_universe` is None.
      - `universe` (bulk universe scan): resolves tickers via
        `free_range.universe.free_range_universe(name)` for each universe
        name (e.g., "nasdaq_100", "sp500_top_50", "russell_2000_top_50").
        Each setup is tagged with the index it came from so the UI can
        group results. First universe a ticker appears in "owns" it.
      - When both are provided, `tickers` wins (explicit beats universe).
      - When neither is provided, raises ValueError.

    `scan_fn` is `scan_ticker(ticker, timeframe)` from src/scan.py — injected
    for tests so we don't hit yfinance. Production callers leave it None.
    `bars_fn` is an optional weekly-bars loader for Track A 19/39 detection.
    When None and not in test mode, defaults to load_bars with interval="1wk".
    Returns per-ticker setups + top-N ranked.
    """
    if scan_fn is None:
        from scan import scan_ticker
        # Adapter so callers can pass a clean (ticker, timeframe) signature
        def scan_fn(ticker: str, timeframe: str) -> dict[str, Any]:
            return scan_ticker(ticker, timeframe=timeframe)

    if bars_fn is None:
        try:
            from data.yfinance_loader import load_bars

            def bars_fn(ticker: str) -> Any:
                # 5y of weekly bars covers 39-bar warmup with margin
                return load_bars(ticker, period="5y", interval="1wk")
        except Exception:
            # Tests / restricted environments may not have yfinance; Track A
            # detection becomes a no-op instead of crashing the scan.
            bars_fn = None

    errors: dict[str, str] = {}

    # Resolve scan target. Explicit `tickers` wins; then `universe`; refuse
    # to scan with neither (callers must opt into one or the other so we
    # never silently scan an empty list and return nothing).
    ticker_universe: dict[str, str | None] = {}
    if tickers:
        tickers_to_scan = [t.strip().upper() for t in tickers if t and t.strip()]
        for t in tickers_to_scan:
            ticker_universe[t] = None
    elif universe:
        from free_range.universe import free_range_universe
        seen_u: set[str] = set()
        tickers_to_scan = []
        for uni_name in universe:
            try:
                for t in free_range_universe(universe=uni_name):
                    t_upper = t.upper()
                    if t_upper in seen_u:
                        continue
                    seen_u.add(t_upper)
                    tickers_to_scan.append(t_upper)
                    # First universe a ticker appears in "owns" it for grouping.
                    ticker_universe[t_upper] = uni_name
            except Exception as exc:
                errors[f"_universe_{uni_name}"] = f"universe resolve failed: {exc}"
    else:
        raise ValueError(
            "scan_weekly_watchlist requires either `tickers` or `universe`"
        )

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
    for raw in tickers_to_scan:
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

        # Track A: 19/39 weekly cross detection. Only attempted if a weekly
        # bars_fn is available; failures are silent (Track A is additive).
        track_a: TrackASignal | None = None
        if bars_fn is not None:
            try:
                weekly_bars = bars_fn(ticker)
                track_a = detect_track_a_signal(weekly_bars, ticker)
            except Exception:
                track_a = None

        # If Track A fires a FRESH cross AND classify_confluence didn't already
        # produce a high-conviction signal, prefer the Track A early-entry
        # confluence so the user sees it surface in the scan output. Existing
        # high_conviction_* / continuation_* take precedence (those are richer
        # Track B signals). chop / compression remain blockers regardless.
        if track_a is not None and confluence in ("no_setup", "compression"):
            if track_a.state == "cross_up":
                confluence = "track_a_cross_long"
                direction = "long"
                if track_a.asset_blocked:
                    blockers = blockers + [
                        f"{ticker} is on the Track A blocked list — switch to Track B "
                        f"(10/20/50/200 ribbon) or skip"
                    ]
            elif track_a.state == "cross_down":
                confluence = "track_a_cross_short"
                direction = "short"
                if track_a.asset_blocked:
                    blockers = blockers + [
                        f"{ticker} is on the Track A blocked list — switch to Track B "
                        f"(10/20/50/200 ribbon) or skip"
                    ]

        is_penny = close is not None and close < PENNY_STOCK_THRESHOLD
        vehicle: Vehicle = "shares" if is_penny else "options"

        # Compute unified verdict + entry/stop/target.
        # Pass Stoch signal so continuation_* requires a fresh momentum
        # trigger (not just stack-aligned state). Pass Track A separation
        # so razor-thin crosses get downgraded to WAIT. Pass weekly-bar
        # color so red-candle reversals don't get classed as BUY.
        from scan_verdict import weekly_verdict
        track_a_sep_pct: float | None = None
        track_a_stretch: float | None = None
        if (
            track_a is not None
            and track_a.ma_19 is not None
            and track_a.ma_39 is not None
            and close is not None
            and close > 0
        ):
            track_a_sep_pct = abs(track_a.ma_19 - track_a.ma_39) / close * 100.0
            # Stretch: how far the close is from the 19WMA stop anchor.
            # Signed (+ = close above 19WMA, − = below).
            if track_a.ma_19 > 0:
                track_a_stretch = (close - track_a.ma_19) / track_a.ma_19 * 100.0
        open_px = row.get("open")
        bar_is_bullish: bool | None = None
        if open_px is not None and close is not None:
            try:
                bar_is_bullish = float(close) > float(open_px)
            except (TypeError, ValueError):
                bar_is_bullish = None
        verdict_obj = weekly_verdict(
            confluence, direction, benchmark_regime, blockers,
            stoch_signal=signal,
            track_a_separation_pct=track_a_sep_pct,
            bar_is_bullish=bar_is_bullish,
            track_a_stretch_pct=track_a_stretch,
        )

        # Weekly-trend asset gates (backtest 2026-05-07): IWM is hard-blocked
        # for this skill, SPY is marginal (warn-only). Previously enforced
        # only at kill-sheet time (and only when the caller tagged
        # skill="weekly-trend-trader") — the scan card itself could show BUY
        # on IWM. Mirror the gate at scan time; constants live in
        # kill_sheet.builder so forward-data revisions edit one place.
        from kill_sheet.builder import (
            WEEKLY_TREND_BLOCKED_TICKERS,
            WEEKLY_TREND_MARGINAL_TICKERS,
        )
        from scan_verdict import TradeVerdict
        ticker_upper = ticker.upper()
        if ticker_upper in WEEKLY_TREND_BLOCKED_TICKERS:
            blockers = blockers + [
                f"{ticker_upper} is on the weekly-trend blocked list "
                f"(backtest net-negative for this skill) — no entry"
            ]
            if verdict_obj.verdict == "buy":
                verdict_obj = TradeVerdict(
                    "no_go",
                    f"{ticker_upper} is blocked for weekly-trend "
                    f"(backtest 2026-05-07: net-negative) — no entry",
                )
        elif ticker_upper in WEEKLY_TREND_MARGINAL_TICKERS:
            blockers = blockers + [
                f"{ticker_upper} is marginal for weekly-trend (backtest) — "
                f"reduced conviction, prefer QQQ/GLD"
            ]

        # Entry / stop / target derivation
        ma_50 = float(ma.get("ma_50") or 0.0) or None
        ma_20 = float(ma.get("ma_20") or 0.0) or None
        entry_p: float | None = close if direction in ("long", "short") else None
        stop_p: float | None = None
        target_p: float | None = None
        suggested_dte: str | None = None
        suggested_delta: str | None = None

        suggested_strike: float | None = None
        if direction in ("long", "short"):
            # Track A entry uses 19WMA stop; Track B uses 50WMA stop.
            if confluence in ("track_a_cross_long", "track_a_cross_short") and track_a:
                stop_p = track_a.ma_19
                suggested_dte = "365+ DTE LEAPS"
                suggested_delta = "0.75-0.90 (deep ITM)"
                target_delta_val = 0.825  # mid of 0.75-0.90
                dte_days = 400
            else:
                stop_p = ma_50  # Track B 50WMA stop
                suggested_dte = "120-180 DTE"
                suggested_delta = "0.50-0.65 (ATM/slight ITM)"
                target_delta_val = 0.575  # mid of 0.50-0.65
                dte_days = 150  # mid of 120-180
            # Target: next major MA overhead (longs) or below (shorts) — use 200WMA
            # as a coarse default. Real target placement is per-trade discretion.
            ma_200 = float(ma.get("ma_200") or 0.0) or None
            if direction == "long" and ma_200 and entry_p and ma_200 > entry_p:
                target_p = ma_200
            elif direction == "short" and ma_200 and entry_p and ma_200 < entry_p:
                target_p = ma_200

            # Concrete strike suggestion. weekly scans use weekly bars for
            # row data, so row.get("hv20") may be None (HV is only computed
            # for daily bars in scan.py). Pull daily HV separately for this.
            hv = row.get("hv20")
            if hv is None:
                try:
                    daily_row = scan_fn(ticker, "1d")
                    hv = daily_row.get("hv20")
                except Exception:
                    hv = None
            if close is not None and hv:
                from lotto.strikes import suggest_strike_for_delta
                suggested_strike = suggest_strike_for_delta(
                    spot=float(close), hv_annual=float(hv),
                    dte_days=dte_days,
                    kind="call" if direction == "long" else "put",
                    target_delta=target_delta_val,
                    ticker=ticker,
                )

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
            why_now=_why_now(confluence, stack, signal, benchmark_regime, track_a),
            blockers=blockers,
            track_a=track_a,
            verdict=verdict_obj.verdict,
            verdict_reason=verdict_obj.reason,
            entry_price=entry_p if verdict_obj.verdict in ("buy", "wait") else None,
            stop_price=stop_p if verdict_obj.verdict in ("buy", "wait") else None,
            target_price=target_p,
            suggested_dte=suggested_dte,
            suggested_delta=suggested_delta,
            suggested_strike=suggested_strike,
            source_universe=ticker_universe.get(ticker),
        )
        setup.rank_score = _rank_score(setup)

        # Action verdict — only meaningful for directional setups.
        # No-direction confluences (chop, compression, no_setup) leave
        # action_verdict=None so the frontend renders no banner.
        if direction in ("long", "short"):
            try:
                from action_gate import classify_weekly_trend_action
                verdict = classify_weekly_trend_action({"1wk": row}, direction)
                setup.action_verdict = verdict.to_dict()
            except Exception:
                import logging as _logging
                _logging.getLogger(__name__).exception(
                    "weekly_trend verdict failed for %s", ticker,
                )

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
