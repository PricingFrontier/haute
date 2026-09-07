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
            assert "RUN pip install --no-cache-dir haute==" in dockerfile_text
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
            assert "./haute-9.9.9-py3-none-any.whl" in dockerfile_text
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
