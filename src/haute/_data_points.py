"""Data points: resolve a consumer node and column demand to leased, current data.

A data point is the data at one pipeline location — ``(producer node, port)`` —
independent of which consumer asks for it. Each point has one kind:

- ``data_input``: a Data Input with blank post-load code. Its frame is the lazy
  execution of that single source node over direct Parquet or its published
  input snapshot, so selections and renames apply exactly as in a run.
- ``api_input_table``: one port of an ``apiInput``, served from the JSON table
  cache without ever shredding the raw source.
- ``node_output``: any other producer, including a Data Input with post-load
  code, read from its node-output snapshot.

Consumers never read a point that is not ``current`` for their column demand:
:func:`lease_point_frame` raises :class:`CacheRequiredError` carrying the state.
"""

from __future__ import annotations

import contextlib
import hashlib
from collections.abc import Callable, Iterator, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

import polars as pl

from haute._cache import GraphFingerprintMemo, canonical_json, graph_fingerprint
from haute._execution_context import ExecutionContext
from haute._json_shred._cache import ApiInputCacheRequiredError, api_input_cache_only
from haute._node_snapshots import (
    BOUNDED_SEMANTICS_CLASS,
    NodeSnapshotColumns,
    NodeSnapshotGeneration,
    NodeSnapshotSlot,
    NodeSnapshotStore,
    node_snapshot_signature,
    pipeline_source_file_key,
)
from haute._source_cache import SourceCacheGenerationMissingError, SourceCacheIdentity
from haute._types import GraphEdge, GraphNode, NodeType, PipelineGraph

PointKind = Literal["data_input", "api_input_table", "node_output"]
PointState = Literal["current", "stale", "partial", "missing", "building", "corrupt"]
BuildingProbe = Callable[[PointKind, str], bool]
"""Report whether a build is running for a point, given its kind and build key.

The build key is the input-snapshot identity digest for ``data_input``, the
working JSON cache directory for ``api_input_table``, and the node-output
identity digest for ``node_output``.
"""


# A JSON cache reader holds its layer lock only briefly; one still held after this
# wait belongs to a build in progress, which a status probe must not wait behind.
_API_CACHE_LOCK_WAIT_SECONDS = 0.5


class NodeDataPointInvalidError(ValueError):
    """A consumer node's wiring does not name exactly one data point."""

    error_code = "node_data_point_invalid"


class CacheRequiredError(RuntimeError):
    """A data point is not current for the requested column demand."""

    error_code = "cache_required"

    def __init__(self, resolution: PointResolution) -> None:
        super().__init__(
            f"Data for node {resolution.point.producer_node_id!r} is {resolution.state}; "
            "cache it before reading the whole dataset."
        )
        self.resolution = resolution

    @property
    def state(self) -> PointState:
        return self.resolution.state


@dataclass(frozen=True, slots=True)
class DataPoint:
    producer_node_id: str
    port_label: str | None = None


@dataclass(frozen=True, slots=True)
class ConsumerPoint:
    consumer_node_id: str
    point: DataPoint
    demand: NodeSnapshotColumns


@dataclass(frozen=True, slots=True)
class PointResolution:
    """The resolved kind, state, and version of one point for one column demand."""

    point: DataPoint
    kind: PointKind
    state: PointState
    demand: NodeSnapshotColumns
    data_version: str | None
    build_key: str | None
    node_output_identity: SourceCacheIdentity | None = None
    node_output_generation: NodeSnapshotGeneration | None = None
    input_identity: SourceCacheIdentity | None = None
    input_generation_id: str | None = None


@dataclass(frozen=True, slots=True)
class LeasedPointFrame:
    kind: PointKind
    data_version: str
    columns: NodeSnapshotColumns
    scan: pl.LazyFrame
    resolution: PointResolution


def _incoming_edges(graph: PipelineGraph, node_id: str) -> list[GraphEdge]:
    return [edge for edge in graph.edges if edge.target == node_id]


def _node(graph: PipelineGraph, node_id: str) -> GraphNode:
    node = graph.node_map.get(node_id)
    if node is None:
        raise NodeDataPointInvalidError(f"Node {node_id!r} is not in the pipeline.")
    return node


