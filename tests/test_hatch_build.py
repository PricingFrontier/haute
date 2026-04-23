from __future__ import annotations

import importlib.util
import sys
import types
from pathlib import Path

import pytest


def _load_hatch_build(monkeypatch: pytest.MonkeyPatch) -> types.ModuleType:
    package_names = [
        "hatchling",
        "hatchling.builders",
        "hatchling.builders.hooks",
        "hatchling.builders.hooks.plugin",
    ]
    for name in package_names:
        package = types.ModuleType(name)
        package.__path__ = []
        monkeypatch.setitem(sys.modules, name, package)

    interface = types.ModuleType("hatchling.builders.hooks.plugin.interface")
    interface.BuildHookInterface = object
    monkeypatch.setitem(sys.modules, interface.__name__, interface)

    path = Path(__file__).resolve().parents[1] / "hatch_build.py"
    spec = importlib.util.spec_from_file_location("_hatch_build_under_test", path)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_editable_build_skips_static_asset_validation(monkeypatch: pytest.MonkeyPatch) -> None:
    module = _load_hatch_build(monkeypatch)

    module.FrontendBuildHook().initialize("editable", {})


def test_standard_build_requires_static_assets(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    module = _load_hatch_build(monkeypatch)
    frontend_dir = tmp_path / "frontend"
    frontend_dir.mkdir()
    hook = module.FrontendBuildHook()
    hook.root = str(tmp_path)

    with pytest.raises(RuntimeError, match="Built frontend assets are missing"):
        hook.initialize("standard", {})
