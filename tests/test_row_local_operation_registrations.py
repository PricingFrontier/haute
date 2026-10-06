"""``pow``, ``replace_strict`` and ``dt.total_days`` are registered row-local.

Registration lets one traced row compute them; chunked execution still has no
proof for them, and every execution computes the same values it did before.
"""

from __future__ import annotations

from datetime import timedelta
from pathlib import Path
from typing import Any

import polars as pl
import pytest
from polars.testing import assert_frame_equal

from haute._execution_context import ExecutionProfile
from haute._graph_walker import CollectPolicy, walk_graph
from haute._node_snapshots import NodeSnapshotStore
from haute._polars_operations import (
    OperationClass,
    OperationPolicy,
    OperationReceiver,
    operation,
)
from haute._seed_plans import SeedPlanRequest, resolve_seed_plan
from haute._types import GraphEdge, GraphNode, NodeData, NodeType, PipelineGraph
from haute.chunking import classify_chunk_local_polars_code, classify_row_local_expression
from haute.execution import execute_lazy_graph
from haute.executor import _build_node_fn
from haute.projection import code_recompute_facts
from haute.trace import execute_trace
from tests.test_snapshot_seeding import _run

_REGISTERED = (
    (OperationReceiver.EXPR, None, "pow"),
    (OperationReceiver.EXPR, None, "replace_strict"),
    (OperationReceiver.NAMESPACE, "dt", "total_days"),
)
_FORMULA = (
    "df = src.with_columns(\n"
    "    squared=pl.col('a').pow(2),\n"
    "    band=pl.col('s').replace_strict({'x': 1, 'y': 2}, default=0),\n"
    "    days=pl.col('d').dt.total_days(),\n"
    ")"
)


@pytest.mark.parametrize(("receiver", "namespace", "name"), _REGISTERED)
def test_each_is_registered_row_local_without_a_chunk_proof(
    receiver: OperationReceiver, namespace: str | None, name: str
) -> None:
    entry = operation(receiver, name, namespace)

    assert entry is not None
    assert entry.operation_class is OperationClass.ROW_LOCAL
    assert entry.policy is OperationPolicy.ROW_LOCAL
    assert (entry.chunk_admitted, entry.lineage_supported) == (False, False)
    assert (entry.costly_to_recompute, entry.slice_transparent) == (False, True)


@pytest.mark.parametrize(
    ("code", "operator"),
    [
        ("df = df.with_columns(pl.col('a').pow(2))", "pow"),
        ("df = df.with_columns(pl.col('s').replace_strict({'x': 1}))", "replace_strict"),
        ("df = df.with_columns(pl.col('d').dt.total_days())", "dt.total_days"),
    ],
)
def test_chunked_execution_still_refuses_them(code: str, operator: str) -> None:
    decision = classify_chunk_local_polars_code(code, frame_names=["df"])

    assert not decision.eligible
    assert decision.blocking_operator == operator


@pytest.mark.parametrize(
    ("expression", "eligible"),
    [
        ("pl.col('a').pow(2)", True),
        ("pl.col('a').pow(pl.col('b'))", True),
        ("pl.col('a').pow(pl.col('b').mean())", False),
        ("pl.col('s').replace_strict({'x': 1, 'y': 2})", True),
        ("pl.col('s').replace_strict(old=['x'], new=[1], return_dtype=pl.Int64)", True),
        ("pl.col('s').replace_strict({'x': 1}, default=0)", True),
        # A mapping or default that is an expression could read other rows.
        ("pl.col('s').replace_strict({'x': 1}, default=pl.col('b'))", False),
        ("pl.col('s').replace_strict(pl.col('k'), pl.col('v'))", False),
        ("pl.col('s').replace_strict()", False),
        ("pl.col('d').dt.total_days()", True),
        ("pl.col('d').dt.total_days(fractional=True)", True),
    ],
)
def test_one_row_computes_them_with_row_local_arguments(expression: str, eligible: bool) -> None:
    assert classify_row_local_expression(expression).eligible is eligible


def test_their_nodes_are_cheap_slice_transparent_recompute_work() -> None:
    """Planning reads the registry too: a node using them is cheap and slice-transparent.

    Unregistered, ``pow`` and ``dt.total_days`` made a node opaque to slices and
    ``replace_strict`` an unresolved, costly call.
    """
    facts = code_recompute_facts(_FORMULA, frozenset({"src"}))

    assert (facts.cost, facts.slice_transparent, facts.full_input_work) == ("cheap", True, False)


def _source_frame() -> pl.DataFrame:
    return pl.DataFrame(
        {
            "id": list(range(6)),
            "a": [1, 2, 3, 4, 5, 6],
            "s": ["x", "y", "z", "x", "y", "z"],
            "d": [timedelta(days=day, hours=5) for day in range(6)],
        }
    )


def _expected() -> pl.DataFrame:
    return _source_frame().with_columns(
        squared=pl.col("a").pow(2),
        band=pl.col("s").replace_strict({"x": 1, "y": 2}, default=0),
        days=pl.col("d").dt.total_days(),
    )


def _node(node_id: str, node_type: NodeType, config: dict[str, Any]) -> GraphNode:
    return GraphNode(id=node_id, data=NodeData(label=node_id, nodeType=node_type, config=config))


