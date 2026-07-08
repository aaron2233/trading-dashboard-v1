"""Broker balance audit for kill-sheet sizing (``--balance-json``).

An MCP client (e.g. a Claude session with the robinhood-trading server
connected) fetches account totals via get_portfolio and writes a snapshot
JSON; the kill-sheet CLI ingests it with
``python -m kill_sheet ... --balance-json <path>``.

Snapshot shape (written by the fetching agent)::

    {
      "source": "robinhood-mcp",
      "fetched_at": "2026-07-04T18:00:00Z",   # when get_portfolio was called
      "account": "...4907",                    # masked — never the full number
      "portfolio": { ...data object from get_portfolio... }
    }

Only ``portfolio.total_value`` is consumed. It is compared against the
journal's own book model — config ``balance.anchor`` plus realized P&L closed
after ``anchor_date`` (discipline.dashboard.compute_account_balance), the
same derivation the dashboard stage banner uses.

The audit never rewrites sleeve capital: the broker sees one combined
account, while main/lotto are journal-side sleeves, so splitting the broker
total across sleeves would be fabrication. Sizing stays on the configured
sleeve balance; the audit's job is to prove (or disprove) that the book
those balances live in still matches the broker. Drift >= DRIFT_WARN_PCT
means unlogged fills, an un-stamped deposit/withdrawal, or a stale anchor —
run the reconcile job, then re-stamp balance.anchor in config.yaml.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

from discipline.dashboard import compute_account_balance
from options_input.robinhood import snapshot_age_minutes

# Balance is an audit anchor, not a fill price — a wider cutoff than the
# 30-min options-quote window is fine, but intraday mark-to-market drift on
# open options means a same-day snapshot is still required by default.
STALE_AFTER_MINUTES = 240.0
DRIFT_WARN_PCT = 2.0


def _mask_account(value) -> str | None:
    """Last-4 mask, defensively re-applied even if the agent wrote it raw."""
    if not isinstance(value, str) or not value.strip():
        return None
    tail = value.strip()[-4:]
    return f"…{tail}"


@dataclass
class BalanceAudit:
    broker_total_usd: float
    model_total_usd: float
    drift_pct: float | None          # None when the book model total is <= 0
    age_minutes: float | None
    account_masked: str | None
    warnings: list[str] = field(default_factory=list)

    def line(self) -> str:
        """One-line render for the kill sheet's sizing block."""
        src = self.account_masked or "broker"
        age = ("age unknown" if self.age_minutes is None
               else f"{self.age_minutes:.0f} min old")
        head = (f"broker ${self.broker_total_usd:,.2f} ({src}, {age}) vs "
                f"book model ${self.model_total_usd:,.2f}")
        if self.drift_pct is None:
            return f"{head} → drift n/a (book model ≤ 0)"
        mark = "⚠" if abs(self.drift_pct) >= DRIFT_WARN_PCT else "✓"
        return f"{head} → drift {self.drift_pct:+.2f}% {mark}"


def load_balance_snapshot(path: str | Path) -> dict:
    """Read and JSON-decode a balance snapshot. Raises ValueError with a
    clear message on missing file / bad JSON (callers surface it verbatim)."""
    p = Path(path).expanduser()
    if not p.is_file():
        raise ValueError(f"balance snapshot not found: {p}")
    try:
        raw = json.loads(p.read_text())
    except json.JSONDecodeError as exc:
        raise ValueError(f"balance snapshot is not valid JSON ({p}): {exc}") from exc
    if not isinstance(raw, dict):
        raise ValueError(f"balance snapshot must be a JSON object ({p})")
    return raw


def audit_balance(raw, config, closed_positions, now=None) -> BalanceAudit:
    """Compare the broker's total account value against the journal's book
    model. Raises ValueError when the snapshot has no usable total."""
    portfolio = raw.get("portfolio")
    if not isinstance(portfolio, dict):
        raise ValueError("balance snapshot has no portfolio object "
                         "(expected the get_portfolio data payload)")
    try:
        broker = float(portfolio["total_value"])
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError(
            "balance snapshot portfolio.total_value is missing or non-numeric"
        ) from exc

    _base, _realized, model_total = compute_account_balance(
        config, closed_positions)

    drift_pct: float | None = None
    warnings: list[str] = []
    if model_total > 0:
        drift_pct = (broker - model_total) / model_total * 100.0
        if abs(drift_pct) >= DRIFT_WARN_PCT:
            warnings.append(
                f"broker vs book-model drift {drift_pct:+.2f}% "
                f"(cutoff {DRIFT_WARN_PCT:.0f}%) — journal or balance.anchor "
                f"out of true: run `python -m reconcile <latest export>`, "
                f"then re-stamp balance.anchor_usd/anchor_date in config.yaml"
            )
    else:
        warnings.append(
            "book model total is ≤ 0 — check config account balances / anchor")

    return BalanceAudit(
        broker_total_usd=broker,
        model_total_usd=round(model_total, 2),
        drift_pct=drift_pct,
        age_minutes=snapshot_age_minutes(raw, now=now),
        account_masked=_mask_account(raw.get("account")),
        warnings=warnings,
    )
