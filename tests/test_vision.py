"""Tests for vision/options_extractor.py.

All tests mock the anthropic client — no real API calls.
"""
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from vision import ExtractError, extract_options_chain, extract_truth_fixture
from vision.options_extractor import _parse_json_response, _strip_fences


def _fake_client(text_response: str) -> MagicMock:
    block = MagicMock()
    block.text = text_response
    response = MagicMock()
    response.content = [block]
    client = MagicMock()
    client.messages.create.return_value = response
    return client


def _make_image(tmp_path: Path, name: str = "img.png") -> Path:
    p = tmp_path / name
    # Minimal valid PNG signature so file exists and has bytes
    p.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 32)
    return p


# ─── Helpers ──────────────────────────────────────────────────────────────────


def test_strip_fences_handles_plain_text():
    assert _strip_fences("just text") == "just text"


def test_strip_fences_strips_triple_backticks():
    text = "```json\n{\"a\": 1}\n```"
    assert _strip_fences(text).strip() == '{"a": 1}'


def test_strip_fences_handles_no_closing():
    text = "```\n{\"a\": 1}"
    assert _strip_fences(text).strip() == '{"a": 1}'


def test_parse_json_response_clean():
    assert _parse_json_response('{"a": 1}', "test") == {"a": 1}


def test_parse_json_response_with_fences():
    assert _parse_json_response('```\n{"a": 1}\n```', "test") == {"a": 1}


def test_parse_json_response_recovers_from_extra_prose():
    # Even when Claude ignores instructions and adds prose
    text = 'Here is the data:\n{"a": 1, "b": 2}\nHope that helps!'
    assert _parse_json_response(text, "test") == {"a": 1, "b": 2}


def test_parse_json_response_raises_on_unparseable():
    with pytest.raises(ExtractError):
        _parse_json_response("not json at all", "test")


# ─── extract_options_chain ────────────────────────────────────────────────────


def test_extract_options_chain_happy_path(tmp_path: Path):
    img = _make_image(tmp_path)
    client = _fake_client(
        '{"strike": 730, "premium": 1.50, "iv_rank": 28, '
        '"open_interest": 12000, "bid_ask_spread": 0.05, '
        '"expiry": "2026-06-19", "contract_type": "call"}'
    )

    result = extract_options_chain(img, "SPY", client=client)

    assert result["strike"] == 730
    assert result["premium"] == 1.50
    assert result["iv_rank"] == 28
    assert result["open_interest"] == 12000
    assert result["bid_ask_spread"] == 0.05
    assert result["expiry"] == "2026-06-19"
    assert result["contract_type"] == "call"

    # Verify the prompt included the ticker
    args, kwargs = client.messages.create.call_args
    text_block = next(b for b in kwargs["messages"][0]["content"] if b["type"] == "text")
    assert "SPY" in text_block["text"]


def test_extract_options_chain_with_target_constraints(tmp_path: Path):
    img = _make_image(tmp_path)
    client = _fake_client(
        '{"strike": 250, "premium": 0.80, "iv_rank": null, '
        '"open_interest": null, "bid_ask_spread": null, '
        '"expiry": "2026-05-09", "contract_type": "call"}'
    )

    extract_options_chain(
        img, "GLD",
        target_strike=250, target_expiry="2026-05-09", contract_type="call",
        client=client,
    )

    _, kwargs = client.messages.create.call_args
    text_block = next(b for b in kwargs["messages"][0]["content"] if b["type"] == "text")
    assert "$250" in text_block["text"]
    assert "2026-05-09" in text_block["text"]
    assert "call" in text_block["text"]


def test_extract_options_chain_handles_null_fields(tmp_path: Path):
    img = _make_image(tmp_path)
    client = _fake_client(
        '{"strike": 100, "premium": 2.0, "iv_rank": null, '
        '"open_interest": null, "bid_ask_spread": null, '
        '"expiry": "2026-05-22", "contract_type": "call"}'
    )

    result = extract_options_chain(img, "FAKE", client=client)
    assert result["iv_rank"] is None
    assert result["open_interest"] is None
    assert result["bid_ask_spread"] is None


def test_extract_options_chain_raises_when_image_missing(tmp_path: Path):
    client = _fake_client("{}")
    with pytest.raises(ExtractError, match="not found"):
        extract_options_chain(tmp_path / "nope.png", "SPY", client=client)


