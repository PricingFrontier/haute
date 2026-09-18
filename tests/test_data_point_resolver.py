"""Data-point resolver: kinds, states, ports, and leased reads (CACHE-S02)."""

from __future__ import annotations

import json
import multiprocessing as mp
import time
from pathlib import Path
from typing import Any

import polars as pl
import pytest
from polars.testing import assert_frame_equal

from haute._data_points import (
    CacheRequiredError,
    DataPoint,
    DataPointResolver,
    NodeDataPointInvalidError,
    PointColumnsMissingError,
    consumer_point,
    lease_point_frame,
    resolve_point,
)
from haute._execution_context import ExecutionProfile
from haute._json_flatten import _json_cache_dir
from haute._json_shred import _writer
from haute._json_shred._cache import build_per_port_cache
from haute._node_snapshots import (
    NodeSnapshotColumns,
    NodeSnapshotPublication,
    NodeSnapshotStore,
)
from haute._source_cache import SourceCacheIdentity
from haute._types import GraphEdge, GraphNode, NodeData, NodeType, PipelineGraph

_TIMEOUT = 60.0
ALL = NodeSnapshotColumns.all()


def _node(node_id: str, node_type: NodeType, config: dict[str, Any]) -> GraphNode:
    return GraphNode(id=node_id, data=NodeData(label=node_id, nodeType=node_type, config=config))


def _banding(column: str = "premium") -> dict[str, Any]:
    return {
        "factors": [
            {"banding": "continuous", "column": column, "outputColumn": "band", "rules": []}
        ]
    }


def _corrupt(data_path: Path) -> None:
    """Replace a generation's data with bytes no reader can parse."""
    data_path.write_bytes(b"corrupt")


def _graph(project: Path, *, source_code: str = "", join_code: str | None = None) -> PipelineGraph:
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
                    **({"code": source_code} if source_code else {}),
                },
            ),
            _node(
                "join",
                NodeType.POLARS,
                {
                    "code": join_code
                    or "df = source.with_columns((pl.col('premium') * 2).alias('double'))"
                },
            ),
            _node("banding", NodeType.BANDING, _banding()),
            _node("explore", NodeType.EXPLORE, {}),
            _node("explore_code", NodeType.EXPLORE, {"code": "df = join.head(2)"}),
            _node(
                "rating",
                NodeType.RATING_STEP,
                {
                    "tables": [
                        {
                            "name": "Region",
                            "factors": ["region"],
                            "outputColumn": "region_factor",
                            "entries": [{"region": "north", "value": 1.0}],
                        }
                    ]
                },
            ),
        ],
        edges=[
            GraphEdge(id="e1", source="source", target="join"),
            GraphEdge(id="e2", source="join", target="banding"),
            GraphEdge(id="e3", source="join", target="explore"),
            GraphEdge(id="e4", source="join", target="explore_code"),
            GraphEdge(id="e5", source="join", target="rating"),
        ],
        preamble="import polars as pl",
        source_file=str(project / "main.py"),
    )


