"""Tests for the rating step node: executor, parser, and codegen."""

from __future__ import annotations

import polars as pl
import pytest

from haute.codegen import graph_to_code
from haute.executor import _build_node_fn
from haute.graph_utils import GraphEdge, GraphNode, NodeData, NodeType, PipelineGraph
from haute.parser import parse_pipeline_source
from tests.conftest import make_source_node as _source_node
from tests.conftest import write_node_config


def _assert_code_equal(actual: str, expected: str) -> None:
    """Compare code snippets by syntax, ignoring harmless formatting differences."""
    import ast

    assert ast.dump(ast.parse(actual)) == ast.dump(ast.parse(expected))


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _rating_node(
    nid: str,
    tables: list[dict] | None = None,
    operation: str = "multiply",
    combined_column: str = "",
    combined_outputs: list[dict] | None = None,
    code: str = "",
) -> GraphNode:
    cfg: dict = {"tables": tables or []}
    if operation != "multiply":
        cfg["operation"] = operation
    if combined_column:
        cfg["combinedColumn"] = combined_column
    if combined_outputs is not None:
        cfg["combinedOutputs"] = combined_outputs
    if code:
        cfg["code"] = code
    return GraphNode(
        id=nid,
        data=NodeData(
            label=nid,
            nodeType="ratingStep",
            config=cfg,
        ),
    )


# ---------------------------------------------------------------------------
# Executor: _apply_rating_table via _build_node_fn
# ---------------------------------------------------------------------------


