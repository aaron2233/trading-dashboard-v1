"""JSON-backed position store at ~/.trading-dashboard/positions.json."""
from __future__ import annotations

import json
from pathlib import Path

from positions.model import Position


DEFAULT_POSITIONS_PATH = Path.home() / ".trading-dashboard" / "positions.json"


class PositionStore:
    def __init__(self, path: Path | None = None):
        self.path = path if path is not None else DEFAULT_POSITIONS_PATH
        self._positions: list[Position] = []
        self._loaded = False

    def _ensure_loaded(self) -> None:
        if self._loaded:
            return
        if self.path.exists():
            data = json.loads(self.path.read_text() or "[]")
            self._positions = [Position.from_dict(p) for p in data]
        self._loaded = True

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = [p.to_dict() for p in self._positions]
        self.path.write_text(json.dumps(payload, indent=2, default=str))

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
              notes: str | None = None) -> Position:
        position = self.get(position_id)
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
