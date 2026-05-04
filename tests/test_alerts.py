"""Tests for the position alerts engine."""
from datetime import date
from pathlib import Path
from unittest.mock import patch

import pytest

from positions import (
    Position,
    PositionAlert,
    PositionStore,
    evaluate_alerts,
    evaluate_all_open,
    sort_alerts,
)
from positions.cli import main as cli_main


def _options_position(**overrides) -> Position:
    base = dict(
        ticker="SPY", direction="long", contract_type="call",
        account_key="main", strike=580, expiry="2026-06-19",
        premium=5.50, contracts=1,
        target_price=600.0, invalidation_price=575.0,
    )
    base.update(overrides)
    return Position.open_options_position(**base)


def _scan(close=580.0, stack="full_bull", signal="neutral",
          ma_20=575.0) -> dict:
    return {
        "ticker": "SPY", "timeframe": "1d", "bar_date": "2026-04-22",
        "close": close,
        "ma_ribbon": {"ma_10": 580, "ma_20": ma_20, "ma_50": 565,
                      "ma_200": 548, "stack_state": stack},
        "stochastic": {"k": 50, "d": 50, "zone": "mid", "signal": signal},
        "sqn": {"sqn_value": 1.0, "regime": "bull"},
    }


# ─── DTE alerts ──────────────────────────────────────────────────────────────


def test_dte_expired_today_action():
    p = _options_position(expiry="2026-04-25")
    alerts = evaluate_alerts(p, _scan(), today=date(2026, 4, 25))
    rules = {a.rule for a in alerts}
    assert "dte_expired" in rules
    assert any(a.severity == "action" for a in alerts if a.rule == "dte_expired")


def test_dte_apex_low_dte_warns_then_actions():
    p_warn = _options_position(expiry="2026-05-08")  # 13 DTE
    a_warn = evaluate_alerts(p_warn, _scan(), today=date(2026, 4, 25))
    rules = {a.rule for a in a_warn}
    assert "dte_warn" in rules

    p_action = _options_position(expiry="2026-05-01")  # 6 DTE
    a_action = evaluate_alerts(p_action, _scan(), today=date(2026, 4, 25))
    rules = {a.rule for a in a_action}
    assert "dte_low" in rules
    assert any(a.severity == "action" for a in a_action if a.rule == "dte_low")


def test_dte_weekly_60_floor_actions():
    p = _options_position(account_key="weekly", expiry="2026-06-15")  # 51 DTE
    alerts = evaluate_alerts(p, _scan(), today=date(2026, 4, 25))
    rules = {a.rule for a in alerts}
    assert "dte_60_floor" in rules


def test_dte_weekly_90_warn():
    p = _options_position(account_key="weekly", expiry="2026-07-15")  # 81 DTE
    alerts = evaluate_alerts(p, _scan(), today=date(2026, 4, 25))
    rules = {a.rule for a in alerts}
    assert "dte_90_warn" in rules


def test_dte_lotto_critical():
    p = _options_position(account_key="lotto", expiry="2026-04-26")  # 1 DTE
    alerts = evaluate_alerts(p, _scan(), today=date(2026, 4, 25))
    rules = {a.rule for a in alerts}
    assert "lotto_dte_critical" in rules


def test_dte_skips_for_shares_position():
    p = Position.open_shares_position(
        ticker="AAPL", direction="long", account_key="main",
        shares=100, entry_price=30.0, invalidation_price=28.0,
    )
    alerts = evaluate_alerts(p, _scan(), today=date(2026, 4, 25))
    rules = {a.rule for a in alerts}
    # No DTE-related alerts for shares
    assert not any(r.startswith("dte_") or r.startswith("lotto_dte") for r in rules)


# ─── Price alerts ────────────────────────────────────────────────────────────


def test_target_hit_long_actions():
    p = _options_position(target_price=600.0)
    alerts = evaluate_alerts(p, _scan(close=605), today=date(2026, 4, 25))
    rules = {a.rule for a in alerts}
    assert "target_hit" in rules
    assert any(a.severity == "action" for a in alerts if a.rule == "target_hit")


def test_target_not_hit():
    p = _options_position(target_price=600.0)
    alerts = evaluate_alerts(p, _scan(close=595), today=date(2026, 4, 25))
    assert not any(a.rule == "target_hit" for a in alerts)


def test_invalidation_hit_long_actions():
    p = _options_position(invalidation_price=575.0)
    alerts = evaluate_alerts(p, _scan(close=574), today=date(2026, 4, 25))
    rules = {a.rule for a in alerts}
    assert "invalidation_hit" in rules


def test_invalidation_hit_short():
    p = _options_position(direction="short", contract_type="put",
                          target_price=560.0, invalidation_price=590.0)
    alerts = evaluate_alerts(p, _scan(close=591), today=date(2026, 4, 25))
    rules = {a.rule for a in alerts}
    assert "invalidation_hit" in rules


def test_target_hit_short():
    p = _options_position(direction="short", contract_type="put",
                          target_price=560.0, invalidation_price=590.0)
    alerts = evaluate_alerts(p, _scan(close=559), today=date(2026, 4, 25))
    rules = {a.rule for a in alerts}
    assert "target_hit" in rules


# ─── Technical alerts ────────────────────────────────────────────────────────


def test_ma_flip_against_long_actions():
    p = _options_position(direction="long")
    alerts = evaluate_alerts(p, _scan(stack="full_bear"), today=date(2026, 4, 25))
    rules = {a.rule for a in alerts}
    assert "ma_flip" in rules
    assert any(a.severity == "action" for a in alerts if a.rule == "ma_flip")


