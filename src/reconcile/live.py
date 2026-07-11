"""Live-state reconcile — diff current broker OPEN positions against the journal.

Input is a snapshot JSON written by the local tripwire (a headless session
calling the read-only robinhood-trading MCP), shape:

    {
      "source": "robinhood-mcp",
      "fetched_at": "2026-07-11T20:15:00Z",
      "account": "…4907",                    # masked — never the full number
      "option_positions":   [ raw get_option_positions rows ],
      "option_instruments": [ raw get_option_instruments rows ],
      "equity_positions":   [ raw get_equity_positions rows ]
    }

Option rows carry quantity/expiry but NOT strike or call/put — those come
from the matching instrument row (join position.option_id == instrument.id).

Unlike the fills reconcile (engine.py), this is a state-vs-state compare:
every broker open position must have a matching open journal position and
vice versa, by the same contract identity — (ticker, kind, strike, expiry)
for options, (ticker, "shares") for shares — across all account sleeves.

Read-only by design, same as the fills reconcile: findings are flagged,
never auto-imported. An unlogged open is scored as a rule violation, not
a deviation (orchestrator "Off-Book Trades & Deviations").
"""
from __future__ import annotations

from positions.model import Position
from reconcile.engine import (
    HIGH,
    MEDIUM,
    Finding,
    ReconcileReport,
    _contract_key,
    _contract_label,
)


class LiveSnapshotError(ValueError):
    """Snapshot JSON is missing required sections or is malformed."""


def _current_size(p: Position) -> float:
    """Open size right now (contracts already reflect partial exits)."""
    if (p.instrument or "").lower() == "shares":
        return float(p.shares or 0)
    return float(p.contracts or 0)


def _normalize_broker_opens(snapshot: dict) -> tuple[dict[tuple, float], list[str]]:
    """Raw MCP rows → {contract_key: quantity}. Returns (opens, warnings)."""
    for section in ("option_positions", "option_instruments", "equity_positions"):
        if not isinstance(snapshot.get(section), list):
            raise LiveSnapshotError(f"snapshot missing list section: {section}")

    warnings: list[str] = []
    instruments = {
        row.get("id"): row for row in snapshot["option_instruments"]
    }
    opens: dict[tuple, float] = {}

    for row in snapshot["option_positions"]:
        qty = float(row.get("quantity") or 0)
        if qty == 0:
            continue
        symbol = row.get("chain_symbol") or "?"
        if (row.get("type") or "long") != "long":
            warnings.append(
                f"{symbol}: broker reports a SHORT option position — "
                "not possible in this cash account, check manually"
            )
        inst = instruments.get(row.get("option_id"))
        if inst is None:
            warnings.append(
                f"{symbol} (option_id {row.get('option_id')}): no matching "
                "instrument row in snapshot — cannot resolve strike/type, "
                "position skipped; re-run the fetch"
            )
            continue
        key = _contract_key(
            symbol,
            inst.get("type") or "?",
            float(inst["strike_price"]) if inst.get("strike_price") else None,
            inst.get("expiration_date"),
        )
        opens[key] = opens.get(key, 0) + qty

    for row in snapshot["equity_positions"]:
        qty = float(row.get("quantity") or 0)
        if qty == 0:
            continue
        key = _contract_key(row.get("symbol") or "?", "shares", None, None)
        opens[key] = opens.get(key, 0) + qty

    return opens, warnings


def live_reconcile(snapshot: dict, positions: list[Position]) -> ReconcileReport:
    """Compare broker open positions against journal open positions."""
    broker_opens, warnings = _normalize_broker_opens(snapshot)

    journal_opens: dict[tuple, list[Position]] = {}
    for p in positions:
        if p.status != "open":
            continue
        key = _contract_key(p.ticker or "", p.instrument or "", p.strike, p.expiry)
        journal_opens.setdefault(key, []).append(p)

    findings: list[Finding] = []

    for key, qty in sorted(broker_opens.items()):
        matched = journal_opens.get(key)
        if not matched:
            findings.append(Finding(
                category="unlogged_open",
                severity=HIGH,
                contract=_contract_label(key),
                detail=(
                    f"open at broker (qty {qty:g}) with no open journal "
                    "position — off-book trade, log it and score the violation"
                ),
            ))
            continue
        journal_qty = sum(_current_size(p) for p in matched)
        if journal_qty != qty:
            findings.append(Finding(
                category="qty_mismatch",
                severity=MEDIUM,
                contract=_contract_label(key),
                detail=f"broker qty {qty:g} vs journal qty {journal_qty:g}",
                position_ids=[p.id for p in matched],
            ))

    for key, matched in sorted(journal_opens.items()):
        if key not in broker_opens:
            findings.append(Finding(
                category="journal_stale_open",
                severity=HIGH,
                contract=_contract_label(key),
                detail=(
                    "open in the journal but not at the broker — position "
                    "was closed (or expired) without a journal close"
                ),
                position_ids=[p.id for p in matched],
            ))

    fetched = str(snapshot.get("fetched_at") or "")[:10] or None
    return ReconcileReport(
        window_start=fetched,
        window_end=fetched,
        fills_count=len(broker_opens),
        findings=findings,
        warnings=warnings,
    )


def format_live_report(report: ReconcileReport) -> str:
    lines = [
        f"Live reconcile — {report.fills_count} broker open position(s), "
        f"fetched {report.window_end or 'unknown'}"
    ]
    if not report.findings:
        lines.append("✓ Journal matches the broker.")
    else:
        lines.append(f"✗ {len(report.findings)} discrepancy(ies):")
        for f in sorted(report.findings, key=lambda f: f.severity != HIGH):
            ids = f" [{', '.join(f.position_ids)}]" if f.position_ids else ""
            lines.append(f"  [{f.severity.upper():<6}] {f.category}: "
                         f"{f.contract}{ids}")
            lines.append(f"           {f.detail}")
    for w in report.warnings:
        lines.append(f"  [INFO  ] snapshot: {w}")
    return "\n".join(lines)
