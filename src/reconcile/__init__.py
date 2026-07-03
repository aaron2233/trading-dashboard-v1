"""Broker-CSV reconciliation — diff broker fills against the journal.

Read-only by design: the reconciler flags discrepancies (ghost trades,
stale opens, quantity mismatches) but never writes positions.json.
Backfilling stays a deliberate human action — auto-importing a fill
would launder an unlogged trade into the journal and defeat the
discipline KPI this exists to protect.
"""