class TestRatingStepExecutor:
    """Rating step executor applies lookup joins correctly."""

    def test_one_way_lookup(self):
        """Single-factor table: join on one column."""
        tables = [
            {
                "name": "Age Factor",
                "factors": ["age_band"],
                "outputColumn": "age_factor",
                "defaultValue": "1.0",
                "entries": [
                    {"age_band": "young", "value": 1.3},
                    {"age_band": "older", "value": 0.9},
                ],
            }
        ]
        node = _rating_node("r1", tables)
        _, fn, _ = _build_node_fn(node)
        lf = pl.DataFrame({"age_band": ["young", "older", "unknown"]}).lazy()
        result = fn(lf).collect()
        assert result["age_factor"].to_list() == [1.3, 0.9, 1.0]

    def test_two_way_lookup(self):
        """Two-factor table: join on two columns."""
        # PIN REVISION (3a.3): the older/Flat miss needs the explicit
        # onMissing="neutral" opt-in; the default is fail-loud
        # (tests/test_rating_miss_fail_loud.py).
        tables = [
            {
                "name": "Age × Prop",
                "factors": ["age_band", "prop_band"],
                "outputColumn": "factor",
                "defaultValue": None,
                "onMissing": "neutral",
                "entries": [
                    {"age_band": "young", "prop_band": "House", "value": 1.2},
                    {"age_band": "young", "prop_band": "Flat", "value": 1.5},
                    {"age_band": "older", "prop_band": "House", "value": 0.9},
                ],
            }
        ]
        node = _rating_node("r2", tables)
        _, fn, _ = _build_node_fn(node)
        lf = pl.DataFrame(
            {
                "age_band": ["young", "young", "older", "older"],
                "prop_band": ["House", "Flat", "House", "Flat"],
            }
        ).lazy()
        result = fn(lf).collect()
        assert result["factor"].to_list() == [1.2, 1.5, 0.9, None]

    def test_three_way_lookup(self):
        """Three-factor table: join on three columns."""
        tables = [
            {
                "name": "3-way",
                "factors": ["a", "b", "c"],
                "outputColumn": "val",
                "defaultValue": "0.0",
                "entries": [
                    {"a": "x", "b": "y", "c": "z", "value": 2.5},
                ],
            }
        ]
        node = _rating_node("r3", tables)
        _, fn, _ = _build_node_fn(node)
        lf = pl.DataFrame(
            {
                "a": ["x", "x"],
                "b": ["y", "y"],
                "c": ["z", "w"],
            }
        ).lazy()
        result = fn(lf).collect()
        assert result["val"].to_list() == [2.5, 0.0]

    def test_multiple_tables(self):
        """Multiple tables in one node, each produces its own output column."""
        tables = [
            {
                "name": "T1",
                "factors": ["band"],
                "outputColumn": "f1",
                "defaultValue": "1.0",
                "entries": [{"band": "A", "value": 2.0}],
            },
            {
                "name": "T2",
                "factors": ["band"],
                "outputColumn": "f2",
                "defaultValue": "1.0",
                "entries": [{"band": "A", "value": 3.0}],
            },
        ]
        node = _rating_node("r4", tables)
        _, fn, _ = _build_node_fn(node)
        lf = pl.DataFrame({"band": ["A", "B"]}).lazy()
        result = fn(lf).collect()
        assert result["f1"].to_list() == [2.0, 1.0]
        assert result["f2"].to_list() == [3.0, 1.0]

    def test_empty_tables_passthrough(self):
        """Rating node with no tables passes through unchanged."""
        node = _rating_node("r5", [])
        _, fn, _ = _build_node_fn(node)
        lf = pl.DataFrame({"x": [1, 2]}).lazy()
        result = fn(lf).collect()
        assert result.columns == ["x"]

    def test_incomplete_table_skipped(self):
        """Table with missing factors/entries/outputColumn is skipped."""
        tables = [
            {
                "name": "bad",
                "factors": [],
                "outputColumn": "out",
                "defaultValue": None,
                "entries": [{"value": 1.0}],
            },
        ]
        node = _rating_node("r6", tables)
        _, fn, _ = _build_node_fn(node)
        lf = pl.DataFrame({"x": [1]}).lazy()
        result = fn(lf).collect()
        assert result.columns == ["x"]

    def test_incomplete_table_not_registered_for_combined_output(self):
        """F082: an incomplete table is a passthrough (no output column), so it
        must NOT be registered for a combined output — otherwise the combine
        references a phantom column that never materialised (crash or silent
        mispricing).  The combined output uses only the base value plus the
        columns that actually exist."""
        from haute._rating import apply_rating_step_from_config

        config = {
            "tables": [
                # Incomplete: empty entries -> passthrough, no "phantom" column.
                {"name": "empty", "factors": ["b"], "outputColumn": "phantom", "entries": []},
                # Real table producing "factor".
                {
                    "name": "real",
                    "factors": ["b"],
                    "outputColumn": "factor",
                    "entries": [{"b": "A", "value": 2.0}],
                },
            ],
            "combinedOutputs": [
                {"outputColumn": "premium", "operation": "multiply", "baseValue": 100}
            ],
        }
        out = apply_rating_step_from_config(pl.DataFrame({"b": ["A"]}).lazy(), config).collect()
        assert "phantom" not in out.columns
        # premium = base 100 * real factor 2.0 (phantom excluded, no crash).
        assert out["premium"].to_list() == [200.0]

    @pytest.mark.parametrize(
        "operation, col_name, expected",
        [
            ("multiply", "combined", [6.0, 1.0]),
            ("add", "total", [5.0, 2.0]),
            ("min", "mn", [2.0, 1.0]),
            ("max", "mx", [3.0, 1.0]),
        ],
        ids=["multiply", "add", "min", "max"],
    )
    def test_combine_operations(self, operation, col_name, expected):
        """Two tables combined via the given operation."""
        tables = [
            {
                "name": "T1",
                "factors": ["band"],
                "outputColumn": "f1",
                "defaultValue": "1.0",
                "entries": [{"band": "A", "value": 2.0}],
            },
            {
                "name": "T2",
                "factors": ["band"],
                "outputColumn": "f2",
                "defaultValue": "1.0",
                "entries": [{"band": "A", "value": 3.0}],
            },
        ]
        node = _rating_node(
            f"rc_{operation}",
            tables,
            operation=operation,
            combined_column=col_name,
        )
        _, fn, _ = _build_node_fn(node)
        lf = pl.DataFrame({"band": ["A", "B"]}).lazy()
        result = fn(lf).collect()
        assert result[col_name].to_list() == expected

    def test_no_combined_column_skips_combine(self):
        """Without combinedColumn, no combination column is created."""
        tables = [
            {
                "name": "T1",
                "factors": ["band"],
                "outputColumn": "f1",
                "defaultValue": "1.0",
                "entries": [{"band": "A", "value": 2.0}],
            },
            {
                "name": "T2",
                "factors": ["band"],
                "outputColumn": "f2",
                "defaultValue": "1.0",
                "entries": [{"band": "A", "value": 3.0}],
            },
        ]
        node = _rating_node("rc5", tables)
        _, fn, _ = _build_node_fn(node)
        lf = pl.DataFrame({"band": ["A"]}).lazy()
        result = fn(lf).collect()
        assert "combined" not in result.columns
        assert result.columns == ["band", "f1", "f2"]

    def test_empty_combined_outputs_without_legacy_column_is_noop(self):
        """Explicit combinedOutputs=[] with no legacy column creates no combined output."""
        tables = [
            {
                "name": "T1",
                "factors": ["band"],
                "outputColumn": "f1",
                "defaultValue": "1.0",
                "entries": [{"band": "A", "value": 2.0}],
            },
            {
                "name": "T2",
                "factors": ["band"],
                "outputColumn": "f2",
                "defaultValue": "1.0",
                "entries": [{"band": "A", "value": 3.0}],
            },
        ]
        node = _rating_node("empty_outputs_no_legacy", tables, combined_outputs=[])
        _, fn, _ = _build_node_fn(node)
        result = fn(pl.DataFrame({"band": ["A"]}).lazy()).collect()

        assert result.columns == ["band", "f1", "f2"]

    def test_empty_combined_outputs_with_blank_legacy_column_is_noop(self):
        """A blank legacy combinedColumn is treated as unconfigured."""
        tables = [
            {
                "name": "T1",
                "factors": ["band"],
                "outputColumn": "f1",
                "defaultValue": "1.0",
                "entries": [{"band": "A", "value": 2.0}],
            },
            {
                "name": "T2",
                "factors": ["band"],
                "outputColumn": "f2",
                "defaultValue": "1.0",
                "entries": [{"band": "A", "value": 3.0}],
            },
        ]
        node = _rating_node(
            "empty_outputs_blank_legacy",
            tables,
            combined_column="  ",
            combined_outputs=[],
        )
        _, fn, _ = _build_node_fn(node)
        result = fn(pl.DataFrame({"band": ["A"]}).lazy()).collect()

        assert result.columns == ["band", "f1", "f2"]

    def test_empty_combined_outputs_preserves_legacy_combined_column(self):
        """Default empty combinedOutputs must not disable legacy combinedColumn."""
        tables = [
            {
                "name": "T1",
                "factors": ["band"],
                "outputColumn": "f1",
                "defaultValue": "1.0",
                "entries": [{"band": "A", "value": 2.0}],
            },
            {
                "name": "T2",
                "factors": ["band"],
                "outputColumn": "f2",
                "defaultValue": "1.0",
                "entries": [{"band": "A", "value": 3.0}],
            },
        ]
        node = _rating_node(
            "legacy_empty_outputs",
            tables,
            combined_column="combined",
            combined_outputs=[],
        )
        _, fn, _ = _build_node_fn(node)
        result = fn(pl.DataFrame({"band": ["A", "B"]}).lazy()).collect()

        assert result["combined"].to_list() == [6.0, 1.0]

    def test_empty_combined_outputs_without_legacy_is_noop(self):
        """An empty combinedOutputs list is a valid optional no-op."""
        tables = [
            {
                "name": "T1",
                "factors": ["band"],
                "outputColumn": "f1",
                "defaultValue": "1.0",
                "entries": [{"band": "A", "value": 2.0}],
            }
        ]
        node = _rating_node("optional_combined", tables, combined_outputs=[])
        _, fn, _ = _build_node_fn(node)
        result = fn(pl.DataFrame({"band": ["A", "B"]}).lazy()).collect()

        assert result.columns == ["band", "f1"]
        assert result["f1"].to_list() == [2.0, 1.0]

    def test_string_factor_values_match(self):
        """Factor values are cast to Utf8 so string bands match."""
        # PIN REVISION (3a.3): the miss on band 2 needs onMissing="neutral".
        tables = [
            {
                "name": "T",
                "factors": ["band"],
                "outputColumn": "out",
                "defaultValue": None,
                "onMissing": "neutral",
                "entries": [{"band": "1", "value": 9.9}],
            }
        ]
        node = _rating_node("r7", tables)
        _, fn, _ = _build_node_fn(node)
        # Source has integer column — should still match via Utf8 cast
        lf = pl.DataFrame({"band": [1, 2]}).lazy()
        result = fn(lf).collect()
        assert result["out"].to_list() == [9.9, None]

    def test_multiple_combined_outputs_with_base_values(self):
        """New configs can produce several rating outputs from the same tables."""
        tables = [
            {
                "name": "T1",
                "factors": ["band"],
                "outputColumn": "f1",
                "defaultValue": "1.0",
                "entries": [{"band": "A", "value": 2.0}],
            },
            {
                "name": "T2",
                "factors": ["band"],
                "outputColumn": "f2",
                "defaultValue": "1.0",
                "entries": [{"band": "A", "value": 3.0}],
            },
        ]
        node = _rating_node(
            "multi_outputs",
            tables,
            combined_outputs=[
                {"outputColumn": "technical_premium", "operation": "multiply", "baseValue": 100},
                {"outputColumn": "additive_score", "operation": "add", "baseValue": 10},
                {"outputColumn": "minimum_factor", "operation": "min", "baseValue": 1.5},
                {"outputColumn": "maximum_factor", "operation": "max", "baseValue": 1.5},
            ],
        )
        _, fn, _ = _build_node_fn(node)
        result = fn(pl.DataFrame({"band": ["A", "B"]}).lazy()).collect()

        assert result["technical_premium"].to_list() == [600.0, 100.0]
        assert result["additive_score"].to_list() == [15.0, 12.0]
        assert result["minimum_factor"].to_list() == [1.5, 1.0]
        assert result["maximum_factor"].to_list() == [3.0, 1.5]

    def test_legacy_combined_column_runs_alongside_new_combined_outputs(self):
        """A legacy combinedColumn is preserved when new combinedOutputs are added."""
        tables = [
            {
                "name": "T1",
                "factors": ["band"],
                "outputColumn": "f1",
                "defaultValue": "1.0",
                "entries": [{"band": "A", "value": 2.0}],
            },
            {
                "name": "T2",
                "factors": ["band"],
                "outputColumn": "f2",
                "defaultValue": "1.0",
                "entries": [{"band": "A", "value": 3.0}],
            },
        ]
        node = _rating_node(
            "legacy_and_new_outputs",
            tables,
            operation="min",
            combined_column="legacy_min",
            combined_outputs=[{"outputColumn": "new_total", "operation": "add", "baseValue": 10}],
        )
        _, fn, _ = _build_node_fn(node)
        result = fn(pl.DataFrame({"band": ["A", "B"]}).lazy()).collect()

        assert result["legacy_min"].to_list() == [2.0, 1.0]
        assert result["new_total"].to_list() == [15.0, 12.0]

    def test_combined_output_base_value_can_create_base_only_output(self):
        """A numeric base value is enough to create a combined output."""
        node = _rating_node(
            "base_only_output",
            [],
            combined_outputs=[
                {"outputColumn": "technical_premium", "operation": "multiply", "baseValue": 100}
            ],
        )
        _, fn, _ = _build_node_fn(node)
        result = fn(pl.DataFrame({"quote_id": [1, 2]}).lazy()).collect()

        assert result["technical_premium"].to_list() == [100.0, 100.0]

    def test_rating_code_runs_after_tables_and_combined_outputs(self):
        """User code can post-process columns created by the rating step."""
        tables = [
            {
                "name": "T1",
                "factors": ["band"],
                "outputColumn": "factor",
                "defaultValue": "1.0",
                "entries": [{"band": "A", "value": 2.0}],
            }
        ]
        node = _rating_node(
            "rating_code",
            tables,
            combined_outputs=[
                {"outputColumn": "premium", "operation": "multiply", "baseValue": 100}
            ],
            code="df = df.with_columns((pl.col('premium') + 5).alias('final_premium'))",
        )
        _, fn, _ = _build_node_fn(node)
        result = fn(pl.DataFrame({"band": ["A", "B"]}).lazy()).collect()

        assert result["premium"].to_list() == [200.0, 100.0]
        assert result["final_premium"].to_list() == [205.0, 105.0]

    def test_combined_outputs_invalid_operation_raises(self):
        """New combined outputs fail loudly on misspelled operations."""
        node = _rating_node(
            "bad_operation",
            [
                {
                    "name": "T",
                    "factors": ["band"],
                    "outputColumn": "factor",
                    "entries": [{"band": "A", "value": 2.0}],
                }
            ],
            combined_outputs=[{"outputColumn": "premium", "operation": "divide", "baseValue": 100}],
        )
        with pytest.raises(ValueError, match="Unsupported rating combine operation"):
            _build_node_fn(node)

    def test_legacy_combined_column_invalid_operation_raises(self):
        """Legacy combinedColumn configs also fail loudly on misspelled operations."""
        tables = [
            {
                "name": "T1",
                "factors": ["band"],
                "outputColumn": "f1",
                "defaultValue": "1.0",
                "entries": [{"band": "A", "value": 2.0}],
            },
            {
                "name": "T2",
                "factors": ["band"],
                "outputColumn": "f2",
                "defaultValue": "1.0",
                "entries": [{"band": "A", "value": 3.0}],
            },
        ]
        node = _rating_node(
            "bad_legacy_operation",
            tables,
            operation="divide",
            combined_column="premium",
        )

        with pytest.raises(ValueError, match="Unsupported rating combine operation"):
            _build_node_fn(node)

    def test_combined_outputs_missing_base_value_raises(self):
        """New combined outputs require an explicit numeric base value."""
        node = _rating_node(
            "missing_base",
            [],
            combined_outputs=[{"outputColumn": "premium", "operation": "multiply"}],
        )
        with pytest.raises(ValueError, match="requires baseValue"):
            _build_node_fn(node)

    def test_combined_outputs_boolean_base_value_raises(self):
        """Booleans are not accepted as numeric base values."""
        node = _rating_node(
            "boolean_base",
            [],
            combined_outputs=[
                {"outputColumn": "premium", "operation": "multiply", "baseValue": True}
            ],
        )
        with pytest.raises(ValueError, match="requires baseValue"):
            _build_node_fn(node)

    def test_combined_outputs_blank_output_column_raises(self):
        """Configured combined outputs must have an explicit output column."""
        node = _rating_node(
            "blank_output_column",
            [],
            combined_outputs=[{"outputColumn": " ", "operation": "multiply", "baseValue": 100}],
        )
        with pytest.raises(ValueError, match="requires outputColumn"):
            _build_node_fn(node)

    def test_combined_outputs_duplicate_output_column_raises(self):
        """New combined outputs cannot overwrite table or combined outputs."""
        node = _rating_node(
            "duplicate_output",
            [
                {
                    "name": "T",
                    "factors": ["band"],
                    "outputColumn": "premium",
                    "entries": [{"band": "A", "value": 2.0}],
                }
            ],
            combined_outputs=[
                {"outputColumn": "premium", "operation": "multiply", "baseValue": 100}
            ],
        )
        with pytest.raises(ValueError, match="duplicates another rating output column"):
            _build_node_fn(node)


