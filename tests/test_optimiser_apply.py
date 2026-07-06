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
from haute.codegen import _generate_node_code, _node_to_code
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
                "optimised_value_column": "selected_price_factor",
            },
            body="",
            param_names=["df"],
        )
        assert config["sourceType"] == "file"
        assert config["artifact_path"] == "artifacts/opt_v1.json"
        assert config["version_column"] == "__opt_ver__"
        assert config["optimised_value_column"] == "selected_price_factor"

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

    def test_build_config_ratebook_input(self):
        config = _build_node_config(
            node_type=NodeType.OPTIMISER_APPLY,
            decorator_kwargs={
                "optimiser_apply": True,
                "source_type": "file",
                "artifact_path": "artifacts/ratebook.json",
                "ratebook_input": "banding-node",
            },
            body="",
            param_names=["scored_quotes", "banded_quotes"],
        )
        assert config["ratebook_input"] == "banding-node"


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
        # Body applies the artifact via the shared helper (not a no-op
        # passthrough) so a standalone pipeline.run() actually optimises.
        assert "apply_optimiser_apply_from_config(" in code
        assert "source_ids=['score_models']" in code

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

    def test_codegen_optimised_value_column(self):
        node = _make_node(
            {
                "sourceType": "file",
                "artifact_path": "a.json",
                "optimised_value_column": "selected_price_factor",
            },
        )
        code = _generate_node_code(node, source_names=["df"])
        assert "optimised_value_column='selected_price_factor'" in code

    def test_codegen_ratebook_input(self):
        node = _make_node(
            {
                "sourceType": "file",
                "artifact_path": "a.json",
                "ratebook_input": "banding-node",
            },
        )
        code = _generate_node_code(node, source_names=["scored_quotes", "banded_quotes"])
        assert "ratebook_input='banding-node'" in code


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

    def test_online_custom_optimised_value_column(self):
        path = _write_artifact(_make_online_artifact())
        try:
            node = _make_node(
                {
                    "sourceType": "file",
                    "artifact_path": path,
                    "optimised_value_column": "selected_price_factor",
                }
            )
            _, fn, _ = _build_node_fn(node, source_names=["scored"])
            result = fn(_scored_df().lazy()).collect()
            assert "selected_price_factor" in result.columns
            assert "optimal_scenario_value" not in result.columns
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

    def test_ratebook_custom_optimised_value_column(self):
        path = _write_artifact(_make_ratebook_artifact())
        try:
            df = pl.DataFrame(
                {
                    "quote_id": ["q1", "q2"],
                    "region": ["London", "Manchester"],
                    "price": [100.0, 200.0],
                }
            )
            node = _make_node(
                {
                    "sourceType": "file",
                    "artifact_path": path,
                    "optimised_value_column": "selected_price_factor",
                }
            )
            _, fn, _ = _build_node_fn(node, source_names=["base"])
            result = fn(df.lazy()).collect()
            assert "selected_price_factor" in result.columns
            assert "optimised_factor" not in result.columns
            assert result["selected_price_factor"].to_list() == pytest.approx([1.05, 0.98])
        finally:
            os.unlink(path)

    def test_ratebook_unseen_level_rates_neutral_and_warns(self):
        """3b.5: an unseen factor level still rates 1.0 (neutral relativity)
        but the miss is counted and logged — never silent."""
        import structlog.testing

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
            with structlog.testing.capture_logs() as logs:
                result = fn(df.lazy()).collect()
            assert result["region_optimised_factor"][0] == pytest.approx(1.0)
            assert result["optimised_factor"][0] == pytest.approx(1.0)
            miss_logs = [log for log in logs if log["event"] == "rating_table_lookup_misses"]
            assert len(miss_logs) == 1
            assert miss_logs[0]["log_level"] == "warning"
            assert miss_logs[0]["table"] == "region"
            assert miss_logs[0]["output_column"] == "region_optimised_factor"
            assert miss_logs[0]["miss_count"] == 1
            assert miss_logs[0]["missing_keys"] == [{"region": "Edinburgh"}]
        finally:
            os.unlink(path)

    def test_ratebook_seen_levels_do_not_warn(self):
        import structlog.testing

        path = _write_artifact(_make_ratebook_artifact())
        try:
            df = pl.DataFrame(
                {
                    "quote_id": ["q1", "q2"],
                    "region": ["London", "Manchester"],
                    "price": [100.0, 200.0],
                }
            )
            node = _make_node({"sourceType": "file", "artifact_path": path})
            _, fn, _ = _build_node_fn(node, source_names=["base"])
            with structlog.testing.capture_logs() as logs:
                result = fn(df.lazy()).collect()
            assert result["region_optimised_factor"].to_list() == pytest.approx([1.05, 0.98])
            assert [log for log in logs if log["event"] == "rating_table_lookup_misses"] == []
        finally:
            os.unlink(path)

    def test_ratebook_null_factor_value_rates_neutral_and_warns(self):
        """A null factor value can never match the lookup join — it is a
        counted neutral miss, not a silent 1.0 and not a crash."""
        import structlog.testing

        path = _write_artifact(_make_ratebook_artifact())
        try:
            df = pl.DataFrame(
                {
                    "quote_id": ["q1", "q2"],
                    "region": ["London", None],
                    "price": [100.0, 200.0],
                }
            )
            node = _make_node({"sourceType": "file", "artifact_path": path})
            _, fn, _ = _build_node_fn(node, source_names=["base"])
            with structlog.testing.capture_logs() as logs:
                result = fn(df.lazy()).collect()
            assert result["region_optimised_factor"].to_list() == pytest.approx([1.05, 1.0])
            miss_logs = [log for log in logs if log["event"] == "rating_table_lookup_misses"]
            assert len(miss_logs) == 1
            assert miss_logs[0]["miss_count"] == 1
        finally:
            os.unlink(path)

    def test_ratebook_partial_miss_combines_neutral_with_matched(self):
        """One table misses, the other matches: per-factor columns are
        [matched, 1.0] and the combined relativity is their product."""
        import structlog.testing

        artifact = _make_ratebook_artifact()
        artifact["factor_tables"]["age_band"] = [
            {"__factor_group__": "young", "optimal_scenario_value": 1.10},
        ]
        path = _write_artifact(artifact)
        try:
            df = pl.DataFrame(
                {
                    "quote_id": ["q1"],
                    "region": ["London"],
                    "age_band": ["unseen-band"],
                    "price": [100.0],
                }
            )
            node = _make_node({"sourceType": "file", "artifact_path": path})
            _, fn, _ = _build_node_fn(node, source_names=["base"])
            with structlog.testing.capture_logs() as logs:
                result = fn(df.lazy()).collect()
            assert result["region_optimised_factor"][0] == pytest.approx(1.05)
            assert result["age_band_optimised_factor"][0] == pytest.approx(1.0)
            assert result["optimised_factor"][0] == pytest.approx(1.05)
            miss_logs = [log for log in logs if log["event"] == "rating_table_lookup_misses"]
            assert [log["table"] for log in miss_logs] == ["age_band"]
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

    def test_ratebook_apply_uses_configured_input_dataframe(self):
        path = _write_artifact(_make_ratebook_artifact())
        try:
            scored_quotes = pl.DataFrame(
                {
                    "quote_id": ["q1", "q2"],
                    "region": ["London", "Manchester"],
                    "price": [100.0, 200.0],
                }
            )
            banded_quotes = pl.DataFrame(
                {
                    "quote_id": ["q1", "q2"],
                    "region": ["Manchester", "London"],
                    "price": [100.0, 200.0],
                }
            )
            node = _make_node(
                {
                    "sourceType": "file",
                    "artifact_path": path,
                    "ratebook_input": "banding-node",
                }
            )
            _, fn, _ = _build_node_fn(
                node,
                source_names=["scored_quotes", "banded_quotes"],
                source_ids=["scored-node", "banding-node"],
            )
            result = fn(scored_quotes.lazy(), banded_quotes.lazy()).collect()

            assert result["region"].to_list() == ["Manchester", "London"]
            assert result["region_optimised_factor"].to_list() == pytest.approx([0.98, 1.05])
        finally:
            os.unlink(path)

    def test_ratebook_apply_rejects_stale_configured_input(self):
        path = _write_artifact(_make_ratebook_artifact())
        try:
            node = _make_node(
                {
                    "sourceType": "file",
                    "artifact_path": path,
                    "ratebook_input": "deleted-banding-node",
                }
            )
            _, fn, _ = _build_node_fn(
                node,
                source_names=["scored_quotes", "banded_quotes"],
                source_ids=["scored-node", "banding-node"],
            )

            with pytest.raises(ValueError, match="deleted-banding-node"):
                fn(
                    pl.DataFrame({"region": ["London"]}).lazy(),
                    pl.DataFrame({"region": ["Manchester"]}).lazy(),
                ).collect()
        finally:
            os.unlink(path)

    def test_ratebook_apply_requires_source_ids_for_configured_input(self):
        path = _write_artifact(_make_ratebook_artifact())
        try:
            node = _make_node(
                {
                    "sourceType": "file",
                    "artifact_path": path,
                    "ratebook_input": "banded_quotes",
                }
            )
            _, fn, _ = _build_node_fn(
                node,
                source_names=["scored_quotes", "banded_quotes"],
            )

            with pytest.raises(ValueError, match="source_ids"):
                fn(
                    pl.DataFrame({"region": ["London"]}).lazy(),
                    pl.DataFrame({"region": ["Manchester"]}).lazy(),
                ).collect()
        finally:
            os.unlink(path)

    def test_online_apply_ignores_ratebook_input_and_uses_first_dataframe(self):
        path = _write_artifact(_make_online_artifact(lambdas={"predicted_volume": 0.0}))
        try:
            unusable_ratebook_input = pl.DataFrame({"region": ["London"]})
            node = _make_node(
                {
                    "sourceType": "file",
                    "artifact_path": path,
                    "ratebook_input": "unusable-ratebook-node",
                }
            )
            _, fn, _ = _build_node_fn(
                node,
                source_names=["scored_quotes", "unusable_ratebook_input"],
                source_ids=["scored-node", "unusable-ratebook-node"],
            )
            result = fn(_scored_df().lazy(), unusable_ratebook_input.lazy()).collect()

            assert len(result) == 2
            assert "optimal_scenario_value" in result.columns
        finally:
            os.unlink(path)

    def test_online_apply_uses_configured_optimised_value_column(self):
        path = _write_artifact(_make_online_artifact(lambdas={"predicted_volume": 0.0}))
        try:
            node = _make_node(
                {
                    "sourceType": "file",
                    "artifact_path": path,
                    "optimised_value_column": "selected_price_factor",
                }
            )
            _, fn, _ = _build_node_fn(node, source_names=["scored_quotes"])
            result = fn(_scored_df().lazy()).collect()

            assert "selected_price_factor" in result.columns
            assert "optimal_scenario_value" not in result.columns
        finally:
            os.unlink(path)


