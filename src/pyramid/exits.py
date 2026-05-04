"""Exit cascade evaluation for an active pyramid.

Per ~/.claude/skills/user/trend-pyramid/SKILL.md, exit conditions operate on
the AGGREGATE position. Any one fires a partial or full exit:

LONG:
  | SQN(100) → Neutral             | Trim 33%, tighten trail to 20MA |
  | SQN(100) → Bear/Strong Bear    | Close entire position           |
  | Daily close < 50MA             | Trim 50%, hard stop at 200MA    |
  | Daily close < 200MA            | Close entire position           |
  | Stoch >80 with bearish div     | Trim 33% (action)               |
  | Stoch >80 alone                | Watch — no auto-trim (warn)     |
  | LEAPS reach 120 DTE            | Roll out                        |
  | LEAPS reach 90 DTE             | Hard close                      |

SHORT (mirrored): SQN(100) → Bull triggers full exit; Daily close > 50MA
trims 50%; etc.

This module returns a list of ExitDirective entries. The caller decides which
to act on (a fully populated CLI/UI surfaces all of them; the trader chooses).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any, Literal


ExitAction = Literal[
    "hold",
    "trim_33",
    "trim_50",
    "full_exit",
    "tighten_trail_20ma",
    "set_hard_stop_200ma",
    "roll_leaps",
    "hard_close_leaps",
]


@dataclass
class ExitDirective:
    action: ExitAction
    reason: str
    severity: Literal["info", "warn", "action"]
    affected_tranches: list[int] = field(default_factory=list)
    # Economic context for LEAPS roll/close directives. None for non-LEAPS
    # directives. Per anti-fabrication rule we do NOT estimate a roll cost
    # here — the user pulls a live brokerage quote (paste / screenshot via
    # options input UI) and feeds the actual numbers into the new kill sheet.
    cost_basis_per_unit: float | None = None
    quantity: int | None = None
    current_exposure_usd: float | None = None
    strike: float | None = None
    expiry: str | None = None
    dte: int | None = None


def _dte_from_expiry(expiry: str | None) -> int | None:
    if not expiry:
        return None
    try:
        exp = datetime.fromisoformat(expiry).date()
    except ValueError:
        try:
            exp = datetime.strptime(expiry, "%Y-%m-%d").date()
        except ValueError:
            return None
    return (exp - date.today()).days


def evaluate_exits(
    direction: str,
    *,
    sqn_100_regime: str | None,
    sqn_20_value: float | None,
    stoch_k: float | None,
    close: float | None,
    ma_50: float | None,
    ma_200: float | None,
    leaps_expiries: list[tuple[int, str]] | None = None,
    leaps_tranches: list[Any] | None = None,
    bearish_divergence: Any | None = None,
    bullish_divergence: Any | None = None,
) -> list[ExitDirective]:
    """Return all firing exit directives. Caller filters/orders by severity.

    leaps_expiries: legacy — list of (tranche_id, expiry ISO date) for LEAPS
        the user holds. Produces directives with empty economic context.
    leaps_tranches: preferred — list of Tranche objects for filled LEAPS
        positions. Produces directives that surface cost basis, quantity, and
        $ exposure per directive so the user knows the scale of the roll
        without fabricating a price.
    bearish_divergence / bullish_divergence: Optional DivergenceResult from
        pyramid.divergence — when supplied with `confirmed=True`, the Stoch
        overbought/oversold trim escalates from warn to action severity. The
        type is loosely-bound (Any) so this module stays import-light.
    """
    directives: list[ExitDirective] = []

    if direction == "long":
        # SQN(100) → Neutral
        if sqn_100_regime == "neutral":
            directives.append(ExitDirective(
                action="trim_33",
                reason="SQN(100) downgraded to Neutral — trim 33% and tighten trail to 20MA",
                severity="action",
            ))
            directives.append(ExitDirective(
                action="tighten_trail_20ma",
                reason="SQN(100) Neutral — trail moves to 20MA close",
                severity="action",
            ))

        # SQN(100) → Bear or Strong Bear
        if sqn_100_regime in ("bear", "strong_bear"):
            directives.append(ExitDirective(
                action="full_exit",
                reason=f"SQN(100) flipped to {sqn_100_regime} — close entire long pyramid",
                severity="action",
            ))

        # Daily close < 50MA
        if close is not None and ma_50 is not None and close < ma_50:
            directives.append(ExitDirective(
                action="trim_50",
                reason=f"Daily close {close:.2f} below 50MA {ma_50:.2f} — trim 50%",
                severity="action",
            ))
            directives.append(ExitDirective(
                action="set_hard_stop_200ma",
                reason="50MA broken — set hard stop on remainder at 200MA",
                severity="warn",
            ))

        # Daily close < 200MA
        if close is not None and ma_200 is not None and close < ma_200:
            directives.append(ExitDirective(
                action="full_exit",
                reason=f"Daily close {close:.2f} below 200MA {ma_200:.2f} — close entire position",
                severity="action",
            ))

        # Stoch >80 — escalates to action when bearish divergence is confirmed.
        # Without divergence, fire warn-only ("watch for it") so we don't
        # auto-trim on a single overbought reading inside a strong trend.
        if stoch_k is not None and stoch_k > 80:
            div_confirmed = (
                bearish_divergence is not None
                and getattr(bearish_divergence, "confirmed", False)
            )
            if div_confirmed:
                div_note = getattr(bearish_divergence, "note", "")
                directives.append(ExitDirective(
                    action="trim_33",
                    reason=(
                        f"Stoch %K={stoch_k:.1f} >80 with confirmed bearish divergence "
                        f"({div_note}) — trim 33%"
                    ),
                    severity="action",
                ))
            else:
                div_note = (
                    getattr(bearish_divergence, "note", "")
                    if bearish_divergence is not None
                    else "no divergence read available"
                )
                directives.append(ExitDirective(
                    action="trim_33",
                    reason=(
                        f"Stoch %K={stoch_k:.1f} >80 — overbought, no confirmed bearish "
                        f"divergence ({div_note}). Watch for divergence; do not auto-trim."
                    ),
                    severity="warn",
                ))

    else:  # short
        if sqn_100_regime == "neutral":
            directives.append(ExitDirective(
                action="trim_33",
                reason="SQN(100) up to Neutral — trim 33% and tighten trail to 20MA",
                severity="action",
            ))
            directives.append(ExitDirective(
                action="tighten_trail_20ma",
                reason="SQN(100) Neutral — trail moves to 20MA close",
                severity="action",
            ))

        if sqn_100_regime in ("bull", "strong_bull"):
            directives.append(ExitDirective(
                action="full_exit",
                reason=f"SQN(100) flipped to {sqn_100_regime} — close entire short pyramid",
                severity="action",
            ))

        if close is not None and ma_50 is not None and close > ma_50:
            directives.append(ExitDirective(
                action="trim_50",
                reason=f"Daily close {close:.2f} above 50MA {ma_50:.2f} — trim 50%",
                severity="action",
            ))
            directives.append(ExitDirective(
                action="set_hard_stop_200ma",
                reason="50MA broken — set hard stop on remainder at 200MA",
                severity="warn",
            ))

        if close is not None and ma_200 is not None and close > ma_200:
            directives.append(ExitDirective(
                action="full_exit",
                reason=f"Daily close {close:.2f} above 200MA {ma_200:.2f} — close entire short",
                severity="action",
            ))

        if stoch_k is not None and stoch_k < 20:
            div_confirmed = (
                bullish_divergence is not None
                and getattr(bullish_divergence, "confirmed", False)
            )
            if div_confirmed:
                div_note = getattr(bullish_divergence, "note", "")
                directives.append(ExitDirective(
                    action="trim_33",
                    reason=(
                        f"Stoch %K={stoch_k:.1f} <20 with confirmed bullish divergence "
                        f"({div_note}) — trim 33%"
                    ),
                    severity="action",
                ))
            else:
                div_note = (
                    getattr(bullish_divergence, "note", "")
                    if bullish_divergence is not None
                    else "no divergence read available"
                )
                directives.append(ExitDirective(
                    action="trim_33",
                    reason=(
                        f"Stoch %K={stoch_k:.1f} <20 — oversold, no confirmed bullish "
                        f"divergence ({div_note}). Watch for divergence; do not auto-trim."
                    ),
                    severity="warn",
                ))

    # ── LEAPS roll calendar (direction-agnostic) ─────────────────────────────
    # Two input shapes supported. `leaps_tranches` is preferred — it surfaces
    # the held-position economics in the directive so the user knows the
    # scale of the roll. `leaps_expiries` is the legacy (id, expiry) form
    # and produces directives without economic context.
    leaps_payload: list[dict[str, Any]] = []
    if leaps_tranches:
        for tr in leaps_tranches:
            if not getattr(tr, "expiry", None):
                continue
            qty = getattr(tr, "quantity", None)
            cost = getattr(tr, "cost_basis_per_unit", None)
            # LEAPS premium is per share; 1 contract = 100 shares
            exposure = (
                cost * qty * 100 if (qty is not None and cost is not None) else None
            )
            leaps_payload.append({
                "id": tr.id,
                "expiry": tr.expiry,
                "cost_basis_per_unit": cost,
                "quantity": qty,
                "current_exposure_usd": exposure,
                "strike": getattr(tr, "strike", None),
            })
    elif leaps_expiries:
        for tranche_id, expiry in leaps_expiries:
            leaps_payload.append({
                "id": tranche_id,
                "expiry": expiry,
                "cost_basis_per_unit": None,
                "quantity": None,
                "current_exposure_usd": None,
                "strike": None,
            })

    for entry in leaps_payload:
        dte = _dte_from_expiry(entry["expiry"])
        if dte is None:
            continue
        # Build the economics suffix when we have it
        econ_parts: list[str] = []
        if entry.get("strike") is not None:
            econ_parts.append(f"strike ${entry['strike']:g}")
        if entry.get("quantity") is not None:
            econ_parts.append(f"{entry['quantity']}× contracts")
        if entry.get("cost_basis_per_unit") is not None:
            econ_parts.append(f"@ ${entry['cost_basis_per_unit']:.2f}/sh basis")
        if entry.get("current_exposure_usd") is not None:
            econ_parts.append(f"= ${entry['current_exposure_usd']:,.0f} exposure")
        econ_suffix = (" — " + ", ".join(econ_parts)) if econ_parts else ""
        roll_quote_hint = (
            " Pull live roll quote from brokerage (paste / screenshot via "
            "Kill Sheet → Options input) to size the roll."
            if entry.get("current_exposure_usd") is not None
            else ""
        )

        common_kwargs = dict(
            affected_tranches=[entry["id"]],
            cost_basis_per_unit=entry.get("cost_basis_per_unit"),
            quantity=entry.get("quantity"),
            current_exposure_usd=entry.get("current_exposure_usd"),
            strike=entry.get("strike"),
            expiry=entry["expiry"],
            dte=dte,
        )

        if dte <= 90:
            directives.append(ExitDirective(
                action="hard_close_leaps",
                reason=(
                    f"T{entry['id']} LEAPS at {dte} DTE — hard close (rule: never "
                    f"hold below 90 DTE){econ_suffix}.{roll_quote_hint}"
                ),
                severity="action",
                **common_kwargs,
            ))
        elif dte <= 120:
            directives.append(ExitDirective(
                action="roll_leaps",
                reason=(
                    f"T{entry['id']} LEAPS at {dte} DTE — roll out to maintain "
                    f"horizon (≤120 DTE trigger){econ_suffix}.{roll_quote_hint}"
                ),
                severity="action",
                **common_kwargs,
            ))
        elif dte <= 150:
            directives.append(ExitDirective(
                action="roll_leaps",
                reason=(
                    f"T{entry['id']} LEAPS at {dte} DTE — 150-DTE warning, plan "
                    f"roll within 30 days{econ_suffix}.{roll_quote_hint}"
                ),
                severity="warn",
                **common_kwargs,
            ))

    if not directives:
        directives.append(ExitDirective(
            action="hold",
            reason="No exit conditions firing — hold per plan",
            severity="info",
        ))

    return directives
