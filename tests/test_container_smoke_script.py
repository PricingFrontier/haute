"""Tests for scripts/container_smoke.py and prepare_build_directory seam."""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

from haute.deploy import DeployConfig, resolve_config
from haute.deploy._container import build_and_push_image, prepare_build_directory
from haute.errors import DeployError
from scripts.container_smoke import run_smoke

# Container builds pin price-contour from the package index; see the fixture.
pytestmark = pytest.mark.usefixtures("released_price_contour")


def _resolve_minimal_live_quote(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.chdir(tmp_path)
    repo_root = Path(__file__).resolve().parents[1]
    example_dir = (
        repo_root / "src" / "haute" / "assistant" / "assets" / "examples" / "minimal_live_quote"
    )
    proj_dir = tmp_path / "project"
    shutil.copytree(example_dir, proj_dir)

    toml_path = proj_dir / "haute.toml"
    original_toml = toml_path.read_text(encoding="utf-8")
    container_section = (
        "\n[deploy]\n"
        'target = "container"\n\n'
        "[deploy.container]\n"
        'base_image = "python:3.11.9-slim"\n'
    )
    toml_path.write_text(original_toml + container_section, encoding="utf-8")

    config = DeployConfig.from_toml(toml_path)
    return resolve_config(config)


class TestPrepareBuildDirectory:
    def test_writes_four_artefacts_and_pinned_pip_line(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        resolved = _resolve_minimal_live_quote(tmp_path, monkeypatch)
        try:
            build_dir = tmp_path / "image"
            manifest_path = prepare_build_directory(resolved, build_dir)

            assert manifest_path == build_dir / "deploy_manifest.json"
            assert manifest_path.is_file()
            assert (build_dir / "app.py").is_file()
            assert (build_dir / "Dockerfile").is_file()
            assert (build_dir / "artifacts").is_dir()

            dockerfile_text = (build_dir / "Dockerfile").read_text(encoding="utf-8")
            assert "RUN pip install --no-cache-dir --no-deps haute==" in dockerfile_text
            assert "polars==" in dockerfile_text
            assert "fastapi==" in dockerfile_text
            assert "uvicorn[standard]==" in dockerfile_text
            assert ".whl" not in dockerfile_text
        finally:
            resolved.close()

    def test_custom_wheel_requirement_copies_wheel_and_updates_dockerfile(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        resolved = _resolve_minimal_live_quote(tmp_path, monkeypatch)
        try:
            fake_wheel = tmp_path / "haute-9.9.9-py3-none-any.whl"
            fake_wheel.write_bytes(b"PK\x03\x04fake_wheel_content")

            build_dir = tmp_path / "image_wheel"
            manifest_path = prepare_build_directory(
                resolved,
                build_dir,
                haute_requirement=str(fake_wheel),
            )

            assert (build_dir / "haute-9.9.9-py3-none-any.whl").is_file()
            dockerfile_text = (build_dir / "Dockerfile").read_text(encoding="utf-8")
            assert "COPY haute-9.9.9-py3-none-any.whl ." in dockerfile_text
            assert "--no-deps ./haute-9.9.9-py3-none-any.whl" in dockerfile_text
            assert "haute==" not in dockerfile_text

            manifest_data = json.loads(manifest_path.read_text(encoding="utf-8"))
            assert "./haute-9.9.9-py3-none-any.whl" in manifest_data.get(
                "container_dependencies", []
            )
        finally:
            resolved.close()


class TestBuildAndPushImageCleanup:
    def test_cleans_build_dir_on_docker_failure(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        resolved = _resolve_minimal_live_quote(tmp_path, monkeypatch)
        try:
            with (
                patch("haute.deploy._container.Path.cwd", return_value=tmp_path),
                patch("haute.deploy._container._check_docker_available"),
                patch("haute.deploy._container._git_sha_short", return_value="abc1234"),
                patch(
                    "haute.deploy._container._docker_build",
                    side_effect=DeployError("Docker build failed: simulated failure"),
                ),
            ):
                with pytest.raises(DeployError, match="Docker build failed"):
                    build_and_push_image(resolved)

            build_dir = tmp_path / ".haute_build"
            assert not build_dir.exists()
        finally:
            resolved.close()


class TestContainerSmokeServeCheck:
    def test_prepare_only_without_serve_check(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.chdir(tmp_path)
        build_dir = tmp_path / "prep_only"
        code = run_smoke(
            build_dir=build_dir,
            example="minimal_live_quote",
            serve_check=False,
        )
        assert code == 0
        assert (build_dir / "image" / "deploy_manifest.json").is_file()
        assert (build_dir / "image" / "app.py").is_file()
        assert (build_dir / "image" / "Dockerfile").is_file()

    @pytest.mark.timeout(180)
    def test_cli_serve_check_boots_the_generated_app_and_scores_the_golden_request(
        self, tmp_path: Path
    ) -> None:
        build_dir = tmp_path / "cli_subproc"
        repo_root = Path(__file__).resolve().parents[1]
        script_path = repo_root / "scripts" / "container_smoke.py"
        result = subprocess.run(
            [
                sys.executable,
                str(script_path),
                "--build-dir",
                str(build_dir),
                "--serve-check",
                "--port",
                "0",
            ],
            cwd=tmp_path,
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0, f"STDOUT:\n{result.stdout}\nSTDERR:\n{result.stderr}"
        assert "GET /health response:" in result.stdout
        assert "POST /quote response:" in result.stdout
        assert (build_dir / "image" / "deploy_manifest.json").is_file()
        assert (build_dir / "uvicorn.log").is_file()
        quote = json.loads(result.stdout.split("POST /quote response:", 1)[1])
        assert quote["row_count"] == 1
        assert quote["rows"] == [{"fixture_value": 10}]


_SLIM_APP_CHILD = """
import importlib.abc, json, sys

blocked = set(json.loads(sys.argv[2]))


class _NotInTheImage(importlib.abc.MetaPathFinder):
    def find_spec(self, name, path=None, target=None):
        if name.split(".")[0] in blocked:
            raise ModuleNotFoundError(f"{name} is not installed in the image", name=name)
        return None


sys.meta_path.insert(0, _NotInTheImage())
sys.path.insert(0, ".")
from fastapi.testclient import TestClient

import app

with open(sys.argv[1], encoding="utf-8") as handle:
    request = json.load(handle)
response = TestClient(app.app).post("/quote", json=request)
print(json.dumps({"status": response.status_code, "body": response.json()}))
"""


def _requirement_closure(requirements: list[str]) -> set[str]:
    """Canonical names of *requirements* and everything they install, from metadata."""
    from importlib.metadata import PackageNotFoundError
    from importlib.metadata import requires as dist_requires

    from packaging.requirements import Requirement
    from packaging.utils import canonicalize_name

    seen: set[tuple[str, frozenset[str]]] = set()
    names: set[str] = set()
    stack = [Requirement(text) for text in requirements]
    while stack:
        requirement = stack.pop()
        key = (canonicalize_name(requirement.name), frozenset(requirement.extras))
        if key in seen:
            continue
        seen.add(key)
        names.add(key[0])
        try:
            children = dist_requires(requirement.name) or []
        except PackageNotFoundError:
            continue  # a platform-marked requirement not installed here
        for text in children:
            child = Requirement(text)
            extras = requirement.extras or {""}
            if child.marker is None or any(
                child.marker.evaluate({"extra": extra}) for extra in extras
            ):
                stack.append(child)
    return names


def _modules_outside_the_image(runtime_requirements: list[str]) -> list[str]:
    """Import names that haute's dependencies provide and the image does not install."""
    from importlib.metadata import packages_distributions
    from importlib.metadata import requires as dist_requires

    from packaging.utils import canonicalize_name

    # The in-process test client (httpx) is the harness, not the image.
    installed = _requirement_closure([*runtime_requirements, "httpx"])
    haute_environment = _requirement_closure(
        [text for text in dist_requires("haute") or [] if "extra ==" not in text]
    )
    outside = haute_environment - installed
    return sorted(
        module
        for module, distributions in packages_distributions().items()
        if {canonicalize_name(name) for name in distributions} & outside
        and not {canonicalize_name(name) for name in distributions} & installed
    )


class TestScoringRuntime:
    def test_generated_app_scores_with_only_the_scoring_runtime_installed(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """DEP-R02: the image installs haute --no-deps plus the pinned scoring runtime.

        Serve the smoke example's golden request in a fresh interpreter in which
        every haute dependency the Dockerfile does not install — and nothing the
        runtime pulls in — cannot be imported, so an import the slim image lacks
        fails here instead of in the weekly container build.
        """
        resolved = _resolve_minimal_live_quote(tmp_path, monkeypatch)
        try:
            build_dir = tmp_path / "image"
            manifest_path = prepare_build_directory(resolved, build_dir)
        finally:
            resolved.close()
        runtime = json.loads(manifest_path.read_text(encoding="utf-8"))["container_dependencies"][
            1:
        ]
        blocked = _modules_outside_the_image(runtime)
        assert {"anthropic", "openai", "optuna", "libcst", "mlflow"} <= set(blocked)

        child = tmp_path / "serve_one_quote.py"
        child.write_text(_SLIM_APP_CHILD, encoding="utf-8")
        request = tmp_path / "project" / "golden_request.json"
        completed = subprocess.run(
            [sys.executable, str(child), str(request), json.dumps(blocked)],
            cwd=build_dir,
            capture_output=True,
            text=True,
            encoding="utf-8",
            check=False,
        )

        assert completed.returncode == 0, completed.stderr[-4000:]
        result = json.loads(completed.stdout.strip().splitlines()[-1])
        assert result["status"] == 200, result
        assert result["body"]["row_count"] >= 1
