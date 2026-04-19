"""Tests for haute.deploy._utils — get_user, get_haute_version, build_manifest."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import MagicMock, patch

from haute._types import GraphNode, NodeData, PipelineGraph
from haute.deploy._config import ResolvedDeploy
from haute.deploy._utils import build_manifest, get_haute_version, get_user
from tests._deploy_helpers import make_resolved_deploy

# ---------------------------------------------------------------------------
# Helpers — real ResolvedDeploy with sensible defaults
# ---------------------------------------------------------------------------

_SENTINEL = object()


def _make_graph(
    pipeline_name: str | None = "my_pipeline",
    nodes_count: int = 3,
) -> PipelineGraph:
    """Build a minimal real PipelineGraph."""
    nodes = [
        GraphNode(
            id=f"n{i}",
            data=NodeData(label=f"node_{i}", nodeType="polars", config={}),
        )
        for i in range(nodes_count)
    ]
    return PipelineGraph(nodes=nodes, edges=[], pipeline_name=pipeline_name)


def _make_resolved(
    *,
    pipeline_name: str | None = "my_pipeline",
    nodes_count: int = 3,
    model_name: str = "my_model",
    pipeline_file: Path = Path("/repo/main.py"),
    target: str = "databricks",
    output_fields: list[str] | None = None,
    input_node_ids: list[str] | None = None,
    output_node_id: str = "sink_1",
    artifacts: object = _SENTINEL,
    input_schema: dict[str, str] | None = None,
    output_schema: dict[str, str] | None = None,
    removed_node_ids: object = _SENTINEL,
) -> ResolvedDeploy:
    """Build a real ResolvedDeploy with sensible defaults.

    Delegates to the shared ``make_resolved_deploy`` helper, adding
    graph-building and richer defaults needed by the deploy-utils tests.
    """
    graph = _make_graph(pipeline_name=pipeline_name, nodes_count=nodes_count)

    if artifacts is _SENTINEL:
        artifacts = {
            "model.pkl": Path("/repo/artifacts/model.pkl"),
            "scaler.pkl": Path("/repo/artifacts/scaler.pkl"),
        }
    if removed_node_ids is _SENTINEL:
        removed_node_ids = ["train_node", "eval_node"]

    return make_resolved_deploy(
        pipeline_file=pipeline_file,
        model_name=model_name,
        target=target,
        output_fields=output_fields or ["premium"],
        full_graph=graph,
        pruned_graph=graph,
        input_node_ids=input_node_ids or ["api_input_1"],
        output_node_id=output_node_id,
        artifacts=artifacts,
        input_schema=input_schema or {"age": "int", "postcode": "str"},
        output_schema=output_schema or {"premium": "float"},
        removed_node_ids=removed_node_ids,
    )


# ---------------------------------------------------------------------------
# get_user()
# ---------------------------------------------------------------------------


class TestGetUser:
    """Tests for get_user()."""

    def test_returns_string(self) -> None:
        result = get_user()
        assert isinstance(result, str)
        assert len(result) > 0

    def test_fallback_on_key_error(self) -> None:
        with patch("haute.deploy._utils.getpass.getuser", side_effect=KeyError("no user")):
            assert get_user() == "unknown"

    def test_fallback_on_os_error(self) -> None:
        with patch("haute.deploy._utils.getpass.getuser", side_effect=OSError("no tty")):
            assert get_user() == "unknown"

    def test_returns_actual_user_when_available(self) -> None:
        with patch("haute.deploy._utils.getpass.getuser", return_value="alice"):
            assert get_user() == "alice"


# ---------------------------------------------------------------------------
# get_haute_version()
# ---------------------------------------------------------------------------


class TestGetHauteVersion:
    """Tests for get_haute_version()."""

    def test_returns_string(self) -> None:
        result = get_haute_version()
        assert isinstance(result, str)
        assert len(result) > 0

    def test_fallback_on_package_not_found(self) -> None:
        from importlib.metadata import PackageNotFoundError

        with patch(
            "importlib.metadata.version",
            side_effect=PackageNotFoundError("haute"),
        ):
            assert get_haute_version() == "0.0.0-dev"

    def test_returns_version_when_installed(self) -> None:
        with patch("importlib.metadata.version", return_value="1.2.3"):
            assert get_haute_version() == "1.2.3"


# ---------------------------------------------------------------------------
# build_manifest()
# ---------------------------------------------------------------------------

_EXPECTED_KEYS = {
    "haute_version",
    "pipeline_name",
    "pipeline_file",
    "target",
    "created_at",
    "created_by",
    "input_node_ids",
    "output_node_id",
    "output_fields",
    "input_schema",
    "output_schema",
    "artifacts",
    "pruned_graph",
    "nodes_deployed",
    "nodes_skipped",
    "nodes_skipped_names",
}


class TestBuildManifest:
    """Tests for build_manifest()."""

    def test_all_keys_present(self) -> None:
        resolved = _make_resolved()
        manifest = build_manifest(resolved)
        assert set(manifest.keys()) == _EXPECTED_KEYS

    def test_pipeline_name_from_pruned_graph(self) -> None:
        resolved = _make_resolved(pipeline_name="graph_pipeline")
        manifest = build_manifest(resolved)
        assert manifest["pipeline_name"] == "graph_pipeline"

    def test_pipeline_name_falls_back_to_model_name(self) -> None:
        resolved = _make_resolved(pipeline_name=None, model_name="fallback_model")
        manifest = build_manifest(resolved)
        assert manifest["pipeline_name"] == "fallback_model"

    def test_pipeline_name_falls_back_when_empty_string(self) -> None:
        """Empty string is falsy, so it should also fall back to model_name."""
        resolved = _make_resolved(pipeline_name="", model_name="fallback_model")
        manifest = build_manifest(resolved)
        assert manifest["pipeline_name"] == "fallback_model"

    def test_pipeline_file_is_stringified(self) -> None:
        resolved = _make_resolved(pipeline_file=Path("/repo/pricing/main.py"))
        manifest = build_manifest(resolved)
        assert manifest["pipeline_file"] == "/repo/pricing/main.py"
        assert isinstance(manifest["pipeline_file"], str)

    def test_target_propagated(self) -> None:
        resolved = _make_resolved(target="container")
        manifest = build_manifest(resolved)
        assert manifest["target"] == "container"

    @patch("haute.deploy._utils.datetime")
    @patch("haute.deploy._utils.get_user", return_value="test_user")
    @patch("haute.deploy._utils.get_haute_version", return_value="2.0.0")
    def test_created_at_frozen(
        self,
        _mock_version: MagicMock,
        _mock_user: MagicMock,
        mock_dt: MagicMock,
    ) -> None:
        frozen = datetime(2026, 3, 1, 12, 0, 0, tzinfo=UTC)
        mock_dt.now.return_value = frozen
        mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)

        resolved = _make_resolved()
        manifest = build_manifest(resolved)

        assert manifest["created_at"] == frozen.isoformat()
        assert manifest["created_by"] == "test_user"
        assert manifest["haute_version"] == "2.0.0"

    def test_created_at_is_iso_format(self) -> None:
        resolved = _make_resolved()
        manifest = build_manifest(resolved)
        # Should parse without error as ISO 8601
        parsed = datetime.fromisoformat(manifest["created_at"])
        assert parsed.tzinfo is not None, "created_at should be timezone-aware"

    def test_artifacts_are_stringified(self) -> None:
        artifacts = {
            "model.pkl": Path("/repo/artifacts/model.pkl"),
            "config.json": Path("/repo/config/config.json"),
        }
        resolved = _make_resolved(artifacts=artifacts)
        manifest = build_manifest(resolved)

        assert manifest["artifacts"] == {
            "model.pkl": "/repo/artifacts/model.pkl",
            "config.json": "/repo/config/config.json",
        }
        for value in manifest["artifacts"].values():
            assert isinstance(value, str)

    def test_empty_artifacts(self) -> None:
        resolved = _make_resolved(artifacts={})
        manifest = build_manifest(resolved)
        assert manifest["artifacts"] == {}

    def test_input_node_ids_propagated(self) -> None:
        resolved = _make_resolved(input_node_ids=["api_1", "api_2"])
        manifest = build_manifest(resolved)
        assert manifest["input_node_ids"] == ["api_1", "api_2"]

    def test_output_node_id_propagated(self) -> None:
        resolved = _make_resolved(output_node_id="output_sink")
        manifest = build_manifest(resolved)
        assert manifest["output_node_id"] == "output_sink"

    def test_schemas_propagated(self) -> None:
        resolved = _make_resolved(
            input_schema={"x": "float", "y": "int"},
            output_schema={"result": "float"},
        )
        manifest = build_manifest(resolved)
        assert manifest["input_schema"] == {"x": "float", "y": "int"}
        assert manifest["output_schema"] == {"result": "float"}

    def test_output_fields_propagated(self) -> None:
        resolved = _make_resolved(output_fields=["premium", "discount"])
        manifest = build_manifest(resolved)
        assert manifest["output_fields"] == ["premium", "discount"]

    def test_nodes_deployed_count(self) -> None:
        resolved = _make_resolved(nodes_count=5)
        manifest = build_manifest(resolved)
        assert manifest["nodes_deployed"] == 5

    def test_nodes_skipped_count_and_names(self) -> None:
        removed = ["train_node", "eval_node", "debug_node"]
        resolved = _make_resolved(removed_node_ids=removed)
        manifest = build_manifest(resolved)
        assert manifest["nodes_skipped"] == 3
        assert manifest["nodes_skipped_names"] == removed

    def test_no_removed_nodes(self) -> None:
        resolved = _make_resolved(removed_node_ids=[])
        manifest = build_manifest(resolved)
        assert manifest["nodes_skipped"] == 0
        assert manifest["nodes_skipped_names"] == []

    def test_pruned_graph_is_model_dump(self) -> None:
        resolved = _make_resolved(pipeline_name="test_pipe", nodes_count=2)
        manifest = build_manifest(resolved)
        graph_dict = manifest["pruned_graph"]
        assert isinstance(graph_dict, dict)
        assert graph_dict["pipeline_name"] == "test_pipe"
        assert len(graph_dict["nodes"]) == 2
        assert graph_dict["edges"] == []