def test_extract_options_chain_raises_on_unparseable_response(tmp_path: Path):
    img = _make_image(tmp_path)
    client = _fake_client("the model failed to comply")
    with pytest.raises(ExtractError):
        extract_options_chain(img, "SPY", client=client)


def test_extract_options_chain_raises_on_non_object_json(tmp_path: Path):
    img = _make_image(tmp_path)
    client = _fake_client('[1, 2, 3]')
    with pytest.raises(ExtractError, match="Expected JSON object"):
        extract_options_chain(img, "SPY", client=client)


def test_extract_options_chain_no_api_key_raises(tmp_path: Path,
                                                  monkeypatch: pytest.MonkeyPatch):
    img = _make_image(tmp_path)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    with pytest.raises(ExtractError, match="ANTHROPIC_API_KEY"):
        extract_options_chain(img, "SPY")


def test_extract_options_chain_handles_api_failure(tmp_path: Path):
    img = _make_image(tmp_path)
    client = MagicMock()
    client.messages.create.side_effect = Exception("rate limit")
    with pytest.raises(ExtractError, match="API call failed"):
        extract_options_chain(img, "SPY", client=client)


# ─── extract_truth_fixture ────────────────────────────────────────────────────


def test_extract_truth_fixture_happy_path(tmp_path: Path):
    img = _make_image(tmp_path)
    client = _fake_client(
        '{"rows": ['
        '{"date": "2026-04-22", "ma_10": 580.5, "ma_20": 575.2}, '
        '{"date": "2026-04-23", "ma_10": 581.0, "ma_20": 575.8}'
        ']}'
    )

    rows = extract_truth_fixture(
        img, "SPY", indicator_name="MA Ribbon",
        value_columns=["ma_10", "ma_20"],
        client=client,
    )
    assert len(rows) == 2
    assert rows[0]["date"] == "2026-04-22"
    assert rows[0]["ma_10"] == 580.5


def test_extract_truth_fixture_raises_on_missing_rows_key(tmp_path: Path):
    img = _make_image(tmp_path)
    client = _fake_client('{"data": []}')
    with pytest.raises(ExtractError, match="rows"):
        extract_truth_fixture(img, "SPY", "MA Ribbon", ["ma_10"], client=client)


# ─── CLI integration ──────────────────────────────────────────────────────────


@patch("kill_sheet.cli.scan_ticker", create=True)
@patch("kill_sheet.cli.compute_multi_tf", create=True)
def test_cli_screenshot_fills_apex_fields(mock_multi, mock_scan, tmp_path: Path,
                                          monkeypatch: pytest.MonkeyPatch,
                                          capsys: pytest.CaptureFixture):
    img = _make_image(tmp_path)
    mock_scan.return_value = {
        "ticker": "SPY", "timeframe": "1d", "bar_date": "2026-04-22", "close": 580.0,
        "ma_ribbon": {"ma_10": 578, "ma_20": 575, "ma_50": 565, "ma_200": 548,
                      "stack_state": "full_bull"},
        "stochastic": {"k": 25, "d": 23, "zone": "oversold",
                       "signal": "bull_cross_oversold"},
        "sqn": {"sqn_value": 1.0, "regime": "bull"},
    }
    mock_multi.return_value = {"1wk": {"error": "skip"}, "4h": {"error": "skip"}}
    monkeypatch.setattr("kill_sheet.cli.KILL_SHEETS_DIR", tmp_path / "ks")
    monkeypatch.setattr("kill_sheet.cli.load_config",
                        lambda: __import__("config").load_config(Path("/nope.yaml")))

    fake_extract = MagicMock(return_value={
        "strike": 580.0, "premium": 5.0, "iv_rank": 30.0,
        "open_interest": 8000, "bid_ask_spread": 0.10,
        "expiry": "2026-06-19", "contract_type": "call",
    })
    monkeypatch.setattr("vision.extract_options_chain", fake_extract)
    # Also patch the import path used in cli._maybe_apply_screenshot
    import vision
    monkeypatch.setattr(vision, "extract_options_chain", fake_extract)

    from kill_sheet.cli import main
    code = main([
        "SPY", "--direction", "long", "--screenshot", str(img),
        "--target", "590", "--invalidation", "575",
        "--no-persist", "--skip-devil",
    ])
    assert code == 0
    out = capsys.readouterr().out
    # Apex options block populated from screenshot
    assert "OPTION STRUCTURE:" in out
    assert "$580" in out  # strike
    assert "$5.00" in out  # premium
    assert "30.0%" in out  # IV Rank rendered as "30.0% (cheap)"
    assert fake_extract.called
