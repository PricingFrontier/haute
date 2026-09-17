"""Node-output snapshot signature and slot identity contracts (CACHE-S01)."""

from __future__ import annotations

import time
from pathlib import Path

import polars as pl
import pytest

from haute._cache import (
    CACHE_CONSUMER_CONTRACTS,
    CacheConsumer,
    CacheInputClass,
    checked_cache_inputs,
)
from haute._node_snapshots import (
    BOUNDED_SEMANTICS_CLASS,
    NodeSnapshotSlot,
    node_snapshot_signature,
    pipeline_source_file_key,
)
from haute._types import GraphEdge, GraphNode, NodeData, NodeType, PipelineGraph

_SIGNATURE_FIELDS = (
    "lineage_fingerprint",
    "runtime_input_fingerprint",
    "source",
    "semantics_class",
    "enforce_contracts",
    "preamble_supplied",
    "execution_semantics_version",
)


def _node(node_id: str, node_type: NodeType, config: dict[str, object]) -> GraphNode:
    return GraphNode(id=node_id, data=NodeData(label=node_id, nodeType=node_type, config=config))


def _graph(project: Path, *, preamble: str = "import polars as pl") -> PipelineGraph:
    return PipelineGraph(
        nodes=[
            _node(
                "source",
                NodeType.DATA_INPUT,
                {
                    "inputType": "file",
                    "format": "parquet",
                    "mode": "scan",
                    "path": str(project / "quotes.parquet"),
                },
            ),
            _node("transform", NodeType.POLARS, {"code": "df = source"}),
            _node("join", NodeType.POLARS, {"code": "df = transform"}),
            _node("downstream", NodeType.POLARS, {"code": "df = join"}),
            _node(
                "banding",
                NodeType.BANDING,
                {"factors": [{"column": "premium", "outputColumn": "band", "rules": []}]},
            ),
            _node(
                "explore",
                NodeType.EXPLORE,
                {"code": "df = join.head(10)", "pivots": [], "charts": []},
            ),
        ],
        edges=[
            GraphEdge(id="e1", source="source", target="transform"),
            GraphEdge(id="e2", source="transform", target="join"),
            GraphEdge(id="e3", source="join", target="downstream"),
            GraphEdge(id="e4", source="join", target="banding"),
            GraphEdge(id="e5", source="join", target="explore"),
        ],
        preamble=preamble,
        source_file=str(project / "main.py"),
    )


def _with_config(graph: PipelineGraph, node_id: str, **updates: object) -> PipelineGraph:
    return graph.model_copy(
        update={
            "nodes": [
                node.with_config({**node.data.config, **updates}) if node.id == node_id else node
                for node in graph.nodes
            ]
        }
    )


def _signature(graph: PipelineGraph, node_id: str = "join", **overrides: object) -> str:
    options: dict[str, object] = {
        "source": "live",
        "semantics_class": BOUNDED_SEMANTICS_CLASS,
        "enforce_contracts": True,
    }
    options.update(overrides)
    return node_snapshot_signature(graph, node_id, **options)  # type: ignore[arg-type]


