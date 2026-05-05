"""Strike-suggestion helper for the Lotto playbook.

Given a spot price + direction, returns a list of strike candidates at
the ATM nearest-rounded level + standard OTM percentage offsets. Used by
the LottoView panel to seed the kill-sheet strike field with one click.

Anti-fabrication: this module returns strike *prices only*. It does NOT
quote premium, delta, IV, or open interest — those vary per broker chain
and per moment, and live data flows through the dashboard via the
options-input pivot (paste / screenshot, src/options_input/parser.py).
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Literal


# Default OTM offsets shown in the panel. ATM (0%) plus standard lottery
# distances. Aaron's lotto sizing scales down at deeper OTM — these are
# the strikes most worth surfacing.
DEFAULT_OTM_PCTS: tuple[float, ...] = (0.0, 1.0, 3.0, 5.0, 7.0, 10.0)


# Per-ticker increment overrides. Used when an underlying trades on a
# non-$1 grid. Default is $1 — true for QQQ/SPY/GLD and most equities
# Aaron's account profile cares about. Add more entries here as needed.
TICKER_INCREMENTS: dict[str, float] = {
    # ETFs — all $1 grid in liquid expirations
    "SPY": 1.0, "QQQ": 1.0, "GLD": 1.0, "IWM": 1.0, "DIA": 1.0,
    "RSP": 1.0, "TLT": 1.0, "USO": 1.0,
    # High-priced names that may use $5 strikes in some weeklies — keep
    # $1 default for v1 since most weeklies are $1; user can override.
}


Direction = Literal["call", "put"]


@dataclass
class StrikeCandidate:
    """One strike suggestion with display metadata."""
    direction: Direction
    strike: float
    pct_otm: float          # signed positive = OTM, 0 = ATM, negative = ITM
    moneyness: str          # display label: "ATM" | "1% OTM" | "3% OTM" | ...
    distance_usd: float     # |strike - spot|, signed by direction

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class StrikeSuggestionsResult:
    """Full suggestion payload for one (ticker, spot) point."""
    ticker: str
    spot: float
    bar_date: str           # the date the spot price comes from
    increment: float        # the strike grid increment used
    calls: list[StrikeCandidate] = field(default_factory=list)
    puts: list[StrikeCandidate] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "ticker": self.ticker,
            "spot": self.spot,
            "bar_date": self.bar_date,
            "increment": self.increment,
            "calls": [c.to_dict() for c in self.calls],
            "puts": [p.to_dict() for p in self.puts],
        }


def _round_to_increment(value: float, increment: float) -> float:
    """Round to the nearest multiple of `increment`. ATM strike rounds
    to nearest grid point (not floor/ceil). Tied values round up — that's
    what most option chains do too."""
    if increment <= 0:
        raise ValueError(f"increment must be > 0, got {increment}")
    return round(value / increment) * increment


def _label_pct(pct: float) -> str:
    if abs(pct) < 0.001:
        return "ATM"
    return f"{pct:.0f}% OTM"


def suggest_strikes(
    spot: float,
    *,
    direction: Direction | None = None,
    ticker: str = "",
    bar_date: str = "",
    increment: float | None = None,
    otm_pcts: tuple[float, ...] | None = None,
) -> StrikeSuggestionsResult:
    """Compute strike candidates for a given spot price.

    Args:
        spot: Latest close. Caller fetches via scan_ticker; this function
            does NOT reach out for live data — anti-fab rule.
        direction: "call" | "put" | None. None returns both lists.
        ticker: Optional. When provided, looks up TICKER_INCREMENTS for
            an override; otherwise uses the explicit `increment` arg or
            defaults to $1.
        bar_date: Date the spot is from — passed through to result for
            display ("close on 2026-05-05").
        increment: Strike grid increment ($). Defaults to TICKER_INCREMENTS
            lookup or $1.
        otm_pcts: Override the default OTM offsets. Use to widen / narrow.

    Returns:
        StrikeSuggestionsResult with calls + puts populated per `direction`.
    """
    if spot <= 0:
        raise ValueError(f"spot must be > 0, got {spot}")
    if increment is None:
        increment = TICKER_INCREMENTS.get(ticker.upper(), 1.0)
    pcts = otm_pcts if otm_pcts is not None else DEFAULT_OTM_PCTS

    calls: list[StrikeCandidate] = []
    puts: list[StrikeCandidate] = []

    if direction in (None, "call"):
        for pct in pcts:
            target = spot * (1.0 + pct / 100.0)
            strike = _round_to_increment(target, increment)
            calls.append(StrikeCandidate(
                direction="call",
                strike=float(strike),
                pct_otm=pct,
                moneyness=_label_pct(pct),
                distance_usd=float(strike - spot),
            ))

    if direction in (None, "put"):
        for pct in pcts:
            target = spot * (1.0 - pct / 100.0)
            strike = _round_to_increment(target, increment)
            puts.append(StrikeCandidate(
                direction="put",
                strike=float(strike),
                pct_otm=pct,
                moneyness=_label_pct(pct),
                distance_usd=float(strike - spot),
            ))

    return StrikeSuggestionsResult(
        ticker=ticker.upper(),
        spot=float(spot),
        bar_date=bar_date,
        increment=float(increment),
        calls=calls,
        puts=puts,
    )
