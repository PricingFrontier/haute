from __future__ import annotations

import importlib.util
import json
import os
import re
import sys
import types
from pathlib import Path
from unittest.mock import Mock

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


def test_invalid_build_flag_fails_before_editable_early_return(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = _load_hatch_build(monkeypatch)
    monkeypatch.setenv("HAUTE_BUILD_FRONTEND", "sometimes")

    with pytest.raises(RuntimeError, match="HAUTE_BUILD_FRONTEND must be"):
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


def test_static_assets_require_non_empty_assets_directory(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    module = _load_hatch_build(monkeypatch)
    frontend_dir = tmp_path / "frontend"
    frontend_dir.mkdir()
    static_dir = tmp_path / "src" / "haute" / "static"
    static_dir.mkdir(parents=True)
    (static_dir / "index.html").write_text("<!doctype html>", encoding="utf-8")
    (static_dir / "assets").mkdir()
    hook = module.FrontendBuildHook()
    hook.root = str(tmp_path)

    with pytest.raises(RuntimeError, match="non-empty assets directory"):
        hook.initialize("standard", {})


def test_explicit_build_runs_clean_install_and_checks_postbuild_readiness(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    module = _load_hatch_build(monkeypatch)
    frontend_dir = tmp_path / "frontend"
    (frontend_dir / "node_modules").mkdir(parents=True)
    static_dir = tmp_path / "src" / "haute" / "static"
    static_dir.mkdir(parents=True)
    (static_dir / "index.html").write_text("<!doctype html>", encoding="utf-8")
    (static_dir / "assets").mkdir()
    (static_dir / "assets" / "app.js").write_text("x", encoding="utf-8")
    monkeypatch.setenv("HAUTE_BUILD_FRONTEND", "1")
    hook = module.FrontendBuildHook()
    hook.root = str(tmp_path)
    run = Mock()
    monkeypatch.setattr(hook, "_run", run)

    hook.initialize("standard", {})

    assert run.call_args_list[0].args[0][1:] == ["ci", "--prefer-offline"]


def test_explicit_build_rejects_incomplete_postbuild_assets(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    module = _load_hatch_build(monkeypatch)
    frontend_dir = tmp_path / "frontend"
    frontend_dir.mkdir()
    static_dir = tmp_path / "src" / "haute" / "static"
    static_dir.mkdir(parents=True)
    index_html = static_dir / "index.html"
    index_html.write_text("<!doctype html>", encoding="utf-8")
    lock = frontend_dir / "package-lock.json"
    lock.write_text("{}", encoding="utf-8")
    index_mtime = index_html.stat().st_mtime
    os.utime(lock, (index_mtime + 1, index_mtime + 1))
    monkeypatch.setenv("HAUTE_BUILD_FRONTEND", "1")
    hook = module.FrontendBuildHook()
    hook.root = str(tmp_path)
    monkeypatch.setattr(hook, "_run", Mock())

    with pytest.raises(RuntimeError, match="non-empty assets directory"):
        hook.initialize("standard", {})


def test_package_lock_participates_in_staleness(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    module = _load_hatch_build(monkeypatch)
    index_html = tmp_path / "index.html"
    index_html.write_text("index", encoding="utf-8")
    lock = tmp_path / "package-lock.json"
    lock.write_text("{}", encoding="utf-8")
    index_mtime = index_html.stat().st_mtime
    os.utime(lock, (index_mtime + 1, index_mtime + 1))
    assert module.FrontendBuildHook._is_stale(tmp_path, index_html) is True


def test_run_uses_safe_text_decoding_and_timeout(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    module = _load_hatch_build(monkeypatch)
    completed = types.SimpleNamespace(returncode=0, stdout="", stderr="")
    run = Mock(return_value=completed)
    monkeypatch.setattr(module.subprocess, "run", run)
    hook = module.FrontendBuildHook()
    hook._run(["npm", "ci"], tmp_path)

    assert run.call_args.kwargs["errors"] == "replace"
    assert run.call_args.kwargs["timeout"] == 900


def test_run_translates_timeout(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    module = _load_hatch_build(monkeypatch)
    timeout = module.subprocess.TimeoutExpired("npm", 900)
    monkeypatch.setattr(module.subprocess, "run", Mock(side_effect=timeout))

    with pytest.raises(RuntimeError, match=r"timed out after 900 seconds: npm ci"):
        module.FrontendBuildHook()._run(["npm", "ci"], tmp_path)


def test_node_and_npm_toolchain_is_exactly_pinned_across_workflows() -> None:
    root = Path(__file__).resolve().parents[1]
    package = json.loads((root / "frontend" / "package.json").read_text(encoding="utf-8"))
    lock = json.loads((root / "frontend" / "package-lock.json").read_text(encoding="utf-8"))
    expected_engines = {"node": "22.14.0", "npm": "10.9.2"}

    assert package["engines"] == expected_engines
    assert lock["packages"][""]["engines"] == expected_engines

    workflow_versions: list[str] = []
    for workflow in (root / ".github" / "workflows").glob("*.yml"):
        workflow_versions.extend(
            re.findall(r"(?m)^\s*node-version:\s*[\"']?([^\"'\s]+)", workflow.read_text())
        )
    assert workflow_versions
    assert set(workflow_versions) == {"22.14.0"}
