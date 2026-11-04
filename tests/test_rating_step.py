"""Tests for the rating step node: executor, parser, and codegen."""

from __future__ import annotations

import polars as pl
import pytest

from haute.codegen import graph_to_code
from haute.errors import NodeConfigError, ParseError
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
    combined_outputs: list[dict] | None = None,
    code: str = "",
) -> GraphNode:
    cfg: dict = {"tables": tables or []}
    if combined_outputs is not None:
        cfg["combined_outputs"] = combined_outputs
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


class TestRatingStepP3:
    @pytest.mark.parametrize("operation, expected", [("min", 100.0), ("max", 100.0)])
    def test_base_value_defines_otherwise_all_null_extrema(
        self, operation: str, expected: float
    ) -> None:
        from haute._rating import apply_rating_step_from_config

        config = {
            "tables": [
                {
                    "factors": ["band"],
                    "output_column": "factor",
                    "on_missing": "neutral",
                    "entries": [{"band": "A", "value": 2.0}],
                }
            ],
            "combined_outputs": [
                {"output_column": "premium", "operation": operation, "base_value": expected}
            ],
        }
        result = apply_rating_step_from_config(
            pl.DataFrame({"band": ["missing"]}), config
        ).collect()
        assert result["premium"].to_list() == [expected]

    def test_extrema_base_value_keeps_missing_table_value_defined(self) -> None:
        from haute._rating import apply_rating_step_from_config

        config = {
            "tables": [
                {
                    "factors": ["band"],
                    "output_column": "factor",
                    "on_missing": "neutral",
                    "entries": [{"band": "A", "value": 2.0}],
                }
            ],
            "combined_outputs": [
                {"output_column": "premium", "operation": "min", "base_value": 1.0}
            ],
        }
        result_lf = apply_rating_step_from_config(pl.DataFrame({"band": ["missing"]}), config)
        assert result_lf.collect()["premium"].to_list() == [1.0]

    def test_multi_table_multi_output_plan_resolves_schema_once(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from haute import _rating

        original_frame_schema = _rating._frame_schema
        calls = 0

        def counting_frame_schema(frame: object) -> object:
            nonlocal calls
            calls += 1
            return original_frame_schema(frame)  # type: ignore[arg-type]

        monkeypatch.setattr(_rating, "_frame_schema", counting_frame_schema)
        config = {
            "tables": [
                {
                    "factors": ["band"],
                    "output_column": "first",
                    "entries": [{"band": "A", "value": 2.0}],
                },
                {
                    "factors": ["band"],
                    "output_column": "second",
                    "entries": [{"band": "A", "value": 3.0}],
                },
            ],
            "combined_outputs": [
                {"output_column": "premium", "operation": "multiply", "base_value": 10.0},
                {"output_column": "total", "operation": "add", "base_value": 1.0},
            ],
        }

        out = _rating.apply_rating_step_from_config(pl.DataFrame({"band": ["A"]}), config).collect()
        assert calls == 1
        assert out["premium"].to_list() == [60.0]
        assert out["total"].to_list() == [6.0]


# ---------------------------------------------------------------------------
# Executor: _apply_rating_table via _build_node_fn
# ---------------------------------------------------------------------------


class TestRatingStepExecutor:
    """Rating step executor applies lookup joins correctly."""

    def test_one_way_lookup(self):
        """Single-factor table: join on one column."""
        tables = [
            {
                "factors": ["age_band"],
                "output_column": "age_factor",
                "default_value": "1.0",
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
        # on_missing="neutral" opt-in; the default is fail-loud
        # (tests/test_rating_miss_fail_loud.py).
        tables = [
            {
                "factors": ["age_band", "prop_band"],
                "output_column": "factor",
                "default_value": None,
                "on_missing": "neutral",
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
                "factors": ["a", "b", "c"],
                "output_column": "val",
                "default_value": "0.0",
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
                "factors": ["band"],
                "output_column": "f1",
                "default_value": "1.0",
                "entries": [{"band": "A", "value": 2.0}],
            },
            {
                "factors": ["band"],
                "output_column": "f2",
                "default_value": "1.0",
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

    def test_partially_populated_table_rejected(self):
        """Rows without a factor contract are malformed, not an empty draft."""
        tables = [
            {
                "factors": [],
                "output_column": "out",
                "default_value": None,
                "entries": [{"value": 1.0}],
            },
        ]
        node = _rating_node("r6", tables)
        with pytest.raises(ValueError, match=r"tables\[0\]\.factors"):
            _build_node_fn(node)

    @pytest.mark.parametrize(
        ("table", "setting", "shape_refuses"),
        [
            ({"factors": "band", "output_column": "out", "entries": []}, "tables[0].factors", True),
            (
                {"factors": ["band"], "output_column": "out", "entries": {}},
                "tables[0].entries",
                True,
            ),
            (
                {"factors": [], "output_column": "out", "entries": [{"value": 1.0}]},
                "tables[0].factors",
                False,
            ),
        ],
        ids=["non_list_factors", "non_list_entries", "empty_factors_with_entries"],
    )
    def test_malformed_collections_generated_executor_parity(
        self, table: dict, setting: str, shape_refuses: bool
    ) -> None:
        """Generated runtime and executor construction reject the same bad table shape at
        the same setting. Building the node holds it to its config model first, so a value
        the model's shape refuses is refused in the model's words."""
        from haute._rating import apply_rating_step_from_config

        config = {"tables": [table]}
        with pytest.raises(ValueError) as generated_error:
            apply_rating_step_from_config(pl.DataFrame({"band": ["A"]}).lazy(), config)

        node = _rating_node("malformed", [table])
        with pytest.raises(ValueError) as executor_error:
            _build_node_fn(node)

        assert setting in str(generated_error.value)
        if shape_refuses:
            assert f"Node 'malformed' (ratingStep) setting '{setting}'" in str(executor_error.value)
        else:
            assert str(executor_error.value) == str(generated_error.value)

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
                {"factors": ["b"], "output_column": "phantom", "entries": []},
                # Real table producing "factor".
                {
                    "factors": ["b"],
                    "output_column": "factor",
                    "entries": [{"b": "A", "value": 2.0}],
                },
            ],
            "combined_outputs": [
                {"output_column": "premium", "operation": "multiply", "base_value": 100}
            ],
        }
        out = apply_rating_step_from_config(pl.DataFrame({"b": ["A"]}).lazy(), config).collect()
        assert "phantom" not in out.columns
        # premium = base 100 * real factor 2.0 (phantom excluded, no crash).
        assert out["premium"].to_list() == [200.0]

    def test_incomplete_table_skip_is_logged_not_silent(self):
        """F082 fail-loud: an incomplete table configured with an output_column
        is a passthrough, so it is omitted from combined outputs — but the
        omission must be OBSERVABLE, not silent.  A WARNING names the table,
        its output column, and the reason; the real table that materialised is
        not warned about."""
        import structlog

        from haute._rating import apply_rating_step_from_config

        config = {
            "tables": [
                # Incomplete: empty entries -> passthrough, no output column.
                {"factors": ["b"], "output_column": "phantom", "entries": []},
                # Real table producing "factor".
                {
                    "factors": ["b"],
                    "output_column": "factor",
                    "entries": [{"b": "A", "value": 2.0}],
                },
            ],
            "combined_outputs": [
                {"output_column": "premium", "operation": "multiply", "base_value": 100}
            ],
        }
        with structlog.testing.capture_logs() as logs:
            out = apply_rating_step_from_config(pl.DataFrame({"b": ["A"]}).lazy(), config).collect()
        assert out["premium"].to_list() == [200.0]

        skips = [log for log in logs if log["event"] == "rating_table_skipped_incomplete"]
        assert len(skips) == 1
        assert skips[0]["log_level"] == "warning"
        assert skips[0]["table"] == "phantom"
        assert skips[0]["output_column"] == "phantom"
        assert "entries" in skips[0]["reason"]

    def test_disabled_table_without_output_column_is_not_warned(self):
        """A table with no output_column is a deliberately disabled node, not an
        incomplete one — it must NOT emit the incomplete-skip warning."""
        import structlog

        from haute._rating import apply_rating_step_from_config

        config = {
            "tables": [
                {"factors": ["b"], "output_column": "", "entries": []},
                {
                    "factors": ["b"],
                    "output_column": "factor",
                    "entries": [{"b": "A", "value": 2.0}],
                },
            ],
        }
        with structlog.testing.capture_logs() as logs:
            apply_rating_step_from_config(pl.DataFrame({"b": ["A"]}).lazy(), config).collect()
        assert [log for log in logs if log["event"] == "rating_table_skipped_incomplete"] == []

    @pytest.mark.parametrize(
        "operation, col_name, base_value, expected",
        [
            ("multiply", "combined", 1.0, [6.0, 1.0]),
            ("add", "total", 0.0, [5.0, 2.0]),
            ("min", "mn", 100.0, [2.0, 1.0]),
            ("max", "mx", 0.0, [3.0, 1.0]),
        ],
        ids=["multiply", "add", "min", "max"],
    )
    def test_combine_operations(self, operation, col_name, base_value, expected):
        """Two tables combined via the given operation."""
        tables = [
            {
                "factors": ["band"],
                "output_column": "f1",
                "default_value": "1.0",
                "entries": [{"band": "A", "value": 2.0}],
            },
            {
                "factors": ["band"],
                "output_column": "f2",
                "default_value": "1.0",
                "entries": [{"band": "A", "value": 3.0}],
            },
        ]
        node = _rating_node(
            f"rc_{operation}",
            tables,
            combined_outputs=[
                {
                    "output_column": col_name,
                    "operation": operation,
                    "base_value": base_value,
                }
            ],
        )
        _, fn, _ = _build_node_fn(node)
        lf = pl.DataFrame({"band": ["A", "B"]}).lazy()
        result = fn(lf).collect()
        assert result[col_name].to_list() == expected

    def test_no_combined_outputs_skips_combine(self):
        """Without combined_outputs, no combination column is created."""
        tables = [
            {
                "factors": ["band"],
                "output_column": "f1",
                "default_value": "1.0",
                "entries": [{"band": "A", "value": 2.0}],
            },
            {
                "factors": ["band"],
                "output_column": "f2",
                "default_value": "1.0",
                "entries": [{"band": "A", "value": 3.0}],
            },
        ]
        node = _rating_node("rc5", tables)
        _, fn, _ = _build_node_fn(node)
        lf = pl.DataFrame({"band": ["A"]}).lazy()
        result = fn(lf).collect()
        assert "combined" not in result.columns
        assert result.columns == ["band", "f1", "f2"]

    def test_empty_combined_outputs_is_noop(self):
        """Explicit combined_outputs=[] creates no combined output."""
        tables = [
            {
                "factors": ["band"],
                "output_column": "f1",
                "default_value": "1.0",
                "entries": [{"band": "A", "value": 2.0}],
            },
            {
                "factors": ["band"],
                "output_column": "f2",
                "default_value": "1.0",
                "entries": [{"band": "A", "value": 3.0}],
            },
        ]
        node = _rating_node("empty_outputs", tables, combined_outputs=[])
        _, fn, _ = _build_node_fn(node)
        result = fn(pl.DataFrame({"band": ["A"]}).lazy()).collect()

        assert result.columns == ["band", "f1", "f2"]

    def test_string_factor_values_match(self):
        """Factor values are cast to Utf8 so string bands match."""
        # PIN REVISION (3a.3): the miss on band 2 needs on_missing="neutral".
        tables = [
            {
                "factors": ["band"],
                "output_column": "out",
                "default_value": None,
                "on_missing": "neutral",
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
                "factors": ["band"],
                "output_column": "f1",
                "default_value": "1.0",
                "entries": [{"band": "A", "value": 2.0}],
            },
            {
                "factors": ["band"],
                "output_column": "f2",
                "default_value": "1.0",
                "entries": [{"band": "A", "value": 3.0}],
            },
        ]
        node = _rating_node(
            "multi_outputs",
            tables,
            combined_outputs=[
                {"output_column": "technical_premium", "operation": "multiply", "base_value": 100},
                {"output_column": "additive_score", "operation": "add", "base_value": 10},
                {"output_column": "minimum_factor", "operation": "min", "base_value": 1.5},
                {"output_column": "maximum_factor", "operation": "max", "base_value": 1.5},
            ],
        )
        _, fn, _ = _build_node_fn(node)
        result = fn(pl.DataFrame({"band": ["A", "B"]}).lazy()).collect()

        assert result["technical_premium"].to_list() == [600.0, 100.0]
        assert result["additive_score"].to_list() == [15.0, 12.0]
        assert result["minimum_factor"].to_list() == [1.5, 1.0]
        assert result["maximum_factor"].to_list() == [3.0, 1.5]

    def test_combined_output_base_value_can_create_base_only_output(self):
        """A numeric base value is enough to create a combined output."""
        node = _rating_node(
            "base_only_output",
            [],
            combined_outputs=[
                {"output_column": "technical_premium", "operation": "multiply", "base_value": 100}
            ],
        )
        _, fn, _ = _build_node_fn(node)
        result = fn(pl.DataFrame({"quote_id": [1, 2]}).lazy()).collect()

        assert result["technical_premium"].to_list() == [100.0, 100.0]

    def test_rating_code_runs_after_tables_and_combined_outputs(self):
        """User code can post-process columns created by the rating step."""
        tables = [
            {
                "factors": ["band"],
                "output_column": "factor",
                "default_value": "1.0",
                "entries": [{"band": "A", "value": 2.0}],
            }
        ]
        node = _rating_node(
            "rating_code",
            tables,
            combined_outputs=[
                {"output_column": "premium", "operation": "multiply", "base_value": 100}
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
                    "factors": ["band"],
                    "output_column": "factor",
                    "entries": [{"band": "A", "value": 2.0}],
                }
            ],
            combined_outputs=[
                {"output_column": "premium", "operation": "divide", "base_value": 100}
            ],
        )
        with pytest.raises(
            NodeConfigError,
            match=r"setting 'combined_outputs\[0\]\.operation': "
            r"Input should be 'multiply', 'add', 'min' or 'max'",
        ):
            _build_node_fn(node)

    def test_combined_outputs_missing_base_value_raises(self):
        """New combined outputs require an explicit numeric base value."""
        node = _rating_node(
            "missing_base",
            [],
            combined_outputs=[{"output_column": "premium", "operation": "multiply"}],
        )
        with pytest.raises(
            NodeConfigError, match=r"setting 'combined_outputs\[0\]\.base_value': Field required"
        ):
            _build_node_fn(node)

    def test_combined_outputs_boolean_base_value_raises(self):
        """Booleans are not accepted as numeric base values."""
        node = _rating_node(
            "boolean_base",
            [],
            combined_outputs=[
                {"output_column": "premium", "operation": "multiply", "base_value": True}
            ],
        )
        with pytest.raises(
            NodeConfigError,
            match=(
                r"setting 'combined_outputs\[0\]\.base_value': Input should be a valid string, "
                r"a valid integer or a valid number"
            ),
        ):
            _build_node_fn(node)

    def test_a_combined_output_that_is_not_an_object_is_refused_by_position(self):
        """The config model refuses a combined output that is not an object, at its position."""
        node = _rating_node(
            "malformed_combined_output",
            [],
            combined_outputs=[
                {"output_column": "base", "operation": "multiply", "base_value": 100},
                "premium",
            ],
        )

        with pytest.raises(NodeConfigError) as caught:
            _build_node_fn(node)

        assert caught.value.message == (
            "Node 'malformed_combined_output' (ratingStep) setting 'combined_outputs[1]': "
            "Input should be a valid dictionary."
        )
        assert caught.value.setting == "combined_outputs[1]"

    @pytest.mark.parametrize(
        ("combined_output", "message"),
        [
            (
                {"output_column": "premium", "operation": "multiply", "base_value": "abc"},
                "ratingStep combined_outputs[1].base_value must be numeric",
            ),
            (
                {"output_column": "premium", "operation": "multiply", "base_value": "inf"},
                "ratingStep combined_outputs[1].base_value must be finite",
            ),
        ],
        ids=["non-numeric-base", "non-finite-base"],
    )
    def test_a_malformed_combined_output_is_refused_by_position(self, combined_output, message):
        """A combined output that cannot price fails the build, naming its position."""
        from haute.errors import ConfigSettingError

        node = _rating_node(
            "malformed_combined_output",
            [],
            combined_outputs=[
                {"output_column": "base", "operation": "multiply", "base_value": 100},
                combined_output,
            ],
        )

        with pytest.raises(ConfigSettingError) as caught:
            _build_node_fn(node)

        assert str(caught.value) == message
        assert caught.value.setting == "combined_outputs"

    def test_combined_outputs_blank_output_column_raises(self):
        """Configured combined outputs must have an explicit output column."""
        node = _rating_node(
            "blank_output_column",
            [],
            combined_outputs=[{"output_column": " ", "operation": "multiply", "base_value": 100}],
        )
        with pytest.raises(ValueError, match="requires output_column"):
            _build_node_fn(node)

    def test_combined_outputs_duplicate_output_column_raises(self):
        """New combined outputs cannot overwrite table or combined outputs."""
        node = _rating_node(
            "duplicate_output",
            [
                {
                    "factors": ["band"],
                    "output_column": "premium",
                    "entries": [{"band": "A", "value": 2.0}],
                }
            ],
            combined_outputs=[
                {"output_column": "premium", "operation": "multiply", "base_value": 100}
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
                        "factors": ["age"],
                        "output_column": "af",
                        "default_value": 1.0,
                        # A sidecar writes the table as a grid (READ-26).
                        "rows": [["young", 1.5]],
                    }
                ]
            },
        )
        code = f'''
import polars as pl
import haute

pipeline = haute.Pipeline("rating")

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
        assert tables[0]["output_column"] == "af"
        assert tables[0]["default_value"] == 1.0
        # The node holds entries, read back from the grid.
        assert tables[0]["entries"] == [{"age": "young", "value": 1.5}]

    def test_parse_empty_tables(self, tmp_path):
        rating_config = write_node_config(
            tmp_path,
            NodeType.RATING_STEP,
            "rating",
            {"tables": []},
        )
        code = f'''
