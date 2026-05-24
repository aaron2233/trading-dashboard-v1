"""Position sizing helpers.

For Standard kill sheets we report the dollar risk budget. The unit count
(shares or contracts) requires a max_loss_per_unit which depends on
invalidation distance for shares, or premium for options — both of which
are user-supplied or filled in by later modules (options template).
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass
class PositionSize:
    max_risk_usd: float
    max_loss_per_unit: float | None
    units: int | None
    capped_by: str | None = None  # "max_per_trade_usd" if cap kicked in

    def units_or_tbd(self) -> str:
        if self.units is None:
            return "[set after max_loss_per_unit (premium or stop distance) is defined]"
        return str(self.units)


def calculate_position_size(
    account_balance_usd: float,
    risk_pct: float,
    max_loss_per_unit: float | None = None,
    max_per_trade_usd: float | None = None,
) -> PositionSize:
    """Compute risk budget + unit count.

    Args:
        account_balance_usd: account size in USD.
        risk_pct: per-trade risk as a fraction in [0, 1] (e.g. 0.025 = 2.5%).
        max_loss_per_unit: dollars at risk per unit (option premium*100 for
            options; stop distance for shares). When None, only the dollar
            risk budget is computed.
        max_per_trade_usd: hard ceiling on a single trade's max risk. Lotto
            account uses this ($150 cap). When the percentage budget exceeds
            the cap, the cap wins and capped_by="max_per_trade_usd".
    """
    if account_balance_usd < 0:
        raise ValueError("account_balance_usd must be non-negative")
    if risk_pct < 0 or risk_pct > 1:
        raise ValueError("risk_pct must be in [0, 1] (e.g. 0.025 for 2.5%)")
    if max_per_trade_usd is not None and max_per_trade_usd < 0:
        raise ValueError("max_per_trade_usd must be non-negative when provided")

    pct_budget = account_balance_usd * risk_pct
    capped_by: str | None = None
    if max_per_trade_usd is not None and pct_budget > max_per_trade_usd:
        max_risk = max_per_trade_usd
        capped_by = "max_per_trade_usd"
    else:
        max_risk = pct_budget

    units: int | None = None
    if max_loss_per_unit is not None:
        if max_loss_per_unit <= 0:
            raise ValueError("max_loss_per_unit must be positive when provided")
        units = int(max_risk // max_loss_per_unit)

    return PositionSize(
        max_risk_usd=round(max_risk, 2),
        max_loss_per_unit=max_loss_per_unit,
        units=units,
        capped_by=capped_by,
    )
