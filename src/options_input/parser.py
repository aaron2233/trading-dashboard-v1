"""Parse pasted brokerage options text into the dashboard's options shape.

Brokerages export options data in wildly different layouts (TD/Schwab tabs,
Robinhood mobile-style key/value, Tastytrade compact). This parser is
intentionally lenient — regex-based extraction across multiple shapes,
returning whatever it can match. Unmatched fields stay None and the user
fills them manually.

Output shape mirrors the existing OptionsStructure / KillSheetRequest fields:
    strike, premium, expiry, contract_type, delta, iv_rank, open_interest,
    bid_ask_spread

The parser does NOT guess. If it sees something ambiguous (two strike-like
numbers without a label), it leaves the field None rather than picking the
more favourable one — per the no-fabrication rules in ~/.claude/CLAUDE.md.
"""
from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field
from datetime import datetime
from typing import Any


@dataclass
class ParsedOptions:
    """Result of parsing pasted brokerage text.

    Each value is None when the parser couldn't confidently extract it.
    `source_fields` lists which fields were actually found in the input —
    everything else is the user's responsibility to fill manually.
    """
    strike: float | None = None
    premium: float | None = None
    expiry: str | None = None         # ISO YYYY-MM-DD
    contract_type: str | None = None  # "call" | "put"
    delta: float | None = None
    iv_rank: float | None = None
    open_interest: int | None = None
    bid_ask_spread: float | None = None
    bid: float | None = None
    ask: float | None = None
    source_fields: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


# ─────────────────────────────────────────────────────────────────────────
# Field extractors
# ─────────────────────────────────────────────────────────────────────────

# Order matters — first alternative wins. Comma-thousands branch REQUIRES at
# least one comma group so it doesn't eat "5000" as "500" before the plain-int
# branch gets a chance. Plain int / decimal comes last.
_NUMBER = (
    r"[-+]?\d{1,3}(?:,\d{3})+(?:\.\d+)?"  # 1,500 / 12,500.25
    r"|[-+]?\d+(?:\.\d+)?"                # 5000 / 4.55 / -0.30
    r"|[-+]?\.\d+"                        # .42
)


def _to_float(s: str | None) -> float | None:
    if s is None:
        return None
    s = s.replace(",", "").replace("$", "").replace("%", "").strip()
    if not s:
        return None
    try:
        return float(s)
    except ValueError:
        return None


def _to_int(s: str | None) -> int | None:
    if s is None:
        return None
    s = s.replace(",", "").replace("$", "").strip()
    if not s:
        return None
    try:
        return int(float(s))  # tolerate "500.0"
    except ValueError:
        return None


# Field-keyed regex patterns. Each captures the value group; first match wins.
# Patterns are case-insensitive and tolerate $/whitespace/colons.
_PATTERNS: dict[str, list[str]] = {
    "strike": [
        rf"\bstrike\s*(?:price)?[:\s]*\$?\s*({_NUMBER})",
        rf"\bstrk[:\s]*\$?\s*({_NUMBER})",
    ],
    "premium": [
        rf"\bpremium[:\s]*\$?\s*({_NUMBER})",
        rf"\bmid[:\s]*\$?\s*({_NUMBER})",
        rf"\blast[:\s]*\$?\s*({_NUMBER})",
        rf"\bmark[:\s]*\$?\s*({_NUMBER})",
    ],
    "expiry": [
        # Labelled ISO date (most explicit)
        r"\b(?:expir\w*|exp)[:\s]*(\d{4}-\d{2}-\d{2})\b",
        # Labelled US format MM/DD/YYYY → normalise downstream
        r"\b(?:expir\w*|exp)[:\s]*(\d{1,2}/\d{1,2}/\d{2,4})\b",
        # Standalone ISO date anywhere in the input
        r"(?<!\d)(\d{4}-\d{2}-\d{2})(?!\d)",
        # Standalone US format anywhere in the input (handles "SPY 06/19/2026 ...")
        r"(?<!\d)(\d{1,2}/\d{1,2}/\d{2,4})(?!\d)",
    ],
    "delta": [
        rf"\bdelta[:\s]*({_NUMBER})",
        rf"\bΔ[:\s]*({_NUMBER})",
    ],
    "iv_rank": [
        rf"\b(?:iv\s*rank|ivr)[:\s]*({_NUMBER})\s*%?",
        rf"\biv\s*r[:\s]*({_NUMBER})",
    ],
    "open_interest": [
        rf"\b(?:open\s*interest|oi)[:\s]*({_NUMBER})",
    ],
    "bid": [
        rf"\bbid[:\s]*\$?\s*({_NUMBER})",
    ],
    "ask": [
        rf"\bask[:\s]*\$?\s*({_NUMBER})",
    ],
    "bid_ask_spread": [
        rf"\b(?:spread|bid[-/]?ask\s*spread)[:\s]*\$?\s*({_NUMBER})",
    ],
}


def _first_match(text: str, field: str) -> str | None:
    for pat in _PATTERNS[field]:
        m = re.search(pat, text, re.IGNORECASE)
        if m:
            return m.group(1)
    return None


