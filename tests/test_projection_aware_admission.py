from __future__ import annotations

import polars as pl

from haute._execution_context import ExecutionAdmission, ExecutionContext, ExecutionProfile
from haute._ram_estimate import (
    MaterialisationEstimateBasis,
    _estimate_peak_bytes,
    _EstimateGraphIndex,
    estimate_materialisation_boundaries,
)
from haute.execution import ProjectionRequest, plan_execution_strategy
from haute.projection import ProjectionEdgeKey
from tests.conftest import make_edge, make_graph, make_ready_file_input_config


def _single_input_graph(path, *, columns: int = 8):
    frame = pl.DataFrame({f"column_{index}": range(10) for index in range(columns)})
    frame.write_parquet(path)
    edge = make_edge("source", "agg")
    graph = make_graph(
        {
            "nodes": [
                {
                    "id": "source",
                    "data": {
                        "label": "source",
                        "nodeType": "dataInput",
                        "config": make_ready_file_input_config(path),
                    },
                },
                {
                    "id": "agg",
                    "data": {
                        "label": "agg",
                        "nodeType": "polars",
                        "config": {"code": "df = df"},
                    },
                },
            ],
            "edges": [edge.model_dump()],
        }
    )
    return graph, edge


def _estimate(graph, edge, demand):
    return dict(
        estimate_materialisation_boundaries(
            graph,
            ["agg"],
            edge_demands={ProjectionEdgeKey.from_edge(edge): demand},
        )
    )["agg"]


def test_exact_edge_demand_sizes_only_physically_required_columns(tmp_path) -> None:
    graph, edge = _single_input_graph(tmp_path / "wide.parquet")

    estimate = _estimate(graph, edge, frozenset({"column_1", "column_6"}))

    assert estimate.estimated_peak_bytes == _estimate_peak_bytes(10, 2)
    assert estimate.basis is MaterialisationEstimateBasis.PROJECTED_COLUMNS
    assert "projected_column_count=2" in estimate.assumptions


def test_cardinality_only_demand_keeps_one_physical_carrier(tmp_path) -> None:
    graph, edge = _single_input_graph(tmp_path / "wide.parquet")

    estimate = _estimate(graph, edge, frozenset())

    assert estimate.estimated_peak_bytes == _estimate_peak_bytes(10, 1)
    assert estimate.basis is MaterialisationEstimateBasis.PROJECTED_COLUMNS
    assert "cardinality_carrier_columns=1" in estimate.assumptions


def test_opaque_or_absent_edge_demand_uses_complete_relevant_width(tmp_path) -> None:
    graph, edge = _single_input_graph(tmp_path / "wide.parquet")

    opaque = _estimate(graph, edge, None)
    absent = dict(estimate_materialisation_boundaries(graph, ["agg"], edge_demands={}))["agg"]

    assert opaque.estimated_peak_bytes == _estimate_peak_bytes(10, 8)
    assert absent.estimated_peak_bytes == _estimate_peak_bytes(10, 8)
    assert opaque.basis is MaterialisationEstimateBasis.COMPLETE_WIDTH_FALLBACK
    assert absent.basis is MaterialisationEstimateBasis.COMPLETE_WIDTH_FALLBACK


def test_unmapped_exact_demand_fails_closed_to_complete_width(tmp_path) -> None:
    graph, edge = _single_input_graph(tmp_path / "wide.parquet")

    estimate = _estimate(graph, edge, frozenset({"missing"}))

    assert estimate.estimated_peak_bytes == _estimate_peak_bytes(10, 8)
    assert estimate.basis is MaterialisationEstimateBasis.COMPLETE_WIDTH_FALLBACK
    assert "projection_fallback_reason=demanded_column_unmapped" in estimate.assumptions


def test_source_boundary_without_incoming_edges_uses_its_complete_width(tmp_path) -> None:
    path = tmp_path / "source.parquet"
    pl.DataFrame({"first": range(10), "second": range(10)}).write_parquet(path)
    graph = make_graph(
        {
            "nodes": [
                {
                    "id": "source",
                    "data": {
                        "label": "source",
                        "nodeType": "dataInput",
                        "config": make_ready_file_input_config(path),
                    },
                }
            ],
            "edges": [],
        }
    )

    estimate = dict(estimate_materialisation_boundaries(graph, ["source"], edge_demands={}))[
        "source"
    ]

    assert estimate.estimated_peak_bytes == _estimate_peak_bytes(10, 2)
    assert estimate.basis is MaterialisationEstimateBasis.COMPLETE_WIDTH_FALLBACK


