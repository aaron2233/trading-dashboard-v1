"""Parse a Robinhood MCP option-quote snapshot into ParsedOptions.

An MCP client (e.g. a Claude session with the robinhood-trading server
connected) fetches a contract quote via get_option_instruments +
get_option_quotes and writes a snapshot JSON file; the kill-sheet CLI ingests
it with ``python -m kill_sheet ... --options-json <path>``. Explicit CLI flags
always win over snapshot values.

Snapshot shape (written by the fetching agent):

    {
      "source": "robinhood-mcp",
      "fetched_at": "2026-07-03T16:40:00Z",   # when the quote was pulled
      "ticker": "TST1",
      "strike": 100.0,
      "expiry": "2026-12-18",                  # ISO YYYY-MM-DD
      "contract_type": "call",
      "quote": { ...one quote object from get_option_quotes results[] ... }
    }

Field mapping from the quote object: premium = mark_price, bid = bid_price,
ask = ask_price, bid_ask_spread = ask - bid, delta = delta,
open_interest = open_interest. iv_rank stays None — the quote carries spot
implied volatility, not IV Rank; IVR still comes from your IVR source.

Per the no-fabrication rules: absent fields stay None, never guessed. A
snapshot older than STALE_AFTER_MINUTES gets a warning (the CLI refuses it
without --allow-stale) — options marks go stale fast; never build a kill
sheet on old quotes.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from options_input.parser import ParsedOptions

STALE_AFTER_MINUTES = 30.0


def _f(value) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def snapshot_age_minutes(raw: dict, now: datetime | None = None) -> float | None:
    """Age of the snapshot in minutes, or None when fetched_at is absent/bad."""
    fetched_at = raw.get("fetched_at")
    if not fetched_at:
        return None
    try:
        ts = datetime.fromisoformat(str(fetched_at).replace("Z", "+00:00"))
    except ValueError:
        return None
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)
    now = now or datetime.now(timezone.utc)
    return (now - ts).total_seconds() / 60.0


def parse_robinhood_snapshot(
    raw: dict, now: datetime | None = None
) -> ParsedOptions:
    """Map a robinhood-mcp snapshot dict onto ParsedOptions."""
    parsed = ParsedOptions()

    parsed.strike = _f(raw.get("strike"))
    if parsed.strike is not None:
        parsed.source_fields.append("strike")

    expiry = raw.get("expiry")
    if expiry:
        try:
            parsed.expiry = datetime.strptime(str(expiry), "%Y-%m-%d").date().isoformat()
            parsed.source_fields.append("expiry")
        except ValueError:
            parsed.warnings.append(f"expiry {expiry!r} is not ISO YYYY-MM-DD — ignored")

    contract_type = raw.get("contract_type")
    if contract_type in ("call", "put"):
        parsed.contract_type = contract_type
        parsed.source_fields.append("contract_type")

    quote = raw.get("quote")
    if not isinstance(quote, dict):
        parsed.warnings.append("snapshot has no quote object — options fields left empty")
        return parsed

    parsed.premium = _f(quote.get("mark_price"))
    if parsed.premium is not None:
        parsed.source_fields.append("premium")

    parsed.bid = _f(quote.get("bid_price"))
    parsed.ask = _f(quote.get("ask_price"))
    if parsed.bid is not None and parsed.ask is not None:
        parsed.bid_ask_spread = round(parsed.ask - parsed.bid, 4)
        parsed.source_fields.extend(["bid", "ask", "bid_ask_spread"])

    parsed.delta = _f(quote.get("delta"))
    if parsed.delta is not None:
        parsed.source_fields.append("delta")

    oi = quote.get("open_interest")
    if isinstance(oi, (int, float)) and not isinstance(oi, bool):
        parsed.open_interest = int(oi)
        parsed.source_fields.append("open_interest")

    # Spot IV is NOT IV Rank — leave iv_rank None and say so.
    if quote.get("implied_volatility") is not None:
        parsed.warnings.append(
            "quote carries spot IV, not IV Rank — fill --iv-rank from your IVR source"
        )

    age = snapshot_age_minutes(raw, now=now)
    if age is None:
        parsed.warnings.append("snapshot has no valid fetched_at — treat quotes as stale")
    elif age > STALE_AFTER_MINUTES:
        parsed.warnings.append(
            f"snapshot is {age:.0f} min old (> {STALE_AFTER_MINUTES:.0f} min) — stale"
        )

    return parsed


def load_snapshot(path: str | Path) -> dict:
    """Read and JSON-decode a snapshot file. Raises ValueError with a clear
    message on missing file / bad JSON (callers surface it verbatim)."""
    p = Path(path).expanduser()
    if not p.is_file():
        raise ValueError(f"options snapshot not found: {p}")
    try:
        raw = json.loads(p.read_text())
    except json.JSONDecodeError as exc:
        raise ValueError(f"options snapshot is not valid JSON ({p}): {exc}") from exc
    if not isinstance(raw, dict):
        raise ValueError(f"options snapshot must be a JSON object ({p})")
    return raw
