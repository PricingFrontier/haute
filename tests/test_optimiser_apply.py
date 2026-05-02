"""Tests for the OptimiserApply node type.

Covers: type inference, config building, codegen, executor (online + ratebook),
artifact caching, and deploy bundling/scoring.
"""

import json
import os
import tempfile
from types import SimpleNamespace
from unittest.mock import patch

import polars as pl
import pytest

from haute._parser_helpers import _build_node_config
from haute._types import GraphNode, NodeData, NodeType
from haute.codegen import _node_to_code
from haute.executor import _apply_online, _apply_ratebook, _build_node_fn

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _make_online_artifact(
    lambdas: dict | None = None,
    version: str = "test_v1",
) -> dict:
    return {
        "version": version,
        "created_at": "2026-02-24T14:00:00Z",
        "mode": "online",
        "lambdas": lambdas or {"predicted_volume": 0.5},
        "objective": "predicted_income",
        "constraints": {"predicted_volume": {"min": 0.9}},
        "quote_id": "quote_id",
        "scenario_index": "scenario_index",
        "scenario_value": "scenario_value",
        "chunk_size": 500_000,
    }


def _make_ratebook_artifact(version: str = "rb_v1") -> dict:
    return {
        "version": version,
        "created_at": "2026-02-24T14:00:00Z",
        "mode": "ratebook",
        "lambdas": {"predicted_volume": 0.3},
        "objective": "predicted_income",
        "constraints": {"predicted_volume": {"min": 0.9}},
        "factor_tables": {
            "region": [
                {"__factor_group__": "London", "optimal_scenario_value": 1.05},
                {"__factor_group__": "Manchester", "optimal_scenario_value": 0.98},
            ],
        },
    }


def _write_artifact(artifact: dict) -> str:
    fd, path = tempfile.mkstemp(suffix=".json")
    os.close(fd)
    with open(path, "w") as f:
        json.dump(artifact, f)
    return path


def _make_node(config: dict, label: str = "apply_opt") -> GraphNode:
    return GraphNode(
        id="apply_1",
        data=NodeData(
            label=label,
            nodeType=NodeType.OPTIMISER_APPLY,
            config=config,
        ),
    )


def _scored_df() -> pl.DataFrame:
    """Two quotes x 3 steps — standard test data for online apply."""
    return pl.DataFrame(
        {
            "quote_id": ["q1", "q1", "q1", "q2", "q2", "q2"],
            "scenario_index": [0, 1, 2, 0, 1, 2],
            "scenario_value": [0.9, 1.0, 1.1, 0.9, 1.0, 1.1],
            "predicted_income": [90.0, 100.0, 110.0, 45.0, 50.0, 55.0],
            "predicted_volume": [1.0, 0.9, 0.7, 1.0, 0.95, 0.8],
        }
    )


# ---------------------------------------------------------------------------
# Type inference
# ---------------------------------------------------------------------------


class TestEnumValue:
    def test_enum_value(self):
        assert NodeType.OPTIMISER_APPLY == "optimiserApply"
        assert NodeType.OPTIMISER_APPLY.value == "optimiserApply"

    def test_distinct_from_optimiser(self):
        assert NodeType.OPTIMISER_APPLY != NodeType.OPTIMISER


# ---------------------------------------------------------------------------
# Config building
# ---------------------------------------------------------------------------


