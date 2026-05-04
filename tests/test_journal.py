"""Tests for the trade journal."""
import csv
from pathlib import Path

import pytest

from journal import (
    JournalStats,
    by_account,
    by_direction,
    by_instrument,
    compute_stats,
)
from journal.cli import main as cli_main
from positions.model import Position
from positions.store import PositionStore


def _closed(pnl: float, ticker: str = "SPY", account: str = "main",
            direction: str = "long", instrument: str = "call") -> Position:
    p = Position.open_options_position(
        ticker=ticker, direction=direction, contract_type=instrument,
        account_key=account, strike=580, expiry="2026-06-19",
        premium=5.0, contracts=1,
    )
    p.close(pnl_usd=pnl)
    return p


# ─── compute_stats ───────────────────────────────────────────────────────────


def test_empty_stats_is_zero():
    s = compute_stats([])
    assert s.total_trades_closed == 0
    assert s.win_rate == 0.0
    assert s.total_pnl_usd == 0.0
    assert s.profit_factor is None


def test_stats_counts_open_separately():
    open_p = Position.open_options_position(
        ticker="SPY", direction="long", contract_type="call",
        account_key="main", strike=580, expiry="2026-06-19",
        premium=5.0, contracts=1,
    )
    s = compute_stats([open_p, _closed(100), _closed(-50)])
    assert s.total_trades_closed == 2
    assert s.open_trades == 1


def test_win_rate_excludes_breakevens():
    s = compute_stats([_closed(100), _closed(100), _closed(-50), _closed(0)])
    # 2 wins, 1 loss, 1 breakeven → win rate = 2/3
    assert s.win_rate == pytest.approx(2 / 3)
    assert s.breakevens == 1


def test_total_pnl_avg_win_loss():
    s = compute_stats([_closed(100), _closed(-50), _closed(200), _closed(-100)])
    assert s.total_pnl_usd == 150.0
    assert s.avg_win_usd == 150.0     # (100+200)/2
    assert s.avg_loss_usd == -75.0    # (-50-100)/2
    assert s.largest_win_usd == 200
    assert s.largest_loss_usd == -100


def test_profit_factor_basic():
    s = compute_stats([_closed(200), _closed(-100)])
    # gross wins = 200, gross losses (abs) = 100 → PF = 2.0
    assert s.profit_factor == 2.0


def test_profit_factor_infinite_when_no_losses():
    s = compute_stats([_closed(100), _closed(50)])
    assert s.profit_factor == float("inf")


def test_profit_factor_none_when_no_decided_trades():
    s = compute_stats([_closed(0), _closed(0)])  # only breakevens
    assert s.profit_factor is None


def test_expectancy_per_trade():
    s = compute_stats([_closed(100), _closed(-50), _closed(200)])
    # total = 250, n = 3 → expectancy ≈ 83.33
    assert s.expectancy_usd == pytest.approx(250 / 3)


def test_capital_deployed():
    # 3 closed positions, each cost $500 (5.0 premium * 100 * 1 contract)
    s = compute_stats([_closed(100), _closed(-50), _closed(200)])
    assert s.total_cost_invested_usd == 1500.0
    assert s.total_max_loss_taken_usd == 1500.0  # for long options, equal


def test_to_dict_serializable():
    s = compute_stats([_closed(100), _closed(-50)])
    d = s.to_dict()
    assert d["wins"] == 1
    assert d["total_pnl_usd"] == 50.0
    assert d["profit_factor"] == 2.0


# ─── Group-by helpers ────────────────────────────────────────────────────────


def test_by_account_groups_correctly():
    positions = [
        _closed(100, account="main"),
        _closed(-50, account="main"),
        _closed(200, account="lotto"),
    ]
    groups = by_account(positions)
    assert set(groups.keys()) == {"main", "lotto"}
    assert groups["main"].total_pnl_usd == 50.0
    assert groups["lotto"].total_pnl_usd == 200.0


def test_by_instrument_groups_calls_and_puts():
    positions = [
        _closed(100, instrument="call"),
        _closed(-50, instrument="put"),
    ]
    groups = by_instrument(positions)
    assert set(groups.keys()) == {"call", "put"}


