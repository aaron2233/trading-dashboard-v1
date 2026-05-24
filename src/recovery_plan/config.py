"""Recovery-plan config persisted at ~/.trading-dashboard/recovery_plan.json.

Stores:
  - year_start_balance     starting balance for 2026
  - current_balance        most recent reported balance (manual update)
  - ytd_realized_pnl       YTD P&L baseline (positive = green, negative = red)
  - deposits_total         cumulative cash adds during the year
  - year_breakeven_target  the recovery target (year_start - ytd_realized_pnl)
  - plan_committed_at      ISO timestamp when 3 hard rules were committed
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from storage.atomic import load_json_safe, write_json_atomic


DEFAULT_CONFIG_PATH = Path.home() / ".trading-dashboard" / "recovery_plan.json"


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass
class RecoveryConfig:
    """Recovery plan state. Manually maintained — user updates current_balance
    via the dashboard after each broker check-in. Deposits should be logged
    here as they happen so the milestones reflect "trading-only" progress.

    First-run defaults are zeros so a new user sees a blank slate and fills
    in their own starting state via the dashboard's recovery-plan view."""
    year_start_balance: float = 0.0
    current_balance: float = 0.0
    ytd_realized_pnl: float = 0.0
    deposits_total: float = 0.0
    year_breakeven_target: float = 0.0
    plan_committed_at: str = ""
    last_updated_at: str = field(default_factory=_now_iso)

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, payload: dict) -> "RecoveryConfig":
        from dataclasses import fields as _fields
        known = {f.name for f in _fields(cls)}
        return cls(**{k: v for k, v in payload.items() if k in known})


def load_config(path: Path | None = None) -> RecoveryConfig:
    """Load the config from JSON. Returns defaults if missing (first run)."""
    target = path if path is not None else DEFAULT_CONFIG_PATH
    payload = load_json_safe(target, default=None)
    if payload is None:
        # First boot — write the seed config so the user can edit it on disk
        cfg = RecoveryConfig()
        save_config(cfg, target)
        return cfg
    return RecoveryConfig.from_dict(payload)


def save_config(cfg: RecoveryConfig, path: Path | None = None) -> None:
    target = path if path is not None else DEFAULT_CONFIG_PATH
    cfg.last_updated_at = _now_iso()
    write_json_atomic(target, cfg.to_dict())