# ---------------------------------------------------------------------------
# Parser
# ---------------------------------------------------------------------------


class TestRatingStepParser:
    """Parser extracts tables config from decorated functions."""

    def test_parse_rating_step(self, tmp_path):
        rating_config = write_node_config(
            tmp_path,
            NodeType.RATING_STEP,
            "rating",
            {
                "tables": [
                    {
                        "name": "T1",
                        "factors": ["age"],
                        "outputColumn": "af",
                        "defaultValue": 1.0,
                        "entries": [{"age": "young", "value": 1.5}],
                    }
                ]
            },
        )
        code = f'''
import polars as pl
from haute import pipeline

@pipeline.rating_step(config="{rating_config}")
def rating(df: pl.LazyFrame) -> pl.LazyFrame:
    """Apply rating."""
    return df
'''
        parsed = parse_pipeline_source(code, _base_dir=tmp_path)
        assert len(parsed.nodes) == 1
        n = parsed.nodes[0]
        assert n.data.nodeType == "ratingStep"
        tables = n.data.config["tables"]
        assert len(tables) == 1
        assert tables[0]["factors"] == ["age"]
        assert tables[0]["outputColumn"] == "af"
        assert tables[0]["defaultValue"] == 1.0
        assert len(tables[0]["entries"]) == 1

    def test_parse_empty_tables(self, tmp_path):
        rating_config = write_node_config(
            tmp_path,
            NodeType.RATING_STEP,
            "rating",
            {"tables": []},
        )
        code = f'''
import polars as pl
from haute import pipeline

@pipeline.rating_step(config="{rating_config}")
def rating(df: pl.LazyFrame) -> pl.LazyFrame:
    """Empty."""
    return df
'''
        parsed = parse_pipeline_source(code, _base_dir=tmp_path)
        n = parsed.nodes[0]
        assert n.data.nodeType == "ratingStep"
        assert n.data.config["tables"] == []

    def test_parse_operation_and_combined(self, tmp_path):
        rating_config = write_node_config(
            tmp_path,
            NodeType.RATING_STEP,
            "rating",
            {
                "tables": [
                    {
                        "name": "T1",
                        "factors": ["band"],
                        "outputColumn": "f1",
                        "entries": [{"band": "A", "value": 1.0}],
                    },
                    {
                        "name": "T2",
                        "factors": ["band"],
                        "outputColumn": "f2",
                        "entries": [{"band": "A", "value": 2.0}],
                    },
                ],
                "operation": "add",
                "combinedColumn": "total",
            },
        )
        code = f'''
import polars as pl
from haute import pipeline

@pipeline.rating_step(config="{rating_config}")
def rating(df: pl.LazyFrame) -> pl.LazyFrame:
    """Combined."""
    return df
'''
        parsed = parse_pipeline_source(code, _base_dir=tmp_path)
        n = parsed.nodes[0]
        assert n.data.config["operation"] == "add"
        assert n.data.config["combinedColumn"] == "total"

    def test_parse_combined_outputs_and_code_from_body(self, tmp_path):
        rating_config = write_node_config(
            tmp_path,
            NodeType.RATING_STEP,
            "rating",
            {
                "tables": [],
                "combinedOutputs": [
                    {"outputColumn": "premium", "operation": "multiply", "baseValue": 100}
                ],
            },
        )
        code = f'''
import polars as pl
from haute import pipeline

@pipeline.rating_step(config="{rating_config}")
def rating(df: pl.LazyFrame) -> pl.LazyFrame:
    """Combined outputs."""
    df = df.with_columns(pl.lit(1).alias("after_rating"))
    return df
'''
        parsed = parse_pipeline_source(code, _base_dir=tmp_path)
        n = parsed.nodes[0]
        assert n.data.config["combinedOutputs"] == [
            {"outputColumn": "premium", "operation": "multiply", "baseValue": 100}
        ]
        assert "after_rating" in n.data.config["code"]

    def test_parse_rating_return_from_input_rebases_to_post_rating_df(self, tmp_path):
        """Handwritten return input.with_columns(...) code must run against rated df."""
        rating_config = write_node_config(
            tmp_path,
            NodeType.RATING_STEP,
            "rating",
            {
                "tables": [],
                "combinedOutputs": [
                    {"outputColumn": "premium", "operation": "multiply", "baseValue": 100}
                ],
            },
        )
        code = f'''
import polars as pl
from haute import pipeline

@pipeline.rating_step(config="{rating_config}")
def rating(source: pl.LazyFrame) -> pl.LazyFrame:
    """Combined outputs."""
    return source.with_columns((pl.col("premium") + 5).alias("final_premium"))
'''
        parsed = parse_pipeline_source(code, _base_dir=tmp_path)
        n = parsed.nodes[0]

        _assert_code_equal(
            n.data.config["code"],
            'df = df.with_columns((pl.col("premium") + 5).alias("final_premium"))',
        )

    def test_parse_rating_alias_from_input_rebases_to_post_rating_df(self, tmp_path):
        """Alias patterns from the input parameter also target the rated df."""
        rating_config = write_node_config(
            tmp_path,
            NodeType.RATING_STEP,
            "rating",
            {
                "tables": [],
                "combinedOutputs": [
                    {"outputColumn": "premium", "operation": "multiply", "baseValue": 100}
                ],
            },
        )
        code = f'''
import polars as pl
from haute import pipeline

@pipeline.rating_step(config="{rating_config}")
def rating(source: pl.LazyFrame) -> pl.LazyFrame:
    """Combined outputs."""
    out = source.with_columns((pl.col("premium") + 5).alias("final_premium"))
    return out
'''
        parsed = parse_pipeline_source(code, _base_dir=tmp_path)
        n = parsed.nodes[0]

        _assert_code_equal(
            n.data.config["code"],
            'out = df.with_columns((pl.col("premium") + 5).alias("final_premium"))\ndf = out',
        )


