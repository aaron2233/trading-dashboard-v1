"""Regime-levered-trend scanner — Layer 1 core-entry scan on the weekly TF.

Implements the scan half of `~/.claude/skills/user/regime-levered-trend/SKILL.md`:

    Layer 1 candidate filter (all must pass):
      1. Broad SPY SQN(100) >= +0.7  (Bull gate — no new entries below it)
      2. Own daily SQN(100) >= +0.7  (asset-level Bull)
      3. Weekly Full Bull ribbon: 10 > 20 > 50 > 200, 10 & 20 rising
      4. Entry trigger: weekly Stoch(14,7,7) %K turned up this week from a
         reset (%K_prev < 70), close holding above the 20WMA, %K < 80.
         Pullback-touch of the 20WMA is nice-to-have, NOT required — the
         2026-07-01 backtest showed requiring the touch starves the book
         (24 vs 53 signals over 26.5 yrs). See skill Provenance table.
    Ranking: own SQN(100), descending. Max 2 concurrent Layer 1 slots — the
    scan caps BUY verdicts at 2; further qualifying names rank as WAIT.

    Layer 2 (dip-buy) check: SPY/QQQ daily Stoch %K < 20 while broad SQN(100)
    is Bull/Strong Bull (CLAUDE.md rule 19). Reported alongside, never in
    Neutral/Bear.

Own-asset SQN(100) is read from the DAILY scan row — the weekly-row SQN is
computed on weekly bars and is NOT the skill's SQN(100) regime gate.

The scanner is position-blind (like the lotto cloud scan): it caps BUY slots
at MAX_CORE_POSITIONS but cannot see open positions — verify against the book
before deploying. Deployment itself is gated by the skill's R1/R2 rule; the
scan surfaces candidates and carries the gate note, it does not place trades.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable, Literal


# Skill-universe default — the 13 optionable names from the Layer 1 backtest
# (scripts/regime_levered_trend_backtest.py). Crypto excluded: no options in
# a cash account. Callers may pass any explicit ticker list.
DEFAULT_UNIVERSE: list[str] = [
    "QQQ", "SPY", "IWM", "GLD", "META", "MU", "AAPL", "MSFT",
    "NVDA", "AMD", "AMZN", "NFLX", "TSLA",
]

BROAD_SQN_MIN: float = 0.7      # broad-market Bull gate
OWN_SQN_MIN: float = 0.7        # asset-level Bull gate
STOCH_RESET_MAX: float = 70.0   # %K_prev below this counts as a reset
STOCH_OVERBOUGHT: float = 80.0  # %K at/above this = watchlist, never entry
MA_SLOPE_LOOKBACK: int = 4      # weekly bars for the rising-MA check
MAX_CORE_POSITIONS: int = 2     # Layer 1 concurrent-slot cap
DIP_BUY_STOCH_MAX: float = 20.0  # daily %K threshold for the rule-19 dip
DIP_BUY_TICKERS: tuple[str, ...] = ("SPY", "QQQ")

DEPLOYMENT_GATE_NOTE: str = (
    "Deployment gate: R1/R2 recovery rules block this skill in the main "
    "account (no override path) — dedicated sleeve or post-recovery only. "
    "Kill sheet + trade-devil required before entry. Scanner is "
    "position-blind: verify the 2-slot cap against the open book."
)

Confluence = Literal[
    "core_entry",           # all Layer 1 filters pass + Stoch turn trigger
    "overbought_watch",     # Full Bull + own SQN ok, but %K >= 80 — watchlist
    "bull_no_trigger",      # Full Bull + own SQN ok, no Stoch turn this week
    "own_regime_blocked",   # Full Bull ribbon but own SQN(100) < +0.7
    "not_full_bull",        # ribbon not a rising Full Bull stack
    "no_data",              # insufficient bars / indicator failure
]


@dataclass
class WeeklyState:
    """Computed weekly-TF indicator state for one ticker. Pure data."""
    bar_date: str | None
    close: float | None
    ma_10: float | None
    ma_20: float | None
    ma_50: float | None
    ma_200: float | None
    ma_19: float | None            # Layer 1 structural stop anchor
    full_bull: bool                # 10>20>50>200 with 10 & 20 rising
    stoch_k: float | None
    stoch_d: float | None
    stoch_k_prev: float | None
    stoch_turned_up: bool          # K > K_prev this week
    close_above_20: bool

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class RegimeLeveredSetup:
    """One ticker's Layer 1 read + verdict."""
    ticker: str
    bar_date: str | None
    close: float | None
    own_sqn_100: float | None
    own_regime: str | None
    weekly: WeeklyState | None
    confluence: Confluence
    rank_score: float              # own SQN(100); NaN-safe fallback -99
    why_now: str
    blockers: list[str] = field(default_factory=list)
    # Unified verdict + entry/stop fields (shared across all scans)
    verdict: str = "wait"          # buy | wait | no_go
    verdict_reason: str = ""
    entry_price: float | None = None
    stop_price: float | None = None      # 19WMA structural stop
    target_price: float | None = None    # None by design — runner strategy
    suggested_dte: str | None = None
    suggested_delta: str | None = None
    suggested_strike: float | None = None

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["weekly"] = self.weekly.to_dict() if self.weekly else None
        return d


