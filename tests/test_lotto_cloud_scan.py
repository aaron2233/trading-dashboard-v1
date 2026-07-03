"""Tests for the standalone cloud lotto-scan renderer + the lotto cut default.

Locks the -50% lotto cut. Code-review finding: the cloud-scan email instructed a
-70% premium cut while the lotto skill spec and the calibrating backtest both use
-50% (HARD_STOP_FRAC=0.50). The email value is config-driven via the lotto
account's cut_rule_pct, so this pins both the config default and the renderer.
"""
from __future__ import annotations

import sys
from pathlib import Path

# scripts/ is not a package — put it on the path and import the module. The
# module inserts src/ onto sys.path itself at import time.
_SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

import lotto_cloud_scan as cloud  # noqa: E402
import notify_telegram  # noqa: E402

from config import load_config  # noqa: E402


def _trade(cut_pct: float | None) -> dict:
    return {
        "ticker": "TST1",
        "kind": "call",
        "spot": 25.0,
        "suggested_strike": None,
        "strike_ladder": [],
        "stock_target": 28.0,
        "stock_stop": 24.0,
        "options_target_pct": cloud.LOTTO_TARGET_PCT,
        "options_cut_pct": cut_pct,
        "why_now": "test setup",
    }


def test_lotto_config_default_cut_is_minus_50():
    """The load-bearing change: the lotto account default cut is -0.50, read by
    the cloud-scan email via account.raw['cut_rule_pct']."""
    assert load_config().account("lotto").raw.get("cut_rule_pct") == -0.50


def test_trade_to_markdown_renders_50pct_cut():
    md = cloud.trade_to_markdown(_trade(-0.50))
    assert "50% of entry premium" in md
    assert "≈0.50× entry premium" in md
    assert "hard cut" in md
    assert "70%" not in md


def test_trade_to_markdown_none_fallback_is_50pct():
    """Degenerate path (cut_pct missing) must not resurface the old 70% / 0.30×."""
    md = cloud.trade_to_markdown(_trade(None))
    assert "50% of entry premium" in md
    assert "≈0.50× entry" in md
    assert "70%" not in md
    assert "0.30" not in md


def test_telegram_trade_block_cut_is_50pct():
    """The Telegram sibling renderer reads the same options_cut_pct — it must
    show -50% and never the old -70% fallback."""
    assert "-50% hard cut" in notify_telegram._trade_block(_trade(-0.50))
    block_none = notify_telegram._trade_block(_trade(None))
    assert "-50% hard cut" in block_none
    assert "70%" not in block_none
