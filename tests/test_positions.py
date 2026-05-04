"""Tests for the positions module: model, store, and rules."""
import json
from pathlib import Path

import pytest

from config import AccountConfig, load_config
from positions import Position, PositionStore, RuleViolation, check_proposed_trade
from positions.cli import build_parser, main as cli_main


# ─── Position model ──────────────────────────────────────────────────────────


def test_open_options_position_computes_cost_and_loss():
    p = Position.open_options_position(
        ticker="SPY", direction="long", contract_type="call",
        account_key="main", strike=580, expiry="2026-06-19",
        premium=5.50, contracts=2,
    )
    assert p.ticker == "SPY"
    assert p.contracts == 2
    assert p.total_cost_usd == 1100.0  # 5.50 * 100 * 2
    assert p.max_loss_usd == 1100.0    # for long options, max loss = cost
    assert p.status == "open"
    assert len(p.id) == 8


def test_open_options_rejects_non_positive():
    with pytest.raises(ValueError):
        Position.open_options_position(
            ticker="SPY", direction="long", contract_type="call",
            account_key="main", strike=580, expiry="2026-06-19",
            premium=5.50, contracts=0,
        )
    with pytest.raises(ValueError):
        Position.open_options_position(
            ticker="SPY", direction="long", contract_type="call",
            account_key="main", strike=580, expiry="2026-06-19",
            premium=-1, contracts=1,
        )


def test_open_shares_position_computes_loss_from_stop_distance():
    p = Position.open_shares_position(
        ticker="AAPL", direction="long", account_key="main",
        shares=100, entry_price=30.0, invalidation_price=28.0,
    )
    assert p.shares == 100
    assert p.total_cost_usd == 3000.0  # 100 * 30
    assert p.max_loss_usd == 200.0     # 100 * (30 - 28)


def test_open_shares_rejects_invalidation_on_wrong_side():
    # long with invalidation ABOVE entry is wrong
    with pytest.raises(ValueError, match="protective"):
        Position.open_shares_position(
            ticker="AAPL", direction="long", account_key="main",
            shares=100, entry_price=30.0, invalidation_price=32.0,
        )


def test_close_sets_lifecycle_fields():
    p = Position.open_options_position(
        ticker="SPY", direction="long", contract_type="call",
        account_key="main", strike=580, expiry="2026-06-19",
        premium=5.50, contracts=1,
    )
    p.close(pnl_usd=275.0, notes="took profit at 50%")
    assert p.status == "closed"
    assert p.closed_date is not None
    assert p.pnl_usd == 275.0
    assert "close: took profit at 50%" in p.notes


def test_close_rejects_already_closed():
    p = Position.open_options_position(
        ticker="SPY", direction="long", contract_type="call",
        account_key="main", strike=580, expiry="2026-06-19",
        premium=5.50, contracts=1,
    )
    p.close()
    with pytest.raises(ValueError, match="already closed"):
        p.close()


def test_to_dict_round_trip():
    p = Position.open_options_position(
        ticker="SPY", direction="long", contract_type="call",
        account_key="main", strike=580, expiry="2026-06-19",
        premium=5.50, contracts=1,
    )
    d = p.to_dict()
    p2 = Position.from_dict(d)
    assert p2.id == p.id
    assert p2.ticker == p.ticker
    assert p2.total_cost_usd == p.total_cost_usd


# ─── PositionStore ───────────────────────────────────────────────────────────


def _new_position(ticker="SPY", account="main", **overrides):
    kwargs = dict(
        ticker=ticker, direction="long", contract_type="call",
        account_key=account, strike=580, expiry="2026-06-19",
        premium=5.50, contracts=1,
    )
    kwargs.update(overrides)
    return Position.open_options_position(**kwargs)


def test_store_add_and_load(tmp_path: Path):
    path = tmp_path / "positions.json"
    s1 = PositionStore(path=path)
    p = _new_position()
    s1.add(p)

    # Reload from disk in a new store
    s2 = PositionStore(path=path)
    loaded = s2.list_all()
    assert len(loaded) == 1
    assert loaded[0].id == p.id


