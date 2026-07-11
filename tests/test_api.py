"""FastAPI integration tests via TestClient.

All scan_ticker / compute_multi_tf calls are mocked — no real network.
"""
from pathlib import Path
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from api.app import create_app
from config import load_config
from positions.model import Position
from positions.store import PositionStore


_FAKE_DAILY = {
    "ticker": "SPY", "timeframe": "1d", "bar_date": "2026-04-22", "close": 580.45,
    "ma_ribbon": {"ma_10": 578.9, "ma_20": 573.2, "ma_50": 565.4,
                  "ma_200": 548.1, "stack_state": "full_bull"},
    "stochastic": {"k": 25.3, "d": 23.1, "zone": "oversold",
                   "signal": "bull_cross_oversold"},
    "sqn": {
        "sqn_value": 1.20, "regime": "bull",
        "sqn_20_value": 1.0, "regime_20": "bull",
        "diagnostic": "healthy_trend",
    },
}

_FAKE_DAILY_BEAR = {
    **_FAKE_DAILY,
    "sqn": {
        "sqn_value": -1.20, "regime": "bear",
        "sqn_20_value": -1.0, "regime_20": "bear",
        "diagnostic": "confluence_bearish",
    },
}


@pytest.fixture
def client(tmp_path):
    positions_path = tmp_path / "positions.json"

    def store_factory():
        return PositionStore(path=positions_path)

    def config_loader():
        return load_config(Path("/nonexistent.yaml"))

    app = create_app(store_factory=store_factory, config_loader=config_loader)
    return TestClient(app), store_factory


# ─── Health ───────────────────────────────────────────────────────────────────


def test_health(client):
    c, _ = client
    r = c.get("/api/v1/health")
    assert r.status_code == 200
    assert r.json()["status"] == "ok"
    assert "version" in r.json()


# ─── Scan ─────────────────────────────────────────────────────────────────────


@patch("api.routes.indicators.scan_ticker", return_value=_FAKE_DAILY)
def test_scan_endpoint(mock_scan, client):
    c, _ = client
    r = c.get("/api/v1/scan/SPY")
    assert r.status_code == 200
    body = r.json()
    assert body["ticker"] == "SPY"
    assert body["ma_ribbon"]["stack_state"] == "full_bull"
    assert mock_scan.called


@patch("api.routes.indicators.scan_ticker", side_effect=ValueError("no bars"))
def test_scan_endpoint_bubbles_502_on_failure(mock_scan, client):
    c, _ = client
    r = c.get("/api/v1/scan/BADSYM")
    assert r.status_code == 502
    assert "no bars" in r.json()["detail"]


@patch("api.routes.indicators.compute_multi_tf")
def test_scan_multi_endpoint(mock_multi, client):
    mock_multi.return_value = {
        "1d": _FAKE_DAILY,
        "1wk": {"error": "weekly bars unavailable"},
        "4h": _FAKE_DAILY,
    }
    c, _ = client
    r = c.get("/api/v1/scan/SPY/multi")
    assert r.status_code == 200
    body = r.json()
    assert body["1d"]["ticker"] == "SPY"
    assert body["1wk"] == {"error": "weekly bars unavailable", "ticker": None}
    assert body["4h"]["ticker"] == "SPY"


# ─── Kill sheet ───────────────────────────────────────────────────────────────


@patch("api.routes.kill_sheet.compute_multi_tf",
       return_value={"1wk": {"error": "skip"}, "4h": {"error": "skip"}})
@patch("api.routes.kill_sheet.scan_ticker", return_value=_FAKE_DAILY)
def test_kill_sheet_basic_post(mock_scan, mock_multi, client):
    c, _ = client
    r = c.post("/api/v1/kill_sheet", json={
        "ticker": "SPY",
        "direction": "long",
        "account": "main",
        "skip_devil": True,
    })
    assert r.status_code == 200
    body = r.json()
    assert body["kill_sheet"]["ticker"] == "SPY"
    assert body["kill_sheet"]["bias"] == "BULLISH"
    assert "KILL SHEET: SPY" in body["rendered_text"]
    assert body["rule_violations"] == []
    assert body["rules_blocked"] is False
    assert body["devil"] is None  # skip_devil=True


@patch("api.routes.kill_sheet.compute_multi_tf",
       return_value={"1wk": {"error": "skip"}, "4h": {"error": "skip"}})
