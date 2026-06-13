"""Per-position alert engine.

Evaluates each open position against the latest scan and emits alerts when:
  - DTE crosses an account-specific threshold
  - Underlying close hits target or invalidation
  - Stochastic signal flips against the position direction
  - MA Ribbon stack flips against the position direction (or goes chop)

Severity ladder:
  - "action" — exit decision warranted now
  - "warn"   — material concern; review before next session
  - "info"   — soft heads-up
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import Any, Callable

from kill_sheet.options import compute_dte
from positions.model import Position


_BULL_STACKS = {"full_bull", "bull_developing"}
_BEAR_STACKS = {"full_bear", "bear_developing"}
_BULL_SIGNALS = {"bull_cross_oversold", "bull_continuation", "bullish_divergence"}
_BEAR_SIGNALS = {"bear_cross_overbought", "bear_continuation", "bearish_divergence"}

_SEVERITY_ORDER = {"action": 0, "warn": 1, "info": 2}


def _is_index_swing_position(position: Position) -> bool:
    """Index-swing positions are tagged with skill='index-swing'.
    Account is typically 'main' (no dedicated index-swing account)."""
    return getattr(position, "skill", None) == "index-swing"


@dataclass
class PositionAlert:
    position_id: str
    ticker: str
    severity: str
    rule: str
    message: str
    details: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "position_id": self.position_id,
            "ticker": self.ticker,
            "severity": self.severity,
            "rule": self.rule,
            "message": self.message,
            "details": self.details,
        }


def _dte_alerts(position: Position, today: date | None) -> list[PositionAlert]:
    if not position.expiry or position.instrument == "shares":
        return []
    dte = compute_dte(position.expiry, today=today)
    out: list[PositionAlert] = []

    if dte == 0:
        out.append(PositionAlert(
            position_id=position.id, ticker=position.ticker,
            severity="action", rule="dte_expired",
            message="Expiry today — exercise, close, or it expires worthless.",
            details={"dte": dte},
        ))
        return out

    if position.account_key == "weekly":
        # weekly-trend-trader: 60-DTE hard floor, 90-DTE roll alert
        # [src: ~/CLAUDE.md weekly account rule "Never hold below 60 DTE"]
        if dte < 60:
            out.append(PositionAlert(
                position_id=position.id, ticker=position.ticker,
                severity="action", rule="dte_60_floor",
                message=f"{dte} DTE below the weekly 60-DTE floor — roll or close.",
                details={"dte": dte, "threshold": 60},
            ))
        elif dte < 90:
            out.append(PositionAlert(
                position_id=position.id, ticker=position.ticker,
                severity="warn", rule="dte_90_warn",
                message=f"{dte} DTE — roll alert (weekly approaching 60-DTE floor).",
                details={"dte": dte, "threshold": 90},
            ))
    elif _is_index_swing_position(position):
        # index-swing: 21-DTE hard floor, 30-DTE roll alert
        # [src: ~/.claude/skills/user/index-swing/SKILL.md anti-pattern
        #  "Never hold options below 60 DTE" → "Never hold below 21 DTE"
        #  for the swing horizon. 30-60 DTE entry, 21 DTE exit.]
        if dte < 21:
            out.append(PositionAlert(
                position_id=position.id, ticker=position.ticker,
                severity="action", rule="dte_21_floor",
                message=f"{dte} DTE below the index-swing 21-DTE floor — close or roll.",
                details={"dte": dte, "threshold": 21},
            ))
        elif dte < 30:
            out.append(PositionAlert(
                position_id=position.id, ticker=position.ticker,
                severity="warn", rule="dte_30_warn",
                message=f"{dte} DTE — roll alert (index-swing approaching 21-DTE floor).",
                details={"dte": dte, "threshold": 30},
            ))
    elif position.account_key == "lotto":
        # lotto runs 5-14 DTE; 0-2 DTE remaining = action
        if dte <= 2:
            out.append(PositionAlert(
                position_id=position.id, ticker=position.ticker,
                severity="action", rule="lotto_dte_critical",
                message=f"{dte} DTE — lotto position about to expire; act or accept.",
                details={"dte": dte},
            ))
    else:
        # main account: 14-DTE warn, 7-DTE action
        if dte <= 7:
            out.append(PositionAlert(
                position_id=position.id, ticker=position.ticker,
                severity="action", rule="dte_low",
                message=f"{dte} DTE — gamma rising and theta burn accelerating.",
                details={"dte": dte},
            ))
        elif dte <= 14:
            out.append(PositionAlert(
                position_id=position.id, ticker=position.ticker,
                severity="warn", rule="dte_warn",
                message=f"{dte} DTE — time-stop window approaching.",
                details={"dte": dte},
            ))
    return out


def _price_alerts(position: Position, scan_row: dict[str, Any]) -> list[PositionAlert]:
    close = scan_row.get("close")
    if close is None:
        return []

    out: list[PositionAlert] = []
    thesis = position.thesis_direction

    if position.target_price is not None:
        if thesis == "bullish" and close >= position.target_price:
            out.append(PositionAlert(
                position_id=position.id, ticker=position.ticker,
                severity="action", rule="target_hit",
                message=f"Underlying ${close:,.2f} ≥ target ${position.target_price:,.2f} "
                        f"— take 50% per exit plan.",
                details={"close": close, "target": position.target_price},
            ))
        elif thesis == "bearish" and close <= position.target_price:
            out.append(PositionAlert(
                position_id=position.id, ticker=position.ticker,
                severity="action", rule="target_hit",
                message=f"Underlying ${close:,.2f} ≤ target ${position.target_price:,.2f} "
                        f"— take 50% per exit plan.",
                details={"close": close, "target": position.target_price},
            ))

    if position.invalidation_price is not None:
        if thesis == "bullish" and close <= position.invalidation_price:
            out.append(PositionAlert(
                position_id=position.id, ticker=position.ticker,
                severity="action", rule="invalidation_hit",
                message=f"Underlying ${close:,.2f} ≤ invalidation ${position.invalidation_price:,.2f} "
                        f"— thesis broken, exit.",
                details={"close": close, "invalidation": position.invalidation_price},
            ))
        elif thesis == "bearish" and close >= position.invalidation_price:
            out.append(PositionAlert(
                position_id=position.id, ticker=position.ticker,
                severity="action", rule="invalidation_hit",
                message=f"Underlying ${close:,.2f} ≥ invalidation ${position.invalidation_price:,.2f} "
                        f"— thesis broken, exit.",
                details={"close": close, "invalidation": position.invalidation_price},
            ))

    return out


def _technical_alerts(position: Position, scan_row: dict[str, Any]) -> list[PositionAlert]:
    out: list[PositionAlert] = []
    # Portfolio sleeve is a multi-quarter thematic hold that exits on a
    # thesis-break PRICE level (handled by _price_alerts), NOT on daily MA
    # flips — firing "thesis structurally broken" on every daily ribbon wobble
    # contradicts the sleeve's design (~/CLAUDE.md long-term sleeve). Skip the
    # technical (ma_flip) alerts here; keep the price-based invalidation/target.
    if position.account_key == "portfolio":
        return out
    thesis = position.thesis_direction

    stack = (scan_row.get("ma_ribbon") or {}).get("stack_state")
    signal = (scan_row.get("stochastic") or {}).get("signal")

    # MA flip is a structural break in the trend
    if stack:
        if thesis == "bullish" and stack in _BEAR_STACKS:
            out.append(PositionAlert(
                position_id=position.id, ticker=position.ticker,
                severity="action", rule="ma_flip",
                message=f"Daily stack flipped to {stack} — bullish thesis structurally broken.",
                details={"stack": stack},
            ))
        elif thesis == "bearish" and stack in _BULL_STACKS:
            out.append(PositionAlert(
                position_id=position.id, ticker=position.ticker,
                severity="action", rule="ma_flip",
                message=f"Daily stack flipped to {stack} — bearish thesis structurally broken.",
                details={"stack": stack},
            ))
        elif stack == "chop":
            out.append(PositionAlert(
                position_id=position.id, ticker=position.ticker,
                severity="warn", rule="ma_chop",
                message="Daily stack went chop — trend dissolving, tighten stop.",
                details={"stack": stack},
            ))

    # Stoch reversal is timing-level
    if signal:
        if thesis == "bullish" and signal in _BEAR_SIGNALS:
            out.append(PositionAlert(
                position_id=position.id, ticker=position.ticker,
                severity="warn", rule="stoch_reversal",
                message=f"Stochastic fired {signal} against the bullish thesis.",
                details={"signal": signal},
            ))
        elif thesis == "bearish" and signal in _BULL_SIGNALS:
            out.append(PositionAlert(
                position_id=position.id, ticker=position.ticker,
                severity="warn", rule="stoch_reversal",
                message=f"Stochastic fired {signal} against the bearish thesis.",
                details={"signal": signal},
            ))

    return out


def _weekly_trail_alerts(
    position: Position,
    weekly_scan_row: dict[str, Any] | None,
) -> list[PositionAlert]:
    """10 WMA trailing-stop alert for weekly-account positions.

    Per ~/.claude/skills/user/weekly-trend-trader/SKILL.md exit plan:
        Trail using the 10 WMA. Close when weekly candle closes below 10 WMA
        (longs) or above 10 WMA (shorts).

    Fires once the most recent weekly close breaches the trail. Does NOT
    fire on shares positions (sub-$5 penny stocks use share-based stops via
    invalidation_price). Does NOT fire when weekly_scan_row is missing.
    """
    if position.account_key != "weekly" or weekly_scan_row is None:
        return []
    if "error" in weekly_scan_row:
        return []
    close = weekly_scan_row.get("close")
    ma_10 = (weekly_scan_row.get("ma_ribbon") or {}).get("ma_10")
    if close is None or ma_10 is None:
        return []

    thesis = position.thesis_direction
    if thesis == "bullish" and close < ma_10:
        return [PositionAlert(
            position_id=position.id, ticker=position.ticker,
            severity="action", rule="weekly_10wma_trail_break",
            message=(
                f"Weekly close ${close:,.2f} below 10 WMA ${ma_10:,.2f} — "
                "trail stop hit. Close per skill exit plan."
            ),
            details={"weekly_close": close, "ma_10_wma": ma_10},
        )]
    if thesis == "bearish" and close > ma_10:
        return [PositionAlert(
            position_id=position.id, ticker=position.ticker,
            severity="action", rule="weekly_10wma_trail_break",
            message=(
                f"Weekly close ${close:,.2f} above 10 WMA ${ma_10:,.2f} — "
                "bearish trail stop hit. Cover per skill exit plan."
            ),
            details={"weekly_close": close, "ma_10_wma": ma_10},
        )]
    return []


def evaluate_alerts(
    position: Position,
    scan_row: dict[str, Any],
    weekly_scan_row: dict[str, Any] | None = None,
    today: date | None = None,
) -> list[PositionAlert]:
    """All alert rules for one position against the latest scan.

    `weekly_scan_row` is the position's ticker scanned at the weekly TF —
    used by the 10 WMA trailing-stop alert (weekly account only). Optional;
    weekly trail alert is silently skipped when absent.
    """
    if position.status != "open":
        return []
    return [
        *_dte_alerts(position, today=today),
        *_price_alerts(position, scan_row),
        *_technical_alerts(position, scan_row),
        *_weekly_trail_alerts(position, weekly_scan_row),
    ]


def evaluate_all_open(
    store,
    scan_fn: Callable[..., dict[str, Any]] | None = None,
    today: date | None = None,
) -> dict[str, list[PositionAlert]]:
    """Evaluate alerts for every open position, deduping scan calls per ticker.

    For weekly-account positions, additionally fetches a weekly-TF scan to
    drive the 10 WMA trailing-stop check. Scan failures degrade gracefully
    to an info-level scan_error alert per position.
    """
    if scan_fn is None:
        from scan import scan_ticker as _scan
        scan_fn = _scan

    open_positions = store.list_open()
    if not open_positions:
        return {}

    tickers = sorted({p.ticker for p in open_positions})
    weekly_tickers = sorted({
        p.ticker for p in open_positions if p.account_key == "weekly"
    })
    scans: dict[str, dict[str, Any]] = {}
    weekly_scans: dict[str, dict[str, Any]] = {}
    for t in tickers:
        try:
            scans[t] = scan_fn(t)
        except Exception as exc:
            scans[t] = {"ticker": t, "error": str(exc)}
    for t in weekly_tickers:
        try:
            weekly_scans[t] = scan_fn(t, timeframe="1wk")
        except Exception as exc:
            weekly_scans[t] = {"ticker": t, "error": str(exc)}

    out: dict[str, list[PositionAlert]] = {}
    for p in open_positions:
        row = scans.get(p.ticker, {"error": "ticker not scanned"})
        weekly_row = weekly_scans.get(p.ticker) if p.account_key == "weekly" else None
        if "error" in row:
            out[p.id] = [PositionAlert(
                position_id=p.id, ticker=p.ticker,
                severity="info", rule="scan_error",
                message=f"Could not fetch latest data: {row['error']}",
            )]
            continue
        out[p.id] = evaluate_alerts(p, row, weekly_scan_row=weekly_row, today=today)
    return out


def sort_alerts(alerts: list[PositionAlert]) -> list[PositionAlert]:
    return sorted(alerts, key=lambda a: (_SEVERITY_ORDER.get(a.severity, 99), a.ticker, a.rule))
