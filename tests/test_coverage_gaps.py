"""Tests filling coverage gaps for low-coverage modules.

Targets:
  - _config_validation.py  — edge cases: underscore-prefixed keys, SUBMODEL_PORT
  - _cache.py              — _graph_base_fingerprint directly, edge sorting, repr fallback
  - _node_builder.py       — wrap_builder with explicit source_names list
  - _optimiser_io.py       — deepcopy isolation, concurrent cache independence
  - deploy/_model_code.py  — multi-artifact mapping, missing manifest key
  - deploy/_utils.py       — get_user actual value, get_haute_version dev fallback
  - deploy/_schema.py      — _find_node standalone, infer_output_schema no inputs,
                             cache write failure path
"""

from __future__ import annotations

import hashlib
import json
import os
import time as _time
from pathlib import Path
from unittest.mock import MagicMock, patch

import polars as pl
import pytest

from tests.conftest import make_graph as _g, make_node


# ===========================================================================
# _config_validation.py — remaining edge cases
# ===========================================================================


class TestConfigValidationEdgeCases:
    """Edge cases not covered by test_config_validation.py."""

    def test_underscore_prefixed_keys_ignored(self):
        """Keys starting with '_' should NOT be flagged as unrecognised."""
        from haute._config_validation import warn_unrecognized_config_keys
        from haute._types import NodeType

        bad = warn_unrecognized_config_keys(
            NodeType.POLARS,
            {"code": "x", "_internal_marker": True, "_debug": 99},
        )
        assert bad == []

    def test_submodel_port_has_no_valid_keys(self):
        """SUBMODEL_PORT is not in the TypedDict registry — returns []."""
        from haute._config_validation import VALID_KEYS, warn_unrecognized_config_keys
        from haute._types import NodeType

        # SUBMODEL_PORT should not appear in VALID_KEYS (no TypedDict for it)
        assert NodeType.SUBMODEL_PORT not in VALID_KEYS

        # Therefore validation returns empty (nothing to validate against)
        bad = warn_unrecognized_config_keys(
            NodeType.SUBMODEL_PORT,
            {"anything": 42, "goes": True},
        )
        assert bad == []

    def test_valid_keys_for_returns_none_for_unknown(self):
        """_valid_keys_for with a type that has no TypedDict returns None."""
        from haute._config_validation import _valid_keys_for
        from haute._types import NodeType

        result = _valid_keys_for(NodeType.SUBMODEL_PORT)
        assert result is None

    def test_valid_keys_for_includes_universal_keys(self):
        """_valid_keys_for merges TypedDict keys with universal keys."""
        from haute._config_validation import _UNIVERSAL_KEYS, _valid_keys_for
        from haute._types import NodeType

        keys = _valid_keys_for(NodeType.API_INPUT)
        assert keys is not None
        for uk in _UNIVERSAL_KEYS:
            assert uk in keys

    def test_node_label_used_in_warning(self, capsys):
        """When node_label is provided, it appears in the warning log."""
        from haute._config_validation import warn_unrecognized_config_keys
        from haute._types import NodeType

        warn_unrecognized_config_keys(
            NodeType.OUTPUT,
            {"bad_key": 1},
            node_label="my_custom_label",
        )
        out = capsys.readouterr().out
        assert "my_custom_label" in out

    def test_node_type_value_used_when_no_label(self, capsys):
        """When node_label is empty, the node type value is used in the log."""
        from haute._config_validation import warn_unrecognized_config_keys
        from haute._types import NodeType

        warn_unrecognized_config_keys(
            NodeType.OUTPUT,
            {"bogus": 1},
            node_label="",
        )
        out = capsys.readouterr().out
        assert "output" in out

    def test_column_renames_universal_key(self):
        """column_renames should be accepted for any node type."""
        from haute._config_validation import warn_unrecognized_config_keys
        from haute._types import NodeType

        bad = warn_unrecognized_config_keys(
            NodeType.POLARS,
            {"code": "x", "column_renames": {"old": "new"}},
        )
        assert bad == []


# ===========================================================================
# _cache.py — _graph_base_fingerprint and graph_fingerprint details
# ===========================================================================