def test_source_boundary_without_resolvable_columns_is_unavailable(
    tmp_path,
    monkeypatch,
) -> None:
    path = tmp_path / "source.parquet"
    pl.DataFrame({"first": range(10)}).write_parquet(path)
    graph = make_graph(
        {
            "nodes": [
                {
                    "id": "source",
                    "data": {
                        "label": "source",
                        "nodeType": "dataInput",
                        "config": make_ready_file_input_config(path),
                    },
                }
            ],
            "edges": [],
        }
    )
    monkeypatch.setattr(_EstimateGraphIndex, "resolve_columns", lambda *_args: None)

    estimate = dict(estimate_materialisation_boundaries(graph, ["source"], edge_demands={}))[
        "source"
    ]

    assert estimate.estimated_peak_bytes is None
    assert estimate.unavailable_reason == "target_schema_unavailable"


def test_unresolvable_input_schema_with_exact_demand_remains_unavailable(
    tmp_path,
    monkeypatch,
) -> None:
    graph, edge = _single_input_graph(tmp_path / "source.parquet", columns=2)
    monkeypatch.setattr(_EstimateGraphIndex, "resolve_columns", lambda *_args: None)

    estimate = _estimate(graph, edge, frozenset({"column_0"}))

    assert estimate.estimated_peak_bytes is None
    assert estimate.unavailable_reason == "target_schema_unavailable"


def test_multiple_input_edges_are_sized_independently_without_name_deduplication(
    tmp_path,
) -> None:
    left_path = tmp_path / "left.parquet"
    right_path = tmp_path / "right.parquet"
    pl.DataFrame({"shared": range(10), "left_1": range(10), "left_2": range(10)}).write_parquet(
        left_path
    )
    pl.DataFrame({"shared": range(20), "right_1": range(20), "right_2": range(20)}).write_parquet(
        right_path
    )
    left_edge = make_edge("left", "agg", target_handle="left")
    right_edge = make_edge("right", "agg", target_handle="right")
    graph = make_graph(
        {
            "nodes": [
                {
                    "id": node_id,
                    "data": {
                        "label": node_id,
                        "nodeType": "dataInput",
                        "config": make_ready_file_input_config(path),
                    },
                }
                for node_id, path in (("left", left_path), ("right", right_path))
            ]
            + [
                {
                    "id": "agg",
                    "data": {
                        "label": "agg",
                        "nodeType": "polars",
                        "config": {"code": "df = left"},
                    },
                }
            ],
            "edges": [left_edge.model_dump(), right_edge.model_dump()],
        }
    )

    estimate = dict(
        estimate_materialisation_boundaries(
            graph,
            ["agg"],
            edge_demands={
                ProjectionEdgeKey.from_edge(left_edge): frozenset({"shared"}),
                ProjectionEdgeKey.from_edge(right_edge): frozenset({"shared"}),
            },
        )
    )["agg"]

    assert estimate.estimated_peak_bytes == _estimate_peak_bytes(20, 2)
    assert estimate.basis is MaterialisationEstimateBasis.PROJECTED_COLUMNS


def test_request_planner_passes_proven_edge_demand_into_admission_estimate(tmp_path) -> None:
    path = tmp_path / "wide.parquet"
    frame = pl.DataFrame(
        {
            "segment": [index % 2 for index in range(10)],
            "premium": range(10),
            **{f"unused_{index}": range(10) for index in range(20)},
        }
    )
    frame.write_parquet(path)
    graph = make_graph(
        {
            "nodes": [
                {
                    "id": "source",
                    "data": {
                        "label": "source",
                        "nodeType": "dataInput",
                        "config": make_ready_file_input_config(path),
                    },
                },
                {
                    "id": "agg",
                    "data": {
                        "label": "agg",
                        "nodeType": "polars",
                        "config": {
                            "code": (
                                "df = df.group_by('segment').agg("
                                "pl.col('premium').sum().alias('premium'))"
                            )
                        },
                    },
                },
            ],
            "edges": [make_edge("source", "agg").model_dump()],
        }
    )
    admission = ExecutionAdmission(
        operation="preview",
        profile=ExecutionProfile.PREVIEW_EAGER,
        memory_limit_bytes=10_000,
        rss_at_admission_bytes=0,
        rss_limit_bytes=10_000,
        headroom_bytes=10_000,
        config_key="test",
    )
    context = ExecutionContext(
        operation="preview",
        profile=ExecutionProfile.PREVIEW_EAGER,
        admission=admission,
    )

    result = plan_execution_strategy(
        ProjectionRequest(
            graph=graph,
            target_node_id="agg",
            profile=ExecutionProfile.PREVIEW_EAGER,
            required_columns_by_node={"agg": {"premium"}},
        ),
        execution_context=context,
    )

    assert result.diagnostic.raw_estimated_peak_bytes == _estimate_peak_bytes(10, 2)
    assert result.diagnostic.estimate_admission_basis == "projected_columns"