def _normalize_expiry(raw: str) -> str | None:
    """Normalise to ISO YYYY-MM-DD. Returns None if unrecognised."""
    raw = raw.strip()
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", raw):
        try:
            datetime.strptime(raw, "%Y-%m-%d")
            return raw
        except ValueError:
            return None
    # MM/DD/YYYY or M/D/YY
    m = re.fullmatch(r"(\d{1,2})/(\d{1,2})/(\d{2,4})", raw)
    if m:
        mm, dd, yy = m.groups()
        if len(yy) == 2:
            yy = "20" + yy
        try:
            d = datetime.strptime(f"{yy}-{int(mm):02d}-{int(dd):02d}", "%Y-%m-%d")
            return d.strftime("%Y-%m-%d")
        except ValueError:
            return None
    return None


def _detect_contract_type(text: str) -> str | None:
    """Detect call vs put from explicit field, header words, or option symbol.

    Returns None if ambiguous (both 'call' and 'put' appear without a clear
    primary context) — caller picks manually rather than guessing.
    """
    lower = text.lower()
    # Explicit field
    m = re.search(r"\b(?:type|contract)[:\s]+(call|put)\b", lower)
    if m:
        return m.group(1)
    # Standalone "Call" or "Put" header words
    has_call = bool(re.search(r"\bcall\b", lower))
    has_put = bool(re.search(r"\bput\b", lower))
    if has_call and not has_put:
        return "call"
    if has_put and not has_call:
        return "put"
    # Both present or neither → ambiguous
    return None


# ─────────────────────────────────────────────────────────────────────────
# Public entry point
# ─────────────────────────────────────────────────────────────────────────


def parse_options_text(text: str) -> ParsedOptions:
    """Parse pasted brokerage options data into a ParsedOptions struct.

    Intentionally lenient — extracts what it can, leaves unmatched fields
    None, and surfaces ambiguity warnings rather than guessing.
    """
    if not text or not text.strip():
        return ParsedOptions(warnings=["empty input"])

    result = ParsedOptions()
    found: list[str] = []

    # Numeric / string fields driven by _PATTERNS
    raw_strike = _first_match(text, "strike")
    if raw_strike:
        result.strike = _to_float(raw_strike)
        if result.strike is not None:
            found.append("strike")

    raw_premium = _first_match(text, "premium")
    if raw_premium:
        result.premium = _to_float(raw_premium)
        if result.premium is not None:
            found.append("premium")

    raw_delta = _first_match(text, "delta")
    if raw_delta:
        result.delta = _to_float(raw_delta)
        if result.delta is not None:
            found.append("delta")

    raw_ivr = _first_match(text, "iv_rank")
    if raw_ivr:
        ivr_val = _to_float(raw_ivr)
        # IV Rank is conventionally 0-100. If we see 0-1, assume the user
        # pasted a fraction by mistake — flag rather than auto-rescale.
        if ivr_val is not None:
            if 0 <= ivr_val <= 1:
                result.warnings.append(
                    f"IV Rank value {ivr_val} looks like a fraction; "
                    "expected 0-100. Verify before submitting."
                )
            result.iv_rank = ivr_val
            found.append("iv_rank")

    raw_oi = _first_match(text, "open_interest")
    if raw_oi:
        result.open_interest = _to_int(raw_oi)
        if result.open_interest is not None:
            found.append("open_interest")

    raw_bid = _first_match(text, "bid")
    if raw_bid:
        result.bid = _to_float(raw_bid)
    raw_ask = _first_match(text, "ask")
    if raw_ask:
        result.ask = _to_float(raw_ask)

    raw_spread = _first_match(text, "bid_ask_spread")
    if raw_spread:
        result.bid_ask_spread = _to_float(raw_spread)
        if result.bid_ask_spread is not None:
            found.append("bid_ask_spread")
    elif result.bid is not None and result.ask is not None:
        # Derive spread from bid + ask when both are present and explicit
        # spread wasn't given. Don't override an explicit spread value.
        result.bid_ask_spread = round(result.ask - result.bid, 4)
        found.append("bid_ask_spread")

    # Expiry — normalise to ISO
    raw_exp = _first_match(text, "expiry")
    if raw_exp:
        norm = _normalize_expiry(raw_exp)
        if norm:
            result.expiry = norm
            found.append("expiry")
        else:
            result.warnings.append(f"Could not normalise expiry value {raw_exp!r}")

    # Contract type
    ctype = _detect_contract_type(text)
    if ctype:
        result.contract_type = ctype
        found.append("contract_type")
    elif re.search(r"\bcall\b", text, re.IGNORECASE) and re.search(r"\bput\b", text, re.IGNORECASE):
        result.warnings.append(
            "Both 'call' and 'put' present in input; contract_type left unset"
        )

    result.source_fields = found
    if not found:
        result.warnings.append(
            "Parser could not extract any options fields — paste may be in an "
            "unsupported format. Fill the fields manually."
        )

    return result
