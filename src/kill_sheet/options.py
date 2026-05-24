"""Options structure helpers for the options template.

Encodes the bundled-reference defaults for delta target by conviction,
DTE band by trigger TF, IV Rank classification, and liquidity thresholds
(open interest, bid-ask spread). Tune the constants below to your own
strategy spec.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime


_DELTA_BY_CONVICTION = {
    "high":         (0.50, 0.60),
    "medium":       (0.35, 0.45),
    "speculative":  (0.20, 0.30),
    "default":      (0.30, 0.50),
}

_DTE_BY_TRIGGER_TF = {
    "2H":     (0, 14),
    "4H":     (14, 30),
    "Daily":  (21, 45),
}


@dataclass
class OptionsStructure:
    strike: float
    contract_type: str   # "call" | "put"
    expiry: str          # ISO date "YYYY-MM-DD"
    dte: int
    premium: float
    delta: float | None = None
    iv_rank: float | None = None
    open_interest: int | None = None
    bid_ask_spread: float | None = None

    def to_dict(self) -> dict:
        return {
            "strike": self.strike,
            "contract_type": self.contract_type,
            "expiry": self.expiry,
            "dte": self.dte,
            "premium": self.premium,
            "delta": self.delta,
            "iv_rank": self.iv_rank,
            "open_interest": self.open_interest,
            "bid_ask_spread": self.bid_ask_spread,
        }


def compute_dte(expiry_iso: str, today: date | None = None) -> int:
    if today is None:
        today = date.today()
    expiry = datetime.strptime(expiry_iso, "%Y-%m-%d").date()
    return max((expiry - today).days, 0)


def breakeven(strike: float, premium: float, contract_type: str) -> float:
    if contract_type == "call":
        return strike + premium
    if contract_type == "put":
        return strike - premium
    raise ValueError(f"contract_type must be 'call' or 'put', got {contract_type!r}")


def iv_rank_label(iv_rank: float | None) -> str:
    if iv_rank is None:
        return "n/a"
    if iv_rank < 30:
        return "cheap"
    if iv_rank < 50:
        return "fair"
    if iv_rank < 80:
        return "elevated"
    return "expensive"


def delta_target(conviction: str) -> tuple[float, float]:
    return _DELTA_BY_CONVICTION.get(conviction.lower(), _DELTA_BY_CONVICTION["default"])


def dte_target(trigger_tf: str) -> tuple[int, int]:
    return _DTE_BY_TRIGGER_TF.get(trigger_tf, (14, 45))


@dataclass
class StructureCheck:
    delta_in_band: bool
    dte_in_band: bool
    iv_rank_label: str
    liquidity_ok: bool
    spread_pct: float | None
    notes: list[str]


def evaluate_structure(
    options: OptionsStructure,
    conviction: str,
    trigger_tf: str,
) -> StructureCheck:
    notes: list[str] = []

    d_lo, d_hi = delta_target(conviction)
    delta_in_band = options.delta is not None and d_lo <= options.delta <= d_hi
    if options.delta is not None and not delta_in_band:
        notes.append(
            f"Delta {options.delta:.2f} outside {conviction} band ({d_lo:.2f}-{d_hi:.2f})"
        )

    dte_lo, dte_hi = dte_target(trigger_tf)
    dte_in_band = dte_lo <= options.dte <= dte_hi
    if not dte_in_band:
        notes.append(
            f"DTE {options.dte} outside {trigger_tf} band ({dte_lo}-{dte_hi})"
        )

    label = iv_rank_label(options.iv_rank)
    if label in {"elevated", "expensive"}:
        notes.append(f"IV Rank {options.iv_rank}% is {label} — premium inflated")

    spread_pct: float | None = None
    liquidity_ok = True
    if options.bid_ask_spread is not None and options.premium > 0:
        spread_pct = options.bid_ask_spread / options.premium
        if spread_pct > 0.10:
            liquidity_ok = False
            notes.append(
                f"Bid-ask spread {options.bid_ask_spread:.2f} is "
                f"{spread_pct:.1%} of premium — illiquidity tax"
            )
    if options.open_interest is not None and options.open_interest < 500:
        liquidity_ok = False
        notes.append(f"OI {options.open_interest} is below the 500 minimum")

    return StructureCheck(
        delta_in_band=delta_in_band,
        dte_in_band=dte_in_band,
        iv_rank_label=label,
        liquidity_ok=liquidity_ok,
        spread_pct=spread_pct,
        notes=notes,
    )