@dataclass
class DipBuySignal:
    """Rule-19 Layer 2 dip-buy read for SPY/QQQ."""
    ticker: str
    daily_stoch_k: float | None
    fired: bool
    note: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class RegimeLeveredScanResult:
    scan_time_utc: str
    benchmark: str
    broad_sqn_100: float | None
    broad_regime: str | None
    layer1_live: bool              # broad gate passed — new entries allowed
    deployment_note: str
    setups: list[RegimeLeveredSetup] = field(default_factory=list)
    core_candidates: list[RegimeLeveredSetup] = field(default_factory=list)
    dip_buy_signals: list[DipBuySignal] = field(default_factory=list)
    errors: dict[str, str] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "scan_time_utc": self.scan_time_utc,
            "benchmark": self.benchmark,
            "broad_sqn_100": self.broad_sqn_100,
            "broad_regime": self.broad_regime,
            "layer1_live": self.layer1_live,
            "deployment_note": self.deployment_note,
            "setups": [s.to_dict() for s in self.setups],
            "core_candidates": [s.to_dict() for s in self.core_candidates],
            "dip_buy_signals": [s.to_dict() for s in self.dip_buy_signals],
            "errors": dict(self.errors),
        }


# ─────────────────────────────────────────────────────────────────────────
# Weekly indicator state (pure pandas, no I/O)
# ─────────────────────────────────────────────────────────────────────────


def compute_weekly_state(weekly_bars: Any) -> WeeklyState | None:
    """Compute ribbon + Stoch state from a weekly OHLC DataFrame.

    Expects lowercase `close`/`high`/`low` columns (data.yfinance_loader
    convention). Returns None when bars are missing or too short for the
    200WMA warmup.
    """
    if weekly_bars is None or len(weekly_bars) < 205:
        return None
    try:
        closes = weekly_bars["close"]
        highs = weekly_bars["high"]
        lows = weekly_bars["low"]

        mas = {n: closes.rolling(n).mean() for n in (10, 20, 50, 200)}
        ma_19 = closes.rolling(19).mean()

        lo14 = lows.rolling(14).min()
        hi14 = highs.rolling(14).max()
        raw_k = 100.0 * (closes - lo14) / (hi14 - lo14)
        k_series = raw_k.rolling(7).mean()
        d_series = k_series.rolling(7).mean()

        close = float(closes.iloc[-1])
        v = {n: float(mas[n].iloc[-1]) for n in (10, 20, 50, 200)}
        rising = all(
            float(mas[n].iloc[-1]) > float(mas[n].iloc[-1 - MA_SLOPE_LOOKBACK])
            for n in (10, 20)
        )
        full_bull = (v[10] > v[20] > v[50] > v[200]) and rising

        k_now = float(k_series.iloc[-1])
        k_prev = float(k_series.iloc[-2])
        d_now = float(d_series.iloc[-1])

        bar_date = None
        try:
            bar_date = str(weekly_bars.index[-1].date())
        except Exception:
            pass

        return WeeklyState(
            bar_date=bar_date,
            close=close,
            ma_10=v[10], ma_20=v[20], ma_50=v[50], ma_200=v[200],
            ma_19=float(ma_19.iloc[-1]),
            full_bull=full_bull,
            stoch_k=k_now, stoch_d=d_now, stoch_k_prev=k_prev,
            stoch_turned_up=k_now > k_prev,
            close_above_20=close > v[20],
        )
    except Exception:
        return None


# ─────────────────────────────────────────────────────────────────────────
# Layer 1 classification (pure)
# ─────────────────────────────────────────────────────────────────────────


