"""Index-swing scanner — 2H-TF breakout above prior swing high on QQQ/IWM/SPY.

Trigger TF switched from 1d → 2h on 2026-05-13 per backtest evidence
(`scripts/index_swing_tf_backtest.py`): 2H produced PF 1.79 / mean R +0.41 /
48% WR vs 1d's 1.35 / +0.21 / 40% on the same 2y window. High-conviction
confluence (≥3 quality filters) was the actionable cohort at PF 1.96.

Implements the entry trigger from
`~/.claude/skills/user/index-swing/SKILL.md`:

    1. Identify the prior 5-bar swing high (most recent qualifying swing high
       on the 2H chart).
    2. 2H close above that level is the breakout signal.
    3. Confluence checks (volume vs 30-bar avg, base tightness via ATR-20,
       upper-third close, no nearby failed breakout).
    4. Disqualifier checks (SQN-100 Bear Volatile from daily, low volume,
       gap >2%). Daily SQN regime is unchanged — it stays daily-anchored.

The scanner returns one `IndexSwingSetup` per ticker — the frontend renders
these as cards. Kill sheets are generated from picked candidates by the
existing kill_sheet pipeline.

The scanner is hard-locked to QQQ, IWM, SPY. Tickers outside this set are
rejected with `confluence="universe_violation"` and not eligible for kill
sheets — the kill_sheet builder mirrors this hard gate via the
`INDEX_SWING_ALLOWED_TICKERS` frozenset.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable, Literal

import pandas as pd


# Hard universe — backtest 1999-2022 only validated these three. IWM is the
# workhorse (n=184, +0.96 avgR), QQQ second (n=175, +0.79), SPY a small but
# clean sample (n=11, +1.17). Single-name extension is unvalidated.
INDEX_SWING_ALLOWED_TICKERS: frozenset[str] = frozenset({"QQQ", "IWM", "SPY"})

# Tier ranking within the allowed universe.
INDEX_SWING_TIER_PRIMARY: frozenset[str] = frozenset({"QQQ", "IWM"})
INDEX_SWING_TIER_SECONDARY: frozenset[str] = frozenset({"SPY"})

# Swing-high lookback / lookforward (bars on each side that must be lower).
# 5 bars each side is the standard "5-bar swing high" definition.
DEFAULT_SWING_BARS: int = 5

# Confluence thresholds (per skill spec).
VOLUME_CONFIRMATION_RATIO: float = 1.0   # >= 1.0× 30d avg for quality
VOLUME_REJECT_RATIO: float = 0.7         # < 0.7× 30d avg → disqualifier
GAP_MAX_PCT: float = 2.0                 # >2% gap on breakout = disqualifier
BASE_TIGHTNESS_ATR_RATIO: float = 1.5    # base range < 1.5× ATR-20 = "tight"
SWING_RECENCY_WINDOW: int = 30           # prior swing high must be within N sessions


Confluence = Literal[
    "breakout_high_conviction",     # close > swing high + 3+ confluence checks
    "breakout_standard",            # close > swing high + base trigger only
    "no_breakout",                  # no recent swing-high break
    "skip_bear_volatile",           # SQN(100) Strong Bear, or Bear + SQN(20) < -1.9
                                    # (reproduces backtest's "Bear Volatile" SQN-100 +
                                    # vol-overlay classification — NOT SQN-20 alone)
    "skip_low_volume",              # breakout volume < 0.7× avg
    "skip_macro_event",             # within 3 sessions of FOMC/CPI/NFP (placeholder)
    "universe_violation",           # ticker not in QQQ/IWM/SPY
]


@dataclass
class SwingHighBreakout:
    """Detected breakout structure for one ticker."""
    swing_high_value: float
    swing_high_date: str
    swing_high_age_sessions: int
    breakout_close: float
    breakout_date: str
    breakout_volume: float
    avg_volume_30d: float
    volume_ratio: float
    base_range_atr_ratio: float | None
    bar_close_in_upper_third: bool
    higher_lows_pattern: bool
    nearby_failed_breakouts: int
    confluence_count: int

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class IndexSwingSetup:
    """One ticker's daily-TF index-swing read."""
    ticker: str
    bar_date: str | None
    close: float | None
    in_universe: bool
    universe_tier: Literal["primary", "secondary", "outside"]
    sqn_20_regime: str | None
    sqn_100_regime: str | None
    confluence: Confluence
    breakout: SwingHighBreakout | None
    suggested_stop: float | None
    suggested_target_2r: float | None
    why_now: str
    blockers: list[str] = field(default_factory=list)
    # ─── Unified verdict + entry/stop fields (shared across all scans) ───
    verdict: str = "wait"          # buy | wait | no_go
    verdict_reason: str = ""
    entry_price: float | None = None
    stop_price: float | None = None
    target_price: float | None = None
    suggested_dte: str | None = "30-60 DTE"
    suggested_delta: str | None = "0.50-0.65 (ATM/slight ITM)"
    # Concrete dollar strike at the 0.575-delta target (mid of 0.50-0.65),
    # BS-derived from spot + HV at 45 DTE (mid of 30-60). None when HV
    # unavailable.
    suggested_strike: float | None = None

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        if self.breakout is not None:
            d["breakout"] = self.breakout.to_dict()
        return d


