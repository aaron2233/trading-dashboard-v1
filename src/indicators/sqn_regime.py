"""SQN Regime — strategic (100-day) and tactical (20-day) windows on log returns.

Source of truth: TRADING-DASHBOARD-HANDOFF.md sections 345-357,
weekly-trend-trader/references/sqn-regime-guide.md, and the SQN(20) tactical
extension documented in the same guide (lines 171-262).

Formula:
    log_returns = log(close[t] / close[t-1])
    SQN = mean(log_returns, lookback) / stdev(log_returns, lookback) * sqrt(lookback)

Applied to daily bars of the benchmark (SPY/QQQ/IWM or sector ETF). The
indicator does not enforce a timeframe — caller provides the bars.

Outputs per bar:
  - sqn_value : float (NaN during warmup or when stdev is zero)
  - regime    : strong_bull | bull | neutral | bear | strong_bear

Two band sets are exported:
  - SQN_100_BANDS — primary regime gatekeeper (default for SQNRegime())
  - SQN_20_BANDS  — tactical timing layer (instantiate with bands=SQN_20_BANDS,
                    lookback=20). Bands are wider on the negative side to match
                    the higher variance of the 20-day window. Calibrated against
                    SPY 1995-2026 daily data per sqn-regime-guide.md.

Boundary semantics (inclusive on neutral, exclusive on outer regimes — preserves
the historical SQN_100 convention so existing tests and downstream consumers do
not change behavior):

    SQN >  upper_strong         -> strong_bull
    upper_bull < SQN <= upper_strong -> bull
    lower_bear <= SQN <= upper_bull  -> neutral
    lower_strong <= SQN < lower_bear -> bear
    SQN <  lower_strong         -> strong_bear

`diagnose_sqn_pair(sqn_100_regime, sqn_20_regime, sqn_20_value)` mirrors the
diagnostic table in sqn-regime-guide.md for the two-window combinations
(confluence / capitulation reset / chase warning / etc.).
"""
from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from indicators.protocol import IndicatorProtocol  # noqa: F401


@dataclass(frozen=True)
class SQNBands:
    """5-region SQN classification thresholds.

    Region layout (left → right on the number line):
        strong_bear  <  bear  <=  neutral  <=  bull  <  strong_bull
                  ^lower_strong   ^lower_bear      ^upper_bull   ^upper_strong
    """
    upper_strong: float   # SQN > upper_strong → strong_bull
    upper_bull: float     # upper_bull < SQN <= upper_strong → bull
    lower_bear: float     # lower_bear <= SQN <= upper_bull → neutral (sub-band: bear is below)
    lower_strong: float   # lower_strong <= SQN < lower_bear → bear; SQN < lower_strong → strong_bear


# Primary 100-day bands (preserves prior SQN_100 behavior bit-for-bit).
SQN_100_BANDS = SQNBands(
    upper_strong=1.5,
    upper_bull=0.7,
    lower_bear=-0.7,
    lower_strong=-1.5,
)

# Tactical 20-day bands — asymmetric (wider on the negative side per
# sqn-regime-guide.md, calibrated SPY 1995-2026).
SQN_20_BANDS = SQNBands(
    upper_strong=1.4,
    upper_bull=0.5,
    lower_bear=-1.1,
    lower_strong=-1.9,
)


@dataclass
class SQNRegime:
    lookback: int = 100
    bands: SQNBands = field(default_factory=lambda: SQN_100_BANDS)
    name: str = "sqn_regime"
    inputs: tuple[str, ...] = ("close",)

    def compute(self, df: pd.DataFrame) -> pd.DataFrame:
        close = df["close"]
        log_returns = np.log(close / close.shift(1))

        mean = log_returns.rolling(self.lookback).mean()
        std = log_returns.rolling(self.lookback).std(ddof=1)

        sqn = (mean / std.where(std != 0)) * np.sqrt(self.lookback)

        b = self.bands
        regime = pd.Series("neutral", index=close.index, dtype="object")
        regime = regime.mask(sqn > b.upper_strong, "strong_bull")
        regime = regime.mask((sqn > b.upper_bull) & (sqn <= b.upper_strong), "bull")
        regime = regime.mask((sqn >= b.lower_bear) & (sqn <= b.upper_bull), "neutral")
        regime = regime.mask((sqn >= b.lower_strong) & (sqn < b.lower_bear), "bear")
        regime = regime.mask(sqn < b.lower_strong, "strong_bear")

        regime = regime.mask(sqn.isna(), other=pd.NA)

        return pd.DataFrame(
            {"sqn_value": sqn, "regime": regime},
            index=close.index,
        )


# ── Two-window diagnostic ────────────────────────────────────────────────────

# Thresholds for SQN(20) extreme cases — both expressed in raw SQN units, NOT
# regime labels (per sqn-regime-guide.md tactical rules section).
SQN_20_CHASE_THRESHOLD = 2.5     # > +2.5 inside Bull SQN(100) → trim/wait
SQN_20_CAPITULATION_DEEP = -2.0  # < -2.0 inside Bear SQN(100) → reversal watch


def diagnose_sqn_pair(
    sqn_100_regime: str | None,
    sqn_20_regime: str | None,
    sqn_20_value: float | None,
) -> str | None:
    """Map (SQN(100) regime, SQN(20) regime, SQN(20) value) → diagnostic string.

    Returns None if either regime is missing (warmup window). Diagnostic strings
    mirror the table in
    ~/.claude/skills/user/weekly-trend-trader/references/sqn-regime-guide.md
    "How to Read SQN(20) vs SQN(100)" + "Tactical Rules".
    """
    if sqn_100_regime is None or sqn_20_regime is None:
        return None
    if pd.isna(sqn_100_regime) or pd.isna(sqn_20_regime):
        return None

    bull_100 = sqn_100_regime in ("bull", "strong_bull")
    bear_100 = sqn_100_regime in ("bear", "strong_bear")
    neutral_100 = sqn_100_regime == "neutral"

    bull_20 = sqn_20_regime in ("bull", "strong_bull")
    bear_20 = sqn_20_regime in ("bear", "strong_bear")
    neutral_20 = sqn_20_regime == "neutral"

    if bull_100:
        if sqn_20_regime == "strong_bull":
            if sqn_20_value is not None and sqn_20_value > SQN_20_CHASE_THRESHOLD:
                return "confluence_chase_warning"  # > +2.5 = trim/wait, no fresh longs
            return "confluence_bullish"
        if sqn_20_regime == "bull":
            return "healthy_trend"
        if neutral_20:
            return "normal_pullback"
        if bear_20:
            return "buy_the_dip"  # capitulation reset within primary bull

    if neutral_100:
        if sqn_20_regime == "strong_bull":
            return "early_bull_signal"
        if sqn_20_regime == "bull":
            return "trend_forming"
        if neutral_20:
            return "true_chop"
        if bear_20:
            return "early_bear_signal"

    if bear_100:
        if bull_20:
            return "counter_trend_bounce"  # do not flip long
        if neutral_20:
            return "bear_weakening"
        if bear_20:
            if sqn_20_value is not None and sqn_20_value < SQN_20_CAPITULATION_DEEP:
                return "confluence_capitulation_watch"
            return "confluence_bearish"

    return "uncategorized"
