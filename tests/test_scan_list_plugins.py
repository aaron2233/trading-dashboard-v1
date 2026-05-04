from pathlib import Path

import pytest


_VALID_PLUGIN = '''
import pandas as pd

class _Ind:
    name = "demo_indicator"
    inputs = ("close",)
    def compute(self, df):
        return pd.DataFrame({"value": df["close"]}, index=df.index)

INDICATOR = _Ind()
'''


def test_list_plugins_with_no_plugins(tmp_path: Path,
                                      monkeypatch: pytest.MonkeyPatch,
                                      capsys: pytest.CaptureFixture):
    monkeypatch.setattr("scan.DEFAULT_PLUGINS_DIR", tmp_path / "missing")
    monkeypatch.setattr("scan.load_plugins",
                        lambda strict=False: {})

    import scan
    exit_code = scan.main(["--list-plugins"])
    assert exit_code == 0
    captured = capsys.readouterr()
    assert "No plugins found" in captured.out


def test_list_plugins_with_one_plugin(tmp_path: Path,
                                      monkeypatch: pytest.MonkeyPatch,
                                      capsys: pytest.CaptureFixture):
    plugins_dir = tmp_path / "plugins"
    plugins_dir.mkdir()
    (plugins_dir / "demo.py").write_text(_VALID_PLUGIN)

    from indicators.loader import load_plugins as real_load
    monkeypatch.setattr("scan.DEFAULT_PLUGINS_DIR", plugins_dir)
    monkeypatch.setattr("scan.load_plugins",
                        lambda strict=False: real_load(plugins_dir, strict=strict))

    import scan
    exit_code = scan.main(["--list-plugins"])
    assert exit_code == 0
    captured = capsys.readouterr()
    assert "demo_indicator" in captured.out
    assert "close" in captured.out