# ---------------------------------------------------------------------------
# Codegen
# ---------------------------------------------------------------------------


class TestRatingStepCodegen:
    """Codegen produces valid rating step decorators."""

    def test_codegen_rating_step(self):
        tables = [
            {
                "name": "T1",
                "factors": ["band"],
                "outputColumn": "factor",
                "defaultValue": 1.0,
                "entries": [{"band": "A", "value": 2.0}],
            }
        ]
        node = _rating_node("rating", tables)
        src = _source_node("src")
        graph = PipelineGraph(
            nodes=[src, node],
            edges=[GraphEdge(id="e1", source="src", target="rating")],
        )
        code = graph_to_code(graph)
        assert 'config="config/rating_step/rating.json"' in code

    def test_codegen_roundtrip(self, tmp_path):
        """Code generated from a graph can be parsed back."""
        from haute._config_io import collect_node_configs

        tables = [
            {
                "name": "Age Factor",
                "factors": ["age_band"],
                "outputColumn": "age_factor",
                "defaultValue": 1.0,
                "entries": [
                    {"age_band": "young", "value": 1.3},
                    {"age_band": "older", "value": 0.9},
                ],
            }
        ]
        node = _rating_node("rating", tables)
        src = _source_node("src")
        graph = PipelineGraph(
            nodes=[src, node],
            edges=[GraphEdge(id="e1", source="src", target="rating")],
        )
        code = graph_to_code(graph)

        # Write config files so the parser can resolve them
        for rel_path, content in collect_node_configs(graph).items():
            cfg_file = tmp_path / rel_path
            cfg_file.parent.mkdir(parents=True, exist_ok=True)
            cfg_file.write_text(content)

        parsed = parse_pipeline_source(code, _base_dir=tmp_path)
        rating_nodes = [n for n in parsed.nodes if n.data.nodeType == "ratingStep"]
        assert len(rating_nodes) == 1
        rt = rating_nodes[0].data.config["tables"]
        assert len(rt) == 1
        assert rt[0]["factors"] == ["age_band"]
        assert rt[0]["outputColumn"] == "age_factor"
        assert len(rt[0]["entries"]) == 2

    def test_codegen_with_operation(self, tmp_path):
        """Codegen emits operation and combined_column in config file when set."""
        from haute._config_io import collect_node_configs

        tables = [
            {
                "name": "T1",
                "factors": ["b"],
                "outputColumn": "f1",
                "defaultValue": 1.0,
                "entries": [{"b": "A", "value": 2.0}],
            },
            {
                "name": "T2",
                "factors": ["b"],
                "outputColumn": "f2",
                "defaultValue": 1.0,
                "entries": [{"b": "A", "value": 3.0}],
            },
        ]
        node = _rating_node("rating", tables, operation="add", combined_column="total")
        src = _source_node("src")
        graph = PipelineGraph(
            nodes=[src, node],
            edges=[GraphEdge(id="e1", source="src", target="rating")],
        )
        code = graph_to_code(graph)
        assert 'config="config/rating_step/rating.json"' in code

        # Write config files so the parser can resolve them
        for rel_path, content in collect_node_configs(graph).items():
            cfg_file = tmp_path / rel_path
            cfg_file.parent.mkdir(parents=True, exist_ok=True)
            cfg_file.write_text(content)

        # Roundtrip
        parsed = parse_pipeline_source(code, _base_dir=tmp_path)
        rn = [n for n in parsed.nodes if n.data.nodeType == "ratingStep"][0]
        assert rn.data.config["operation"] == "add"
        assert rn.data.config["combinedColumn"] == "total"

    def test_codegen_multiply_default_omitted(self, tmp_path):
        """Multiply (default) is not emitted in decorator; config file has combinedColumn."""
        import json

        from haute._config_io import collect_node_configs

        tables = [
            {
                "name": "T1",
                "factors": ["b"],
                "outputColumn": "f1",
                "entries": [{"b": "A", "value": 2.0}],
            },
        ]
        node = _rating_node("rating", tables, combined_column="c")
        src = _source_node("src")
        graph = PipelineGraph(
            nodes=[src, node],
            edges=[GraphEdge(id="e1", source="src", target="rating")],
        )
        code = graph_to_code(graph)
        # Decorator should just be a config= reference
        assert 'config="config/rating_step/rating.json"' in code

        # Verify config file contents
        configs = collect_node_configs(graph)
        rating_cfg = json.loads(configs["config/rating_step/rating.json"])
        assert rating_cfg["combinedColumn"] == "c"

    def test_codegen_preserves_combined_outputs_in_sidecar(self):
        import json

        from haute._config_io import collect_node_configs

        node = _rating_node(
            "rating",
            [{"name": "T", "factors": ["b"], "outputColumn": "f", "entries": []}],
            combined_outputs=[
                {"outputColumn": "premium", "operation": "multiply", "baseValue": 100}
            ],
        )
        src = _source_node("src")
        graph = PipelineGraph(
            nodes=[src, node],
            edges=[GraphEdge(id="e1", source="src", target="rating")],
        )

        configs = collect_node_configs(graph)
        rating_cfg = json.loads(configs["config/rating_step/rating.json"])
        assert rating_cfg["combinedOutputs"] == [
            {"outputColumn": "premium", "operation": "multiply", "baseValue": 100}
        ]

    def test_codegen_rating_code_runs_after_combined_output(self, tmp_path):
        """Generated pipelines run rating tables/combined outputs before custom code."""
        from haute._config_io import collect_node_configs

        node = _rating_node(
            "rating",
            [
                {
                    "name": "factor",
                    "factors": ["band"],
                    "outputColumn": "factor",
                    "defaultValue": "1.0",
                    "entries": [{"band": "A", "value": 2.0}],
                }
            ],
            combined_outputs=[
                {"outputColumn": "premium", "operation": "multiply", "baseValue": 100}
            ],
            code="df = df.with_columns((pl.col('premium') + 5).alias('final_premium'))",
        )
        src = _source_node("src")
        graph = PipelineGraph(
            nodes=[src, node],
            edges=[GraphEdge(id="e1", source="src", target="rating")],
        )
        code = graph_to_code(graph)
        main_path = tmp_path / "main.py"
        main_path.write_text(code, encoding="utf-8")
        for rel_path, content in collect_node_configs(graph).items():
            cfg_file = tmp_path / rel_path
            cfg_file.parent.mkdir(parents=True, exist_ok=True)
            cfg_file.write_text(content, encoding="utf-8")

        ns = {"__file__": str(main_path)}
        exec(compile(code, str(main_path), "exec"), ns)
        result = ns["pipeline"].score(pl.DataFrame({"band": ["A", "B"]}))
        if isinstance(result, pl.LazyFrame):
            result = result.collect()

        assert result["premium"].to_list() == [200.0, 100.0]
        assert result["final_premium"].to_list() == [205.0, 105.0]

    def test_codegen_rating_scaffold_is_not_roundtripped_as_user_code(self, tmp_path):
        """Parser keeps only user-authored post-rating code from generated Rating nodes."""
        from haute._config_io import collect_node_configs

        user_code = "df = df.with_columns((pl.col('premium') + 5).alias('final_premium'))"
        node = _rating_node(
            "rating",
            [],
            combined_outputs=[
                {"outputColumn": "premium", "operation": "multiply", "baseValue": 100}
            ],
            code=user_code,
        )
        src = _source_node("src")
        graph = PipelineGraph(
            nodes=[src, node],
            edges=[GraphEdge(id="e1", source="src", target="rating")],
        )
        code = graph_to_code(graph)
        for rel_path, content in collect_node_configs(graph).items():
            cfg_file = tmp_path / rel_path
            cfg_file.parent.mkdir(parents=True, exist_ok=True)
            cfg_file.write_text(content, encoding="utf-8")

        parsed = parse_pipeline_source(code, _base_dir=tmp_path)
        rating_node = [n for n in parsed.nodes if n.data.nodeType == "ratingStep"][0]

        assert rating_node.data.config["code"] == user_code

    def test_generated_rating_function_applies_config_before_user_code(self):
        """Generated rating/main.py code applies rating config before custom code."""
        from haute.codegen import _generate_node_code

        node = _rating_node(
            "rating",
            [
                {
                    "name": "Age Factor",
                    "factors": ["age_band"],
                    "outputColumn": "age_factor",
                    "defaultValue": "1.0",
                    "entries": [{"age_band": "young", "value": 2.0}],
                },
            ],
            combined_outputs=[
                {"outputColumn": "premium", "operation": "multiply", "baseValue": 100}
            ],
            code="df = df.with_columns((pl.col('premium') + 5).alias('final_premium'))",
        )
        generated = _generate_node_code(node, source_names=["quotes"])

        assert "apply_rating_step_from_config" in generated
        assert generated.index("apply_rating_step_from_config") < generated.index("final_premium")


