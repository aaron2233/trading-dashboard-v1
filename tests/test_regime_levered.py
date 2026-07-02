"""Tests for the regime-levered-trend Layer 1 scanner."""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from regime_levered import (
    MAX_CORE_POSITIONS,
    WeeklyState,
    classify_layer1,
    compute_weekly_state,
    scan_regime_levered,
)


def _state(
    *,
    full_bull: bool = True,
    stoch_k: float = 45.0,
    stoch_k_prev: float = 40.0,
    close_above_20: bool = True,
) -> WeeklyState:
    return WeeklyState(
        bar_date="2026-06-26",
        close=100.0,
        ma_10=98.0, ma_20=95.0, ma_50=90.0, ma_200=70.0,
        ma_19=94.0,
        full_bull=full_bull,
        stoch_k=stoch_k, stoch_d=50.0, stoch_k_prev=stoch_k_prev,
        stoch_turned_up=stoch_k > stoch_k_prev,
        close_above_20=close_above_20,
    )


class TestClassifyLayer1:
    def test_core_entry_all_filters_pass(self):
        conf, verdict, _, blockers = classify_layer1(_state(), 1.2, True)
        assert conf == "core_entry"
        assert verdict == "buy"
        assert blockers == []

    def test_overbought_is_watchlist_never_entry(self):
        conf, verdict, reason, _ = classify_layer1(
            _state(stoch_k=85.0, stoch_k_prev=75.0), 1.2, True,
        )
        assert conf == "overbought_watch"
        assert verdict == "wait"
        assert "chase" in reason

    def test_no_stoch_turn_waits(self):
        conf, verdict, _, _ = classify_layer1(
            _state(stoch_k=40.0, stoch_k_prev=45.0), 1.2, True,
        )
        assert conf == "bull_no_trigger"
        assert verdict == "wait"

    def test_reset_above_70_is_not_a_trigger(self):
        # K turned up but from 72 — not a reset per STOCH_RESET_MAX
        conf, verdict, _, _ = classify_layer1(
            _state(stoch_k=75.0, stoch_k_prev=72.0), 1.2, True,
        )
        assert conf == "bull_no_trigger"
        assert verdict == "wait"

    def test_own_sqn_below_gate_blocks(self):
        conf, verdict, _, _ = classify_layer1(_state(), 0.4, True)
        assert conf == "own_regime_blocked"
        assert verdict == "no_go"

    def test_not_full_bull_blocks(self):
        conf, verdict, _, _ = classify_layer1(_state(full_bull=False), 1.2, True)
        assert conf == "not_full_bull"
        assert verdict == "no_go"

    def test_broad_gate_closed_downgrades_trigger_to_wait(self):
        conf, verdict, _, blockers = classify_layer1(_state(), 1.2, False)
        assert conf == "core_entry"
        assert verdict == "wait"
        assert any("Layer 1 closed" in b for b in blockers)

    def test_no_data(self):
        conf, verdict, _, _ = classify_layer1(None, 1.2, True)
        assert conf == "no_data"
        assert verdict == "no_go"


class TestComputeWeeklyState:
    def _bars(self, closes: np.ndarray) -> pd.DataFrame:
        idx = pd.date_range("2018-01-05", periods=len(closes), freq="W-FRI")
        return pd.DataFrame(
            {"close": closes, "high": closes * 1.01, "low": closes * 0.99},
            index=idx,
        )

    def test_monotonic_ramp_is_full_bull(self):
        closes = np.linspace(50, 200, 260)
        state = compute_weekly_state(self._bars(closes))
        assert state is not None
        assert state.full_bull
        assert state.close_above_20
        assert state.ma_10 > state.ma_20 > state.ma_50 > state.ma_200
        assert state.ma_19 == pytest.approx(float(pd.Series(closes).tail(19).mean()))

    def test_downtrend_is_not_full_bull(self):
        closes = np.linspace(200, 50, 260)
        state = compute_weekly_state(self._bars(closes))
        assert state is not None
        assert not state.full_bull

    def test_too_short_returns_none(self):
        closes = np.linspace(50, 100, 100)
        assert compute_weekly_state(self._bars(closes)) is None

    def test_none_bars_return_none(self):
        assert compute_weekly_state(None) is None


