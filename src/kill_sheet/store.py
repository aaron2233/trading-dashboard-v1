"""Kill-sheet persistence — JSON files at ~/.trading-dashboard/kill_sheets/<id>.json.

Phase B (position-open authorization gate, 2026-05-04): every AUTHORIZED
kill sheet is stored on disk so a position can reference its origin via
kill_sheet_id. Rejected kill sheets are NOT persisted — they're transient
diagnostic output, not load-bearing for the position record.

JSON canonical (matches the broader storage contract); written atomically
via storage.atomic.write_json_atomic. Resilient loads via load_json_safe.
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import TYPE_CHECKING

from kill_sheet.model import KillSheet
from storage.atomic import load_json_safe, write_json_atomic

if TYPE_CHECKING:
    from storage.cache import Cache


DEFAULT_KILL_SHEETS_DIR = Path.home() / ".trading-dashboard" / "kill_sheets"

logger = logging.getLogger(__name__)


class KillSheetStore:
    def __init__(
        self,
        base_dir: Path | None = None,
        cache: "Cache | None" = None,
    ) -> None:
        self.base_dir = base_dir or DEFAULT_KILL_SHEETS_DIR
        self.base_dir.mkdir(parents=True, exist_ok=True)
        self.cache = cache

    def _path(self, kill_sheet_id: str) -> Path:
        return self.base_dir / f"{kill_sheet_id}.json"

    def save(self, kill_sheet: KillSheet) -> Path:
        """Persist an authorized kill sheet. Caller is responsible for
        rejecting non-AUTHORIZED kill sheets if persistence isn't wanted."""
        path = self._path(kill_sheet.id)
        payload = kill_sheet.to_dict()
        write_json_atomic(path, payload)
        if self.cache is not None:
            try:
                self.cache.upsert_kill_sheet(payload)
            except Exception:
                logger.exception(
                    "cache upsert failed for kill sheet id=%s", kill_sheet.id
                )
        return path

    def load(self, kill_sheet_id: str) -> KillSheet | None:
        """Load by ID. Returns None if missing or corrupt."""
        path = self._path(kill_sheet_id)
        if not path.exists():
            return None
        data = load_json_safe(path)
        if data is None:
            return None
        return _kill_sheet_from_dict(data)

    def exists(self, kill_sheet_id: str) -> bool:
        return self._path(kill_sheet_id).exists()

    def list_all(self) -> list[KillSheet]:
        out: list[KillSheet] = []
        for path in sorted(self.base_dir.glob("*.json")):
            data = load_json_safe(path)
            if data is None:
                continue
            try:
                out.append(_kill_sheet_from_dict(data))
            except (TypeError, ValueError, KeyError):
                continue
        return out


def _kill_sheet_from_dict(data: dict) -> KillSheet:
    """Rebuild a KillSheet from its persisted to_dict() payload.

    Tolerates extra/missing fields for forward + backward compat.
    Nested options + discipline_attestation are dataclass-shaped so we
    rebuild them carefully.
    """
    from dataclasses import fields as _fields
    from kill_sheet.model import DisciplineAttestation
    from kill_sheet.options import OptionsStructure

    known = {f.name for f in _fields(KillSheet)}
    filtered = {k: v for k, v in data.items() if k in known}

    options_raw = filtered.pop("options", None)
    if options_raw is not None and isinstance(options_raw, dict):
        opt_known = {f.name for f in _fields(OptionsStructure)}
        opt_filtered = {k: v for k, v in options_raw.items() if k in opt_known}
        filtered["options"] = OptionsStructure(**opt_filtered)

    att_raw = filtered.pop("discipline_attestation", None)
    if att_raw is not None and isinstance(att_raw, dict):
        att_known = {f.name for f in _fields(DisciplineAttestation)}
        att_filtered = {k: v for k, v in att_raw.items() if k in att_known}
        filtered["discipline_attestation"] = DisciplineAttestation(**att_filtered)

    return KillSheet(**filtered)
