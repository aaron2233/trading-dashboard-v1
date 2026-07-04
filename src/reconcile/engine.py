"""Diff broker fills against journal positions.

Matching is by contract identity — (ticker, kind, strike, expiry) for
options, (ticker, "shares") for shares — across ALL account sleeves,
because the broker sees one account while main/lotto/portfolio are
journal-side labels.

Journal-side gaps (journal_only) are only flagged for positions entered
inside the CSV's date window: a report covering June can't say anything
about a March trade. Broker-side findings (ghost_trade, stale_open) are
always in scope because the fills themselves are in the window.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, timedelta

from positions.model import Position
from reconcile.robinhood_csv import Fill, SHORT_SIDE_CODES

# A position is "entered in the window" with this much slack on each
# edge — entry_date records when the trade was LOGGED, which can lag the
# fill by a day or two. Slack widens the window so a late-logged trade
# still counts as covered by the CSV.
WINDOW_SLACK_DAYS = 3

HIGH = "high"
MEDIUM = "medium"
INFO = "info"


@dataclass
class Finding:
    category: str    # ghost_trade | stale_open | qty_mismatch | journal_only | short_side_code
    severity: str    # high | medium | info
    contract: str    # human-readable contract identity
    detail: str
    position_ids: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "category": self.category,
            "severity": self.severity,
            "contract": self.contract,
            "detail": self.detail,
            "position_ids": self.position_ids,
        }


@dataclass
class ReconcileReport:
    window_start: str | None
    window_end: str | None
    fills_count: int
    findings: list[Finding] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)  # parser warnings passed through

    @property
    def has_high_severity(self) -> bool:
        return any(f.severity == HIGH for f in self.findings)

    def to_dict(self) -> dict:
        return {
            "window_start": self.window_start,
            "window_end": self.window_end,
            "fills_count": self.fills_count,
            "findings": [f.to_dict() for f in self.findings],
            "warnings": self.warnings,
        }


def _normalize_expiry(expiry: str | None) -> str | None:
    """Zero-pad Y-M-D so hand-entered '2026-7-17' matches '2026-07-17'."""
    if not expiry:
        return expiry
    parts = expiry.strip().split("-")
    if len(parts) != 3:
        return expiry
    try:
        y, m, d = (int(x) for x in parts)
    except ValueError:
        return expiry
    return f"{y:04d}-{m:02d}-{d:02d}"


def _contract_key(ticker: str, kind: str, strike: float | None,
                  expiry: str | None) -> tuple:
    if kind == "shares":
        return (ticker.upper(), "shares", None, None)
    return (ticker.upper(), kind.lower(), strike, _normalize_expiry(expiry))


def _position_key(p: Position) -> tuple:
    return _contract_key(p.ticker or "", p.instrument or "", p.strike, p.expiry)


def _contract_label(key: tuple) -> str:
    ticker, kind, strike, expiry = key
    if kind == "shares":
        return f"{ticker} shares"
    return f"{ticker} {expiry} {kind} ${strike:g}"


def _original_size(p: Position) -> float:
    """Contracts (or shares) at entry, before any partial exits."""
    if (p.instrument or "").lower() == "shares":
        return float(p.shares or 0)
    closed_in_partials = sum(
        float(leg.get("contracts_closed") or 0) for leg in (p.partial_exits or [])
    )
    return float(p.contracts or 0) + closed_in_partials


def _entry_day(p: Position) -> date | None:
    raw = (p.entry_date or "")[:10]
    try:
        return date.fromisoformat(raw)
    except ValueError:
        return None


def reconcile(fills: list[Fill], positions: list[Position],
              parser_warnings: list[str] | None = None) -> ReconcileReport:
    if not fills:
        return ReconcileReport(
            window_start=None, window_end=None, fills_count=0,
            findings=[], warnings=list(parser_warnings or []),
        )

    window_start = min(f.date for f in fills)
    window_end = max(f.date for f in fills)
    slack = timedelta(days=WINDOW_SLACK_DAYS)
    in_window_lo = date.fromisoformat(window_start) - slack
    in_window_hi = date.fromisoformat(window_end) + slack

    report = ReconcileReport(
        window_start=window_start, window_end=window_end,
        fills_count=len(fills), warnings=list(parser_warnings or []),
    )

    # Group broker fills by contract.
    groups: dict[tuple, dict] = {}
    for f in fills:
        key = _contract_key(f.ticker, f.kind, f.strike, f.expiry)
        g = groups.setdefault(key, {
            "opened": 0.0, "closed": 0.0, "close_all": False,
            "short_codes": set(),
        })
        if f.code in SHORT_SIDE_CODES:
            g["short_codes"].add(f.code)
        if f.action == "open":
            g["opened"] += f.quantity
        else:
            g["closed"] += f.quantity
            if f.quantity == 0:  # OEXP row with no quantity
                g["close_all"] = True

    # Index journal positions by contract.
    by_key: dict[tuple, list[Position]] = {}
    for p in positions:
        by_key.setdefault(_position_key(p), []).append(p)

    def entered_in_window(p: Position) -> bool:
        d = _entry_day(p)
        return d is not None and in_window_lo <= d <= in_window_hi

    for key, g in sorted(groups.items(), key=lambda kv: kv[0]):
        label = _contract_label(key)
        matched = by_key.get(key, [])

        if g["short_codes"]:
            report.findings.append(Finding(
                category="short_side_code", severity=HIGH, contract=label,
                detail=(
                    f"broker shows short-side/assignment code(s) "
                    f"{sorted(g['short_codes'])} — this book is long "
                    f"calls/puts only; investigate before reconciling"
                ),
                position_ids=[p.id for p in matched],
            ))

        if not matched:
            if g["opened"] == 0 and g["closed"] == 0 and g["close_all"]:
                activity = "an expiration (OEXP)"
            else:
                activity = f"{g['opened']:g} opened / {g['closed']:g} closed"
            report.findings.append(Finding(
                category="ghost_trade", severity=HIGH, contract=label,
                detail=(
                    f"broker shows {activity} between {window_start} and "
                    f"{window_end}, but no journal position exists for "
                    f"this contract — backfill it (kill sheet + scorecard)"
                ),
            ))
            continue

        fully_closed_at_broker = (
            g["opened"] > 0 and (g["close_all"] or g["closed"] >= g["opened"])
        )
        still_open = [p for p in matched if p.status == "open"]
        if fully_closed_at_broker and still_open:
            report.findings.append(Finding(
                category="stale_open", severity=HIGH, contract=label,
                detail=(
                    f"broker shows this contract fully closed "
                    f"({g['closed']:g} of {g['opened']:g}"
                    f"{', incl. expiration' if g['close_all'] else ''}) but "
                    f"the journal still has it open — log the close"
                ),
                position_ids=[p.id for p in still_open],
            ))

        in_window = [p for p in matched if entered_in_window(p)]
        if g["opened"] > 0:
            journal_opened = sum(_original_size(p) for p in in_window)
            if in_window and journal_opened != g["opened"]:
                report.findings.append(Finding(
                    category="qty_mismatch", severity=MEDIUM, contract=label,
                    detail=(
                        f"broker opened {g['opened']:g} in the window but the "
                        f"journal logged {journal_opened:g} — a lot was "
                        f"over- or under-logged"
                    ),
                    position_ids=[p.id for p in in_window],
                ))
            elif not in_window:
                report.findings.append(Finding(
                    category="qty_mismatch", severity=MEDIUM, contract=label,
                    detail=(
                        f"broker opened {g['opened']:g} in the window but the "
                        f"only journal position(s) for this contract predate "
                        f"it — an add-on lot may be unlogged"
                    ),
                    position_ids=[p.id for p in matched],
                ))

    # Journal positions entered inside the window with no broker fills.
    for key, plist in sorted(by_key.items(), key=lambda kv: kv[0]):
        if key in groups:
            continue
        orphans = [p for p in plist if entered_in_window(p)]
        if orphans:
            report.findings.append(Finding(
                category="journal_only", severity=MEDIUM,
                contract=_contract_label(key),
                detail=(
                    f"journal has {len(orphans)} position(s) entered in the "
                    f"window but the broker CSV shows no fills for this "
                    f"contract — mislogged contract details, or a different "
                    f"broker/account"
                ),
                position_ids=[p.id for p in orphans],
            ))

    return report
