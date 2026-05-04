"""Plugin loader for user-authored indicators.

Discovers and instantiates indicators dropped into ~/.trading-dashboard/plugins/.
Each plugin .py file should expose ONE of:

    INDICATOR = MyIndicator()   # module-level instance, simplest case

    class Indicator:            # class named exactly 'Indicator', no-arg init
        name = "my_thing"
        inputs = ("close",)
        def compute(self, df): ...

The loader validates that the resulting instance satisfies IndicatorProtocol.

Files starting with underscore are skipped (treated as private helpers).

Self-hosted, single-user, local Python — no sandboxing. Plugins run as the
user. Per Winston's round-4 guidance: "the same threat model as `pip install`
anything." Don't install plugins you don't trust.
"""
from __future__ import annotations

import importlib.util
from pathlib import Path
from typing import Iterable

from indicators.protocol import IndicatorProtocol


DEFAULT_PLUGINS_DIR = Path.home() / ".trading-dashboard" / "plugins"


class PluginLoadError(Exception):
    """Raised when a plugin file fails to import or doesn't satisfy the protocol."""


def _discover_files(plugins_dir: Path) -> Iterable[Path]:
    if not plugins_dir.exists() or not plugins_dir.is_dir():
        return []
    return sorted(p for p in plugins_dir.glob("*.py") if not p.name.startswith("_"))


def _instantiate(module) -> IndicatorProtocol | None:
    instance = getattr(module, "INDICATOR", None)
    if instance is not None:
        return instance

    cls = getattr(module, "Indicator", None)
    if cls is not None:
        return cls()

    return None


def load_plugins(
    plugins_dir: Path = DEFAULT_PLUGINS_DIR,
    strict: bool = True,
) -> dict[str, IndicatorProtocol]:
    """Discover and load all indicator plugins.

    Args:
        plugins_dir: directory containing .py plugin files.
        strict: if True (default), raise PluginLoadError on any plugin that fails
                to import or doesn't satisfy IndicatorProtocol. If False, log to
                stderr and skip the offending plugin.

    Returns:
        dict mapping indicator name -> instance.
    """
    registry: dict[str, IndicatorProtocol] = {}

    for path in _discover_files(plugins_dir):
        module_name = f"_trading_dashboard_plugin_{path.stem}"
        spec = importlib.util.spec_from_file_location(module_name, path)
        if spec is None or spec.loader is None:
            if strict:
                raise PluginLoadError(f"Could not create import spec for {path}")
            continue

        module = importlib.util.module_from_spec(spec)
        try:
            spec.loader.exec_module(module)
        except Exception as exc:
            if strict:
                raise PluginLoadError(f"Failed to import {path.name}: {exc}") from exc
            continue

        instance = _instantiate(module)
        if instance is None:
            # No indicator defined in this file — skip silently.
            continue

        if not isinstance(instance, IndicatorProtocol):
            if strict:
                raise PluginLoadError(
                    f"{path.name}: object does not satisfy IndicatorProtocol "
                    f"(missing name/inputs/compute)"
                )
            continue

        if instance.name in registry:
            if strict:
                raise PluginLoadError(
                    f"Duplicate indicator name {instance.name!r} from {path.name}"
                )
            continue

        registry[instance.name] = instance

    return registry
