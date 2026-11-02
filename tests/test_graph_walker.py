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
    MeasuredInput,
    WalkPurpose,
    WalkRequest,
    WalkResult,
    _Walk,
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
                maintainOrder="left_right",
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


def _expander_graph(root: Path, *consumers: GraphNode, **shaping: object) -> PipelineGraph:
    """A Scenario Expander whose builder contract promises price_adjustment."""
    return PipelineGraph(
        nodes=[
            _source(root, "src", pl.DataFrame({"quote_id": [1, 2], "premium": [100.0, 200.0]})),
            _node(
                "grid",
                NodeType.SCENARIO_EXPANDER,
                column_name="price_adjustment",
                min_value=0.9,
                max_value=1.1,
                stepCount=3,
                **shaping,
            ),
            *consumers,
        ],
        edges=[_edge("src", "grid"), *(_edge("grid", node.id) for node in consumers)],
    )


_DISPLAY_AND_LAZY_WALKS = pytest.mark.parametrize(
    "policy",
    [CollectPolicy.display(), CollectPolicy.sink()],
    ids=["display", "lazy"],
)


@_DISPLAY_AND_LAZY_WALKS
@pytest.mark.parametrize(
    ("shaping", "columns"),
    [
        (
            {"selected_columns": ["quote_id", "premium", "scenario_index"]},
            ["quote_id", "premium", "scenario_index"],
        ),
        (
            {"column_renames": {"price_adjustment": "adj"}},
            ["quote_id", "premium", "scenario_index", "adj"],
        ),
    ],
    ids=["deselected", "renamed"],
)
def test_a_node_may_deselect_or_rename_a_column_it_creates(
    haute_scratch: Path, policy: CollectPolicy, shaping: dict[str, object], columns: list[str]
) -> None:
    walked = walk_graph(
        _expander_graph(haute_scratch, **shaping),
        _build_node_fn,
        policy=policy,
        enforce_contracts=True,
    )

    output = walked.frames["grid"].collect()
    assert output.columns == columns
    assert output.height == 6


@_DISPLAY_AND_LAZY_WALKS
def test_a_consumer_of_a_deselected_column_fails_at_its_own_input(
    haute_scratch: Path, policy: CollectPolicy
) -> None:
    consumer = _node(
        "uses_adj",
        NodeType.POLARS,
        code="df = grid.with_columns(scaled=pl.col('premium') * pl.col('price_adjustment'))",
        contract={"inputs": ["premium", "price_adjustment"], "outputs": ["scaled"]},
    )
    graph = _expander_graph(
        haute_scratch, consumer, selected_columns=["quote_id", "premium", "scenario_index"]
    )

    with pytest.raises(ContractMismatchError) as raised:
        walk_graph(graph, _build_node_fn, policy=policy, enforce_contracts=True)

    assert str(raised.value) == (
        "'uses_adj' needs the column 'price_adjustment', which is not in its input. "
        "(node_id=uses_adj)"
    )


@_DISPLAY_AND_LAZY_WALKS
def test_a_shaping_node_that_does_not_create_a_promised_column_still_fails(
    haute_scratch: Path, policy: CollectPolicy
) -> None:
    graph = PipelineGraph(
        nodes=[
            _source(haute_scratch, "src", pl.DataFrame({"a": [1, 2], "b": [3, 4]})),
            _node(
                "t",
                NodeType.POLARS,
                code="df = src.with_columns(c=pl.col('a'))",
                contract={"inputs": ["a"], "outputs": ["new_col"]},
                selected_columns=["a", "c"],
            ),
        ],
        edges=[_edge("src", "t")],
    )

    with pytest.raises(ContractMismatchError) as raised:
        walk_graph(graph, _build_node_fn, policy=policy, enforce_contracts=True)

    assert str(raised.value) == (
        "'t' did not create the column 'new_col', which its contract says it outputs. (node_id=t)"
    )