def classify_layer1(
    state: WeeklyState | None,
    own_sqn: float | None,
    broad_gate_open: bool,
) -> tuple[Confluence, str, str, list[str]]:
    """Return (confluence, verdict, reason, blockers) for one ticker.

    Verdict here is pre-slot-cap: `scan_regime_levered` may downgrade a BUY
    to WAIT when more than MAX_CORE_POSITIONS names qualify.
    """
    blockers: list[str] = []

    if state is None:
        return "no_data", "no_go", "Insufficient weekly bars (200WMA warmup)", blockers

    if not state.full_bull:
        return (
            "not_full_bull", "no_go",
            "Ribbon is not a rising Full Bull stack — Layer 1 requires 10>20>50>200 rising",
            blockers,
        )

    if own_sqn is None or own_sqn < OWN_SQN_MIN:
        sqn_txt = f"{own_sqn:+.2f}" if own_sqn is not None else "n/a"
        return (
            "own_regime_blocked", "no_go",
            f"Own SQN(100) {sqn_txt} < +{OWN_SQN_MIN} — asset regime below Bull",
            blockers,
        )

    if not broad_gate_open:
        blockers.append(
            f"Broad SQN(100) below +{BROAD_SQN_MIN} — Layer 1 closed to new entries"
        )

    k = state.stoch_k if state.stoch_k is not None else 0.0
    k_prev = state.stoch_k_prev if state.stoch_k_prev is not None else 0.0

    if k >= STOCH_OVERBOUGHT:
        return (
            "overbought_watch", "wait",
            f"Weekly Stoch %K {k:.0f} >= {STOCH_OVERBOUGHT:.0f} — never chase; "
            "watchlist for the 20WMA pullback",
            blockers,
        )

    triggered = (
        state.stoch_turned_up
        and k_prev < STOCH_RESET_MAX
        and state.close_above_20
    )
    if triggered:
        if blockers:  # broad gate closed — signal valid, entry blocked
            return ("core_entry", "wait",
                    "Trigger valid but broad regime gate is closed", blockers)
        return ("core_entry", "buy",
                "Full Bull + own SQN Bull + Stoch reset-turn holding the 20WMA",
                blockers)

    return (
        "bull_no_trigger", "wait",
        "Full Bull stack, no Stoch turn-up from reset this week",
        blockers,
    )


def _why_now(setup_confluence: Confluence, state: WeeklyState | None,
             own_sqn: float | None) -> str:
    sqn_txt = f"SQN(100) {own_sqn:+.2f}" if own_sqn is not None else "SQN n/a"
    if state is None:
        return "No weekly data"
    k_txt = f"%K {state.stoch_k:.0f}" if state.stoch_k is not None else "%K n/a"
    labels: dict[Confluence, str] = {
        "core_entry": f"CORE ENTRY · Stoch turn from reset · {k_txt} · {sqn_txt}",
        "overbought_watch": f"Overbought — wait for 20WMA pullback · {k_txt} · {sqn_txt}",
        "bull_no_trigger": f"Full Bull, no trigger yet · {k_txt} · {sqn_txt}",
        "own_regime_blocked": f"Ribbon bull but {sqn_txt} below gate",
        "not_full_bull": "Not a Full Bull stack — out of Layer 1 universe this week",
        "no_data": "No weekly data",
    }
    return labels[setup_confluence]


# ─────────────────────────────────────────────────────────────────────────
# Top-level scan
# ─────────────────────────────────────────────────────────────────────────


