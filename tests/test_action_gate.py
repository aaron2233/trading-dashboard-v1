"""Action gate verdict classifiers — all 5 states across 3 skills."""
from __future__ import annotations

from action_gate import (
    ActionVerdict,
    classify_focus_action,
    classify_lotto_action,
    classify_weekly_trend_action,
)


# ── Read fixtures ────────────────────────────────────────────────────────────


def _read(
    *,
    timeframe: str = "1d",
    close: float = 100.0,
    stack: str = "full_bull",
    stoch_k: float = 50.0,
    stoch_d: float = 50.0,
    stoch_zone: str = "mid",
    stoch_signal: str = "neutral",
    sqn_regime: str = "bull",
    sqn_value: float = 1.0,
    sqn_20_value: float = 0.8,
    sqn_20_regime: str = "bull",
    diag: str = "healthy_trend",
) -> dict:
    return {
        "ticker": "TEST", "timeframe": timeframe, "bar_date": "2026-05-05",
        "close": close,
        "ma_ribbon": {
            "ma_10": close, "ma_20": close, "ma_50": close, "ma_200": close,
            "stack_state": stack,
        },
        "stochastic": {
            "k": stoch_k, "d": stoch_d,
            "zone": stoch_zone, "signal": stoch_signal,
        },
        "sqn": {
            "sqn_value": sqn_value, "regime": sqn_regime,
            "sqn_20_value": sqn_20_value, "regime_20": sqn_20_regime,
            "diagnostic": diag,
        },
    }


# ═════════════════════════════════════════════════════════════════════════════
# Lotto classifier (Tier 2: daily filter / 2H trigger)
# ═════════════════════════════════════════════════════════════════════════════


def test_lotto_enter_now_long():
    """Daily bull + 2H bull stack + 2H bull_cross_oversold = ENTER NOW."""
    reads = {
        "1d":  _read(timeframe="1d",  stack="full_bull"),
        "2h":  _read(timeframe="2h", close=100.0, stack="full_bull",
                     stoch_k=28, stoch_d=22, stoch_zone="oversold",
                     stoch_signal="bull_cross_oversold"),
    }
    v = classify_lotto_action(reads, "long")
    assert v.state == "enter_now"
    assert v.direction == "long"
    assert v.suggested_entry_price == 100.0
    assert "BUY CALLS" in v.headline
    assert "$100.00" in v.headline


def test_lotto_enter_now_short():
    reads = {
        "1d":  _read(timeframe="1d",  stack="full_bear", sqn_regime="bear", sqn_20_regime="bear"),
        "2h":  _read(timeframe="2h", close=50.0, stack="full_bear",
                     stoch_k=72, stoch_d=78, stoch_zone="overbought",
                     stoch_signal="bear_cross_overbought",
                     sqn_regime="bear", sqn_20_regime="bear"),
    }
    v = classify_lotto_action(reads, "short")
    assert v.state == "enter_now"
    assert "BUY PUTS" in v.headline
    assert "$50.00" in v.headline


def test_lotto_setup_forming_when_trigger_not_fired():
    """Daily aligned, 2H aligned, but stoch hasn't crossed yet."""
    reads = {
        "1d":  _read(timeframe="1d",  stack="full_bull"),
        "2h":  _read(timeframe="2h", stack="full_bull",
                     stoch_k=45, stoch_d=42, stoch_zone="mid",
                     stoch_signal="neutral"),
    }
    v = classify_lotto_action(reads, "long")
    assert v.state == "setup_forming"
    assert "WAIT" in v.headline
    assert any("bull_cross_oversold" in c for c in v.advance_conditions)


def test_lotto_disqualified_when_2h_chop():
    """2H stack chop = no trigger TF trend = hard skip."""
    reads = {
        "1d":  _read(timeframe="1d", stack="full_bull"),
        "2h":  _read(timeframe="2h", stack="chop"),
    }
    v = classify_lotto_action(reads, "long")
    assert v.state == "disqualified"
    assert "2H" in v.headline
    assert any("chop" in b.lower() for b in v.blockers)


def test_lotto_disqualified_when_daily_chop():
    reads = {
        "1d":  _read(timeframe="1d", stack="chop"),
        "2h":  _read(timeframe="2h", stack="full_bull"),
    }
    v = classify_lotto_action(reads, "long")
    assert v.state == "disqualified"
    assert "daily" in v.headline.lower()