import polars as pl
import haute

pipeline = haute.Pipeline("rating")

@pipeline.rating_step(config="{rating_config}")
def rating(df: pl.LazyFrame) -> pl.LazyFrame:
    """Empty."""
    return df
'''
        parsed = parse_pipeline_source(code, _base_dir=tmp_path)
        n = parsed.nodes[0]
        assert n.data.nodeType == "ratingStep"
        assert n.data.config["tables"] == []

    def test_parse_combined_outputs_and_code_from_body(self, tmp_path):
        rating_config = write_node_config(
            tmp_path,
            NodeType.RATING_STEP,
            "rating",
            {
                "tables": [],
                "combined_outputs": [
                    {"output_column": "premium", "operation": "multiply", "base_value": 100}
                ],
            },
        )
        code = f'''
import polars as pl
import haute

pipeline = haute.Pipeline("rating")

@pipeline.rating_step(config="{rating_config}")
def rating(df: pl.LazyFrame) -> pl.LazyFrame:
    """Combined outputs."""
    df = df.with_columns(pl.lit(1).alias("after_rating"))
    return df
'''
        parsed = parse_pipeline_source(code, _base_dir=tmp_path)
        n = parsed.nodes[0]
        assert n.data.config["combined_outputs"] == [
            {"output_column": "premium", "operation": "multiply", "base_value": 100}
        ]
        assert "after_rating" in n.data.config["code"]

    def test_parse_rating_hook_return_expression_becomes_a_df_assignment(self, tmp_path):
        """A hook's ``return <expr>`` on the rated df reloads as ``df = <expr>``."""
        rating_config = write_node_config(
            tmp_path,
            NodeType.RATING_STEP,
            "rating",
            {
                "tables": [],
                "combined_outputs": [
                    {"output_column": "premium", "operation": "multiply", "base_value": 100}
                ],
            },
        )
        code = f'''