@patch("api.routes.kill_sheet.scan_ticker", return_value=_FAKE_DAILY)
def test_kill_sheet_runs_devil_when_above_threshold(mock_scan, mock_multi, client):
    c, _ = client
    r = c.post("/api/v1/kill_sheet", json={
        "ticker": "SPY", "direction": "long", "account": "main",
        "target": 600, "invalidation": 575,
    })
    assert r.status_code == 200
    body = r.json()
    # 2.5% high conviction × $10K = $250 → above $150 → devil fires
    assert body["devil"] is not None
    assert body["devil"]["aggregate"] in ("KILL", "CONDITIONAL PROCEED", "PROCEED")
    assert len(body["devil"]["results"]) == 8


@patch("api.routes.kill_sheet.compute_multi_tf",
       return_value={"1wk": {"error": "skip"}, "4h": {"error": "skip"}})
@patch("api.routes.kill_sheet.scan_ticker", return_value=_FAKE_DAILY)
def test_kill_sheet_unknown_account_returns_400(mock_scan, mock_multi, client):
    c, _ = client
    r = c.post("/api/v1/kill_sheet", json={
        "ticker": "SPY", "direction": "long", "account": "ghost",
    })
    assert r.status_code == 400


@patch("api.routes.kill_sheet.compute_multi_tf",
       return_value={"1wk": {"error": "skip"}, "4h": {"error": "skip"}})
@patch("api.routes.kill_sheet.scan_ticker", return_value=_FAKE_DAILY)
def test_kill_sheet_renders_apex_options_block(mock_scan, mock_multi, client):
    c, _ = client
    r = c.post("/api/v1/kill_sheet", json={
        "ticker": "SPY", "direction": "long", "account": "main",
        "strike": 580, "premium": 5.0, "expiry": "2026-06-19", "contract_type": "call",
        "iv_rank": 30, "oi": 8000, "spread": 0.05,
        "target": 600, "invalidation": 575,
        "skip_devil": True,
    })
    assert r.status_code == 200
    body = r.json()
    assert body["kill_sheet"]["options"] is not None
    assert body["kill_sheet"]["options"]["strike"] == 580
    assert "OPTION STRUCTURE:" in body["rendered_text"]


# ─── Positions ────────────────────────────────────────────────────────────────


def _open_args(**overrides):
    # Phase B: tests that aren't specifically exercising the kill-sheet
    # authorization gate use the bypass affordance (with a documented reason
    # in notes — required by the validator).
    base = dict(
        ticker="SPY", direction="long", instrument="call", account="main",
        strike=580, expiry="2026-06-19", premium=5.50, contracts=1,
        bypass_kill_sheet=True,
        notes="test fixture — kill-sheet gate not under test",
    )
    base.update(overrides)
    return base


def test_open_position_returns_201(client):
    c, _ = client
    r = c.post("/api/v1/positions", json=_open_args())
    assert r.status_code == 201
    body = r.json()
    assert body["ticker"] == "SPY"
    assert body["status"] == "open"
    assert len(body["id"]) == 8


def test_open_position_missing_required_returns_400(client):
    c, _ = client
    # missing premium — bypass the gate so the missing-premium 400 surfaces
    r = c.post("/api/v1/positions", json={
        "ticker": "SPY", "instrument": "call",
        "strike": 580, "expiry": "2026-06-19", "contracts": 1,
        "bypass_kill_sheet": True,
        "notes": "test fixture",
    })
    assert r.status_code == 400


def test_list_positions_filters_by_status_and_account(client):
    c, store_factory = client
    store = store_factory()
    p1 = Position.open_options_position(**{
        "ticker": "SPY", "direction": "long", "contract_type": "call",
        "account_key": "main", "strike": 580, "expiry": "2026-06-19",
        "premium": 5.0, "contracts": 1,
    })
    store.add(p1)
    p2 = Position.open_options_position(**{
        "ticker": "GLD", "direction": "long", "contract_type": "call",
        "account_key": "lotto", "strike": 250, "expiry": "2026-05-09",
        "premium": 0.80, "contracts": 1,
    })
    store.add(p2)
    p2_closed = store.close(p2.id, pnl_usd=20)

    # Open in main: 1
    r = c.get("/api/v1/positions", params={"status": "open", "account": "main"})
    assert r.status_code == 200
    assert len(r.json()) == 1
    assert r.json()[0]["ticker"] == "SPY"

    # Closed in any account: 1
    r = c.get("/api/v1/positions", params={"status": "closed"})
    assert len(r.json()) == 1
    assert r.json()[0]["ticker"] == "GLD"

    # All: 2
    r = c.get("/api/v1/positions", params={"status": "all"})
    assert len(r.json()) == 2


