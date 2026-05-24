"""Tests for src/storage/atomic.py — atomic writes and resilient loads.

These tests guard the durability invariant: "once trades are saved and the
app is reloaded, those trades are still there." The atomic-write primitive
is the foundation that makes that true even on crashes / OOM / power loss.
"""
from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from storage.atomic import load_json_safe, write_json_atomic


# ── write_json_atomic ──────────────────────────────────────────────────────


def test_write_atomic_basic_roundtrip(tmp_path: Path) -> None:
    p = tmp_path / "data.json"
    write_json_atomic(p, {"a": 1, "b": [2, 3]})
    assert json.loads(p.read_text()) == {"a": 1, "b": [2, 3]}


def test_write_atomic_creates_parent_dirs(tmp_path: Path) -> None:
    p = tmp_path / "deep" / "nested" / "path" / "data.json"
    write_json_atomic(p, [{"x": 1}])
    assert p.exists()
    assert json.loads(p.read_text()) == [{"x": 1}]


def test_write_atomic_overwrites_existing(tmp_path: Path) -> None:
    p = tmp_path / "data.json"
    p.write_text('{"old": true}')
    write_json_atomic(p, {"new": True})
    assert json.loads(p.read_text()) == {"new": True}


def test_write_atomic_preserves_file_on_serialize_failure(tmp_path: Path) -> None:
    """If serialization raises, the original file is untouched."""
    p = tmp_path / "data.json"
    p.write_text('{"original": true}')

    class Unserializable:
        pass

    with pytest.raises(TypeError):
        # `default=None` means non-JSON types raise TypeError
        write_json_atomic(p, {"bad": Unserializable()}, default=None)

    # Original content intact
    assert json.loads(p.read_text()) == {"original": True}


def test_write_atomic_no_temp_files_left_on_success(tmp_path: Path) -> None:
    p = tmp_path / "data.json"
    write_json_atomic(p, {"x": 1})
    write_json_atomic(p, {"x": 2})
    write_json_atomic(p, {"x": 3})
    # After 3 writes, only the canonical file should remain.
    files = list(tmp_path.iterdir())
    assert len(files) == 1
    assert files[0] == p


def test_write_atomic_no_temp_files_left_on_failure(tmp_path: Path) -> None:
    p = tmp_path / "data.json"
    p.write_text('{"original": true}')

    class Unserializable:
        pass

    with pytest.raises(TypeError):
        write_json_atomic(p, {"bad": Unserializable()}, default=None)

    # Only the canonical file remains; tmp file cleaned up.
    files = list(tmp_path.iterdir())
    assert len(files) == 1
    assert files[0] == p


def test_write_atomic_uses_default_serializer(tmp_path: Path) -> None:
    """Default `default=str` lets us serialize Path, datetime, etc."""
    from datetime import date

    p = tmp_path / "data.json"
    write_json_atomic(p, {"d": date(2026, 5, 4)})
    assert json.loads(p.read_text()) == {"d": "2026-05-04"}


# ── load_json_safe ─────────────────────────────────────────────────────────


def test_load_safe_reads_valid_json(tmp_path: Path) -> None:
    p = tmp_path / "data.json"
    p.write_text('{"a": 1}')
    assert load_json_safe(p) == {"a": 1}


def test_load_safe_returns_default_when_missing(tmp_path: Path) -> None:
    p = tmp_path / "missing.json"
    assert load_json_safe(p, default=[]) == []
    assert load_json_safe(p, default={"x": 1}) == {"x": 1}
    assert load_json_safe(p) is None


def test_load_safe_returns_default_on_empty_file(tmp_path: Path) -> None:
    p = tmp_path / "empty.json"
    p.write_text("")
    assert load_json_safe(p, default=[]) == []


def test_load_safe_returns_default_on_whitespace_only(tmp_path: Path) -> None:
    p = tmp_path / "ws.json"
    p.write_text("   \n  \t  ")
    assert load_json_safe(p, default=[]) == []


def test_load_safe_returns_default_on_corrupt_json(tmp_path: Path) -> None:
    p = tmp_path / "bad.json"
    p.write_text('{"unterminated": ')
    assert load_json_safe(p, default=[]) == []


def test_load_safe_returns_default_on_truncated_mid_write(tmp_path: Path) -> None:
    """Simulate the exact crash-during-write failure mode."""
    p = tmp_path / "truncated.json"
    p.write_text('[{"id": "abc", "ticker": "AAP')  # truncated JSON
    assert load_json_safe(p, default=[]) == []


def test_load_safe_returns_default_on_non_utf8(tmp_path: Path) -> None:
    p = tmp_path / "binary.json"
    p.write_bytes(b"\x80\x81\x82\x83")
    assert load_json_safe(p, default={}) == {}


def test_load_safe_no_swallow_after_successful_atomic_write(tmp_path: Path) -> None:
    """Round-trip: atomic write + safe load should always recover the data."""
    p = tmp_path / "data.json"
    payload = [{"id": f"pos_{i}", "ticker": "AAPL"} for i in range(50)]
    write_json_atomic(p, payload)
    assert load_json_safe(p, default=[]) == payload


# ── Crash-safety simulation ────────────────────────────────────────────────


def test_simulated_concurrent_writers_yield_consistent_state(tmp_path: Path) -> None:
    """Two sequential writers; we verify only complete payloads end up on disk."""
    p = tmp_path / "data.json"
    write_json_atomic(p, {"writer": "A", "n": 1})
    intermediate = json.loads(p.read_text())
    assert intermediate == {"writer": "A", "n": 1}

    write_json_atomic(p, {"writer": "B", "n": 2})
    final = json.loads(p.read_text())
    assert final == {"writer": "B", "n": 2}


def test_atomic_write_visible_inode_swap(tmp_path: Path) -> None:
    """After overwrite, the file inode changes — proving we used os.replace
    rather than truncate-and-rewrite."""
    p = tmp_path / "data.json"
    write_json_atomic(p, {"v": 1})
    inode_a = os.stat(p).st_ino
    write_json_atomic(p, {"v": 2})
    inode_b = os.stat(p).st_ino
    # On Linux/macOS, os.replace from a different file gives a new inode.
    assert inode_a != inode_b
