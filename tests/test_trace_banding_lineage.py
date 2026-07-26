"""Trace lineage tests for banding-created fields."""

from __future__ import annotations

import polars as pl
import pytest

from haute.graph_utils import GraphNode, NodeData, NodeType
from haute.trace import SchemaDiff, TraceResult, TraceStep, execute_trace
from tests.conftest import (
    make_edge as _edge,
)
from tests.conftest import (
    make_graph as _g,
)
from tests.conftest import (
    make_source_node as _source_node,
)
from tests.conftest import (
    make_transform_node as _transform_node,
)

pytestmark = pytest.mark.usefixtures("_widen_sandbox_root")


def _step_by_id(result: TraceResult, node_id: str) -> TraceStep:
    for step in result.steps:
        if step.node_id == node_id:
            return step
    raise KeyError(f"No step with node_id={node_id!r}")


def _banding_node(
    factor: dict,
    *,
    node_id: str = "banding",
    label: str = "Age banding",
) -> GraphNode:
    return GraphNode(
        id=node_id,
        data=NodeData(
            label=label,
            nodeType=NodeType.BANDING,
            config={"factors": [factor]},
        ),
    )


def test_build_input_sources_rejects_steps_outside_trace_order():
    """Lineage should fail loudly if called with a step outside the trace order."""
    from haute._trace_enrichment import _build_input_sources

    current_step = TraceStep(
        node_id="current",
        node_name="Current",
        node_type="polars",
        schema_diff=SchemaDiff(
            columns_added=["derived"],
            columns_removed=[],
            columns_modified=[],
            columns_passed=[],
        ),
        input_values={"source": 1},
        output_values={"derived": 2},
    )

    with pytest.raises(ValueError, match="current_step 'current' is not present"):
        _build_input_sources(
            ["source"],
            current_step,
            [],
            {},
            None,
        )


def test_banding_trace_shows_source_value_and_lineage(tmp_path):
    """A traced banding output explains the input value that produced the band."""
    p = tmp_path / "policies.parquet"
    pl.DataFrame({"driver_age": [22], "policy_id": [101]}).write_parquet(p)

    graph = _g(
        {
            "nodes": [
                _source_node("data", str(p)),
                _banding_node(
                    {
                        "column": "driver_age",
                        "outputColumn": "age_band",
                        "banding": "continuous",
                        "rules": [
                            {"op1": "<=", "val1": 25, "assignment": "young"},
                            {"op1": ">", "val1": 25, "assignment": "adult"},
                        ],
                        "default": "unknown",
                    }
                ),
            ],
            "edges": [_edge("data", "banding")],
        }
    )

    result = execute_trace(
        graph,
        row_index=0,
        target_node_id="banding",
        column="age_band",
    )
    step = _step_by_id(result, "banding")

    assert step.expression is not None
    assert step.expression["expression_type"] == "banding"
    assert step.expression["referenced_columns"] == ["driver_age"]
    assert "driver_age" in step.expression["expression_text"]
    assert "age_band" in step.expression["expression_text"]

    assert step.calculation is not None
    assert step.calculation["result_value"] == "young"
    assert step.calculation["input_values"] == {"driver_age": 22}
    assert step.expression["expression_text"] == "driver_age -> age_band"
    assert step.calculation["substituted_text"] == '22 -> "young"'
    assert step.calculation["substituted_text"] != "computed"

    input_sources = step.calculation["input_sources"]
    assert input_sources["driver_age"]["node_name"] == "data"
    assert input_sources["driver_age"]["result_value"] == 22


def test_banding_trace_continues_lineage_through_computed_input(tmp_path):
    """Banding input_sources recurse when the value being banded was computed."""
    p = tmp_path / "policies.parquet"
    pl.DataFrame({"raw_age": [21], "policy_id": [101]}).write_parquet(p)

    graph = _g(
        {
            "nodes": [
                _source_node("data", str(p)),
                _transform_node(
                    "prepare",
                    "df = df.with_columns(driver_age=pl.col('raw_age') + 1)",
                ),
                _banding_node(
                    {
                        "column": "driver_age",
                        "outputColumn": "age_band",
                        "banding": "continuous",
                        "rules": [
                            {"op1": "<=", "val1": 25, "assignment": "young"},
                            {"op1": ">", "val1": 25, "assignment": "adult"},
                        ],
                        "default": "unknown",
                    }
                ),
            ],
            "edges": [_edge("data", "prepare"), _edge("prepare", "banding")],
        }
    )

    result = execute_trace(
        graph,
        row_index=0,
        target_node_id="banding",
        column="age_band",
    )
    step = _step_by_id(result, "banding")
    assert step.calculation is not None

    driver_age_source = step.calculation["input_sources"]["driver_age"]
    assert driver_age_source["node_name"] == "prepare"
    assert driver_age_source["result_value"] == 22
    assert "raw_age" in driver_age_source["expression_text"]

    raw_age_source = driver_age_source["input_sources"]["raw_age"]
    assert raw_age_source["node_name"] == "data"
    assert raw_age_source["result_value"] == 21


