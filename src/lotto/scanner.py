"""Lotto setup scanner — daily MA filter + 2H Stoch trigger + SQN regime gates.

Per ~/.claude/skills/user/lotto-options/SKILL.md. Runs on the default Tier 2
watchlist (QQQ + GLD) and outputs a per-ticker LottoSetup with the unified
verdict (buy/wait/no_go), entry/stop/target prices, and suggested options
DTE/delta band.

Lotto is the $1K satellite book; sizing is $50-150 per trade with 5-14 DTE
deep-OTM contracts for asymmetric payoff. The scanner does NOT recommend
specific strikes — that's `lotto.strikes.suggest_strikes()`. It surfaces
whether a setup IS firing; the caller picks strikes from there.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable, Literal

from free_range.filters import price_band_violation
from free_range.universe import LOTTO_HIGH_VOL_WATCHLIST  # re-exported below
from scan_verdict import TradeVerdict, lotto_verdict


# Default Tier 2 watchlist per ~/CLAUDE.md
LOTTO_DEFAULT_WATCHLIST: tuple[str, ...] = ("QQQ", "GLD")

# Magnificent Seven big caps — exempt from the $15-50 single-stock price
# band for lotto specifically (added 2026-05-12 per user direction).
# These have the deepest options chains in the US and produce
# lotto-suitable premium at delta/DTE combinations even when spot is
# well above $50. Exemption is lotto-scoped — other strategies still
# respect the band. Tracked in [[project-lotto-mag7-exemption]] memory.
LOTTO_MAG7_PRICE_EXEMPT: frozenset[str] = frozenset({
    "AAPL", "MSFT", "GOOGL", "GOOG", "AMZN", "META", "NVDA", "TSLA",
})


# LOTTO_HIGH_VOL_WATCHLIST (imported above, re-exported via __all__) lives
# in free_range/universe.py so it can double as the "lotto_high_vol" named
# universe without an import cycle. Full backtest rationale is documented
# there; this module keeps the historical import path working
# (`from lotto import LOTTO_HIGH_VOL_WATCHLIST` — used by the cloud scripts).


Direction = Literal["long", "short"]


@dataclass
class LottoSetup:
    """One ticker's lotto-TF read for a single direction."""
    ticker: str
    direction: Direction
    bar_date: str | None
    close: float | None
    daily_stack: str | None
    daily_stoch_k: float | None
    daily_stoch_d: float | None
    sqn_100_regime: str | None
    sqn_100_value: float | None
    sqn_20_regime: str | None
    sqn_20_value: float | None
    h2_stack: str | None
    h2_stoch_k: float | None
    h2_stoch_d: float | None
    h2_zone: str | None
    h2_signal: str | None
    why_now: str
    blockers: list[str] = field(default_factory=list)
    # Unified verdict fields
    verdict: str = "wait"
    verdict_reason: str = ""
    entry_price: float | None = None
    stop_price: float | None = None
    target_price: float | None = None
    suggested_dte: str | None = "5-14 DTE"
    suggested_delta: str | None = "0.10-0.25 (deep OTM lotto)"
    # Concrete dollar strike at the 0.20-delta target, BS-derived from spot
    # + HV20. None when scan didn't carry HV (e.g., test fixtures or thin
    # bars).
    suggested_strike: float | None = None
    # Which index this ticker came from when the lotto scan ran across
    # multiple universes. None when scanned via the legacy QQQ+GLD-only
    # default or an explicit ticker list. Used by the frontend to group
    # results by source index in the setup scan section.
    source_universe: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class LottoScanResult:
    scan_time_utc: str
    setups: list[LottoSetup] = field(default_factory=list)
    actionable_setups: list[LottoSetup] = field(default_factory=list)
    errors: dict[str, str] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "scan_time_utc": self.scan_time_utc,
            "setups": [s.to_dict() for s in self.setups],
            "actionable_setups": [s.to_dict() for s in self.actionable_setups],
            "errors": dict(self.errors),
        }


