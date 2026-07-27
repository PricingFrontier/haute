from __future__ import annotations

import importlib.util
import json
import re
import sys
import types
from collections.abc import Callable
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


def _seed_frontend_inputs(root: Path) -> Path:
    """Create the complete minimal production-input inventory."""
    frontend = root / "frontend"
    (frontend / "src").mkdir(parents=True)
    (frontend / "public").mkdir()
    (root / "pyproject.toml").write_text(
        '[project]\nname = "haute"\nversion = "1.2.3"\n',
        encoding="utf-8",
    )
    (frontend / "index.html").write_text(
        '<div id="root"></div><script type="module" src="/src/main.tsx"></script>',
        encoding="utf-8",
    )
    (frontend / "src" / "main.tsx").write_text("export const app = true\n", encoding="utf-8")
    (frontend / "public" / "favicon.svg").write_text("<svg/>", encoding="utf-8")
    (frontend / "public" / "vite.svg").write_text("<svg/>", encoding="utf-8")
    (frontend / ".npmrc").write_text("fund=false\n", encoding="utf-8")
    (frontend / "package.json").write_text('{"private": true}\n', encoding="utf-8")
    (frontend / "package-lock.json").write_text('{"lockfileVersion": 3}\n', encoding="utf-8")
    (frontend / "vite.config.ts").write_text("export default {}\n", encoding="utf-8")
    (frontend / "tsconfig.json").write_text(
        '{"files":[],"references":[{"path":"./tsconfig.app.json"},'
        '{"path":"./tsconfig.node.json"}]}\n',
        encoding="utf-8",
    )
    (frontend / "tsconfig.app.json").write_text('{"compilerOptions":{}}\n', encoding="utf-8")
    (frontend / "tsconfig.node.json").write_text('{"compilerOptions":{}}\n', encoding="utf-8")
    return frontend


def _write_coherent_bundle(
    module: types.ModuleType,
    root: Path,
    *,
    record_inputs: bool = True,
) -> tuple[Path, Path]:
    frontend = root / "frontend"
    static = root / "src" / "haute" / "static"
    assets = static / "assets"
    assets.mkdir(parents=True, exist_ok=True)
    index = static / "index.html"
    index.write_text(
        (
            '<!doctype html><script type="module" src="/assets/main.js"></script>'
            '<link rel="stylesheet" href="/assets/main.css">'
        ),
        encoding="utf-8",
    )
    (assets / "main.js").write_text("import('./lazy.js')\n", encoding="utf-8")
    (assets / "main.css").write_text("body{}\n", encoding="utf-8")
    (assets / "lazy.js").write_text("export default 1\n", encoding="utf-8")
    (static / "manifest.json").write_text(
        json.dumps(
            {
                "src/main.tsx": {
                    "file": "assets/main.js",
                    "isEntry": True,
                    "css": ["assets/main.css"],
                    "dynamicImports": ["src/lazy.tsx"],
                },
                "src/lazy.tsx": {
                    "file": "assets/lazy.js",
                    "isDynamicEntry": True,
                },
            }
        ),
        encoding="utf-8",
    )
    if record_inputs:
        module.FrontendBuildHook._write_input_manifest(frontend, index)
    return static, index


def _hook(module: types.ModuleType, root: Path) -> object:
    hook = module.FrontendBuildHook()
    hook.root = str(root)
    return hook


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

    with pytest.raises(RuntimeError, match="manifest|incomplete"):
        hook.initialize("standard", {})