def scan_regime_levered(
    tickers: list[str] | None = None,
    *,
    benchmark: str = "SPY",
    scan_fn: Callable[[str, str], dict[str, Any]] | None = None,
    bars_fn: Callable[[str], Any] | None = None,
) -> RegimeLeveredScanResult:
    """Run the Layer 1 core scan + Layer 2 dip-buy check.

    `scan_fn(ticker, timeframe)` is src/scan.py::scan_ticker — injected for
    tests. Used for DAILY reads only (own/broad SQN(100), daily Stoch, HV).
    `bars_fn(ticker)` returns weekly OHLC bars for the ribbon/Stoch state;
    defaults to yfinance weekly bars (max period for the 200WMA warmup).
    """
    if scan_fn is None:
        from scan import scan_ticker

        def scan_fn(ticker: str, timeframe: str) -> dict[str, Any]:
            return scan_ticker(ticker, timeframe=timeframe)

    if bars_fn is None:
        try:
            from data.yfinance_loader import load_bars

            def bars_fn(ticker: str) -> Any:
                # 200WMA warmup needs ~4y of weekly bars; take 10y for slope
                # stability on the 200.
                return load_bars(ticker, period="10y", interval="1wk")
        except Exception:
            bars_fn = None

    errors: dict[str, str] = {}
    tickers_to_scan = [
        t.strip().upper() for t in (tickers or DEFAULT_UNIVERSE) if t and t.strip()
    ]

    # Broad regime gate — daily SQN(100) on the benchmark.
    broad_sqn: float | None = None
    broad_regime: str | None = None
    daily_rows: dict[str, dict[str, Any]] = {}
    try:
        bench_row = scan_fn(benchmark.upper(), "1d")
        daily_rows[benchmark.upper()] = bench_row
        sqn_block = bench_row.get("sqn") or {}
        broad_sqn = sqn_block.get("sqn_value")
        broad_regime = sqn_block.get("regime")
    except Exception as exc:
        errors[benchmark.upper()] = f"benchmark regime read failed: {exc}"
    layer1_live = broad_sqn is not None and broad_sqn >= BROAD_SQN_MIN

    # Layer 2 — rule-19 dip-buy check on SPY/QQQ daily Stoch.
    dip_signals: list[DipBuySignal] = []
    for t in DIP_BUY_TICKERS:
        try:
            row = daily_rows.get(t) or scan_fn(t, "1d")
            daily_rows[t] = row
            k = (row.get("stochastic") or {}).get("k")
            fired = bool(layer1_live and k is not None and k < DIP_BUY_STOCH_MAX)
            if fired:
                note = (f"Rule-19 dip: daily %K {k:.0f} < {DIP_BUY_STOCH_MAX:.0f} in "
                        f"Bull SQN(100) — 120-180 DTE 60-70Δ call, −60% premium stop, no 2R cap")
            elif k is not None and k < DIP_BUY_STOCH_MAX:
                note = "Daily oversold but broad SQN(100) below Bull — no edge, stand aside"
            else:
                note = "No dip signal"
            dip_signals.append(DipBuySignal(ticker=t, daily_stoch_k=k,
                                            fired=fired, note=note))
        except Exception as exc:
            errors[f"dip_{t}"] = str(exc)

    setups: list[RegimeLeveredSetup] = []
    seen: set[str] = set()
    for ticker in tickers_to_scan:
        if ticker in seen:
            continue
        seen.add(ticker)

        own_sqn: float | None = None
        own_regime: str | None = None
        hv: float | None = None
        try:
            row = daily_rows.get(ticker) or scan_fn(ticker, "1d")
            daily_rows[ticker] = row
            sqn_block = row.get("sqn") or {}
            own_sqn = sqn_block.get("sqn_value")
            own_regime = sqn_block.get("regime")
            hv = row.get("hv20")
        except Exception as exc:
            errors[ticker] = f"daily read failed: {exc}"

        state: WeeklyState | None = None
        if bars_fn is not None:
            try:
                state = compute_weekly_state(bars_fn(ticker))
            except Exception as exc:
                errors[ticker] = f"weekly bars failed: {exc}"

        confluence, verdict, reason, blockers = classify_layer1(
            state, own_sqn, layer1_live,
        )

        entry_p = state.close if (state and verdict in ("buy", "wait")) else None
        stop_p = state.ma_19 if (state and verdict in ("buy", "wait")) else None
        suggested_strike: float | None = None
        if verdict == "buy" and state and state.close and hv:
            try:
                from lotto.strikes import suggest_strike_for_delta
                suggested_strike = suggest_strike_for_delta(
                    spot=float(state.close), hv_annual=float(hv),
                    dte_days=450,           # mid of 365-540 DTE band
                    kind="call",
                    target_delta=0.825,     # mid of 0.75-0.90 deep ITM
                    ticker=ticker,
                )
            except Exception:
                suggested_strike = None

        setups.append(RegimeLeveredSetup(
            ticker=ticker,
            bar_date=state.bar_date if state else None,
            close=state.close if state else None,
            own_sqn_100=own_sqn,
            own_regime=own_regime,
            weekly=state,
            confluence=confluence,
            rank_score=own_sqn if own_sqn is not None else -99.0,
            why_now=_why_now(confluence, state, own_sqn),
            blockers=blockers,
            verdict=verdict,
            verdict_reason=reason,
            entry_price=entry_p,
            stop_price=stop_p,
            suggested_dte="365-540 DTE LEAPS" if verdict != "no_go" else None,
            suggested_delta="0.75-0.90 (deep ITM)" if verdict != "no_go" else None,
            suggested_strike=suggested_strike,
        ))

    # Rank: own SQN desc, ties by ticker. Cap BUY slots at MAX_CORE_POSITIONS.
    setups.sort(key=lambda s: (-s.rank_score, s.ticker))
    buy_slots = 0
    for s in setups:
        if s.verdict == "buy":
            buy_slots += 1
            if buy_slots > MAX_CORE_POSITIONS:
                s.verdict = "wait"
                s.verdict_reason = (
                    f"Qualifies, but ranked below the top {MAX_CORE_POSITIONS} "
                    f"by own SQN(100) — max {MAX_CORE_POSITIONS} concurrent Layer 1 slots"
                )
    core = [s for s in setups if s.verdict == "buy"]

    return RegimeLeveredScanResult(
        scan_time_utc=datetime.now(timezone.utc).isoformat(),
        benchmark=benchmark.upper(),
        broad_sqn_100=broad_sqn,
        broad_regime=broad_regime,
        layer1_live=layer1_live,
        deployment_note=DEPLOYMENT_GATE_NOTE,
        setups=setups,
        core_candidates=core,
        dip_buy_signals=dip_signals,
        errors=errors,
    )
