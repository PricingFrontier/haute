"""Tests for the container deployment target (_container.py)."""

from __future__ import annotations

import ast
import importlib.util
import json
import subprocess
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import polars as pl
import pytest
from fastapi.testclient import TestClient

from haute.deploy._config import ContainerConfig, DeployConfig, ResolvedDeploy
from haute.deploy._container import (
    DEFAULT_QUOTE_RESPONSE_ROW_LIMIT,
    ContainerBuildResult,
    _check_docker_available,
    _detect_extra_deps,
    _docker_push,
    _generate_app_source,
    _generate_dockerfile,
    _git_sha_short,
    _next_version,
    _update_service,
    _validate_base_image,
    _validate_model_name,
    build_and_push_image,
    deploy_to_container,
    deploy_to_platform_container,
)
from haute.deploy._mlflow import DeployResult
from haute.deploy._utils import build_manifest as _build_manifest
from haute.graph_utils import GraphNode, NodeData, PipelineGraph
from tests._deploy_helpers import make_resolved_deploy

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _make_resolved(
    artifacts: dict[str, Path] | None = None,
    container: ContainerConfig | None = None,
    model_name: str = "test-model",
    target: str = "container",
) -> ResolvedDeploy:
    """Build a minimal ResolvedDeploy for container unit tests.

    Delegates to the shared helper with container-specific defaults.
    ``ContainerConfig`` uses a patch-pinned base image so the post-init
    check in ``DeployConfig`` accepts the target-``container`` config;
    tests that exercise image validation itself pass an explicit
    ``container=ContainerConfig(base_image=...)`` override.
    """
    return make_resolved_deploy(
        pipeline_file=Path("main.py"),
        model_name=model_name,
        target=target,
        container=container or ContainerConfig(base_image="python:3.11.9-slim"),
        pruned_graph=PipelineGraph(nodes=[GraphNode(id="n1", data=NodeData(label="n1"))]),
        artifacts=artifacts or {},
        input_schema={"age": "int", "region": "str"},
        output_schema={"premium": "float"},
        removed_node_ids=["exposure"],
    )


