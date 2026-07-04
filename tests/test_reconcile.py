"""Tests for the broker-CSV reconciler."""
import json
from pathlib import Path

import pytest

from positions.model import Position
from reconcile.engine import reconcile
from reconcile.robinhood_csv import RobinhoodCsvError, parse_report_csv

HEADER = ("Activity Date,Process Date,Settle Date,Account Type,"
          "Instrument,Description,Trans Code,Quantity,Price,Amount")


def _csv(tmp_path: Path, rows: list[str], name: str = "report.csv") -> Path:
    path = tmp_path / name
    path.write_text("\n".join([HEADER, *rows]) + "\n")
    return path


def _option(ticker="PYPL", kind="put", strike=42.0, expiry="2026-06-26",
            contracts=4, account="lotto", entry_date=None) -> Position:
    p = Position.open_options_position(
        ticker=ticker, direction="long", contract_type=kind,
        account_key=account, strike=strike, expiry=expiry,
        premium=0.25, contracts=contracts,
    )
    if entry_date:
        p.entry_date = entry_date
    return p


# ─── parser ──────────────────────────────────────────────────────────────────


def test_parses_option_bto_and_stc(tmp_path):
    path = _csv(tmp_path, [
        '6/17/2026,6/17/2026,6/18/2026,Cash,PYPL,'
        '"PYPL 6/26/2026 Put $42.00",BTO,4,$0.25,($100.00)',
        '6/18/2026,6/18/2026,6/19/2026,Cash,PYPL,'
        '"PYPL 6/26/2026 Put $42.00",STC,4,$0.59,$235.00',
    ])
    result = parse_report_csv(path)
    assert len(result.fills) == 2
    bto, stc = result.fills
    assert (bto.ticker, bto.kind, bto.strike, bto.expiry) == \
        ("PYPL", "put", 42.0, "2026-06-26")
    assert bto.action == "open" and bto.quantity == 4
    assert bto.amount == -100.0          # parenthesized = negative
    assert bto.date == "2026-06-17"
    assert stc.action == "close" and stc.amount == 235.0


def test_parses_shares_and_skips_non_trade_rows(tmp_path):
    path = _csv(tmp_path, [
        '5/27/2026,5/27/2026,5/28/2026,Cash,MRLN,'
        'Marlin Software,Buy,70,$7.14,($499.80)',
        '5/27/2026,5/27/2026,5/27/2026,Cash,,ACH Deposit,ACH,,,$500.00',
        '6/02/2026,6/02/2026,6/02/2026,Cash,MRLN,Cash Div,CDIV,,,$1.40',
    ])
    result = parse_report_csv(path)
    assert len(result.fills) == 1
    fill = result.fills[0]
    assert (fill.ticker, fill.kind, fill.action, fill.quantity) == \
        ("MRLN", "shares", "open", 70)
    assert sorted(result.skipped_rows) == ["ACH", "CDIV"]
    assert result.warnings == []


def test_oexp_without_quantity_becomes_close_all(tmp_path):
    path = _csv(tmp_path, [
        '6/26/2026,6/26/2026,6/26/2026,Cash,PYPL,'
        '"PYPL 6/26/2026 Put $42.00",OEXP,,,',
    ])
    result = parse_report_csv(path)
    assert len(result.fills) == 1
    assert result.fills[0].action == "close"
    assert result.fills[0].quantity == 0.0


def test_bad_option_description_warns_not_crashes(tmp_path):
    path = _csv(tmp_path, [
        '6/17/2026,6/17/2026,6/18/2026,Cash,PYPL,'
        'Something Unrecognizable,BTO,4,$0.25,($100.00)',
    ])
    result = parse_report_csv(path)
    assert result.fills == []
    assert len(result.warnings) == 1
    assert "could not parse option description" in result.warnings[0]


def test_wrong_headers_raise_with_observed_headers(tmp_path):
    path = tmp_path / "other.csv"
    path.write_text("Date,Symbol,Side,Qty\n1/1/2026,SPY,Buy,1\n")
    with pytest.raises(RobinhoodCsvError) as exc:
        parse_report_csv(path)
    assert "Trans Code" in str(exc.value)     # names a missing column
    assert "Symbol" in str(exc.value)          # echoes what it saw


# ─── engine ──────────────────────────────────────────────────────────────────


def _fills(tmp_path, rows):
    return parse_report_csv(_csv(tmp_path, rows)).fills


def test_clean_match_produces_no_findings(tmp_path):
    fills = _fills(tmp_path, [
        '6/17/2026,6/17/2026,6/18/2026,Cash,PYPL,'
        '"PYPL 6/26/2026 Put $42.00",BTO,4,$0.25,($100.00)',
        '6/18/2026,6/18/2026,6/19/2026,Cash,PYPL,'
        '"PYPL 6/26/2026 Put $42.00",STC,4,$0.59,$235.00',
    ])
    p = _option(entry_date="2026-06-17T16:00:00+00:00")
    p.close(pnl_usd=135.0)
    report = reconcile(fills, [p])
    assert report.findings == []
    assert report.window_start == "2026-06-17"
    assert report.window_end == "2026-06-18"