class TestBuildConfig:
    def test_build_config(self):
        config = _build_node_config(
            node_type=NodeType.OPTIMISER_APPLY,
            decorator_kwargs={
                "optimiser_apply": True,
                "source_type": "file",
                "artifact_path": "artifacts/opt_v1.json",
                "version_column": "__opt_ver__",
            },
            body="",
            param_names=["df"],
        )
        assert config["sourceType"] == "file"
        assert config["artifact_path"] == "artifacts/opt_v1.json"
        assert config["version_column"] == "__opt_ver__"

    def test_build_config_minimal(self):
        config = _build_node_config(
            node_type=NodeType.OPTIMISER_APPLY,
            decorator_kwargs={"optimiser_apply": True},
            body="",
            param_names=["df"],
        )
        assert "artifact_path" not in config

    def test_build_config_mlflow_keys(self):
        config = _build_node_config(
            node_type=NodeType.OPTIMISER_APPLY,
            decorator_kwargs={
                "optimiser_apply": True,
                "source_type": "registered",
                "registered_model": "my_opt_model",
                "version": "3",
            },
            body="",
            param_names=["df"],
        )
        assert config["sourceType"] == "registered"
        assert config["registered_model"] == "my_opt_model"
        assert config["version"] == "3"


# ---------------------------------------------------------------------------
# Codegen
# ---------------------------------------------------------------------------


class TestCodegen:
    def test_codegen_with_path(self):
        node = _make_node(
            {"sourceType": "file", "artifact_path": "artifacts/opt_v1.json"},
            label="apply_optimised_price",
        )
        code = _node_to_code(node, source_names=["score_models"])
        assert 'config="config/apply_optimisation/apply_optimised_price.json"' in code
        assert "def apply_optimised_price(" in code
        assert "return score_models" in code

    def test_codegen_empty_config(self):
        node = _make_node({}, label="apply_opt")
        code = _node_to_code(node, source_names=["df"])
        assert 'config="config/apply_optimisation/apply_opt.json"' in code

    def test_codegen_mlflow_registered(self):
        node = _make_node(
            {
                "sourceType": "registered",
                "registered_model": "opt_model",
                "version": "2",
            }
        )
        code = _node_to_code(node, source_names=["df"])
        assert 'config="config/apply_optimisation/apply_opt.json"' in code

    def test_codegen_mlflow_run(self):
        node = _make_node(
            {
                "sourceType": "run",
                "run_id": "abc123",
            }
        )
        code = _node_to_code(node, source_names=["df"])
        assert 'config="config/apply_optimisation/apply_opt.json"' in code

    def test_codegen_version_column(self):
        node = _make_node(
            {"sourceType": "file", "artifact_path": "a.json", "version_column": "__ver__"},
        )
        code = _node_to_code(node, source_names=["df"])
        assert 'config="config/apply_optimisation/apply_opt.json"' in code


# ---------------------------------------------------------------------------
# Executor: passthrough
# ---------------------------------------------------------------------------


class TestExecutorPassthrough:
    def test_passthrough_when_no_config(self):
        node = _make_node({})
        _, fn, is_source = _build_node_fn(node, source_names=["s"])
        assert not is_source
        lf = pl.DataFrame({"a": [1, 2]}).lazy()
        result = fn(lf).collect()
        assert result.columns == ["a"]
        assert len(result) == 2

    def test_passthrough_when_empty_path(self):
        node = _make_node({"sourceType": "file", "artifact_path": ""})
        _, fn, _ = _build_node_fn(node, source_names=["s"])
        lf = pl.DataFrame({"x": [1]}).lazy()
        assert fn(lf).collect().columns == ["x"]

    def test_passthrough_when_mlflow_run_no_run_id(self):
        node = _make_node({"sourceType": "run", "run_id": ""})
        _, fn, _ = _build_node_fn(node, source_names=["s"])
        lf = pl.DataFrame({"x": [1]}).lazy()
        assert fn(lf).collect().columns == ["x"]

    def test_passthrough_when_registered_no_model(self):
        node = _make_node({"sourceType": "registered", "registered_model": ""})
        _, fn, _ = _build_node_fn(node, source_names=["s"])
        lf = pl.DataFrame({"x": [1]}).lazy()
        assert fn(lf).collect().columns == ["x"]

    def test_file_source_type_with_path(self):
        path = _write_artifact(_make_online_artifact())
        try:
            node = _make_node({"sourceType": "file", "artifact_path": path})
            _, fn, _ = _build_node_fn(node, source_names=["scored"])
            result = fn(_scored_df().lazy()).collect()
            assert len(result) == 2
            assert "optimal_scenario_value" in result.columns
        finally:
            os.unlink(path)

    def test_missing_source_type_with_path_raises(self):
        from haute.errors import ConfigError

        path = _write_artifact(_make_ratebook_artifact())
        try:
            node = _make_node({"artifact_path": path})
            with pytest.raises(ConfigError, match="sourceType"):
                _build_node_fn(node, source_names=["base"])
        finally:
            os.unlink(path)