def test_ma_chop_warns():
    p = _options_position(direction="long")
    alerts = evaluate_alerts(p, _scan(stack="chop"), today=date(2026, 4, 25))
    rules = {a.rule for a in alerts}
    assert "ma_chop" in rules
    assert any(a.severity == "warn" for a in alerts if a.rule == "ma_chop")


def test_stoch_reversal_against_long_warns():
    p = _options_position(direction="long")
    alerts = evaluate_alerts(p, _scan(signal="bear_cross_overbought"),
                             today=date(2026, 4, 25))
    rules = {a.rule for a in alerts}
    assert "stoch_reversal" in rules


def test_no_alerts_when_thesis_intact():
    p = _options_position(direction="long")
    # Far from target/invalidation, healthy stack, neutral stoch, plenty of DTE
    alerts = evaluate_alerts(
        p, _scan(close=580, stack="full_bull", signal="neutral"),
        today=date(2026, 4, 25),
    )
    assert alerts == []


# ─── Position lifecycle ──────────────────────────────────────────────────────


def test_closed_positions_emit_no_alerts():
    p = _options_position()
    p.close()
    alerts = evaluate_alerts(p, _scan(close=605), today=date(2026, 4, 25))
    assert alerts == []


# ─── evaluate_all_open ───────────────────────────────────────────────────────


def test_evaluate_all_open_dedupes_scan_per_ticker(tmp_path: Path):
    store = PositionStore(path=tmp_path / "p.json")
    store.add(_options_position(ticker="SPY"))
    store.add(_options_position(
        ticker="SPY", direction="long", strike=590, target_price=600.0,
    ))
    store.add(_options_position(ticker="QQQ"))

    call_count = {"n": 0}

    def fake_scan(t):
        call_count["n"] += 1
        return _scan(close=605)  # both target_hits will fire

    out = evaluate_all_open(store, scan_fn=fake_scan, today=date(2026, 4, 25))
    # 3 positions, but only 2 unique tickers → 2 scan calls
    assert call_count["n"] == 2
    assert len(out) == 3  # one entry per position


def test_evaluate_all_open_handles_scan_failure(tmp_path: Path):
    store = PositionStore(path=tmp_path / "p.json")
    store.add(_options_position())

    def fake_scan(t):
        raise RuntimeError("network busted")

    out = evaluate_all_open(store, scan_fn=fake_scan, today=date(2026, 4, 25))
    alerts = list(out.values())[0]
    assert any(a.rule == "scan_error" for a in alerts)


def test_evaluate_all_open_empty_when_no_positions(tmp_path: Path):
    store = PositionStore(path=tmp_path / "p.json")
    out = evaluate_all_open(store, scan_fn=lambda t: _scan())
    assert out == {}


# ─── Sort ────────────────────────────────────────────────────────────────────


def test_sort_orders_action_warn_info():
    a = PositionAlert("1", "SPY", "warn", "ma_chop", "x")
    b = PositionAlert("2", "QQQ", "action", "target_hit", "x")
    c = PositionAlert("3", "IWM", "info", "scan_error", "x")
    out = sort_alerts([a, b, c])
    assert [x.severity for x in out] == ["action", "warn", "info"]


# ─── CLI ─────────────────────────────────────────────────────────────────────


def test_alerts_cli_no_open_positions(tmp_path: Path,
                                      monkeypatch: pytest.MonkeyPatch,
                                      capsys: pytest.CaptureFixture):
    monkeypatch.setattr("positions.cli.PositionStore",
                        lambda: PositionStore(path=tmp_path / "p.json"))
    rc = cli_main(["alerts"])
    assert rc == 0
    assert "no open positions" in capsys.readouterr().out


def test_alerts_cli_clean_state(tmp_path: Path,
                                monkeypatch: pytest.MonkeyPatch,
                                capsys: pytest.CaptureFixture):
    store = PositionStore(path=tmp_path / "p.json")
    store.add(_options_position())
    monkeypatch.setattr("positions.cli.PositionStore", lambda: store)

    monkeypatch.setattr(
        "positions.alerts.evaluate_all_open",
        lambda s, scan_fn=None, today=None: {p.id: [] for p in s.list_open()},
    )
    monkeypatch.setattr(
        "positions.cli.evaluate_all_open",
        lambda s, scan_fn=None, today=None: {p.id: [] for p in s.list_open()},
    )

    rc = cli_main(["alerts"])
    assert rc == 0
    assert "no alerts" in capsys.readouterr().out.lower()


def test_alerts_cli_action_severity_returns_5(tmp_path: Path,
                                              monkeypatch: pytest.MonkeyPatch,
                                              capsys: pytest.CaptureFixture):
    store = PositionStore(path=tmp_path / "p.json")
    p = store.add(_options_position())
    monkeypatch.setattr("positions.cli.PositionStore", lambda: store)

    fake_alerts = {p.id: [PositionAlert(
        position_id=p.id, ticker="SPY", severity="action",
        rule="target_hit", message="hit target",
    )]}
    monkeypatch.setattr(
        "positions.cli.evaluate_all_open",
        lambda s, scan_fn=None, today=None: fake_alerts,
    )

    rc = cli_main(["alerts"])
    assert rc == 5
    out = capsys.readouterr().out
    assert "ACTION" in out
    assert "target_hit" in out