@dataclass
class IndexSwingScanResult:
    scan_time_utc: str
    setups: list[IndexSwingSetup] = field(default_factory=list)
    actionable_setups: list[IndexSwingSetup] = field(default_factory=list)
    errors: dict[str, str] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "scan_time_utc": self.scan_time_utc,
            "setups": [s.to_dict() for s in self.setups],
            "actionable_setups": [s.to_dict() for s in self.actionable_setups],
            "errors": dict(self.errors),
        }


# ─────────────────────────────────────────────────────────────────────────
# Swing-high detection
# ─────────────────────────────────────────────────────────────────────────


def _find_prior_swing_high(
    bars: pd.DataFrame, *, swing_bars: int = DEFAULT_SWING_BARS,
    max_age: int = SWING_RECENCY_WINDOW,
) -> tuple[float, str, int] | None:
    """Find the most recent N-bar swing high in the last `max_age` sessions.

    A swing high is a bar whose `high` is greater than the highs of the
    `swing_bars` bars preceding AND following it. We exclude the trailing
    `swing_bars` bars from the search since they cannot yet be confirmed as
    swing highs (need `swing_bars` more bars to confirm).

    Returns (swing_high_value, swing_high_date_iso, age_in_sessions) or None
    if no qualifying swing high found in the window.
    """
    if len(bars) < (2 * swing_bars + 2):
        return None

    # The latest bar we can confirm as a swing high must have `swing_bars`
    # additional bars after it. So search from `len-swing_bars-1` down to
    # `swing_bars` (inclusive).
    search_end_idx = len(bars) - swing_bars - 1
    earliest_idx = max(swing_bars, search_end_idx - max_age)

    highs = bars["high"].values
    for i in range(search_end_idx, earliest_idx - 1, -1):
        candidate = highs[i]
        # Confirm strict inequality vs `swing_bars` bars on each side.
        left_ok = all(highs[i - k] < candidate for k in range(1, swing_bars + 1))
        right_ok = all(highs[i + k] < candidate for k in range(1, swing_bars + 1))
        if left_ok and right_ok:
            age = (len(bars) - 1) - i
            date_iso = bars.index[i].strftime("%Y-%m-%d")
            return float(candidate), date_iso, age

    return None


def _atr_20(bars: pd.DataFrame) -> float | None:
    """20-bar Average True Range. None if insufficient data."""
    if len(bars) < 21:
        return None
    high = bars["high"].iloc[-21:]
    low = bars["low"].iloc[-21:]
    close_prev = bars["close"].shift(1).iloc[-21:]
    tr = pd.concat(
        [(high - low),
         (high - close_prev).abs(),
         (low - close_prev).abs()],
        axis=1,
    ).max(axis=1)
    return float(tr.iloc[1:].mean())


