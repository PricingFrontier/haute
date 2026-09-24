"""Data points: resolve a consumer node and column demand to leased, current data.

A data point is the data at one pipeline location — ``(producer node, port)`` —
independent of which consumer asks for it. Each point has one kind:

- ``data_input``: a Data Input with blank post-load code. Its frame is the lazy
  execution of that single source node over direct Parquet or its published
  input snapshot, so selections and renames apply exactly as in a run.
- ``api_input_table``: one port of a structured ``apiInput``, served from that
  table's input snapshot without ever shredding the raw source.
- ``node_output``: any other producer, including a Data Input with post-load
  code, read from its node-output snapshot.

Consumers never read a point that is not ``current`` for their column demand:
:func:`lease_point_frame` raises :class:`CacheRequiredError` carrying the state.
"""

from __future__ import annotations

import contextlib
import hashlib
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

import polars as pl

from haute._cache import GraphFingerprintMemo, canonical_json, graph_fingerprint
from haute._execution_context import ExecutionContext
from haute._node_snapshots import (
    BOUNDED_SEMANTICS_CLASS,
    NodeSnapshotColumns,
    NodeSnapshotGeneration,
    NodeSnapshotSlot,
    NodeSnapshotStore,
    node_snapshot_signature,
    pipeline_source_file_key,
)
from haute._source_cache import (
    SourceCacheGenerationMissingError,
    SourceCacheIdentity,
    SourceCacheStatus,
)
from haute._types import GraphEdge, GraphNode, NodeType, PipelineGraph

PointKind = Literal["data_input", "api_input_table", "node_output"]
PointState = Literal["current", "stale", "partial", "missing", "building", "corrupt"]
BuildingProbe = Callable[[PointKind, str], bool]
"""Report whether a build is running for a point, given its kind and build key.

The build key is the input-snapshot identity digest for ``data_input`` and for
``api_input_table`` (the table's own identity), and the node-output identity
digest for ``node_output``.
"""


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