@pytest.fixture()
def project(haute_scratch: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.chdir(haute_scratch)
    (haute_scratch / "main.py").write_text("# pipeline\n", encoding="utf-8")
    pl.DataFrame(
        {
            "policy_id": list(range(10)),
            "premium": [float(value) for value in range(10)],
            "region": ["north", "south"] * 5,
        }
    ).write_parquet(haute_scratch / "quotes.parquet")
    return haute_scratch


def _capture(
    store: NodeSnapshotStore,
    graph: PipelineGraph,
    node_id: str,
    columns: NodeSnapshotColumns,
    *,
    source: str = "live",
) -> NodeSnapshotPublication:
    """Materialise one node's output as a run would and publish it."""
    from haute.execution import execute_lazy_graph
    from haute.executor import _build_node_fn, _compile_preamble, _pipeline_dir

    resolver = DataPointResolver(graph, source=source, store=store)
    identity = resolver.node_output_slot(node_id).identity(resolver.node_output_signature(node_id))
    outputs, *_ = execute_lazy_graph(
        resolver.graph,
        _build_node_fn,
        target_node_id=node_id,
        preamble_ns=_compile_preamble(graph.preamble or "", pipeline_dir=_pipeline_dir(graph))
        or None,
        source=source,
        enforce_contracts=True,
    )
    frame = outputs[node_id]
    if columns.names is not None:
        frame = frame.select(sorted(columns.names))
    artifact = store.stage_node_output(identity)
    frame.collect().write_parquet(artifact.data_path)
    return store.publish_node_output(
        identity,
        artifact,
        columns=columns,
        dependencies={},
        explicit=columns.is_all,
        profile=ExecutionProfile.TRAINING_PREP,
    )


def test_explore_with_blank_code_and_banding_resolve_one_point(project: Path) -> None:
    graph = _graph(project)

    banding = consumer_point(graph, "banding")
    explore = consumer_point(graph, "explore")
    explore_code = consumer_point(graph, "explore_code")
    rating = consumer_point(graph, "rating")

    assert banding.point == explore.point == rating.point == DataPoint("join", None)
    assert banding.demand == NodeSnapshotColumns.of(["premium"])
    assert rating.demand == NodeSnapshotColumns.of(["region"])
    assert explore.demand == ALL
    assert explore_code.point == DataPoint("explore_code", None)


def test_a_consumer_without_exactly_one_input_is_invalid(project: Path) -> None:
    graph = _graph(project)
    two_inputs = graph.model_copy(
        update={"edges": [*graph.edges, GraphEdge(id="e6", source="source", target="banding")]}
    )
    no_input = graph.model_copy(
        update={"edges": [edge for edge in graph.edges if edge.target != "banding"]}
    )

    for candidate in (two_inputs, no_input):
        with pytest.raises(NodeDataPointInvalidError) as raised:
            consumer_point(candidate, "banding")
        assert raised.value.error_code == "node_data_point_invalid"


def test_a_captured_narrow_generation_is_current_for_banding_and_partial_for_explore(
    project: Path,
) -> None:
    graph = _graph(project)
    store = NodeSnapshotStore(project)
    with _capture(store, graph, "join", NodeSnapshotColumns.of(["premium", "region"])):
        pass
    banding = consumer_point(graph, "banding")
    explore = consumer_point(graph, "explore")

    banding_state = resolve_point(
        graph, banding.point, source="live", columns=banding.demand, store=store
    )
    explore_state = resolve_point(
        graph, explore.point, source="live", columns=explore.demand, store=store
    )

    assert (banding_state.kind, banding_state.state) == ("node_output", "current")
    assert (explore_state.kind, explore_state.state) == ("node_output", "partial")
    with lease_point_frame(graph, banding.point, "live", banding.demand, store=store) as leased:
        frame = leased.scan.collect()
    assert frame.columns == ["premium"]
    assert frame["premium"].to_list() == [float(v) for v in range(10)]
    with pytest.raises(CacheRequiredError) as raised:
        with lease_point_frame(graph, explore.point, "live", ALL, store=store):
            pass
    assert raised.value.state == "partial"


def test_a_leased_scan_rejects_absent_columns_and_keeps_rows_for_an_empty_demand(
    project: Path,
) -> None:
    graph = _graph(project)
    store = NodeSnapshotStore(project)
    with _capture(store, graph, "join", ALL):
        pass
    point = DataPoint("join", None)

    with lease_point_frame(
        graph, point, "live", NodeSnapshotColumns.of(["region", "premium"]), store=store
    ) as leased:
        subset = leased.scan.collect()
    with lease_point_frame(graph, point, "live", NodeSnapshotColumns.of([]), store=store) as empty:
        carrier = empty.scan.collect()

    assert subset.columns == ["premium", "region"]
    assert subset.height == 10
    assert carrier.height == 10
    assert carrier.width == 1
    with pytest.raises(PointColumnsMissingError) as raised:
        with lease_point_frame(
            graph, point, "live", NodeSnapshotColumns.of(["premium", "absent"]), store=store
        ):
            pass
    assert raised.value.missing == ["absent"]
    with pytest.raises(PointColumnsMissingError):
        with lease_point_frame(
            graph, DataPoint("source", None), "live", NodeSnapshotColumns.of(["absent"])
        ):
            pass


def test_instance_nodes_resolve_through_their_originals(project: Path) -> None:
    graph = _graph(project)
    instances = [
        _node("banding_copy", NodeType.BANDING, {"instanceOf": "banding"}),
        _node("explore_copy", NodeType.EXPLORE, {"instanceOf": "explore_code"}),
        _node("source_copy", NodeType.DATA_INPUT, {"instanceOf": "source"}),
    ]
    graph = graph.model_copy(
        update={
            "nodes": [*graph.nodes, *instances],
            "edges": [
                *graph.edges,
                GraphEdge(id="i1", source="join", target="banding_copy"),
                GraphEdge(id="i2", source="join", target="explore_copy"),
            ],
        }
    )

    banding_copy = consumer_point(graph, "banding_copy")
    explore_copy = consumer_point(graph, "explore_copy")

    assert banding_copy.point == DataPoint("join", None)
    assert banding_copy.demand == NodeSnapshotColumns.of(["premium"])
    assert explore_copy.point == DataPoint("explore_copy", None)
    source_copy = resolve_point(graph, DataPoint("source_copy", None), source="live", columns=ALL)
    assert (source_copy.kind, source_copy.state) == ("data_input", "current")
    with lease_point_frame(graph, DataPoint("source_copy", None), "live", ALL) as leased:
        assert leased.scan.collect().height == 10


def test_node_output_states_missing_stale_building_and_corrupt(project: Path) -> None:
    graph = _graph(project)
    store = NodeSnapshotStore(project)
    point = DataPoint("join", None)

    assert resolve_point(graph, point, source="live", columns=ALL, store=store).state == "missing"
    building = resolve_point(
        graph,
        point,
        source="live",
        columns=ALL,
        store=store,
        building=lambda kind, key: kind == "node_output",
    )
    assert building.state == "building"

    with _capture(store, graph, "join", ALL) as publication:
        generation = publication.generation
    edited = _graph(project, join_code="df = source.head(3)")
    assert resolve_point(edited, point, source="live", columns=ALL, store=store).state == "stale"

    assert generation is not None
    _corrupt(generation.generation.data_path)
    store._verified_generations.clear()
    corrupt = resolve_point(graph, point, source="live", columns=ALL, store=store)
    assert corrupt.state == "corrupt"
    for state_graph, expected in ((edited, "stale"), (graph, "corrupt")):
        with pytest.raises(CacheRequiredError) as raised:
            with lease_point_frame(state_graph, point, "live", ALL, store=store):
                pass
        assert raised.value.state == expected
        assert raised.value.error_code == "cache_required"


def test_a_direct_parquet_data_input_is_current_and_versioned_by_its_file(project: Path) -> None:
    graph = _graph(project)
    point = DataPoint("source", None)

    first = resolve_point(graph, point, source="live", columns=ALL)
    time.sleep(0.01)
    pl.DataFrame({"policy_id": [1], "premium": [1.0], "region": ["east"]}).write_parquet(
        project / "quotes.parquet"
    )
    second = resolve_point(graph, point, source="live", columns=ALL)

    assert (first.kind, first.state) == ("data_input", "current")
    assert second.state == "current"
    assert first.data_version != second.data_version
    with lease_point_frame(graph, point, "live", ALL) as leased:
        assert leased.scan.collect()["region"].to_list() == ["east"]


def _run_frame(graph: PipelineGraph, node_id: str) -> pl.DataFrame:
    from haute.execution import canonical_dataframe_execution_graph, execute_lazy_graph
    from haute.executor import _build_node_fn

    outputs, *_ = execute_lazy_graph(
        canonical_dataframe_execution_graph(graph),
        _build_node_fn,
        target_node_id=node_id,
        source="live",
        enforce_contracts=True,
    )
    return outputs[node_id].collect()


def test_a_data_input_point_yields_exactly_the_frame_a_run_produces(project: Path) -> None:
    graph = _graph(project)
    source = graph.node_map["source"]
    configured = source.with_config(
        {
            **source.data.config,
            "selected_columns": ["policy_id", "premium"],
            "column_renames": {"premium": "gross_premium"},
        }
    )
    graph = graph.model_copy(
        update={"nodes": [configured if n.id == "source" else n for n in graph.nodes]}
    )
    point = DataPoint("source", None)

    with lease_point_frame(graph, point, "live", ALL) as leased:
        frame = leased.scan.collect()

    assert frame.columns == ["policy_id", "gross_premium"]
    assert_frame_equal(frame, _run_frame(graph, "source"))
    assert resolve_point(graph, point, source="live", columns=ALL).data_version != (
        resolve_point(_graph(project), point, source="live", columns=ALL).data_version
    )


def test_a_data_input_with_post_load_code_is_one_shared_node_output(project: Path) -> None:
    code = (
        "df = df.filter(pl.col('premium') >= 2)"
        ".with_columns((pl.col('premium') + 1).alias('uplift'))"
        ".collect().sample(fraction=0.5).lazy()"
    )
    graph = _graph(project, source_code=code)
    store = NodeSnapshotStore(project)
    point = DataPoint("source", None)

    assert resolve_point(graph, point, source="live", columns=ALL, store=store).kind == (
        "node_output"
    )
    with _capture(store, graph, "source", ALL):
        pass
    with lease_point_frame(graph, point, "live", ALL, store=store) as first:
        first_rows = first.scan.collect()
    with lease_point_frame(graph, point, "live", ALL, store=store) as second:
        second_rows = second.scan.collect()

    assert_frame_equal(first_rows, second_rows)
    assert set(first_rows.columns) >= {"premium", "uplift"}
    edited = _graph(project, source_code=code.replace(">= 2", ">= 3"))
    assert resolve_point(edited, point, source="live", columns=ALL, store=store).state == "stale"


def test_a_missing_snapshot_backed_data_input_starts_no_build(project: Path) -> None:
    csv_path = project / "quotes.csv"
    pl.DataFrame({"premium": [1.0, 2.0]}).write_csv(csv_path)
    config = {"inputType": "file", "format": "csv", "mode": "scan", "path": str(csv_path)}
    graph = _graph(project)
    graph = graph.model_copy(
        update={"nodes": [n.with_config(config) if n.id == "source" else n for n in graph.nodes]}
    )
    point = DataPoint("source", None)

    resolution = resolve_point(graph, point, source="live", columns=ALL)
    assert (resolution.kind, resolution.state) == ("data_input", "missing")
    with pytest.raises(CacheRequiredError) as raised:
        with lease_point_frame(graph, point, "live", ALL):
            pass

    assert raised.value.state == "missing"
    assert list(project.glob(".haute_cache/inputs/*/generations/*")) == []


def test_a_built_snapshot_backed_data_input_is_current_then_stale(project: Path) -> None:
    from haute._input_providers import build_input_snapshot

    csv_path = project / "quotes.csv"
    pl.DataFrame({"premium": [1.0, 2.0]}).write_csv(csv_path)
    config = {"inputType": "file", "format": "csv", "mode": "scan", "path": str(csv_path)}
    graph = _graph(project)
    graph = graph.model_copy(
        update={"nodes": [n.with_config(config) if n.id == "source" else n for n in graph.nodes]}
    )
    store = NodeSnapshotStore(project)
    build_input_snapshot(config, store=store, base_dir=project)
    point = DataPoint("source", None)

    current = resolve_point(graph, point, source="live", columns=ALL, store=store)
    assert current.state == "current"
    with lease_point_frame(graph, point, "live", ALL, store=store) as leased:
        assert leased.scan.collect()["premium"].to_list() == [1.0, 2.0]
        assert leased.data_version == current.data_version

    time.sleep(0.01)
    pl.DataFrame({"premium": [5.0]}).write_csv(csv_path)
    assert resolve_point(graph, point, source="live", columns=ALL, store=store).state == "stale"


def _api_config(data_path: Path) -> dict[str, Any]:
    return {
        "path": str(data_path),
        "contract": "opaque",
        "tables": [
            {
                "path": "$[:]",
                "label": "policies",
                "emit": True,
                "columns": [
                    {"name": "policy_id", "path": "$[:].policy_id", "type": "int", "selected": True}
                ],
            },
            {
                "path": "$[:].drivers[:]",
                "label": "drivers",
                "emit": True,
                "columns": [
                    {
                        "name": "driver_id",
                        "path": "$[:].drivers[:].driver_id",
                        "type": "int",
                        "selected": True,
                    }
                ],
            },
        ],
    }


def _api_graph(project: Path, config: dict[str, Any]) -> PipelineGraph:
    return PipelineGraph(
        nodes=[
            _node("api", NodeType.API_INPUT, config),
            _node("band_policies", NodeType.BANDING, _banding("policy_id")),
            _node("band_drivers", NodeType.BANDING, _banding("driver_id")),
        ],
        edges=[
            GraphEdge(id="p", source="api", target="band_policies", sourceHandle="policies"),
            GraphEdge(id="d", source="api", target="band_drivers", sourceHandle="drivers"),
        ],
        source_file=str(project / "main.py"),
    )


def _write_records(path: Path) -> None:
    path.write_text(
        json.dumps(
            [
                {"policy_id": 1, "drivers": [{"driver_id": 10}, {"driver_id": 11}]},
                {"policy_id": 2, "drivers": [{"driver_id": 12}]},
            ]
        ),
        encoding="utf-8",
    )


def test_two_api_input_ports_resolve_distinct_points_and_scan_only_their_tables(
    project: Path,
) -> None:
    data_path = project / "records.json"
    _write_records(data_path)
    config = _api_config(data_path)
    build_per_port_cache(data_path, config, _json_cache_dir(data_path, "working"))
    graph = _api_graph(project, config)

    policies = consumer_point(graph, "band_policies")
    drivers = consumer_point(graph, "band_drivers")

    assert policies.point == DataPoint("api", "policies")
    assert drivers.point == DataPoint("api", "drivers")
    for consumer, expected in ((policies, {"policy_id": [1, 2]}), (drivers, None)):
        resolution = resolve_point(graph, consumer.point, source="live", columns=ALL)
        assert (resolution.kind, resolution.state) == ("api_input_table", "current")
        with lease_point_frame(graph, consumer.point, "live", ALL) as leased:
            frame = leased.scan.collect()
        if expected is None:
            assert frame.columns == ["driver_id"]
            assert frame["driver_id"].to_list() == [10, 11, 12]
        else:
            assert frame.to_dict(as_series=False) == expected


def test_an_uncached_api_input_table_is_cache_required_without_shredding(
    project: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    data_path = project / "records.json"
    _write_records(data_path)
    config = _api_config(data_path)
    graph = _api_graph(project, config)
    point = DataPoint("api", "policies")

    def forbidden_shred(*_args: object, **_kwargs: object) -> object:
        raise AssertionError("a cache-required read must never shred the raw source")

    monkeypatch.setattr(_writer, "_shred_data_file_to_direct_spill", forbidden_shred)

    assert resolve_point(graph, point, source="live", columns=ALL).state == "missing"
    with pytest.raises(CacheRequiredError) as raised:
        with lease_point_frame(graph, point, "live", ALL):
            pass
    assert raised.value.state == "missing"

    # A source-only read under the cache-only mode raises too, even if resolution raced.
    resolver = DataPointResolver(graph, source="live")
    from haute._json_shred._cache import ApiInputCacheRequiredError

    with pytest.raises(ApiInputCacheRequiredError):
        resolver._source_node_frame(point, execution_context=None)


def _paused_child_reader(
    root: str,
    identity_payload: dict[str, Any],
    generation_id: str,
    leased: Any,
    release: Any,
    results: Any,
) -> None:
    store = NodeSnapshotStore(root)
    identity = SourceCacheIdentity(
        provider=identity_payload["provider"],
        descriptor=identity_payload["descriptor"],
        schema_version=identity_payload["schema_version"],
    )
    with store.lease_generation(identity, generation_id) as generation:
        leased.put(generation.generation_id)
        if not release.wait(_TIMEOUT):
            raise TimeoutError("test did not release the child reader")
        results.put(generation.lazy_frame.collect()["premium"].to_list())


def test_a_child_keeps_reading_a_parent_leased_generation_through_refresh_and_clear(
    project: Path,
) -> None:
    graph = _graph(project)
    store = NodeSnapshotStore(project)
    point = DataPoint("join", None)
    with _capture(store, graph, "join", ALL):
        pass
    resolver = DataPointResolver(graph, source="live", store=store)
    ctx = mp.get_context("spawn")
    leased_queue = ctx.Queue()
    results = ctx.Queue()
    release = ctx.Event()

    with resolver.lease_frame(point, ALL) as parent:
        resolution = parent.resolution
        identity = resolution.node_output_identity
        generation = resolution.node_output_generation
        assert identity is not None and generation is not None
        generation_dir = generation.generation.data_path.parent
        child = ctx.Process(
            target=_paused_child_reader,
            args=(
                str(project),
                identity.payload,
                generation.generation_id,
                leased_queue,
                release,
                results,
            ),
        )
        child.start()
        try:
            assert leased_queue.get(timeout=_TIMEOUT) == generation.generation_id
            with _capture(store, graph, "join", ALL) as refreshed:
                assert refreshed.outcome == "superseded"
            refresh_artifact = store.stage_node_output(identity)
            pl.DataFrame({"premium": [99.0]}).write_parquet(refresh_artifact.data_path)
            with store.publish_node_output(
                identity,
                refresh_artifact,
                columns=ALL,
                dependencies={},
                explicit=True,
                profile=ExecutionProfile.NODE_SNAPSHOT,
                refresh=True,
            ) as publication:
                assert publication.outcome == "published"
            store.clear_slot(resolver.node_output_slot("join"))
            assert generation_dir.is_dir()
        except BaseException:
            child.terminate()
            raise
    assert generation_dir.is_dir(), "the child still leases the generation"

    release.set()
    assert results.get(timeout=_TIMEOUT) == [float(value) for value in range(10)]
    child.join(timeout=_TIMEOUT)
    assert child.exitcode == 0
    assert not generation_dir.exists()


def test_a_rebuild_between_resolution_and_load_is_versioned_as_the_data_read(
    project: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    data_path = project / "records.json"
    _write_records(data_path)
    config = _api_config(data_path)
    working = _json_cache_dir(data_path, "working")
    build_per_port_cache(data_path, config, working)
    graph = _api_graph(project, config)
    point = DataPoint("api", "policies")
    resolver = DataPointResolver(graph, source="live")
    resolved_before = resolver.resolve(point, ALL)
    original_frame = DataPointResolver._source_node_frame

    def rebuild_then_load(self, *args, **kwargs):
        data_path.write_text(json.dumps([{"policy_id": 7, "drivers": []}]), encoding="utf-8")
        build_per_port_cache(data_path, config, working)
        return original_frame(self, *args, **kwargs)

    with monkeypatch.context() as patched:
        patched.setattr(DataPointResolver, "_source_node_frame", rebuild_then_load)
        with resolver.lease_frame(point, ALL) as leased:
            rows = leased.scan.collect()["policy_id"].to_list()
            leased_version = leased.data_version

    fresh = DataPointResolver(graph, source="live").resolve(point, ALL)
    assert rows == [7]
    assert leased_version != resolved_before.data_version
    assert leased_version == fresh.data_version


def test_api_input_status_never_waits_behind_a_running_cache_build(project: Path) -> None:
    import threading

    from haute._json_shred._publication import _build_lock_for

    data_path = project / "records.json"
    _write_records(data_path)
    config = _api_config(data_path)
    working = _json_cache_dir(data_path, "working")
    committed = _json_cache_dir(data_path, "committed")
    build_per_port_cache(data_path, config, working)
    graph = _api_graph(project, config)
    held = threading.Event()
    release = threading.Event()

    def hold_build_locks() -> None:
        with _build_lock_for(working), _build_lock_for(committed):
            held.set()
            release.wait(_TIMEOUT)

    holder = threading.Thread(target=hold_build_locks)
    holder.start()
    try:
        assert held.wait(_TIMEOUT)
        started = time.monotonic()
        resolution = resolve_point(graph, DataPoint("api", "policies"), source="live", columns=ALL)
        elapsed = time.monotonic() - started
    finally:
        release.set()
        holder.join(_TIMEOUT)

    assert resolution.state == "building"
    assert elapsed < 5.0
    assert (
        resolve_point(graph, DataPoint("api", "policies"), source="live", columns=ALL).state
        == "current"
    )


def test_api_input_status_is_prompt_when_one_locked_layer_needs_a_fresh_source_proof(
    project: Path,
) -> None:
    import threading

    from haute._json_shred import _source_proof
    from haute._json_shred._publication import _build_lock_for

    data_path = project / "records.json"
    _write_records(data_path)
    config = _api_config(data_path)
    working = _json_cache_dir(data_path, "working")
    build_per_port_cache(data_path, config, _json_cache_dir(data_path, "committed"))
    # Same content, new revision: validity must prove the source afresh.
    time.sleep(0.01)
    data_path.write_bytes(data_path.read_bytes())
    _source_proof._clear_data_file_signature_memo()
    held = threading.Event()
    release = threading.Event()

    def hold_working() -> None:
        with _build_lock_for(working):
            held.set()
            release.wait(_TIMEOUT)

    holder = threading.Thread(target=hold_working)
    holder.start()
    try:
        assert held.wait(_TIMEOUT)
        started = time.monotonic()
        resolution = resolve_point(
            graph=_api_graph(project, config),
            point=DataPoint("api", "policies"),
            source="live",
            columns=ALL,
        )
        elapsed = time.monotonic() - started
    finally:
        release.set()
        holder.join(_TIMEOUT)

    assert elapsed < 5.0
    assert resolution.state == "current"
