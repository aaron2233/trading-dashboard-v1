"""Pytest config — isolates tests from the user's real ~/.trading-dashboard/.

The dashboard's storage paths are module-level constants, e.g.
    DEFAULT_DISCIPLINE_DIR = Path.home() / ".trading-dashboard" / "discipline"
which freeze at import time. Without isolation, any test that imports
those modules and runs a write code path leaks state into the dev's real
data dir (we accumulated 58 orphan discipline files before this fix).

Fix is two-layered:

1. Module-load time: redirect HOME to a session-scoped tempdir BEFORE any
   src/ module is imported. This catches the module-level constants.
2. Per-test: monkeypatch Path.home() so any runtime resolution also lands
   in tmp_path. Belt-and-suspenders against future code that resolves
   paths lazily.
"""
from __future__ import annotations

import os
import shutil
import tempfile
from pathlib import Path

import pytest


# Runs at conftest load time — before pytest imports any test files,
# which means before src/ modules are imported.
_TEST_HOME_KEY = "TRADING_DASHBOARD_TEST_HOME"
if _TEST_HOME_KEY not in os.environ:
    _test_home = tempfile.mkdtemp(prefix="trading-dashboard-test-home-")
    os.environ["HOME"] = _test_home
    os.environ[_TEST_HOME_KEY] = _test_home


def pytest_sessionfinish(session, exitstatus):  # noqa: ARG001
    test_home = os.environ.get(_TEST_HOME_KEY)
    if test_home and Path(test_home).exists():
        shutil.rmtree(test_home, ignore_errors=True)


@pytest.fixture(autouse=True)
def _isolate_home_per_test(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
