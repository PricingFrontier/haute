"""A tie among candidates identical in every column is shown, never guessed."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import polars as pl
import pytest

from haute._trace_correlation import _correlate_rows_posthoc, _find_matching_row
from haute._types import GraphNode, NodeData, NodeType
from haute.trace import execute_trace, trace_result_to_dict
from tests.conftest import make_edge, make_graph, make_source_node, make_transform_node

pytestmark = pytest.mark.usefixtures("_widen_sandbox_root")


def _node(node_id: str, node_type: NodeType, code: str | None = None) -> GraphNode:
    config: dict[str, Any] = {"code": code} if code is not None else {}
    return GraphNode(id=node_id, data=NodeData(label=node_id, nodeType=node_type, config=config))


def test_candidates_identical_in_every_column_return_their_values_without_a_position() -> None:
    frame = pl.DataFrame({"policy_id": [10, 10], "premium": [100.0, 100.0]})
    diagnostics: list[dict[str, object]] = []

    row, index = _find_matching_row(
        frame,
        {"policy_id": 10, "premium": 100.0},
        diagnostics=diagnostics,
        node_id="source",
        child_node_id="rating",
    )

    assert row == {"policy_id": 10, "premium": 100.0}
    assert index == -1
    assert diagnostics == [
        {
            "code": "identical_row_match",
            "severity": "info",
            "reason": "identical_rows",
            "message": (
                "Row correlation for node 'source' for child node 'rating' matched 2 rows "
                "identical in every column; any of them gives these values."
            ),
            "node_id": "source",
            "child_node_id": "rating",
            "match_strategy": "exact",
            "match_columns": ["policy_id", "premium"],
            "ignored_columns": [],
            "matched_row_count": 2,
            "matched_row_indices": [],
            "candidate_count": 2,
        }
    ]


def test_candidates_that_differ_outside_the_matched_columns_stay_ambiguous() -> None:
    frame = pl.DataFrame({"policy_id": [10, 10], "region": ["north", "south"]})
    diagnostics: list[dict[str, object]] = []

    row, index = _find_matching_row(
        frame, {"policy_id": 10}, diagnostics=diagnostics, node_id="source"
    )

    assert (row, index) == (None, -1)
    assert [diagnostic["reason"] for diagnostic in diagnostics] == ["duplicate_exact_match"]


def test_identity_is_proven_beyond_the_capped_candidate_list() -> None:
    # Only 16 candidate indices are kept; identity is counted over all rows.
    identical = pl.DataFrame({"bucket": ["same"] * 40, "value": [1] * 40})
    diagnostics: list[dict[str, object]] = []
    row, index = _find_matching_row(identical, {"bucket": "same"}, diagnostics=diagnostics)
    assert (row, index) == ({"bucket": "same", "value": 1}, -1)
    assert diagnostics[0]["candidate_count"] == 40

    differs_after_the_cap = identical.with_columns(
        value=pl.when(pl.int_range(pl.len()) == 39).then(2).otherwise(1)
    )
    row, index = _find_matching_row(differs_after_the_cap, {"bucket": "same"})
    assert (row, index) == (None, -1)


def test_a_sorted_duplicate_row_is_traced_to_its_identical_source_rows() -> None:
    source = pl.DataFrame({"a": [1, 1, 2], "b": [5, 5, 6]})
    ordered = source.sort("a", descending=True)
    diagnostics: list[dict[str, Any]] = []
    unresolved: dict[str, tuple[str, int]] = {}
    positions: dict[str, int] = {}

    rows = _correlate_rows_posthoc(
        {"source": source, "ordered": ordered},
        ["source", "ordered"],
        {"source": [], "ordered": ["source"]},
        "ordered",
        1,
        node_map={
            "source": _node("source", NodeType.DATA_INPUT),
            "ordered": _node("ordered", NodeType.POLARS, 'df = source.sort("a", descending=True)'),
        },
        diagnostics=diagnostics,
        unresolved=unresolved,
        row_positions=positions,
    )

    assert rows["source"] == {"a": 1, "b": 5}
    assert unresolved == {}
    assert "source" not in positions  # no physical row was chosen
    assert [(d["code"], d["node_id"], d["candidate_count"]) for d in diagnostics] == [
        ("identical_row_match", "source", 2)
    ]


def test_rows_made_identical_by_a_projection_do_not_name_a_source_row() -> None:
    # Two different source rows become identical once `id` is dropped, and a
    # join multiplies them. The projected step shows its values; the source
    # stays ambiguous instead of being aligned by an unchosen position.
    source = pl.DataFrame({"id": [1, 2], "x": ["a", "a"]})
    projected = source.select("x")
    lookup = pl.DataFrame({"x": ["a", "a"], "y": [10, 20]})
    joined = projected.join(lookup, on="x", how="left", maintain_order="left")
    diagnostics: list[dict[str, Any]] = []
    unresolved: dict[str, tuple[str, int]] = {}

    rows = _correlate_rows_posthoc(
        {"source": source, "projected": projected, "lookup": lookup, "joined": joined},
        ["source", "lookup", "projected", "joined"],
        {"source": [], "lookup": [], "projected": ["source"], "joined": ["projected", "lookup"]},
        "joined",
        0,
        node_map={
            "source": _node("source", NodeType.DATA_INPUT),
            "lookup": _node("lookup", NodeType.DATA_INPUT),
            "projected": _node("projected", NodeType.POLARS, 'df = source.select("x")'),
            "joined": _node(
                "joined",
                NodeType.POLARS,
                'df = projected.join(lookup, on="x", how="left", maintain_order="left")',
            ),
        },
        diagnostics=diagnostics,
        unresolved=unresolved,
    )

    assert rows["projected"] == {"x": "a"}
    assert rows["source"] is None
    assert unresolved["source"][0] == "duplicate_exact_match"


@pytest.mark.parametrize(
    "code",
    [
        'df = source.sort("region", descending=True)',
        'df = source.filter(pl.col("premium") > 0)',
        "df = source.unique(maintain_order=True)",
    ],
    ids=["sort", "filter", "unique"],
)
def test_a_trace_shows_identical_rows_as_one_step(tmp_path, code: str) -> None:
    path = tmp_path / "source.parquet"
    pl.DataFrame({"region": ["north", "north", "south"], "premium": [10, 10, 20]}).write_parquet(
        path
    )
    graph = make_graph(
        {
            "nodes": [
                make_source_node("source", str(path)),
                make_transform_node("shaped", code),
                make_transform_node(
                    "rated", "df = shaped.with_columns(charge=pl.col('premium') * 2)"
                ),
            ],
            "edges": [make_edge("source", "shaped"), make_edge("shaped", "rated")],
        }
    )
    rated = pl.read_parquet(path)
    row_index = next(
        index
        for index, row in enumerate(
            rated.sort("region", descending=True).iter_rows(named=True)
            if "sort" in code
            else rated.iter_rows(named=True)
        )
        if row["region"] == "north"
    )

    result = execute_trace(graph, row_index=row_index, target_node_id="rated", column="charge")

    assert result.omissions == []
    source_step = next(step for step in result.steps if step.node_id == "source")
    assert source_step.output_values == {"region": "north", "premium": 10}
    assert source_step.identical_row_count == 2
    payload = trace_result_to_dict(result)
    source_payload = next(step for step in payload["steps"] if step["node_id"] == "source")
    assert source_payload["identical_row_count"] == 2


def test_a_trace_keeps_the_gap_when_source_rows_differ_in_a_dropped_column(tmp_path) -> None:
    # The lookup reads at most two rows; identity is counted over the source's
    # whole plan, where the two north rows differ in `id`.
    path = tmp_path / "source.parquet"
    pl.DataFrame(
        {"id": [1, 2, 3], "region": ["north", "north", "south"], "premium": [10, 10, 20]}
    ).write_parquet(path)
    graph = make_graph(
        {
            "nodes": [
                make_source_node("source", str(path)),
                make_transform_node(
                    "shaped", 'df = source.select("region", "premium").sort("region")'
                ),
            ],
            "edges": [make_edge("source", "shaped")],
        }
    )

    result = execute_trace(graph, row_index=0, target_node_id="shaped", column="premium")

    assert [(omission.node_id, omission.reason) for omission in result.omissions] == [
        ("source", "duplicate_exact_match")
    ]
    shaped = next(step for step in result.steps if step.node_id == "shaped")
    assert shaped.identical_row_count is None


def test_float_tolerant_key_matches_are_not_taken_as_identical_rows() -> None:
    # The matcher's float comparison accepts rows 0 and 1 for the key; they
    # differ in premium, so they are not identical, whatever row 2 holds.
    frame = pl.DataFrame({"key": [10000000.009, 10000000.0, 10000000.018], "premium": [10, 20, 10]})
    diagnostics: list[dict[str, object]] = []

    row, index = _find_matching_row(frame, {"key": 10000000.0}, diagnostics=diagnostics)

    assert (row, index) == (None, -1)
    assert [diagnostic["reason"] for diagnostic in diagnostics] == ["duplicate_exact_match"]


def test_a_multi_frame_parent_keeps_the_winning_frames_identical_row_count() -> None:
    node_map = {
        "api": _node("api", NodeType.API_INPUT),
        "edge": GraphNode(
            id="edge",
            data=NodeData(
                label="edge",
                nodeType=NodeType.EDGE_JOIN,
                config={"how": "inner", "on": ["policy_id"], "suffix": "_right"},
            ),
        ),
    }
    policies = pl.DataFrame({"policy_id": ["P1"], "premium": [100]})
    rates = pl.DataFrame({"policy_id": ["P1", "P1"], "premium": [999, 999], "rate": [1.2, 1.2]})
    edge = policies.join(rates, on="policy_id", how="inner", suffix="_right")
    diagnostics: list[dict[str, Any]] = []

    rows = _correlate_rows_posthoc(
        {"api": {"policies": policies, "rates": rates}, "edge": edge},
        order=["api", "edge"],
        parents_of={"edge": ["api"]},
        target_node_id="edge",
        row_index=0,
        node_map=node_map,
        edge_metadata={("api", "edge"): [("policies", "base"), ("rates", "join")]},
        traced_column="rate",
        diagnostics=diagnostics,
    )

    assert rows["api"] == {"policy_id": "P1", "premium": 999, "rate": 1.2}
    assert [(d["code"], d["node_id"], d["candidate_count"]) for d in diagnostics] == [
        ("identical_row_match", "api", 2)
    ]


def test_a_row_scope_port_keeps_the_winning_ports_identical_row_count() -> None:
    from haute._trace_correlation import RowScopeResolver
    from haute._types import GraphEdge
    from haute.trace import _trace_lineage_alignments

    code = "df = policies.join(rates, how='cross', maintain_order='left_right')"
    node_map = {
        "api": _node("api", NodeType.API_INPUT),
        "priced": _node("priced", NodeType.POLARS, code),
    }
    edges = [
        GraphEdge(id=f"e_{handle}", source="api", target="priced", sourceHandle=handle)
        for handle in ("policies", "rates")
    ]
    alignments, input_names, child_input_names, _aliases = _trace_lineage_alignments(
        SimpleNamespace(relevant_edges=edges, node_map=node_map, submodels=None)
    )
    policies = pl.LazyFrame({"x": [1]})
    rates = pl.LazyFrame({"y": [5, 5, 5], "premium": [7, 7, 7]})
    priced = policies.join(rates, how="cross", maintain_order="left_right")
    plans = {"api": {"policies": policies, "rates": rates}, "priced": priced}
    edge_metadata = {("api", "priced"): [("policies", None), ("rates", None)]}
    frames: dict[str, Any] = {"priced": priced.head(2).collect()}
    resolver = RowScopeResolver(
        node_map=node_map,
        prefixes={"priced": 2},
        alignments=alignments,
        edge_metadata=edge_metadata,
        input_names=input_names,
        child_input_names=child_input_names,
        plans=lambda: plans,
        frames=frames,
        head_resolved={"priced"},
    )
    diagnostics: list[dict[str, Any]] = []

    rows = _correlate_rows_posthoc(
        frames,
        order=["api", "priced"],
        parents_of={"priced": ["api"]},
        target_node_id="priced",
        row_index=0,
        node_map=node_map,
        edge_metadata=edge_metadata,
        traced_column="premium",
        row_scope=resolver,
        diagnostics=diagnostics,
    )

    # The lookup holds two rows; the count is over the whole rates plan.
    assert rows["api"] == {"y": 5, "premium": 7}
    assert [(d["code"], d["node_id"], d["candidate_count"]) for d in diagnostics] == [
        ("identical_row_match", "api", 3)
    ]


@pytest.mark.parametrize(
    ("rows", "expected_count", "expected_omissions"),
    [
        # Three identical candidates: the two-row lookup cannot count them.
        ({"id": [7, 7, 7], "region": ["north"] * 3, "premium": [10] * 3}, 3, []),
        # The first two candidates are identical, the third differs in `id`.
        (
            {"id": [7, 7, 8], "region": ["north"] * 3, "premium": [10] * 3},
            None,
            [("source", "duplicate_exact_match")],
        ),
    ],
    ids=["three_identical", "third_differs"],
)
def test_a_trace_counts_identity_over_the_whole_source(
    tmp_path, rows: dict[str, list[Any]], expected_count: int | None, expected_omissions
) -> None:
    path = tmp_path / "source.parquet"
    pl.DataFrame(rows).write_parquet(path)
    graph = make_graph(
        {
            "nodes": [
                make_source_node("source", str(path)),
                make_transform_node(
                    "shaped", 'df = source.select("region", "premium").sort("region")'
                ),
            ],
            "edges": [make_edge("source", "shaped")],
        }
    )

    result = execute_trace(graph, row_index=0, target_node_id="shaped", column="premium")

    assert [(omission.node_id, omission.reason) for omission in result.omissions] == (
        expected_omissions
    )
    counts = {step.node_id: step.identical_row_count for step in result.steps}
    assert counts.get("source") == expected_count
