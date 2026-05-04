"""Pyramid persistence — JSON files at ~/.trading-dashboard/pyramids/<id>.json.

Single-file-per-pyramid keeps debugging trivial and avoids races when multiple
pyramids are concurrently active. Modeled on src/positions/store.py.
"""
from __future__ import annotations

import json
from pathlib import Path

from pyramid.model import Pyramid


DEFAULT_PYRAMIDS_DIR = Path.home() / ".trading-dashboard" / "pyramids"


class PyramidStore:
    def __init__(self, base_dir: Path | None = None) -> None:
        self.base_dir = base_dir or DEFAULT_PYRAMIDS_DIR
        self.base_dir.mkdir(parents=True, exist_ok=True)

    def _path(self, pyramid_id: str) -> Path:
        return self.base_dir / f"{pyramid_id}.json"

    def save(self, pyramid: Pyramid) -> Path:
        path = self._path(pyramid.id)
        path.write_text(json.dumps(pyramid.to_dict(), indent=2, default=str))
        return path

    def load(self, pyramid_id: str) -> Pyramid:
        path = self._path(pyramid_id)
        if not path.exists():
            raise KeyError(f"No pyramid with id={pyramid_id}")
        data = json.loads(path.read_text())
        return Pyramid.from_dict(data)

    def list_all(self) -> list[Pyramid]:
        out: list[Pyramid] = []
        for path in sorted(self.base_dir.glob("*.json")):
            try:
                data = json.loads(path.read_text())
                out.append(Pyramid.from_dict(data))
            except (json.JSONDecodeError, OSError, TypeError, ValueError):
                # Skip corrupt files; surfacing the error is the caller's job
                continue
        return out

    def list_active(self) -> list[Pyramid]:
        return [p for p in self.list_all() if p.status in ("pending", "active")]

    def delete(self, pyramid_id: str) -> bool:
        path = self._path(pyramid_id)
        if not path.exists():
            return False
        path.unlink()
        return True