def test_lotto_disqualified_when_daily_opposes_long():
    reads = {
        "1d":  _read(timeframe="1d", stack="full_bear"),
        "2h":  _read(timeframe="2h", stack="full_bull"),
    }
    v = classify_lotto_action(reads, "long")
    assert v.state == "disqualified"
    assert "opposes" in v.headline


def test_lotto_chase_zone_long_via_sqn20():
    """SPY-style: daily SQN20 strong_bull + daily stoch overbought = chase."""
    reads = {
        "1d":  _read(timeframe="1d", stack="full_bull",
                     stoch_zone="overbought", sqn_20_regime="strong_bull",
                     diag="confluence_chase_warning"),
        "2h":  _read(timeframe="2h", stack="full_bull"),
    }
    v = classify_lotto_action(reads, "long")
    assert v.state == "chase_zone"
    assert "chase" in v.headline.lower()


def test_lotto_chase_zone_via_diagnostic_keyword():
    """Daily diag literally contains 'chase' -> chase_zone."""
    reads = {
        "1d":  _read(timeframe="1d", stack="full_bull",
                     stoch_zone="mid", sqn_20_regime="bull",
                     diag="confluence_chase_warning"),
        "2h":  _read(timeframe="2h", stack="full_bull"),
    }
    v = classify_lotto_action(reads, "long")
    assert v.state == "chase_zone"


def test_lotto_stale_long_when_bull_move_exhausted():
    """Long-side stale = bull move exhausted: daily stoch overbought,
    weekly stoch overbought, 2H diag weakening. Routes to STALE
    (not CHASE) because the explicit weakening diag wins precedence."""
    reads = {
        "1wk": _read(timeframe="1wk", stoch_zone="overbought"),
        "1d":  _read(timeframe="1d", stack="full_bull",
                     stoch_zone="overbought"),
        "2h":  _read(timeframe="2h", stack="full_bull",
                     diag="bull_weakening"),
    }
    v = classify_lotto_action(reads, "long")
    assert v.state == "stale"
    assert "exhausted" in v.headline


def test_lotto_disqualified_when_missing_2h_read():
    reads = {"1d": _read(timeframe="1d", stack="full_bull")}
    v = classify_lotto_action(reads, "long")
    assert v.state == "disqualified"
    assert any("2h" in b.lower() for b in v.blockers)


def test_lotto_disqualified_when_2h_has_error():
    reads = {
        "1d":  _read(timeframe="1d", stack="full_bull"),
        "2h":  {"timeframe": "2h", "error": "yfinance dead"},
    }
    v = classify_lotto_action(reads, "long")
    assert v.state == "disqualified"
    assert any("yfinance dead" in b for b in v.blockers)


# ═════════════════════════════════════════════════════════════════════════════
# Weekly trend classifier (Tier 1: weekly anchor / weekly trigger)
# ═════════════════════════════════════════════════════════════════════════════


def test_weekly_enter_now_long():
    """Weekly full_bull + bull_cross_oversold = ENTER NOW."""
    reads = {
        "1wk": _read(timeframe="1wk", close=580.0, stack="full_bull",
                     stoch_k=28, stoch_d=22, stoch_zone="oversold",
                     stoch_signal="bull_cross_oversold"),
    }
    v = classify_weekly_trend_action(reads, "long")
    assert v.state == "enter_now"
    assert "$580.00" in v.headline
    assert "LEAPS" in v.headline


def test_weekly_setup_forming_when_in_trend_no_trigger():
    """Weekly bull stack but stoch hasn't crossed at oversold yet."""
    reads = {
        "1wk": _read(timeframe="1wk", stack="full_bull",
                     stoch_zone="overbought", stoch_signal="neutral"),
    }
    v = classify_weekly_trend_action(reads, "long")
    assert v.state == "setup_forming"


def test_weekly_disqualified_when_stack_chop():
    reads = {"1wk": _read(timeframe="1wk", stack="compression")}
    v = classify_weekly_trend_action(reads, "long")
    assert v.state == "disqualified"