# ---------------------------------------------------------------------------
# Executor: ratebook composite factor groups (3b.2)
# ---------------------------------------------------------------------------

_SEP = "\x1f"  # price-contour's interaction (unit) separator


def _make_composite_artifact(version: str = "rb_comp_v1") -> dict:
    """Artifact with a composite group, exactly as the save path emits it:
    table name colon-joined, levels unit-separator-joined."""
    return {
        "version": version,
        "mode": "ratebook",
        "lambdas": {},
        "factor_tables": {
            "channel:age_band": [
                {
                    "__factor_group__": f"online{_SEP}18-25",
                    "optimal_scenario_value": 1.05,
                    "quote_count": 2,
                },
                {
                    "__factor_group__": f"phone{_SEP}18-25",
                    "optimal_scenario_value": 0.98,
                    "quote_count": 2,
                },
                {
                    "__factor_group__": f"online{_SEP}26-40",
                    "optimal_scenario_value": 1.10,
                    "quote_count": 2,
                },
            ],
        },
    }


class TestExecutorRatebookComposite:
    def test_composite_group_joins_on_component_columns(self):
        """3b.2 repro: a composite artifact must join channel+age_band, not a
        literal "channel:age_band" column (ColumnNotFoundError at HEAD)."""
        path = _write_artifact(_make_composite_artifact())
        try:
            df = pl.DataFrame(
                {
                    "quote_id": ["q1", "q2", "q3"],
                    "channel": ["online", "phone", "online"],
                    "age_band": ["18-25", "18-25", "26-40"],
                    "price": [100.0, 200.0, 300.0],
                }
            )
            node = _make_node({"sourceType": "file", "artifact_path": path})
            _, fn, _ = _build_node_fn(node, source_names=["base"])
            result = fn(df.lazy()).collect()
            assert result["channel:age_band_optimised_factor"].to_list() == pytest.approx(
                [1.05, 0.98, 1.10]
            )
            assert result["optimised_factor"].to_list() == pytest.approx([1.05, 0.98, 1.10])
            # Join columns keep their values and dtypes.
            assert result["channel"].to_list() == ["online", "phone", "online"]
            assert result["age_band"].to_list() == ["18-25", "18-25", "26-40"]
        finally:
            os.unlink(path)

    def test_composite_survives_json_round_trip(self):
        """The unit separator must survive json.dumps/load (\\u001f escape)."""
        artifact = _make_composite_artifact()
        rehydrated = json.loads(json.dumps(artifact))
        level = rehydrated["factor_tables"]["channel:age_band"][0]["__factor_group__"]
        assert level == f"online{_SEP}18-25"

    def test_three_component_composite(self):
        artifact = {
            "version": "v1",
            "mode": "ratebook",
            "factor_tables": {
                "a:b:c": [
                    {
                        "__factor_group__": f"x{_SEP}y{_SEP}z",
                        "optimal_scenario_value": 1.25,
                    },
                ],
            },
        }
        df = pl.DataFrame({"a": ["x"], "b": ["y"], "c": ["z"]})
        result = _apply_ratebook(df.lazy(), artifact, "v1", "__ver__").collect()
        assert result["a:b:c_optimised_factor"].to_list() == pytest.approx([1.25])

    def test_composite_mixed_with_single_column_table(self):
        artifact = _make_composite_artifact()
        artifact["factor_tables"]["region"] = [
            {"__factor_group__": "London", "optimal_scenario_value": 1.20},
        ]
        path = _write_artifact(artifact)
        try:
            df = pl.DataFrame(
                {
                    "quote_id": ["q1"],
                    "channel": ["online"],
                    "age_band": ["18-25"],
                    "region": ["London"],
                    "price": [100.0],
                }
            )
            node = _make_node({"sourceType": "file", "artifact_path": path})
            _, fn, _ = _build_node_fn(node, source_names=["base"])
            result = fn(df.lazy()).collect()
            assert result["channel:age_band_optimised_factor"][0] == pytest.approx(1.05)
            assert result["region_optimised_factor"][0] == pytest.approx(1.20)
            assert result["optimised_factor"][0] == pytest.approx(1.05 * 1.20)
        finally:
            os.unlink(path)

    def test_composite_unseen_combination_rates_neutral_and_warns(self):
        """A channel/age_band pair the solver never saw is a counted neutral
        miss, even when each component value exists in other combinations."""
        import structlog.testing

        artifact = _make_composite_artifact()
        df = pl.DataFrame(
            {
                "quote_id": ["q1"],
                "channel": ["phone"],
                "age_band": ["26-40"],  # phone x 26-40 not in the table
                "price": [100.0],
            }
        )
        with structlog.testing.capture_logs() as logs:
            result = _apply_ratebook(df.lazy(), artifact, "v1", "__ver__").collect()
        assert result["channel:age_band_optimised_factor"][0] == pytest.approx(1.0)
        miss_logs = [log for log in logs if log["event"] == "rating_table_lookup_misses"]
        assert len(miss_logs) == 1
        assert miss_logs[0]["table"] == "channel:age_band"
        assert miss_logs[0]["missing_keys"] == [{"channel": "phone", "age_band": "26-40"}]

    def test_composite_numeric_component_column_matches(self):
        """Component parts are strings; an int-like numeric frame column must
        still match its digit-string part (W3a key normalisation)."""
        artifact = {
            "version": "v1",
            "mode": "ratebook",
            "factor_tables": {
                "channel:age": [
                    {"__factor_group__": f"online{_SEP}25", "optimal_scenario_value": 1.15},
                ],
            },
        }
        df = pl.DataFrame({"channel": ["online"], "age": [25]})
        result = _apply_ratebook(df.lazy(), artifact, "v1", "__ver__").collect()
        assert result["channel:age_optimised_factor"].to_list() == pytest.approx([1.15])
        assert result["age"].dtype == pl.Int64  # dtype reverted after the join

    def test_literal_colon_column_without_separator_levels_joins_literally(self):
        """A table whose name contains ":" but whose levels carry no unit
        separator is a literal single column named "a:b" — joined as such."""
        artifact = {
            "version": "v1",
            "mode": "ratebook",
            "factor_tables": {
                "channel:age_band": [
                    {"__factor_group__": "combo-1", "optimal_scenario_value": 1.30},
                ],
            },
        }
        df = pl.DataFrame({"channel:age_band": ["combo-1", "combo-2"]})
        result = _apply_ratebook(df.lazy(), artifact, "v1", "__ver__").collect()
        assert result["channel:age_band_optimised_factor"].to_list()[0] == pytest.approx(1.30)