def test_get_position_404_when_unknown(client):
    c, _ = client
    r = c.get("/api/v1/positions/deadbeef")
    assert r.status_code == 404


def test_close_position_round_trip(client):
    c, _ = client
    r = c.post("/api/v1/positions", json=_open_args())
    pid = r.json()["id"]
    r2 = c.post(f"/api/v1/positions/{pid}/close", json={"pnl": 87.5,
                                                        "notes": "took profits"})
    assert r2.status_code == 200
    body = r2.json()
    assert body["status"] == "closed"
    assert body["pnl_usd"] == 87.5
    assert "took profits" in (body.get("notes") or "")


def test_close_already_closed_returns_409(client):
    c, _ = client
    r = c.post("/api/v1/positions", json=_open_args())
    pid = r.json()["id"]
    c.post(f"/api/v1/positions/{pid}/close", json={"pnl": 0.0})
    r3 = c.post(f"/api/v1/positions/{pid}/close", json={"pnl": 0.0})
    assert r3.status_code == 409


def test_partial_close_keeps_position_open(client):
    c, _ = client
    r = c.post("/api/v1/positions", json=_open_args(contracts=3))
    pid = r.json()["id"]

    r2 = c.post(
        f"/api/v1/positions/{pid}/close",
        json={"pnl": 60.0, "notes": "trim 1", "contracts": 1},
    )
    assert r2.status_code == 200
    body = r2.json()
    assert body["status"] == "open"
    assert body["contracts"] == 2
    assert len(body["partial_exits"]) == 1
    assert body["partial_exits"][0]["contracts_closed"] == 1
    assert body["partial_exits"][0]["pnl_usd"] == 60.0


def test_partial_then_full_close_aggregates_pnl(client):
    c, _ = client
    r = c.post("/api/v1/positions", json=_open_args(contracts=2))
    pid = r.json()["id"]

    c.post(f"/api/v1/positions/{pid}/close",
           json={"pnl": 50.0, "contracts": 1})
    r2 = c.post(f"/api/v1/positions/{pid}/close",
                json={"pnl": 75.0, "contracts": 1})
    assert r2.status_code == 200
    body = r2.json()
    assert body["status"] == "closed"
    assert body["contracts"] == 0
    assert body["pnl_usd"] == 125.0
    assert len(body["partial_exits"]) == 2


def test_partial_close_rejects_too_many_contracts(client):
    c, _ = client
    r = c.post("/api/v1/positions", json=_open_args(contracts=2))
    pid = r.json()["id"]
    r2 = c.post(f"/api/v1/positions/{pid}/close",
                json={"pnl": 0.0, "contracts": 5})
    assert r2.status_code == 409


def test_journal_exits_lists_partial_legs(client):
    c, _ = client
    # Two positions: one partial-closed once (still open), one closed
    # legacy-style (single shot, no contracts param).
    r1 = c.post("/api/v1/positions", json=_open_args(contracts=3, ticker="HBM"))
    p1_id = r1.json()["id"]
    c.post(f"/api/v1/positions/{p1_id}/close",
           json={"pnl": 78.0, "contracts": 2, "notes": "trim winner"})

    r2 = c.post("/api/v1/positions", json=_open_args(contracts=1, ticker="RDW"))
    p2_id = r2.json()["id"]
    c.post(f"/api/v1/positions/{p2_id}/close", json={"pnl": 50.0})

    r = c.get("/api/v1/journal/exits")
    assert r.status_code == 200
    events = r.json()
    assert len(events) == 2

    # Partial-close leg is present
    hbm = next(e for e in events if e["ticker"] == "HBM")
    assert hbm["is_partial"] is True
    assert hbm["contracts_closed"] == 2
    assert hbm["pnl_usd"] == 78.0
    assert hbm["position_id"] == p1_id

    # Legacy full close is also present as a single event
    rdw = next(e for e in events if e["ticker"] == "RDW")
    assert rdw["is_partial"] is False
    assert rdw["pnl_usd"] == 50.0