def test_ghost_trade_flagged_high(tmp_path):
    fills = _fills(tmp_path, [
        '6/17/2026,6/17/2026,6/18/2026,Cash,NVDA,'
        '"NVDA 6/26/2026 Call $150.00",BTO,1,$2.00,($200.00)',
    ])
    report = reconcile(fills, [])
    assert len(report.findings) == 1
    f = report.findings[0]
    assert f.category == "ghost_trade" and f.severity == "high"
    assert "NVDA" in f.contract
    assert report.has_high_severity


def test_stale_open_flagged_when_broker_fully_closed(tmp_path):
    fills = _fills(tmp_path, [
        '6/17/2026,6/17/2026,6/18/2026,Cash,PYPL,'
        '"PYPL 6/26/2026 Put $42.00",BTO,4,$0.25,($100.00)',
        '6/18/2026,6/18/2026,6/19/2026,Cash,PYPL,'
        '"PYPL 6/26/2026 Put $42.00",STC,4,$0.59,$235.00',
    ])
    p = _option(entry_date="2026-06-17T16:00:00+00:00")  # still open
    report = reconcile(fills, [p])
    cats = [f.category for f in report.findings]
    assert "stale_open" in cats
    stale = next(f for f in report.findings if f.category == "stale_open")
    assert stale.position_ids == [p.id]


def test_stale_open_via_expiration_close_all(tmp_path):
    fills = _fills(tmp_path, [
        '6/17/2026,6/17/2026,6/18/2026,Cash,PYPL,'
        '"PYPL 6/26/2026 Put $42.00",BTO,4,$0.25,($100.00)',
        '6/26/2026,6/26/2026,6/26/2026,Cash,PYPL,'
        '"PYPL 6/26/2026 Put $42.00",OEXP,,,',
    ])
    p = _option(entry_date="2026-06-17T16:00:00+00:00")
    report = reconcile(fills, [p])
    assert any(f.category == "stale_open" for f in report.findings)


def test_qty_mismatch_flagged_medium(tmp_path):
    fills = _fills(tmp_path, [
        '6/17/2026,6/17/2026,6/18/2026,Cash,PYPL,'
        '"PYPL 6/26/2026 Put $42.00",BTO,4,$0.25,($100.00)',
    ])
    p = _option(contracts=2, entry_date="2026-06-17T16:00:00+00:00")
    report = reconcile(fills, [p])
    assert len(report.findings) == 1
    f = report.findings[0]
    assert f.category == "qty_mismatch" and f.severity == "medium"
    assert "4" in f.detail and "2" in f.detail


def test_partial_exits_count_toward_original_size(tmp_path):
    fills = _fills(tmp_path, [
        '6/17/2026,6/17/2026,6/18/2026,Cash,PYPL,'
        '"PYPL 6/26/2026 Put $42.00",BTO,4,$0.25,($100.00)',
        '6/18/2026,6/18/2026,6/19/2026,Cash,PYPL,'
        '"PYPL 6/26/2026 Put $42.00",STC,4,$0.59,$235.00',
    ])
    p = _option(contracts=4, entry_date="2026-06-17T16:00:00+00:00")
    p.partial_close(contracts_closed=1, pnl_usd=20.0)
    p.partial_close(contracts_closed=3, pnl_usd=115.0)   # now closed, contracts=0
    report = reconcile(fills, [p])
    assert report.findings == []


def test_journal_only_flagged_inside_window_only(tmp_path):
    fills = _fills(tmp_path, [
        '6/17/2026,6/17/2026,6/18/2026,Cash,QQQ,'
        '"QQQ 12/18/2026 Call $500.00",BTO,1,$20.00,($2000.00)',
    ])
    in_window = _option(ticker="TSLA", kind="call", strike=300.0,
                        expiry="2026-07-17",
                        entry_date="2026-06-17T20:00:00+00:00")
    out_of_window = _option(ticker="GLD", kind="call", strike=250.0,
                            expiry="2026-09-18",
                            entry_date="2026-03-02T20:00:00+00:00")
    qqq = _option(ticker="QQQ", kind="call", strike=500.0,
                  expiry="2026-12-18", contracts=1,
                  entry_date="2026-06-17T20:00:00+00:00")
    report = reconcile(fills, [in_window, out_of_window, qqq])
    assert len(report.findings) == 1
    f = report.findings[0]
    assert f.category == "journal_only"
    assert f.position_ids == [in_window.id]


def test_unpadded_journal_expiry_still_matches(tmp_path):
    # Real positions.json contains hand-entered expiries like "2026-7-17";
    # the CSV side normalizes to "2026-07-17" — keys must still match.
    fills = _fills(tmp_path, [
        '5/10/2026,5/10/2026,5/11/2026,Cash,HBM,'
        '"HBM 7/17/2026 Call $30.00",BTO,3,$1.00,($300.00)',
    ])
    p = _option(ticker="HBM", kind="call", strike=30.0, expiry="2026-7-17",
                contracts=3, entry_date="2026-05-10T16:00:00+00:00")
    report = reconcile(fills, [p])
    assert report.findings == []