class TestRatingStepCompactSidecarIntegration:
    def test_parser_expands_compact_rating_sidecar_to_canonical_rows(self, tmp_path):
        import json

        config_path = tmp_path / "config" / "rating_step" / "rating.json"
        config_path.parent.mkdir(parents=True, exist_ok=True)
        config_path.write_text(
            json.dumps(
                {
                    "tables": [
                        {
                            "name": "vehicle_factor",
                            "factors": ["vehicle_age_band", "cover_type"],
                            "outputColumn": "vehicle_factor",
                            "entries": {"1-3": {"comprehensive": 0.9}},
                        }
                    ]
                }
            ),
            encoding="utf-8",
        )
        code = """
import polars as pl
from haute import pipeline

@pipeline.rating_step(config="config/rating_step/rating.json")
def rating(df: pl.LazyFrame) -> pl.LazyFrame:
    return df
"""

        parsed = parse_pipeline_source(code, _base_dir=tmp_path)
        rating_node = [n for n in parsed.nodes if n.data.nodeType == "ratingStep"][0]

        assert rating_node.data.config["tables"][0]["entries"] == [
            {
                "vehicle_age_band": "1-3",
                "cover_type": "comprehensive",
                "value": 0.9,
            }
        ]

    def test_parser_expands_three_factor_sidecar_using_editor_axis_order(self, tmp_path):
        import json

        config_path = tmp_path / "config" / "rating_step" / "rating.json"
        config_path.parent.mkdir(parents=True, exist_ok=True)
        config_path.write_text(
            json.dumps(
                {
                    "tables": [
                        {
                            "name": "vehicle_factor",
                            "factors": ["vehicle_age_band", "cover_type", "channel"],
                            "outputColumn": "vehicle_factor",
                            "entries": {
                                "confused": {
                                    "comprehensive": {
                                        "1-3": 0.91,
                                        "4-5": 0.96,
                                    }
                                }
                            },
                        }
                    ]
                }
            ),
            encoding="utf-8",
        )
        code = """
import polars as pl
from haute import pipeline

@pipeline.rating_step(config="config/rating_step/rating.json")
def rating(df: pl.LazyFrame) -> pl.LazyFrame:
    return df
"""

        parsed = parse_pipeline_source(code, _base_dir=tmp_path)
        rating_node = [n for n in parsed.nodes if n.data.nodeType == "ratingStep"][0]

        assert rating_node.data.config["tables"][0]["entries"] == [
            {
                "vehicle_age_band": "1-3",
                "cover_type": "comprehensive",
                "channel": "confused",
                "value": 0.91,
            },
            {
                "vehicle_age_band": "4-5",
                "cover_type": "comprehensive",
                "channel": "confused",
                "value": 0.96,
            },
        ]

    def test_collect_then_parse_keeps_graph_canonical_but_sidecar_compact(self, tmp_path):
        import json

        from haute._config_io import collect_node_configs

        node = _rating_node(
            "rating",
            [
                {
                    "name": "vehicle_factor",
                    "factors": ["vehicle_age_band", "cover_type"],
                    "outputColumn": "vehicle_factor",
                    "entries": [
                        {
                            "vehicle_age_band": "1-3",
                            "cover_type": "comprehensive",
                            "value": 0.9,
                        }
                    ],
                }
            ],
        )
        src = _source_node("src")
        graph = PipelineGraph(
            nodes=[src, node],
            edges=[GraphEdge(id="e1", source="src", target="rating")],
        )
        code = graph_to_code(graph)
        configs = collect_node_configs(graph)
        rating_config = json.loads(configs["config/rating_step/rating.json"])
        assert rating_config["tables"][0]["entries"] == {"1-3": {"comprehensive": 0.9}}
        for rel_path, content in configs.items():
            config_file = tmp_path / rel_path
            config_file.parent.mkdir(parents=True, exist_ok=True)
            config_file.write_text(content, encoding="utf-8")

        parsed = parse_pipeline_source(code, _base_dir=tmp_path)
        rating_node = [n for n in parsed.nodes if n.data.nodeType == "ratingStep"][0]

        assert isinstance(rating_node.data.config["tables"][0]["entries"], list)
        assert rating_node.data.config["tables"][0]["entries"][0] == {
            "vehicle_age_band": "1-3",
            "cover_type": "comprehensive",
            "value": 0.9,
        }

    def test_collect_then_parse_three_factor_keeps_editor_axis_sidecar_order(self, tmp_path):
        import json

        from haute._config_io import collect_node_configs

        expected_entries = [
            {
                "vehicle_age_band": "1-3",
                "cover_type": "comprehensive",
                "channel": "confused",
                "value": 0.91,
            },
            {
                "vehicle_age_band": "1-3",
                "cover_type": "third_party_only",
                "channel": "compare_the_market",
                "value": 1.08,
            },
        ]
        node = _rating_node(
            "rating",
            [
                {
                    "name": "vehicle_factor",
                    "factors": ["vehicle_age_band", "cover_type", "channel"],
                    "outputColumn": "vehicle_factor",
                    "entries": expected_entries,
                }
            ],
        )
        src = _source_node("src")
        graph = PipelineGraph(
            nodes=[src, node],
            edges=[GraphEdge(id="e1", source="src", target="rating")],
        )
        code = graph_to_code(graph)
        configs = collect_node_configs(graph)
        rating_config = json.loads(configs["config/rating_step/rating.json"])
        assert rating_config["tables"][0]["entries"] == {
            "confused": {"comprehensive": {"1-3": 0.91}},
            "compare_the_market": {"third_party_only": {"1-3": 1.08}},
        }
        for rel_path, content in configs.items():
            config_file = tmp_path / rel_path
            config_file.parent.mkdir(parents=True, exist_ok=True)
            config_file.write_text(content, encoding="utf-8")

        parsed = parse_pipeline_source(code, _base_dir=tmp_path)
        rating_node = [n for n in parsed.nodes if n.data.nodeType == "ratingStep"][0]

        assert rating_node.data.config["tables"][0]["entries"] == expected_entries