def test_journal_exits_no_double_count_on_partial_path_full_close(client):
    """A position fully closed via the partial path should produce one event
    per leg — not duplicate entries combining legs and the parent position."""
    c, _ = client
    r = c.post("/api/v1/positions", json=_open_args(contracts=2, ticker="ABC"))
    pid = r.json()["id"]
    c.post(f"/api/v1/positions/{pid}/close", json={"pnl": 40.0, "contracts": 1})
    c.post(f"/api/v1/positions/{pid}/close", json={"pnl": 60.0, "contracts": 1})

    events = c.get("/api/v1/journal/exits").json()
    abc = [e for e in events if e["position_id"] == pid]
    assert len(abc) == 2  # one event per leg, no third "parent" event
    assert sum(e["pnl_usd"] for e in abc) == 100.0
    assert all(e["is_partial"] for e in abc)


# ─── Alerts ───────────────────────────────────────────────────────────────────


@patch("api.routes.positions.evaluate_all_open")
def test_alerts_endpoint_returns_flat_list(mock_eval, client):
    from positions.alerts import PositionAlert

    mock_eval.return_value = {
        "abc123": [
            PositionAlert("abc123", "SPY", "action", "target_hit", "hit"),
            PositionAlert("abc123", "SPY", "warn", "ma_chop", "chop"),
        ]
    }
    c, _ = client
    r = c.get("/api/v1/positions/alerts")
    assert r.status_code == 200
    body = r.json()
    assert len(body) == 2
    assert body[0]["severity"] == "action"


# ─── Journal ──────────────────────────────────────────────────────────────────


def test_journal_stats_empty(client):
    c, _ = client
    r = c.get("/api/v1/journal/stats")
    assert r.status_code == 200
    body = r.json()
    assert body["total_trades_closed"] == 0
    assert body["label"] == "all"


def test_journal_stats_with_data(client):
    c, store_factory = client
    store = store_factory()
    p1 = Position.open_options_position(**{
        "ticker": "SPY", "direction": "long", "contract_type": "call",
        "account_key": "main", "strike": 580, "expiry": "2026-06-19",
        "premium": 5.0, "contracts": 1,
    })
    p1.close(pnl_usd=200)
    store.add(p1)

    p2 = Position.open_options_position(**{
        "ticker": "QQQ", "direction": "long", "contract_type": "call",
        "account_key": "main", "strike": 350, "expiry": "2026-06-19",
        "premium": 3.0, "contracts": 1,
    })
    p2.close(pnl_usd=-50)
    store.add(p2)

    r = c.get("/api/v1/journal/stats")
    body = r.json()
    assert body["wins"] == 1
    assert body["losses"] == 1
    assert body["total_pnl_usd"] == 150.0


def test_journal_breakdown(client):
    c, store_factory = client
    store = store_factory()
    p = Position.open_options_position(**{
        "ticker": "SPY", "direction": "long", "contract_type": "call",
        "account_key": "main", "strike": 580, "expiry": "2026-06-19",
        "premium": 5.0, "contracts": 1,
    })
    p.close(pnl_usd=200)
    store.add(p)

    r = c.get("/api/v1/journal/breakdown")
    assert r.status_code == 200
    body = r.json()
    assert body["overall"]["total_pnl_usd"] == 200
    assert "main" in body["by_account"]
    assert "call" in body["by_instrument"]
    assert "long" in body["by_direction"]


def test_journal_recent_orders_by_close_date(client):
    c, store_factory = client
    store = store_factory()
    p1 = Position.open_options_position(**{
        "ticker": "SPY", "direction": "long", "contract_type": "call",
        "account_key": "main", "strike": 580, "expiry": "2026-06-19",
        "premium": 5.0, "contracts": 1,
    })
    p1.close(pnl_usd=100)
    p1.closed_date = "2026-04-20T10:00:00+00:00"
    store.add(p1)
    p2 = Position.open_options_position(**{
        "ticker": "QQQ", "direction": "long", "contract_type": "call",
        "account_key": "main", "strike": 350, "expiry": "2026-06-19",
        "premium": 3.0, "contracts": 1,
    })
    p2.close(pnl_usd=50)
    p2.closed_date = "2026-04-25T10:00:00+00:00"
    store.add(p2)

    r = c.get("/api/v1/journal/recent", params={"limit": 5})
    body = r.json()
    assert len(body) == 2
    # Most recent first
    assert body[0]["ticker"] == "QQQ"
    assert body[1]["ticker"] == "SPY"


# ─── Discipline-loop closure (Tier 3 Story 35-39) ────────────────────────────


@patch("api.routes.kill_sheet.compute_multi_tf",
       return_value={"1wk": {"error": "skip"}, "4h": {"error": "skip"}})