# ---------------------------------------------------------------------------
# Executor: online mode
# ---------------------------------------------------------------------------


class TestExecutorOnline:
    def test_online_apply_basic(self):
        path = _write_artifact(_make_online_artifact())
        try:
            node = _make_node({"sourceType": "file", "artifact_path": path})
            _, fn, _ = _build_node_fn(node, source_names=["scored"])
            result = fn(_scored_df().lazy()).collect()
            assert len(result) == 2  # one row per quote
            assert "optimal_scenario_value" in result.columns
            assert "optimal_objective" in result.columns
            assert "__optimiser_version__" in result.columns
            assert result["__optimiser_version__"][0] == "test_v1"
        finally:
            os.unlink(path)

    def test_online_custom_version_column(self):
        path = _write_artifact(_make_online_artifact())
        try:
            node = _make_node(
                {"sourceType": "file", "artifact_path": path, "version_column": "__v__"}
            )
            _, fn, _ = _build_node_fn(node, source_names=["scored"])
            result = fn(_scored_df().lazy()).collect()
            assert "__v__" in result.columns
            assert "__optimiser_version__" not in result.columns
        finally:
            os.unlink(path)

    def test_online_no_version_when_empty(self):
        artifact = _make_online_artifact(version="")
        path = _write_artifact(artifact)
        try:
            node = _make_node({"sourceType": "file", "artifact_path": path})
            _, fn, _ = _build_node_fn(node, source_names=["scored"])
            result = fn(_scored_df().lazy()).collect()
            # Version column should not be present when version is empty
            assert "__optimiser_version__" not in result.columns
        finally:
            os.unlink(path)

    def test_online_zero_lambdas_picks_max_objective(self):
        """With zero lambdas, each quote should pick the step maximizing objective."""
        artifact = _make_online_artifact(lambdas={"predicted_volume": 0.0})
        path = _write_artifact(artifact)
        try:
            node = _make_node({"sourceType": "file", "artifact_path": path})
            _, fn, _ = _build_node_fn(node, source_names=["scored"])
            result = fn(_scored_df().lazy()).collect()
            # Step 2 (scenario_value=1.1) has highest income for both quotes
            assert result["optimal_scenario_value"].to_list() == pytest.approx([1.1, 1.1])
        finally:
            os.unlink(path)


# ---------------------------------------------------------------------------
# Executor: ratebook mode
# ---------------------------------------------------------------------------


