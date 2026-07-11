"""Account + dashboard configuration loader.

Defaults are sourced from ~/CLAUDE.md (the orchestrator file) so a fresh install
works without any config.yaml. Users override by creating
~/.trading-dashboard/config.yaml — only the fields they want to change.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml


DEFAULT_CONFIG_PATH = Path.home() / ".trading-dashboard" / "config.yaml"


# Defaults from ~/CLAUDE.md (current session): cash account, long calls/puts only,
# $10K main / $1K lotto, 2-3% high-conviction risk, -60% to -70% cut rule,
# $15-50 single-stock range.
_DEFAULT_ACCOUNTS: dict[str, dict[str, Any]] = {
    "main": {
        "name": "Main Account",
        "type": "cash",
        "balance_usd": 10_000.0,
        "instruments": ["long_calls", "long_puts"],
        "risk_per_trade": {
            "high": 0.025,
            "medium": 0.015,
            "speculative": 0.0075,
        },
        "max_open_positions": 5,
        "max_premium_at_risk_pct": 0.10,
        "cut_rule_pct": -0.60,
        "single_stock_price_min": 15.0,
        "single_stock_price_max": 50.0,
        "tempo_per_week": "1-2",
    },
    "lotto": {
        "name": "Lotto Account",
        "type": "cash",
        "balance_usd": 1_000.0,
        "instruments": ["long_calls", "long_puts"],
        "risk_per_trade": {
            "default": 0.075,
        },
        "max_per_trade_usd": 150.0,
        "max_open_positions": 3,
        "max_premium_at_risk_pct": 0.30,
        "cash_floor_usd": 200.0,
        "contract_price_min": 0.20,
        "contract_price_max": 1.50,
        "dte_min": 5,
        "dte_max": 14,
        # Lotto stop is -50% per the lotto-options skill and the v2-gate
        # backtest's R definition (HARD_STOP_FRAC=0.50); -0.70 broke those PF
        # numbers. (Decision 2026-06.) The cloud-scan email/Telegram renderers
        # and the discipline scorecard's lotto cut check all read this value.
        "cut_rule_pct": -0.50,
        "tempo_per_week": "2-4",
    },
    "weekly": {
        "name": "Weekly/Position Account",
        "type": "cash",
        "balance_usd": 10_000.0,  # shares the main account pool
        # When set, this account's balance is NOT counted as standalone equity
        # for stage detection or total-balance aggregation — it shares its
        # pool with the named account. Per ~/CLAUDE.md account profile.
        "pool_member_of": "main",
        "instruments": ["long_calls", "long_puts", "underlying_shares"],
        "risk_per_trade": {
            "high": 0.025,
            "medium": 0.015,
        },
        "max_open_positions": 5,
        "max_premium_at_risk_pct": 0.10,
        "dte_min_for_options": 60,
        "dte_target_for_options": 180,
        "cut_rule_pct": -0.60,
    },
}


# Skill → tier + default watchlist. Sourced from ~/CLAUDE.md "Skill Routing".
# Tiers:
#   1 — anchor (weekly-trend-trader)
#   2 — secondary (lotto-options)
#   4 — explicit-trigger only (index-swing, trading-edge)
# Default watchlist drives Tier 1/Tier 2 baseline scans (empty list = asset-agnostic).
# Gates (trade-devil, discipline) intentionally absent — they're orthogonal to
# tiered routing.
_DEFAULT_SKILLS: dict[str, dict[str, Any]] = {
    "weekly-trend-trader": {
        "tier": 1,
        "default_watchlist": ["QQQ", "GLD"],
    },
    "lotto-options": {
        "tier": 2,
        "default_watchlist": ["QQQ", "GLD"],
    },
    "index-swing": {
        "tier": 4,
        "default_watchlist": ["QQQ", "IWM", "SPY"],
    },
    "trading-edge": {
        "tier": 4,
        "default_watchlist": [],
    },
}


@dataclass
class AccountConfig:
    name: str
    type: str
    balance_usd: float
    raw: dict[str, Any] = field(default_factory=dict)
    # When set, this account shares its pool with the named account and
    # should not contribute its own balance to total-equity aggregations.
    pool_member_of: str | None = None

    def risk_pct(self, conviction: str = "high") -> float:
        risk = self.raw.get("risk_per_trade", {})
        if conviction in risk:
            return float(risk[conviction])
        # Fall through to a sensible default
        if "default" in risk:
            return float(risk["default"])
        if "high" in risk:
            return float(risk["high"])
        return 0.01  # 1% absolute fallback

    def max_loss_for(self, conviction: str = "high") -> float:
        return self.balance_usd * self.risk_pct(conviction)


@dataclass
class SkillConfig:
    """Routing config for one skill — tier + default watchlist.

    Per ~/CLAUDE.md tiered hierarchy. Skills not in the config dict (e.g. gates
    like trade-devil and discipline) raise KeyError on lookup.
    """
    name: str
    tier: int
    default_watchlist: list[str] = field(default_factory=list)


@dataclass
class Config:
    accounts: dict[str, AccountConfig]
    skills: dict[str, SkillConfig] = field(default_factory=dict)
    raw: dict[str, Any] = field(default_factory=dict)

    def account(self, name: str) -> AccountConfig:
        if name not in self.accounts:
            available = ", ".join(sorted(self.accounts))
            raise KeyError(f"Unknown account {name!r}. Available: {available}")
        return self.accounts[name]

    def skill(self, name: str) -> SkillConfig:
        if name not in self.skills:
            available = ", ".join(sorted(self.skills))
            raise KeyError(f"Unknown skill {name!r}. Available: {available}")
        return self.skills[name]

    def skills_at_tier(self, tier: int) -> list[SkillConfig]:
        return [s for s in self.skills.values() if s.tier == tier]

    def pool_account_keys(self, account_key: str) -> set[str]:
        """All account keys sharing a capital pool with ``account_key``.

        Accounts with ``pool_member_of: X`` draw on X's balance (e.g. the
        'weekly' account shares 'main's $10K pool), so premium-at-risk and
        position-count gates must aggregate across the whole pool — otherwise
        each key can independently consume the full budget. Returns just
        ``{account_key}`` for a standalone account.
        """
        acct = self.accounts.get(account_key)
        root = (acct.pool_member_of if acct and acct.pool_member_of else account_key)
        keys = {root}
        for k, a in self.accounts.items():
            if k == root or a.pool_member_of == root:
                keys.add(k)
        keys.add(account_key)
        return keys


def _deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    out = dict(base)
    for key, val in override.items():
        if key in out and isinstance(out[key], dict) and isinstance(val, dict):
            out[key] = _deep_merge(out[key], val)
        else:
            out[key] = val
    return out


def load_config(path: Path = DEFAULT_CONFIG_PATH) -> Config:
    """Load ~/.trading-dashboard/config.yaml on top of baked-in defaults.

    Missing file => returns defaults. Empty file => returns defaults. Partial
    config => merged onto defaults at any nesting depth.
    """
    accounts_raw: dict[str, dict[str, Any]] = {k: dict(v) for k, v in _DEFAULT_ACCOUNTS.items()}
    skills_raw: dict[str, dict[str, Any]] = {k: dict(v) for k, v in _DEFAULT_SKILLS.items()}
    full_raw: dict[str, Any] = {"accounts": accounts_raw, "skills": skills_raw}

    if path.exists():
        text = path.read_text()
        if text.strip():
            user_cfg = yaml.safe_load(text) or {}
            if not isinstance(user_cfg, dict):
                raise ValueError(f"{path}: top-level config must be a mapping")
            full_raw = _deep_merge(full_raw, user_cfg)
            accounts_raw = full_raw.get("accounts", accounts_raw)
            skills_raw = full_raw.get("skills", skills_raw)

    accounts: dict[str, AccountConfig] = {}
    for key, data in accounts_raw.items():
        accounts[key] = AccountConfig(
            name=data.get("name", key),
            type=data.get("type", "cash"),
            balance_usd=float(data.get("balance_usd", 0)),
            raw=data,
            pool_member_of=data.get("pool_member_of"),
        )

    skills: dict[str, SkillConfig] = {}
    for key, data in skills_raw.items():
        skills[key] = SkillConfig(
            name=key,
            tier=int(data.get("tier", 4)),
            default_watchlist=list(data.get("default_watchlist", [])),
        )

    return Config(accounts=accounts, skills=skills, raw=full_raw)
