"""Default threshold rules for each indicator.

NOTE: most defaults are conservative-permissive starting values informed by
trading literature; user-overridable via ~/.trading-dashboard/config.yaml
under `regime_health.thresholds`.

Tier 3 breadth thresholds (rsp_spy_5d_slope, iwm_spy_5d_slope) are calibrated
from a 23-year SPY-drawdown backtest — see
`scripts/backtest_rsp_spy_5d_slope.py` and
`scripts/backtest_*_5d_slope_output.json` for the precision/lift tables that
support these levels. Other indicators are still ⚠️ UNVERIFIED pending
their own calibration sprints.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

from regime_health.model import IndicatorStatus


@dataclass
class NumericThreshold:
    """Threshold rule for a numeric indicator — green/amber/red bands.

    `direction` controls how `amber_at` and `red_at` are interpreted:
      - "above": amber when value >= amber_at; red when value >= red_at
        (use for indicators where higher = worse, e.g. VIX, HY OAS)
      - "below": amber when value <= amber_at; red when value <= red_at
        (use for indicators where lower = worse, e.g. nothing right now;
        kept for symmetry / future use)
      - "outside_band": green when amber_at <= value <= red_at_high;
        amber outside that band; red beyond a wider outer band. Used for
        breakeven inflation where both too-low AND too-high are bad.
    """
    amber_at: float
    red_at: float
    direction: Literal["above", "below"] = "above"
    units: str = ""               # display unit, e.g. "bps", "%", "pts"

    def evaluate(self, value: float | None) -> IndicatorStatus:
        if value is None:
            return "unknown"
        if self.direction == "above":
            if value >= self.red_at:
                return "red"
            if value >= self.amber_at:
                return "amber"
            return "green"
        else:  # "below"
            if value <= self.red_at:
                return "red"
            if value <= self.amber_at:
                return "amber"
            return "green"

    def describe(self) -> str:
        sign = ">" if self.direction == "above" else "<"
        u = f" {self.units}" if self.units else ""
        return f"amber {sign}{self.amber_at:g}{u} / red {sign}{self.red_at:g}{u}"


@dataclass
class BreakevenThreshold:
    """Two-sided band threshold for inflation breakeven — too low or too high
    are both flagged. Used only for T5YIE."""
    green_low: float
    green_high: float
    amber_low: float
    amber_high: float
    red_low: float
    red_high: float
    units: str = "%"

    def evaluate(self, value: float | None) -> IndicatorStatus:
        if value is None:
            return "unknown"
        if value < self.red_low or value > self.red_high:
            return "red"
        if value < self.amber_low or value > self.amber_high:
            return "amber"
        if self.green_low <= value <= self.green_high:
            return "green"
        return "amber"

    def describe(self) -> str:
        return (
            f"green {self.green_low:g}-{self.green_high:g}{self.units} / "
            f"amber outside / red <{self.red_low:g}{self.units} or >{self.red_high:g}{self.units}"
        )


@dataclass
class ThresholdConfig:
    """Top-level config for all numeric-threshold indicators. Categorical
    indicators (SQN regime, MA stack) use direct mapping in the tier modules,
    not this config."""

    # Tier 1
    vix: NumericThreshold = field(
        default_factory=lambda: NumericThreshold(
            amber_at=18.0, red_at=25.0, direction="above", units="pts",
        )
    )
    vvix: NumericThreshold = field(
        default_factory=lambda: NumericThreshold(
            amber_at=100.0, red_at=115.0, direction="above", units="pts",
        )
    )

    # Tier 2 — FRED macro
    hy_oas_bps: NumericThreshold = field(
        default_factory=lambda: NumericThreshold(
            amber_at=350.0, red_at=500.0, direction="above", units="bps",
        )
    )
    five_year_breakeven: BreakevenThreshold = field(
        default_factory=lambda: BreakevenThreshold(
            green_low=2.0, green_high=2.7,
            amber_low=1.8, amber_high=3.0,
            red_low=1.5, red_high=3.5,
            units="%",
        )
    )
    # Yield-curve and dollar are categorical/regime-derived, handled in tier2.

    # Tier 3 — breadth (slope-based). Amber/red levels backtest-calibrated
    # against forward 20d SPY drawdown ≥5% over the 2003-2026 RSP+SPY sample
    # (5,787 trading days). See scripts/backtest_rsp_spy_5d_slope.py.
    #   RSP/SPY (cap-concentration breadth):
    #     amber -1.5%/5d → 1.78x lift, 27.7% precision, ~7 fires/yr
    #     red   -2.5%/5d → 3.30x lift, 51.4% precision, ~1.5 fires/yr
    #   IWM/SPY (size-factor breadth, complementary):
    #     amber -2.0%/5d → 2.41x lift @ 5d×5%, ~19 fires/yr (2000-2026 sample)
    #     red   -3.0%/5d → 3.66x lift @ 5d×5%, ~6 fires/yr
    rsp_spy_5d_slope: NumericThreshold = field(
        default_factory=lambda: NumericThreshold(
            amber_at=-1.5, red_at=-2.5, direction="below", units="%",
        )
    )
    iwm_spy_5d_slope: NumericThreshold = field(
        default_factory=lambda: NumericThreshold(
            amber_at=-2.0, red_at=-3.0, direction="below", units="%",
        )
    )


DEFAULT_THRESHOLDS = ThresholdConfig()


# ── Categorical mappings ─────────────────────────────────────────────────────

SQN_REGIME_TO_STATUS: dict[str, IndicatorStatus] = {
    "strong_bull": "green",
    "bull": "green",
    "neutral": "amber",
    "bear": "red",
    "strong_bear": "red",
}

# Weekly MA stack state → status for the "Weekly MA on SPY/QQQ" indicator.
# States below are the exact strings emitted by indicators.ma_ribbon.MARibbon
# (see ma_ribbon.py mask sequence). Mapping rationale:
#   - full_bull / bull_developing → green (clean uptrend)
#   - compression / chop → amber (mixed/tangled MAs, late-cycle ambiguity)
#   - bear_developing / full_bear → red (downtrend or rolling over)
MA_STACK_TO_STATUS: dict[str, IndicatorStatus] = {
    "full_bull": "green",
    "bull_developing": "green",
    "compression": "amber",
    "chop": "amber",
    "bear_developing": "red",
    "full_bear": "red",
}