def test_weekly_disqualified_when_stack_opposes():
    """Long verdict but weekly is full_bear = counter-weekly hard skip."""
    reads = {"1wk": _read(timeframe="1wk", stack="full_bear")}
    v = classify_weekly_trend_action(reads, "long")
    assert v.state == "disqualified"
    assert "counter-weekly" in " ".join(v.blockers)


def test_weekly_chase_zone_via_bearish_divergence_at_overbought():
    """Long setup but weekly stoch shows bearish divergence at overbought
    = top forming."""
    reads = {
        "1wk": _read(timeframe="1wk", stack="full_bull",
                     stoch_zone="overbought",
                     stoch_signal="bearish_divergence"),
    }
    v = classify_weekly_trend_action(reads, "long")
    assert v.state == "chase_zone"


def test_weekly_stale_when_exhaustion_plus_diag():
    reads = {
        "1wk": _read(timeframe="1wk", stack="full_bull",
                     stoch_zone="overbought", diag="bull_weakening"),
    }
    v = classify_weekly_trend_action(reads, "long")
    assert v.state == "stale"


def test_weekly_disqualified_when_missing_weekly_read():
    v = classify_weekly_trend_action({}, "long")
    assert v.state == "disqualified"


def test_weekly_tolerates_sustained_overbought_without_divergence():
    """Sustained overbought weekly stoch alone (no divergence, no
    weakening diag) is NOT chase — weekly trends grind. Setup just
    waits for next pullback trigger."""
    reads = {
        "1wk": _read(timeframe="1wk", stack="full_bull",
                     stoch_zone="overbought", stoch_signal="neutral",
                     diag="healthy_trend"),
    }
    v = classify_weekly_trend_action(reads, "long")
    assert v.state == "setup_forming"


# ═════════════════════════════════════════════════════════════════════════════
# Focus classifier (Tier 4 specialty: same as lotto, longer DTE)
# ═════════════════════════════════════════════════════════════════════════════


def test_focus_reuses_lotto_rules_with_different_skill_label():
    """Focus shares lotto's chop/chase/trigger logic; only skill label
    + DTE band differ."""
    reads = {
        "1d":  _read(timeframe="1d",  stack="full_bull"),
        "2h":  _read(timeframe="2h", close=100.0, stack="full_bull",
                     stoch_k=28, stoch_d=22, stoch_zone="oversold",
                     stoch_signal="bull_cross_oversold"),
    }
    v = classify_focus_action(reads, "long")
    assert v.state == "enter_now"
    assert v.skill == "qqq-gld-focus"
    # Adds the focus-specific DTE band citation
    assert any("21-60 DTE" in c for c in v.rule_citations)


def test_focus_chase_zone_when_lotto_would_be_chase():
    reads = {
        "1d":  _read(timeframe="1d", stack="full_bull",
                     stoch_zone="overbought", sqn_20_regime="strong_bull"),
        "2h":  _read(timeframe="2h", stack="full_bull"),
    }
    v = classify_focus_action(reads, "long")
    assert v.state == "chase_zone"
    assert v.skill == "qqq-gld-focus"


# ═════════════════════════════════════════════════════════════════════════════
# Verdict serialization
# ═════════════════════════════════════════════════════════════════════════════


def test_verdict_to_dict_round_trippable():
    reads = {
        "1d":  _read(timeframe="1d", stack="full_bull"),
        "2h":  _read(timeframe="2h", stack="full_bull",
                     stoch_signal="bull_cross_oversold", stoch_zone="oversold"),
    }
    v = classify_lotto_action(reads, "long")
    d = v.to_dict()
    assert d["state"] == "enter_now"
    assert d["skill"] == "lotto-options"
    assert d["direction"] == "long"
    assert "rule_citations" in d
    assert isinstance(d["rule_citations"], list)


# ═════════════════════════════════════════════════════════════════════════════
# State sort order
# ═════════════════════════════════════════════════════════════════════════════


def test_state_sort_key_orders_actionable_first():
    from action_gate.model import state_sort_key
    states = ["disqualified", "setup_forming", "enter_now", "chase_zone", "stale"]
    states.sort(key=state_sort_key)
    assert states == [
        "enter_now", "setup_forming", "chase_zone", "stale", "disqualified",
    ]