def test_banding_trace_uses_latest_upstream_modifier_for_input_value(tmp_path):
    """When a banding input is overwritten upstream, trace the overwritten value."""
    p = tmp_path / "policies.parquet"
    pl.DataFrame({"policy_id": [101], "driver_age": [21]}).write_parquet(p)

    graph = _g(
        {
            "nodes": [
                _source_node("data", str(p)),
                _transform_node(
                    "prepare",
                    "df = df.with_columns(driver_age=pl.col('driver_age') + 1)",
                ),
                _banding_node(
                    {
                        "column": "driver_age",
                        "outputColumn": "age_band",
                        "banding": "continuous",
                        "rules": [
                            {"op1": "<=", "val1": 21, "assignment": "younger"},
                            {"op1": ">", "val1": 21, "assignment": "young"},
                        ],
                    }
                ),
            ],
            "edges": [_edge("data", "prepare"), _edge("prepare", "banding")],
        }
    )

    result = execute_trace(
        graph,
        row_index=0,
        target_node_id="banding",
        column="age_band",
    )
    step = _step_by_id(result, "banding")
    assert step.calculation is not None
    assert step.calculation["input_values"] == {"driver_age": 22}

    driver_age_source = step.calculation["input_sources"]["driver_age"]
    assert driver_age_source["node_name"] == "prepare"
    assert driver_age_source["result_value"] == 22
    assert "driver_age" in driver_age_source["expression_text"]

    raw_age_source = driver_age_source["input_sources"]["driver_age"]
    assert raw_age_source["node_name"] == "data"
    assert raw_age_source["result_value"] == 21


def test_banding_trace_continues_lineage_through_prior_banding(tmp_path):
    """When one banding consumes another banding's output, trace both levels."""
    p = tmp_path / "policies.parquet"
    pl.DataFrame({"driver_age": [22]}).write_parquet(p)

    graph = _g(
        {
            "nodes": [
                _source_node("data", str(p)),
                _banding_node(
                    {
                        "column": "driver_age",
                        "outputColumn": "age_band",
                        "banding": "continuous",
                        "rules": [
                            {"op1": "<=", "val1": 25, "assignment": "young"},
                            {"op1": ">", "val1": 25, "assignment": "adult"},
                        ],
                    },
                    node_id="age_banding",
                    label="Age banding",
                ),
                _banding_node(
                    {
                        "column": "age_band",
                        "outputColumn": "risk_band",
                        "banding": "categorical",
                        "rules": [
                            {"value": "young", "assignment": "low"},
                            {"value": "adult", "assignment": "standard"},
                        ],
                    },
                    node_id="risk_banding",
                    label="Risk banding",
                ),
            ],
            "edges": [_edge("data", "age_banding"), _edge("age_banding", "risk_banding")],
        }
    )

    result = execute_trace(
        graph,
        row_index=0,
        target_node_id="risk_banding",
        column="risk_band",
    )
    risk_step = _step_by_id(result, "risk_banding")
    assert risk_step.calculation is not None

    age_band_source = risk_step.calculation["input_sources"]["age_band"]
    assert age_band_source["node_name"] == "Age banding"
    assert age_band_source["result_value"] == "young"
    assert age_band_source["expression_text"] == "driver_age -> age_band"

    driver_age_source = age_band_source["input_sources"]["driver_age"]
    assert driver_age_source["node_name"] == "data"
    assert driver_age_source["result_value"] == 22


def test_breakpoint_banding_trace_shows_boundary_match(tmp_path):
    """Breakpoint banding traces use the generated interval rules."""
    p = tmp_path / "policies.parquet"
    pl.DataFrame({"driver_age": [30]}).write_parquet(p)

    graph = _g(
        {
            "nodes": [
                _source_node("data", str(p)),
                _banding_node(
                    {
                        "column": "driver_age",
                        "outputColumn": "age_band",
                        "banding": "breakpoints",
                        "rules": [
                            {"boundary": "25", "label": "young"},
                            {"boundary": "60", "label": "adult"},
                            {"boundary": "", "label": "senior"},
                        ],
                        "rightClosed": True,
                    }
                ),
            ],
            "edges": [_edge("data", "banding")],
        }
    )

    result = execute_trace(
        graph,
        row_index=0,
        target_node_id="banding",
        column="age_band",
    )
    step = _step_by_id(result, "banding")

    assert step.node_detail is not None
    assert step.node_detail["matched_band"] == "adult"
    assert step.node_detail["rule_index"] == 1
    assert step.calculation is not None
    assert step.calculation["input_values"] == {"driver_age": 30}
    assert step.calculation["substituted_text"] == '30 -> "adult"'
