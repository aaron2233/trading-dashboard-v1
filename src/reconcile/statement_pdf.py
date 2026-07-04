"""Parse a Robinhood monthly-statement PDF into normalized fills.

Statements are generated automatically every month (unlike the on-demand
CSV report), so they're the natural input for a monthly reconcile ritual.

Layout (validated against a real 2026-05 statement):

  Account Activity table —
    Description Symbol "Acct Type" Transaction Date Qty Price Debit Credit
  option rows extract as one line:
    TLT 06/05/2026 Put $86.00 TLT Cash BTO 04/30/2026 2 $1.48000 $296.08
  shares rows extract as two lines (company name, then CUSIP row):
    Lithium Americas
    CUSIP: 53681J103 LAC Cash Buy 05/05/2026 182 $5.47760 $996.92

  "Executed Trades Pending Settlement" table — same idea but with NO
  Symbol column and two dates (Trade Date, Settle Date):
    NVDA 06/08/2026 Call $232.50 Cash BTO 05/29/2026 06/01/2026 1 $0.96000 $96.04
    CUSIP: 590106100 Cash Sell 05/29/2026 06/01/2026 70.028011 $7.86000 $550.39
  Pending shares rows carry no symbol at all — it's recovered from a
  CUSIP→symbol map built from the Account Activity rows; if the CUSIP
  never appeared with a symbol, the row becomes a warning, not a fill.

PDF text extraction requires pypdf (lazy import — only the .pdf path
needs it). Non-trade rows (ACH, FUTSWP, GOLD, ...) don't match the row
patterns and are ignored; genuinely malformed trade-looking rows land
in warnings so nothing is dropped silently.
"""
from __future__ import annotations

import re
from pathlib import Path

from reconcile.robinhood_csv import Fill, ParseResult

_DATE = r"\d{2}/\d{2}/\d{4}"
_MONEY = r"\$[\d,]+(?:\.\d+)?"
_QTY = r"[\d,]+(?:\.\d+)?"
_CODES = r"BTO|STC|STO|BTC|OEXP|OASGN|Buy|Sell"

# Option trade row. The description leads with the contract; Symbol is
# present in Account Activity and absent in Pending Settlement, so it's
# optional; one or two dates follow the trans code.
_OPTION_ROW_RE = re.compile(
    rf"^(?P<ticker>[A-Z][A-Z.\-]*)\s+"
    rf"(?P<exp>{_DATE})\s+(?P<kind>Call|Put)\s+\$(?P<strike>[\d,]+(?:\.\d+)?)\s+"
    rf"(?:(?P<symbol>[A-Z][A-Z.\-]*)\s+)?"
    rf"(?P<acct>\w+)\s+(?P<code>{_CODES})\s+"
    rf"(?P<date>{_DATE})(?:\s+(?P<settle>{_DATE}))?\s+"
    rf"(?P<qty>{_QTY})\s+(?P<price>{_MONEY})\s+(?P<amount>{_MONEY})$"
)

# Shares trade row (the CUSIP line; company name is the line above).
_SHARES_ROW_RE = re.compile(
    rf"^CUSIP:\s*(?P<cusip>\w+)\s+"
    rf"(?:(?P<symbol>[A-Z][A-Z.\-]*)\s+)?"
    rf"(?P<acct>\w+)\s+(?P<code>Buy|Sell)\s+"
    rf"(?P<date>{_DATE})(?:\s+(?P<settle>{_DATE}))?\s+"
    rf"(?P<qty>{_QTY})\s+(?P<price>{_MONEY})\s+(?P<amount>{_MONEY})$"
)

# Something that *looks* like it wants to be a trade row but didn't
# match — surfaced as a warning instead of vanishing.
_TRADE_HINT_RE = re.compile(rf"\s({_CODES})\s+{_DATE}\s")


def _iso(mdy: str) -> str:
    m, d, y = mdy.split("/")
    return f"{y}-{int(m):02d}-{int(d):02d}"


def _num(raw: str) -> float:
    return float(raw.replace("$", "").replace(",", ""))


def extract_lines(path: Path) -> list[str]:
    try:
        import pypdf
    except ImportError as exc:
        raise RuntimeError(
            "Parsing statement PDFs requires pypdf — install with "
            "`pip install pypdf` (or `pip install -e '.[reconcile-pdf]'`)"
        ) from exc
    reader = pypdf.PdfReader(str(path))
    lines: list[str] = []
    for page in reader.pages:
        text = page.extract_text() or ""
        lines.extend(ln.strip() for ln in text.splitlines())
    return lines


def parse_statement_lines(lines: list[str]) -> ParseResult:
    result = ParseResult()
    cusip_to_symbol: dict[str, str] = {}
    pending_shares: list[tuple[int, re.Match]] = []

    for idx, line in enumerate(lines):
        m = _OPTION_ROW_RE.match(line)
        if m:
            code = m["code"]
            result.fills.append(Fill(
                date=_iso(m["date"]),
                ticker=m["ticker"],
                kind=m["kind"].lower(),
                action="open" if code in ("BTO", "STO", "Buy") else "close",
                code=code,
                quantity=_num(m["qty"]),
                strike=_num(m["strike"]),
                expiry=_iso(m["exp"]),
                price=_num(m["price"]),
                amount=_num(m["amount"]),
            ))
            continue

        m = _SHARES_ROW_RE.match(line)
        if m:
            symbol = m["symbol"]
            if symbol:
                cusip_to_symbol[m["cusip"]] = symbol
                result.fills.append(_shares_fill(m, symbol))
            else:
                # Pending-settlement shares row — symbol resolved after
                # the full pass, once the CUSIP map is complete.
                pending_shares.append((idx, m))
            continue

        if _TRADE_HINT_RE.search(line):
            result.warnings.append(
                f"statement line {idx + 1}: looks like a trade row but did "
                f"not parse — {line!r}"
            )

    for idx, m in pending_shares:
        symbol = cusip_to_symbol.get(m["cusip"])
        if symbol:
            result.fills.append(_shares_fill(m, symbol))
        else:
            result.warnings.append(
                f"statement line {idx + 1}: pending-settlement shares row "
                f"with unknown CUSIP {m['cusip']} (no symbol seen elsewhere "
                f"in the statement) — skipped: {lines[idx]!r}"
            )
    return result


def _shares_fill(m: re.Match, symbol: str) -> Fill:
    code = m["code"]
    return Fill(
        date=_iso(m["date"]),
        ticker=symbol,
        kind="shares",
        action="open" if code == "Buy" else "close",
        code=code,
        quantity=_num(m["qty"]),
        price=_num(m["price"]),
        amount=_num(m["amount"]),
    )


def parse_statement_pdf(path: Path) -> ParseResult:
    return parse_statement_lines(extract_lines(Path(path)))