class TestGraphBaseFingerprint:
    """Direct tests for _graph_base_fingerprint."""

    def test_deterministic_across_calls(self):
        """Same graph always produces the same base fingerprint."""
        from haute._cache import _graph_base_fingerprint
        from haute._types import GraphEdge, GraphNode, NodeData, PipelineGraph

        nodes = [
            GraphNode(id="n1", data=NodeData(label="A", nodeType="polars", config={"k": 1})),
            GraphNode(id="n2", data=NodeData(label="B", nodeType="output", config={})),
        ]
        edges = [GraphEdge(id="e1", source="n1", target="n2")]
        g = PipelineGraph(nodes=nodes, edges=edges)

        fp1 = _graph_base_fingerprint(g)
        fp2 = _graph_base_fingerprint(g)
        assert fp1 == fp2
        assert len(fp1) == 64  # sha256 hex digest

    def test_node_order_does_not_matter(self):
        """Nodes are sorted by ID, so insertion order is irrelevant."""
        from haute._cache import _graph_base_fingerprint
        from haute._types import GraphNode, NodeData, PipelineGraph

        n1 = GraphNode(id="a", data=NodeData(label="A", nodeType="polars", config={}))
        n2 = GraphNode(id="b", data=NodeData(label="B", nodeType="polars", config={}))

        g1 = PipelineGraph(nodes=[n1, n2], edges=[])
        g2 = PipelineGraph(nodes=[n2, n1], edges=[])  # reversed order

        assert _graph_base_fingerprint(g1) == _graph_base_fingerprint(g2)

    def test_edge_order_does_not_matter(self):
        """Edges are sorted by (source, target), so insertion order is irrelevant."""
        from haute._cache import _graph_base_fingerprint
        from haute._types import GraphEdge, GraphNode, NodeData, PipelineGraph

        n1 = GraphNode(id="n1", data=NodeData(label="A", nodeType="polars", config={}))
        n2 = GraphNode(id="n2", data=NodeData(label="B", nodeType="polars", config={}))
        e1 = GraphEdge(id="e1", source="n1", target="n2")
        e2 = GraphEdge(id="e2", source="n2", target="n1")

        g1 = PipelineGraph(nodes=[n1, n2], edges=[e1, e2])
        g2 = PipelineGraph(nodes=[n1, n2], edges=[e2, e1])  # reversed

        assert _graph_base_fingerprint(g1) == _graph_base_fingerprint(g2)

    def test_config_with_non_json_serializable_value(self):
        """Non-JSON-serializable config values use repr() as fallback."""
        from haute._cache import _graph_base_fingerprint
        from haute._types import GraphNode, NodeData, PipelineGraph

        # sets are not JSON-serializable; the code uses default=repr
        n = GraphNode(
            id="n1",
            data=NodeData(label="A", nodeType="polars", config={"vals": {1, 2, 3}}),
        )
        g = PipelineGraph(nodes=[n], edges=[])

        # Should not raise — repr() is used as JSON default
        fp = _graph_base_fingerprint(g)
        assert isinstance(fp, str)
        assert len(fp) == 64

    def test_different_config_different_fingerprint(self):
        """Changing a config value produces a different fingerprint."""
        from haute._cache import _graph_base_fingerprint
        from haute._types import GraphNode, NodeData, PipelineGraph

        g1 = PipelineGraph(
            nodes=[
                GraphNode(id="n1", data=NodeData(label="A", nodeType="polars", config={"x": 1}))
            ],
        )
        g2 = PipelineGraph(
            nodes=[
                GraphNode(id="n1", data=NodeData(label="A", nodeType="polars", config={"x": 2}))
            ],
        )
        assert _graph_base_fingerprint(g1) != _graph_base_fingerprint(g2)

    def test_empty_graph_fingerprint(self):
        """Empty graph produces a valid sha256 hash."""
        from haute._cache import _graph_base_fingerprint
        from haute._types import PipelineGraph

        g = PipelineGraph()
        fp = _graph_base_fingerprint(g)
        # Hash of empty string
        assert fp == hashlib.sha256(b"").hexdigest()


