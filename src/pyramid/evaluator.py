"""Top-level evaluator: fetch bars, compute indicators, run gate/tranches/exits.

This is the entry point most callers use. The CLI/API/frontend route through
`evaluate_pyramid()` which:

1. Fetches daily bars for the ticker (yfinance via existing data loader).
2. Fetches benchmark bars for SQN regime.
3. Computes MA Ribbon + Stochastic + SQN(100) + SQN(20).
4. Runs price-structure analysis (swing highs/lows, pullback hold).
5. Evaluates gate, all three tranches, and exits.
6. Returns a single PyramidEvaluation object the caller can render.

Pyramid persistence (which tranches are filled, costs, etc.) is handled by
`pyramid.store`; this module just reads the Pyramid to know which tranche to
evaluate next and which LEAPS to check on the roll calendar.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any

from data.crypto_loader import is_crypto_symbol, load_crypto_bars
from data.yfinance_loader import load_bars
from indicators.ma_ribbon import MARibbon
from indicators.sqn_regime import (
    SQN_20_BANDS,
    SQNRegime,
    diagnose_sqn_pair,
)
from indicators.stochastic import Stochastic
from pyramid.divergence import (
    detect_bearish_divergence,
    detect_bullish_divergence,
)
from pyramid.exits import ExitDirective, evaluate_exits
from pyramid.gate import GateResult, evaluate_gate
from pyramid.model import Pyramid
from pyramid.structure import StructureRead, analyze_structure
from pyramid.tranches import TrancheTriggerResult, evaluate_t1, evaluate_t2, evaluate_t3


@dataclass
class PyramidEvaluation:
    ticker: str
    direction: str
    bar_date: str
    close: float
    sqn_100_value: float | None
    sqn_100_regime: str | None
    sqn_20_value: float | None
    sqn_20_regime: str | None
    sqn_diagnostic: str | None
    ma_10: float | None
    ma_20: float | None
    ma_50: float | None
    ma_200: float | None
    ma_stack_state: str | None
    stoch_k: float | None
    stoch_d: float | None
    structure: StructureRead
    gate: GateResult
    t1: TrancheTriggerResult | None
    t2: TrancheTriggerResult | None
    t3: TrancheTriggerResult | None
    exits: list[ExitDirective] = field(default_factory=list)
    next_tranche: int | None = None  # which tranche should be evaluated for fire

    def to_dict(self) -> dict[str, Any]:
        return {
            "ticker": self.ticker,
            "direction": self.direction,
            "bar_date": self.bar_date,
            "close": self.close,
            "sqn_100_value": self.sqn_100_value,
            "sqn_100_regime": self.sqn_100_regime,
            "sqn_20_value": self.sqn_20_value,
            "sqn_20_regime": self.sqn_20_regime,
            "sqn_diagnostic": self.sqn_diagnostic,
            "ma_10": self.ma_10,
            "ma_20": self.ma_20,
            "ma_50": self.ma_50,
            "ma_200": self.ma_200,
            "ma_stack_state": self.ma_stack_state,
            "stoch_k": self.stoch_k,
            "stoch_d": self.stoch_d,
            "structure": asdict(self.structure),
            "gate": asdict(self.gate),
            "t1": asdict(self.t1) if self.t1 else None,
            "t2": asdict(self.t2) if self.t2 else None,
            "t3": asdict(self.t3) if self.t3 else None,
            "exits": [asdict(e) for e in self.exits],
            "next_tranche": self.next_tranche,
        }


def _safe(v: Any) -> Any:
    if v is None:
        return None
    if isinstance(v, float) and v != v:
        return None
    return v


def _load_bars_for(ticker: str, period: str = "2y"):
    if is_crypto_symbol(ticker):
        return load_crypto_bars(ticker, timeframe="1d", count=300)
    return load_bars(ticker, period=period, interval="1d")


def evaluate_pyramid(
    ticker: str,
    direction: str,
    *,
    benchmark: str = "SPY",
    pyramid: Pyramid | None = None,
    period: str = "2y",
) -> PyramidEvaluation:
    """Run a full pyramid evaluation for `ticker` in `direction`.

    If `pyramid` is provided, T2/T3 evaluators are aware of which tranches are
    already filled. If None, evaluator returns gate + T1 evaluation only
    (planning mode for a fresh pyramid).
    """
    if direction not in ("long", "short"):
        raise ValueError(f"direction must be 'long' or 'short', got {direction!r}")

    bars = _load_bars_for(ticker, period=period)
    if bars.empty:
        raise ValueError(f"No bars for {ticker}")

    # Indicators on the ticker itself (for MA stack, Stoch, structure)
    ma_df = MARibbon().compute(bars)
    stoch_df = Stochastic().compute(bars)

    # SQN regime is computed on the BENCHMARK, not the ticker (per skill rule).
    if benchmark.upper() == ticker.upper():
        bench_bars = bars
    else:
        bench_bars = _load_bars_for(benchmark, period=period)
    sqn_100_df = SQNRegime().compute(bench_bars)
    sqn_20_df = SQNRegime(
        lookback=20, bands=SQN_20_BANDS, name="sqn_regime_20",
    ).compute(bench_bars)

    latest = bars.index[-1]
    close = float(bars["close"].iloc[-1])
    ma_last = ma_df.loc[latest]

    bench_latest = bench_bars.index[-1]
    sqn_100_last = sqn_100_df.loc[bench_latest]
    sqn_20_last = sqn_20_df.loc[bench_latest]

    stoch_last = stoch_df.loc[latest]

    sqn_100_value = _safe(sqn_100_last["sqn_value"])
    sqn_100_regime = _safe(sqn_100_last["regime"])
    sqn_20_value = _safe(sqn_20_last["sqn_value"])
    sqn_20_regime = _safe(sqn_20_last["regime"])
    sqn_diagnostic = diagnose_sqn_pair(sqn_100_regime, sqn_20_regime, sqn_20_value)

    ma_10 = _safe(ma_last["ma_10"])
    ma_20 = _safe(ma_last["ma_20"])
    ma_50 = _safe(ma_last["ma_50"])
    ma_200 = _safe(ma_last["ma_200"])
    ma_stack_state = _safe(ma_last["stack_state"])

    stoch_k = _safe(stoch_last["k"])
    stoch_d = _safe(stoch_last["d"])

    structure = analyze_structure(
        bars, ma_df["ma_20"], ma_df["ma_50"],
    )

    gate = evaluate_gate(
        direction=direction,
        sqn_100_regime=sqn_100_regime,
        sqn_20_regime=sqn_20_regime,
        sqn_20_value=sqn_20_value,
        ma_stack_state=ma_stack_state,
        structure=structure,
    )

    # Tranche evaluations
    t1 = evaluate_t1(
        direction=direction,
        stoch_k=stoch_k,
        stoch_d=stoch_d,
        close=close,
        ma_20=ma_20,
    )

    t2: TrancheTriggerResult | None = None
    t3: TrancheTriggerResult | None = None
    next_tranche: int | None = 1
    if pyramid is not None:
        t1_filled = pyramid.get_tranche(1).status == "filled"
        t2_filled = pyramid.get_tranche(2).status == "filled"
        if t1_filled:
            t2 = evaluate_t2(
                direction=direction,
                pyramid=pyramid,
                stoch_k=stoch_k,
                stoch_d=stoch_d,
                sqn_100_regime=sqn_100_regime,
                sqn_20_regime=sqn_20_regime,
                structure=structure,
            )
            next_tranche = 2
        if t2_filled:
            t3 = evaluate_t3(
                direction=direction,
                pyramid=pyramid,
                stoch_k=stoch_k,
                stoch_d=stoch_d,
                sqn_20_value=sqn_20_value,
                sqn_100_regime=sqn_100_regime,
                structure=structure,
                ma_10=ma_10,
                ma_20=ma_20,
            )
            next_tranche = 3
        if pyramid.get_tranche(3).status == "filled":
            next_tranche = None  # all tranches filled — trail-management mode

    # LEAPS roll calendar input — pass the rich Tranche objects so directives
    # can surface cost basis, quantity, and $ exposure for each held LEAPS.
    leaps_tranches = []
    if pyramid is not None:
        leaps_tranches = [
            tr for tr in pyramid.filled_tranches()
            if tr.vehicle in ("leaps_call", "leaps_put") and tr.expiry
        ]

    # Divergence reads — only meaningful when Stoch %K is at the relevant
    # extreme. Cheap enough to compute always; the exit cascade ignores when
    # confirmed=False.
    bearish_div = detect_bearish_divergence(bars["close"], stoch_df["k"])
    bullish_div = detect_bullish_divergence(bars["close"], stoch_df["k"])

    exits = evaluate_exits(
        direction=direction,
        sqn_100_regime=sqn_100_regime,
        sqn_20_value=sqn_20_value,
        stoch_k=stoch_k,
        close=close,
        ma_50=ma_50,
        ma_200=ma_200,
        leaps_tranches=leaps_tranches or None,
        bearish_divergence=bearish_div,
        bullish_divergence=bullish_div,
    )

    return PyramidEvaluation(
        ticker=ticker.upper(),
        direction=direction,
        bar_date=latest.strftime("%Y-%m-%d"),
        close=close,
        sqn_100_value=sqn_100_value,
        sqn_100_regime=sqn_100_regime,
        sqn_20_value=sqn_20_value,
        sqn_20_regime=sqn_20_regime,
        sqn_diagnostic=sqn_diagnostic,
        ma_10=ma_10,
        ma_20=ma_20,
        ma_50=ma_50,
        ma_200=ma_200,
        ma_stack_state=ma_stack_state,
        stoch_k=stoch_k,
        stoch_d=stoch_d,
        structure=structure,
        gate=gate,
        t1=t1,
        t2=t2,
        t3=t3,
        exits=exits,
        next_tranche=next_tranche,
    )