import polars as pl
import haute

pipeline = haute.Pipeline("rating")

@pipeline.rating_step(config="{rating_config}")
def rating(df: pl.LazyFrame) -> pl.LazyFrame:
    """Combined outputs."""
    return df.with_columns((pl.col("premium") + 5).alias("final_premium"))
'''
        parsed = parse_pipeline_source(code, _base_dir=tmp_path)
        n = parsed.nodes[0]

        _assert_code_equal(
            n.data.config["code"],
            'df = df.with_columns((pl.col("premium") + 5).alias("final_premium"))',
        )

    def test_parse_rating_hook_alias_is_kept_as_authored(self, tmp_path):
        """An alias of the rated df in a hook round-trips as the user wrote it."""
        rating_config = write_node_config(
            tmp_path,
            NodeType.RATING_STEP,
            "rating",
            {
                "tables": [],
                "combined_outputs": [
                    {"output_column": "premium", "operation": "multiply", "base_value": 100}
                ],
            },
        )
        code = f'''
import polars as pl
import haute

pipeline = haute.Pipeline("rating")

@pipeline.rating_step(config="{rating_config}")
def rating(df: pl.LazyFrame) -> pl.LazyFrame:
    """Combined outputs."""
    out = df.with_columns((pl.col("premium") + 5).alias("final_premium"))
    return out