@pytest.fixture()
def project(haute_scratch: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.chdir(haute_scratch)
    (haute_scratch / "main.py").write_text("# pipeline\n", encoding="utf-8")
    pl.DataFrame({"premium": [1.0, 2.0]}).write_parquet(haute_scratch / "quotes.parquet")
    return haute_scratch


def test_signature_contract_is_the_ninth_checked_consumer() -> None:
    contract = CACHE_CONSUMER_CONTRACTS[CacheConsumer.NODE_SNAPSHOT_SIGNATURE]

    assert len(CacheConsumer) == 9
    assert contract.fields == _SIGNATURE_FIELDS
    assert contract.input_classes[CacheInputClass.ROW_LIMIT].fields == ()
    assert contract.input_classes[CacheInputClass.REQUEST_SHAPE].fields == ()
    valid = dict.fromkeys(_SIGNATURE_FIELDS, "x")
    checked_cache_inputs(CacheConsumer.NODE_SNAPSHOT_SIGNATURE, valid)
    with pytest.raises(ValueError, match="missing.*source"):
        checked_cache_inputs(
            CacheConsumer.NODE_SNAPSHOT_SIGNATURE,
            {key: value for key, value in valid.items() if key != "source"},
        )
    with pytest.raises(ValueError, match="unknown.*generation"):
        checked_cache_inputs(CacheConsumer.NODE_SNAPSHOT_SIGNATURE, {**valid, "generation": "g1"})


def test_signature_is_deterministic(project: Path) -> None:
    graph = _graph(project)

    assert _signature(graph) == _signature(_graph(project))
    assert _signature(graph).startswith("node-snapshot:v1:")


@pytest.mark.parametrize(
    "change",
    [
        "source_config",
        "upstream_code",
        "own_code",
        "edge_handle",
        "preamble",
        "source_selection",
        "semantics_class",
        "contract_enforcement",
    ],
)
def test_upstream_and_execution_changes_change_the_signature(project: Path, change: str) -> None:
    graph = _graph(project)
    baseline = _signature(graph)
    other = project / "other.parquet"
    pl.DataFrame({"premium": [3.0]}).write_parquet(other)

    if change == "source_config":
        changed = _signature(_with_config(graph, "source", path=str(other)))
    elif change == "upstream_code":
        changed = _signature(_with_config(graph, "transform", code="df = source.head(1)"))
    elif change == "own_code":
        changed = _signature(_with_config(graph, "join", code="df = transform.head(1)"))
    elif change == "edge_handle":
        edges = [
            edge.model_copy(update={"sourceHandle": "other"}) if edge.id == "e1" else edge
            for edge in graph.edges
        ]
        changed = _signature(graph.model_copy(update={"edges": edges}))
    elif change == "preamble":
        changed = _signature(_graph(project, preamble="import polars as pl\nimport math"))
    elif change == "source_selection":
        changed = _signature(graph, source="batch")
    elif change == "semantics_class":
        changed = _signature(graph, semantics_class="preview")
    else:
        changed = _signature(graph, enforce_contracts=False)

    assert changed != baseline


def test_utility_module_edit_changes_the_signature(project: Path) -> None:
    utility = project / "utility.py"
    utility.write_text("RATE = 1\n", encoding="utf-8")
    graph = _graph(project, preamble="import utility")
    baseline = _signature(graph)

    utility.write_text("RATE = 2  # changed\n", encoding="utf-8")

    assert _signature(graph) != baseline


def test_runtime_file_rewrite_changes_the_signature(project: Path) -> None:
    graph = _graph(project)
    baseline = _signature(graph)

    time.sleep(0.01)
    pl.DataFrame({"premium": [1.0, 2.0, 3.0]}).write_parquet(project / "quotes.parquet")

    assert _signature(graph) != baseline


@pytest.mark.parametrize("change", ["downstream_code", "banding_rules", "sibling_explore"])
def test_downstream_edits_change_neither_slot_nor_signature(project: Path, change: str) -> None:
    graph = _graph(project)
    baseline = _signature(graph)
    slot = NodeSnapshotSlot(
        pipeline_source_file=pipeline_source_file_key(graph),
        node_id="join",
        source="live",
        semantics_class=BOUNDED_SEMANTICS_CLASS,
    )

    if change == "downstream_code":
        changed_graph = _with_config(graph, "downstream", code="df = join.head(5)")
    elif change == "banding_rules":
        changed_graph = _with_config(
            graph,
            "banding",
            factors=[
                {
                    "column": "premium",
                    "outputColumn": "band",
                    "rules": [{"op1": ">", "val1": "1", "assignment": "high"}],
                }
            ],
        )
    else:
        changed_graph = _with_config(graph, "explore", code="df = join.tail(3)")

    changed_slot = NodeSnapshotSlot(
        pipeline_source_file=pipeline_source_file_key(changed_graph),
        node_id="join",
        source="live",
        semantics_class=BOUNDED_SEMANTICS_CLASS,
    )
    assert changed_slot.digest == slot.digest
    assert _signature(changed_graph) == baseline


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("overview", {"cards": ["missing"]}),
        ("pivots", [{"id": "p1", "rows": ["premium"]}]),
        ("pivot_formulas", [{"id": "f1", "expression": "sum(premium)"}]),
        ("charts", [{"id": "c1"}]),
    ],
)
def test_explore_presentation_fields_do_not_change_its_own_signature(
    project: Path, field: str, value: object
) -> None:
    graph = _graph(project)
    baseline = _signature(graph, node_id="explore")

    assert _signature(_with_config(graph, "explore", **{field: value}), node_id="explore") == (
        baseline
    )


def test_slot_identity_carries_the_signature_and_round_trips(project: Path) -> None:
    slot = NodeSnapshotSlot(
        pipeline_source_file=pipeline_source_file_key(_graph(project)),
        node_id="join",
        source="live",
        semantics_class=BOUNDED_SEMANTICS_CLASS,
    )
    first = slot.identity("sig-1")
    second = slot.identity("sig-2")

    assert first.digest != second.digest
    assert NodeSnapshotSlot.from_identity(first) == (slot, "sig-1")


def test_an_instance_nodes_signature_follows_its_original(project: Path) -> None:
    graph = _graph(project)
    instance = _node("join_copy", NodeType.POLARS, {"instanceOf": "join"})
    graph = graph.model_copy(
        update={
            "nodes": [*graph.nodes, instance],
            "edges": [*graph.edges, GraphEdge(id="e6", source="transform", target="join_copy")],
        }
    )
    baseline = _signature(graph, node_id="join_copy")

    edited = _with_config(graph, "join", code="df = transform.head(1)")

    assert _signature(edited, node_id="join_copy") != baseline