class TestExecutorRatebook:
    def test_ratebook_apply_basic(self):
        path = _write_artifact(_make_ratebook_artifact())
        try:
            df = pl.DataFrame(
                {
                    "quote_id": ["q1", "q2", "q3"],
                    "region": ["London", "Manchester", "London"],
                    "price": [100.0, 200.0, 150.0],
                }
            )
            node = _make_node({"sourceType": "file", "artifact_path": path})
            _, fn, _ = _build_node_fn(node, source_names=["base"])
            result = fn(df.lazy()).collect()
            assert "region_optimised_factor" in result.columns
            assert "optimised_factor" in result.columns
            assert "__optimiser_version__" in result.columns
            # London factor = 1.05
            london = result.filter(pl.col("region") == "London")
            assert london["region_optimised_factor"][0] == pytest.approx(1.05)
            assert london["optimised_factor"][0] == pytest.approx(1.05)
            # Manchester factor = 0.98
            manc = result.filter(pl.col("region") == "Manchester")
            assert manc["region_optimised_factor"][0] == pytest.approx(0.98)
            assert manc["optimised_factor"][0] == pytest.approx(0.98)
        finally:
            os.unlink(path)

    def test_ratebook_multi_factor_combined(self):
        """Multiple factor tables should each get a column and be multiplied together."""
        artifact = _make_ratebook_artifact()
        artifact["factor_tables"]["age_band"] = [
            {"__factor_group__": "young", "optimal_scenario_value": 1.10},
            {"__factor_group__": "old", "optimal_scenario_value": 0.95},
        ]
        path = _write_artifact(artifact)
        try:
            df = pl.DataFrame(
                {
                    "quote_id": ["q1", "q2"],
                    "region": ["London", "Manchester"],
                    "age_band": ["young", "old"],
                    "price": [100.0, 200.0],
                }
            )
            node = _make_node({"sourceType": "file", "artifact_path": path})
            _, fn, _ = _build_node_fn(node, source_names=["base"])
            result = fn(df.lazy()).collect()
            assert "region_optimised_factor" in result.columns
            assert "age_band_optimised_factor" in result.columns
            assert "optimised_factor" in result.columns
            # q1: London(1.05) * young(1.10) = 1.155
            assert result["optimised_factor"][0] == pytest.approx(1.05 * 1.10)
            # q2: Manchester(0.98) * old(0.95) = 0.931
            assert result["optimised_factor"][1] == pytest.approx(0.98 * 0.95)
        finally:
            os.unlink(path)

    def test_ratebook_missing_level_gets_default(self):
        path = _write_artifact(_make_ratebook_artifact())
        try:
            df = pl.DataFrame(
                {
                    "quote_id": ["q1"],
                    "region": ["Edinburgh"],  # not in factor table
                    "price": [100.0],
                }
            )
            node = _make_node({"sourceType": "file", "artifact_path": path})
            _, fn, _ = _build_node_fn(node, source_names=["base"])
            result = fn(df.lazy()).collect()
            # Should get default value of 1.0
            assert result["region_optimised_factor"][0] == pytest.approx(1.0)
            assert result["optimised_factor"][0] == pytest.approx(1.0)
        finally:
            os.unlink(path)

    def test_ratebook_empty_factor_tables(self):
        artifact = _make_ratebook_artifact()
        artifact["factor_tables"] = {}
        path = _write_artifact(artifact)
        try:
            df = pl.DataFrame({"x": [1, 2]})
            node = _make_node({"sourceType": "file", "artifact_path": path})
            _, fn, _ = _build_node_fn(node, source_names=["base"])
            result = fn(df.lazy()).collect()
            # Should pass through with version column added
            assert len(result) == 2
            assert "__optimiser_version__" in result.columns
        finally:
            os.unlink(path)


# ---------------------------------------------------------------------------
# apply helpers directly
# ---------------------------------------------------------------------------


class TestApplyOnlineHelper:
    def test_apply_online(self):
        artifact = _make_online_artifact()
        lf = _scored_df().lazy()
        result_lf = _apply_online(lf, artifact, "v1", "__ver__")
        result = result_lf.collect()
        assert len(result) == 2
        assert "__ver__" in result.columns

    def test_apply_online_no_version(self):
        artifact = _make_online_artifact()
        result = _apply_online(_scored_df().lazy(), artifact, "", "__ver__").collect()
        assert "__ver__" not in result.columns

    def test_apply_online_does_not_pass_chunk_size_to_price_contour_constructor(self):
        artifact = _make_online_artifact()
        result_df = pl.DataFrame(
            {
                "quote_id": ["q1"],
                "optimal_step": [1],
                "optimal_scenario_value": [1.0],
                "optimal_objective": [100.0],
                "optimal_predicted_volume": [0.9],
            }
        )

        with patch("price_contour.ApplyOptimiser") as mock_apply:
            mock_apply.return_value.apply.return_value = SimpleNamespace(dataframe=result_df)
            result = _apply_online(_scored_df().lazy(), artifact, "", "__ver__").collect()

        assert len(result) == 1
        assert "chunk_size" not in mock_apply.call_args.kwargs