def _has_higher_lows(bars: pd.DataFrame, lookback: int = 15) -> bool:
    """Loose check: are the last 2-3 daily-low pivot points trending up?"""
    if len(bars) < lookback + 1:
        return False
    recent = bars["low"].iloc[-lookback:]
    # Simple monotonic-ish check on the rolling 5-bar lows.
    rolling_min = recent.rolling(5, min_periods=3).min().dropna()
    if len(rolling_min) < 3:
        return False
    # First-third low vs last-third low
    third = max(1, len(rolling_min) // 3)
    early_low = rolling_min.iloc[:third].min()
    late_low = rolling_min.iloc[-third:].min()
    return float(late_low) > float(early_low)


def _count_failed_breakouts(
    bars: pd.DataFrame, level: float, lookback: int = 10,
) -> int:
    """Count how many of the last `lookback` daily closes touched/exceeded the
    `level` intraday but failed to close above it.

    A "failed breakout" = high >= level AND close < level on the same bar.
    """
    if len(bars) < lookback:
        return 0
    recent = bars.iloc[-lookback:]
    failed = ((recent["high"] >= level) & (recent["close"] < level)).sum()
    return int(failed)


# ─────────────────────────────────────────────────────────────────────────
# Per-ticker setup classification
# ─────────────────────────────────────────────────────────────────────────


def detect_swing_high_breakout(
    bars: pd.DataFrame, *,
    swing_bars: int = DEFAULT_SWING_BARS,
) -> tuple[Confluence, SwingHighBreakout | None, list[str]]:
    """Run the breakout detection on a single ticker's daily bars.

    Returns (confluence_label, breakout_struct_or_None, blockers).
    """
    blockers: list[str] = []

    if len(bars) < 60:
        return "no_breakout", None, ["insufficient daily history (need 60+ bars)"]

    swing = _find_prior_swing_high(bars, swing_bars=swing_bars)
    if swing is None:
        return "no_breakout", None, ["no qualifying swing high in lookback window"]

    swing_high, swing_date, age_sessions = swing
    last_bar = bars.iloc[-1]
    last_close = float(last_bar["close"])
    last_high = float(last_bar["high"])
    last_low = float(last_bar["low"])
    last_volume = float(last_bar["volume"])
    last_date = bars.index[-1].strftime("%Y-%m-%d")
    prev_close = float(bars["close"].iloc[-2])

    # Did today close above the prior swing high?
    if last_close <= swing_high:
        return "no_breakout", None, [
            f"latest close ${last_close:.2f} <= prior swing high ${swing_high:.2f}"
        ]

    # ── Confluence / disqualifier checks ──────────────────────────────────
    avg_vol_30d = float(bars["volume"].iloc[-30:].mean())
    vol_ratio = last_volume / avg_vol_30d if avg_vol_30d > 0 else 0.0

    # Disqualifier: low volume
    if vol_ratio < VOLUME_REJECT_RATIO:
        return "skip_low_volume", None, [
            f"breakout volume {vol_ratio:.2f}× avg < {VOLUME_REJECT_RATIO}× threshold"
        ]

    # Disqualifier: gap >2% on the breakout bar
    gap_pct = ((float(last_bar["open"]) - prev_close) / prev_close) * 100.0
    if gap_pct > GAP_MAX_PCT:
        blockers.append(
            f"open gap +{gap_pct:.2f}% > {GAP_MAX_PCT}% — chase risk"
        )

    # Confluence checks (each contributes to confluence count)
    confluence_count = 0

    # 1. Volume confirmation (>= 1.0× avg)
    if vol_ratio >= VOLUME_CONFIRMATION_RATIO:
        confluence_count += 1

    # 2. Tight base before breakout — last 10 bars range relative to ATR-20
    base_range = float(bars["high"].iloc[-11:-1].max() - bars["low"].iloc[-11:-1].min())
    atr = _atr_20(bars)
    base_atr_ratio: float | None = None
    if atr is not None and atr > 0:
        base_atr_ratio = base_range / (atr * 10)  # normalize against 10-bar ATR sum
        if base_atr_ratio < BASE_TIGHTNESS_ATR_RATIO:
            confluence_count += 1

    # 3. Bar close in upper third of day's range
    bar_range = last_high - last_low
    upper_third_threshold = last_high - (bar_range / 3.0)
    upper_third = last_close >= upper_third_threshold
    if upper_third:
        confluence_count += 1

    # 4. Higher-lows pattern leading in
    higher_lows = _has_higher_lows(bars)
    if higher_lows:
        confluence_count += 1

    # 5. No nearby failed breakouts at this level
    failed_count = _count_failed_breakouts(bars, swing_high)
    if failed_count == 0:
        confluence_count += 1

    breakout = SwingHighBreakout(
        swing_high_value=swing_high,
        swing_high_date=swing_date,
        swing_high_age_sessions=age_sessions,
        breakout_close=last_close,
        breakout_date=last_date,
        breakout_volume=last_volume,
        avg_volume_30d=avg_vol_30d,
        volume_ratio=vol_ratio,
        base_range_atr_ratio=base_atr_ratio,
        bar_close_in_upper_third=upper_third,
        higher_lows_pattern=higher_lows,
        nearby_failed_breakouts=failed_count,
        confluence_count=confluence_count,
    )

    if confluence_count >= 3:
        return "breakout_high_conviction", breakout, blockers
    return "breakout_standard", breakout, blockers


# ─────────────────────────────────────────────────────────────────────────
# Why-now copy
# ─────────────────────────────────────────────────────────────────────────


def _why_now(
    confluence: Confluence,
    breakout: SwingHighBreakout | None,
    sqn_20_regime: str | None,
) -> str:
    if confluence == "universe_violation":
        return "Outside QQQ/IWM/SPY universe — index-swing skill is hard-locked"
    if confluence == "skip_bear_volatile":
        return (
            "Structural Bear-Volatile (SQN-100 Strong Bear, or SQN-100 Bear + "
            "SQN-20 < -1.9) — only net-negative regime in backtest"
        )
    if confluence == "skip_low_volume":
        return "Breakout volume below 0.7× 30d avg — false-breakout risk too high"
    if confluence == "no_breakout":
        return "No daily close above prior swing high"
    if breakout is None:
        return "Breakout flag set but breakout details missing"

    parts = [confluence.replace("_", " ").upper()]
    parts.append(f"swing high ${breakout.swing_high_value:.2f} ({breakout.swing_high_age_sessions}d ago)")
    parts.append(f"vol {breakout.volume_ratio:.2f}× avg")
    parts.append(f"confluence {breakout.confluence_count}/5")
    if sqn_20_regime:
        parts.append(f"SQN(20) {sqn_20_regime}")
    return " · ".join(parts)


# ─────────────────────────────────────────────────────────────────────────
# Top-level scan
# ─────────────────────────────────────────────────────────────────────────


def _classify_universe(ticker: str) -> Literal["primary", "secondary", "outside"]:
    t = ticker.upper()
    if t in INDEX_SWING_TIER_PRIMARY:
        return "primary"
    if t in INDEX_SWING_TIER_SECONDARY:
        return "secondary"
    return "outside"


def scan_index_swing_watchlist(
    tickers: list[str] | None = None,
    *,
    bars_fn: Callable[[str], pd.DataFrame] | None = None,
    scan_fn: Callable[[str, str], dict[str, Any]] | None = None,
) -> IndexSwingScanResult:
    """Run the index-swing scan over the hard-locked QQQ/IWM/SPY universe.

    Trigger TF is **2H** as of 2026-05-13 — the TF-comparison backtest
    (`scripts/index_swing_tf_backtest.py`) showed 2H delivered +0.41
    mean R / 48% WR / PF 1.79 across 77 trades vs 1d's +0.21 / 40% /
    1.35 across 125 trades (same universe, 2y window). High-conviction
    confluence (3+ checks) is the actionable cohort — n=60, PF 1.96.
    The daily SQN regime read is still daily-anchored (gate is the same
    regardless of trigger TF).

    Args:
        tickers: optional caller list — but ALL non-allowed tickers are
            tagged `universe_violation` and produce no actionable setup.
            Default: scan the full INDEX_SWING_ALLOWED_TICKERS set.
        bars_fn: injected for tests — returns 2H DataFrame for a ticker.
            Production: uses load_bars from data.yfinance_loader.
        scan_fn: injected for tests — returns scan_ticker(...) result.
            Production: uses scan.scan_ticker for SQN regime reads.
    """
    if bars_fn is None:
        from data.yfinance_loader import load_bars
        def bars_fn(ticker: str) -> pd.DataFrame:
            # 3mo of 2H bars (~200 bars) covers 5-bar swing-high detection,
            # 30-bar volume avg, 20-bar ATR, and 30-bar recency window with
            # headroom. yfinance 1h-resampled is 730d capped if we ever need
            # more.
            return load_bars(ticker, period="3mo", interval="2h")

    if scan_fn is None:
        from scan import scan_ticker
        def scan_fn(ticker: str, timeframe: str) -> dict[str, Any]:
            return scan_ticker(ticker, timeframe=timeframe)

    if tickers is None:
        tickers_to_scan = sorted(INDEX_SWING_ALLOWED_TICKERS)
    else:
        tickers_to_scan = [t.strip().upper() for t in tickers if t.strip()]

    errors: dict[str, str] = {}
    setups: list[IndexSwingSetup] = []
    seen: set[str] = set()

    for ticker in tickers_to_scan:
        if ticker in seen:
            continue
        seen.add(ticker)

        tier = _classify_universe(ticker)
        in_universe = tier != "outside"

        if not in_universe:
            setups.append(IndexSwingSetup(
                ticker=ticker,
                bar_date=None,
                close=None,
                in_universe=False,
                universe_tier="outside",
                sqn_20_regime=None,
                sqn_100_regime=None,
                confluence="universe_violation",
                breakout=None,
                suggested_stop=None,
                suggested_target_2r=None,
                why_now=_why_now("universe_violation", None, None),
                blockers=[
                    f"{ticker} is outside the index-swing hard universe (QQQ/IWM/SPY)"
                ],
            ))
            continue

        # Fetch daily bars for breakout detection
        try:
            bars = bars_fn(ticker)
        except Exception as exc:
            errors[ticker] = f"bars fetch failed: {exc}"
            continue

        if bars is None or bars.empty:
            errors[ticker] = "no daily bars available"
            continue

        # Fetch SQN regime via existing scanner
        sqn_20_regime: str | None = None
        sqn_100_regime: str | None = None
        try:
            scan_row = scan_fn(ticker, "1d")
            sqn_data = scan_row.get("sqn") or {}
            sqn_20_regime = sqn_data.get("regime_20")
            sqn_100_regime = sqn_data.get("regime")
        except Exception as exc:
            errors[ticker] = f"SQN read failed: {exc}"
            # Continue with breakout detection anyway — SQN is a gate, not a blocker.

        last_close = float(bars["close"].iloc[-1])
        last_date = bars.index[-1].strftime("%Y-%m-%d")

        # Structural Bear-Volatile = hard skip. Reproduces the index-swing
        # backtest's "Bear Volatile" classification, which is SQN(100)-based
        # with a realized-vol overlay. In our codebase the closest analog is:
        #   (a) SQN(100) = Strong Bear, OR
        #   (b) SQN(100) = Bear AND SQN(20) < -1.9 (extreme low)
        # NOT SQN(20) Bear Volatile alone — that's often the buy-the-dip zone
        # per orchestrator rule 12 (high edge inside a Bull SQN-100 context).
        try:
            sqn_20_value = float(scan_row.get("sqn", {}).get("sqn_20_value")) if scan_row.get("sqn", {}).get("sqn_20_value") is not None else None
        except (TypeError, ValueError, NameError):
            sqn_20_value = None
        is_strong_bear_100 = sqn_100_regime == "strong_bear"
        is_bear_with_capitulation = (
            sqn_100_regime == "bear"
            and sqn_20_value is not None
            and sqn_20_value < -1.9
        )
        if is_strong_bear_100 or is_bear_with_capitulation:
            reason = (
                "SQN(100) Strong Bear" if is_strong_bear_100
                else f"SQN(100) Bear + SQN(20) {sqn_20_value:.2f} < -1.9 (capitulation)"
            )
            setups.append(IndexSwingSetup(
                ticker=ticker, bar_date=last_date, close=last_close,
                in_universe=True, universe_tier=tier,
                sqn_20_regime=sqn_20_regime,
                sqn_100_regime=sqn_100_regime,
                confluence="skip_bear_volatile",
                breakout=None,
                suggested_stop=None, suggested_target_2r=None,
                why_now=_why_now("skip_bear_volatile", None, sqn_20_regime),
                blockers=[f"{reason} — index-swing hard skip (structural bear-volatile)"],
            ))
            continue

        confluence, breakout, blockers = detect_swing_high_breakout(bars)

        # Compute suggested stop / target if there's a breakout
        suggested_stop: float | None = None
        suggested_target_2r: float | None = None
        if breakout is not None:
            entry = breakout.breakout_close
            # Stop: lesser of (a) -2% from entry, (b) bar low (which is also
            # the breakout day's low). Per skill: use the more structural one.
            stop_2pct = entry * 0.98
            stop_bar_low = float(bars.iloc[-1]["low"])
            suggested_stop = max(stop_2pct, stop_bar_low - (entry * 0.001))
            # Use min so stop is closer to entry (tighter); the skill says
            # LESSER (closer to entry) for the "less of" rule.
            suggested_stop = min(stop_2pct, stop_bar_low)
            risk = entry - suggested_stop
            suggested_target_2r = entry + (2.0 * risk)

        setups.append(IndexSwingSetup(
            ticker=ticker, bar_date=last_date, close=last_close,
            in_universe=True, universe_tier=tier,
            sqn_20_regime=sqn_20_regime,
            sqn_100_regime=sqn_100_regime,
            confluence=confluence,
            breakout=breakout,
            suggested_stop=suggested_stop,
            suggested_target_2r=suggested_target_2r,
            why_now=_why_now(confluence, breakout, sqn_20_regime),
            blockers=blockers,
        ))

    # Apply the unified verdict + entry/stop projection to every setup.
    from scan_verdict import index_swing_verdict
    for s in setups:
        confluence_count = s.breakout.confluence_count if s.breakout else None
        v = index_swing_verdict(s.confluence, confluence_count)
        s.verdict = v.verdict
        s.verdict_reason = v.reason
        if s.breakout is not None:
            s.entry_price = s.breakout.breakout_close
            s.stop_price = s.suggested_stop
            s.target_price = s.suggested_target_2r

        # Concrete dollar strike at 0.575 delta (mid of 0.50-0.65), 45 DTE.
        # Compute on every actionable card (verdict ≠ no_go) so the user
        # sees "here's the strike to look at" even before the breakout fires.
        # Uses the breakout close when present, otherwise the latest close.
        if v.verdict in ("buy", "wait") and s.close is not None:
            try:
                daily_row = scan_fn(s.ticker, "1d")
                hv = daily_row.get("hv20")
            except Exception:
                hv = None
            spot_for_strike = s.entry_price if s.entry_price is not None else s.close
            if hv and spot_for_strike is not None:
                from lotto.strikes import suggest_strike_for_delta
                s.suggested_strike = suggest_strike_for_delta(
                    spot=float(spot_for_strike), hv_annual=float(hv),
                    dte_days=45,
                    kind="call",   # index-swing is long-only by design
                    target_delta=0.575,
                    ticker=s.ticker,
                )

    actionable = [
        s for s in setups
        if s.confluence in ("breakout_high_conviction", "breakout_standard")
    ]

    return IndexSwingScanResult(
        scan_time_utc=datetime.now(timezone.utc).isoformat(),
        setups=setups,
        actionable_setups=actionable,
        errors=errors,
    )