class TestRatebookCompositeContractErrors:
    def test_level_arity_mismatch_raises(self):
        artifact = {
            "version": "v1",
            "mode": "ratebook",
            "factor_tables": {
                "channel:age_band": [
                    {
                        "__factor_group__": f"online{_SEP}18-25{_SEP}extra",
                        "optimal_scenario_value": 1.0,
                    },
                ],
            },
        }
        df = pl.DataFrame({"channel": ["online"], "age_band": ["18-25"]})
        with pytest.raises(ValueError, match="channel:age_band"):
            _apply_ratebook(df.lazy(), artifact, "v1", "__ver__")

    def test_separator_levels_under_non_composite_name_raise(self):
        artifact = {
            "version": "v1",
            "mode": "ratebook",
            "factor_tables": {
                "region": [
                    {"__factor_group__": f"north{_SEP}east", "optimal_scenario_value": 1.0},
                ],
            },
        }
        df = pl.DataFrame({"region": ["north"]})
        with pytest.raises(ValueError, match="'region'"):
            _apply_ratebook(df.lazy(), artifact, "v1", "__ver__")

    def test_duplicate_component_columns_raise(self):
        artifact = {
            "version": "v1",
            "mode": "ratebook",
            "factor_tables": {
                "a:a": [
                    {"__factor_group__": f"x{_SEP}y", "optimal_scenario_value": 1.0},
                ],
            },
        }
        df = pl.DataFrame({"a": ["x"]})
        with pytest.raises(ValueError, match="'a:a'"):
            _apply_ratebook(df.lazy(), artifact, "v1", "__ver__")

    def test_empty_component_column_raises(self):
        artifact = {
            "version": "v1",
            "mode": "ratebook",
            "factor_tables": {
                "channel:": [
                    {"__factor_group__": f"online{_SEP}x", "optimal_scenario_value": 1.0},
                ],
            },
        }
        df = pl.DataFrame({"channel": ["online"]})
        with pytest.raises(ValueError, match="'channel:'"):
            _apply_ratebook(df.lazy(), artifact, "v1", "__ver__")

    def test_missing_component_column_raises_named_error(self):
        """A frame without one component column fails at apply with an error
        naming the table and the missing column — not a bare
        ColumnNotFoundError from inside the join at collect time."""
        artifact = _make_composite_artifact()
        df = pl.DataFrame({"channel": ["online"], "price": [100.0]})
        with pytest.raises(ValueError, match="age_band") as excinfo:
            _apply_ratebook(df.lazy(), artifact, "v1", "__ver__")
        assert "channel:age_band" in str(excinfo.value)

    def test_missing_single_factor_column_raises_named_error(self):
        """Single-column tables get the same clear contract error."""
        artifact = _make_ratebook_artifact()
        df = pl.DataFrame({"quote_id": ["q1"], "price": [100.0]})
        with pytest.raises(ValueError, match="'region'"):
            _apply_ratebook(df.lazy(), artifact, "v1", "__ver__")


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

    def test_apply_online_passes_only_documented_kwargs_to_price_contour_constructor(self):
        """ApplyOptimiser's signature is the price-contour ABI we depend on.

        The wrapper must (a) NOT pass extra kwargs the upstream API doesn't
        accept (chunk_size used to be silently swallowed; now it'd fail
        loudly), and (b) pass exactly the documented six kwargs sourced from
        the artifact, in the right shape.  Each of those is independently
        regression-prone, so assert all of them — not just the absence of
        chunk_size.

        We also assert the wrapper still applies the version column to the
        returned dataframe so a "function returns None / wrong shape"
        mutation cannot pass this test.
        """
        artifact = _make_online_artifact()
        # Use a marker value so we can assert the wrapper fed it through.
        result_df = pl.DataFrame(
            {
                "quote_id": ["q-marker-123"],
                "optimal_step": [1],
                "optimal_scenario_value": [1.0],
                "optimal_objective": [100.0],
                "optimal_predicted_volume": [0.9],
            }
        )

        with patch("price_contour.ApplyOptimiser") as mock_apply:
            mock_apply.return_value.apply.return_value = SimpleNamespace(dataframe=result_df)
            result = _apply_online(
                _scored_df().lazy(),
                artifact,
                "v3.7.1",
                "__ver__",
            ).collect()

        # ── (a) Constructor was called exactly once with the documented kwargs. ──
        mock_apply.assert_called_once()
        call_kwargs = mock_apply.call_args.kwargs
        # Positive: every required kwarg present with the artifact's value.
        assert call_kwargs == {
            "lambdas": artifact["lambdas"],
            "objective": artifact["objective"],
            "constraints": artifact["constraints"],
            "quote_id": artifact["quote_id"],
            "scenario_index": artifact["scenario_index"],
            "scenario_value": artifact["scenario_value"],
        }
        # No positional args slipped in.
        assert mock_apply.call_args.args == ()
        # Belt-and-braces explicit no-chunk_size assertion (the regression bug).
        assert "chunk_size" not in call_kwargs

        # ── (b) Wrapper threaded the result dataframe through correctly. ──
        # Marker quote_id flows from the apply() return into the final result.
        assert result["quote_id"].to_list() == ["q-marker-123"]
        # Version column was appended with the exact value we passed in.
        assert "__ver__" in result.columns
        assert result["__ver__"].to_list() == ["v3.7.1"]

        # ── (c) Apply was driven with the SAME projected dataframe shape. ──
        # The wrapper casts qid/step/value/objective to the types
        # price-contour expects.  Verify the dataframe handed to apply()
        # has those casts (regression: someone removes the .cast pipeline).
        applied_df = mock_apply.return_value.apply.call_args.args[0]
        assert applied_df.schema["quote_id"] == pl.Utf8
        assert applied_df.schema["scenario_index"] == pl.Int32
        assert applied_df.schema["scenario_value"] == pl.Float32
        assert applied_df.schema[artifact["objective"]] == pl.Float32
        # Constraint columns are also cast to Float32 for the price-contour API.
        for cname in artifact["constraints"]:
            assert applied_df.schema[cname] == pl.Float32


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

    def test_apply_ratebook_uses_configured_optimised_value_column(self):
        artifact = _make_ratebook_artifact()
        df = pl.DataFrame(
            {
                "region": ["London", "Manchester"],
                "price": [100.0, 200.0],
            }
        )
        result = _apply_ratebook(
            df.lazy(),
            artifact,
            "v1",
            "__ver__",
            "selected_price_factor",
        ).collect()

        assert "region_optimised_factor" in result.columns
        assert "selected_price_factor" in result.columns
        assert "optimised_factor" not in result.columns
        assert result["selected_price_factor"].to_list() == pytest.approx([1.05, 0.98])

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
