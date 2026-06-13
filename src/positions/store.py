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
import shutil
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING

from positions.model import Position
from storage.atomic import load_json_safe, write_json_atomic

if TYPE_CHECKING:
    from storage.cache import Cache


DEFAULT_POSITIONS_PATH = Path.home() / ".trading-dashboard" / "positions.json"

logger = logging.getLogger(__name__)


def _open_dedup_key(p: Position) -> tuple:
    """Identity for double-submit detection: same contract in the same account."""
    return (
        (p.ticker or "").upper(),
        (p.instrument or "").lower(),
        p.strike,
        p.expiry,
        p.account_key,
    )


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
            # File exists but was corrupt/unreadable. Preserve the original
            # bytes BEFORE we proceed — otherwise the next save() atomically
            # overwrites the corrupt file with a near-empty array, destroying
            # data a JSON repair could often have recovered (this is the
            # failure mode behind the positions.json.bak-* incident trail).
            stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
            backup = self.path.with_name(f"{self.path.name}.corrupt-{stamp}")
            try:
                shutil.copy2(self.path, backup)
                logger.error(
                    "positions.json at %s could not be parsed; preserved the "
                    "original to %s and starting empty. Inspect/restore before "
                    "opening new trades.",
                    self.path, backup,
                )
            except Exception:
                logger.exception(
                    "positions.json at %s could not be parsed AND the corrupt "
                    "file could not be backed up; starting empty. Do NOT open "
                    "new trades until you have inspected the file by hand.",
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

    def add(self, position: Position, allow_duplicate: bool = False) -> Position:
        self._ensure_loaded()
        if any(p.id == position.id for p in self._positions):
            raise ValueError(f"Position id {position.id} already exists")
        # Dedup guard: reject a new OPEN position identical (ticker + instrument
        # + strike + expiry) to one already open in the same account — almost
        # always a double-submit (the failure mode behind the MARA-dupe
        # incident). Pass allow_duplicate=True for a genuine second lot. The
        # portfolio sleeve is exempt: DCA into a held name is sanctioned there.
        if not allow_duplicate and position.account_key != "portfolio":
            key = _open_dedup_key(position)
            dupe = next(
                (p for p in self._positions
                 if p.status == "open" and _open_dedup_key(p) == key),
                None,
            )
            if dupe is not None:
                raise ValueError(
                    f"An open {position.ticker} {position.instrument} position with the "
                    f"same strike/expiry already exists (id {dupe.id}). This is usually a "
                    f"double-submit — pass allow_duplicate=True to add a second lot."
                )
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