def _single_input_point(graph: PipelineGraph, node: GraphNode) -> DataPoint:
    edges = _incoming_edges(graph, node.id)
    if len(edges) != 1:
        raise NodeDataPointInvalidError(
            f"Node {node.data.label!r} must have exactly one incoming connection to read its "
            f"data (found {len(edges)})."
        )
    edge = edges[0]
    producer = _node(graph, edge.source)
    if producer.data.nodeType == NodeType.API_INPUT and edge.sourceHandle is not None:
        return DataPoint(edge.source, edge.sourceHandle)
    return DataPoint(edge.source, None)


def _banding_demand(node: GraphNode) -> NodeSnapshotColumns:
    from haute._banding_config import normalise_banding_factors

    columns = {
        str(factor["column"])
        for factor in normalise_banding_factors(dict(node.data.config))
        if isinstance(factor.get("column"), str) and factor["column"]
    }
    return NodeSnapshotColumns.of(columns)


def _rating_step_demand(node: GraphNode) -> NodeSnapshotColumns:
    from haute._rating_step_config import normalise_rating_tables

    columns = {
        str(factor)
        for table in normalise_rating_tables(dict(node.data.config))
        for factor in table.get("factors") or []
        if isinstance(factor, str) and factor
    }
    return NodeSnapshotColumns.of(columns)


class PointColumnsMissingError(ValueError):
    """A column demand names columns the data point does not have."""

    error_code = "node_data_columns_missing"

    def __init__(self, missing: list[str]) -> None:
        super().__init__(f"The data has no column(s) {missing}.")
        self.missing = missing


def _effective_graph(graph: PipelineGraph) -> PipelineGraph:
    from haute._builders import resolve_instance_nodes

    return resolve_instance_nodes(graph)


def consumer_point(graph: PipelineGraph, consumer_node_id: str) -> ConsumerPoint:
    """Map a consumer node to the data point it reads and its column demand."""
    graph = _effective_graph(graph)
    node = _node(graph, consumer_node_id)
    node_type = node.data.nodeType
    if node_type == NodeType.BANDING:
        return ConsumerPoint(node.id, _single_input_point(graph, node), _banding_demand(node))
    if node_type == NodeType.RATING_STEP:
        return ConsumerPoint(node.id, _single_input_point(graph, node), _rating_step_demand(node))
    if node_type == NodeType.EXPLORE and not str(node.data.config.get("code") or "").strip():
        # A blank-code Explore node returns its input unchanged.
        return ConsumerPoint(node.id, _single_input_point(graph, node), NodeSnapshotColumns.all())
    return ConsumerPoint(node.id, DataPoint(node.id, None), NodeSnapshotColumns.all())


def point_kind(graph: PipelineGraph, point: DataPoint) -> PointKind:
    node = _node(_effective_graph(graph), point.producer_node_id)
    node_type = node.data.nodeType
    if node_type == NodeType.API_INPUT and point.port_label is not None:
        return "api_input_table"
    if point.port_label is not None:
        raise NodeDataPointInvalidError(
            f"Node {node.data.label!r} has no output port {point.port_label!r}."
        )
    if node_type == NodeType.DATA_INPUT and not str(node.data.config.get("code") or "").strip():
        return "data_input"
    return "node_output"


def _version(kind: PointKind, payload: object) -> str:
    return f"{kind}:{hashlib.sha256(canonical_json(payload).encode('utf-8')).hexdigest()}"


def _lineage_fingerprint(graph: PipelineGraph, node_id: str, memo: GraphFingerprintMemo) -> str:
    from haute._dataframe_execution_cache import _upstream_subgraph

    return graph_fingerprint(_upstream_subgraph(graph, node_id), memo=memo)


def _pipeline_base_dir(graph: PipelineGraph) -> Path | None:
    from haute._builders import _configured_pipeline_dir
    from haute.execution import _cache_pipeline_dir

    return _cache_pipeline_dir(graph) or _configured_pipeline_dir()


def _default_store() -> NodeSnapshotStore:
    from haute._sandbox import _get_project_root

    return NodeSnapshotStore(_get_project_root())


