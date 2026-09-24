"""The graph walker: one walk per execution, under a collect policy."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any, cast

import polars as pl
import pytest

from haute._execution_context import ExecutionContext, ExecutionProfile
from haute._graph_walker import (
    CollectPolicy,
    WalkPurpose,
    WalkResult,
    prepare_walk,
    walk_graph,
)
from haute._node_snapshots import NodeSnapshotStore
from haute._seed_plans import SeedPlan, SeedPlanRequest, open_seed_plan
from haute._types import GraphEdge, GraphNode, NodeData, NodeType, PipelineGraph
from haute.errors import ContractMismatchError, SnapshotPlanInputsChangedError
from haute.executor import _build_node_fn, _compile_preamble, _pipeline_dir
from tests.test_snapshot_seeding import _context, _identity, _staging_dirs


def _edge(source: str, target: str, target_handle: str | None = None) -> GraphEdge:
    return GraphEdge(
        id=f"e_{source}_{target}", source=source, target=target, targetHandle=target_handle
    )


def _node(node_id: str, node_type: NodeType, **config: object) -> GraphNode:
    return GraphNode(
        id=node_id, data=NodeData(label=node_id, nodeType=node_type, config=dict(config))
    )


def _source(root: Path, node_id: str, frame: pl.DataFrame, **config: object) -> GraphNode:
    path = root / f"{node_id}.parquet"
    frame.write_parquet(path)
    return _node(
        node_id, NodeType.DATA_INPUT, inputType="file", format="parquet", path=str(path), **config
    )


def test_sink_policy_collects_nothing() -> None:
    assert CollectPolicy.sink().purpose is WalkPurpose.SINK


def test_a_sink_walk_returns_each_node_lazy_frame_and_the_prepared_order(
    haute_scratch: Path,
) -> None:
    graph = PipelineGraph(
        nodes=[
            _source(haute_scratch, "src", pl.DataFrame({"x": [1, 2, 3]})),
            _node("double", NodeType.POLARS, code="df = src.with_columns(y=pl.col('x') * 2)"),
        ],
        edges=[_edge("src", "double")],
    )

    walked = walk_graph(graph, _build_node_fn, policy=CollectPolicy.sink())

    assert walked.order == ["src", "double"]
    assert walked.parents_of == {"src": [], "double": ["src"]}
    assert walked.id_to_name == {"src": "src", "double": "double"}
    assert all(isinstance(frame, pl.LazyFrame) for frame in walked.frames.values())
    assert walked.frames["double"].collect()["y"].to_list() == [2, 4, 6]


def test_a_sink_walk_computes_nothing_until_its_caller_collects() -> None:
    evaluated: list[int] = []

    def counted(frame: pl.DataFrame) -> pl.DataFrame:
        evaluated.append(frame.height)
        return frame.with_columns(y=pl.col("x") * 10)

    def build(node: GraphNode, **_kwargs: Any) -> tuple[str, Callable[..., Any], bool]:
        if node.id == "src":
            return node.id, lambda: pl.LazyFrame({"x": [1, 2, 3]}), True
        return (
            node.id,
            lambda frame: frame.map_batches(counted, schema={"x": pl.Int64, "y": pl.Int64}),
            False,
        )

    graph = PipelineGraph(
        nodes=[_node("src", NodeType.DATA_INPUT), _node("udf", NodeType.POLARS)],
        edges=[_edge("src", "udf")],
    )

    walked = walk_graph(graph, build, policy=CollectPolicy.sink(), enforce_contracts=False)

    assert evaluated == []
    assert walked.frames["udf"].collect()["y"].to_list() == [10, 20, 30]
    assert evaluated == [3]


def test_a_sink_walk_hands_back_the_recipes_a_full_write_is_chunked_by(
    haute_scratch: Path,
) -> None:
    graph = PipelineGraph(
        nodes=[
            _source(haute_scratch, "base", pl.DataFrame({"k": [1, 2], "a": [10, 20]})),
            _source(haute_scratch, "lookup", pl.DataFrame({"k": [1, 2], "b": ["x", "y"]})),
            _node(
                "joined",
                NodeType.EDGE_JOIN,
                how="left",
                on=["k"],
            ),
            _node(
                "shaped",
                NodeType.POLARS,
                code="df = joined.with_columns(c=pl.col('a') + 1)",
                selected_columns=["k", "c"],
            ),
        ],
        edges=[
            _edge("base", "joined", "base"),
            _edge("lookup", "joined", "join"),
            _edge("joined", "shaped"),
        ],
    )

    walked = walk_graph(graph, _build_node_fn, policy=CollectPolicy.sink())

    assert set(walked.join_recipes) == {"joined"}
    recipe = walked.write_recipes["shaped"]
    assert recipe.fn is not None
    # The pre-shaping frame of a node that selects its own columns.
    assert set(walked.unshaped_frames) == {"shaped"}
    assert walked.unshaped_frames["shaped"].collect_schema().names() == ["k", "a", "b", "c"]
    assert walked.frames["shaped"].collect().sort("k").to_dicts() == [
        {"k": 1, "c": 11},
        {"k": 2, "c": 21},
    ]


def test_a_walk_result_needs_only_its_frames() -> None:
    result = WalkResult(frames={})

    assert (result.order, result.write_recipes, result.unshaped_frames) == ([], {}, {})


# ---------------------------------------------------------------------------
# Planned sink walks: a captured source waits for the input check
# ---------------------------------------------------------------------------


@pytest.fixture()
def project(haute_scratch: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.chdir(haute_scratch)
    (haute_scratch / "main.py").write_text("# pipeline\n", encoding="utf-8")
    return haute_scratch


def _shaped_source_graph(project: Path) -> PipelineGraph:
    """A Data Input with post-load code (a captured point) that selects its own columns."""
    return PipelineGraph(
        nodes=[
            _source(
                project,
                "src",
                pl.DataFrame({"id": [0, 1, 2], "a": [5, 6, 7], "b": [8, 9, 10]}),
                code="df = df.with_columns((pl.col('a') + 1).alias('a1')).sort('a1')",
                selected_columns=["id", "a1"],
            ),
            _node("T", NodeType.MODELLING),
        ],
        edges=[_edge("src", "T")],
        source_file=str(project / "main.py"),
    )


def _plan_request(graph: PipelineGraph) -> SeedPlanRequest:
    return SeedPlanRequest(
        graph=graph,
        target_node_id="T",
        source="live",
        profile=ExecutionProfile.TRAINING_PREP,
        required_columns_by_node={"T": ["a1"]},
    )


def _walk_under(plan: SeedPlan, graph: PipelineGraph, context: ExecutionContext) -> WalkResult:
    return walk_graph(
        graph,
        _build_node_fn,
        policy=CollectPolicy.sink(),
        target_node_id="T",
        preamble_ns=_compile_preamble(graph.preamble or "", pipeline_dir=_pipeline_dir(graph))
        or None,
        required_columns_by_node={"T": ["a1"]},
        execution_context=context,
        prepare_inputs=False,
        snapshot_plan=plan,
    )


def test_a_planned_walk_captures_a_shaped_source_with_its_columns_before_shaping(
    project: Path,
) -> None:
    store = NodeSnapshotStore(project)
    graph = _shaped_source_graph(project)
    context = _context(ExecutionProfile.TRAINING_PREP)
    with open_seed_plan(_plan_request(graph), store=store, execution_context=context) as plan:
        walked = _walk_under(plan, graph, context)
        output = walked.frames["T"].collect()

    metrics = context.metrics_payload(status="completed")
    captures = cast(list[dict[str, Any]], metrics["shared_snapshot_captures"])
    assert [(capture["node_id"], capture["outcome"]) for capture in captures] == [
        ("src", "published")
    ]
    latest = store.latest_generation(_identity(store, graph, "src"))
    assert latest is not None
    # The capture records what the source offered before its own selection.
    assert latest.unshaped_columns == tuple(
        (name, str(dtype)) for name, dtype in walked.unshaped_frames["src"].collect_schema().items()
    )
    assert [name for name, _dtype in latest.unshaped_columns] == ["id", "a", "b", "a1"]
    assert output["a1"].to_list() == [6, 7, 8]


def test_a_planned_walk_refuses_changed_inputs_before_capturing_a_source(
    project: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = NodeSnapshotStore(project)
    graph = _shaped_source_graph(project)
    context = _context(ExecutionProfile.TRAINING_PREP)
    staged: list[str] = []

    def staging_tripwire(self: NodeSnapshotStore, identity: Any, **_kwargs: Any) -> Any:
        staged.append(identity.digest)
        raise AssertionError("a capture was staged before the input check")

    with open_seed_plan(_plan_request(graph), store=store, execution_context=context) as plan:
        pl.DataFrame({"id": [0], "a": [1], "b": [2]}).write_parquet(project / "src.parquet")
        monkeypatch.setattr(NodeSnapshotStore, "stage_node_output", staging_tripwire)
        with pytest.raises(SnapshotPlanInputsChangedError):
            _walk_under(plan, graph, context)

    # The walk refused before staging anything, not after writing and discarding it.
    assert staged == []

    # Refused before anything was sunk, not merely before it was published.
    assert context.metrics_payload()["shared_snapshot_captures"] == []
    assert store.latest_generation(_identity(store, graph, "src")) is None
    assert not _staging_dirs(store)


# ---------------------------------------------------------------------------
# Display walks: preview and trace
# ---------------------------------------------------------------------------


def _chain_graph(root: Path) -> PipelineGraph:
    return PipelineGraph(
        nodes=[
            _source(root, "src", pl.DataFrame({"x": list(range(10))})),
            _node("double", NodeType.POLARS, code="df = src.with_columns(y=pl.col('x') * 2)"),
            _node(
                "shaped",
                NodeType.POLARS,
                code="df = double.with_columns(z=pl.col('y') + 1)",
                selected_columns=["x", "z"],
            ),
        ],
        edges=[_edge("src", "double"), _edge("double", "shaped")],
    )


def test_a_display_walk_collects_each_node_to_its_limit_and_keeps_uncapped_plans(
    haute_scratch: Path,
) -> None:
    walked = walk_graph(
        _chain_graph(haute_scratch),
        _build_node_fn,
        policy=CollectPolicy.display(row_limit=4, row_limits_by_node={"double": 2}),
    )

    assert walked.run_order == ["src", "double", "shaped"]
    assert {node_id: frame.height for node_id, frame in walked.collected.items()} == {
        "src": 4,
        "double": 2,
        "shaped": 4,
    }
    # Consumers never read a limited collection: every plan is the whole output.
    assert walked.frames["shaped"].collect().height == 10
    assert walked.available_columns["shaped"] == [
        ("x", "Int64"),
        ("y", "Int64"),
        ("z", "Int64"),
    ]
    assert walked.output_columns["shaped"] == [("x", "Int64"), ("z", "Int64")]
    assert set(walked.timings) == {"src", "double", "shaped"}


def test_a_display_walk_collects_only_the_named_nodes(haute_scratch: Path) -> None:
    walked = walk_graph(
        _chain_graph(haute_scratch),
        _build_node_fn,
        policy=CollectPolicy.display(collect={"shaped"}, row_limit=3),
    )

    assert set(walked.collected) == {"shaped"}
    assert walked.collected["shaped"].columns == ["x", "z"]
    # An uncollected ancestor still reports its full schema.
    assert walked.output_columns["double"] == [("x", "Int64"), ("y", "Int64")]


def test_a_display_walk_records_node_failures_when_the_policy_says_so(
    haute_scratch: Path,
) -> None:
    graph = _chain_graph(haute_scratch)
    graph.nodes[1] = _node("double", NodeType.POLARS, code="df = src.with_columns(y=undefined)")

    walked = walk_graph(graph, _build_node_fn, policy=CollectPolicy.display(record_failures=True))

    assert walked.collected["double"] is None
    assert "undefined" in walked.errors["double"]
    assert walked.errors["shaped"].startswith("Upstream node(s) failed: double: ")
    # The failing node was timed; the node below it never ran.
    assert "double" in walked.timings and "shaped" not in walked.timings
    assert set(walked.frames) == {"src"}

    with pytest.raises(NameError, match="undefined"):
        walk_graph(graph, _build_node_fn, policy=CollectPolicy.display())


@pytest.mark.parametrize(
    ("limits", "message"),
    [
        ({"": 1}, "must be node ids"),
        ({"node": 0}, "positive integers"),
        ({"node": True}, "positive integers"),
    ],
)
def test_collection_limits_are_positive_integers_keyed_by_node(
    limits: dict[str, Any], message: str
) -> None:
    with pytest.raises(ValueError, match=message):
        CollectPolicy.display(row_limits_by_node=limits)
    with pytest.raises(ValueError, match=message):
        CollectPolicy.display(column_limits_by_node=limits)


# ---------------------------------------------------------------------------
# Chunk walks: the chunked runner's per-chunk walk
# ---------------------------------------------------------------------------


def _chunk_chain() -> PipelineGraph:
    return PipelineGraph(
        nodes=[
            _node("start", NodeType.DATA_INPUT),
            _node("plus", NodeType.POLARS),
            _node("times", NodeType.POLARS),
        ],
        edges=[_edge("start", "plus"), _edge("plus", "times")],
    )


def _counting_chain_builder(
    built: list[str],
) -> Callable[..., tuple[str, Callable[..., Any], bool]]:
    def build(node: GraphNode, **_kwargs: Any) -> tuple[str, Callable[..., Any], bool]:
        built.append(node.id)
        if node.id == "plus":
            return node.id, lambda frame: frame.with_columns(y=pl.col("x") + 1), False
        return node.id, lambda frame: frame.with_columns(z=pl.col("y") * 10), False

    return build


def test_a_prepared_chunk_walk_builds_its_chain_once_and_walks_every_chunk() -> None:
    built: list[str] = []
    walk = prepare_walk(
        _chunk_chain(),
        _counting_chain_builder(built),
        policy=CollectPolicy.chunk(),
        target_node_id="times",
        walk_node_ids=["plus", "times"],
        output_demand={"plus": frozenset({"x", "y"}), "times": frozenset({"z"})},
    )

    first = walk.run({"start": pl.LazyFrame({"x": [1, 2], "unused": [0, 0]})})
    second = walk.run({"start": pl.LazyFrame({"x": [5], "unused": [0]})})

    # The start node's frame is each chunk; only the chain below it is built, once.
    assert built == ["plus", "times"]
    assert first.frames["times"].collect().to_dict(as_series=False) == {"z": [20, 30]}
    assert second.frames["times"].collect().to_dict(as_series=False) == {"z": [60]}
    assert first.frames["plus"].collect_schema().names() == ["x", "y"]


def test_a_chunk_walk_refuses_a_demand_its_node_does_not_produce() -> None:
    walk = prepare_walk(
        _chunk_chain(),
        _counting_chain_builder([]),
        policy=CollectPolicy.chunk(),
        target_node_id="times",
        walk_node_ids=["plus", "times"],
        output_demand={"plus": frozenset({"x", "absent"})},
    )

    with pytest.raises(ContractMismatchError, match="Chunk projection references columns"):
        walk.run({"start": pl.LazyFrame({"x": [1]})})
