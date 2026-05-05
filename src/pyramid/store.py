"""Pyramid persistence — JSON files at ~/.trading-dashboard/pyramids/<id>.json.

Single-file-per-pyramid keeps debugging trivial and avoids races when multiple
pyramids are concurrently active. Modeled on src/positions/store.py.
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import TYPE_CHECKING

from pyramid.model import Pyramid
from storage.atomic import load_json_safe, write_json_atomic

if TYPE_CHECKING:
    from storage.cache import Cache


DEFAULT_PYRAMIDS_DIR = Path.home() / ".trading-dashboard" / "pyramids"

logger = logging.getLogger(__name__)


class PyramidStore:
    def __init__(
        self,
        base_dir: Path | None = None,
        cache: "Cache | None" = None,
    ) -> None:
        self.base_dir = base_dir or DEFAULT_PYRAMIDS_DIR
        self.base_dir.mkdir(parents=True, exist_ok=True)
        self.cache = cache

    def _path(self, pyramid_id: str) -> Path:
        return self.base_dir / f"{pyramid_id}.json"

    def save(self, pyramid: Pyramid) -> Path:
        path = self._path(pyramid.id)
        payload = pyramid.to_dict()
        write_json_atomic(path, payload)
        if self.cache is not None:
            try:
                self.cache.upsert_pyramid(payload)
            except Exception:
                logger.exception(
                    "cache upsert failed for pyramid id=%s", pyramid.id
                )
        return path

    def load(self, pyramid_id: str) -> Pyramid:
        path = self._path(pyramid_id)
        if not path.exists():
            raise KeyError(f"No pyramid with id={pyramid_id}")
        data = load_json_safe(path)
        if data is None:
            raise KeyError(f"Pyramid file for id={pyramid_id} is corrupt")
        return Pyramid.from_dict(data)

    def list_all(self) -> list[Pyramid]:
        out: list[Pyramid] = []
        for path in sorted(self.base_dir.glob("*.json")):
            data = load_json_safe(path)
            if data is None:
                continue
            try:
                out.append(Pyramid.from_dict(data))
            except (TypeError, ValueError, KeyError):
                continue
        return out

    def list_active(self) -> list[Pyramid]:
        return [p for p in self.list_all() if p.status in ("pending", "active")]

    def delete(self, pyramid_id: str) -> bool:
        path = self._path(pyramid_id)
        if not path.exists():
            return False
        path.unlink()
        if self.cache is not None:
            try:
                self.cache.delete_pyramid(pyramid_id)
            except Exception:
                logger.exception(
                    "cache delete failed for pyramid id=%s", pyramid_id
                )
        return True