def test_explicit_build_runs_clean_install_and_checks_postbuild_readiness(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    module = _load_hatch_build(monkeypatch)
    frontend_dir = _seed_frontend_inputs(tmp_path)
    (frontend_dir / "node_modules").mkdir(parents=True)
    monkeypatch.setenv("HAUTE_BUILD_FRONTEND", "1")
    hook = _hook(module, tmp_path)

    def run_command(cmd: list[str], *, cwd: Path) -> None:  # noqa: ARG001
        if cmd[-1] == "build":
            _write_coherent_bundle(module, tmp_path, record_inputs=False)

    run = Mock(side_effect=run_command)
    monkeypatch.setattr(hook, "_run", run)

    hook.initialize("standard", {})

    assert run.call_args_list[0].args[0][1:] == ["ci", "--prefer-offline"]
    assert run.call_args_list[1].args[0][1:] == ["run", "build"]
    assert (tmp_path / "src" / "haute" / "static" / "haute-build-inputs.json").is_file()


def test_explicit_build_rejects_incomplete_postbuild_assets(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    module = _load_hatch_build(monkeypatch)
    _seed_frontend_inputs(tmp_path)
    monkeypatch.setenv("HAUTE_BUILD_FRONTEND", "1")
    hook = _hook(module, tmp_path)
    monkeypatch.setattr(hook, "_run", Mock())

    with pytest.raises(RuntimeError, match="manifest|missing|incomplete"):
        hook.initialize("standard", {})


def test_explicit_build_rejects_inputs_changed_while_vite_runs(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    module = _load_hatch_build(monkeypatch)
    _seed_frontend_inputs(tmp_path)
    frontend = tmp_path / "frontend"
    monkeypatch.setenv("HAUTE_BUILD_FRONTEND", "1")
    hook = _hook(module, tmp_path)

    def run_command(cmd: list[str], *, cwd: Path) -> None:  # noqa: ARG001
        if cmd[-1] == "build":
            _write_coherent_bundle(module, tmp_path, record_inputs=False)
            (frontend / "src" / "main.tsx").write_text(
                "export const app = 'changed during build'\n",
                encoding="utf-8",
            )

    monkeypatch.setattr(hook, "_run", Mock(side_effect=run_command))

    with pytest.raises(RuntimeError, match="changed while.*build"):
        hook.initialize("standard", {})


@pytest.mark.parametrize(
    "relative",
    [
        "pyproject.toml",
        "frontend/index.html",
        "frontend/public/favicon.svg",
        "frontend/public/vite.svg",
        "frontend/src/main.tsx",
        "frontend/vite.config.ts",
        "frontend/tsconfig.node.json",
        "frontend/.npmrc",
        "frontend/package.json",
        "frontend/package-lock.json",
    ],
)
def test_every_declared_production_input_invalidates_the_fingerprint(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    relative: str,
) -> None:
    module = _load_hatch_build(monkeypatch)
    _seed_frontend_inputs(tmp_path)
    _write_coherent_bundle(module, tmp_path)
    target = tmp_path / relative
    if target.name.startswith("tsconfig"):
        payload = json.loads(target.read_text(encoding="utf-8"))
        payload["_changed"] = True
        target.write_text(json.dumps(payload), encoding="utf-8")
    else:
        target.write_text(
            target.read_text(encoding="utf-8") + "\nchanged",
            encoding="utf-8",
        )

    with pytest.raises(RuntimeError, match="stale|fingerprint"):
        _hook(module, tmp_path).initialize("standard", {})


@pytest.mark.parametrize("mutation", ["addition", "deletion", "rename"])
def test_production_input_additions_deletions_and_renames_are_detected(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    mutation: str,
) -> None:
    module = _load_hatch_build(monkeypatch)
    _seed_frontend_inputs(tmp_path)
    frontend = tmp_path / "frontend"
    _write_coherent_bundle(module, tmp_path)
    if mutation == "addition":
        (frontend / "src" / "added.ts").write_text("export {}\n", encoding="utf-8")
    elif mutation == "deletion":
        (frontend / "public" / "vite.svg").unlink()
    else:
        (frontend / "src" / "main.tsx").rename(frontend / "src" / "renamed.tsx")

    with pytest.raises(RuntimeError, match="stale|fingerprint"):
        _hook(module, tmp_path).initialize("standard", {})


@pytest.mark.parametrize(
    "relative",
    [
        "frontend/README.md",
        "frontend/eslint.config.js",
        "frontend/vitest.config.ts",
        "frontend/playwright.config.ts",
        "frontend/src/__tests__/ignored.test.ts",
    ],
)
def test_documented_non_inputs_do_not_invalidate_the_fingerprint(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    relative: str,
) -> None:
    module = _load_hatch_build(monkeypatch)
    _seed_frontend_inputs(tmp_path)
    _write_coherent_bundle(module, tmp_path)
    target = tmp_path / relative
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("changed\n", encoding="utf-8")

    _hook(module, tmp_path).initialize("standard", {})


@pytest.mark.parametrize("state", ["missing", "malformed", "mismatched"])
def test_validation_rejects_absent_corrupt_or_mismatched_input_manifest(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    state: str,
) -> None:
    module = _load_hatch_build(monkeypatch)
    _seed_frontend_inputs(tmp_path)
    _write_coherent_bundle(module, tmp_path)
    manifest = tmp_path / "src" / "haute" / "static" / "haute-build-inputs.json"
    if state == "missing":
        manifest.unlink()
    elif state == "malformed":
        manifest.write_text("{", encoding="utf-8")
    else:
        payload = json.loads(manifest.read_text(encoding="utf-8"))
        payload["digest"] = "0" * 64
        manifest.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(RuntimeError, match="input manifest|fingerprint|stale"):
        _hook(module, tmp_path).initialize("standard", {})


def test_explicit_build_skips_vite_only_for_the_same_complete_proof(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    module = _load_hatch_build(monkeypatch)
    _seed_frontend_inputs(tmp_path)
    _write_coherent_bundle(module, tmp_path)
    monkeypatch.setenv("HAUTE_BUILD_FRONTEND", "1")
    hook = _hook(module, tmp_path)
    run = Mock()
    monkeypatch.setattr(hook, "_run", run)

    hook.initialize("standard", {})

    assert len(run.call_args_list) == 1
    assert run.call_args.args[0][1:] == ["ci", "--prefer-offline"]


@pytest.mark.parametrize("reference", ["assets/missing.js", "assets/missing.css"])
def test_readiness_rejects_missing_direct_entry_references_despite_unrelated_assets(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    reference: str,
) -> None:
    module = _load_hatch_build(monkeypatch)
    _seed_frontend_inputs(tmp_path)
    _write_coherent_bundle(module, tmp_path)
    static = tmp_path / "src" / "haute" / "static"
    index = static / "index.html"
    attribute = "href" if reference.endswith(".css") else "src"
    index.write_text(f'<script {attribute}="/{reference}"></script>', encoding="utf-8")
    (static / "assets" / "unrelated.js").write_text("x", encoding="utf-8")

    with pytest.raises(RuntimeError, match="missing|reference"):
        _hook(module, tmp_path).initialize("standard", {})


@pytest.mark.parametrize("edge_field", ["imports", "dynamicImports"])
def test_readiness_rejects_missing_transitive_and_dynamic_chunks(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    edge_field: str,
) -> None:
    module = _load_hatch_build(monkeypatch)
    _seed_frontend_inputs(tmp_path)
    _write_coherent_bundle(module, tmp_path)
    static = tmp_path / "src" / "haute" / "static"
    manifest_path = static / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["src/main.tsx"][edge_field] = ["src/missing.tsx"]
    manifest["src/missing.tsx"] = {"file": "assets/missing.js"}
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    with pytest.raises(RuntimeError, match="missing|manifest"):
        _hook(module, tmp_path).initialize("standard", {})


@pytest.mark.parametrize(
    "mutate",
    [
        lambda static: (static / "manifest.json").unlink(),
        lambda static: (static / "manifest.json").write_text("{", encoding="utf-8"),
        lambda static: (static / "manifest.json").write_text(
            '{"src/main.tsx":{"file":"../escape.js","isEntry":true}}',
            encoding="utf-8",
        ),
    ],
    ids=["missing-manifest", "malformed-manifest", "path-escape"],
)
def test_readiness_rejects_absent_malformed_or_escaping_output_manifest(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    mutate: Callable[[Path], object],
) -> None:
    module = _load_hatch_build(monkeypatch)
    _seed_frontend_inputs(tmp_path)
    static, _index = _write_coherent_bundle(module, tmp_path)
    mutate(static)

    with pytest.raises(RuntimeError, match="manifest|escape|outside"):
        _hook(module, tmp_path).initialize("standard", {})


def test_validation_accepts_a_coherent_output_graph_and_matching_fingerprint(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    module = _load_hatch_build(monkeypatch)
    _seed_frontend_inputs(tmp_path)
    _write_coherent_bundle(module, tmp_path)

    _hook(module, tmp_path).initialize("standard", {})


def test_readiness_ignores_navigation_links_that_are_not_asset_references(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    module = _load_hatch_build(monkeypatch)
    _seed_frontend_inputs(tmp_path)
    _write_coherent_bundle(module, tmp_path)
    index = tmp_path / "src" / "haute" / "static" / "index.html"
    index.write_text(
        index.read_text(encoding="utf-8") + '<a href="/pipelines/example">Open pipeline</a>',
        encoding="utf-8",
    )

    _hook(module, tmp_path).initialize("standard", {})


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


def test_vite_defines_app_version_from_package_metadata_without_fallback() -> None:
    root = Path(__file__).resolve().parents[1]
    source = (root / "frontend" / "vite.config.ts").read_text(encoding="utf-8")

    assert "../pyproject.toml" in source
    assert re.search(r"__APP_VERSION__\s*:\s*JSON\.stringify\(appVersion\)", source)
    assert "throw new Error" in source
    assert 'appVersion = versionMatch ? versionMatch[1] : "0.1.0"' not in source
