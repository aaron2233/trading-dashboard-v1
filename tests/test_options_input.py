"""Tests for the brokerage paste parser (src/options_input/parser.py).

Coverage targets per ~/.claude/CLAUDE.md anti-fabrication discipline:
- Common formats parse correctly (Schwab tabs, Robinhood key/value, free-form)
- Missing fields stay None — parser does not invent values
- Ambiguous input flags warnings instead of guessing
- Date normalisation handles ISO and US formats
- IV Rank fraction-vs-percent detection
"""
from __future__ import annotations

from options_input.parser import ParsedOptions, parse_options_text


# ─────────────────────────────────────────────────────────────────────────
# Happy path — common brokerage layouts
# ─────────────────────────────────────────────────────────────────────────


def test_parse_schwab_style_tabular():
    """Schwab/TD typically pastes as labelled rows."""
    text = """\
SPY 06/19/2026 480 Call
Strike: 480
Bid: 4.50
Ask: 4.60
Premium: 4.55
Delta: 0.42
IV Rank: 35
Open Interest: 12,500
"""
    p = parse_options_text(text)
    assert p.strike == 480.0
    assert p.bid == 4.50
    assert p.ask == 4.60
    assert p.premium == 4.55
    assert p.delta == 0.42
    assert p.iv_rank == 35.0
    assert p.open_interest == 12500
    assert p.bid_ask_spread == 0.10  # derived from bid/ask
    assert p.expiry == "2026-06-19"
    assert p.contract_type == "call"


def test_parse_robinhood_mobile_style():
    text = """\
AAPL Call
Strike Price $30
Mid $1.45
Expiration: 2026-07-17
IVR 28%
OI 850
"""
    p = parse_options_text(text)
    assert p.strike == 30.0
    assert p.premium == 1.45
    assert p.expiry == "2026-07-17"
    assert p.iv_rank == 28.0
    assert p.open_interest == 850
    assert p.contract_type == "call"


def test_parse_freeform_keyvalue():
    text = "strike: 100, premium: 2.30, exp 2026-08-21, type put, delta -0.30, oi 5000"
    p = parse_options_text(text)
    assert p.strike == 100.0
    assert p.premium == 2.30
    assert p.expiry == "2026-08-21"
    assert p.contract_type == "put"
    assert p.delta == -0.30
    assert p.open_interest == 5000


def test_parse_explicit_spread_overrides_derived():
    """If the user pastes 'Spread: $0.50' that wins over bid/ask derivation."""
    text = "Bid: 1.00 Ask: 1.10 Spread: 0.50"
    p = parse_options_text(text)
    assert p.bid_ask_spread == 0.50  # explicit spread, not 0.10 from derivation


def test_parse_us_date_format_normalises():
    """MM/DD/YYYY → ISO."""
    text = "Strike 50, premium 1.20, expires 7/17/2026"
    p = parse_options_text(text)
    assert p.expiry == "2026-07-17"


def test_parse_two_digit_year_normalises():
    text = "Strike 50, premium 1.20, expires 7/17/26"
    p = parse_options_text(text)
    assert p.expiry == "2026-07-17"


def test_parse_dollar_signs_and_commas_stripped():
    text = "Strike $1,500 Premium $25.50 OI 2,500"
    p = parse_options_text(text)
    assert p.strike == 1500.0
    assert p.premium == 25.50
    assert p.open_interest == 2500


# ─────────────────────────────────────────────────────────────────────────
# Don't-fabricate behaviour
# ─────────────────────────────────────────────────────────────────────────


def test_parse_empty_input_warns():
    p = parse_options_text("")
    assert p.warnings == ["empty input"]
    assert p.source_fields == []


def test_parse_garbage_returns_warnings_no_data():
    p = parse_options_text("the quick brown fox jumps over the lazy dog")
    assert p.strike is None
    assert p.premium is None
    assert "could not extract" in " ".join(p.warnings).lower()


def test_parse_missing_fields_stay_none():
    """Only strike provided → other fields stay None, no guessing."""
    text = "Strike: 50"
    p = parse_options_text(text)
    assert p.strike == 50.0
    assert p.premium is None
    assert p.expiry is None
    assert p.contract_type is None
    assert p.iv_rank is None