@pytest.mark.parametrize("budgeted", [True, False])
def test_a_native_allocation_failure_ends_the_walk_as_a_memory_error(
    haute_scratch: Path, budgeted: bool
) -> None:
    """A bare MemoryError is the run exceeding its memory cap, never the node's own failure.

    Native code (CatBoost's ``bad allocation``) fails an allocation the
    process's cap refuses with a bare MemoryError; recorded at the node it
    read as a fault of whichever node happened to allocate.
    """
    from haute._execution_context import ExecutionMemoryLimitExceededError, current_rss_bytes

    graph = _chain_graph(haute_scratch)
    graph.nodes[1] = _node("double", NodeType.POLARS, code="raise MemoryError('bad allocation')")
    limit = 4 * 1024**3
    context = (
        ExecutionContext(
            "pipeline_preview",
            ExecutionProfile.PREVIEW_EAGER,
            memory_limit_bytes=limit,
            memory_baseline_bytes=current_rss_bytes(),
        )
        if budgeted
        else None
    )

    with pytest.raises(MemoryError) as raised:
        walk_graph(
            graph,
            _build_node_fn,
            policy=CollectPolicy.display(record_failures=True),
            execution_context=context,
        )

    if budgeted:
        assert isinstance(raised.value, ExecutionMemoryLimitExceededError)
        assert raised.value.limit_bytes == limit
        assert "memory growth budget" in str(raised.value)
    else:
        # No budget to name: the allocation failure itself ends the walk.
        assert type(raised.value) is MemoryError


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
# Failure paths every walk keeps
# ---------------------------------------------------------------------------


def _two_node_graph() -> PipelineGraph:
    return PipelineGraph(
        nodes=[_node("src", NodeType.DATA_INPUT), _node("child", NodeType.POLARS)],
        edges=[_edge("src", "child")],
    )


def _builder(
    source_result: object, child: Callable[..., Any] | None = None
) -> Callable[..., tuple[str, Callable[..., Any], bool]]:
    def build(node: GraphNode, **_kwargs: Any) -> tuple[str, Callable[..., Any], bool]:
        if node.id == "src":
            return node.id, lambda: source_result, True
        return node.id, child or (lambda frame: frame), False

    return build


def test_a_sink_walk_records_the_width_of_a_collected_source() -> None:
    context = ExecutionContext(operation="walk", profile=ExecutionProfile.LAZY_SINK)

    walked = walk_graph(
        _two_node_graph(),
        _builder(pl.DataFrame({"x": [1], "y": [2]})),
        policy=CollectPolicy.sink(),
        enforce_contracts=False,
        execution_context=context,
    )

    assert isinstance(walked.frames["src"], pl.LazyFrame)
    widths = cast(dict[str, Any], context.metrics_payload(status="completed")["column_widths"])
    by_node = {item["node_id"]: item for item in widths["items"]}
    assert by_node["src"]["output_width"] == 2


@pytest.mark.parametrize("record_failures", [False, True])
def test_a_display_walk_refuses_a_result_that_is_not_a_frame(record_failures: bool) -> None:
    policy = CollectPolicy.display(record_failures=record_failures)
    if not record_failures:
        with pytest.raises(TypeError, match="returned int; expected a Polars frame"):
            walk_graph(_two_node_graph(), _builder(7), policy=policy, enforce_contracts=False)
        return

    walked = walk_graph(_two_node_graph(), _builder(7), policy=policy, enforce_contracts=False)

    assert walked.errors["src"] == "Node 'src' returned int; expected a Polars frame."
    assert walked.errors["child"].startswith("Upstream node(s) failed: src: ")


def test_a_display_walk_refuses_a_bundle_frame_that_is_not_a_frame() -> None:
    bundle = {"quotes": pl.LazyFrame({"x": [1]}), "broken": "not a frame"}

    with pytest.raises(TypeError, match="frame 'broken' is not a Polars frame"):
        walk_graph(
            _two_node_graph(),
            _builder(bundle),
            policy=CollectPolicy.display(),
            enforce_contracts=False,
            target_node_id="src",
        )