@patch("api.routes.kill_sheet.scan_ticker", return_value=_FAKE_DAILY_BEAR)
def test_kill_sheet_rejected_when_regime_opposes(mock_scan, mock_multi, client):
    """Long-in-Bear without divergence thesis → REJECTED."""
    c, _ = client
    r = c.post("/api/v1/kill_sheet", json={
        "ticker": "SPY",
        "direction": "long",
        "account": "main",
        "skip_devil": True,
    })
    assert r.status_code == 200
    body = r.json()
    assert body["kill_sheet"]["status"] == "REJECTED"
    assert body["kill_sheet"]["rejection_reason"] is not None


@patch("api.routes.kill_sheet.compute_multi_tf",
       return_value={"1wk": {"error": "skip"}, "4h": {"error": "skip"}})
@patch("api.routes.kill_sheet.scan_ticker", return_value=_FAKE_DAILY_BEAR)
def test_kill_sheet_authorized_with_divergence_thesis(mock_scan, mock_multi, client):
    """Same setup with divergence_thesis → AUTHORIZED."""
    c, _ = client
    r = c.post("/api/v1/kill_sheet", json={
        "ticker": "SPY",
        "direction": "long",
        "account": "main",
        "skip_devil": True,
        "divergence_thesis": "Bottom forming; VIX spike post-Powell signal",
    })
    assert r.status_code == 200
    body = r.json()
    assert body["kill_sheet"]["status"] == "AUTHORIZED"
    assert body["kill_sheet"]["divergence_thesis"] is not None
    att = body["kill_sheet"]["discipline_attestation"]
    assert att["divergence_thesis_documented"] is True


@patch("api.routes.kill_sheet.compute_multi_tf",
       return_value={"1wk": {"error": "skip"}, "4h": {"error": "skip"}})
@patch("api.routes.kill_sheet.scan_ticker", return_value=_FAKE_DAILY)
def test_kill_sheet_attestation_inputs_propagate(mock_scan, mock_multi, client):
    """attestation_user_inputs reach the builder and clear the relevant flag."""
    c, _ = client
    r = c.post("/api/v1/kill_sheet", json={
        "ticker": "SPY",
        "direction": "long",
        "account": "lotto",
        "skip_devil": True,
        "strike": 580, "premium": 2.0, "expiry": "2026-05-05",
        "iv_rank": 85,  # > 70 → triggers iv_rank_over_70 auto-flag
        "attestation_user_inputs": {
            "explicit_post_earnings_crush_thesis": True,
        },
    })
    assert r.status_code == 200
    body = r.json()
    att = body["kill_sheet"]["discipline_attestation"]
    assert att["iv_rank_over_70"] is True
    assert att["explicit_post_earnings_crush_thesis"] is True


def test_close_position_auto_scores(client):
    """Closing a position triggers auto-score and persists DisciplineScore."""
    from datetime import datetime, timezone
    from discipline import DisciplineStore
    from positions.model import Position

    c, _store_factory = client
    store = _store_factory()

    # Open a fresh (non-legacy) position
    today_iso = datetime.now(timezone.utc).isoformat()
    p = Position(
        id="ds_test",
        ticker="SPY", direction="long", instrument="call", account_key="main",
        entry_date=today_iso,
        contracts=1, strike=500.0, expiry="2026-07-01",
        premium_paid_per_contract=10.0,
        total_cost_usd=1000.0, max_loss_usd=200.0,  # 2% of $10k → passes size
        status="open",
    )
    store.add(p)

    r = c.post("/api/v1/positions/ds_test/close", json={"pnl": 250.0})
    assert r.status_code == 200
    assert r.json()["status"] == "closed"

    # DisciplineScore should now exist for this position
    dstore = DisciplineStore()
    assert dstore.has_score("ds_test")
    score = dstore.load_score("ds_test")
    assert score.position_id == "ds_test"
    assert score.pnl_usd == 250.0
    assert len(score.rules) == 15
    # Cleanup so this test is hermetic against real ~/.trading-dashboard/
    dstore.delete_score("ds_test")