def test_store_rejects_duplicate_id(tmp_path: Path):
    s = PositionStore(path=tmp_path / "p.json")
    p = _new_position()
    s.add(p)
    with pytest.raises(ValueError, match="already exists"):
        s.add(p)


def test_store_close_marks_position_closed(tmp_path: Path):
    s = PositionStore(path=tmp_path / "p.json")
    p = s.add(_new_position())
    closed = s.close(p.id, pnl_usd=100.0)
    assert closed.status == "closed"
    # And persists
    s2 = PositionStore(path=s.path)
    again = s2.get(p.id)
    assert again.status == "closed"
    assert again.pnl_usd == 100.0


def test_store_list_open_filters_to_account(tmp_path: Path):
    s = PositionStore(path=tmp_path / "p.json")
    s.add(_new_position(ticker="SPY", account="main"))
    s.add(_new_position(ticker="GLD", account="lotto"))

    main_open = s.list_open(account_key="main")
    lotto_open = s.list_open(account_key="lotto")
    assert len(main_open) == 1
    assert main_open[0].ticker == "SPY"
    assert len(lotto_open) == 1
    assert lotto_open[0].ticker == "GLD"


def test_store_open_premium_at_risk(tmp_path: Path):
    s = PositionStore(path=tmp_path / "p.json")
    s.add(_new_position(account="main", premium=5.50, contracts=1))  # $550
    s.add(_new_position(ticker="QQQ", account="main", premium=2.00, contracts=2))  # $400
    assert s.open_premium_at_risk("main") == 950.0


def test_store_get_raises_on_unknown(tmp_path: Path):
    s = PositionStore(path=tmp_path / "p.json")
    with pytest.raises(KeyError):
        s.get("nonexistent")


# ─── Rules check ─────────────────────────────────────────────────────────────


def _account(**raw_overrides) -> AccountConfig:
    raw = {
        "name": "Main",
        "type": "cash",
        "balance_usd": 10_000.0,
        "max_open_positions": 5,
        "max_premium_at_risk_pct": 0.10,
    }
    raw.update(raw_overrides)
    return AccountConfig(name=raw["name"], type=raw["type"],
                         balance_usd=raw["balance_usd"], raw=raw)


def test_rules_clean_when_under_all_limits():
    account = _account()
    violations = check_proposed_trade(
        proposed_max_loss_usd=200.0,
        account=account,
        account_key="main",
        open_positions=[],
    )
    assert violations == []


def test_rules_max_open_positions_blocks():
    account = _account(max_open_positions=2)
    open_positions = [_new_position(account="main"), _new_position(account="main")]
    violations = check_proposed_trade(
        proposed_max_loss_usd=100.0,
        account=account,
        account_key="main",
        open_positions=open_positions,
    )
    assert any(v.rule == "max_open_positions" for v in violations)
    assert all(v.severity == "block" for v in violations)


def test_rules_premium_at_risk_blocks():
    account = _account(max_premium_at_risk_pct=0.10)  # $1000 cap on $10K
    # Already $800 at risk
    open_positions = [_new_position(premium=8.00, contracts=1)]  # $800
    # Proposed adds $500 → $1300 = 13% > 10%
    violations = check_proposed_trade(
        proposed_max_loss_usd=500.0,
        account=account,
        account_key="main",
        open_positions=open_positions,
    )
    assert any(v.rule == "max_premium_at_risk_pct" for v in violations)