def test_a_display_walk_refuses_a_projection_its_node_does_not_produce() -> None:
    """The caller's demand, carried to a parent that lacks the column, fails loudly."""
    graph = PipelineGraph(
        nodes=[
            _node("src", NodeType.DATA_INPUT),
            _node("child", NodeType.POLARS, code="df = src.with_columns(b=pl.col('a') + 1)"),
        ],
        edges=[_edge("src", "child")],
    )

    def build(node: GraphNode, **kwargs: Any) -> tuple[str, Callable[..., Any], bool]:
        if node.id == "src":
            return node.id, lambda: pl.LazyFrame({"x": [1]}), True
        return _build_node_fn(node, **kwargs)

    with pytest.raises(ContractMismatchError, match="Eager projection references columns"):
        walk_graph(
            graph,
            build,
            policy=CollectPolicy.display(),
            required_columns_by_node={"child": ["b"]},
            enforce_contracts=False,
        )


def test_a_planned_sink_walk_stops_at_a_source_that_fails_to_build(
    project: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A failing source stops the walk while sources bind: nothing is captured first.

    The captured source binds before the failing one, so a walk that held the
    failure until the node's turn would capture it before reporting.
    """
    store = NodeSnapshotStore(project)
    captured = _shaped_source_graph(project)
    good = captured.nodes[0].model_copy(update={"id": "good"})
    good.data.label = "good"
    bad = _source(project, "bad", pl.DataFrame({"id": [0], "w": [1]}), code="df = df")
    graph = PipelineGraph(
        nodes=[
            good,
            bad,
            _node("J", NodeType.POLARS, code="df = good.join(bad, on='id', how='left')"),
            _node("T", NodeType.MODELLING),
        ],
        edges=[_edge("good", "J"), _edge("bad", "J"), _edge("J", "T")],
        source_file=str(project / "main.py"),
    )
    context = _context(ExecutionProfile.TRAINING_PREP)
    staged: list[str] = []
    real_stage = NodeSnapshotStore.stage_node_output

    def recording_stage(self: NodeSnapshotStore, identity: Any, **kwargs: Any) -> Any:
        staged.append(identity.digest)
        return real_stage(self, identity, **kwargs)

    def failing(node: GraphNode, **kwargs: Any) -> tuple[str, Callable[..., Any], bool]:
        name, fn, is_source = _build_node_fn(node, **kwargs)
        if node.id != "bad":
            return name, fn, is_source

        def broken() -> pl.LazyFrame:
            raise RuntimeError("the source cannot be read")

        return name, broken, is_source

    request = SeedPlanRequest(
        graph=graph, target_node_id="T", source="live", profile=ExecutionProfile.TRAINING_PREP
    )
    with open_seed_plan(request, store=store, execution_context=context) as plan:
        assert "good" in plan.decision.captures
        monkeypatch.setattr(NodeSnapshotStore, "stage_node_output", recording_stage)
        with pytest.raises(RuntimeError, match="the source cannot be read"):
            walk_graph(
                graph,
                failing,
                policy=CollectPolicy.sink(),
                target_node_id="T",
                execution_context=context,
                prepare_inputs=False,
                snapshot_plan=plan,
            )

    assert staged == []


def test_a_walk_refuses_a_node_whose_parent_frame_is_missing() -> None:
    """A defensive invariant: every parent is built or seeded before its children."""
    walk = _Walk(
        WalkRequest(graph=_two_node_graph(), build_node_fn=_builder(pl.LazyFrame({"x": [1]}))),
        CollectPolicy.sink(),
    )
    walk.prepare()
    walk._init_walk_state()

    with pytest.raises(ValueError, match=r"Node 'child' is missing input\(s\) from: \['src'\]"):
        walk._visit("child")


class _RowCounts:
    """Measurement queries that count rows and read every column."""

    def input_query(self, node: GraphNode, measured: MeasuredInput) -> pl.LazyFrame:
        return measured.frame.select(pl.len().alias("rows"), pl.all().null_count().sum())

    def joint_query(self, node: GraphNode, inputs: object) -> None:
        return None

    def output_query(
        self, node: GraphNode, port: str | None, frame: pl.LazyFrame, inputs: object
    ) -> pl.LazyFrame:
        return frame.select(pl.len().alias("rows"), pl.all().null_count().sum())


def test_a_measuring_walk_records_each_failure_against_the_node_it_belongs_to(
    haute_scratch: Path,
) -> None:
    """A builder's failure is its own node's; a failure collecting an input is
    its producer's, at or upstream; independent branches still measure."""
    graph = PipelineGraph(
        nodes=[
            _source(haute_scratch, "src", pl.DataFrame({"x": ["1", "two", "3"]})),
            _node("unbuildable", NodeType.RATING_STEP, tables="not a list"),
            _node(
                "as_int", NodeType.POLARS, code="df = src.with_columns(pl.col('x').cast(pl.Int64))"
            ),
            _node("reads_unbuildable", NodeType.POLARS, code="df = unbuildable"),
            _node("reads_as_int", NodeType.POLARS, code="df = as_int"),
            _node("also_reads_as_int", NodeType.POLARS, code="df = as_int.head(1)"),
            _node("independent", NodeType.POLARS, code="df = src.with_columns(y=pl.lit(1))"),
        ],
        edges=[
            _edge("src", "unbuildable"),
            _edge("src", "as_int"),
            _edge("unbuildable", "reads_unbuildable"),
            _edge("as_int", "reads_as_int"),
            _edge("as_int", "also_reads_as_int"),
            _edge("src", "independent"),
        ],
    )
    checked = ("reads_unbuildable", "reads_as_int", "also_reads_as_int", "independent")

    walked = walk_graph(
        graph,
        _build_node_fn,
        policy=CollectPolicy.measuring(checked=checked, row_bound=2, queries=_RowCounts()),
    )

    assert walked.frames == {}
    assert {
        node: failure.at_or_upstream for node, failure in walked.attributed_failures.items()
    } == {
        "unbuildable": False,
        "as_int": True,
    }
    inherited = {
        node: (measurement.status, measurement.failure and measurement.failure.node_id)
        for node, measurement in walked.measurements.items()
    }
    assert inherited == {
        "reads_unbuildable": ("upstream_failed", "unbuildable"),
        "reads_as_int": ("upstream_failed", "as_int"),
        "also_reads_as_int": ("upstream_failed", "as_int"),
        "independent": ("measured", None),
    }
    independent = walked.measurements["independent"]
    # Each measured frame is cut to the row bound.
    assert independent.inputs[0] == ("src", {"rows": 2, "x": 0})
    assert independent.outputs == ((None, {"rows": 2, "x": 0, "y": 0}),)


def test_a_measuring_policy_carries_queries_and_a_row_bound() -> None:
    with pytest.raises(ValueError, match="carries queries"):
        CollectPolicy(purpose=WalkPurpose.MEASURE, row_limit=10)
    with pytest.raises(ValueError, match="carries queries"):
        CollectPolicy(purpose=WalkPurpose.DISPLAY, queries=_RowCounts())
    with pytest.raises(ValueError, match="row bound"):
        CollectPolicy(purpose=WalkPurpose.MEASURE, queries=_RowCounts())


def _diamond_walk(shared_code: str, *, row_limit: int | None) -> tuple[WalkResult, list[int]]:
    """A source two branches read through *shared*, collected once where they meet.

    The source counts its computations; *shared*'s declared code is what the walker
    classifies, while its builder only adds a column.
    """
    reads: list[int] = []

    def counted(frame: pl.DataFrame) -> pl.DataFrame:
        reads.append(frame.height)
        return frame

    def node(node_id: str, node_type: NodeType = NodeType.POLARS, **config: object) -> GraphNode:
        return GraphNode(
            id=node_id, data=NodeData(label=node_id, nodeType=node_type, config=dict(config))
        )

    def build(built: GraphNode, **_kwargs: object) -> tuple[str, object, bool]:
        steps = {
            "src": lambda: pl.LazyFrame({"x": list(range(100))}).map_batches(counted),
            "shared": lambda f: f.with_columns(y=pl.col("x") * 2),
            "low": lambda f: f.filter(pl.col("y") < 50),
            "high": lambda f: f.filter(pl.col("y") >= 50).select(pl.col("x").alias("x_high")),
            "both": lambda low, high: pl.concat([low.select("x"), high.select(x="x_high")]),
        }
        return built.id, steps[built.id], built.id == "src"

    graph = PipelineGraph(
        nodes=[
            node("src", NodeType.DATA_INPUT),
            node("shared", code=shared_code),
            *(node(n) for n in ("low", "high", "both")),
        ],
        edges=[
            GraphEdge(id="1", source="src", target="shared"),
            GraphEdge(id="2", source="shared", target="low"),
            GraphEdge(id="3", source="shared", target="high"),
            GraphEdge(id="4", source="low", target="both"),
            GraphEdge(id="5", source="high", target="both"),
        ],
    )
    result = walk_graph(
        graph,
        build,
        policy=CollectPolicy.display(collect=["both"], row_limit=row_limit, record_failures=True),
    )
    assert result.errors == {}
    return result, reads


@pytest.mark.parametrize(
    "shared_code",
    [
        "df = src.group_by('x').agg(pl.len())",
        "df = src.unique()",
        "df = src.map_batches(score)",
    ],
)
def test_a_limited_preview_shares_a_frame_its_row_limit_cannot_reach(shared_code: str) -> None:
    """The branches meet in the collected node and the shared frame's code holds an
    operation the limit cannot pass: one cache, one computation."""
    result, reads = _diamond_walk(shared_code, row_limit=10)

    assert reads == [100]
    assert "CACHE" in result.frames["low"].explain(optimized=False)


@pytest.mark.parametrize(
    "shared_code",
    [
        "df = src.with_columns(y=pl.col('x') * 2)",
        "df = src.sort('x')",
        "df = src.join(other, on='x', how='left')",
        "df = helper(src)",
    ],
)
def test_a_limited_preview_recomputes_a_frame_its_row_limit_reaches(shared_code: str) -> None:
    """A row-local frame, a sort (a top-k under the limit), a join (its probe side
    streams) or code the analyser cannot classify is computed once per path, with no
    cache to stop the limit reaching the source."""
    result, reads = _diamond_walk(shared_code, row_limit=10)

    assert len(reads) == 2
    assert "CACHE" not in result.frames["low"].explain(optimized=False)


def test_a_preview_without_a_row_limit_shares_every_frame_its_branches_meet_below() -> None:
    result, reads = _diamond_walk("df = src.with_columns(y=pl.col('x') * 2)", row_limit=None)

    assert reads == [100]
    assert "CACHE" in result.frames["low"].explain(optimized=False)


def _shared_by(graph: PipelineGraph, collect: str, *, row_limit: int | None = 10) -> set[str]:
    """The frames a display walk of *graph* collected at *collect* decides to share."""
    walk = _Walk(
        WalkRequest(
            graph=graph, build_node_fn=_builder(pl.LazyFrame({"x": [1]})), target_node_id=collect
        ),
        CollectPolicy.display(collect=[collect], row_limit=row_limit),
    )
    return {source for source, _port in walk._frames_to_share()}


@pytest.mark.parametrize("reader", [NodeType.MODEL_SCORE, NodeType.OUTPUT, NodeType.RATING_STEP])
def test_a_frame_read_through_a_python_scan_is_not_shared(reader: NodeType) -> None:
    """A Python scan collects its input in a query of its own, so two of them meeting
    below a frame never compute it in one query: a cache would hold a copy for each."""
    graph = PipelineGraph(
        nodes=[
            _node("src", NodeType.DATA_INPUT),
            _node("grouped", NodeType.POLARS, code="df = src.group_by('x').agg(pl.len())"),
            _node("left", reader),
            _node("right", reader),
            _node("both", NodeType.POLARS, code="df = pl.concat([left, right])"),
        ],
        edges=[
            _edge("src", "grouped"),
            _edge("grouped", "left"),
            _edge("grouped", "right"),
            _edge("left", "both"),
            _edge("right", "both"),
        ],
    )

    assert _shared_by(graph, "both") == set()


def _joined_graph(*, grouped_side: str) -> PipelineGraph:
    """An Edge Join of quotes and a grouped lookup, read by two branches that meet."""
    other_side = "join" if grouped_side == "base" else "base"
    return PipelineGraph(
        nodes=[
            _node("quotes", NodeType.DATA_INPUT),
            _node("lookup", NodeType.DATA_INPUT),
            _node("grouped", NodeType.POLARS, code="df = lookup.group_by('k').agg(pl.len())"),
            _node("joined", NodeType.EDGE_JOIN, how="left", on=["k"]),
            _node("a", NodeType.POLARS, code="df = joined.select('k')"),
            _node("b", NodeType.POLARS, code="df = joined.select(k2='k')"),
            _node("both", NodeType.POLARS, code="df = pl.concat([a, b], how='diagonal')"),
        ],
        edges=[
            _edge("lookup", "grouped"),
            _edge("grouped", "joined", target_handle=grouped_side),
            _edge("quotes", "joined", target_handle=other_side),
            _edge("joined", "a"),
            _edge("joined", "b"),
            _edge("a", "both"),
            _edge("b", "both"),
        ],
    )


def test_a_blocker_on_a_joins_lookup_side_does_not_share_the_join() -> None:
    """The limit still reaches the join's probe side, which a cache would read whole."""
    assert _shared_by(_joined_graph(grouped_side="join"), "both") == set()


def test_a_blocker_on_a_joins_probe_side_shares_the_join() -> None:
    assert _shared_by(_joined_graph(grouped_side="base"), "both") == {"joined"}


_COUNTING_PREAMBLE = """
SEEN = []

def score(frame):
    SEEN.append(frame.height)
    return frame

def seen(values):
    SEEN.append(len(values))
    return values
"""


def _real_diamond(scratch: Path, shared_code: str) -> tuple[WalkResult, list[int]]:
    """The executor's own builders run every node's declared code; SEEN counts the rows a
    Python function in *shared* is handed."""
    graph = PipelineGraph(
        nodes=[
            _source(scratch, "src", pl.DataFrame({"x": list(range(1_000))})),
            _node("shared", NodeType.POLARS, code=shared_code),
            _node("low", NodeType.POLARS, code="df = shared.select('x')"),
            _node("high", NodeType.POLARS, code="df = shared.select(x=pl.col('x') + 1_000)"),
            _node("both", NodeType.POLARS, code="df = pl.concat([low, high])"),
        ],
        edges=[
            _edge("src", "shared"),
            _edge("shared", "low"),
            _edge("shared", "high"),
            _edge("low", "both"),
            _edge("high", "both"),
        ],
        preamble=_COUNTING_PREAMBLE,
    )
    namespace = _compile_preamble(_COUNTING_PREAMBLE)
    namespace["SEEN"].clear()
    result = walk_graph(
        graph,
        _build_node_fn,
        policy=CollectPolicy.display(collect=["both"], row_limit=10, record_failures=True),
        target_node_id="both",
        preamble_ns=namespace,
    )
    assert result.errors == {}
    return result, list(namespace["SEEN"])


def test_a_limited_preview_computes_a_python_step_it_shares_once(haute_scratch: Path) -> None:
    result, seen = _real_diamond(haute_scratch, "df = src.map_batches(score)")

    assert sum(seen) == 1_000
    assert result.collected["both"].height == 10


def test_a_limited_preview_lets_its_limit_reach_an_elementwise_step(haute_scratch: Path) -> None:
    result, seen = _real_diamond(
        haute_scratch,
        "df = src.with_columns(y=pl.col('x').map_batches(seen, return_dtype=pl.Int64, "
        "is_elementwise=True))",
    )

    assert sum(seen) < 1_000
    assert "CACHE" not in result.frames["low"].explain(optimized=False)
    assert result.collected["both"]["x"].to_list() == list(range(10))


def test_a_preview_collect_runs_without_polars_subplan_sharing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The walker decides what is shared, so a simple diamond of plain columns reaches
    the collect with no cache Polars would add, and its limit reaches the source."""
    import haute._graph_walker as walker_module

    handed: list[pl.QueryOptFlags | None] = []
    real_collect = walker_module.streaming_collect

    def recording(frame: pl.LazyFrame, **kwargs: Any) -> pl.DataFrame:
        handed.append(kwargs.get("optimizations"))
        return real_collect(frame, **kwargs)

    monkeypatch.setattr(walker_module, "streaming_collect", recording)
    result, reads = _diamond_walk("df = src.with_columns(y=pl.col('x') * 2)", row_limit=10)

    assert handed and all(flags is not None for flags in handed)
    plan = result.frames["both"].head(10).explain(optimizations=handed[-1])
    assert "CACHE" not in plan