class TestScanRegimeLevered:
    """Integration via injected scan_fn / bars_fn — no network."""

    @staticmethod
    def _bull_bars(seed: int = 0) -> pd.DataFrame:
        # Ramp with a recent shallow dip-and-turn so the Stoch trigger fires
        # while the ribbon stays a rising Full Bull stack.
        rng = np.random.default_rng(seed)
        base = np.linspace(50, 200, 252)
        tail = np.array([198.0, 196.0, 195.0, 194.0, 197.0, 201.0, 204.0])
        base = np.concatenate([base, tail])
        base = base + rng.normal(0, 0.2, len(base))
        idx = pd.date_range("2018-01-05", periods=len(base), freq="W-FRI")
        return pd.DataFrame(
            {"close": base, "high": base * 1.01, "low": base * 0.99}, index=idx,
        )

    @staticmethod
    def _scan_fn(sqn_by_ticker: dict[str, float], benchmark_sqn: float = 1.0,
                 daily_k: float = 50.0):
        def scan_fn(ticker: str, timeframe: str):
            assert timeframe == "1d"
            val = sqn_by_ticker.get(ticker, benchmark_sqn)
            regime = "bull" if val >= 0.7 else "neutral"
            return {
                "sqn": {"sqn_value": val, "regime": regime},
                "stochastic": {"k": daily_k, "d": daily_k},
                "hv20": None,
            }
        return scan_fn

    def test_buy_capped_at_max_core_positions(self):
        tickers = ["AAA", "BBB", "CCC"]
        sqns = {"AAA": 2.0, "BBB": 1.5, "CCC": 1.0, "SPY": 1.2, "QQQ": 1.2}
        result = scan_regime_levered(
            tickers,
            scan_fn=self._scan_fn(sqns),
            bars_fn=lambda t: self._bull_bars(),
        )
        buys = [s for s in result.setups if s.verdict == "buy"]
        assert len(buys) == MAX_CORE_POSITIONS
        # Ranked by own SQN — CCC (lowest) is the one downgraded
        assert {s.ticker for s in buys} == {"AAA", "BBB"}
        ccc = next(s for s in result.setups if s.ticker == "CCC")
        assert ccc.verdict == "wait"
        assert ccc.confluence == "core_entry"
        assert result.core_candidates == buys

    def test_broad_gate_closed_blocks_all_buys(self):
        sqns = {"AAA": 2.0, "SPY": 0.1, "QQQ": 0.1}
        result = scan_regime_levered(
            ["AAA"],
            scan_fn=self._scan_fn(sqns, benchmark_sqn=0.1),
            bars_fn=lambda t: self._bull_bars(),
        )
        assert not result.layer1_live
        assert all(s.verdict != "buy" for s in result.setups)
        assert result.core_candidates == []

    def test_dip_buy_fires_only_in_bull(self):
        sqns = {"SPY": 1.2, "QQQ": 1.2}
        result = scan_regime_levered(
            ["SPY"],
            scan_fn=self._scan_fn(sqns, daily_k=12.0),
            bars_fn=lambda t: self._bull_bars(),
        )
        assert all(d.fired for d in result.dip_buy_signals)

        sqns_bear = {"SPY": -1.0, "QQQ": -1.0}
        result = scan_regime_levered(
            ["SPY"],
            scan_fn=self._scan_fn(sqns_bear, benchmark_sqn=-1.0, daily_k=12.0),
            bars_fn=lambda t: self._bull_bars(),
        )
        assert all(not d.fired for d in result.dip_buy_signals)

    def test_stop_price_is_19wma(self):
        sqns = {"AAA": 2.0, "SPY": 1.2, "QQQ": 1.2}
        result = scan_regime_levered(
            ["AAA"],
            scan_fn=self._scan_fn(sqns),
            bars_fn=lambda t: self._bull_bars(),
        )
        s = result.setups[0]
        assert s.verdict == "buy"
        assert s.stop_price == pytest.approx(s.weekly.ma_19)
        assert s.suggested_dte == "365-540 DTE LEAPS"

    def test_deployment_note_always_present(self):
        sqns = {"SPY": 1.2, "QQQ": 1.2}
        result = scan_regime_levered(
            ["SPY"],
            scan_fn=self._scan_fn(sqns),
            bars_fn=lambda t: self._bull_bars(),
        )
        assert "R1/R2" in result.deployment_note


class TestKillSheetDeploymentGate:
    """R1/R2 gate: skill='regime-levered-trend' is hard-blocked on main/lotto."""

    @staticmethod
    def _scan_row() -> dict:
        return {
            "ticker": "MU",
            "timeframe": "1d",
            "bar_date": "2026-06-26",
            "close": 1132.0,
            "ma_ribbon": {
                "ma_10": 1100.0, "ma_20": 1000.0, "ma_50": 800.0,
                "ma_200": 400.0, "stack_state": "full_bull",
            },
            "stochastic": {"k": 55.0, "d": 50.0, "zone": "mid", "signal": None},
            "sqn": {"sqn_value": 1.7, "regime": "strong_bull",
                    "sqn_20_value": 0.5, "regime_20": "bull"},
        }

    @staticmethod
    def _options():
        from kill_sheet.options import OptionsStructure
        return OptionsStructure(
            strike=900.0, contract_type="call", expiry="2027-09-17",
            dte=440, premium=280.0, delta=0.82, iv_rank=35.0,
        )

    def _build(self, account_key: str, skill: str | None):
        from kill_sheet.builder import build_standard
        from config import load_config
        cfg = load_config()
        return build_standard(
            self._scan_row(), "long", cfg.account("main"),
            account_key=account_key, intent="POSITION", trigger_tf="Weekly",
            options=self._options(), skill=skill,
        )

    def test_blocked_on_main(self):
        sheet = self._build("main", "regime-levered-trend")
        att = sheet.discipline_attestation
        assert att.regime_levered_deployment_blocked is True
        assert att.entry_authorized is False

    def test_blocked_on_lotto(self):
        sheet = self._build("lotto", "regime-levered-trend")
        assert sheet.discipline_attestation.regime_levered_deployment_blocked is True

    def test_allowed_on_dedicated_sleeve(self):
        sheet = self._build("leaps_sleeve", "regime-levered-trend")
        assert sheet.discipline_attestation.regime_levered_deployment_blocked is False

    def test_other_skills_on_main_unaffected(self):
        sheet = self._build("main", "weekly-trend-trader")
        assert sheet.discipline_attestation.regime_levered_deployment_blocked is False