def test_discipline_scores_endpoint_lists_recent(client, tmp_path):
    """GET /api/v1/discipline/scores returns scored trades sorted by closed_at desc."""
    from discipline import DisciplineStore
    from discipline.model import DisciplineScore, RuleResult

    c, _ = client

    # Inject two fake scored trades into the default store, cleanup after.
    dstore = DisciplineStore()
    s1 = DisciplineScore.stamp(
        position_id="p_old", kill_sheet_id=None,
        closed_at="2026-05-10T12:00:00+00:00",
        ticker="SPY", direction="long", instrument="call",
        rules=[RuleResult(rule_id="kill_sheet_complete", score="Y", auto_evaluated=True)],
        score_numerator=1, score_denominator=1, pnl_usd=100,
    )
    s2 = DisciplineScore.stamp(
        position_id="p_new", kill_sheet_id=None,
        closed_at="2026-05-15T12:00:00+00:00",
        ticker="QQQ", direction="long", instrument="call",
        rules=[RuleResult(rule_id="kill_sheet_complete", score="N", auto_evaluated=True)],
        score_numerator=0, score_denominator=1, pnl_usd=200,
        profitable_violation=True,
    )
    dstore.save_score(s1)
    dstore.save_score(s2)
    try:
        r = c.get("/api/v1/discipline/scores?limit=10")
        assert r.status_code == 200
        scores = r.json()
        # Should include our two — newest first
        ids = [s["position_id"] for s in scores]
        i_new = ids.index("p_new")
        i_old = ids.index("p_old")
        assert i_new < i_old  # newest first
    finally:
        dstore.delete_score("p_old")
        dstore.delete_score("p_new")


# ─── Sprint B: tier portfolio rules wiring ───────────────────────────────────


_FAKE_QQQ_DAILY = {
    **_FAKE_DAILY,
    "ticker": "QQQ",
    "ma_ribbon": {**_FAKE_DAILY["ma_ribbon"]},
}


@patch("api.routes.kill_sheet.compute_multi_tf",
       return_value={"1wk": {"error": "skip"}, "4h": {"error": "skip"}})
@patch("api.routes.kill_sheet.scan_ticker", return_value=_FAKE_QQQ_DAILY)
def test_tier_portfolio_blocks_second_qqq(mock_scan, mock_multi, client):
    """Orchestrator rule 11 fires whenever ticker is QQQ/GLD, regardless of skill."""
    c, store_factory = client
    store = store_factory()
    # Open one QQQ long
    p = Position.open_options_position(
        ticker="QQQ", direction="long", contract_type="call",
        account_key="main", strike=580, expiry="2026-06-19",
        premium=5.0, contracts=1,
    )
    store.add(p)

    r = c.post("/api/v1/kill_sheet", json={
        "ticker": "QQQ", "direction": "long", "account": "main",
        "skip_devil": True,
    })
    assert r.status_code == 200
    body = r.json()
    rules = {v["rule"] for v in body["rule_violations"]}
    assert "tier_portfolio_one_per_asset" in rules
    assert body["rules_blocked"] is True


@patch("api.routes.kill_sheet.compute_multi_tf",
       return_value={"1wk": {"error": "skip"}, "4h": {"error": "skip"}})
@patch("api.routes.kill_sheet.scan_ticker", return_value=_FAKE_QQQ_DAILY)
def test_tier_portfolio_does_not_fire_for_aapl(mock_scan, mock_multi, client):
    """Rule 11 is QQQ/GLD only — AAPL kill sheet gets no tier_portfolio violation."""
    aapl_row = {**_FAKE_DAILY, "ticker": "AAPL"}
    mock_scan.return_value = aapl_row
    c, _ = client
    r = c.post("/api/v1/kill_sheet", json={
        "ticker": "AAPL", "direction": "long", "account": "main",
        "skip_devil": True,
    })
    assert r.status_code == 200
    rules = {v["rule"] for v in r.json()["rule_violations"]}
    assert not any(rule.startswith("tier_portfolio_") for rule in rules)


@patch("api.routes.kill_sheet.compute_multi_tf",
       return_value={"1wk": {"error": "skip"}, "4h": {"error": "skip"}})
@patch("api.routes.kill_sheet.scan_ticker", return_value=_FAKE_QQQ_DAILY)
def test_tier_portfolio_blocks_same_direction_qqq_gld_pair(mock_scan, mock_multi, client):
    c, store_factory = client
    store = store_factory()
    gld_long = Position.open_options_position(
        ticker="GLD", direction="long", contract_type="call",
        account_key="main", strike=250, expiry="2026-06-19",
        premium=2.0, contracts=1,
    )
    store.add(gld_long)

    # Try to open a QQQ long while GLD long is already open
    r = c.post("/api/v1/kill_sheet", json={
        "ticker": "QQQ", "direction": "long", "account": "main",
        "skip_devil": True,
    })
    assert r.status_code == 200
    rules = {v["rule"] for v in r.json()["rule_violations"]}
    assert "tier_portfolio_no_same_direction_pair" in rules