class DataPointResolver:
    """Resolve points of one graph under one source against one snapshot store."""

    def __init__(
        self,
        graph: PipelineGraph,
        *,
        source: str,
        store: NodeSnapshotStore | None = None,
        building: BuildingProbe | None = None,
    ) -> None:
        from haute.execution import canonical_dataframe_execution_graph

        self.graph = canonical_dataframe_execution_graph(_effective_graph(graph))
        self.source = source
        self.store = store if store is not None else _default_store()
        self._building = building
        self._memo = GraphFingerprintMemo()

    def _is_building(self, kind: PointKind, key: str) -> bool:
        return self._building is not None and self._building(kind, key)

    def resolve(self, point: DataPoint, demand: NodeSnapshotColumns) -> PointResolution:
        kind = point_kind(self.graph, point)
        if kind == "data_input":
            return self._resolve_data_input(point, demand)
        if kind == "api_input_table":
            return self._resolve_api_input_table(point, demand)
        return self._resolve_node_output(point, demand)

    def node_output_slot(self, node_id: str) -> NodeSnapshotSlot:
        return NodeSnapshotSlot(
            pipeline_source_file=pipeline_source_file_key(self.graph),
            node_id=node_id,
            source=self.source,
            semantics_class=BOUNDED_SEMANTICS_CLASS,
        )

    def node_output_signature(self, node_id: str) -> str:
        return node_snapshot_signature(
            self.graph,
            node_id,
            source=self.source,
            semantics_class=BOUNDED_SEMANTICS_CLASS,
            enforce_contracts=True,
            memo=self._memo,
        )

    # ------------------------------------------------------------- kinds

    def _resolve_data_input(self, point: DataPoint, demand: NodeSnapshotColumns) -> PointResolution:
        from haute._input_providers import source_cache_identity, source_signature
        from haute._polars_io_registry import anchor_config_source_path, data_input_is_direct

        node = _node(self.graph, point.producer_node_id)
        config = dict(node.data.config)
        base_dir = _pipeline_base_dir(self.graph)
        lineage = _lineage_fingerprint(self.graph, node.id, self._memo)
        if data_input_is_direct(config):
            anchored = anchor_config_source_path(config, Path(base_dir or Path.cwd()))
            path = Path(str(anchored["path"])).resolve()
            try:
                stat = path.stat()
                file_version: object = {"size": stat.st_size, "mtime_ns": stat.st_mtime_ns}
            except FileNotFoundError:
                file_version = "missing"
            return PointResolution(
                point=point,
                kind="data_input",
                state="current",
                demand=demand,
                data_version=_version(
                    "data_input",
                    {"path": str(path), "file": file_version, "lineage": lineage},
                ),
                build_key=None,
            )
        identity = source_cache_identity(config, base_dir=base_dir)
        status = self.store.status(
            identity, source_signature=source_signature(config, base_dir=base_dir)
        )
        state: PointState
        if status.state == "ready":
            state = "stale" if status.freshness == "stale" else "current"
        elif status.state == "corrupt":
            state = "corrupt"
        else:
            state = "missing"
        if state != "current" and self._is_building("data_input", identity.digest):
            state = "building"
        generation_id = status.generation.generation_id if status.generation else None
        return PointResolution(
            point=point,
            kind="data_input",
            state=state,
            demand=demand,
            data_version=(
                _version("data_input", {"generation": generation_id, "lineage": lineage})
                if generation_id is not None
                else None
            ),
            build_key=identity.digest,
            input_identity=identity,
            input_generation_id=generation_id,
        )

    def _api_input_paths(self, node: GraphNode) -> tuple[str, Path, Path]:
        from haute._json_flatten import _json_cache_dir
        from haute._node_apply import _anchored_required_path

        data_path = _anchored_required_path(node.data.config, _pipeline_base_dir(self.graph))
        return (
            data_path,
            _json_cache_dir(data_path, "working"),
            _json_cache_dir(data_path, "committed"),
        )

    def _resolve_api_input_table(
        self, point: DataPoint, demand: NodeSnapshotColumns
    ) -> PointResolution:
        from haute._json_shred._cache import is_per_port_cache_valid, read_per_port_cache_meta
        from haute._json_shred._publication import _build_lock_for

        node = _node(self.graph, point.producer_node_id)
        config = dict(node.data.config)
        tables = config.get("tables")
        labels = (
            {table.get("label") for table in tables if isinstance(table, dict)}
            if isinstance(tables, list)
            else set()
        )
        if point.port_label not in labels:
            raise NodeDataPointInvalidError(
                f"API Input {node.data.label!r} has no table {point.port_label!r}."
            )
        data_path, working, committed = self._api_input_paths(node)
        building = self._is_building("api_input_table", str(working))
        layer_busy = False
        has_metadata = False
        for layer_dir in (working, committed):
            lock = _build_lock_for(layer_dir)
            # Never wait behind a multi-minute cache build: readers hold the
            # lock only briefly, so a lock still held after a short wait is a
            # build in progress for that layer.
            if not lock.acquire(timeout=_API_CACHE_LOCK_WAIT_SECONDS):
                layer_busy = True
                continue
            try:
                meta = read_per_port_cache_meta(layer_dir)
                if meta is None:
                    continue
                has_metadata = True
                if not is_per_port_cache_valid(layer_dir, config, data_path=data_path):
                    continue
                return PointResolution(
                    point=point,
                    kind="api_input_table",
                    state="current",
                    demand=demand,
                    data_version=self._api_input_version(node, point, meta),
                    build_key=str(working),
                )
            finally:
                lock.release()
        state: PointState = "stale" if has_metadata else "missing"
        if building or layer_busy:
            state = "building"
        return PointResolution(
            point=point,
            kind="api_input_table",
            state=state,
            demand=demand,
            data_version=None,
            build_key=str(working),
        )

    def _api_input_version(self, node: GraphNode, point: DataPoint, meta: Mapping[str, Any]) -> str:
        return _version(
            "api_input_table",
            {
                "metadata": hashlib.sha256(canonical_json(meta).encode("utf-8")).hexdigest(),
                "port": point.port_label,
                "lineage": _lineage_fingerprint(self.graph, node.id, self._memo),
            },
        )

    def _resolve_node_output(
        self, point: DataPoint, demand: NodeSnapshotColumns
    ) -> PointResolution:
        slot = self.node_output_slot(point.producer_node_id)
        signature = self.node_output_signature(point.producer_node_id)
        status = self.store.slot_status(slot, signature)
        generation = status.generation
        state: PointState = status.state
        if state == "current" and generation is not None and not generation.columns.covers(demand):
            state = "partial"
        if state != "current" and self._is_building("node_output", status.identity.digest):
            state = "building"
        return PointResolution(
            point=point,
            kind="node_output",
            state=state,
            demand=demand,
            data_version=generation.generation_id if generation is not None else None,
            build_key=status.identity.digest,
            node_output_identity=status.identity,
            node_output_generation=generation,
        )

    # ------------------------------------------------------------ leasing

    @contextlib.contextmanager
    def lease_frame(
        self,
        point: DataPoint,
        demand: NodeSnapshotColumns,
        *,
        execution_context: ExecutionContext | None = None,
    ) -> Iterator[LeasedPointFrame]:
        """Lease the point for the caller's whole operation, including final collection.

        The yielded data version always names the data the scan reads.
        """
        resolution = self.resolve(point, demand)
        if resolution.state != "current":
            raise CacheRequiredError(resolution)
        assert resolution.data_version is not None
        if resolution.kind == "node_output":
            identity = resolution.node_output_identity
            generation = resolution.node_output_generation
            assert identity is not None and generation is not None
            try:
                lease = self.store.lease_generation(identity, generation.generation_id)
                leased = lease.__enter__()
            except SourceCacheGenerationMissingError:
                # Retired between resolution and lease: report the point as it is now.
                raise CacheRequiredError(self.resolve(point, demand)) from None
            try:
                yield LeasedPointFrame(
                    kind="node_output",
                    data_version=resolution.data_version,
                    columns=demand,
                    scan=_project(leased.lazy_frame, demand),
                    resolution=resolution,
                )
            finally:
                lease.__exit__(None, None, None)
            return
        with contextlib.ExitStack() as stack:
            data_version = resolution.data_version
            if resolution.kind == "data_input" and resolution.input_identity is not None:
                assert resolution.input_generation_id is not None
                stack.enter_context(
                    self.store.lease_generation(
                        resolution.input_identity, resolution.input_generation_id
                    )
                )
            try:
                frame, served_meta = self._source_node_frame(
                    point, execution_context=execution_context
                )
            except ApiInputCacheRequiredError:
                # The cache stopped serving the schema after resolution.
                raise CacheRequiredError(self.resolve(point, demand)) from None
            if resolution.kind == "api_input_table":
                # Version exactly the cache generation the load pinned, which a
                # rebuild after resolution may have replaced.
                assert served_meta is not None
                data_version = self._api_input_version(
                    _node(self.graph, point.producer_node_id), point, served_meta
                )
            elif resolution.input_identity is not None:
                current = self.store.status(resolution.input_identity).generation
                if current is None or current.generation_id != resolution.input_generation_id:
                    # The execution leased a newer generation than the one resolved.
                    raise CacheRequiredError(self.resolve(point, demand))
            yield LeasedPointFrame(
                kind=resolution.kind,
                data_version=data_version,
                columns=demand,
                scan=_project(frame, demand),
                resolution=resolution,
            )

    def _source_node_frame(
        self,
        point: DataPoint,
        *,
        execution_context: ExecutionContext | None,
    ) -> tuple[pl.LazyFrame, Mapping[str, Any] | None]:
        """Lazily execute the single source node, never building or shredding its source.

        Returns the frame and, for an API-input table, the metadata of the cache
        layer that served it.
        """
        from haute.execution import execute_lazy_graph
        from haute.executor import _build_node_fn, _compile_preamble, _pipeline_dir

        node = _node(self.graph, point.producer_node_id)
        subgraph = self.graph.model_copy(update={"nodes": [node], "edges": []})
        preamble_ns = _compile_preamble(
            subgraph.preamble or "", pipeline_dir=_pipeline_dir(subgraph)
        )
        with api_input_cache_only() as reads:
            outputs, *_ = execute_lazy_graph(
                subgraph,
                _build_node_fn,
                target_node_id=node.id,
                preamble_ns=preamble_ns or None,
                source=self.source,
                enforce_contracts=True,
                execution_context=execution_context,
                prepare_inputs=False,
            )
        output: Any = outputs[node.id]
        served_meta: Mapping[str, Any] | None = None
        if isinstance(output, dict):
            output = output[point.port_label]
            data_path, _working, _committed = self._api_input_paths(node)
            served_meta = reads.served.get(data_path)
        frame = output.lazy() if isinstance(output, pl.DataFrame) else output
        return frame, served_meta