class TestApplyRatebookHelper:
    def test_apply_ratebook(self):
        artifact = _make_ratebook_artifact()
        df = pl.DataFrame(
            {
                "region": ["London", "Manchester"],
                "price": [100.0, 200.0],
            }
        )
        result = _apply_ratebook(df.lazy(), artifact, "v1", "__ver__").collect()
        assert "region_optimised_factor" in result.columns
        assert "optimised_factor" in result.columns
        assert "__ver__" in result.columns

    def test_missing_factor_group_logs_warning(self):
        """Entries without __factor_group__ are skipped and a warning is logged."""
        from unittest.mock import patch

        artifact = {
            "version": "v1",
            "mode": "ratebook",
            "factor_tables": {
                "area": [
                    {"__factor_group__": "London", "optimal_scenario_value": 1.1},
                    # Missing __factor_group__ key:
                    {"optimal_scenario_value": 0.9},
                    {"bad_key": "X", "optimal_scenario_value": 0.8},
                ],
            },
        }
        df = pl.DataFrame({"area": ["London"]})

        with patch("haute._builders.logger") as mock_logger:
            result = _apply_ratebook(df.lazy(), artifact, "v1", "__ver__").collect()

        mock_logger.warning.assert_any_call(
            "ratebook_entries_missing_factor_group",
            factor="area",
            skipped=2,
            total=3,
        )
        # The one valid entry should still produce results
        assert "area_optimised_factor" in result.columns

    def test_all_entries_valid_no_warning(self):
        """When all entries have __factor_group__, no warning is logged."""
        from unittest.mock import patch

        artifact = _make_ratebook_artifact()
        df = pl.DataFrame(
            {
                "region": ["London", "Manchester"],
                "price": [100.0, 200.0],
            }
        )

        with patch("haute._builders.logger") as mock_logger:
            _apply_ratebook(df.lazy(), artifact, "v1", "__ver__").collect()

        # No call to warning with the skipped-entries event
        for call in mock_logger.warning.call_args_list:
            assert call[0][0] != "ratebook_entries_missing_factor_group"


# ---------------------------------------------------------------------------
# Deploy: bundler
# ---------------------------------------------------------------------------


class TestBundler:
    def test_collect_optimiser_apply_artifact(self):
        from haute._types import PipelineGraph
        from haute.deploy._bundler import collect_artifacts

        artifact = _make_online_artifact()
        path = _write_artifact(artifact)
        try:
            graph = PipelineGraph(
                nodes=[
                    GraphNode(
                        id="apply_1",
                        data=NodeData(
                            label="Apply Opt",
                            nodeType=NodeType.OPTIMISER_APPLY,
                            config={"sourceType": "file", "artifact_path": path},
                        ),
                    ),
                ],
                edges=[],
            )
            artifacts = collect_artifacts(graph, [], pipeline_dir=os.path.dirname(path))
            assert len(artifacts) == 1
            key = list(artifacts.keys())[0]
            assert key.startswith("apply_1__")
            assert artifacts[key].name.endswith(".json")
        finally:
            os.unlink(path)

    def test_bundler_skips_empty_path(self):
        from haute._types import PipelineGraph
        from haute.deploy._bundler import collect_artifacts

        graph = PipelineGraph(
            nodes=[
                GraphNode(
                    id="apply_1",
                    data=NodeData(
                        label="Apply Opt",
                        nodeType=NodeType.OPTIMISER_APPLY,
                        config={"sourceType": "file", "artifact_path": ""},
                    ),
                ),
            ],
            edges=[],
        )
        artifacts = collect_artifacts(graph, [], pipeline_dir="/tmp")
        assert len(artifacts) == 0
