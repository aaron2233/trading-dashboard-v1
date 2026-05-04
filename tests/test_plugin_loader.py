from pathlib import Path

import pandas as pd
import pytest

from indicators import IndicatorProtocol, PluginLoadError, load_plugins


_VALID_INDICATOR_FROM_INSTANCE = '''
import pandas as pd

class _MyIndicator:
    name = "from_instance"
    inputs = ("close",)
    def compute(self, df):
        return pd.DataFrame({"value": df["close"] * 2}, index=df.index)

INDICATOR = _MyIndicator()
'''

_VALID_INDICATOR_FROM_CLASS = '''
import pandas as pd

class Indicator:
    name = "from_class"
    inputs = ("close", "volume")
    def compute(self, df):
        return pd.DataFrame({"value": df["close"]}, index=df.index)
'''

_PRIVATE_HELPER = '''
# This file starts with underscore-prefix and should be skipped entirely.
class Indicator:
    name = "should_not_load"
    inputs = ("close",)
    def compute(self, df): return df
'''

_NO_INDICATOR_DEFINED = '''
def helper():
    return 42
'''

_BROKEN_PLUGIN = '''
raise RuntimeError("plugin blew up at import time")
'''

_MISSING_PROTOCOL = '''
class Indicator:
    # No `compute` method => does not satisfy IndicatorProtocol
    name = "broken"
    inputs = ("close",)
'''


def _write(plugin_dir: Path, filename: str, content: str) -> Path:
    plugin_dir.mkdir(parents=True, exist_ok=True)
    path = plugin_dir / filename
    path.write_text(content)
    return path


def test_load_plugins_returns_empty_for_missing_dir(tmp_path: Path):
    missing = tmp_path / "does_not_exist"
    assert load_plugins(missing) == {}


def test_load_plugins_returns_empty_for_empty_dir(tmp_path: Path):
    (tmp_path / "plugins").mkdir()
    assert load_plugins(tmp_path / "plugins") == {}


def test_loads_indicator_from_instance(tmp_path: Path):
    plugins = tmp_path / "plugins"
    _write(plugins, "my_ind.py", _VALID_INDICATOR_FROM_INSTANCE)
    registry = load_plugins(plugins)
    assert "from_instance" in registry
    assert isinstance(registry["from_instance"], IndicatorProtocol)
    assert list(registry["from_instance"].inputs) == ["close"]


def test_loads_indicator_from_class(tmp_path: Path):
    plugins = tmp_path / "plugins"
    _write(plugins, "klass_ind.py", _VALID_INDICATOR_FROM_CLASS)
    registry = load_plugins(plugins)
    assert "from_class" in registry


def test_underscore_files_are_skipped(tmp_path: Path):
    plugins = tmp_path / "plugins"
    _write(plugins, "_helpers.py", _PRIVATE_HELPER)
    _write(plugins, "real.py", _VALID_INDICATOR_FROM_INSTANCE)
    registry = load_plugins(plugins)
    assert "from_instance" in registry
    assert "should_not_load" not in registry


def test_files_with_no_indicator_are_skipped(tmp_path: Path):
    plugins = tmp_path / "plugins"
    _write(plugins, "helpers.py", _NO_INDICATOR_DEFINED)
    registry = load_plugins(plugins)
    assert registry == {}


def test_strict_mode_raises_on_import_error(tmp_path: Path):
    plugins = tmp_path / "plugins"
    _write(plugins, "broken.py", _BROKEN_PLUGIN)
    with pytest.raises(PluginLoadError, match="broken.py"):
        load_plugins(plugins, strict=True)


def test_lenient_mode_skips_broken_plugins(tmp_path: Path):
    plugins = tmp_path / "plugins"
    _write(plugins, "broken.py", _BROKEN_PLUGIN)
    _write(plugins, "good.py", _VALID_INDICATOR_FROM_INSTANCE)
    registry = load_plugins(plugins, strict=False)
    assert "from_instance" in registry
    assert len(registry) == 1


def test_strict_mode_rejects_protocol_violation(tmp_path: Path):
    plugins = tmp_path / "plugins"
    _write(plugins, "missing.py", _MISSING_PROTOCOL)
    with pytest.raises(PluginLoadError, match="IndicatorProtocol"):
        load_plugins(plugins, strict=True)


def test_duplicate_names_raise_in_strict_mode(tmp_path: Path):
    plugins = tmp_path / "plugins"
    _write(plugins, "first.py", _VALID_INDICATOR_FROM_INSTANCE)
    # Second file using a different file name but same indicator name
    _write(plugins, "second.py", _VALID_INDICATOR_FROM_INSTANCE)
    with pytest.raises(PluginLoadError, match="Duplicate"):
        load_plugins(plugins, strict=True)


def test_loaded_plugin_can_be_called(tmp_path: Path):
    plugins = tmp_path / "plugins"
    _write(plugins, "ind.py", _VALID_INDICATOR_FROM_INSTANCE)
    registry = load_plugins(plugins)
    indicator = registry["from_instance"]
    bars = pd.DataFrame({"close": [100.0, 110.0, 120.0]},
                        index=pd.bdate_range("2026-01-02", periods=3))
    out = indicator.compute(bars)
    assert list(out["value"]) == [200.0, 220.0, 240.0]