class PointDataChangedError(RuntimeError):
    """The data a resolution named changed before a worker could read it."""

    error_code = "node_data_changed"

    def __init__(self, resolution: PointResolution) -> None:
        super().__init__(
            f"The data for node {resolution.point.producer_node_id!r} changed while it was "
            "being analysed; run the analysis again."
        )
        self.resolution = resolution


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
    from haute._graph_utils import upstream_subgraph

    return graph_fingerprint(upstream_subgraph(graph, node_id), memo=memo)


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

    def point_digest(self, point: DataPoint) -> str:
        """Consumer-independent digest of a data point, the key of its analyses."""
        payload = {
            "schema_version": 1,
            "pipeline_source_file": pipeline_source_file_key(self.graph),
            "producer_node_id": point.producer_node_id,
            "port_label": point.port_label,
            "source": self.source,
        }
        return hashlib.sha256(canonical_json(payload).encode("utf-8")).hexdigest()

    def api_input_table_labels(self, node_id: str) -> tuple[str, ...]:
        """The emitting table labels of a structured API Input, in schema order.

        Empty for any other node, and for an API Input whose schema the node
        builder would reject: it has no tables to hold.
        """
        return tuple(table.label for table in self._api_input_tables(node_id))

    def api_input_table_digests(self, node_id: str) -> tuple[str, ...]:
        """The distinct input-snapshot identities of those tables."""
        return tuple(
            dict.fromkeys(table.identity.digest for table in self._api_input_tables(node_id))
        )

    def _api_input_tables(self, node_id: str) -> tuple[Any, ...]:
        from haute._api_input_schema import ApiInputSchemaError, is_json_api_input_path
        from haute._json_shred._snapshots import api_input_snapshot_source
        from haute._node_apply import _anchored_required_path

        node = _node(self.graph, node_id)
        config = node.data.config
        path = config.get("path")
        if (
            node.data.nodeType != NodeType.API_INPUT
            or not isinstance(path, str)
            or not path
            or not is_json_api_input_path(path)
            or not isinstance(config.get("tables"), list)
        ):
            return ()
        try:
            data_path = _anchored_required_path(config, _pipeline_base_dir(self.graph))
            source = api_input_snapshot_source(config, data_path)
        except (ApiInputSchemaError, TypeError, ValueError):
            return ()
        return source.tables

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
        return self._input_snapshot_resolution(
            point, demand, kind="data_input", identity=identity, status=status
        )

    def _resolve_api_input_table(
        self, point: DataPoint, demand: NodeSnapshotColumns
    ) -> PointResolution:
        from haute._json_shred._snapshots import (
            api_input_snapshot_source,
            api_input_source_signature,
            freshness_signature,
        )
        from haute._node_apply import _anchored_required_path

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
        data_path = _anchored_required_path(config, _pipeline_base_dir(self.graph))
        source = api_input_snapshot_source(config, data_path)
        assert point.port_label is not None
        try:
            identity = source.table(point.port_label).identity
        except KeyError:
            raise NodeDataPointInvalidError(
                f"API Input {node.data.label!r} does not emit table {point.port_label!r}."
            ) from None
        status = self.store.status(
            identity,
            source_signature=freshness_signature(api_input_source_signature(source.data_path)),
        )
        return self._input_snapshot_resolution(
            point, demand, kind="api_input_table", identity=identity, status=status
        )

    def _input_snapshot_resolution(
        self,
        point: DataPoint,
        demand: NodeSnapshotColumns,
        *,
        kind: PointKind,
        identity: SourceCacheIdentity,
        status: SourceCacheStatus,
    ) -> PointResolution:
        """A point served by one input snapshot: a Data Input or an API-input table."""
        state: PointState
        if status.state == "ready":
            state = "stale" if status.freshness == "stale" else "current"
        elif status.state == "corrupt":
            state = "corrupt"
        else:
            state = "missing"
        if state != "current" and self._is_building(kind, identity.digest):
            state = "building"
        generation_id = status.generation.generation_id if status.generation else None
        lineage = _lineage_fingerprint(self.graph, point.producer_node_id, self._memo)
        return PointResolution(
            point=point,
            kind=kind,
            state=state,
            demand=demand,
            data_version=(
                _version(
                    kind,
                    {"generation": generation_id, "port": point.port_label, "lineage": lineage}
                    if kind == "api_input_table"
                    else {"generation": generation_id, "lineage": lineage},
                )
                if generation_id is not None
                else None
            ),
            build_key=identity.digest,
            input_identity=identity,
            input_generation_id=generation_id,
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
        with self.lease_resolved(resolution, execution_context=execution_context) as leased:
            yield leased

    @contextlib.contextmanager
    def lease_resolved(
        self,
        resolution: PointResolution,
        *,
        exact: bool = False,
        execution_context: ExecutionContext | None = None,
    ) -> Iterator[LeasedPointFrame]:
        """Lease the data a current resolution names.

        A node output, a snapshot-backed Data Input and an API-input table always
        read exactly the resolved generation. With ``exact`` — a spawned worker
        reading the resolution its parent leased — a direct Parquet file that has
        moved on since resolution raises :class:`PointDataChangedError` instead
        of being read under a new version.
        """
        point, demand = resolution.point, resolution.demand
        if resolution.state != "current":
            raise CacheRequiredError(resolution)
        assert resolution.data_version is not None
        if resolution.kind in ("node_output", "api_input_table"):
            # Both are exactly their generation: an API-input table has no
            # post-load code, so its data is the table the build wrote.
            if resolution.kind == "node_output":
                assert resolution.node_output_generation is not None
                identity = resolution.node_output_identity
                generation_id: str | None = resolution.node_output_generation.generation_id
            else:
                identity = resolution.input_identity
                generation_id = resolution.input_generation_id
            assert identity is not None and generation_id is not None
            try:
                lease = self.store.lease_generation(identity, generation_id)
                leased = lease.__enter__()
            except SourceCacheGenerationMissingError:
                # Retired between resolution and lease: report the point as it is now.
                raise CacheRequiredError(self.resolve(point, demand)) from None
            try:
                yield LeasedPointFrame(
                    kind=resolution.kind,
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
                try:
                    stack.enter_context(
                        self.store.lease_generation(
                            resolution.input_identity, resolution.input_generation_id
                        )
                    )
                except SourceCacheGenerationMissingError:
                    raise CacheRequiredError(self.resolve(point, demand)) from None
            frame = self._source_node_frame(point, execution_context=execution_context)
            if resolution.input_identity is not None:
                current = self.store.status(resolution.input_identity).generation
                if current is None or current.generation_id != resolution.input_generation_id:
                    # The execution leased a newer generation than the one resolved.
                    raise CacheRequiredError(self.resolve(point, demand))
            else:
                observed = self._resolve_data_input(point, demand).data_version
                assert observed is not None
                data_version = observed
            if exact and data_version != resolution.data_version:
                raise PointDataChangedError(resolution)
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
    ) -> pl.LazyFrame:
        """Lazily execute the single Data Input node, never building its snapshot."""
        from haute.execution import execute_lazy_graph
        from haute.executor import _build_node_fn, _compile_preamble, _pipeline_dir

        node = _node(self.graph, point.producer_node_id)
        subgraph = self.graph.model_copy(update={"nodes": [node], "edges": []})
        preamble_ns = _compile_preamble(
            subgraph.preamble or "", pipeline_dir=_pipeline_dir(subgraph)
        )
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
        return output.lazy() if isinstance(output, pl.DataFrame) else output


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