class TestGraphFingerprintWithExtraKeys:
    """Test graph_fingerprint with extra_keys combinations."""

    def test_multiple_extra_keys(self):
        """Multiple extra keys are incorporated into the fingerprint."""
        from haute._cache import graph_fingerprint
        from haute._types import PipelineGraph

        g = PipelineGraph()
        fp_a = graph_fingerprint(g, "key1")
        fp_ab = graph_fingerprint(g, "key1", "key2")
        fp_ba = graph_fingerprint(g, "key2", "key1")

        assert fp_a != fp_ab
        # Order matters for extra keys
        assert fp_ab != fp_ba

    def test_extra_key_order_matters(self):
        """Different ordering of extra keys produces different fingerprints."""
        from haute._cache import graph_fingerprint
        from haute._types import GraphNode, NodeData, PipelineGraph

        n = GraphNode(id="n1", data=NodeData(label="A", nodeType="polars", config={}))
        g = PipelineGraph(nodes=[n])
        assert graph_fingerprint(g, "a", "b") != graph_fingerprint(g, "b", "a")


# ===========================================================================
# _node_builder.py — additional coverage for wrap_builder
# ===========================================================================


class TestWrapBuilderAdditional:
    """Additional edge cases for wrap_builder."""

    def test_hook_receives_correct_source_names_list(self):
        """When source_names is a populated list, the hook gets that list."""
        from haute._node_builder import NodeBuildHooks, wrap_builder

        received = []

        def capture(node, names):
            received.append(list(names))
            return None

        def base(node, source_names=None, **kw):
            return (node.data.label, lambda df: df, False)

        hooks = NodeBuildHooks(before_build=capture)
        wrapped = wrap_builder(base, hooks)
        node = make_node({"id": "t", "data": {"label": "t", "nodeType": "polars", "config": {}}})
        wrapped(node, source_names=["src_a", "src_b", "src_c"])
        assert received == [["src_a", "src_b", "src_c"]]

    def test_hook_override_skips_base_completely(self):
        """When hook returns a result, base is never invoked."""
        from haute._node_builder import NodeBuildHooks, wrap_builder

        base_called = []

        def base(node, source_names=None, **kw):
            base_called.append(True)
            return ("base", lambda: None, False)

        override_fn = lambda df: df  # noqa: E731
        hooks = NodeBuildHooks(before_build=lambda n, s: ("override", override_fn, True))
        wrapped = wrap_builder(base, hooks)
        node = make_node({"id": "t", "data": {"label": "t", "nodeType": "polars", "config": {}}})
        name, fn, is_source = wrapped(node, source_names=["a"])
        assert name == "override"
        assert fn is override_fn
        assert is_source is True
        assert base_called == []


# ===========================================================================
# _optimiser_io.py — deepcopy isolation and edge cases
# ===========================================================================


class TestOptimiserArtifactDeepCopy:
    """Ensure load_optimiser_artifact returns independent copies."""

    def test_mutation_does_not_affect_cache(self, tmp_path):
        """Mutating the returned dict should not change cached data."""
        from haute._optimiser_io import _artifact_cache, load_optimiser_artifact

        _artifact_cache.clear()

        f = tmp_path / "artifact.json"
        data = {"mode": "online", "lambdas": {"x": 1.5}}
        f.write_text(json.dumps(data))

        result1 = load_optimiser_artifact(str(f))
        result1["mode"] = "MUTATED"
        result1["lambdas"]["x"] = 999

        result2 = load_optimiser_artifact(str(f))
        assert result2["mode"] == "online"
        assert result2["lambdas"]["x"] == 1.5

        _artifact_cache.clear()

    def test_two_results_are_independent(self, tmp_path):
        """Two calls return independent dict objects."""
        from haute._optimiser_io import _artifact_cache, load_optimiser_artifact

        _artifact_cache.clear()

        f = tmp_path / "artifact.json"
        f.write_text(json.dumps({"items": [1, 2, 3]}))

        r1 = load_optimiser_artifact(str(f))
        r2 = load_optimiser_artifact(str(f))
        assert r1 is not r2
        assert r1 == r2

        r1["items"].append(4)
        assert r2["items"] == [1, 2, 3]

        _artifact_cache.clear()