def _why_now_text(setup: LottoSetup) -> str:
    parts = [f"{setup.direction.upper()}"]
    parts.append(f"daily {setup.daily_stack or '?'}")
    if setup.h2_signal:
        parts.append(f"2H {setup.h2_signal.replace('_', ' ')}")
    if setup.h2_zone:
        parts.append(f"({setup.h2_zone})")
    if setup.sqn_100_regime:
        parts.append(f"SQN(100) {setup.sqn_100_regime}")
    if setup.sqn_20_value is not None:
        parts.append(f"SQN(20) {setup.sqn_20_value:+.2f}")
    return " · ".join(parts)


def _entry_stop_target_for(
    direction: Direction, close: float | None,
    daily_ma_20: float | None, daily_ma_50: float | None,
) -> tuple[float | None, float | None, float | None]:
    """Compute entry / stop / target for a lotto setup.

    Lotto entry = current close (caller will adjust at trigger fire).
    Stop on the underlying:
      - Long: ~2% below entry (matches ~50% premium decay on a 0.20-delta call)
      - Short: ~2% above entry
    Target: lotto trails for 5-10x premium gains; underlying-side target is
    coarse — the daily 200WMA equivalent in the move direction, or 5% favorable.
    """
    if close is None:
        return None, None, None
    entry = close
    stop = (close * 0.98) if direction == "long" else (close * 1.02)
    target = (close * 1.05) if direction == "long" else (close * 0.95)
    return entry, stop, target


