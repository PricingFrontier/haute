"""Focused contracts preventing trace explanations from diverging from runtime."""

from __future__ import annotations

import types

import polars as pl
import pytest

from haute._rating import SUPPORTED_BANDING_OPERATORS, _banding_condition
from haute._trace_correlation import SchemaDiff
from haute._trace_enrichment import (
    _match_continuous_rule,
    _sniff_operation_type,
    detect_row_lineage_type,
    enrich_model_score,
    enrich_rating_step,
)
from haute._trace_waterfall import build_waterfall_from_steps
from haute.trace import TraceStep, _assemble_steps


def _node(node_id: str, label: str | None = None) -> types.SimpleNamespace:
    return types.SimpleNamespace(
        id=node_id,
        data=types.SimpleNamespace(
            label=label or node_id,
            nodeType="polars",
            config={},
        ),
    )


def test_rating_detail_separates_table_selection_from_post_code_output() -> None:
    detail = enrich_rating_step(
        {
            "tables": [
                {
                    "name": "territory",
                    "factors": ["region"],
                    "entries": [{"region": "north", "value": 1.1}],
                    "outputColumn": "factor",
                }
            ],
            "code": "df = df.with_columns(factor=pl.col('factor') * 2)",
        },
        {"region": "north"},
        {"region": "north", "factor": 2.2},
        factor_input_dtypes={"region": pl.String},
    )

    table = detail["tables"][0]
    assert table["selected_value"] == 1.1
    assert table["post_code_output_value"] == 2.2
    assert table["status"] == "matched"


def test_rating_selection_uses_the_runtime_numeric_value_before_post_code() -> None:
    detail = enrich_rating_step(
        {
            "tables": [
                {
                    "name": "territory",
                    "factors": ["region"],
                    "entries": [{"region": "north", "value": "1.10"}],
                    "outputColumn": "factor",
                }
            ],
            "code": "df = df.with_columns(note=pl.lit('unchanged'))",
        },
        {"region": "north"},
        {"region": "north", "factor": 1.1, "note": "unchanged"},
        factor_input_dtypes={"region": pl.String},
    )

    table = detail["tables"][0]
    assert table["selected_value"] == 1.1
    assert "post_code_output_value" not in table


@pytest.mark.parametrize(
    ("operator_name", "input_value", "threshold"),
    [
        ("<", 4, 5),
        ("<=", 5, 5),
        (">", 6, 5),
        (">=", 5, 5),
        ("=", 5, 5),
        ("==", 5, 5),
    ],
)
def test_runtime_and_trace_share_one_banding_operator_matrix(
    operator_name: str,
    input_value: float,
    threshold: float,
) -> None:
    rule = {"op1": operator_name, "val1": threshold}
    runtime_match = (
        pl.DataFrame({"value": [input_value]})
        .select(_banding_condition(pl.col("value"), rule).alias("matched"))
        .item()
    )

    assert set(SUPPORTED_BANDING_OPERATORS) == {"<", "<=", ">", ">=", "=", "=="}
    assert runtime_match is _match_continuous_rule(input_value, rule)


def test_banding_operator_contract_is_immutable_and_rejects_unknowns() -> None:
    with pytest.raises(TypeError):
        SUPPORTED_BANDING_OPERATORS["!="] = lambda left, right: left != right  # type: ignore[index]
    with pytest.raises(ValueError, match="unsupported operator"):
        _banding_condition(pl.col("value"), {"op1": "!=", "val1": 5})
    with pytest.raises(ValueError, match="unsupported operator"):
        _match_continuous_rule(4, {"op1": "!=", "val1": 5})


@pytest.mark.parametrize("threshold", ["not-a-number", float("nan"), float("inf")])
def test_runtime_and_trace_reject_the_same_invalid_thresholds(threshold: object) -> None:
    rule = {"op1": "<", "val1": threshold}

    with pytest.raises(ValueError):
        _banding_condition(pl.col("value"), rule)
    with pytest.raises(ValueError):
        _match_continuous_rule(4, rule)