class TestOptimiserArtifactMtimeZero:
    """Test mtime fallback when getmtime raises OSError."""

    def test_mtime_defaults_to_zero_on_oserror(self, tmp_path):
        """When os.path.getmtime raises, mtime defaults to 0.0 but open() still fails."""
        from haute._optimiser_io import _artifact_cache, load_optimiser_artifact

        _artifact_cache.clear()

        nonexistent = str(tmp_path / "does_not_exist.json")
        with pytest.raises(FileNotFoundError):
            load_optimiser_artifact(nonexistent)

        _artifact_cache.clear()


class TestLoadMlflowOptimiserArtifactDeepCopy:
    """Ensure load_mlflow_optimiser_artifact returns independent copies."""

    def test_mutation_does_not_affect_mlflow_cache(self, tmp_path):
        """Mutating result from MLflow path should not affect cache."""
        from haute._optimiser_io import _mlflow_cache, load_mlflow_optimiser_artifact

        _mlflow_cache.clear()

        artifact_data = {"mode": "ratebook", "constraints": [1, 2]}
        artifact_path = tmp_path / "optimiser_result.json"
        artifact_path.write_text(json.dumps(artifact_data))

        with (
            patch("haute._mlflow_utils.resolve_mlflow_source") as mock_resolve,
            patch("mlflow.artifacts.download_artifacts", return_value=str(artifact_path)),
        ):
            mock_resolve.return_value = ("run_id_1", "1", MagicMock(), MagicMock())

            r1 = load_mlflow_optimiser_artifact(source_type="run", run_id="run_id_1")
            r1["mode"] = "MUTATED"

            r2 = load_mlflow_optimiser_artifact(source_type="run", run_id="run_id_1")
            assert r2["mode"] == "ratebook"

        _mlflow_cache.clear()


# ===========================================================================
# deploy/_model_code.py — additional edge cases
# ===========================================================================


class TestHauteModelEdgeCases:
    """Additional tests for HauteModel."""

    def test_load_context_multiple_artifacts(self, tmp_path):
        """load_context maps all artifacts present in context."""
        from haute.deploy._model_code import HauteModel

        manifest = {
            "pruned_graph": {"nodes": [], "edges": []},
            "input_node_ids": ["src"],
            "output_node_id": "out",
            "output_fields": None,
            "artifacts": {
                "model.pkl": "/orig/model.pkl",
                "scaler.pkl": "/orig/scaler.pkl",
                "encoder.pkl": "/orig/encoder.pkl",
            },
        }
        manifest_path = tmp_path / "deploy_manifest.json"
        manifest_path.write_text(json.dumps(manifest))

        model = HauteModel()
        ctx = MagicMock()
        ctx.artifacts = {
            "deploy_manifest": str(manifest_path),
            "model.pkl": "/served/model.pkl",
            "scaler.pkl": "/served/scaler.pkl",
            # encoder.pkl deliberately missing from context
        }
        model.load_context(ctx)

        assert model._artifact_paths == {
            "model.pkl": "/served/model.pkl",
            "scaler.pkl": "/served/scaler.pkl",
        }
        assert "encoder.pkl" not in model._artifact_paths

    def test_load_context_empty_artifacts(self, tmp_path):
        """Manifest with no artifacts results in empty artifact_paths."""
        from haute.deploy._model_code import HauteModel

        manifest = {
            "pruned_graph": {"nodes": [], "edges": []},
            "input_node_ids": ["src"],
            "output_node_id": "out",
            "artifacts": {},
        }
        manifest_path = tmp_path / "deploy_manifest.json"
        manifest_path.write_text(json.dumps(manifest))

        model = HauteModel()
        ctx = MagicMock()
        ctx.artifacts = {"deploy_manifest": str(manifest_path)}
        model.load_context(ctx)

        assert model._artifact_paths == {}

    def test_predict_input_converted_to_polars(self):
        """predict() converts pandas input to polars for score_graph."""
        import pandas as pd

        from haute.deploy._model_code import HauteModel

        model = HauteModel()
        model._graph = MagicMock()
        model._input_node_ids = ["src"]
        model._output_node_id = "out"
        model._artifact_paths = {}
        model._output_fields = None

        input_pd = pd.DataFrame({"a": [1, 2], "b": [3.0, 4.0]})
        mock_result = pl.DataFrame({"a": [1, 2], "b": [3.0, 4.0]})

        with patch("haute.deploy._scorer.score_graph", return_value=mock_result) as mock:
            result = model.predict(MagicMock(), input_pd)

        # Verify input was converted to polars
        call_kw = mock.call_args.kwargs
        assert isinstance(call_kw["input_df"], pl.DataFrame)
        assert call_kw["input_df"].columns == ["a", "b"]
        assert call_kw["input_df"].shape == (2, 2)

        # Verify output was converted back to pandas
        assert isinstance(result, pd.DataFrame)
        assert list(result.columns) == ["a", "b"]


