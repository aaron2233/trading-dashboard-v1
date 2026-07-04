"""Parse a Robinhood activity-report CSV into normalized fills.

Expected format (Robinhood app → Account → Reports and statements →
Reports → generate report → CSV):

    Activity Date,Process Date,Settle Date,Account Type,Instrument,
    Description,Trans Code,Quantity,Price,Amount[,Suppressed]

Options rows describe the contract in the Description column, e.g.
"IWM 6/12/2026 Put $232.00". Trans codes: BTO/STC (long options),
STO/BTC (short options — not expected in this account), OEXP
(expiration), OASGN (assignment), Buy/Sell (shares). Everything else
(ACH, CDIV, GOLD, INT, ...) is non-trade activity and is skipped.

The header is validated up front and the parser raises with the headers
it actually saw — the first run against a real export doubles as the
format check.
"""
from __future__ import annotations

import csv
import re
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path


REQUIRED_COLUMNS = {
    "Activity Date", "Instrument", "Description",
    "Trans Code", "Quantity", "Price", "Amount",
}

# Trans codes that open exposure vs close it. Short-option codes are
# included so they parse cleanly — the engine flags them separately
# (this is a long-calls/long-puts book; STO/BTC/OASGN means something
# is off, not that we should silently reconcile it).
OPEN_CODES = {"BTO", "STO", "Buy"}
CLOSE_CODES = {"STC", "BTC", "Sell", "OEXP", "OASGN"}
TRADE_CODES = OPEN_CODES | CLOSE_CODES
SHORT_SIDE_CODES = {"STO", "BTC", "OASGN"}

_OPTION_DESC_RE = re.compile(
    r"^(?P<ticker>[A-Z][A-Z.\-]*)\s+"
    r"(?P<month>\d{1,2})/(?P<day>\d{1,2})/(?P<year>\d{4})\s+"
    r"(?P<kind>Call|Put)\s+"
    r"\$(?P<strike>[\d,]+(?:\.\d+)?)$"
)


class RobinhoodCsvError(ValueError):
    """Raised when the file doesn't look like a Robinhood report CSV."""


@dataclass
class Fill:
    """One trade row from the broker CSV, normalized."""
    date: str                    # ISO YYYY-MM-DD (Activity Date)
    ticker: str
    kind: str                    # call | put | shares
    action: str                  # open | close
    code: str                    # raw Trans Code (BTO, STC, OEXP, ...)
    quantity: float
    strike: float | None = None
    expiry: str | None = None    # ISO YYYY-MM-DD
    price: float | None = None   # per share/contract-share, as reported
    amount: float | None = None  # signed cash impact


@dataclass
class ParseResult:
    fills: list[Fill] = field(default_factory=list)
    skipped_rows: list[str] = field(default_factory=list)   # non-trade codes (counted, not itemized)
    warnings: list[str] = field(default_factory=list)       # trade rows we could not use


def _parse_money(raw: str | None) -> float | None:
    if raw is None:
        return None
    s = raw.strip().replace("$", "").replace(",", "")
    if not s:
        return None
    negative = s.startswith("(") and s.endswith(")")
    if negative:
        s = s[1:-1]
    try:
        value = float(s)
    except ValueError:
        return None
    return -value if negative else value


def _parse_date(raw: str) -> str:
    return datetime.strptime(raw.strip(), "%m/%d/%Y").date().isoformat()


def parse_report_csv(path: Path) -> ParseResult:
    with Path(path).open(newline="") as fh:
        reader = csv.DictReader(fh)
        headers = set(reader.fieldnames or [])
        missing = REQUIRED_COLUMNS - headers
        if missing:
            raise RobinhoodCsvError(
                f"{path} does not look like a Robinhood report CSV — "
                f"missing column(s) {sorted(missing)}; "
                f"found headers: {sorted(headers)}"
            )

        result = ParseResult()
        for line_no, row in enumerate(reader, start=2):
            code = (row.get("Trans Code") or "").strip()
            if code not in TRADE_CODES:
                result.skipped_rows.append(code or "(blank)")
                continue

            try:
                date = _parse_date(row["Activity Date"])
            except ValueError:
                result.warnings.append(
                    f"line {line_no}: unparseable Activity Date "
                    f"{row.get('Activity Date')!r} on {code} row — skipped"
                )
                continue

            qty_raw = (row.get("Quantity") or "").strip().replace(",", "")
            quantity: float | None
            try:
                quantity = float(qty_raw) if qty_raw else None
            except ValueError:
                quantity = None
            if quantity is None:
                if code == "OEXP":
                    # Expiration rows sometimes omit quantity; the engine
                    # treats 0 as "close whatever remains".
                    quantity = 0.0
                else:
                    result.warnings.append(
                        f"line {line_no}: unparseable Quantity {qty_raw!r} "
                        f"on {code} row — skipped"
                    )
                    continue

            price = _parse_money(row.get("Price"))
            amount = _parse_money(row.get("Amount"))
            instrument = (row.get("Instrument") or "").strip().upper()
            description = (row.get("Description") or "").strip()

            if code in {"Buy", "Sell"}:
                if not instrument:
                    result.warnings.append(
                        f"line {line_no}: {code} row with empty Instrument — skipped"
                    )
                    continue
                result.fills.append(Fill(
                    date=date, ticker=instrument, kind="shares",
                    action="open" if code in OPEN_CODES else "close",
                    code=code, quantity=quantity, price=price, amount=amount,
                ))
                continue

            m = _OPTION_DESC_RE.match(description)
            if not m:
                result.warnings.append(
                    f"line {line_no}: could not parse option description "
                    f"{description!r} on {code} row — skipped"
                )
                continue
            expiry = (
                f"{m['year']}-{int(m['month']):02d}-{int(m['day']):02d}"
            )
            result.fills.append(Fill(
                date=date,
                ticker=m["ticker"],
                kind=m["kind"].lower(),
                action="open" if code in OPEN_CODES else "close",
                code=code,
                quantity=quantity,
                strike=float(m["strike"].replace(",", "")),
                expiry=expiry,
                price=price,
                amount=amount,
            ))
        return result