def test_model_detail_never_guesses_identifier_columns_as_features(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "haute._model_explainability.explain_model_score_from_config",
        lambda *_args, **_kwargs: None,
    )

    detail = enrich_model_score(
        {"output_column": "prediction"},
        {"quote_id": "Q-1", "age": 42},
        {"quote_id": "Q-1", "age": 42, "prediction": 0.7},
    )

    assert "feature_columns" not in detail
    assert "quote_id" not in detail.get("feature_values", {})
    assert detail["feature_metadata_unavailable"]


@pytest.mark.parametrize(
    "code",
    [
        "df = df.with_columns(note=pl.lit('.join('))",
        "df = df.with_columns(parts=pl.col('x').list.join(','))",
        "df = df.with_columns(text=pl.col('x').str.join(','))",
        "df = df.with_columns(x=pl.col('x'))  # df.join(other)",
    ],
)
def test_lineage_sniff_ignores_non_structural_join_text(code: str) -> None:
    assert _sniff_operation_type(code) == ""


def test_lineage_sniff_preserves_real_join_sort_and_observed_filter_semantics() -> None:
    assert _sniff_operation_type("df = df.join(other, on='id')") == "join"
    assert _sniff_operation_type("df = df.sort('id')") == "sort"
    assert (
        detect_row_lineage_type(
            input_row_count=2,
            output_row_count=2,
            operation_type="filter",
        )
        == "passthrough"
    )
    assert (
        detect_row_lineage_type(
            input_row_count=2,
            output_row_count=1,
            operation_type="filter",
        )
        == "filtered"
    )


def test_parent_collision_provenance_is_symmetric_and_order_independent() -> None:
    node_map = {node_id: _node(node_id) for node_id in ("left", "right", "child")}
    cached_rows = {
        "left": {"score": 1, "left_only": "L"},
        "right": {"score": 2, "right_only": "R"},
        "child": {"score": 1, "left_only": "L", "right_only": "R"},
    }

    left_first = _assemble_steps(
        order=["left", "right", "child"],
        source_ids={"left", "right"},
        node_map=node_map,
        parents_of={"child": ["left", "right"]},
        cached_rows=cached_rows,
    )[-1]
    right_first = _assemble_steps(
        order=["left", "right", "child"],
        source_ids={"left", "right"},
        node_map=node_map,
        parents_of={"child": ["right", "left"]},
        cached_rows=cached_rows,
    )[-1]

    expected = {
        "left.score": 1,
        "right.score": 2,
        "left_only": "L",
        "right_only": "R",
    }
    assert left_first.input_values == expected
    assert right_first.input_values == expected
    assert left_first.schema_diff.columns_removed == []
    assert left_first.schema_diff.columns_added == []
    assert left_first.schema_diff.columns_passed == ["left_only", "right_only", "score"]


def test_waterfall_carries_default_evidence_from_specialised_detail() -> None:
    def step(
        node_id: str,
        value: float,
        *,
        added: bool = False,
        default_used: bool = False,
    ) -> TraceStep:
        return TraceStep(
            node_id=node_id,
            node_name=node_id,
            node_type="ratingStep",
            schema_diff=SchemaDiff(
                columns_added=["premium"] if added else [],
                columns_removed=[],
                columns_modified=[] if added else ["premium"],
                columns_passed=[],
            ),
            input_values={},
            output_values={"premium": value},
            node_detail={
                "detail_type": "rating_step",
                "tables": [{"default_used": default_used}],
            },
        )

    result = build_waterfall_from_steps(
        [
            step("base", 100, added=True),
            step("defaulted", 110, default_used=True),
            step("target", 121),
        ],
        "premium",
        target_node_id="target",
        final_output_value=121,
    )

    assert isinstance(result, list)
    assert [entry["default_used"] for entry in result] == [False, True, False]