def _graph(root: Path) -> PipelineGraph:
    """``src → n``, and ``n`` joined to a lookup: under a seed plan ``n`` feeds a join."""
    _source_frame().write_parquet(root / "quotes.parquet")
    pl.DataFrame({"id": list(range(6)), "w": [10] * 6}).write_parquet(root / "weights.parquet")
    parquet = {"inputType": "file", "format": "parquet", "mode": "scan"}
    return PipelineGraph(
        nodes=[
            _node("src", NodeType.DATA_INPUT, {**parquet, "path": str(root / "quotes.parquet")}),
            _node(
                "lookup", NodeType.DATA_INPUT, {**parquet, "path": str(root / "weights.parquet")}
            ),
            _node("n", NodeType.POLARS, {"code": _FORMULA}),
            _node("J", NodeType.POLARS, {"code": "df = n.join(lookup, on='id', how='left')"}),
            _node("T", NodeType.MODELLING, {}),
        ],
        edges=[
            GraphEdge(id="e0", source="src", target="n"),
            GraphEdge(id="e1", source="n", target="J"),
            GraphEdge(id="e2", source="lookup", target="J"),
            GraphEdge(id="e3", source="J", target="T"),
        ],
        source_file=str(root / "main.py"),
    )


@pytest.fixture()
def project(haute_scratch: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.chdir(haute_scratch)
    (haute_scratch / "main.py").write_text("# pipeline\n", encoding="utf-8")
    return haute_scratch


def test_every_execution_computes_the_values_it_did(project: Path) -> None:
    graph = _graph(project)
    columns = ["id", "a", "s", "d", "squared", "band", "days"]
    expected = _expected().select(columns)

    lazy_outputs, *_ = execute_lazy_graph(graph, _build_node_fn, target_node_id="n")
    preview = walk_graph(
        graph, _build_node_fn, policy=CollectPolicy.display(collect={"n"}), target_node_id="n"
    )
    planned = _run(graph, NodeSnapshotStore(project), profile=ExecutionProfile.TRAINING_PREP)

    assert_frame_equal(lazy_outputs["n"].collect().select(columns), expected)
    assert_frame_equal(preview.collected["n"].select(columns), expected)
    # A planned run, whose captures now see n as cheap and slice-transparent,
    # computes the same joined rows.
    joined = expected.join(pl.DataFrame({"id": list(range(6)), "w": [10] * 6}), on="id")
    assert_frame_equal(planned.frame.sort("id").select(joined.columns), joined)


def test_a_trace_computes_their_values_from_the_row(project: Path) -> None:
    graph = _graph(project)
    expected = _expected().row(4, named=True)

    for column in ("squared", "band", "days"):
        result = execute_trace(graph, row_index=4, target_node_id="n", column=column)
        step = next(step for step in result.steps if step.node_id == "n")
        calculation = step.calculation
        assert calculation is not None
        assert calculation["not_computable_reason"] is None
        assert "result_source" not in calculation
        assert calculation["result_value"] == expected[column]


@pytest.mark.parametrize(
    ("mapping", "cost", "slice_transparent"),
    [
        ("{'x': 1, 'y': 2}, default=0", "cheap", True),
        # A mapping read from columns looks each value up in whole columns: it is
        # classified exactly as it was before replace_strict was registered.
        ("pl.col('k'), pl.col('v')", "cheap", False),
        ("{'x': 1}, default=pl.col('b')", "costly", False),
        ("MAPPING", "costly", False),
    ],
)
def test_replace_strict_is_cheap_and_slice_transparent_only_with_a_literal_mapping(
    mapping: str, cost: str, slice_transparent: bool
) -> None:
    code = f"df = src.with_columns(band=pl.col('s').replace_strict({mapping}))"

    facts = code_recompute_facts(code, frozenset({"src"}))

    assert (facts.cost, facts.slice_transparent) == (cost, slice_transparent)


@pytest.mark.parametrize(
    ("mapping", "cost", "slice_transparent"),
    [
        ("{'x': 'a', 'y': 'b'}", "cheap", True),
        ("old=['x'], new=['a']", "cheap", True),
        # A mapping read from columns looks each value up in whole columns, the
        # same rule replace_strict follows.
        ("pl.col('k'), pl.col('v')", "cheap", False),
        ("old=pl.col('k'), new=pl.col('v')", "cheap", False),
        ("MAPPING", "costly", False),
    ],
)
def test_replace_is_cheap_and_slice_transparent_only_with_a_literal_mapping(
    mapping: str, cost: str, slice_transparent: bool
) -> None:
    code = f"df = src.with_columns(band=pl.col('s').replace({mapping}))"

    facts = code_recompute_facts(code, frozenset({"src"}))

    assert (facts.cost, facts.slice_transparent) == (cost, slice_transparent)


@pytest.mark.parametrize(
    ("method", "mapping", "captured"),
    [
        ("replace_strict", "pl.col('k'), pl.col('v')", True),
        ("replace_strict", "{'x': 1, 'y': 2}, default=0", False),
        ("replace", "pl.col('k'), pl.col('v')", True),
        ("replace", "{'x': 'a', 'y': 'b'}", False),
    ],
)
def test_a_join_feeder_using_a_column_mapping_stays_captured(
    project: Path, method: str, mapping: str, captured: bool
) -> None:
    graph = _graph(project)
    code = f"df = src.with_columns(band=pl.col('s').{method}({mapping}))"
    graph.nodes[2] = _node("n", NodeType.POLARS, {"code": code})
    request = SeedPlanRequest(
        graph=graph,
        target_node_id="T",
        source="live",
        profile=ExecutionProfile.TRAINING_PREP,
    )

    decision = resolve_seed_plan(request, store=NodeSnapshotStore(project))

    if captured:
        assert decision.captures["n"].kind == "structural"
    else:
        assert "n" not in decision.captures
        assert decision.skipped_captures["n"] == "slice_transparent_feeder"
