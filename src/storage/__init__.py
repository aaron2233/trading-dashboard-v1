"""Storage primitives shared across stores.

Pattern: JSON is canonical (source of truth on disk, human-inspectable).
SQLite is a derived cache for fast queries — always rebuildable from JSON.

`atomic` provides crash-safe writes (tmp + os.replace) and resilient
loaders that don't crash on corrupt/empty/missing files.

`cache` provides the SQLite cache layer — see cache.py.
"""

from storage.atomic import (
    load_json_safe,
    write_json_atomic,
)

__all__ = ["load_json_safe", "write_json_atomic"]