'''
        parsed = parse_pipeline_source(code, _base_dir=tmp_path)
        n = parsed.nodes[0]

        _assert_code_equal(
            n.data.config["code"],
            'out = df.with_columns((pl.col("premium") + 5).alias("final_premium"))\ndf = out',
        )

    def test_parse_rating_code_that_reads_its_input_is_rejected(self, tmp_path):
        """Code must take the rated df; a body on the raw input would never run."""
        rating_config = write_node_config(
            tmp_path, NodeType.RATING_STEP, "rating", {"tables": [], "combined_outputs": []}
        )
        code = f'''
import polars as pl
import haute

pipeline = haute.Pipeline("rating")

@pipeline.rating_step(config="{rating_config}")
def rating(source: pl.LazyFrame) -> pl.LazyFrame:
    return source.with_columns((pl.col("premium") + 5).alias("final_premium"))
'''
        with pytest.raises(ParseError, match="make the first parameter df"):
            parse_pipeline_source(code, _base_dir=tmp_path)


# ---------------------------------------------------------------------------
# Codegen
# ---------------------------------------------------------------------------


class TestRatingStepCodegen:
    """Codegen produces valid rating step decorators."""

    def test_codegen_rating_step(self):
        tables = [
            {
                "factors": ["band"],
                "output_column": "factor",
                "default_value": 1.0,
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
                "factors": ["age_band"],
                "output_column": "age_factor",
                "default_value": 1.0,
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
        assert rt[0]["output_column"] == "age_factor"
        assert len(rt[0]["entries"]) == 2

    def test_codegen_preserves_canonical_combined_output(self, tmp_path):
        """Codegen round-trips canonical combined_outputs through its config file."""
        from haute._config_io import collect_node_configs

        tables = [
            {
                "factors": ["b"],
                "output_column": "f1",
                "default_value": 1.0,
                "entries": [{"b": "A", "value": 2.0}],
            },
            {
                "factors": ["b"],
                "output_column": "f2",
                "default_value": 1.0,
                "entries": [{"b": "A", "value": 3.0}],
            },
        ]
        node = _rating_node(
            "rating",
            tables,
            combined_outputs=[{"output_column": "total", "operation": "add", "base_value": 0}],
        )
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
        assert rn.data.config["combined_outputs"] == [
            {"output_column": "total", "operation": "add", "base_value": 0}
        ]

    def test_codegen_multiply_combined_output_is_explicit(self, tmp_path):
        """The config file preserves the complete canonical combined output."""
        import json

        from haute._config_io import collect_node_configs

        tables = [
            {
                "factors": ["b"],
                "output_column": "f1",
                "entries": [{"b": "A", "value": 2.0}],
            },
        ]
        node = _rating_node(
            "rating",
            tables,
            combined_outputs=[{"output_column": "c", "operation": "multiply", "base_value": 1}],
        )
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
        assert rating_cfg["combined_outputs"] == [
            {"output_column": "c", "operation": "multiply", "base_value": 1}
        ]

    def test_codegen_preserves_combined_outputs_in_sidecar(self):
        import json

        from haute._config_io import collect_node_configs

        node = _rating_node(
            "rating",
            [{"factors": ["b"], "output_column": "f", "entries": []}],
            combined_outputs=[
                {"output_column": "premium", "operation": "multiply", "base_value": 100}
            ],
        )
        src = _source_node("src")
        graph = PipelineGraph(
            nodes=[src, node],
            edges=[GraphEdge(id="e1", source="src", target="rating")],
        )

        configs = collect_node_configs(graph)
        rating_cfg = json.loads(configs["config/rating_step/rating.json"])
        assert rating_cfg["combined_outputs"] == [
            {"output_column": "premium", "operation": "multiply", "base_value": 100}
        ]

    def test_codegen_rating_code_runs_after_combined_output(self, tmp_path):
        """Generated pipelines run rating tables/combined outputs before custom code."""
        from haute._config_io import collect_node_configs

        node = _rating_node(
            "rating",
            [
                {
                    "factors": ["band"],
                    "output_column": "factor",
                    "default_value": "1.0",
                    "entries": [{"band": "A", "value": 2.0}],
                }
            ],
            combined_outputs=[
                {"output_column": "premium", "operation": "multiply", "base_value": 100}
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
                {"output_column": "premium", "operation": "multiply", "base_value": 100}
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

    def test_generated_rating_hook_receives_the_rated_frame_as_df(self):
        """A rating step with code is a hook: its decorator rates, then hands it df."""
        from haute._codegen_builders import Param
        from haute.codegen import _generate_node_code

        node = _rating_node(
            "rating",
            [
                {
                    "factors": ["age_band"],
                    "output_column": "age_factor",
                    "default_value": "1.0",
                    "entries": [{"age_band": "young", "value": 2.0}],
                },
            ],
            combined_outputs=[
                {"output_column": "premium", "operation": "multiply", "base_value": 100}
            ],
            code="df = df.with_columns((pl.col('premium') + 5).alias('final_premium'))",
        )
        generated = _generate_node_code(node, source_names=["quotes"])

        assert generated.decorator == "rating_step"
        assert generated.keywords == (("config", "config/rating_step/rating.json"),)
        assert generated.params == (Param("df", "pl.LazyFrame"),)
        assert generated.returns == "pl.LazyFrame"
        assert generated.body == (
            "df = df.with_columns((pl.col('premium') + 5).alias('final_premium'))\nreturn df"
        )


class TestRatingStepCanonicalSidecarIntegration:
    def test_collect_then_parse_keeps_canonical_rows(self, tmp_path):
        import json

        from haute._config_io import collect_node_configs

        node = _rating_node(
            "rating",
            [
                {
                    "factors": ["vehicle_age_band", "cover_type"],
                    "output_column": "vehicle_factor",
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
        # The sidecar holds the table as a grid (READ-26); the node holds its entries.
        table = rating_config["tables"][0]
        assert "entries" not in table
        assert (table["columns"], table["rows"]) == (["comprehensive"], [["1-3", 0.9]])
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

    def test_collect_then_parse_three_factor_grid_keeps_the_entries(self, tmp_path):
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
                    "factors": ["vehicle_age_band", "cover_type", "channel"],
                    "output_column": "vehicle_factor",
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
        # The last factor's levels are the columns, a row per combination of the others,
        # null where no entry is (READ-26).
        table = rating_config["tables"][0]
        assert "entries" not in table
        assert table["columns"] == ["confused", "compare_the_market"]
        assert table["rows"] == [
            ["1-3", "comprehensive", 0.91, None],
            ["1-3", "third_party_only", None, 1.08],
        ]
        for rel_path, content in configs.items():
            config_file = tmp_path / rel_path
            config_file.parent.mkdir(parents=True, exist_ok=True)
            config_file.write_text(content, encoding="utf-8")

        parsed = parse_pipeline_source(code, _base_dir=tmp_path)
        rating_node = [n for n in parsed.nodes if n.data.nodeType == "ratingStep"][0]

        assert rating_node.data.config["tables"][0]["entries"] == expected_entries
