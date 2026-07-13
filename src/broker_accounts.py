"""Broker-account breakout for the Accounts panel.

The dashboard's sleeves (main / lotto / portfolio / ...) are journal-side
constructs; the broker sees real accounts. Users map the two in
~/.trading-dashboard/config.yaml::

    broker_accounts:
      - key: individual
        label: "Individual · Options Book"
        account_mask: "1234"        # last-4 ONLY — never the full number
        sleeves: [main, lotto]      # journal sleeves funded by this account
      - key: roth
        label: "Roth IRA"
        account_mask: "5678"
        sleeves: []

Balances come from local snapshot files written by an MCP-connected agent
(the same ferry that feeds the reconcile tripwire and the kill-sheet
``--balance-json`` audit)::

    ~/.trading-dashboard/balance_snapshots/portfolio-<mask>.json
    {"source": "robinhood-mcp", "fetched_at": ISO-8601, "account": "…1234",
     "portfolio": { ...data object from get_portfolio... }}

Privacy rule: the repo never carries balances or account numbers — labels,
masks, and dollar values all live in local config + snapshot files.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from config.loader import Config

DEFAULT_SNAPSHOT_DIR = Path.home() / ".trading-dashboard" / "balance_snapshots"

# The snapshot ferry runs on weekdays; anything older than 4 days (a full
# weekend plus a missed run) means the feed is broken, not just resting.
STALE_AFTER_HOURS = 96.0


@dataclass
class BrokerAccount:
    key: str
    label: str
    account_masked: str              # "…1234" — mask re-applied defensively
    sleeves: list[str] = field(default_factory=list)
    total_value_usd: float | None = None
    cash_usd: float | None = None
    as_of: str | None = None         # snapshot fetched_at, ISO-8601
    age_hours: float | None = None
    stale: bool = True               # no snapshot == stale
    error: str | None = None


@dataclass
class UnmappedSleeve:
    """A configured sleeve not funded by any mapped broker account
    (e.g. capital held outside the broker). Balance is the config base."""
    key: str
    name: str
    balance_usd: float


def _mask(value: str) -> str:
    tail = value.strip()[-4:]
    return f"…{tail}"


def _parse_fetched_at(raw: object) -> datetime | None:
    if not isinstance(raw, str) or not raw.strip():
        return None
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def _read_snapshot(path: Path) -> tuple[dict, str | None]:
    """Return (snapshot_dict, error). Missing/corrupt files degrade to an
    error string — the panel renders the account with a stale flag rather
    than 500ing the whole endpoint."""
    if not path.exists():
        return {}, f"no snapshot ({path.name})"
    try:
        data = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError) as exc:
        return {}, f"unreadable snapshot: {exc}"
    if not isinstance(data, dict):
        return {}, "snapshot is not a JSON object"
    return data, None


def load_broker_accounts(
    config: Config,
    snapshot_dir: Path = DEFAULT_SNAPSHOT_DIR,
    *,
    now: datetime | None = None,
) -> list[BrokerAccount]:
    """Materialize the configured broker accounts with snapshot balances.

    Returns [] when the user config has no ``broker_accounts`` block — the
    frontend hides the panel entirely on a fresh install.
    """
    raw = config.raw.get("broker_accounts")
    if not isinstance(raw, list):
        return []
    now = now or datetime.now(timezone.utc)

    accounts: list[BrokerAccount] = []
    for entry in raw:
        if not isinstance(entry, dict):
            continue
        mask = str(entry.get("account_mask", "")).strip()
        if not mask:
            continue
        acct = BrokerAccount(
            key=str(entry.get("key", mask)),
            label=str(entry.get("label", mask)),
            account_masked=_mask(mask),
            sleeves=[str(s) for s in entry.get("sleeves", []) or []],
        )

        snapshot, err = _read_snapshot(snapshot_dir / f"portfolio-{mask[-4:]}.json")
        if err is not None:
            acct.error = err
            accounts.append(acct)
            continue

        portfolio = snapshot.get("portfolio")
        if isinstance(portfolio, dict):
            try:
                acct.total_value_usd = float(portfolio.get("total_value"))
            except (TypeError, ValueError):
                acct.error = "snapshot missing portfolio.total_value"
            try:
                acct.cash_usd = float(portfolio.get("cash"))
            except (TypeError, ValueError):
                acct.cash_usd = None
        else:
            acct.error = "snapshot missing portfolio object"

        fetched = _parse_fetched_at(snapshot.get("fetched_at"))
        if fetched is not None:
            acct.as_of = fetched.isoformat()
            acct.age_hours = max(0.0, (now - fetched).total_seconds() / 3600.0)
            acct.stale = acct.age_hours > STALE_AFTER_HOURS
        accounts.append(acct)
    return accounts


def unmapped_sleeves(
    config: Config,
    broker_accounts: list[BrokerAccount],
) -> list[UnmappedSleeve]:
    """Configured sleeves not claimed by any broker account.

    Pool members (e.g. an account with ``pool_member_of``) are excluded —
    they don't hold standalone capital. Returns [] when no broker accounts
    are configured: without a mapping, "unmapped" is meaningless.
    """
    if not broker_accounts:
        return []
    mapped: set[str] = set()
    for acct in broker_accounts:
        mapped.update(acct.sleeves)
    return [
        UnmappedSleeve(key=key, name=sleeve.name, balance_usd=sleeve.balance_usd)
        for key, sleeve in config.accounts.items()
        if key not in mapped and sleeve.pool_member_of is None
    ]


def selectable_account_keys(config: Config) -> list[str]:
    """Sleeve keys offered in new-position / kill-sheet dropdowns.

    Pool members are excluded: they draw on another sleeve's capital and are
    a legacy artifact for historical positions, not a valid entry target.
    Order follows config order (defaults first, then user additions).
    """
    return [
        key for key, acct in config.accounts.items()
        if acct.pool_member_of is None
    ]