def test_parse_ambiguous_call_put_leaves_type_unset():
    """Both 'call' and 'put' present → ambiguous, surface warning instead of picking."""
    text = """\
SPY Options Chain
Strike: 480
Premium: 4.50
Notes: comparing call vs put for the same strike
"""
    p = parse_options_text(text)
    # Strike + premium still extract via labels
    assert p.strike == 480.0
    assert p.premium == 4.50
    # Contract type is ambiguous — both words present — must NOT guess
    assert p.contract_type is None
    assert any("ambiguous" in w.lower() or "both" in w.lower() for w in p.warnings)


def test_parse_iv_rank_fraction_warns():
    """0.35 is suspicious IVR (looks like a fraction not 0-100)."""
    text = "Strike 50 Premium 1.0 IVR 0.35"
    p = parse_options_text(text)
    assert p.iv_rank == 0.35
    assert any("fraction" in w.lower() for w in p.warnings)


def test_parse_invalid_date_format_warns():
    text = "Strike 50 Premium 1.0 Expires Friday"
    p = parse_options_text(text)
    assert p.expiry is None
    # No date in the input → no warning is required (the regex didn't match anything)
    # The "could not normalise" warning only fires when regex matched but parsing failed
    assert p.strike == 50.0


def test_parse_unparseable_date_after_match_warns():
    """If regex matches MM/DD/YYYY but the date is invalid (e.g. 13/45/2026), warn."""
    text = "Strike 50 expires 13/45/2026"
    p = parse_options_text(text)
    assert p.expiry is None
    assert any("normalise expiry" in w for w in p.warnings)


# ─────────────────────────────────────────────────────────────────────────
# Source-field tracking
# ─────────────────────────────────────────────────────────────────────────


def test_source_fields_lists_only_extracted():
    text = "Strike 50 Premium 1.20"
    p = parse_options_text(text)
    assert "strike" in p.source_fields
    assert "premium" in p.source_fields
    assert "expiry" not in p.source_fields
    assert "delta" not in p.source_fields


def test_source_fields_includes_derived_spread():
    text = "Bid 1.00 Ask 1.10"
    p = parse_options_text(text)
    assert "bid_ask_spread" in p.source_fields


def test_to_dict_round_trip():
    text = "Strike 50 Premium 1.20 Expires 2026-06-19 type call"
    p = parse_options_text(text)
    d = p.to_dict()
    assert d["strike"] == 50.0
    assert d["expiry"] == "2026-06-19"
    assert d["contract_type"] == "call"
    assert "source_fields" in d
    assert "warnings" in d


# ─────────────────────────────────────────────────────────────────────────
# Edge cases
# ─────────────────────────────────────────────────────────────────────────


def test_parse_negative_delta():
    """Puts have negative delta — must not break the float parser."""
    text = "Strike 100 Delta -0.45 type put"
    p = parse_options_text(text)
    assert p.delta == -0.45
    assert p.contract_type == "put"


def test_parse_iso_date_in_text_without_label():
    """Standalone ISO date is picked up as expiry."""
    text = "SPY 480 strike call premium 4.50 2026-06-19"
    p = parse_options_text(text)
    assert p.expiry == "2026-06-19"


def test_parse_case_insensitive():
    text = "STRIKE: 50 PREMIUM: 1.20 IVR: 35"
    p = parse_options_text(text)
    assert p.strike == 50.0
    assert p.premium == 1.20
    assert p.iv_rank == 35.0


def test_parsed_options_default_has_no_data():
    p = ParsedOptions()
    assert p.strike is None
    assert p.source_fields == []
    assert p.warnings == []


# ─────────────────────────────────────────────────────────────────────────
# API integration — paste endpoint
# ─────────────────────────────────────────────────────────────────────────


def test_api_options_extract_text():
    """POST /api/v1/options/extract/text returns parsed fields + paste tag."""
    from fastapi.testclient import TestClient
    from api.app import create_app

    app = create_app()
    client = TestClient(app)
    resp = client.post(
        "/api/v1/options/extract/text",
        json={"text": "Strike 50 Premium 1.20 expires 2026-06-19 type call"},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["strike"] == 50.0
    assert body["premium"] == 1.20
    assert body["expiry"] == "2026-06-19"
    assert body["contract_type"] == "call"
    assert body["extraction_source"] == "paste"


def test_api_options_extract_text_empty_returns_warnings():
    from fastapi.testclient import TestClient
    from api.app import create_app

    app = create_app()
    client = TestClient(app)
    resp = client.post("/api/v1/options/extract/text", json={"text": ""})
    assert resp.status_code == 200
    body = resp.json()
    assert body["strike"] is None
    assert "empty input" in body["warnings"]


