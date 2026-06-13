from pathlib import Path

import pytest

from config import AccountConfig, load_config


def test_load_config_returns_defaults_when_no_file(tmp_path: Path):
    cfg = load_config(tmp_path / "missing.yaml")
    assert "main" in cfg.accounts
    assert "lotto" in cfg.accounts
    assert "weekly" in cfg.accounts
    assert cfg.accounts["main"].balance_usd == 10_000.0
    assert cfg.accounts["lotto"].balance_usd == 1_000.0


def test_pool_account_keys_groups_pooled_accounts(tmp_path: Path):
    # 'weekly' shares 'main's capital pool (pool_member_of: main); 'lotto' is
    # standalone. Premium-at-risk gates must aggregate across a pool.
    cfg = load_config(tmp_path / "missing.yaml")
    assert cfg.pool_account_keys("main") == {"main", "weekly"}
    assert cfg.pool_account_keys("weekly") == {"main", "weekly"}
    assert cfg.pool_account_keys("lotto") == {"lotto"}


def test_lotto_cut_rule_is_minus_50pct(tmp_path: Path):
    # Decision 2026-06: lotto stop is -50% (skill + backtest R), not -70%.
    cfg = load_config(tmp_path / "missing.yaml")
    assert cfg.account("lotto").raw["cut_rule_pct"] == -0.50


def test_load_config_returns_defaults_for_empty_file(tmp_path: Path):
    p = tmp_path / "empty.yaml"
    p.write_text("")
    cfg = load_config(p)
    assert cfg.accounts["main"].balance_usd == 10_000.0


def test_user_can_override_balance(tmp_path: Path):
    p = tmp_path / "config.yaml"
    p.write_text("""
accounts:
  main:
    balance_usd: 12500
""")
    cfg = load_config(p)
    assert cfg.accounts["main"].balance_usd == 12_500.0
    # Other defaults remain
    assert cfg.accounts["main"].name == "Main Account"


def test_user_can_add_new_account(tmp_path: Path):
    p = tmp_path / "config.yaml"
    p.write_text("""
accounts:
  experiment:
    name: Experiment Pot
    type: cash
    balance_usd: 500
    risk_per_trade:
      high: 0.05
""")
    cfg = load_config(p)
    assert cfg.account("experiment").balance_usd == 500.0
    assert cfg.account("experiment").risk_pct("high") == 0.05


def test_account_risk_pct_falls_back_when_conviction_missing():
    cfg = load_config(Path("/nonexistent.yaml"))
    main = cfg.account("main")
    # 'high' is defined; should return defined value
    assert main.risk_pct("high") == 0.025
    # 'unknown' falls back to 'high' default
    assert main.risk_pct("unknown") == 0.025


def test_account_max_loss_for():
    cfg = load_config(Path("/nonexistent.yaml"))
    assert cfg.account("main").max_loss_for("high") == 250.0
    assert cfg.account("main").max_loss_for("speculative") == 75.0


def test_unknown_account_raises():
    cfg = load_config(Path("/nonexistent.yaml"))
    with pytest.raises(KeyError, match="Unknown account"):
        cfg.account("does_not_exist")


def test_invalid_top_level_yaml_raises(tmp_path: Path):
    p = tmp_path / "config.yaml"
    p.write_text("just a string\n")
    with pytest.raises(ValueError, match="must be a mapping"):
        load_config(p)


def test_account_config_dataclass():
    ac = AccountConfig(
        name="Test",
        type="cash",
        balance_usd=5000.0,
        raw={"risk_per_trade": {"high": 0.03, "medium": 0.02}},
    )
    assert ac.risk_pct("high") == 0.03
    assert ac.max_loss_for("medium") == 100.0


# ── Sprint A: skills config ──────────────────────────────────────────────────


def test_load_config_includes_default_skills(tmp_path: Path):
    cfg = load_config(tmp_path / "nope.yaml")
    expected = {
        "weekly-trend-trader",
        "lotto-options",
        "index-swing",
        "qqq-gld-focus",
        "trading-edge",
    }
    assert set(cfg.skills) == expected


def test_skill_tier_assignment():
    cfg = load_config(Path("/nonexistent.yaml"))
    assert cfg.skill("weekly-trend-trader").tier == 1
    assert cfg.skill("lotto-options").tier == 2
    assert cfg.skill("index-swing").tier == 4
    assert cfg.skill("qqq-gld-focus").tier == 4
    assert cfg.skill("trading-edge").tier == 4


def test_default_watchlist_qqq_gld_for_tier_1_and_2():
    cfg = load_config(Path("/nonexistent.yaml"))
    assert cfg.skill("weekly-trend-trader").default_watchlist == ["QQQ", "GLD"]
    assert cfg.skill("lotto-options").default_watchlist == ["QQQ", "GLD"]


def test_unknown_skill_raises():
    cfg = load_config(Path("/nonexistent.yaml"))
    import pytest as _pytest
    with _pytest.raises(KeyError):
        cfg.skill("never-heard-of-it")


def test_skills_at_tier_filter():
    cfg = load_config(Path("/nonexistent.yaml"))
    tier_4 = {s.name for s in cfg.skills_at_tier(4)}
    assert tier_4 == {"index-swing", "qqq-gld-focus", "trading-edge"}
    assert {s.name for s in cfg.skills_at_tier(1)} == {"weekly-trend-trader"}


def test_user_can_override_skill_tier(tmp_path: Path):
    p = tmp_path / "config.yaml"
    p.write_text(
        "skills:\n"
        "  weekly-trend-trader:\n"
        "    tier: 2\n"
        "    default_watchlist: [\"QQQ\"]\n"
    )
    cfg = load_config(p)
    s = cfg.skill("weekly-trend-trader")
    assert s.tier == 2
    assert s.default_watchlist == ["QQQ"]
