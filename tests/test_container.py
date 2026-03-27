"""Tests for the container deployment target (_container.py)."""

from __future__ import annotations

import ast
from dataclasses import field
from pathlib import Path

import pytest

from haute.graph_utils import GraphNode, NodeData, PipelineGraph
from haute.deploy._config import ContainerConfig, DeployConfig, ResolvedDeploy
from haute.deploy._container import (
    _ARTIFACT_EXT_TO_DEP,
    _detect_extra_deps,
    _generate_app_source,
    _generate_dockerfile,
)
from haute.deploy._utils import build_manifest as _build_manifest

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
    """
    return make_resolved_deploy(
        pipeline_file=Path("main.py"),
        model_name=model_name,
        target=target,
        container=container or ContainerConfig(),
        pruned_graph=PipelineGraph(nodes=[GraphNode(id="n1", data=NodeData(label="n1"))]),
        artifacts=artifacts or {},
        input_schema={"age": "int", "region": "str"},
        output_schema={"premium": "float"},
        removed_node_ids=["exposure"],
    )


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
