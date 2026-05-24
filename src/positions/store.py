"""JSON-backed position store at ~/.trading-dashboard/positions.json.

Durability invariant: positions written here survive process crashes,
mid-write OOM kills, and accidental file corruption. The single-file
shape (one JSON array of all positions) is the highest-leverage place
for atomic-write semantics — a partial write would lose every trade.

`save()` uses `write_json_atomic` (tmp + os.replace, fsynced).
`_ensure_loaded` uses `load_json_safe` so a corrupt file logs and starts
empty rather than crashing the app at boot.
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import TYPE_CHECKING

from positions.model import Position
from storage.atomic import load_json_safe, write_json_atomic

if TYPE_CHECKING:
    from storage.cache import Cache


DEFAULT_POSITIONS_PATH = Path.home() / ".trading-dashboard" / "positions.json"

logger = logging.getLogger(__name__)


class PositionStore:
    def __init__(self, path: Path | None = None, cache: "Cache | None" = None):
        """Construct a position store.

        `cache` is an optional SQLite cache wired write-through — every
        save() will upsert affected positions. JSON remains canonical;
        cache write failures are logged but never raised, so a broken
        cache never prevents a trade from being recorded.
        """
        self.path = path if path is not None else DEFAULT_POSITIONS_PATH
        self.cache = cache
        self._positions: list[Position] = []
        self._loaded = False

    def _ensure_loaded(self) -> None:
        if self._loaded:
            return
        data = load_json_safe(self.path, default=None)
        if data is None and self.path.exists():
            # File exists but was corrupt/unreadable. Surface loudly so the
            # user can recover from a backup, but don't crash the app.
            logger.error(
                "positions.json at %s could not be parsed; starting empty. "
                "Inspect/restore the file before opening new trades.",
                self.path,
            )
            data = []
        elif data is None:
            data = []
        self._positions = [Position.from_dict(p) for p in data]
        self._loaded = True

    def save(self) -> None:
        payload = [p.to_dict() for p in self._positions]
        write_json_atomic(self.path, payload)
        # Write-through to SQLite cache. Never fail the JSON save if the
        # cache write blows up — the cache is rebuildable.
        if self.cache is not None:
            for entry in payload:
                try:
                    self.cache.upsert_position(entry)
                except Exception:
                    logger.exception(
                        "cache upsert failed for position id=%s; cache "
                        "will be inconsistent until rebuilt",
                        entry.get("id"),
                    )

    def add(self, position: Position) -> Position:
        self._ensure_loaded()
        if any(p.id == position.id for p in self._positions):
            raise ValueError(f"Position id {position.id} already exists")
        self._positions.append(position)
        self.save()
        return position

    def get(self, position_id: str) -> Position:
        self._ensure_loaded()
        for p in self._positions:
            if p.id == position_id:
                return p
        raise KeyError(f"Position id {position_id!r} not found")

    def update(self, position: Position) -> Position:
        self._ensure_loaded()
        for i, p in enumerate(self._positions):
            if p.id == position.id:
                self._positions[i] = position
                self.save()
                return position
        raise KeyError(f"Position id {position.id!r} not found")

    def close(self, position_id: str, pnl_usd: float | None = None,
              notes: str | None = None, contracts: int | None = None) -> Position:
        """Close a position, fully or partially.

        If `contracts` is None: full close, P&L is recorded as-supplied
        (legacy behavior). If `contracts` is provided: routes through
        partial_close so that prior partial legs are aggregated correctly
        when this is the final leg. Callers should check `position.status`
        to know whether the position is fully closed (e.g. for triggering
        auto-scoring).
        """
        position = self.get(position_id)
        if contracts is not None:
            position.partial_close(
                contracts_closed=contracts,
                pnl_usd=pnl_usd,
                notes=notes,
            )
        else:
            position.close(pnl_usd=pnl_usd, notes=notes)
        self.update(position)
        return position

    def list_all(self) -> list[Position]:
        self._ensure_loaded()
        return list(self._positions)

    def list_open(self, account_key: str | None = None) -> list[Position]:
        self._ensure_loaded()
        out = [p for p in self._positions if p.status == "open"]
        if account_key is not None:
            out = [p for p in out if p.account_key == account_key]
        return out

    def open_premium_at_risk(self, account_key: str | None = None) -> float:
        return sum(p.max_loss_usd for p in self.list_open(account_key))