def _load_generated_app(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    result_df: pl.DataFrame,
):
    manifest = {
        "pruned_graph": PipelineGraph(
            nodes=[GraphNode(id="quotes", data=NodeData(label="quotes"))],
            edges=[],
        ).model_dump(mode="json"),
        "input_node_ids": ["quotes"],
        "output_node_id": "quotes",
        "artifacts": {},
        "output_fields": None,
    }
    (tmp_path / "deploy_manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    app_path = tmp_path / "app.py"
    app_path.write_text(_generate_app_source("motor", 8080), encoding="utf-8")

    def fake_score_graph(**_kwargs):
        return result_df

    monkeypatch.setattr("haute.deploy._scorer.score_graph", fake_score_graph)
    module_name = f"_haute_generated_app_{tmp_path.name}"
    spec = importlib.util.spec_from_file_location(module_name, app_path)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    try:
        spec.loader.exec_module(module)
        return module
    finally:
        sys.modules.pop(module_name, None)


# ---------------------------------------------------------------------------
# Validation - base image
# ---------------------------------------------------------------------------


class TestValidateBaseImage:
    @pytest.mark.parametrize(
        "image",
        [
            "python:3.11-slim",
            "docker.io/myrepo/image:v1",
            "ghcr.io/org/img:latest",
            "registry.example.com:5000/app:1.0",
            "ubuntu",
            "nvidia/cuda:12.0-base",
        ],
    )
    def test_valid_images_accepted(self, image: str) -> None:
        _validate_base_image(image)

    @pytest.mark.parametrize(
        "image",
        [
            "python:3.11 slim",
            "image\ninjection",
            "img|cat",
            "img'quote",
            'img"double',
            "img;rm -rf /",
            "img`whoami`",
            "img$HOME",
            "img&bg",
        ],
    )
    def test_rejects_shell_metacharacters(self, image: str) -> None:
        with pytest.raises(ValueError, match="Invalid base_image"):
            _validate_base_image(image)

    def test_rejects_empty_string(self) -> None:
        with pytest.raises(ValueError, match="Invalid base_image"):
            _validate_base_image("")

    def test_rejects_very_long_image_name(self) -> None:
        long_name = "a" * 10_000
        _validate_base_image(long_name)

    def test_rejects_whitespace_only(self) -> None:
        with pytest.raises(ValueError, match="Invalid base_image"):
            _validate_base_image("   ")


# ---------------------------------------------------------------------------
# Validation - model name
# ---------------------------------------------------------------------------


class TestValidateModelName:
    @pytest.mark.parametrize(
        "name",
        [
            "motor",
            "motor-pricing",
            "pricing_v1",
            "model123",
            "A-B-C",
        ],
    )
    def test_valid_names_accepted(self, name: str) -> None:
        _validate_model_name(name)

    @pytest.mark.parametrize(
        "name",
        [
            "has space",
            "has.dot",
            "has/slash",
            "has\\backslash",
            "has@at",
            "has!bang",
            "has$dollar",
            "has;semi",
        ],
    )
    def test_rejects_special_characters(self, name: str) -> None:
        with pytest.raises(ValueError, match="Invalid model_name"):
            _validate_model_name(name)

    def test_rejects_empty_string(self) -> None:
        with pytest.raises(ValueError, match="Invalid model_name"):
            _validate_model_name("")

    def test_rejects_whitespace_only(self) -> None:
        with pytest.raises(ValueError, match="Invalid model_name"):
            _validate_model_name("  ")


# ---------------------------------------------------------------------------
# App generation
# ---------------------------------------------------------------------------


class TestGenerateAppSource:
    def test_produces_valid_python(self) -> None:
        source = _generate_app_source("motor", 8080)
        ast.parse(source)

    def test_contains_health_endpoint(self) -> None:
        source = _generate_app_source("motor", 8080)
        assert "/health" in source

    def test_contains_quote_endpoint(self) -> None:
        source = _generate_app_source("motor", 8080)
        assert "/quote" in source

    def test_embeds_model_name(self) -> None:
        source = _generate_app_source("motor-pricing", 9000)
        assert "motor-pricing" in source

    def test_imports_score_graph(self) -> None:
        source = _generate_app_source("m", 8080)
        assert "from haute.deploy._scorer import score_graph" in source

    @pytest.mark.parametrize(
        "row_count",
        [
            1,
            DEFAULT_QUOTE_RESPONSE_ROW_LIMIT - 1,
            DEFAULT_QUOTE_RESPONSE_ROW_LIMIT,
            DEFAULT_QUOTE_RESPONSE_ROW_LIMIT + 1,
            10 * DEFAULT_QUOTE_RESPONSE_ROW_LIMIT,
        ],
    )
    def test_quote_response_uses_stable_envelope_at_limit_boundaries(
        self,
        row_count: int,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        module = _load_generated_app(
            tmp_path,
            monkeypatch,
            pl.DataFrame({"premium": list(range(row_count))}),
        )
        original_to_dicts = pl.DataFrame.to_dicts
        serialized_heights: list[int] = []

        def guarded_to_dicts(self: pl.DataFrame, *args, **kwargs):
            serialized_heights.append(self.height)
            if self.height > DEFAULT_QUOTE_RESPONSE_ROW_LIMIT:
                raise AssertionError("quote response serialized more rows than the limit")
            return original_to_dicts(self, *args, **kwargs)

        monkeypatch.setattr(pl.DataFrame, "to_dicts", guarded_to_dicts)

        response = TestClient(module.app).post("/quote", json=[{"age": 30}])

        assert response.status_code == 200
        body = response.json()
        expected_returned_rows = min(row_count, DEFAULT_QUOTE_RESPONSE_ROW_LIMIT)
        assert set(body) == {"rows", "row_count", "returned_rows", "truncated", "limit"}
        assert body["row_count"] == row_count
        assert body["returned_rows"] == expected_returned_rows
        assert body["limit"] == DEFAULT_QUOTE_RESPONSE_ROW_LIMIT
        assert body["truncated"] is (row_count > DEFAULT_QUOTE_RESPONSE_ROW_LIMIT)
        assert len(body["rows"]) == expected_returned_rows
        assert body["rows"][0] == {"premium": 0}
        assert body["rows"][-1] == {"premium": expected_returned_rows - 1}
        assert serialized_heights == [expected_returned_rows]

    def test_quote_response_uses_stable_empty_envelope_for_zero_rows(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        module = _load_generated_app(
            tmp_path,
            monkeypatch,
            pl.DataFrame({"premium": pl.Series([], dtype=pl.Int64)}),
        )

        response = TestClient(module.app).post("/quote", json=[])

        assert response.status_code == 200
        assert response.json() == {
            "rows": [],
            "row_count": 0,
            "returned_rows": 0,
            "truncated": False,
            "limit": DEFAULT_QUOTE_RESPONSE_ROW_LIMIT,
        }


# ---------------------------------------------------------------------------
# Dockerfile generation
# ---------------------------------------------------------------------------


class TestGenerateDockerfile:
    def test_default_base_image(self) -> None:
        resolved = _make_resolved()
        df = _generate_dockerfile("python:3.11-slim", 8080, resolved)
        assert df.startswith("FROM python:3.11-slim")

    def test_custom_port(self) -> None:
        resolved = _make_resolved()
        df = _generate_dockerfile("python:3.11-slim", 9090, resolved)
        assert "EXPOSE 9090" in df
        assert '"9090"' in df

    def test_includes_base_deps(self) -> None:
        resolved = _make_resolved()
        df = _generate_dockerfile("python:3.11-slim", 8080, resolved)
        for dep in ("haute", "polars", "fastapi", "uvicorn[standard]"):
            assert dep in df

    def test_includes_catboost_for_cbm(self) -> None:
        resolved = _make_resolved(artifacts={"freq.cbm": Path("models/freq.cbm")})
        df = _generate_dockerfile("python:3.11-slim", 8080, resolved)
        assert "catboost" in df

    def test_includes_sklearn_for_pkl(self) -> None:
        resolved = _make_resolved(artifacts={"model.pkl": Path("models/model.pkl")})
        df = _generate_dockerfile("python:3.11-slim", 8080, resolved)
        assert "scikit-learn" in df


# ---------------------------------------------------------------------------
# Dependency detection
# ---------------------------------------------------------------------------


class TestDetectExtraDeps:
    def test_empty_artifacts(self) -> None:
        resolved = _make_resolved(artifacts={})
        assert _detect_extra_deps(resolved) == []

    def test_cbm_maps_to_catboost(self) -> None:
        resolved = _make_resolved(artifacts={"m.cbm": Path("m.cbm")})
        assert _detect_extra_deps(resolved) == ["catboost"]

    def test_pkl_maps_to_sklearn(self) -> None:
        resolved = _make_resolved(artifacts={"m.pkl": Path("m.pkl")})
        assert _detect_extra_deps(resolved) == ["scikit-learn"]

    def test_pickle_maps_to_sklearn(self) -> None:
        resolved = _make_resolved(artifacts={"m.pickle": Path("m.pickle")})
        assert _detect_extra_deps(resolved) == ["scikit-learn"]

    def test_lgb_maps_to_lightgbm(self) -> None:
        resolved = _make_resolved(artifacts={"m.lgb": Path("m.lgb")})
        assert _detect_extra_deps(resolved) == ["lightgbm"]

    def test_xgb_maps_to_xgboost(self) -> None:
        resolved = _make_resolved(artifacts={"m.xgb": Path("m.xgb")})
        assert _detect_extra_deps(resolved) == ["xgboost"]

    def test_onnx_maps_to_onnxruntime(self) -> None:
        resolved = _make_resolved(artifacts={"m.onnx": Path("m.onnx")})
        assert _detect_extra_deps(resolved) == ["onnxruntime"]

    def test_txt_does_not_match(self) -> None:
        resolved = _make_resolved(artifacts={"readme.txt": Path("readme.txt")})
        assert _detect_extra_deps(resolved) == []

    def test_json_does_not_match(self) -> None:
        resolved = _make_resolved(artifacts={"config.json": Path("config.json")})
        assert _detect_extra_deps(resolved) == []

    def test_multiple_artifacts_deduped_and_sorted(self) -> None:
        resolved = _make_resolved(
            artifacts={
                "freq.cbm": Path("freq.cbm"),
                "sev.cbm": Path("sev.cbm"),
                "scaler.pkl": Path("scaler.pkl"),
            }
        )
        assert _detect_extra_deps(resolved) == ["catboost", "scikit-learn"]

    def test_case_insensitive(self) -> None:
        resolved = _make_resolved(artifacts={"Model.CBM": Path("Model.CBM")})
        assert _detect_extra_deps(resolved) == ["catboost"]


# ---------------------------------------------------------------------------
# Manifest
# ---------------------------------------------------------------------------


class TestBuildManifest:
    def test_required_keys_present(self) -> None:
        resolved = _make_resolved()
        m = _build_manifest(resolved)
        required = {
            "haute_version",
            "pipeline_name",
            "target",
            "created_at",
            "created_by",
            "input_node_ids",
            "output_node_id",
            "input_schema",
            "output_schema",
            "artifacts",
            "pruned_graph",
            "nodes_deployed",
            "nodes_skipped",
        }
        assert required.issubset(m.keys())

    def test_pipeline_name_matches_config(self) -> None:
        resolved = _make_resolved(model_name="motor-pricing")
        m = _build_manifest(resolved)
        assert m["pipeline_name"] == "motor-pricing"

    def test_target_is_container(self) -> None:
        resolved = _make_resolved()
        m = _build_manifest(resolved)
        assert m["target"] == "container"

    def test_nodes_deployed_count(self) -> None:
        resolved = _make_resolved()
        m = _build_manifest(resolved)
        assert m["nodes_deployed"] == 1

    def test_nodes_skipped_count(self) -> None:
        resolved = _make_resolved()
        m = _build_manifest(resolved)
        assert m["nodes_skipped"] == 1

    def test_schemas_included(self) -> None:
        resolved = _make_resolved()
        m = _build_manifest(resolved)
        assert m["input_schema"] == {"age": "int", "region": "str"}
        assert m["output_schema"] == {"premium": "float"}


# ---------------------------------------------------------------------------
# Deploy dispatch
# ---------------------------------------------------------------------------


class TestDeployDispatch:
    def test_unknown_target_raises_value_error(self) -> None:
        from haute.deploy import deploy

        config = DeployConfig(
            pipeline_file=Path("main.py"),
            model_name="test",
            target="foobar",
        )
        with pytest.raises(ValueError, match="[Uu]nknown.*target"):
            deploy(config)

    @pytest.mark.parametrize("target", ["sagemaker", "azure-ml"])
    def test_planned_target_raises_not_implemented(self, target: str) -> None:
        from haute.deploy import deploy

        config = DeployConfig(
            pipeline_file=Path("main.py"),
            model_name="test",
            target=target,
        )
        with pytest.raises(NotImplementedError, match="planned.*not yet"):
            deploy(config)


# ---------------------------------------------------------------------------
# _docker_push
# ---------------------------------------------------------------------------


class TestDockerPush:
    def test_success_when_returncode_zero(self) -> None:
        with patch("haute.deploy._container.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stderr="")
            _docker_push("myregistry/model:abc1234")

        mock_run.assert_called_once_with(
            ["docker", "push", "myregistry/model:abc1234"],
            capture_output=True,
            text=True,
        )

    def test_raises_runtime_error_on_failure(self) -> None:
        with patch("haute.deploy._container.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(
                returncode=1,
                stderr="denied: requested access to the resource is denied",
            )
            with pytest.raises(RuntimeError, match="Docker push failed") as exc_info:
                _docker_push("myregistry/model:abc1234")
            assert "denied" in str(exc_info.value)


# ---------------------------------------------------------------------------
# _next_version
# ---------------------------------------------------------------------------


class TestNextVersion:
    def test_returns_one(self) -> None:
        assert _next_version() == 1


# ---------------------------------------------------------------------------
# _check_docker_available (additional: FileNotFoundError path)
# ---------------------------------------------------------------------------


class TestCheckDockerAvailableExtra:
    def test_raises_runtime_error_on_file_not_found(self) -> None:
        with patch("haute.deploy._container.subprocess.run") as mock_run:
            mock_run.side_effect = FileNotFoundError("docker not found")
            with pytest.raises(RuntimeError, match="Docker is not available"):
                _check_docker_available()


# ---------------------------------------------------------------------------
# _git_sha_short (additional: CalledProcessError path)
# ---------------------------------------------------------------------------


class TestGitShaShortExtra:
    def test_returns_local_on_called_process_error(self) -> None:
        with patch("haute.deploy._container.subprocess.run") as mock_run:
            mock_run.side_effect = subprocess.CalledProcessError(1, "git")
            assert _git_sha_short() == "local"


# ---------------------------------------------------------------------------
# _update_service
# ---------------------------------------------------------------------------


class TestUpdateService:
    @pytest.mark.parametrize(
        "target",
        ["azure-container-apps", "aws-ecs", "gcp-run", "container"],
    )
    def test_raises_not_implemented_for_all_targets(self, target: str) -> None:
        resolved = _make_resolved(target=target)
        with pytest.raises(NotImplementedError, match="not yet implemented"):
            _update_service(target, "img:tag", resolved)

    def test_error_message_contains_target_and_image(self) -> None:
        with pytest.raises(NotImplementedError) as exc_info:
            _update_service("gcp-run", "myregistry/model:v1", MagicMock())
        msg = str(exc_info.value)
        assert "gcp-run" in msg
        assert "myregistry/model:v1" in msg


# ---------------------------------------------------------------------------
# build_and_push_image
# ---------------------------------------------------------------------------


class TestBuildAndPushImage:
    """Tests for the full build_and_push_image flow with mocked externals.

    We also mock _generate_app_source and _generate_dockerfile because the
    generated source contains unicode (checkmark chars) which cannot be
    written via Path.write_text on Windows cp1252.  Those generators have
    their own dedicated test classes above.
    """

    _COMMON_PATCHES = [
        "_docker_push",
        "_docker_build",
        "_check_docker_available",
        "_generate_app_source",
        "_generate_dockerfile",
    ]

    def _make_resolved_with_artifacts(
        self,
        tmp_path: Path,
        registry: str = "",
    ) -> ResolvedDeploy:
        """Create a resolved deploy with real artifact files in tmp_path."""
        artifact_file = tmp_path / "model.cbm"
        artifact_file.write_text("fake model data")
        return _make_resolved(
            artifacts={"model.cbm": artifact_file},
            container=ContainerConfig(
                registry=registry,
                port=8080,
                base_image="python:3.11.9-slim",
            ),
        )

    @patch("haute.deploy._container._generate_dockerfile", return_value="FROM python:3.11-slim\n")
    @patch("haute.deploy._container._generate_app_source", return_value="# app\n")
    @patch("haute.deploy._container._docker_push")
    @patch("haute.deploy._container._docker_build")
    @patch("haute.deploy._container._check_docker_available")
    @patch("haute.deploy._container._git_sha_short", return_value="abc1234")
    @patch("haute.deploy._container._next_version", return_value=1)
    def test_full_build_no_registry(
        self,
        mock_version: MagicMock,
        mock_sha: MagicMock,
        mock_check: MagicMock,
        mock_build: MagicMock,
        mock_push: MagicMock,
        mock_app: MagicMock,
        mock_df: MagicMock,
        tmp_path: Path,
    ) -> None:
        """Without registry: builds locally, does not push."""
        resolved = self._make_resolved_with_artifacts(tmp_path, registry="")

        with patch("haute.deploy._container.Path.cwd", return_value=tmp_path):
            result = build_and_push_image(resolved)

        assert isinstance(result, ContainerBuildResult)
        assert result.image_tag == "test-model:abc1234"
        assert result.model_name == "test-model"
        assert result.model_version == 1
        assert result.manifest_path.exists()
        mock_build.assert_called_once()
        mock_push.assert_not_called()

    @patch("haute.deploy._container._generate_dockerfile", return_value="FROM python:3.11-slim\n")
    @patch("haute.deploy._container._generate_app_source", return_value="# app\n")
    @patch("haute.deploy._container._docker_push")
    @patch("haute.deploy._container._docker_build")
    @patch("haute.deploy._container._check_docker_available")
    @patch("haute.deploy._container._git_sha_short", return_value="def5678")
    @patch("haute.deploy._container._next_version", return_value=1)
    def test_full_build_with_registry(
        self,
        mock_version: MagicMock,
        mock_sha: MagicMock,
        mock_check: MagicMock,
        mock_build: MagicMock,
        mock_push: MagicMock,
        mock_app: MagicMock,
        mock_df: MagicMock,
        tmp_path: Path,
    ) -> None:
        """With registry: builds and pushes."""
        resolved = self._make_resolved_with_artifacts(tmp_path, registry="myregistry.io/models")

        with patch("haute.deploy._container.Path.cwd", return_value=tmp_path):
            result = build_and_push_image(resolved)

        assert result.image_tag == "myregistry.io/models/test-model:def5678"
        mock_push.assert_called_once_with("myregistry.io/models/test-model:def5678")

    @patch("haute.deploy._container._generate_dockerfile", return_value="FROM python:3.11-slim\n")
    @patch("haute.deploy._container._generate_app_source", return_value="# app\n")
    @patch("haute.deploy._container._docker_push")
    @patch("haute.deploy._container._docker_build")
    @patch("haute.deploy._container._check_docker_available")
    @patch("haute.deploy._container._git_sha_short", return_value="abc1234")
    @patch("haute.deploy._container._next_version", return_value=1)
    def test_artifacts_copied_to_build_dir(
        self,
        mock_version: MagicMock,
        mock_sha: MagicMock,
        mock_check: MagicMock,
        mock_build: MagicMock,
        mock_push: MagicMock,
        mock_app: MagicMock,
        mock_df: MagicMock,
        tmp_path: Path,
    ) -> None:
        """Artifact files are copied into .haute_build/artifacts/."""
        resolved = self._make_resolved_with_artifacts(tmp_path)

        with patch("haute.deploy._container.Path.cwd", return_value=tmp_path):
            result = build_and_push_image(resolved)

        artifact_dest = result.build_dir / "artifacts" / "model.cbm"
        assert artifact_dest.exists()
        assert artifact_dest.read_text() == "fake model data"

    @patch("haute.deploy._container._generate_dockerfile", return_value="FROM python:3.11-slim\n")
    @patch("haute.deploy._container._generate_app_source", return_value="# app\n")
    @patch("haute.deploy._container._docker_push")
    @patch("haute.deploy._container._docker_build")
    @patch("haute.deploy._container._check_docker_available")
    @patch("haute.deploy._container._git_sha_short", return_value="abc1234")
    @patch("haute.deploy._container._next_version", return_value=1)
    def test_dockerfile_and_app_generated(
        self,
        mock_version: MagicMock,
        mock_sha: MagicMock,
        mock_check: MagicMock,
        mock_build: MagicMock,
        mock_push: MagicMock,
        mock_app: MagicMock,
        mock_df: MagicMock,
        tmp_path: Path,
    ) -> None:
        """Dockerfile and app.py are generated in the build directory."""
        resolved = self._make_resolved_with_artifacts(tmp_path)

        with patch("haute.deploy._container.Path.cwd", return_value=tmp_path):
            result = build_and_push_image(resolved)

        assert (result.build_dir / "Dockerfile").exists()
        assert (result.build_dir / "app.py").exists()
        assert (result.build_dir / "deploy_manifest.json").exists()

    @patch("haute.deploy._container._generate_dockerfile", return_value="FROM python:3.11-slim\n")
    @patch("haute.deploy._container._generate_app_source", return_value="# app\n")
    @patch("haute.deploy._container._docker_build")
    @patch("haute.deploy._container._check_docker_available")
    @patch("haute.deploy._container._git_sha_short", return_value="abc1234")
    @patch("haute.deploy._container._next_version", return_value=1)
    def test_cleanup_on_exception(
        self,
        mock_version: MagicMock,
        mock_sha: MagicMock,
        mock_check: MagicMock,
        mock_build: MagicMock,
        mock_app: MagicMock,
        mock_df: MagicMock,
        tmp_path: Path,
    ) -> None:
        """Build directory is removed when an exception occurs."""
        mock_build.side_effect = RuntimeError("docker build failed")
        resolved = self._make_resolved_with_artifacts(tmp_path)

        with patch("haute.deploy._container.Path.cwd", return_value=tmp_path):
            with pytest.raises(RuntimeError, match="docker build failed"):
                build_and_push_image(resolved)

        build_dir = tmp_path / ".haute_build"
        assert not build_dir.exists()

    @patch("haute.deploy._container._generate_dockerfile", return_value="FROM python:3.11-slim\n")
    @patch("haute.deploy._container._generate_app_source", return_value="# app\n")
    @patch("haute.deploy._container._docker_push")
    @patch("haute.deploy._container._docker_build")
    @patch("haute.deploy._container._check_docker_available")
    @patch("haute.deploy._container._git_sha_short", return_value="abc1234")
    @patch("haute.deploy._container._next_version", return_value=1)
    def test_progress_callback_called(
        self,
        mock_version: MagicMock,
        mock_sha: MagicMock,
        mock_check: MagicMock,
        mock_build: MagicMock,
        mock_push: MagicMock,
        mock_app: MagicMock,
        mock_df: MagicMock,
        tmp_path: Path,
    ) -> None:
        """Progress callback receives step messages."""
        resolved = self._make_resolved_with_artifacts(tmp_path)
        messages: list[str] = []

        with patch("haute.deploy._container.Path.cwd", return_value=tmp_path):
            build_and_push_image(resolved, progress=messages.append)

        assert any("manifest" in m.lower() for m in messages)
        assert any("docker image" in m.lower() for m in messages)

    @patch("haute.deploy._container._generate_dockerfile", return_value="FROM python:3.11-slim\n")
    @patch("haute.deploy._container._generate_app_source", return_value="# app\n")
    @patch("haute.deploy._container._docker_push")
    @patch("haute.deploy._container._docker_build")
    @patch("haute.deploy._container._check_docker_available")
    @patch("haute.deploy._container._git_sha_short", return_value="abc1234")
    @patch("haute.deploy._container._next_version", return_value=1)
    def test_registry_trailing_slash_stripped(
        self,
        mock_version: MagicMock,
        mock_sha: MagicMock,
        mock_check: MagicMock,
        mock_build: MagicMock,
        mock_push: MagicMock,
        mock_app: MagicMock,
        mock_df: MagicMock,
        tmp_path: Path,
    ) -> None:
        """Trailing slash on registry is stripped from image tag."""
        resolved = self._make_resolved_with_artifacts(tmp_path, registry="myregistry.io/")

        with patch("haute.deploy._container.Path.cwd", return_value=tmp_path):
            result = build_and_push_image(resolved)

        assert result.image_tag == "myregistry.io/test-model:abc1234"


# ---------------------------------------------------------------------------
# deploy_to_container
# ---------------------------------------------------------------------------


class TestDeployToContainer:
    """Tests for deploy_to_container()."""

    @patch("haute.deploy._container.build_and_push_image")
    def test_returns_deploy_result_with_correct_fields(
        self, mock_build: MagicMock, tmp_path: Path
    ) -> None:
        mock_build.return_value = ContainerBuildResult(
            image_tag="test-model:abc1234",
            manifest_path=tmp_path / "deploy_manifest.json",
            build_dir=tmp_path,
            model_name="test-model",
            model_version=1,
        )
        resolved = _make_resolved()
        result = deploy_to_container(resolved)

        assert isinstance(result, DeployResult)
        assert result.model_name == "test-model"
        assert result.model_version == 1
        assert result.model_uri == "test-model:abc1234"
        assert result.endpoint_url is None
        assert result.manifest_path == tmp_path / "deploy_manifest.json"

    @patch("haute.deploy._container.build_and_push_image")
    def test_passes_progress_to_build(self, mock_build: MagicMock) -> None:
        mock_build.return_value = ContainerBuildResult(
            image_tag="m:v",
            manifest_path=Path("m.json"),
            build_dir=Path("."),
            model_name="m",
            model_version=1,
        )
        progress_fn = MagicMock()
        deploy_to_container(_make_resolved(), progress=progress_fn)
        mock_build.assert_called_once_with(_make_resolved(), progress_fn)


# ---------------------------------------------------------------------------
# deploy_to_platform_container
# ---------------------------------------------------------------------------


class TestDeployToPlatformContainer:
    """Tests for deploy_to_platform_container()."""

    @patch("haute.deploy._container._update_service")
    @patch("haute.deploy._container.build_and_push_image")
    def test_calls_update_service(
        self, mock_build: MagicMock, mock_update: MagicMock, tmp_path: Path
    ) -> None:
        mock_build.return_value = ContainerBuildResult(
            image_tag="registry/model:abc",
            manifest_path=tmp_path / "manifest.json",
            build_dir=tmp_path,
            model_name="test-model",
            model_version=1,
        )
        mock_update.return_value = "https://my-service.example.com"

        resolved = _make_resolved(target="azure-container-apps")
        result = deploy_to_platform_container(resolved)

        assert isinstance(result, DeployResult)
        assert result.endpoint_url == "https://my-service.example.com"
        assert result.model_uri == "registry/model:abc"
        mock_update.assert_called_once_with("azure-container-apps", "registry/model:abc", resolved)

    @patch("haute.deploy._container._update_service")
    @patch("haute.deploy._container.build_and_push_image")
    def test_handles_not_implemented_error(
        self, mock_build: MagicMock, mock_update: MagicMock, tmp_path: Path
    ) -> None:
        mock_build.return_value = ContainerBuildResult(
            image_tag="registry/model:abc",
            manifest_path=tmp_path / "manifest.json",
            build_dir=tmp_path,
            model_name="test-model",
            model_version=1,
        )
        mock_update.side_effect = NotImplementedError("not yet implemented")

        resolved = _make_resolved(target="aws-ecs")
        with pytest.raises(NotImplementedError, match="not yet implemented"):
            deploy_to_platform_container(resolved)

    @patch("haute.deploy._container._update_service")
    @patch("haute.deploy._container.build_and_push_image")
    def test_progress_callback_reports_service_update(
        self, mock_build: MagicMock, mock_update: MagicMock, tmp_path: Path
    ) -> None:
        mock_build.return_value = ContainerBuildResult(
            image_tag="registry/model:abc",
            manifest_path=tmp_path / "manifest.json",
            build_dir=tmp_path,
            model_name="test-model",
            model_version=1,
        )
        mock_update.return_value = None

        messages: list[str] = []
        resolved = _make_resolved(target="gcp-run")
        result = deploy_to_platform_container(resolved, progress=messages.append)

        assert any("gcp-run" in m.lower() for m in messages)
        assert result.endpoint_url is None

    @patch("haute.deploy._container._update_service")
    @patch("haute.deploy._container.build_and_push_image")
    def test_no_url_returned_message(
        self, mock_build: MagicMock, mock_update: MagicMock, tmp_path: Path
    ) -> None:
        """When _update_service returns None, progress shows '(no URL returned)'."""
        mock_build.return_value = ContainerBuildResult(
            image_tag="registry/model:abc",
            manifest_path=tmp_path / "manifest.json",
            build_dir=tmp_path,
            model_name="test-model",
            model_version=1,
        )
        mock_update.return_value = None

        messages: list[str] = []
        resolved = _make_resolved(target="gcp-run")
        deploy_to_platform_container(resolved, progress=messages.append)

        assert any("no URL returned" in m for m in messages)