def test_short_side_code_flagged_high(tmp_path):
    fills = _fills(tmp_path, [
        '6/17/2026,6/17/2026,6/18/2026,Cash,SPY,'
        '"SPY 6/26/2026 Put $600.00",STO,1,$2.00,$200.00',
    ])
    report = reconcile(fills, [])
    cats = {f.category for f in report.findings}
    assert "short_side_code" in cats
    assert report.has_high_severity


def test_empty_csv_reconciles_to_empty_report(tmp_path):
    report = reconcile([], [_option()])
    assert report.findings == []
    assert report.fills_count == 0


# ─── CLI ─────────────────────────────────────────────────────────────────────


def test_cli_exit_codes_and_json(tmp_path, monkeypatch, capsys):
    from positions.store import PositionStore
    from reconcile import cli

    csv_path = _csv(tmp_path, [
        '6/17/2026,6/17/2026,6/18/2026,Cash,NVDA,'
        '"NVDA 6/26/2026 Call $150.00",BTO,1,$2.00,($200.00)',
    ])
    monkeypatch.setattr(
        cli, "PositionStore",
        lambda: PositionStore(path=tmp_path / "positions.json"),
    )

    json_out = tmp_path / "report.json"
    rc = cli.main([str(csv_path), "--json", str(json_out)])
    assert rc == 1                                  # ghost trade → high severity
    out = capsys.readouterr().out
    assert "ghost_trade" in out
    payload = json.loads(json_out.read_text())
    assert payload["findings"][0]["category"] == "ghost_trade"


def test_cli_bad_file_returns_2(tmp_path, capsys):
    from reconcile import cli
    rc = cli.main([str(tmp_path / "nope.csv")])
    assert rc == 2


# ─── statement PDF parser ────────────────────────────────────────────────────
# Fixture lines are verbatim pypdf extractions from a real 2026-05 statement.

from reconcile.statement_pdf import parse_statement_lines


def test_statement_option_row_parses():
    result = parse_statement_lines([
        "TLT 06/05/2026 Put $86.00 TLT Cash BTO 04/30/2026 2 $1.48000 $296.08",
    ])
    assert result.warnings == []
    [f] = result.fills
    assert (f.ticker, f.kind, f.strike, f.expiry) == ("TLT", "put", 86.0, "2026-06-05")
    assert (f.action, f.code, f.quantity, f.date) == ("open", "BTO", 2, "2026-04-30")
    assert f.price == 1.48 and f.amount == 296.08


def test_statement_shares_row_parses_with_cusip_line():
    result = parse_statement_lines([
        "Lithium Americas",
        "CUSIP: 53681J103 LAC Cash Buy 05/05/2026 182 $5.47760 $996.92",
        "CUSIP: 53681J103 LAC Cash Buy 05/05/2026 0.561705 $5.47760 $3.08",
    ])
    assert result.warnings == []
    assert [f.quantity for f in result.fills] == [182, 0.561705]
    assert all(f.ticker == "LAC" and f.kind == "shares" for f in result.fills)


def test_statement_pending_settlement_rows_parse():
    # Pending option rows have no Symbol column and two dates; pending
    # shares rows have no symbol at all — recovered via the CUSIP map.
    result = parse_statement_lines([
        "Merlin, Inc.",
        "CUSIP: 590106100 MRLN Cash Buy 05/22/2026 70 $7.14000 $499.80",
        "NVDA 06/08/2026 Call $232.50 Cash BTO 05/29/2026 06/01/2026 1 $0.96000 $96.04",
        "Merlin, Inc.",
        "CUSIP: 590106100 Cash Sell 05/29/2026 06/01/2026 70.028011 $7.86000 $550.39",
    ])
    assert result.warnings == []
    nvda = next(f for f in result.fills if f.ticker == "NVDA")
    assert (f"{nvda.strike}", nvda.expiry, nvda.date) == ("232.5", "2026-06-08", "2026-05-29")
    mrln_sell = next(f for f in result.fills if f.code == "Sell")
    assert mrln_sell.ticker == "MRLN" and mrln_sell.quantity == 70.028011


def test_statement_pending_shares_unknown_cusip_warns():
    result = parse_statement_lines([
        "Mystery Corp.",
        "CUSIP: 999999999 Cash Sell 05/29/2026 06/01/2026 5 $10.00000 $50.00",
    ])
    assert result.fills == []
    assert len(result.warnings) == 1
    assert "unknown CUSIP 999999999" in result.warnings[0]


def test_statement_skips_summary_and_cash_rows():
    result = parse_statement_lines([
        "Portfolio Summary",
        "XLE 06/30/2026 Call $60.00",
        "Estimated Yield: 0.00% XLE Cash 2 $0.51000 $102.00 $0.00 0.95%",
        "ACH Deposit Cash ACH 05/08/2026 $187.50",
        "Event Contracts Inter-Entity Cash Transfer Cash FUTSWP 05/08/2026 $39.72",
        "Total Funds Paid and Received $12,802.64 $12,195.84",
    ])
    assert result.fills == []
    assert result.warnings == []
