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
from tests._project import use_project

_SIGNATURE_FIELDS = (
    "lineage_fingerprint",
    "runtime_input_fingerprint",
    "source",
    "semantics_class",
    "enforce_contracts",
    "preamble_supplied",
    "execution_semantics_version",
    "polars_version",
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
                    "input_type": "file",
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
                {
                    "factors": [
                        {
                            "banding": "breakpoints",
                            "column": "premium",
                            "output_column": "band",
                            "rules": [],
                        }
                    ]
                },
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
    use_project(haute_scratch)
    (haute_scratch / "main.py").write_text("# pipeline\n", encoding="utf-8")
    pl.DataFrame({"premium": [1.0, 2.0]}).write_parquet(haute_scratch / "quotes.parquet")
    return haute_scratch


def test_signature_contract_is_the_eighth_checked_consumer() -> None:
    contract = CACHE_CONSUMER_CONTRACTS[CacheConsumer.NODE_SNAPSHOT_SIGNATURE]

    assert len(CacheConsumer) == 8
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
    assert _signature(graph).startswith("node-snapshot:v3:")


def test_another_polars_version_signs_another_snapshot(
    project: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A release can change what the same pipeline computes, so its results never mix."""
    graph = _graph(project)
    current = _signature(graph)

    monkeypatch.setattr(pl, "__version__", "9.9.9")

    assert _signature(graph) != current


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
                    "banding": "breakpoints",
                    "column": "premium",
                    "output_column": "band",
                    "rules": [{"boundary": "1", "label": "high"}],
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
    from haute._flatten import flatten_graph
    from haute._submodel_instances import qualified_runtime_node_id
    from haute.routes._submodel_ops import create_node_instance_graph

    wrapped = create_node_instance_graph(
        _graph(project), "join", "joined", read_utility=lambda _: pytest.fail("No utility expected")
    )
    graph = wrapped.graph
    graph.edges.append(
        GraphEdge(
            id="copy_input",
            source="transform",
            target=wrapped.instance_name,
            targetHandle="in__transform",
        )
    )
    target = qualified_runtime_node_id(wrapped.instance_name, "join_node")
    baseline = _signature(flatten_graph(graph), node_id=target)
    edited = graph.model_copy(deep=True)
    definition = edited.submodels[wrapped.definition_id]
    definition.graph.nodes[0] = definition.graph.nodes[0].with_config(
        {"code": "df = transform.head(1)"}
    )
    assert _signature(flatten_graph(edited), node_id=target) != baseline


# ------------------------------------------------------------ source switches


def _data_input(node_id: str, path: Path) -> GraphNode:
    return _node(
        node_id,
        NodeType.DATA_INPUT,
        {"input_type": "file", "format": "parquet", "mode": "scan", "path": str(path)},
    )


def _switch_graph(project: Path) -> PipelineGraph:
    """Two branches into a live switch that reads ``live_t`` for live and ``batch_t`` for batch."""
    for name in ("live", "batch"):
        pl.DataFrame({"premium": [1.0, 2.0]}).write_parquet(project / f"{name}.parquet")
    return PipelineGraph(
        nodes=[
            _data_input("live_src", project / "live.parquet"),
            _data_input("batch_src", project / "batch.parquet"),
            _node("live_t", NodeType.POLARS, {"code": "df = live_src"}),
            _node("batch_t", NodeType.POLARS, {"code": "df = batch_src"}),
            _node(
                "switch",
                NodeType.LIVE_SWITCH,
                {
                    "input_scenario_map": {"live_t": "live", "batch_t": "batch"},
                    "inputs": ["live_t", "batch_t"],
                },
            ),
            _node("below", NodeType.POLARS, {"code": "df = switch"}),
        ],
        edges=[
            GraphEdge(id="s1", source="live_src", target="live_t"),
            GraphEdge(id="s2", source="batch_src", target="batch_t"),
            GraphEdge(id="s3", source="live_t", target="switch"),
            GraphEdge(id="s4", source="batch_t", target="switch"),
            GraphEdge(id="s5", source="switch", target="below"),
        ],
        preamble="import polars as pl",
        source_file=str(project / "main.py"),
    )


def _rewrite(path: Path) -> None:
    time.sleep(0.01)
    pl.DataFrame({"premium": [1.0, 2.0, 3.0]}).write_parquet(path)


@pytest.mark.parametrize("change", ["branch_code", "branch_input_file"])
def test_the_branch_a_source_does_not_read_is_not_signed(project: Path, change: str) -> None:
    graph = _switch_graph(project)
    baseline = _signature(graph, node_id="below", source="batch")

    if change == "branch_code":
        graph = _with_config(graph, "live_t", code="df = live_src.head(1)")
    else:
        _rewrite(project / "live.parquet")

    assert _signature(graph, node_id="below", source="batch") == baseline


def test_the_unread_branchs_input_file_is_never_hashed(
    project: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from haute._json_shred import _source_proof

    hashed: list[Path] = []
    real = _source_proof._hash_file

    def counting(path: Path) -> str:
        hashed.append(Path(path).resolve())
        return real(path)

    monkeypatch.setattr(_source_proof, "_hash_file", counting)

    _signature(_switch_graph(project), node_id="below", source="batch")

    assert (project / "live.parquet").resolve() not in hashed
    assert (project / "batch.parquet").resolve() in hashed


@pytest.mark.parametrize("change", ["branch_code", "branch_input_file", "switch_mapping"])
def test_the_branch_a_source_reads_is_signed(project: Path, change: str) -> None:
    graph = _switch_graph(project)
    baseline = _signature(graph, node_id="below", source="batch")

    if change == "branch_code":
        graph = _with_config(graph, "batch_t", code="df = batch_src.head(1)")
    elif change == "branch_input_file":
        _rewrite(project / "batch.parquet")
    else:
        graph = _with_config(
            graph, "switch", input_scenario_map={"live_t": "batch", "batch_t": "live"}
        )

    assert _signature(graph, node_id="below", source="batch") != baseline


@pytest.mark.parametrize("edited", ["live_t", "batch_t"])
def test_a_source_the_switch_does_not_map_signs_both_branches(project: Path, edited: str) -> None:
    graph = _switch_graph(project)
    baseline = _signature(graph, node_id="below", source="scenario")

    changed = _with_config(graph, edited, code=f"df = {edited.removesuffix('_t')}_src.head(1)")

    assert _signature(changed, node_id="below", source="scenario") != baseline


def test_each_source_keeps_its_own_signature_across_a_toggle(project: Path) -> None:
    graph = _switch_graph(project)
    live = _signature(graph, node_id="below", source="live")
    batch = _signature(graph, node_id="below", source="batch")

    assert live != batch
    assert _signature(graph, node_id="below", source="live") == live
    assert _signature(graph, node_id="below", source="batch") == batch


def test_a_switch_free_lineage_is_the_upstream_subgraph(project: Path) -> None:
    """Without a live switch nothing is pruned, so no existing capture is re-keyed."""
    from haute._cache import graph_fingerprint
    from haute._graph_utils import upstream_subgraph
    from haute.execution import dataframe_graph_input_fingerprint, source_lineage_graph

    graph = _graph(project)
    for node_id in ("join", "explore"):
        lineage = source_lineage_graph(graph, node_id, source="live")
        upstream = upstream_subgraph(graph, node_id)

        assert graph_fingerprint(lineage) == graph_fingerprint(upstream)
        assert dataframe_graph_input_fingerprint(
            lineage, target_node_id=node_id, source="live"
        ) == dataframe_graph_input_fingerprint(graph, target_node_id=node_id, source="live")


def test_signing_an_unknown_node_fails(project: Path) -> None:
    with pytest.raises(ValueError, match="unknown node 'missing'"):
        _signature(_graph(project), node_id="missing")


def test_a_read_constant_re_signs_its_lineage_and_leaves_others_alone(project: Path) -> None:
    from haute._types import GlobalConstant

    def declare(graph: PipelineGraph, rate: float, other: float) -> PipelineGraph:
        return graph.model_copy(
            update={
                "global_constants": [
                    GlobalConstant(
                        name="rate", type="float", by_source={"live": 1.0, "nb_batch": rate}
                    ),
                    GlobalConstant(name="other", type="float", value=other),
                ]
            }
        )

    graph = _with_config(
        _graph(project),
        "transform",
        code="df = source.with_columns(r=pl.lit(global_constants.rate))",
    )
    graph = _with_config(
        graph, "downstream", code="df = join.with_columns(o=pl.lit(global_constants.other))"
    )
    baseline = _signature(declare(graph, rate=2.0, other=1.0))

    assert _signature(declare(graph, rate=2.5, other=1.0)) != baseline
    assert _signature(declare(graph, rate=2.0, other=9.0)) == baseline


def _signing_corpus(project: Path) -> list[tuple[PipelineGraph, tuple[str, ...]]]:
    """Graphs whose every node a resolution signs, with the sources each is read under.

    Relative input paths (which canonicalisation rewrites), a snapshot-backed CSV
    input, wired handles, a read Global Constant, nested non-ASCII configuration
    with floats, a file-sourced Model Score and a live switch.
    """
    from haute._types import GlobalConstant

    (project / "claims.csv").write_text("id,claim\n1,2.5\n", encoding="utf-8")
    pl.DataFrame({"id": [1], "premium": [1.0]}).write_parquet(project / "batch.parquet")
    joined = PipelineGraph(
        nodes=[
            _node(
                "quotes",
                NodeType.DATA_INPUT,
                {
                    "input_type": "file",
                    "format": "parquet",
                    "mode": "scan",
                    "path": "quotes.parquet",
                },
            ),
            _node(
                "claims",
                NodeType.DATA_INPUT,
                {"input_type": "file", "format": "csv", "mode": "scan", "path": "claims.csv"},
            ),
            _node(
                "joined",
                NodeType.POLARS,
                {
                    "code": "df = quotes.join(claims, how='cross').with_columns("
                    "r=pl.lit(global_constants.rate) * factor)",
                    "keyword_parameters": [
                        {"name": "factor", "default": 1.25},
                        {"name": "label", "default": "Région ☃"},
                    ],
                },
            ),
            _node(
                "rated",
                NodeType.RATING_STEP,
                {
                    "tables": [
                        {
                            "factors": ["region"],
                            "entries": [{"region": "Nörd", "value": 1.5}],
                        }
                    ]
                },
            ),
            _node(
                "scored",
                NodeType.MODEL_SCORE,
                {
                    "source_type": "file",
                    "model_path": "model.cbm",
                    "output_column": "prediction",
                    "task": "regression",
                },
            ),
            _node(
                "model",
                NodeType.MODELLING,
                {
                    "target": "claim",
                    "params": {"learning_rate": 0.05, "nested": {"a": [1, 2.5, "é"]}},
                    "feature_columns": ["Région", "âge"],
                },
            ),
        ],
        edges=[
            GraphEdge(id="e1", source="quotes", target="joined", targetHandle="quotes"),
            GraphEdge(id="e2", source="claims", target="joined", targetHandle="claims"),
            GraphEdge(id="e3", source="joined", target="rated"),
            GraphEdge(id="e4", source="rated", target="scored"),
            GraphEdge(id="e5", source="scored", target="model"),
        ],
        preamble="import polars as pl",
        global_constants=[GlobalConstant(name="rate", type="float", value=1.1)],
        source_file=str(project / "main.py"),
    )
    switched = PipelineGraph(
        nodes=[
            _node(
                "quotes",
                NodeType.DATA_INPUT,
                {
                    "input_type": "file",
                    "format": "parquet",
                    "mode": "scan",
                    "path": "quotes.parquet",
                },
            ),
            _node(
                "batch_quotes",
                NodeType.DATA_INPUT,
                {
                    "input_type": "file",
                    "format": "parquet",
                    "mode": "scan",
                    "path": "batch.parquet",
                },
            ),
            _node(
                "policies",
                NodeType.LIVE_SWITCH,
                {
                    "input_scenario_map": {"quotes": "live", "batch_quotes": "batch"},
                    "inputs": ["quotes", "batch_quotes"],
                },
            ),
            _node("priced", NodeType.POLARS, {"code": "df = policies"}),
        ],
        edges=[
            GraphEdge(id="s1", source="quotes", target="policies"),
            GraphEdge(id="s2", source="batch_quotes", target="policies"),
            GraphEdge(id="s3", source="policies", target="priced"),
        ],
        preamble="import polars as pl",
        source_file=str(project / "main.py"),
    )
    return [(_graph(project), ("live",)), (joined, ("live",)), (switched, ("live", "batch"))]


def test_a_resolution_s_memo_signs_every_node_exactly_as_a_fresh_signature_does(
    project: Path,
) -> None:
    """The memo a resolution signs with changes no identity byte (CACHE-S17)."""
    from haute._cache import GraphFingerprintMemo, graph_fingerprint
    from haute._data_points import DataPointResolver
    from haute._node_snapshots import NodeSnapshotStore

    store = NodeSnapshotStore(project)
    for graph, sources in _signing_corpus(project):
        for source in sources:
            resolver = DataPointResolver(graph, source=source, store=store)
            memo = GraphFingerprintMemo()
            for node in graph.nodes:
                fresh = node_snapshot_signature(
                    graph,
                    node.id,
                    source=source,
                    semantics_class=BOUNDED_SEMANTICS_CLASS,
                    enforce_contracts=True,
                )
                assert resolver.node_output_signature(node.id) == fresh, (source, node.id)
            for _ in range(2):
                assert graph_fingerprint(graph, memo=memo) == graph_fingerprint(graph)
        assert graph_fingerprint(graph) == graph_fingerprint(graph.model_copy())


def test_a_resolution_canonicalises_its_graph_and_reads_each_input_once(
    project: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Signing every node of a lineage reads its inputs once; a new resolution reads them again."""
    import haute.execution as execution
    from haute._data_points import DataPointResolver
    from haute._node_snapshots import NodeSnapshotStore

    calls = {"canonical": 0, "entries": []}
    canonicalise = execution._canonical_dataframe_execution_graph
    read_entry = execution._runtime_input_fingerprint_entry

    def counted_canonical(graph: PipelineGraph) -> PipelineGraph:
        calls["canonical"] += 1
        return canonicalise(graph)

    def counted_entry(graph: PipelineGraph, node: GraphNode) -> object:
        calls["entries"].append(node.id)
        return read_entry(graph, node)

    monkeypatch.setattr(execution, "_canonical_dataframe_execution_graph", counted_canonical)
    monkeypatch.setattr(execution, "_runtime_input_fingerprint_entry", counted_entry)
    graph = _signing_corpus(project)[1][0]
    store = NodeSnapshotStore(project)

    for resolution in (1, 2):
        resolver = DataPointResolver(graph, source="live", store=store)
        for node in graph.nodes:
            resolver.node_output_signature(node.id)

        assert calls["canonical"] == resolution
        assert sorted(calls["entries"]) == sorted(["claims", "quotes", "scored"] * resolution)
