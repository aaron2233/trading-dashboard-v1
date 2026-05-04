"""MA Ribbon indicator (10/20/50/200 SMA of close).

Source of truth: TRADING-DASHBOARD-HANDOFF.md sections 313-330 and 67-84.

Outputs per bar:
  - ma_10, ma_20, ma_50, ma_200 : simple moving averages of close
  - stack_state : one of {full_bull, bull_developing, compression, chop,
                          bear_developing, full_bear}

State classification:
  full_bull         : 10 > 20 > 50 > 200 AND all rising over slope_lookback
  full_bear         : 10 < 20 < 50 < 200 AND all falling over slope_lookback
  bull_developing   : 10 > 20 AND 10 > 50 AND 200 not strongly declining
  bear_developing   : 10 < 20 AND 10 < 50 AND 200 not strongly rising
  compression       : (max(MAs) - min(MAs)) / close < compression_threshold_pct
  chop              : none of the above

Evaluation priority: compression is evaluated first (overridable by clean stacks),
then developing states, then full_bull / full_bear override developing if the
stack is clean AND slopes confirm.

Warmup: the first 200 bars have NaN for ma_200 and therefore NaN stack_state
(implementation returns 'chop' once 200 bars are available; before that,
stack_state is NaN). Fixture dates should be drawn from the post-warmup window.
"""
from dataclasses import dataclass

import pandas as pd

from indicators.protocol import IndicatorProtocol  # noqa: F401  (runtime check)


@dataclass
class MARibbon:
    periods: tuple[int, int, int, int] = (10, 20, 50, 200)
    slope_lookback: int = 5
    compression_threshold_pct: float = 0.015
    name: str = "ma_ribbon"
    inputs: tuple[str, ...] = ("close",)

    def compute(self, df: pd.DataFrame) -> pd.DataFrame:
        close = df["close"]
        p1, p2, p3, p4 = self.periods

        ma1 = close.rolling(p1).mean()
        ma2 = close.rolling(p2).mean()
        ma3 = close.rolling(p3).mean()
        ma4 = close.rolling(p4).mean()

        n = self.slope_lookback
        rising1 = ma1 > ma1.shift(n)
        rising2 = ma2 > ma2.shift(n)
        rising3 = ma3 > ma3.shift(n)
        rising4 = ma4 > ma4.shift(n)

        falling1 = ma1 < ma1.shift(n)
        falling2 = ma2 < ma2.shift(n)
        falling3 = ma3 < ma3.shift(n)
        falling4 = ma4 < ma4.shift(n)

        all_rising = rising1 & rising2 & rising3 & rising4
        all_falling = falling1 & falling2 & falling3 & falling4

        stacked_bull = (ma1 > ma2) & (ma2 > ma3) & (ma3 > ma4)
        stacked_bear = (ma1 < ma2) & (ma2 < ma3) & (ma3 < ma4)

        ribbon = pd.concat([ma1, ma2, ma3, ma4], axis=1)
        ribbon_max = ribbon.max(axis=1)
        ribbon_min = ribbon.min(axis=1)
        ribbon_width_pct = (ribbon_max - ribbon_min) / close

        compressed = ribbon_width_pct < self.compression_threshold_pct

        bull_dev = (ma1 > ma2) & (ma1 > ma3) & ~falling4
        bear_dev = (ma1 < ma2) & (ma1 < ma3) & ~rising4
        full_bull = stacked_bull & all_rising
        full_bear = stacked_bear & all_falling

        state = pd.Series("chop", index=close.index, dtype="object")
        state = state.mask(compressed, "compression")
        state = state.mask(bull_dev & ~compressed, "bull_developing")
        state = state.mask(bear_dev & ~compressed, "bear_developing")
        state = state.mask(full_bull, "full_bull")
        state = state.mask(full_bear, "full_bear")

        warmup_nan = ma4.isna()
        state = state.mask(warmup_nan, other=pd.NA)

        return pd.DataFrame(
            {
                "ma_10": ma1,
                "ma_20": ma2,
                "ma_50": ma3,
                "ma_200": ma4,
                "stack_state": state,
            },
            index=close.index,
        )