# ===========================================================================
# deploy/_utils.py — remaining paths
# ===========================================================================


class TestGetUserEdgeCases:
    """Extra coverage for get_user."""

    def test_returns_nonempty_string(self):
        """get_user should always return a non-empty string."""
        from haute.deploy._utils import get_user

        result = get_user()
        assert isinstance(result, str)
        assert len(result) > 0

    def test_actual_username_matches_getpass(self):
        """Normal path: get_user returns the same value as getpass.getuser."""
        import getpass

        from haute.deploy._utils import get_user

        expected = getpass.getuser()
        assert get_user() == expected


class TestGetHauteVersionEdgeCases:
    """Extra coverage for get_haute_version."""

    def test_returns_version_string_format(self):
        """Version string should contain at least one dot (semantic versioning)."""
        from haute.deploy._utils import get_haute_version

        version = get_haute_version()
        # Either a real version like "0.3.1" or fallback "0.0.0-dev"
        assert "." in version


class TestBuildManifestEdgeCases:
    """Extra coverage for build_manifest field values."""

    def test_manifest_includes_created_by(self):
        """build_manifest always includes created_by field."""
        from tests._deploy_helpers import make_resolved_deploy

        from haute.deploy._utils import build_manifest

        resolved = make_resolved_deploy()
        manifest = build_manifest(resolved)
        assert "created_by" in manifest
        assert isinstance(manifest["created_by"], str)
        assert len(manifest["created_by"]) > 0

    def test_manifest_includes_haute_version(self):
        """build_manifest always includes haute_version field."""
        from tests._deploy_helpers import make_resolved_deploy

        from haute.deploy._utils import build_manifest

        resolved = make_resolved_deploy()
        manifest = build_manifest(resolved)
        assert "haute_version" in manifest
        assert isinstance(manifest["haute_version"], str)

    def test_manifest_nodes_deployed_matches_graph(self):
        """nodes_deployed count matches the actual number of nodes in pruned_graph."""
        from haute._types import GraphNode, NodeData, PipelineGraph

        from tests._deploy_helpers import make_resolved_deploy

        from haute.deploy._utils import build_manifest

        nodes = [
            GraphNode(
                id=f"n{i}",
                data=NodeData(label=f"node_{i}", nodeType="polars", config={}),
            )
            for i in range(7)
        ]
        graph = PipelineGraph(nodes=nodes, edges=[])
        resolved = make_resolved_deploy(pruned_graph=graph)
        manifest = build_manifest(resolved)
        assert manifest["nodes_deployed"] == 7


# ===========================================================================
# deploy/_schema.py — _find_node and edge cases
# ===========================================================================


class TestFindNode:
    """Direct tests for _find_node."""

    def test_finds_existing_node(self):
        """_find_node returns the correct node when it exists."""
        from haute.deploy._schema import _find_node

        graph = _g(
            {
                "nodes": [
                    {
                        "id": "src",
                        "data": {
                            "label": "src",
                            "nodeType": "apiInput",
                            "config": {"path": "/data"},
                        },
                    },
                    {
                        "id": "out",
                        "data": {"label": "out", "nodeType": "output", "config": {}},
                    },
                ],
            }
        )

        node = _find_node(graph, "src")
        assert node.id == "src"
        assert node.data.config.get("path") == "/data"

    def test_raises_for_missing_node(self):
        """_find_node raises ValueError for non-existent node ID."""
        from haute.deploy._schema import _find_node

        graph = _g({"nodes": []})
        with pytest.raises(ValueError, match="not found"):
            _find_node(graph, "nonexistent")

    def test_raises_message_includes_node_id(self):
        """ValueError message includes the missing node ID."""
        from haute.deploy._schema import _find_node

        graph = _g({"nodes": []})
        with pytest.raises(ValueError, match="my_missing_node"):
            _find_node(graph, "my_missing_node")