def test_by_direction_long_short():
    positions = [
        _closed(100, direction="long"),
        _closed(-50, direction="short", instrument="put"),
    ]
    groups = by_direction(positions)
    assert set(groups.keys()) == {"long", "short"}


# ─── CLI ─────────────────────────────────────────────────────────────────────


def test_cli_stats_empty(tmp_path: Path,
                         monkeypatch: pytest.MonkeyPatch,
                         capsys: pytest.CaptureFixture):
    monkeypatch.setattr("journal.cli.PositionStore",
                        lambda: PositionStore(path=tmp_path / "p.json"))
    rc = cli_main(["stats"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "Journal: all" in out
    assert "no closed positions yet" in out


def test_cli_stats_with_data(tmp_path: Path,
                             monkeypatch: pytest.MonkeyPatch,
                             capsys: pytest.CaptureFixture):
    store = PositionStore(path=tmp_path / "p.json")
    store.add(_closed(100, account="main"))
    store.add(_closed(-50, account="main"))
    store.add(_closed(200, account="lotto"))
    monkeypatch.setattr("journal.cli.PositionStore", lambda: store)

    rc = cli_main(["stats"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "Journal: all" in out
    assert "Wins / losses:    2 / 1" in out
    assert "By account" in out
    assert "[main]" in out
    assert "[lotto]" in out


def test_cli_stats_account_filter(tmp_path: Path,
                                  monkeypatch: pytest.MonkeyPatch,
                                  capsys: pytest.CaptureFixture):
    store = PositionStore(path=tmp_path / "p.json")
    store.add(_closed(100, account="main"))
    store.add(_closed(200, account="lotto"))
    monkeypatch.setattr("journal.cli.PositionStore", lambda: store)

    rc = cli_main(["stats", "--account", "main"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "Journal: main" in out
    assert "Total P&L:        $+100.00" in out
    # Per-account breakdown is suppressed when --account is set
    assert "By account" not in out


def test_cli_recent_lists_most_recent_first(tmp_path: Path,
                                            monkeypatch: pytest.MonkeyPatch,
                                            capsys: pytest.CaptureFixture):
    store = PositionStore(path=tmp_path / "p.json")
    p1 = _closed(100, ticker="SPY")
    p1.closed_date = "2026-04-20T10:00:00+00:00"
    p2 = _closed(50, ticker="QQQ")
    p2.closed_date = "2026-04-25T10:00:00+00:00"
    store.add(p1)
    store.add(p2)
    monkeypatch.setattr("journal.cli.PositionStore", lambda: store)

    rc = cli_main(["recent", "--limit", "5"])
    assert rc == 0
    out = capsys.readouterr().out
    # QQQ closed 2026-04-25 should appear before SPY closed 2026-04-20
    qqq_idx = out.find("QQQ")
    spy_idx = out.find("SPY")
    assert qqq_idx < spy_idx


def test_cli_export_writes_csv(tmp_path: Path,
                               monkeypatch: pytest.MonkeyPatch):
    store = PositionStore(path=tmp_path / "p.json")
    store.add(_closed(100, ticker="SPY"))
    store.add(_closed(-50, ticker="QQQ"))
    monkeypatch.setattr("journal.cli.PositionStore", lambda: store)

    out_csv = tmp_path / "export.csv"
    rc = cli_main(["export", str(out_csv)])
    assert rc == 0
    assert out_csv.exists()
    rows = list(csv.DictReader(out_csv.open()))
    assert len(rows) == 2
    tickers = {r["ticker"] for r in rows}
    assert tickers == {"SPY", "QQQ"}


def test_cli_export_empty(tmp_path: Path,
                          monkeypatch: pytest.MonkeyPatch,
                          capsys: pytest.CaptureFixture):
    monkeypatch.setattr("journal.cli.PositionStore",
                        lambda: PositionStore(path=tmp_path / "p.json"))
    rc = cli_main(["export", str(tmp_path / "out.csv")])
    assert rc == 0
    assert "no positions to export" in capsys.readouterr().out
