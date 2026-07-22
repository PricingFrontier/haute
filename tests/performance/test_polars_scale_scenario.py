"""Deterministic join-to-training preparation scenario at selectable scales."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import polars as pl
import pytest
from polars.testing import assert_frame_equal

from haute._execution_context import ExecutionAdmission, ExecutionContext, ExecutionProfile
from haute._ram_estimate import MaterialisationEstimate
from haute._types import GraphEdge, GraphNode, NodeData, NodeType, PipelineGraph
from haute.errors import GroupByExecutionUnsupportedError
from haute.execution import ProjectionRequest, execute_lazy_graph, plan_execution_strategy
from haute.executor import _build_node_fn
from haute.routes._train_service import _build_training_feature_selection

pytestmark = pytest.mark.perf

_ROWS_BY_SCALE = {"ci": 20_000, "1m": 1_000_000, "10m": 10_000_000}
_UNUSED_COLUMNS = 12
_TRAINING_COLUMNS = (
    "policy_id",
    "feature_a",
    "feature_b",
    "region_factor",
    "target",
    "weight",
)


def _node(node_id: str, node_type: NodeType, config: dict[str, Any]) -> GraphNode:
    return GraphNode(
        id=node_id,
        data=NodeData(label=node_id, nodeType=node_type, config=config),
    )


def _edge(source: str, target: str) -> GraphEdge:
    return GraphEdge(id=f"{source}-{target}", source=source, target=target)


def _generate_inputs(tmp_path: Path, rows: int) -> tuple[Path, Path, list[str], list[str]]:
    base_path = tmp_path / "polars-scale-base.parquet"
    lookup_path = tmp_path / "polars-scale-lookup.parquet"
    base_columns = [
        "policy_id",
        "region_key",
        "feature_a",
        "feature_b",
        "target",
        "weight",
        *[f"unused_base_{index:02d}" for index in range(_UNUSED_COLUMNS)],
    ]
    lookup_columns = [
        "region_key",
        "region_factor",
        *[f"unused_lookup_{index:02d}" for index in range(_UNUSED_COLUMNS)],
    ]

    policy_id = pl.arange(0, rows, eager=True, dtype=pl.Int64).alias("policy_id")
    base = (
        pl.DataFrame([policy_id])
        .lazy()
        .with_columns(
            (pl.col("policy_id") % 64).cast(pl.Int32).alias("region_key"),
            (pl.col("policy_id") % 101).cast(pl.Float64).alias("feature_a"),
            ((pl.col("policy_id") * 3) % 211).cast(pl.Float64).alias("feature_b"),
            ((pl.col("policy_id") % 17) * 0.25).cast(pl.Float64).alias("target"),
            (1.0 + (pl.col("policy_id") % 5) * 0.1).cast(pl.Float64).alias("weight"),
            *[
                pl.lit(index).cast(pl.Int16).alias(f"unused_base_{index:02d}")
                for index in range(_UNUSED_COLUMNS)
            ],
        )
    )
    base.select(base_columns).sink_parquet(base_path)

    lookup = (
        pl.DataFrame({"region_key": range(64)})
        .lazy()
        .with_columns(
            (1.0 + pl.col("region_key") / 100.0).alias("region_factor"),
            *[
                pl.lit(index).cast(pl.Int16).alias(f"unused_lookup_{index:02d}")
                for index in range(_UNUSED_COLUMNS)
            ],
        )
    )
    lookup.select(lookup_columns).sink_parquet(lookup_path)
    return base_path, lookup_path, base_columns, lookup_columns


def _scenario_graph(
    base_path: Path,
    lookup_path: Path,
    base_columns: list[str],
    lookup_columns: list[str],
) -> PipelineGraph:
    return PipelineGraph(
        nodes=[
            _node(
                "base",
                NodeType.DATA_SOURCE,
                {
                    "path": str(base_path),
                    "contract": {"inputs": [], "outputs": base_columns},
                },
            ),
            _node(
                "lookup",
                NodeType.DATA_SOURCE,
                {
                    "path": str(lookup_path),
                    "contract": {"inputs": [], "outputs": lookup_columns},
                },
            ),
            _node(
                "training_input",
                NodeType.EDGE_JOIN,
                {
                    "baseInput": "base",
                    "joinInput": "lookup",
                    "how": "left",
                    "on": ["region_key"],
                    "suffix": "_lookup",
                    "contract": "opaque",
                },
            ),
        ],
        edges=[_edge("base", "training_input"), _edge("lookup", "training_input")],
    )


def _semantic_summary(frame: pl.LazyFrame) -> pl.LazyFrame:
    return frame.select(
        pl.len().alias("rows"),
        pl.col("feature_a").sum().alias("feature_a_sum"),
        pl.col("feature_b").sum().alias("feature_b_sum"),
        pl.col("region_factor").sum().alias("region_factor_sum"),
        pl.col("target").sum().alias("target_sum"),
        pl.col("weight").sum().alias("weight_sum"),
    )


def _budget_rejection(graph: PipelineGraph) -> GroupByExecutionUnsupportedError:
    aggregate_graph = PipelineGraph(
        nodes=[
            *graph.nodes,
            _node(
                "aggregate",
                NodeType.POLARS,
                {
                    "code": (
                        "df = df.group_by('region_factor').agg("
                        "pl.col('target').mean().alias('target'))"
                    )
                },
            ),
        ],
        edges=[*graph.edges, _edge("training_input", "aggregate")],
    )
    headroom = 64 * 1024 * 1024
    admission = ExecutionAdmission(
        operation="polars_scale_rejection",
        profile=ExecutionProfile.PREVIEW_EAGER,
        memory_limit_bytes=headroom,
        rss_at_admission_bytes=0,
        rss_limit_bytes=headroom,
        headroom_bytes=headroom,
        config_key="perf",
    )
    context = ExecutionContext(
        operation="polars_scale_rejection",
        profile=ExecutionProfile.PREVIEW_EAGER,
        admission=admission,
    )
    with pytest.raises(GroupByExecutionUnsupportedError) as raised:
        plan_execution_strategy(
            ProjectionRequest(
                graph=aggregate_graph,
                target_node_id="aggregate",
                profile=ExecutionProfile.PREVIEW_EAGER,
            ),
            execution_context=context,
            materialisation_estimate=MaterialisationEstimate.available(headroom + 1),
        )
    return raised.value


def test_generated_join_training_projection_scale_contract(
    tmp_path: Path,
    request: pytest.FixtureRequest,
) -> None:
    scale = os.environ.get("HAUTE_POLARS_PERF_SCALE", "ci")
    if scale not in _ROWS_BY_SCALE:
        raise AssertionError(f"Unsupported HAUTE_POLARS_PERF_SCALE={scale!r}")
    rows = _ROWS_BY_SCALE[scale]
    base_path, lookup_path, base_columns, lookup_columns = _generate_inputs(tmp_path, rows)
    graph = _scenario_graph(base_path, lookup_path, base_columns, lookup_columns)
    context = ExecutionContext(
        operation="polars_scale_join_training",
        profile=ExecutionProfile.TRAINING_PREP,
    )

    outputs, *_ = execute_lazy_graph(
        graph,
        _build_node_fn,
        target_node_id="training_input",
        source="batch",
        required_columns_by_node={"training_input": frozenset(_TRAINING_COLUMNS)},
        execution_context=context,
    )
    joined_lf = outputs["training_input"]
    joined_columns = joined_lf.collect_schema().names()
    assert set(joined_columns) == {*_TRAINING_COLUMNS, "region_key"}
    training_lf = joined_lf.select(_TRAINING_COLUMNS)

    actual = _semantic_summary(training_lf).collect(engine="streaming")
    reference_lf = (
        pl.scan_parquet(base_path)
        .join(
            pl.scan_parquet(lookup_path),
            on="region_key",
            how="left",
            suffix="_lookup",
        )
        .select(_TRAINING_COLUMNS)
    )
    expected = _semantic_summary(reference_lf).collect(engine="streaming")
    assert_frame_equal(actual, expected, check_exact=False, rel_tol=1e-12)

    feature_selection = _build_training_feature_selection(
        {
            "algorithm": "catboost",
            "target": "target",
            "weight": "weight",
            "id_columns": ["policy_id"],
            "feature_columns": ["feature_a", "feature_b", "region_factor"],
        },
        training_lf.collect_schema().names(),
    )
    assert feature_selection.features.items == ["feature_a", "feature_b", "region_factor"]

    metrics = context.metrics_payload(status="completed")
    strategy = metrics["execution_strategy"]
    assert strategy["status"] == "projected"
    assert strategy["boundedness"] == "bounded"
    widths = {item["node_id"]: item for item in metrics["column_widths"]["items"]}
    assert widths["base"]["physically_scanned_width"] == 6
    assert widths["lookup"]["physically_scanned_width"] == 2
    assert widths["base"]["physically_scanned_width"] < len(base_columns)
    assert widths["lookup"]["physically_scanned_width"] < len(lookup_columns)
    assert isinstance(metrics["observed_peak_rss_bytes"], int)

    rejection = _budget_rejection(graph)
    assert rejection.reason_code == "materialisation_exceeds_headroom"
    assert rejection.error_code == "group_by_execution_unsupported"

    request.node.user_properties.append(
        (
            "haute_perf_evidence",
            {
                "scenario": "generated_join_training_projection",
                "scale": scale,
                "input_rows": rows,
                "base_input_width": len(base_columns),
                "lookup_input_width": len(lookup_columns),
                "selected_strategy": strategy["strategy"],
                "strategy_status": strategy["status"],
                "source_widths": widths,
                "feature_selection": feature_selection.model_dump(mode="json"),
                "semantic_summary": actual.to_dicts()[0],
                "product_observed_peak_rss_bytes": metrics["observed_peak_rss_bytes"],
                "budgeted_rejection": rejection.to_payload(),
            },
        )
    )