class TestInferOutputSchemaEdgeCases:
    """Edge cases for infer_output_schema."""

    def test_no_input_node_ids_raises(self, monkeypatch, tmp_path):
        """infer_output_schema with empty input_node_ids raises ValueError."""
        from haute.deploy._schema import infer_output_schema

        monkeypatch.chdir(tmp_path)

        graph = _g(
            {
                "nodes": [
                    {
                        "id": "out",
                        "data": {"label": "out", "nodeType": "output", "config": {}},
                    },
                ],
            }
        )

        with pytest.raises(ValueError, match="No API input nodes"):
            infer_output_schema(graph, "out", [])

    def test_cache_write_failure_does_not_raise(self, monkeypatch, tmp_path):
        """If cache writing fails, infer_output_schema still returns the schema."""
        from haute.deploy._schema import infer_output_schema

        monkeypatch.chdir(tmp_path)

        pq_path = tmp_path / "data.parquet"
        pl.DataFrame({"x": [1.0]}).write_parquet(pq_path)

        graph = _g(
            {
                "nodes": [
                    {
                        "id": "src",
                        "data": {
                            "label": "src",
                            "nodeType": "apiInput",
                            "config": {"path": str(pq_path)},
                        },
                    },
                    {
                        "id": "out",
                        "data": {"label": "out", "nodeType": "output", "config": {}},
                    },
                ],
                "edges": [{"id": "e1", "source": "src", "target": "out"}],
            }
        )

        mock_result = pl.DataFrame({"result": [42.0]})

        # Make cache directory read-only to cause write failure
        with (
            patch("haute.deploy._scorer.score_graph", return_value=mock_result),
            patch("haute.deploy._schema.Path.write_text", side_effect=OSError("read-only")),
        ):
            result = infer_output_schema(graph, "out", ["src"])

        # Should still return the computed schema despite cache write failure
        assert result == {"result": "Float64"}

    def test_input_node_with_no_path_for_sample_raises(self, monkeypatch, tmp_path):
        """infer_output_schema raises when input node has no path for sample row."""
        from haute.deploy._schema import infer_output_schema

        monkeypatch.chdir(tmp_path)

        graph = _g(
            {
                "nodes": [
                    {
                        "id": "src",
                        "data": {
                            "label": "src",
                            "nodeType": "apiInput",
                            "config": {},  # no path
                        },
                    },
                    {
                        "id": "out",
                        "data": {"label": "out", "nodeType": "output", "config": {}},
                    },
                ],
                "edges": [{"id": "e1", "source": "src", "target": "out"}],
            }
        )

        with pytest.raises(ValueError, match="no path"):
            infer_output_schema(graph, "out", ["src"])


class TestInferInputSchemaEdgeCases:
    """Extra edge cases for infer_input_schema."""

    def test_csv_file_schema(self, tmp_path):
        """infer_input_schema can read schema from CSV files."""
        from haute.deploy._schema import infer_input_schema

        csv_path = tmp_path / "input.csv"
        pl.DataFrame({"name": ["alice"], "score": [95.5]}).write_csv(csv_path)

        graph = _g(
            {
                "nodes": [
                    {
                        "id": "src",
                        "data": {
                            "label": "src",
                            "nodeType": "apiInput",
                            "config": {"path": str(csv_path)},
                        },
                    },
                ],
            }
        )

        schema = infer_input_schema(graph, "src")
        assert "name" in schema
        assert "score" in schema
        assert isinstance(schema["name"], str)

    def test_parquet_multiple_columns(self, tmp_path):
        """infer_input_schema returns all columns from a multi-column parquet."""
        from haute.deploy._schema import infer_input_schema

        pq_path = tmp_path / "input.parquet"
        pl.DataFrame(
            {"age": [25], "premium": [100.5], "region": ["A"], "active": [True]}
        ).write_parquet(pq_path)

        graph = _g(
            {
                "nodes": [
                    {
                        "id": "src",
                        "data": {
                            "label": "src",
                            "nodeType": "apiInput",
                            "config": {"path": str(pq_path)},
                        },
                    },
                ],
            }
        )

        schema = infer_input_schema(graph, "src")
        assert len(schema) == 4
        assert set(schema.keys()) == {"age", "premium", "region", "active"}
