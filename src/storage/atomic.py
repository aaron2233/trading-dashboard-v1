"""Crash-safe JSON storage primitives.

`write_json_atomic` writes to a sibling temp file then `os.replace()`s into
place — POSIX-atomic, so a crash mid-write leaves the original file intact
(or the new file complete, never a half-written hybrid).

`load_json_safe` returns a default value on missing / empty / corrupt /
unreadable files instead of raising. Callers that want strict semantics can
fall back to a direct json.loads(path.read_text()).
"""
from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Any, Callable, TypeVar

T = TypeVar("T")


def write_json_atomic(
    path: Path,
    data: Any,
    *,
    indent: int = 2,
    default: Callable[[Any], Any] | None = str,
) -> None:
    """Atomically write `data` as JSON to `path`.

    Crash-safety guarantee: after this returns, `path` either contains the
    fully-written new data or is untouched (the prior contents). Never a
    truncated mid-write file.

    Implementation: write to a tempfile in the same directory (so rename is
    a same-filesystem move), fsync, then os.replace. POSIX-atomic on a
    single filesystem.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_path = tempfile.mkstemp(
        prefix=path.name + ".",
        suffix=".tmp",
        dir=path.parent,
    )
    tmp = Path(tmp_path)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=indent, default=default)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    except BaseException:
        # Best-effort cleanup; tmp may already have been replaced.
        try:
            tmp.unlink()
        except OSError:
            pass
        raise


def load_json_safe(
    path: Path,
    *,
    default: T | None = None,
) -> Any | T | None:
    """Load JSON from `path`, returning `default` on any failure.

    Failure modes covered:
      - Path doesn't exist
      - File is empty / whitespace-only
      - File is not valid UTF-8 / not valid JSON
      - File can't be read (permissions, etc.)

    Use this when you want best-effort recovery (e.g. on app startup).
    For strict semantics, use json.loads(path.read_text()) directly.
    """
    if not path.exists():
        return default
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return default
    if not text.strip():
        return default
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return default
