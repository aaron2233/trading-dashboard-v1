"""Strike-suggestion helper for the Lotto playbook.

Given a spot price + direction, returns a list of strike candidates at
the ATM nearest-rounded level + standard OTM percentage offsets. Used by
the LottoView panel to seed the kill-sheet strike field with one click.

Anti-fabrication: this module returns strike *prices only*. It does NOT
quote premium, delta, IV, or open interest — those vary per broker chain
and per moment, and live data flows through the dashboard via the
options-input pivot (paste, src/options_input/parser.py).
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Literal


# Default OTM offsets shown in the panel. ATM (0%) plus standard lottery
# distances. Lotto sizing scales down at deeper OTM — these are the
# strikes most worth surfacing.
DEFAULT_OTM_PCTS: tuple[float, ...] = (0.0, 1.0, 3.0, 5.0, 7.0, 10.0)


# Per-ticker increment overrides. Used when an underlying trades on a
# non-$1 grid. Default is $1 — true for QQQ/SPY/GLD and most equities
# typical lotto setups care about. Add more entries here as needed.
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


# ─── Black-Scholes-derived strike suggester (delta-based) ───────────────────
# Used by lotto/weekly/index-swing scanners to surface a concrete dollar
# strike on each setup card, vs the older textual "0.10-0.25 (deep OTM)"
# hints. Inputs come from the scanner's existing daily-bars pull (for HV)
# and the setup's spot close.

import math


def _norm_ppf(p: float) -> float:
    """Beasley-Springer-Moro inverse-normal CDF (matches lotto_param_sweep).
    p ∈ (0,1). Returns z such that Φ(z) = p."""
    a = [-3.969683028665376e+01,  2.209460984245205e+02,
         -2.759285104469687e+02,  1.383577518672690e+02,
         -3.066479806614716e+01,  2.506628277459239e+00]
    b = [-5.447609879822406e+01,  1.615858368580409e+02,
         -1.556989798598866e+02,  6.680131188771972e+01,
         -1.328068155288572e+01]
    c = [-7.784894002430293e-03, -3.223964580411365e-01,
         -2.400758277161838e+00, -2.549732539343734e+00,
          4.374664141464968e+00,  2.938163982698783e+00]
    d = [ 7.784695709041462e-03,  3.224671290700398e-01,
          2.445134137142996e+00,  3.754408661907416e+00]
    p_low, p_high = 0.02425, 1 - 0.02425
    if p < p_low:
        q = math.sqrt(-2 * math.log(p))
        return (((((c[0]*q+c[1])*q+c[2])*q+c[3])*q+c[4])*q+c[5]) / (
            (((d[0]*q+d[1])*q+d[2])*q+d[3])*q+1)
    if p <= p_high:
        q = p - 0.5
        r = q * q
        return (((((a[0]*r+a[1])*r+a[2])*r+a[3])*r+a[4])*r+a[5]) * q / (
            ((((b[0]*r+b[1])*r+b[2])*r+b[3])*r+b[4])*r+1)
    q = math.sqrt(-2 * math.log(1 - p))
    return -(((((c[0]*q+c[1])*q+c[2])*q+c[3])*q+c[4])*q+c[5]) / (
        (((d[0]*q+d[1])*q+d[2])*q+d[3])*q+1)


def compute_hv20_annualized(daily_closes) -> float | None:
    """Compute 20-day historical volatility, annualized (√252).

    `daily_closes` can be a list, np.array, or pandas Series of close prices.
    Returns None if fewer than 21 prices available.
    """
    try:
        # Accept pandas / numpy / list
        if hasattr(daily_closes, "values"):
            closes = list(daily_closes.values)[-21:]
        else:
            closes = list(daily_closes)[-21:]
    except Exception:
        return None
    if len(closes) < 21:
        return None
    log_returns = []
    for i in range(1, len(closes)):
        prev, curr = closes[i - 1], closes[i]
        if prev is None or curr is None or prev <= 0 or curr <= 0:
            continue
        log_returns.append(math.log(curr / prev))
    if len(log_returns) < 10:
        return None
    n = len(log_returns)
    mean = sum(log_returns) / n
    var = sum((r - mean) ** 2 for r in log_returns) / (n - 1)
    sigma_daily = math.sqrt(var)
    return sigma_daily * math.sqrt(252)


def suggest_strike_for_delta(
    spot: float,
    hv_annual: float,
    dte_days: int,
    kind: Direction,
    target_delta: float,
    *,
    ticker: str = "",
    risk_free_rate: float = 0.04,
    iv_markup: float = 0.05,
) -> float | None:
    """Return the dollar strike that targets `target_delta` magnitude OTM
    for a Black-Scholes call (or put), rounded to the ticker's strike grid.

    For a call: solves K such that N(d1) = target_delta. (Lower delta = farther OTM.)
    For a put:  solves K such that N(d1) = 1 - target_delta. (Lower delta magnitude = farther OTM.)

    `hv_annual`: 20-day historical volatility, annualized. We add `iv_markup`
    on top (IV typically runs above HV).

    Returns None when inputs are degenerate (spot ≤ 0, dte ≤ 0, hv ≤ 0).
    """
    if spot <= 0 or hv_annual <= 0 or dte_days <= 0:
        return None
    if not (0 < target_delta < 1):
        return None
    sigma = max(0.10, min(2.0, hv_annual + iv_markup))
    T = dte_days / 365.0
    drift = (risk_free_rate + 0.5 * sigma * sigma) * T
    if kind == "call":
        z = _norm_ppf(target_delta)  # negative for delta < 0.5
        # d1 = (ln(S/K) + drift) / (σ√T) = z
        # ln(K/S) = -z·σ·√T + drift  →  K = S · exp(-z·σ·√T + drift)
        K_raw = spot * math.exp(-z * sigma * math.sqrt(T) + drift)
    else:
        z = _norm_ppf(1 - target_delta)  # positive
        K_raw = spot * math.exp(-z * sigma * math.sqrt(T) + drift)
    increment = TICKER_INCREMENTS.get(ticker.upper(), 1.0)
    # For sub-$25 names, use $0.50 grid where likely
    if increment == 1.0 and spot < 25:
        increment = 0.5
    return _round_to_increment(K_raw, increment)
