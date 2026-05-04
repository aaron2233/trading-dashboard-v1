"""Stochastic (14, 7, 7) with 7-type signal classification.

Source of truth: TRADING-DASHBOARD-HANDOFF.md sections 332-343 and 86-103.

Formula:
    K_raw = 100 * (close - LL(14)) / (HH(14) - LL(14))
    K     = SMA(K_raw, 7)
    D     = SMA(K, 7)

Outputs per bar:
  - k, d   : smoothed stochastic and signal line
  - zone   : oversold (K<20) | mid (20<=K<=80) | overbought (K>80)
  - signal : per-bar event, one of
             bull_cross_oversold    (K crosses above D this bar, K < 30)
             bear_cross_overbought  (K crosses below D this bar, K > 70)
             bull_continuation      (K crosses above D this bar, 40 <= K <= 60)
             bear_continuation      (K crosses below D this bar, 40 <= K <= 60)
             bullish_divergence     (new price low in last 14 bars, D not at new low)
             bearish_divergence     (new price high in last 14 bars, D not at new high)
             neutral                (no event this bar)

Cross priority: oversold/overbought cross beats continuation; divergence fires
independently when no cross fires; ties resolved by cross > divergence.

Handoff note [TRADING-DASHBOARD-HANDOFF.md:102]: overbought during breakouts =
strength, not a sell. Downstream code should consider MA Ribbon state before
treating overbought as a reversal.
"""
from dataclasses import dataclass

import pandas as pd

from indicators.protocol import IndicatorProtocol  # noqa: F401


@dataclass
class Stochastic:
    length: int = 14
    smooth_k: int = 7
    smooth_d: int = 7
    divergence_lookback: int = 14
    name: str = "stochastic"
    inputs: tuple[str, ...] = ("high", "low", "close")

    def compute(self, df: pd.DataFrame) -> pd.DataFrame:
        high = df["high"]
        low = df["low"]
        close = df["close"]

        hh = high.rolling(self.length).max()
        ll = low.rolling(self.length).min()
        rng = hh - ll
        k_raw = 100.0 * (close - ll) / rng.where(rng != 0)
        k = k_raw.rolling(self.smooth_k).mean()
        d = k.rolling(self.smooth_d).mean()

        zone = pd.Series("mid", index=close.index, dtype="object")
        zone = zone.mask(k < 20, "oversold")
        zone = zone.mask(k > 80, "overbought")
        zone = zone.mask(k.isna(), other=pd.NA)

        k_prev = k.shift(1)
        d_prev = d.shift(1)
        bull_cross = (k_prev <= d_prev) & (k > d)
        bear_cross = (k_prev >= d_prev) & (k < d)

        lb = self.divergence_lookback
        price_max = close.rolling(lb).max()
        price_min = close.rolling(lb).min()
        d_max = d.rolling(lb).max()
        d_min = d.rolling(lb).min()

        price_new_high = close >= price_max
        price_new_low = close <= price_min
        d_new_high = d >= d_max
        d_new_low = d <= d_min

        bearish_div = price_new_high & ~d_new_high
        bullish_div = price_new_low & ~d_new_low

        signal = pd.Series("neutral", index=close.index, dtype="object")

        signal = signal.mask(bullish_div & ~bull_cross & ~bear_cross, "bullish_divergence")
        signal = signal.mask(bearish_div & ~bull_cross & ~bear_cross, "bearish_divergence")

        bull_cont_zone = (k >= 40) & (k <= 60)
        bear_cont_zone = (k >= 40) & (k <= 60)
        signal = signal.mask(bull_cross & bull_cont_zone, "bull_continuation")
        signal = signal.mask(bear_cross & bear_cont_zone, "bear_continuation")

        signal = signal.mask(bull_cross & (k < 30), "bull_cross_oversold")
        signal = signal.mask(bear_cross & (k > 70), "bear_cross_overbought")

        signal = signal.mask(k.isna() | d.isna(), other=pd.NA)

        return pd.DataFrame(
            {"k": k, "d": d, "zone": zone, "signal": signal},
            index=close.index,
        )