def scan_lotto_watchlist(
    tickers: list[str] | None = None,
    *,
    scan_fn: Callable[[str, str], dict[str, Any]] | None = None,
    universe: list[str] | None = None,
) -> LottoScanResult:
    """Run the lotto setup scan.

    Args:
        tickers: explicit ticker list (takes precedence over `universe`).
            Use this for tests or when the caller wants a specific scan target.
        scan_fn: injected for tests; production uses scan_ticker.
        universe: list of universe names from src/free_range/universe.py
            (e.g., ["nasdaq_100", "sp500_top_50", "russell_2000_top_50"]).
            When `tickers` is None and `universe` is given, scan all tickers
            in those universes and tag each LottoSetup with `source_universe`
            so the UI can group results by index.

    When BOTH `tickers` and `universe` are None, falls back to the legacy
    QQQ + GLD baseline (LOTTO_DEFAULT_WATCHLIST) — keeps existing test
    fixtures and any callers that relied on the no-arg signature working.

    For each ticker, the scanner emits TWO LottoSetup rows (long + short).
    Verdict is computed independently per direction so the user sees both
    paths classified.
    """
    if scan_fn is None:
        from scan import scan_ticker
        def scan_fn(ticker: str, timeframe: str) -> dict[str, Any]:
            return scan_ticker(ticker, timeframe=timeframe)

    # Resolve scan target. Explicit `tickers` wins; then `universe`; then
    # legacy QQQ + GLD fallback. ticker_universe maps each scan target to
    # its source universe name so we can tag the resulting LottoSetup.
    ticker_universe: dict[str, str | None] = {}
    if tickers is not None:
        tickers_to_scan = [t.strip().upper() for t in tickers if t.strip()]
        for t in tickers_to_scan:
            ticker_universe[t] = None
    elif universe:
        from free_range.universe import free_range_universe
        seen: set[str] = set()
        tickers_to_scan = []
        for uni_name in universe:
            for t in free_range_universe(universe=uni_name):
                t_upper = t.upper()
                if t_upper in seen:
                    continue
                seen.add(t_upper)
                tickers_to_scan.append(t_upper)
                # First universe a ticker appears in "owns" it for grouping.
                ticker_universe[t_upper] = uni_name
    else:
        tickers_to_scan = list(LOTTO_DEFAULT_WATCHLIST)
        for t in tickers_to_scan:
            ticker_universe[t] = None

    errors: dict[str, str] = {}
    setups: list[LottoSetup] = []

    for ticker in tickers_to_scan:
        ticker = ticker.upper()
        try:
            daily = scan_fn(ticker, "1d")
        except Exception as exc:
            errors[ticker] = f"daily fetch failed: {exc}"
            continue

        try:
            h2 = scan_fn(ticker, "2h")
        except Exception:
            h2 = {"ma_ribbon": {}, "stochastic": {}}

        ma = daily.get("ma_ribbon") or {}
        sqn = daily.get("sqn") or {}
        stoch_d_data = daily.get("stochastic") or {}
        h2_ma = h2.get("ma_ribbon") or {}
        h2_stoch = h2.get("stochastic") or {}

        daily_stack = ma.get("stack_state")
        sqn_100_regime = sqn.get("regime")
        sqn_100_value = sqn.get("sqn_value")
        sqn_20_regime = sqn.get("regime_20")
        sqn_20_value = sqn.get("sqn_20_value")
        try:
            sqn_20_value_f = float(sqn_20_value) if sqn_20_value is not None else None
        except (TypeError, ValueError):
            sqn_20_value_f = None

        h2_signal = h2_stoch.get("signal")
        h2_zone = h2_stoch.get("zone")
        h2_stack = h2_ma.get("stack_state")
        close = daily.get("close")
        hv20 = daily.get("hv20")

        # Hard universe gate — single-stock price band per CLAUDE.md account
        # profile ($15-50; ETFs exempt; Mag 7 exempt for lotto specifically).
        # Applied before lotto_verdict so a band violator never surfaces as
        # BUY regardless of regime/momentum. Both directions get the same
        # violation answer for a given ticker.
        if ticker in LOTTO_MAG7_PRICE_EXEMPT:
            price_violation = None
        else:
            price_violation = price_band_violation(ticker, close)

        for direction in ("long", "short"):
            if price_violation is not None:
                v = TradeVerdict(
                    "no_go",
                    f"Outside lotto price band — {price_violation}",
                )
            else:
                v = lotto_verdict(
                    daily_stack=daily_stack,
                    sqn_100_regime=sqn_100_regime,
                    sqn_20_value=sqn_20_value_f,
                    h2_signal=h2_signal,
                    h2_zone=h2_zone,
                    direction=direction,
                )

            entry_p, stop_p, target_p = _entry_stop_target_for(
                direction, close,
                ma.get("ma_20"), ma.get("ma_50"),
            )

            # Concrete dollar strike at 0.20-delta (mid of 0.10-0.25 lotto band).
            # Mid-DTE = 10 days (mid of 7-14). Compute only when we have spot
            # + HV; falls through to None on missing data.
            suggested_strike: float | None = None
            if v.verdict in ("buy", "wait") and close is not None and hv20:
                from lotto.strikes import suggest_strike_for_delta
                suggested_strike = suggest_strike_for_delta(
                    spot=float(close), hv_annual=float(hv20),
                    dte_days=10,
                    kind="call" if direction == "long" else "put",
                    target_delta=0.20,
                    ticker=ticker,
                )

            setup = LottoSetup(
                ticker=ticker,
                direction=direction,  # type: ignore[arg-type]
                bar_date=daily.get("bar_date"),
                close=close,
                daily_stack=daily_stack,
                daily_stoch_k=stoch_d_data.get("k"),
                daily_stoch_d=stoch_d_data.get("d"),
                sqn_100_regime=sqn_100_regime,
                sqn_100_value=sqn_100_value,
                sqn_20_regime=sqn_20_regime,
                sqn_20_value=sqn_20_value_f,
                h2_stack=h2_stack,
                h2_stoch_k=h2_stoch.get("k"),
                h2_stoch_d=h2_stoch.get("d"),
                h2_zone=h2_zone,
                h2_signal=h2_signal,
                verdict=v.verdict,
                verdict_reason=v.reason,
                entry_price=entry_p if v.verdict in ("buy", "wait") else None,
                stop_price=stop_p if v.verdict in ("buy", "wait") else None,
                target_price=target_p if v.verdict in ("buy", "wait") else None,
                suggested_strike=suggested_strike,
                why_now="",  # filled below
                blockers=[],
                source_universe=ticker_universe.get(ticker),
            )
            setup.why_now = _why_now_text(setup)
            setups.append(setup)

    actionable = [s for s in setups if s.verdict == "buy"]

    return LottoScanResult(
        scan_time_utc=datetime.now(timezone.utc).isoformat(),
        setups=setups,
        actionable_setups=actionable,
        errors=errors,
    )


__all__ = [
    "Direction",
    "LOTTO_DEFAULT_WATCHLIST",
    "LOTTO_HIGH_VOL_WATCHLIST",
    "LOTTO_MAG7_PRICE_EXEMPT",
    "LottoScanResult",
    "LottoSetup",
    "scan_lotto_watchlist",
]
