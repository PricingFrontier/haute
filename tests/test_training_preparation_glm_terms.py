"""GLM term contract in route preparation: demand, feature selection, schema resolution."""

from __future__ import annotations

import polars as pl
import pytest

from haute._execution_context import ExecutionContext, ExecutionProfile
from haute.errors import HauteValidationError
from haute.routes._training_preparation import (
    _build_training_feature_selection,
    _glm_training_term_columns,
    _training_required_columns_by_node,
    _training_sink_exclusions,
    resolve_training_input_schema,
)
from tests.conftest import make_edge, make_graph, make_ready_file_input_config

pytestmark = pytest.mark.usefixtures("_widen_sandbox_root")

GLM_CONFIG = {
    "algorithm": "glm",
    "target": "y",
    "family": "gaussian",
    "terms": {
        "age": {"type": "linear"},
        "age_sq": {"type": "expression", "expr": "age ** 2"},
    },
    "interactions": [{"factors": ["income", "region"], "include_main": True}],
    "exclude": ["age"],
}


def test_required_columns_demand_includes_expression_and_interaction_only_columns():
    demand = _training_required_columns_by_node("m1", GLM_CONFIG)
    assert demand == {"m1": frozenset({"age", "income", "region", "y"})}


def test_glm_term_columns_are_the_resolved_model_columns():
    assert _glm_training_term_columns(GLM_CONFIG) == frozenset({"age", "income", "region"})
    assert _glm_training_term_columns({"algorithm": "catboost", "target": "y"}) is None
    assert _glm_training_term_columns({"algorithm": "glm", "target": "y", "terms": {}}) is None


SCHEMA = {
    "age": "Float64",
    "income": "Float64",
    "region": "String",
    "y": "Float64",
    "unused": "Float64",
}


def test_feature_selection_ignores_exclude_and_feature_columns_for_glm():
    payload = _build_training_feature_selection(
        {**GLM_CONFIG, "feature_columns": ["unused"]}, SCHEMA
    )
    assert payload.mode == "glm_terms"
    assert payload.features.items == ["age", "income", "region"]
    assert [(item.column, item.reason) for item in payload.excluded_columns.items] == [
        ("y", "target"),
        ("unused", "not_in_formula"),
    ]


def test_feature_selection_rejects_expression_keyed_by_a_column():
    with pytest.raises(HauteValidationError, match="names a column"):
        _build_training_feature_selection(GLM_CONFIG, {**SCHEMA, "age_sq": "Float64"})


def test_feature_selection_rejects_role_columns_and_unsupported_dtypes():
    with pytest.raises(HauteValidationError, match=r"role columns: 'income' \(weight\)"):
        _build_training_feature_selection({**GLM_CONFIG, "weight": "income"}, SCHEMA)
    with pytest.raises(HauteValidationError, match="'region' has dtype Date"):
        _build_training_feature_selection(GLM_CONFIG, {**SCHEMA, "region": "Date"})


def test_sink_exclusions_are_none_for_glm_and_configured_for_catboost():
    assert _training_sink_exclusions(GLM_CONFIG) is None
    assert _training_sink_exclusions({"algorithm": "catboost", "exclude": ["age"]}) == ["age"]
    assert _training_sink_exclusions({"algorithm": "catboost", "exclude": []}) is None


def test_resolve_training_input_schema_reflects_added_renamed_and_dropped_columns(tmp_path):
    """A Polars node adds x2, renames y to y2, drops w: the resolved schema is
    the transformed one, unprojected (unused columns retained)."""
    source = tmp_path / "src.parquet"
    pl.DataFrame({"x": [1.0, 2.0], "y": [3.0, 4.0], "w": [5.0, 6.0]}).write_parquet(source)
    graph = make_graph(
        {
            "nodes": [
                {
                    "id": "source",
                    "data": {
                        "label": "src",
                        "nodeType": "dataInput",
                        "config": make_ready_file_input_config(str(source)),
                    },
                },
                {
                    "id": "prep",
                    "data": {
                        "label": "prep",
                        "nodeType": "polars",
                        "config": {
                            "code": (
                                "df = src.with_columns(x2=pl.col('x') ** 2)"
                                ".rename({'y': 'y2'}).drop('w')"
                            )
                        },
                    },
                },
                {
                    "id": "model",
                    "data": {
                        "label": "model",
                        "nodeType": "modelling",
                        "config": {
                            "algorithm": "glm",
                            "target": "y2",
                            "terms": {"x2": {"type": "linear"}},
                        },
                    },
                },
            ],
            "edges": [
                make_edge("source", "prep").model_dump(),
                make_edge("prep", "model").model_dump(),
            ],
        }
    )

    context = ExecutionContext(
        operation="training_glm_schema",
        profile=ExecutionProfile.TRAINING_PREP,
    )
    schema = resolve_training_input_schema(graph, "model", None, "live", execution_context=context)
    assert schema == {"x": "Float64", "y2": "Float64", "x2": "Float64"}