def _project(frame: pl.LazyFrame, demand: NodeSnapshotColumns) -> pl.LazyFrame:
    """Select the demanded columns in schema order, failing on any absent column.

    An empty demand keeps one carrier column so the frame keeps its row count.
    """
    if demand.names is None:
        return frame
    from haute._polars_utils import projected_or_carrier_columns

    schema_columns = list(frame.collect_schema().names())
    missing = sorted(demand.names - set(schema_columns))
    if missing:
        raise PointColumnsMissingError(missing)
    return frame.select(projected_or_carrier_columns(schema_columns, demand.names))


def resolve_point(
    graph: PipelineGraph,
    point: DataPoint,
    *,
    source: str,
    columns: NodeSnapshotColumns,
    store: NodeSnapshotStore | None = None,
    building: BuildingProbe | None = None,
) -> PointResolution:
    return DataPointResolver(graph, source=source, store=store, building=building).resolve(
        point, columns
    )


@contextlib.contextmanager
def lease_point_frame(
    graph: PipelineGraph,
    point: DataPoint,
    source: str,
    columns: NodeSnapshotColumns,
    *,
    store: NodeSnapshotStore | None = None,
    execution_context: ExecutionContext | None = None,
) -> Iterator[LeasedPointFrame]:
    """Yield a leased frame for *point* projected to *columns*, or raise ``cache_required``."""
    resolver = DataPointResolver(graph, source=source, store=store)
    with resolver.lease_frame(point, columns, execution_context=execution_context) as leased:
        yield leased