def test_rules_cash_floor_blocks_when_proposed_eats_floor():
    # Lotto-style: $1000 balance, $200 cash floor, $300 already at risk
    raw = {
        "name": "Lotto", "type": "cash", "balance_usd": 1_000.0,
        "max_open_positions": 3,
        "cash_floor_usd": 200.0,
    }
    account = AccountConfig(name="Lotto", type="cash",
                            balance_usd=1000.0, raw=raw)
    open_positions = [_new_position(account="lotto", premium=3.00, contracts=1)]  # $300
    # Proposed $600 → cash after = 1000 - 300 - 600 = 100, below 200 floor
    violations = check_proposed_trade(
        proposed_max_loss_usd=600.0,
        account=account,
        account_key="lotto",
        open_positions=open_positions,
    )
    assert any(v.rule == "cash_floor" for v in violations)


def test_rules_ignore_other_account_positions():
    account = _account(max_open_positions=2)
    # Two positions in lotto account — shouldn't count against main
    open_positions = [
        _new_position(account="lotto"), _new_position(account="lotto"),
    ]
    violations = check_proposed_trade(
        proposed_max_loss_usd=100.0,
        account=account,
        account_key="main",
        open_positions=open_positions,
    )
    assert violations == []


def test_rules_ignore_closed_positions():
    account = _account(max_open_positions=2)
    p1 = _new_position(account="main")
    p1.close()
    open_positions = [p1, _new_position(account="main")]  # only 1 open
    violations = check_proposed_trade(
        proposed_max_loss_usd=100.0,
        account=account,
        account_key="main",
        open_positions=open_positions,
    )
    assert violations == []


# ─── CLI ─────────────────────────────────────────────────────────────────────


def test_cli_open_then_list_then_close(tmp_path: Path,
                                       monkeypatch: pytest.MonkeyPatch,
                                       capsys: pytest.CaptureFixture):
    positions_file = tmp_path / "positions.json"
    monkeypatch.setattr("positions.cli.PositionStore",
                        lambda: PositionStore(path=positions_file))

    rc = cli_main([
        "open", "SPY", "--instrument", "call",
        "--strike", "580", "--expiry", "2026-06-19",
        "--premium", "5.50", "--contracts", "1",
        "--account", "main",
    ])
    assert rc == 0
    open_out = capsys.readouterr().out
    assert "Opened" in open_out
    # extract id from the output
    pos_id = open_out.split("\n")[0].split()[1].rstrip(":")

    rc = cli_main(["list"])
    assert rc == 0
    list_out = capsys.readouterr().out
    assert "SPY" in list_out
    assert pos_id in list_out

    rc = cli_main(["close", pos_id, "--pnl", "100"])
    assert rc == 0

    rc = cli_main(["list"])
    assert rc == 0
    list_after = capsys.readouterr().out
    # default list shows open only — should be empty
    assert "(no positions)" in list_after


def test_cli_open_options_requires_required_fields(tmp_path: Path,
                                                    monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr("positions.cli.PositionStore",
                        lambda: PositionStore(path=tmp_path / "p.json"))
    # Missing --premium
    rc = cli_main([
        "open", "SPY", "--instrument", "call",
        "--strike", "580", "--expiry", "2026-06-19",
        "--contracts", "1",
    ])
    assert rc == 2


def test_cli_open_shares_requires_invalidation(tmp_path: Path,
                                                monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr("positions.cli.PositionStore",
                        lambda: PositionStore(path=tmp_path / "p.json"))
    rc = cli_main([
        "open", "AAPL", "--instrument", "shares",
        "--shares", "100", "--entry-price", "30.0",
    ])
    assert rc == 2


def test_cli_close_unknown_id_returns_1(tmp_path: Path,
                                         monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr("positions.cli.PositionStore",
                        lambda: PositionStore(path=tmp_path / "p.json"))
    rc = cli_main(["close", "deadbeef"])
    assert rc == 1


def test_cli_show_outputs_json(tmp_path: Path,
                                monkeypatch: pytest.MonkeyPatch,
                                capsys: pytest.CaptureFixture):
    store = PositionStore(path=tmp_path / "p.json")
    p = store.add(_new_position())
    monkeypatch.setattr("positions.cli.PositionStore", lambda: store)

    rc = cli_main(["show", p.id])
    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["id"] == p.id
    assert payload["ticker"] == "SPY"
