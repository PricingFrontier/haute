"""RAM estimation for training — metadata-based approach.

Before materialising a full pipeline for model training, estimate the
memory footprint from parquet metadata alone:

1. Walk the graph backwards from the training node (respecting the
   active source and live-switch pruning) to find ancestor sources.
2. Read parquet row-group metadata — row count and column count — from
   those sources.  This is instant (reads only the file footer).
3. Estimate ``bytes_per_row`` from column count × dtype width, then
   apply an algorithm-specific overhead multiplier.
4. If the estimate exceeds available RAM, calculate a safe row limit.

This replaces the previous probe-based approach which ran a 1 000-row
sample through the pipeline. The probe was fragile: inner joins with no
key overlap in small samples produced zero rows, breaking the estimate.
When source metadata, projected width, or row-cardinality evidence is not
provable, the estimator returns an explicit unavailable result rather than
manufacturing a number.

Host-side observation (what the machine has: available RAM/VRAM, cgroup
headroom) lives in :mod:`haute._host_memory`; this module estimates what
the workload needs and compares the two.
"""

from __future__ import annotations

import math
from collections.abc import Iterable, Iterator, Mapping, Sequence
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from types import MappingProxyType
from typing import Any, NamedTuple, cast

import polars as pl

from haute._api_input_schema import ApiInputSchemaError, is_json_api_input_path
from haute._cardinality import join_cardinality_upper_bound
from haute._column_lineage import RowCardinalityAnalysis, analyze_polars_cardinality
from haute._edge_join import (
    build_edge_join_kwargs,
    edge_join_key_columns_by_role,
    resolve_edge_join_role_indices,
)
from haute._graph_utils import build_parents_of, edge_input_name
from haute._host_memory import available_ram_bytes, require_positive_available_ram
from haute._logging import get_logger
from haute._polars_utils import read_parquet_metadata
from haute._types import GraphEdge, GraphNode, NodeType, PipelineGraph
from haute.errors import ConfigError
from haute.projection import ProjectionEdgeKey

logger = get_logger(component="ram_estimate")

__all__ = [
    "MaterialisationEstimate",
    "MaterialisationEstimateBasis",
    "MaterialisationEstimateState",
    "estimate_gpu_vram_bytes",
    "estimate_materialisation_boundaries",
    "estimate_safe_training_rows",
    "RamEstimate",
]


class MaterialisationEstimateState(StrEnum):
    """Availability state for a conservative full-boundary estimate."""

    AVAILABLE = "available"
    UNAVAILABLE = "unavailable"


class MaterialisationEstimateBasis(StrEnum):
    """Evidence used for a materialisation estimate's width."""

    PROVIDED = "provided"
    PROJECTED_COLUMNS = "projected_columns"
    COMPLETE_WIDTH_FALLBACK = "complete_width_fallback"


@dataclass(frozen=True, slots=True)
class MaterialisationEstimate:
    """Conservative peak estimate used to admit a full materialisation.

    Unknown is represented only by ``state=unavailable`` and ``None``.  Zero
    therefore remains an honest estimate for a known-empty input.
    """

    state: MaterialisationEstimateState
    estimated_peak_bytes: int | None
    assumptions: tuple[str, ...] = ()
    unavailable_reason: str | None = None
    basis: MaterialisationEstimateBasis = MaterialisationEstimateBasis.PROVIDED

    def __post_init__(self) -> None:
        if not isinstance(self.basis, MaterialisationEstimateBasis):
            raise TypeError("materialisation estimate basis must be a MaterialisationEstimateBasis")
        if self.state is MaterialisationEstimateState.AVAILABLE:
            if (
                not isinstance(self.estimated_peak_bytes, int)
                or isinstance(self.estimated_peak_bytes, bool)
                or self.estimated_peak_bytes < 0
            ):
                raise ValueError(
                    "an available materialisation estimate requires a non-negative integer"
                )
            if self.unavailable_reason is not None:
                raise ValueError("an available materialisation estimate has no unavailable reason")
        elif self.estimated_peak_bytes is not None:
            raise ValueError("an unavailable materialisation estimate must use None")

    @classmethod
    def available(
        cls,
        estimated_peak_bytes: int,
        *,
        assumptions: Iterable[str] = (),
        basis: MaterialisationEstimateBasis = MaterialisationEstimateBasis.PROVIDED,
    ) -> MaterialisationEstimate:
        return cls(
            state=MaterialisationEstimateState.AVAILABLE,
            estimated_peak_bytes=estimated_peak_bytes,
            assumptions=tuple(str(item) for item in assumptions),
            basis=basis,
        )

    @classmethod
    def unavailable(cls, reason: str) -> MaterialisationEstimate:
        if not reason:
            raise ValueError("an unavailable materialisation estimate requires a reason")
        return cls(
            state=MaterialisationEstimateState.UNAVAILABLE,
            estimated_peak_bytes=None,
            unavailable_reason=reason,
        )


# ---------------------------------------------------------------------------
# GPU VRAM estimation
# ---------------------------------------------------------------------------


# CatBoost GPU stores per row:
#   float32 feature data  (4 bytes/feature)
#   + binarised features  (1 byte/feature)
#   + label/gradient/hessian (12 bytes)
# Plus histogram buffers that depend on border_count and tree depth,
# and ~500 MB CUDA runtime overhead.
# The 2× safety multiplier accounts for CUDA fragmentation,
# CatBoost-internal temporary buffers, and memory that grows during
# tree construction.  Empirically validated: 10M rows × 100 features
# OOM'd an 8 GB GPU at iteration 231/1000, confirming that the raw
# data footprint (~5 GB) roughly doubles during training.
_VRAM_SAFETY_MULTIPLIER = 2.0


def estimate_gpu_vram_bytes(
    n_rows: int,
    n_features: int,
    *,
    border_count: int = 128,
    depth: int = 6,
) -> int:
    """Estimate CatBoost GPU VRAM needed for *n_rows* × *n_features*."""
    feature_bytes = n_rows * n_features * 5  # float32 + binarised
    per_row_bytes = n_rows * 12  # label + gradient + hessian
    n_leaves = 2 ** min(depth, 10)
    histogram_bytes = n_features * border_count * n_leaves * 8

    raw = feature_bytes + per_row_bytes + histogram_bytes
    return int(raw * _VRAM_SAFETY_MULTIPLIER)


# ---------------------------------------------------------------------------
# Source metadata — source-aware
# ---------------------------------------------------------------------------


class _DetailedSourceMetadata(NamedTuple):
    row_count: int
    column_count: int
    columns: Mapping[str, str]
    column_width_keys: Mapping[str, str]
    column_uncompressed_size_bytes: Mapping[str, int]
    uncompressed_size_bytes: int
    column_expanded_width_bytes: Mapping[str, float] | None = None


class _AncestorSourceMetadata(NamedTuple):
    row_count: int | None
    column_count: int
    sources: tuple[_DetailedSourceMetadata, ...]


class _ResolvedTargetColumns(NamedTuple):
    columns: tuple[str, ...]
    width_columns: Mapping[str, str]


@dataclass(frozen=True, slots=True)
class _ResolvedRowCardinality:
    """One graph node's finite output/peak row proof, or a blocking reason."""

    output_rows: int | None
    peak_rows: int | None
    evidence: tuple[str, ...] = ()
    unavailable_reason: str | None = None
    blocking_node_id: str | None = None

    @property
    def available(self) -> bool:
        return self.output_rows is not None and self.peak_rows is not None

    @classmethod
    def proven(
        cls,
        output_rows: int,
        peak_rows: int,
        evidence: Iterable[str],
    ) -> _ResolvedRowCardinality:
        if (
            not isinstance(output_rows, int)
            or isinstance(output_rows, bool)
            or output_rows < 0
            or not isinstance(peak_rows, int)
            or isinstance(peak_rows, bool)
            or peak_rows < output_rows
        ):
            raise ValueError("row-cardinality proof requires finite non-negative bounds")
        return cls(
            output_rows=output_rows,
            peak_rows=peak_rows,
            evidence=_bounded_cardinality_evidence(evidence),
        )

    @classmethod
    def unavailable(
        cls,
        node_id: str,
        reason: str,
    ) -> _ResolvedRowCardinality:
        return cls(
            output_rows=None,
            peak_rows=None,
            unavailable_reason=reason,
            blocking_node_id=node_id,
        )


_MAX_CARDINALITY_EVIDENCE_ITEMS = 64


def _bounded_cardinality_evidence(items: Iterable[str]) -> tuple[str, ...]:
    """Deduplicate and cap planner evidence without hiding truncation."""

    unique = tuple(dict.fromkeys(str(item) for item in items if str(item)))
    if len(unique) <= _MAX_CARDINALITY_EVIDENCE_ITEMS:
        return unique
    retained = unique[: _MAX_CARDINALITY_EVIDENCE_ITEMS - 1]
    return retained + (f"cardinality_evidence_truncated={len(unique) - len(retained)}",)


@dataclass(slots=True)
class _EstimateGraphIndex:
    """Per-estimate graph indexes and memoized metadata/schema results."""

    graph: PipelineGraph
    source: str
    node_map: Mapping[str, GraphNode]
    pruned_edges: tuple[GraphEdge, ...]
    parents: Mapping[str, Sequence[str]]
    metadata_by_node: dict[str, _DetailedSourceMetadata | None]
    columns_by_target: dict[tuple[str, str | None], _ResolvedTargetColumns | None]
    resolving_targets: set[tuple[str, str | None]]
    port_metadata: dict[tuple[str, str], _DetailedSourceMetadata | None]
    cardinality_by_target: dict[tuple[str, str | None], _ResolvedRowCardinality]
    resolving_cardinality: set[tuple[str, str | None]]

    @classmethod
    def build(cls, graph: PipelineGraph, source: str) -> _EstimateGraphIndex:
        from haute._execute_lazy import _prune_live_switch_edges

        node_map = {node.id: node for node in graph.nodes}
        pruned_edges = tuple(_prune_live_switch_edges(graph.edges, node_map, source))
        return cls(
            graph=graph,
            source=source,
            node_map=node_map,
            pruned_edges=pruned_edges,
            parents=build_parents_of(list(pruned_edges), set(node_map)),
            metadata_by_node={},
            columns_by_target={},
            resolving_targets=set(),
            port_metadata={},
            cardinality_by_target={},
            resolving_cardinality=set(),
        )

    def source_metadata(self, node: GraphNode) -> _DetailedSourceMetadata | None:
        if node.id not in self.metadata_by_node:
            self.metadata_by_node[node.id] = _detailed_source_metadata_for_node(node)
        return self.metadata_by_node[node.id]

    def api_input_port_metadata(
        self,
        node: GraphNode,
        port: str,
    ) -> _DetailedSourceMetadata | None:
        """Metadata for one emitted table of a JSON API-input cache."""

        key = (node.id, port)
        if key not in self.port_metadata:
            self.port_metadata[key] = _json_api_input_port_metadata(node, port)
        return self.port_metadata[key]

    def parent_ports(self, child_id: str) -> tuple[tuple[str, str | None], ...]:
        """Return each (parent id, source handle) feeding one node."""

        return tuple(
            (edge.source, edge.sourceHandle)
            for edge in self.pruned_edges
            if edge.target == child_id and edge.source in self.node_map
        )

    def parent_port(self, child_id: str, parent_id: str) -> str | None:
        """Return the handle on one parent edge, or None for a handle-less edge."""

        matches = [
            edge.sourceHandle
            for edge in self.pruned_edges
            if edge.target == child_id and edge.source == parent_id
        ]
        if len(matches) != 1:
            raise RuntimeError(
                f"expected exactly one edge from {parent_id!r} to {child_id!r}; "
                f"found {len(matches)}"
            )
        return matches[0]

    def resolve_columns(
        self,
        target_node_id: str,
        port: str | None = None,
    ) -> _ResolvedTargetColumns | None:
        # The memo key carries the arrival port: two consumers of different
        # tables of the same multi-frame source resolve different columns.
        key = (target_node_id, port)
        if key in self.columns_by_target:
            return self.columns_by_target[key]
        if key in self.resolving_targets:
            raise RuntimeError("cycle encountered while resolving RAM-estimate columns")
        self.resolving_targets.add(key)
        try:
            resolved = _resolve_target_columns_from_index(self, target_node_id, port)
            self.columns_by_target[key] = resolved
            return resolved
        finally:
            self.resolving_targets.remove(key)

    def resolve_cardinality(
        self,
        target_node_id: str,
        port: str | None = None,
    ) -> _ResolvedRowCardinality:
        """Return a memoised finite row bound for one node/arrival port."""

        key = (target_node_id, port)
        if key in self.cardinality_by_target:
            return self.cardinality_by_target[key]
        if key in self.resolving_cardinality:
            raise RuntimeError("cycle encountered while resolving RAM-estimate cardinality")
        self.resolving_cardinality.add(key)
        try:
            resolved = _resolve_row_cardinality_from_index(self, target_node_id, port)
            self.cardinality_by_target[key] = resolved
            return resolved
        finally:
            self.resolving_cardinality.remove(key)


# Bytes per column for the analytical estimate.  Training features are
# cast to Float32 (4 bytes) in _build_pool, but the Polars DataFrame
def _parquet_metadata(path: str) -> tuple[int, int]:
    """Return (row_count, column_count) from parquet footer metadata."""
    meta = _detailed_parquet_metadata(path)
    return meta.row_count, meta.column_count


def _detailed_parquet_metadata(path: str) -> _DetailedSourceMetadata:
    """Return footer-only parquet metadata used by the RAM estimator."""
    meta = read_parquet_metadata(Path(path))
    columns = dict(meta.get("columns", {}))
    return _DetailedSourceMetadata(
        row_count=int(meta["row_count"]),
        column_count=int(meta["column_count"]),
        columns=columns,
        column_width_keys={str(column): str(column) for column in columns},
        column_uncompressed_size_bytes={
            str(name): int(size)
            for name, size in dict(meta.get("column_uncompressed_size_bytes", {})).items()
        },
        uncompressed_size_bytes=int(meta.get("uncompressed_size_bytes", 0)),
        column_expanded_width_bytes=_probe_expanded_variable_widths(
            path,
            columns,
            row_count=int(meta["row_count"]),
        ),
    )


def _probe_expanded_variable_widths(
    path: str,
    columns: Mapping[str, str],
    *,
    row_count: int,
    max_rows: int = 4096,
) -> Mapping[str, float]:
    """Return bounded in-memory widths for dictionary/variable columns.

    Parquet's uncompressed page size can still describe dictionary codes
    rather than the expanded Arrow/Polars string buffers.  A bounded head probe
    measures the representation the materialisation will actually allocate.
    """
    if row_count <= 0:
        return MappingProxyType({})
    variable_columns = [
        column
        for column, arrow_type in columns.items()
        if _is_variable_width_arrow_type(arrow_type)
    ]
    if not variable_columns:
        return MappingProxyType({})
    probe = pl.scan_parquet(path).select(variable_columns).head(min(row_count, max_rows)).collect()
    if probe.height == 0:
        return MappingProxyType({})
    return MappingProxyType(
        {
            column: probe.get_column(column).estimated_size() / probe.height
            for column in variable_columns
        }
    )


def _source_scoped_metadata(
    meta: _DetailedSourceMetadata,
    node_id: str,
) -> _DetailedSourceMetadata:
    return meta._replace(
        column_width_keys={column: f"{node_id}\0{column}" for column in meta.columns},
    )


def _data_input_parquet_artifact(config: Mapping[str, Any]) -> tuple[int | None, Path]:
    """Return the Parquet artifact used by a Data Input, with a free row count.

    Snapshot generations carry their row count in verified metadata; a direct
    Parquet source returns ``None`` so callers touch the footer only when they
    actually need the count — each caller reads source metadata at most once.
    """
    from haute._builders import _configured_pipeline_dir
    from haute._input_providers import source_cache_identity
    from haute._polars_io_registry import (
        anchor_config_source_path,
        data_input_is_direct,
        validate_data_input_config,
    )
    from haute._sandbox import _get_project_root
    from haute._source_cache import SourceCacheStore

    base_dir = _configured_pipeline_dir()
    validated = validate_data_input_config(config)
    if data_input_is_direct(validated):
        anchored = anchor_config_source_path(validated, base_dir)
        return None, Path(str(anchored["path"]))

    identity = source_cache_identity(
        validated,
        base_dir=base_dir,
    )
    generation = SourceCacheStore(_get_project_root()).open_generation(identity)
    return generation.metadata.row_count, generation.data_path


def _json_api_input_port_metadata(node: GraphNode, port: str) -> _DetailedSourceMetadata | None:
    """Return cached parquet metadata for one emitted table of a JSON API input.

    A v2 JSON API-input cache is one parquet per emit-true table, so the node
    as a whole has no single (row_count, column_count) summary — but each table
    does, and an edge names the exact table it carries. Resolving per port is
    what lets a downstream boundary be estimated at all; without it every
    group-by under an API input was refused for want of an estimate.

    Layer preference and cache validity are delegated to the same reader the
    engine uses, so a stale cache is rejected here exactly as it is at
    execution rather than silently sizing a boundary from the wrong data.
    """

    from haute._api_input_schema import sanitise_label_for_filesystem as _sanitise_label
    from haute._json_flatten import _json_cache_dir
    from haute._json_shred._cache import (
        _cache_manifest_structure_failure,
        _read_matching_cache_meta_unlocked,
        _read_per_port_cache_meta_unlocked,
    )
    from haute._json_shred._publication import _build_lock_for
    from haute._json_shred._runtime_storage import (
        _release_runtime_snapshot,
        _snapshot_cache_artifact_locked,
    )
    from haute._json_shred._shred import (
        _declared_frame_schema,
        _emitting_table_specs,
        _v2_fingerprint,
    )
    from haute._json_shred._source_proof import _data_file_signature

    config = dict(node.data.config)
    raw_path = config.get("path", "")
    if not isinstance(raw_path, str) or not raw_path:
        return None
    data_path = Path(raw_path)
    try:
        if not data_path.exists():
            return None
        complete_specs = _emitting_table_specs(config)
        specs_by_label = {spec.label: spec for spec in complete_specs}
        port_spec = specs_by_label.get(port)
        if port_spec is None:
            return None
        expected_labels = tuple(spec.label for spec in complete_specs)
        expected_fingerprint = _v2_fingerprint(config)
        signature: Mapping[str, Any] | None = None
        for layer in ("working", "committed"):
            cache_dir = _json_cache_dir(data_path, layer)
            with _build_lock_for(cache_dir):
                candidate_meta = _read_per_port_cache_meta_unlocked(cache_dir)
                if (
                    candidate_meta is None
                    or candidate_meta.get("schema_mode") != "v2"
                    or candidate_meta.get("schema_fingerprint") != expected_fingerprint
                ):
                    continue
                if signature is None:
                    signature = _data_file_signature(data_path)
                meta = _read_matching_cache_meta_unlocked(
                    cache_dir,
                    config,
                    data_path=data_path,
                    data_file_signature=signature,
                )
                if meta is None or _cache_manifest_structure_failure(
                    meta,
                    expected_labels=expected_labels,
                ):
                    continue
                entries = {entry["label"]: entry for entry in meta["tables"]}
                parquet_path = cache_dir / f"{_sanitise_label(port)}.parquet"
                snapshot_path = _snapshot_cache_artifact_locked(
                    cache_dir,
                    parquet_path,
                    entries[port]["content_signature"],
                )
                if snapshot_path is None:
                    continue
                try:
                    actual_schema = pl.scan_parquet(snapshot_path).collect_schema()
                    expected_schema = _declared_frame_schema(port_spec)
                    if dict(actual_schema.items()) != dict(expected_schema.items()):
                        continue
                    return _source_scoped_metadata(
                        _detailed_parquet_metadata(str(snapshot_path)),
                        node.id,
                    )
                finally:
                    _release_runtime_snapshot(snapshot_path)
    except (
        ApiInputSchemaError,
        OSError,
        TypeError,
        ValueError,
        pl.exceptions.PolarsError,
    ) as exc:
        logger.warning(
            "api_input_port_metadata_failed",
            node_id=node.id,
            port=port,
            error=str(exc),
        )
    return None


def _detailed_source_metadata_for_node(node: GraphNode) -> _DetailedSourceMetadata | None:
    """Return detailed parquet source metadata for a source node, or None."""
    config = node.data.config
    node_type = node.data.nodeType

    try:
        path = config.get("path", "")

        if node_type == NodeType.API_INPUT:
            if not path:
                return None
            if isinstance(path, str) and is_json_api_input_path(path):
                # v2 per-frame caches are one parquet per emit-true table,
                # so there's no single (row_count, column_count) summary
                # to return. Conservative None lets the caller fall back
                # to its "unknown source size" branch.
                return None
            if Path(path).exists():
                return _source_scoped_metadata(_detailed_parquet_metadata(path), node.id)
            return None

        if node_type == NodeType.DATA_INPUT:
            _, snapshot_path = _data_input_parquet_artifact(config)
            return _source_scoped_metadata(
                _detailed_parquet_metadata(str(snapshot_path)),
                node.id,
            )
    except (OSError, TypeError, ValueError) as exc:
        logger.warning("source_metadata_failed", node_id=node.id, error=str(exc))
        return None

    return None


def _detailed_ancestor_source_metadata(
    graph: PipelineGraph,
    target_node_id: str,
    source: str = "live",
    *,
    _index: _EstimateGraphIndex | None = None,
) -> _AncestorSourceMetadata:
    from haute._topo import ancestors

    index = _index or _EstimateGraphIndex.build(graph, source)
    ancestor_ids = ancestors(
        target_node_id,
        list(index.pruned_edges),
        set(index.node_map),
    )

    max_rows: int | None = None
    max_cols: int = 0
    sources: list[_DetailedSourceMetadata] = []

    reachable = set(ancestor_ids) | {target_node_id}
    for nid in sorted(ancestor_ids):
        node = index.node_map.get(nid)
        if node is None:
            continue
        if node.data.nodeType not in (NodeType.API_INPUT, NodeType.DATA_INPUT):
            continue
        node_metadata = [index.source_metadata(node)]
        if node_metadata[0] is None and node.data.nodeType == NodeType.API_INPUT:
            # A multi-frame JSON API input has no whole-node summary. Size it
            # from exactly the tables that feed this target, not from every
            # table it could emit.
            node_metadata = [
                index.api_input_port_metadata(node, port)
                for port in _feeding_ports(index, nid, reachable)
            ]
        for meta in node_metadata:
            if meta is None:
                continue
            sources.append(meta)
            if max_rows is None or meta.row_count > max_rows:
                max_rows = meta.row_count
            max_cols = max(max_cols, meta.column_count)

    return _AncestorSourceMetadata(max_rows, max_cols, tuple(sources))


def _feeding_ports(
    index: _EstimateGraphIndex,
    source_node_id: str,
    reachable: set[str],
) -> tuple[str, ...]:
    """Return the distinct source handles this node contributes to `reachable`."""

    ports = {
        edge.sourceHandle
        for edge in index.pruned_edges
        if edge.source == source_node_id and edge.target in reachable and edge.sourceHandle
    }
    return tuple(sorted(ports))


def _cardinality_from_analysis(
    node_id: str,
    analysis: RowCardinalityAnalysis,
    input_results: Iterable[_ResolvedRowCardinality],
    *,
    prior_evidence: Iterable[str] = (),
) -> _ResolvedRowCardinality:
    """Combine one local AST proof with already-proven parent bounds."""

    if not analysis.supported:
        return _ResolvedRowCardinality.unavailable(node_id, analysis.reason)
    assert analysis.output_upper_bound is not None
    assert analysis.peak_upper_bound is not None
    parents = tuple(input_results)
    parent_peak = max((result.peak_rows or 0 for result in parents), default=0)
    return _ResolvedRowCardinality.proven(
        analysis.output_upper_bound,
        max(parent_peak, analysis.peak_upper_bound),
        (
            *(item for result in parents for item in result.evidence),
            *prior_evidence,
            *(f"node={node_id}:{item}" for item in analysis.evidence),
        ),
    )


def _named_cardinality_inputs(
    index: _EstimateGraphIndex,
    node: GraphNode,
    edge_results: Sequence[tuple[GraphEdge, _ResolvedRowCardinality]],
    *,
    alias_first_as_df: bool = False,
) -> Mapping[str, _ResolvedRowCardinality] | None:
    """Mirror runtime edge/inputMapping names for AST cardinality analysis."""

    by_name: dict[str, tuple[str, _ResolvedRowCardinality]] = {}
    for edge, result in edge_results:
        try:
            name = edge_input_name(edge, index.node_map[edge.source])
        except (KeyError, ValueError):
            return None
        previous = by_name.get(name)
        if previous is not None and previous[0] != edge.id:
            return None
        by_name[name] = (edge.id, result)

    raw_mapping = node.data.config.get("inputMapping")
    if raw_mapping:
        if not isinstance(raw_mapping, Mapping):
            return None
        for alias, current_name in raw_mapping.items():
            if (
                not isinstance(alias, str)
                or not alias
                or not isinstance(current_name, str)
                or current_name not in by_name
            ):
                return None
            current = by_name[current_name]
            existing = by_name.get(alias)
            if existing is not None and existing[0] != current[0]:
                return None
            by_name[alias] = current

    if alias_first_as_df:
        if not edge_results:
            return None
        first_edge, first_result = edge_results[0]
        existing = by_name.get("df")
        if existing is not None and existing[0] != first_edge.id:
            return None
        by_name["df"] = (first_edge.id, first_result)

    return MappingProxyType({name: result for name, (_edge_id, result) in by_name.items()})


def _passthrough_cardinality(
    node_id: str,
    parents: Sequence[_ResolvedRowCardinality],
    selected_index: int = 0,
    *,
    evidence: str = "row_cardinality_preserved",
) -> _ResolvedRowCardinality:
    if not parents or selected_index < 0 or selected_index >= len(parents):
        return _ResolvedRowCardinality.unavailable(node_id, "input_cardinality_unavailable")
    selected = parents[selected_index]
    assert selected.output_rows is not None and selected.peak_rows is not None
    return _ResolvedRowCardinality.proven(
        selected.output_rows,
        max(result.peak_rows or 0 for result in parents),
        (
            *(item for result in parents for item in result.evidence),
            f"node={node_id}:{evidence}",
            f"node={node_id}:cardinality_output_upper_bound={selected.output_rows}",
        ),
    )


def _edge_index_for_input_name(
    edge_results: Sequence[tuple[GraphEdge, _ResolvedRowCardinality]],
    index: _EstimateGraphIndex,
    input_name: object,
) -> int | None:
    """Return the position of the one edge whose executable name is *input_name*.

    Estimation never raises for a stale or ambiguous selector; ``None`` lets
    the caller report the cardinality as unavailable instead.
    """
    if not isinstance(input_name, str) or not input_name:
        return None
    matching = [
        edge_index
        for edge_index, (edge, _result) in enumerate(edge_results)
        if edge_input_name(edge, index.node_map[edge.source]) == input_name
    ]
    return matching[0] if len(matching) == 1 else None


def _resolve_row_cardinality_from_index(
    index: _EstimateGraphIndex,
    target_node_id: str,
    port: str | None,
) -> _ResolvedRowCardinality:
    """Prove one graph node's output and peak row bounds without executing it."""

    node = index.node_map.get(target_node_id)
    if node is None:
        return _ResolvedRowCardinality.unavailable(target_node_id, "node_missing")
    node_type = node.data.nodeType

    if node_type in {NodeType.API_INPUT, NodeType.DATA_INPUT}:
        metadata = index.source_metadata(node)
        if metadata is None and node_type is NodeType.API_INPUT and port is not None:
            metadata = index.api_input_port_metadata(node, port)
        if metadata is None:
            return _ResolvedRowCardinality.unavailable(
                target_node_id,
                "source_row_count_unavailable",
            )
        base = _ResolvedRowCardinality.proven(
            metadata.row_count,
            metadata.row_count,
            (f"source={target_node_id}:row_count={metadata.row_count}",),
        )
        code = node.data.config.get("code")
        if node_type is NodeType.DATA_INPUT and isinstance(code, str) and code.strip():
            analysis = analyze_polars_cardinality(code, {"df": metadata.row_count})
            return _cardinality_from_analysis(target_node_id, analysis, (base,))
        return base

    if node_type is NodeType.CONSTANT:
        return _ResolvedRowCardinality.proven(
            1,
            1,
            (f"source={target_node_id}:constant_row_count=1",),
        )

    incoming_edges = tuple(
        edge
        for edge in index.pruned_edges
        if edge.target == target_node_id and edge.source in index.node_map
    )
    edge_results = tuple(
        (edge, index.resolve_cardinality(edge.source, edge.sourceHandle)) for edge in incoming_edges
    )
    for _edge, result in edge_results:
        if not result.available:
            return result
    parents = tuple(result for _edge, result in edge_results)

    if node_type is NodeType.EDGE_JOIN:
        if len(edge_results) != 2:
            return _ResolvedRowCardinality.unavailable(target_node_id, "invalid_join_arity")
        target_handles = [edge.targetHandle for edge, _result in edge_results]
        try:
            left_index, right_index = resolve_edge_join_role_indices(target_handles)
            kwargs = build_edge_join_kwargs(node.data.config)
            left = parents[left_index]
            right = parents[right_index]
            assert left.output_rows is not None and right.output_rows is not None
            bound = join_cardinality_upper_bound(
                left.output_rows,
                right.output_rows,
                how=str(kwargs["how"]),
                validate=cast(str | None, kwargs.get("validate")),
            )
        except (ConfigError, TypeError, ValueError):
            return _ResolvedRowCardinality.unavailable(target_node_id, "invalid_join_config")
        return _ResolvedRowCardinality.proven(
            bound.max_rows,
            max(*(result.peak_rows or 0 for result in parents), bound.max_rows),
            (
                *(item for result in parents for item in result.evidence),
                *(f"node={target_node_id}:{item}" for item in bound.evidence),
            ),
        )

    if node_type is NodeType.POLARS:
        bindings = _named_cardinality_inputs(index, node, edge_results)
        if not bindings:
            return _ResolvedRowCardinality.unavailable(
                target_node_id,
                "invalid_input_name_binding",
            )
        code = node.data.config.get("code")
        if not isinstance(code, str) or not code.strip():
            return _ResolvedRowCardinality.unavailable(target_node_id, "empty_code")
        analysis = analyze_polars_cardinality(
            code,
            {name: cast(int, result.output_rows) for name, result in bindings.items()},
        )
        return _cardinality_from_analysis(target_node_id, analysis, parents)

    if node_type is NodeType.SCENARIO_EXPANDER:
        if len(parents) != 1:
            return _ResolvedRowCardinality.unavailable(
                target_node_id,
                "invalid_input_cardinality",
            )
        from haute._node_apply import _DEFAULT_SCENARIO_STEPS

        raw_steps = node.data.config.get("steps")
        try:
            steps = int(raw_steps) if raw_steps is not None else _DEFAULT_SCENARIO_STEPS
        except (TypeError, ValueError, OverflowError):
            return _ResolvedRowCardinality.unavailable(target_node_id, "invalid_scenario_steps")
        if steps < 1:
            return _ResolvedRowCardinality.unavailable(target_node_id, "invalid_scenario_steps")
        parent = parents[0]
        assert parent.output_rows is not None and parent.peak_rows is not None
        expanded_rows = parent.output_rows * steps
        expanded = _ResolvedRowCardinality.proven(
            expanded_rows,
            max(parent.peak_rows, expanded_rows),
            (
                *parent.evidence,
                f"node={target_node_id}:scenario_steps={steps}",
                f"node={target_node_id}:cardinality_output_upper_bound={expanded_rows}",
            ),
        )
        code = node.data.config.get("code")
        if isinstance(code, str) and code.strip():
            analysis = analyze_polars_cardinality(code, {"df": expanded_rows})
            return _cardinality_from_analysis(target_node_id, analysis, (expanded,))
        return expanded

    if node_type in {NodeType.RATING_STEP, NodeType.EXPLORE}:
        if len(parents) != 1:
            return _ResolvedRowCardinality.unavailable(
                target_node_id,
                "invalid_input_cardinality",
            )
        code = node.data.config.get("code")
        if isinstance(code, str) and code.strip():
            assert parents[0].output_rows is not None
            analysis = analyze_polars_cardinality(code, {"df": parents[0].output_rows})
            return _cardinality_from_analysis(target_node_id, analysis, parents)
        return _passthrough_cardinality(target_node_id, parents)

    if node_type is NodeType.MODEL_SCORE:
        if not parents:
            return _ResolvedRowCardinality.unavailable(
                target_node_id,
                "input_cardinality_unavailable",
            )
        code = node.data.config.get("code")
        if isinstance(code, str) and code.strip():
            bindings = _named_cardinality_inputs(
                index,
                node,
                edge_results,
                alias_first_as_df=True,
            )
            if not bindings:
                return _ResolvedRowCardinality.unavailable(
                    target_node_id,
                    "invalid_input_name_binding",
                )
            analysis = analyze_polars_cardinality(
                code,
                {name: cast(int, result.output_rows) for name, result in bindings.items()},
            )
            return _cardinality_from_analysis(target_node_id, analysis, parents)
        return _passthrough_cardinality(target_node_id, parents)

    if node_type is NodeType.OPTIMISER:
        if not parents:
            return _ResolvedRowCardinality.unavailable(
                target_node_id,
                "input_cardinality_unavailable",
            )
        selected_index = 0
        data_input = node.data.config.get("data_input")
        if data_input not in (None, ""):
            matched = _edge_index_for_input_name(edge_results, index, data_input)
            if matched is None:
                return _ResolvedRowCardinality.unavailable(
                    target_node_id,
                    "invalid_optimiser_input",
                )
            selected_index = matched
        elif len(edge_results) > 1:
            return _ResolvedRowCardinality.unavailable(
                target_node_id,
                "invalid_optimiser_input",
            )
        return _passthrough_cardinality(target_node_id, parents, selected_index)

    if node_type is NodeType.OPTIMISER_APPLY:
        if not parents:
            return _ResolvedRowCardinality.unavailable(
                target_node_id,
                "input_cardinality_unavailable",
            )
        if node.data.config.get("optimiser_mode") == "ratebook":
            matched = _edge_index_for_input_name(
                edge_results,
                index,
                node.data.config.get("ratebook_input"),
            )
            if matched is None:
                return _ResolvedRowCardinality.unavailable(
                    target_node_id,
                    "invalid_optimiser_apply_input",
                )
            return _passthrough_cardinality(target_node_id, parents, matched)
        output_rows = max(cast(int, result.output_rows) for result in parents)
        return _ResolvedRowCardinality.proven(
            output_rows,
            max(cast(int, result.peak_rows) for result in parents),
            (
                *(item for result in parents for item in result.evidence),
                f"node={target_node_id}:one_connected_input_selected",
                f"node={target_node_id}:cardinality_output_upper_bound={output_rows}",
            ),
        )

    if node_type is NodeType.EXTERNAL_FILE:
        if not parents:
            return _ResolvedRowCardinality.unavailable(
                target_node_id,
                "external_object_row_cardinality_unknown",
            )
        code = node.data.config.get("code")
        if isinstance(code, str) and code.strip():
            bindings = _named_cardinality_inputs(
                index,
                node,
                edge_results,
                alias_first_as_df=True,
            )
            if not bindings:
                return _ResolvedRowCardinality.unavailable(
                    target_node_id,
                    "invalid_input_name_binding",
                )
            analysis = analyze_polars_cardinality(
                code,
                {name: cast(int, result.output_rows) for name, result in bindings.items()},
            )
            return _cardinality_from_analysis(target_node_id, analysis, parents)
        return _passthrough_cardinality(target_node_id, parents)

    if node_type in {
        NodeType.BANDING,
        NodeType.DATA_OUTPUT,
        NodeType.LIVE_SWITCH,
        NodeType.MODELLING,
        NodeType.SUBMODEL,
        NodeType.SUBMODEL_PORT,
    }:
        if len(parents) != 1:
            return _ResolvedRowCardinality.unavailable(
                target_node_id,
                "invalid_input_cardinality",
            )
        return _passthrough_cardinality(target_node_id, parents)

    return _ResolvedRowCardinality.unavailable(
        target_node_id,
        "row_semantics_unsupported",
    )


# ---------------------------------------------------------------------------
# Estimation
# ---------------------------------------------------------------------------
#
# The training lifecycle has multiple memory-intensive phases:
#   0. Pipeline execution — _execute_and_sink collects the full lazy
#      plan to parquet.  Intermediate joins and transforms can hold
#      multiple large DataFrames simultaneously.
#   1. Split — reads the full dataset eagerly to add _partition column.
#   2. model.fit() — train + eval CatBoost Pools + internal buffers.
#   3. Diagnostics — SHAP, PDP, feature importance.
#   4. Cross-validation (if enabled).
#
# Pipeline execution is typically the peak because it holds join
# intermediates in memory.  Empirically validated: the peak is
# approximately 3× the raw dataset size at the training node.
#
# We use: N_rows × N_cols × 8 bytes (Float64) × 3.0

_RAM_SAFETY_FACTOR = 0.7
_MIN_SAFE_ROWS = 500

# Empirical overhead multiplier.  Covers pipeline execution (join
# intermediates), split, CatBoost Pool construction, and training
# buffers (gradients, hessians, histograms).  Validated against
# observed 25 GB peak for 10M × 101 cols (~8 GB raw data).
_OVERHEAD_MULTIPLIER = 3.0

_BYTES_PER_COL = 8  # Float64 in Polars


def _estimate_peak_bytes(
    n_rows: int,
    n_cols: int,
    *,
    base_bytes_per_row: float | None = None,
) -> int:
    """Estimate peak RAM for the full training lifecycle."""
    if not isinstance(n_rows, int) or isinstance(n_rows, bool) or n_rows < 0:
        raise ValueError("n_rows must be a non-negative integer")
    if not isinstance(n_cols, int) or isinstance(n_cols, bool) or n_cols < 0:
        raise ValueError("n_cols must be a non-negative integer")
    if base_bytes_per_row is None:
        width_numerator = n_cols * _BYTES_PER_COL
        width_denominator = 1
    else:
        if (
            not isinstance(base_bytes_per_row, (int, float))
            or isinstance(base_bytes_per_row, bool)
            or not math.isfinite(base_bytes_per_row)
            or base_bytes_per_row < 0
        ):
            raise ValueError("base_bytes_per_row must be a finite non-negative number")
        width_numerator, width_denominator = float(base_bytes_per_row).as_integer_ratio()
    overhead_numerator, overhead_denominator = _OVERHEAD_MULTIPLIER.as_integer_ratio()
    # Width probes can produce fractional per-row averages. Admission is a
    # safety boundary, so truncating even one fractional byte is the wrong
    # direction—and the absolute error can grow at extreme join cardinality.
    # Integer rational arithmetic also prevents a repeated join product from
    # overflowing IEEE-754 before Python can form the estimate.
    numerator = n_rows * width_numerator * overhead_numerator
    denominator = width_denominator * overhead_denominator
    return (numerator + denominator - 1) // denominator


class RamEstimate(NamedTuple):
    """Result of the RAM estimation."""

    safe_row_limit: int | None
    """Row limit that fits in RAM, or ``None`` if no limit is needed."""
    total_rows: int | None
    """Estimated total source rows, or ``None`` if unknown."""
    estimated_bytes: int
    """Estimated peak bytes across all training phases."""
    available_bytes: int
    """Available system RAM in bytes (estimation fails if this is unknown)."""
    bytes_per_row: float
    """Estimated bytes per row (at peak phase)."""
    was_downsampled: bool
    """Whether a row limit was applied."""
    warning: str | None
    """Human-readable warning message if downsampled, else ``None``."""
    probe_columns: int = 0
    """Number of columns (from source metadata)."""


def _resolve_target_columns(
    graph: PipelineGraph,
    target_node_id: str,
    source: str,
    *,
    _index: _EstimateGraphIndex | None = None,
) -> _ResolvedTargetColumns | None:
    """Resolve the detailed target schema through one per-estimate index."""
    index = _index or _EstimateGraphIndex.build(graph, source)
    return index.resolve_columns(target_node_id)


def _edge_join_input_roles(
    node: GraphNode,
    index: _EstimateGraphIndex,
) -> tuple[GraphEdge, GraphEdge] | None:
    incoming = [edge for edge in index.pruned_edges if edge.target == node.id]
    if len(incoming) != 2:
        return None
    try:
        base_index, join_index = resolve_edge_join_role_indices(
            [edge.targetHandle for edge in incoming]
        )
    except ConfigError:
        return None
    return incoming[base_index], incoming[join_index]


def _dedupe_columns(columns: Iterable[str]) -> tuple[str, ...]:
    seen: set[str] = set()
    ordered: list[str] = []
    for column in columns:
        if column in seen:
            continue
        seen.add(column)
        ordered.append(column)
    return tuple(ordered)


def _resolved_from_columns(columns: Iterable[str]) -> _ResolvedTargetColumns:
    deduped = _dedupe_columns(columns)
    return _ResolvedTargetColumns(
        columns=deduped,
        width_columns={column: column for column in deduped},
    )


def _resolved_from_source_metadata(
    meta: _DetailedSourceMetadata,
) -> _ResolvedTargetColumns:
    columns = _dedupe_columns(meta.columns)
    return _ResolvedTargetColumns(
        columns=columns,
        width_columns={column: meta.column_width_keys.get(column, column) for column in columns},
    )


def _dedupe_resolved_columns(
    columns: Iterable[tuple[str, str]],
) -> _ResolvedTargetColumns:
    seen: set[str] = set()
    ordered: list[str] = []
    width_columns: dict[str, str] = {}
    for output_column, width_column in columns:
        if output_column in seen:
            continue
        seen.add(output_column)
        ordered.append(output_column)
        width_columns[output_column] = width_column
    return _ResolvedTargetColumns(tuple(ordered), width_columns)


def _filter_resolved_columns(
    resolved: _ResolvedTargetColumns,
    selected: Iterable[str],
) -> _ResolvedTargetColumns:
    deduped = _dedupe_columns(selected)
    return _ResolvedTargetColumns(
        deduped,
        {column: resolved.width_columns.get(column, column) for column in deduped},
    )


def _has_selected_columns(config: Mapping[str, object]) -> bool:
    selected = config.get("selected_columns")
    return isinstance(selected, list) and len(selected) > 0


def _selected_column_names(config: Mapping[str, object]) -> tuple[str, ...] | None:
    selected = config.get("selected_columns")
    if not isinstance(selected, list) or len(selected) == 0:
        return None
    if not all(isinstance(column, str) for column in selected):
        return None
    return tuple(selected)


def _resolve_edge_join_columns(
    node: GraphNode,
    graph: PipelineGraph,
    source: str,
    *,
    _index: _EstimateGraphIndex | None = None,
) -> _ResolvedTargetColumns | None:
    index = _index or _EstimateGraphIndex.build(graph, source)
    return _resolve_edge_join_columns_from_index(node, index)


def _resolve_edge_join_columns_from_index(
    node: GraphNode,
    index: _EstimateGraphIndex,
) -> _ResolvedTargetColumns | None:
    roles = _edge_join_input_roles(node, index)
    if roles is None:
        return None
    base_edge, join_edge = roles
    kwargs = build_edge_join_kwargs(node.data.config)
    how = kwargs["how"]
    if how not in {"inner", "left"}:
        return None

    base_resolved = index.resolve_columns(base_edge.source, base_edge.sourceHandle)
    join_resolved = index.resolve_columns(join_edge.source, join_edge.sourceHandle)
    if base_resolved is None or join_resolved is None:
        return None

    base_columns = base_resolved.columns
    join_columns = join_resolved.columns
    _, join_key_columns = edge_join_key_columns_by_role(node.data.config)
    coalesce = kwargs.get("coalesce")
    if coalesce is not False:
        coalesced_join_keys = join_key_columns
    else:
        coalesced_join_keys = frozenset()

    suffix = str(kwargs["suffix"])
    output_columns = [
        (column, base_resolved.width_columns.get(column, column)) for column in base_columns
    ]
    base_set = set(base_columns)
    for column in join_columns:
        if column in coalesced_join_keys:
            continue
        width_column = join_resolved.width_columns.get(column, column)
        if column in base_set:
            output_columns.append((f"{column}{suffix}", width_column))
            continue
        output_columns.append((column, width_column))

    return _dedupe_resolved_columns(output_columns)


def _resolve_edge_join_column_names(
    node: GraphNode,
    graph: PipelineGraph,
    source: str,
) -> tuple[str, ...] | None:
    resolved = _resolve_edge_join_columns(node, graph, source)
    return resolved.columns if resolved is not None else None


def _resolve_target_columns_from_index(
    index: _EstimateGraphIndex,
    target_node_id: str,
    arrival_port: str | None = None,
) -> _ResolvedTargetColumns | None:
    """Resolve target columns through one memoized per-estimate graph index.

    `arrival_port` is the source handle the walk reached this node through. It
    matters only at a multi-frame source, where it names which emitted table's
    columns the consumer actually receives.
    """
    from collections import deque

    visited: set[tuple[str, str | None]] = set()
    queue = deque([(target_node_id, arrival_port)])
    while queue:
        nid, port = queue.popleft()
        if (nid, port) in visited:
            continue
        visited.add((nid, port))
        node = index.node_map.get(nid)
        if node is None:
            continue

        selected_columns = _selected_column_names(node.data.config)
        if _has_selected_columns(node.data.config) and selected_columns is None:
            return None

        if node.data.nodeType == NodeType.EDGE_JOIN:
            edge_join_columns = _resolve_edge_join_columns_from_index(node, index)
            if edge_join_columns is not None:
                if selected_columns is not None:
                    return _filter_resolved_columns(edge_join_columns, selected_columns)
                return edge_join_columns

        if selected_columns is not None:
            parent_ports = index.parent_ports(nid)
            if len(parent_ports) == 1:
                parent_id, parent_port = parent_ports[0]
                parent_columns = index.resolve_columns(parent_id, parent_port)
                if parent_columns is not None:
                    return _filter_resolved_columns(parent_columns, selected_columns)
            return _resolved_from_columns(selected_columns)

        if node.data.nodeType in (NodeType.API_INPUT, NodeType.DATA_INPUT):
            meta = index.source_metadata(node)
            if meta is None and node.data.nodeType == NodeType.API_INPUT and port is not None:
                meta = index.api_input_port_metadata(node, port)
            if meta is not None:
                return _resolved_from_source_metadata(meta)

        queue.extend(sorted(index.parent_ports(nid)))

    return None


def _resolve_target_column_names(
    graph: PipelineGraph,
    target_node_id: str,
    source: str,
) -> tuple[str, ...] | None:
    """Resolve target column names when config or parquet metadata exposes them."""
    resolved = _resolve_target_columns(graph, target_node_id, source)
    return resolved.columns if resolved is not None else None


def _edge_join_key_columns_on_path(
    graph: PipelineGraph,
    target_node_id: str,
    source: str,
    *,
    _index: _EstimateGraphIndex | None = None,
) -> frozenset[str]:
    """Return materialized edgeJoin key output columns needed upstream."""
    from haute._topo import ancestors

    index = _index or _EstimateGraphIndex.build(graph, source)
    path_ids = ancestors(target_node_id, list(index.pruned_edges), set(index.node_map)) | {
        target_node_id
    }

    join_keys: set[str] = set()
    for nid in sorted(path_ids):
        node = index.node_map.get(nid)
        if node is None or node.data.nodeType != NodeType.EDGE_JOIN:
            continue
        base_keys, joined_keys = edge_join_key_columns_by_role(node.data.config)
        join_keys.update(base_keys)
        roles = _edge_join_input_roles(node, index)
        if roles is None:
            join_keys.update(joined_keys)
            continue

        _, join_edge = roles
        resolved = index.resolve_columns(nid)
        join_resolved = index.resolve_columns(join_edge.source, join_edge.sourceHandle)
        if resolved is None or join_resolved is None:
            join_keys.update(joined_keys)
            continue

        joined_key_width_columns = {
            join_resolved.width_columns.get(column, column) for column in joined_keys
        }
        for column in resolved.columns:
            if resolved.width_columns.get(column, column) in joined_key_width_columns:
                join_keys.add(column)
    return frozenset(join_keys)


def _normalised_string_sequence(value: object) -> frozenset[str]:
    if not isinstance(value, Sequence) or isinstance(value, str | bytes):
        return frozenset()
    return frozenset(item for item in value if isinstance(item, str))


def _is_variable_width_arrow_type(arrow_type: str) -> bool:
    normalised = arrow_type.lower()
    return any(token in normalised for token in ("string", "utf8", "binary"))


def _source_column_base_widths(
    sources: tuple[_DetailedSourceMetadata, ...],
) -> dict[str, float]:
    widths: dict[str, float] = {}
    for meta in sources:
        for column, arrow_type in meta.columns.items():
            width = float(_BYTES_PER_COL)
            if meta.row_count > 0 and _is_variable_width_arrow_type(arrow_type):
                uncompressed_size = meta.column_uncompressed_size_bytes.get(column)
                if uncompressed_size is None and meta.column_count > 0:
                    uncompressed_size = int(meta.uncompressed_size_bytes / meta.column_count)
                if uncompressed_size is not None:
                    width = max(width, math.ceil(uncompressed_size / meta.row_count))
                expanded_widths = meta.column_expanded_width_bytes or {}
                expanded_width = expanded_widths.get(column)
                if expanded_width is not None:
                    width = max(width, math.ceil(expanded_width))
            width_key = meta.column_width_keys.get(column, column)
            widths[width_key] = max(widths.get(width_key, 0.0), width)
            widths[column] = max(widths.get(column, 0.0), width)
    return widths


def _estimate_base_bytes_per_row(
    n_columns: int,
    *,
    target_columns: tuple[str, ...] | None,
    target_width_columns: tuple[str, ...] | None,
    sources: tuple[_DetailedSourceMetadata, ...],
) -> float:
    if not target_columns:
        return float(n_columns * _BYTES_PER_COL)

    width_column_names = target_width_columns or target_columns
    if len(width_column_names) != len(target_columns):
        raise ValueError("target_width_columns must align with target_columns")

    widths = _source_column_base_widths(sources)
    return sum(widths.get(column, float(_BYTES_PER_COL)) for column in width_column_names)


def estimate_safe_training_rows(
    graph: PipelineGraph,
    target_node_id: str,
    build_node_fn: object | None = None,
    *,
    safety_factor: float = _RAM_SAFETY_FACTOR,
    preamble_ns: dict | None = None,
    source: str = "live",
) -> RamEstimate:
    """Estimate whether the full pipeline fits in RAM for training.

    1. Row count from the graph's proven target-cardinality upper bound.
    2. Column count resolved from the lazy plan at the training node
       (captures joins and transforms), minus excluded features.
    3. Peak memory estimate using the empirical 3× multiplier.

    Returns a :class:`RamEstimate` with the decision and warning message.
    """
    # Refusing a zero budget here beats flooring the safe-row calculation to
    # _MIN_SAFE_ROWS against capacity known to be empty; the shared validator
    # keeps this consumer's None/negative/zero semantics and remedies aligned
    # with admission's.
    available = require_positive_available_ram(available_ram_bytes())

    # ── 1. Source metadata and target row-cardinality proof ───────────
    estimate_index = _EstimateGraphIndex.build(graph, source)
    source_metadata = _detailed_ancestor_source_metadata(
        graph,
        target_node_id,
        source,
        _index=estimate_index,
    )
    source_cols = source_metadata.column_count
    cardinality = estimate_index.resolve_cardinality(target_node_id)
    if not cardinality.available:
        logger.info(
            "target_cardinality_unavailable",
            target=target_node_id,
            source=source,
            blocking_node_id=cardinality.blocking_node_id,
            reason=cardinality.unavailable_reason,
        )
        return RamEstimate(
            safe_row_limit=None,
            total_rows=None,
            estimated_bytes=0,
            available_bytes=available,
            bytes_per_row=0,
            was_downsampled=False,
            warning=None,
            probe_columns=0,
        )
    assert cardinality.output_rows is not None
    total_rows = cardinality.output_rows

    # ── 2. Column count at the training node ─────────────────────────
    # Walk backwards through the graph from the target and resolve the
    # canonical detailed target schema from selected_columns or source metadata.
    target_columns = _resolve_target_columns(
        graph,
        target_node_id,
        source,
        _index=estimate_index,
    )

    if target_columns is None or not target_columns.columns:
        logger.info(
            "schema_unavailable",
            target=target_node_id,
            source=source,
        )
        return RamEstimate(
            safe_row_limit=None,
            total_rows=total_rows,
            estimated_bytes=0,
            available_bytes=available,
            bytes_per_row=0,
            was_downsampled=False,
            warning=None,
            probe_columns=0,
        )

    # Subtract excluded features — the pipeline now projects before
    # sinking, so excluded columns never enter the split or pools.
    node_map = {n.id: n for n in graph.nodes}
    target_node = node_map.get(target_node_id)
    excluded = (
        _normalised_string_sequence(target_node.data.config.get("exclude", []))
        if target_node
        else frozenset()
    )
    join_keys_on_path = _edge_join_key_columns_on_path(
        graph,
        target_node_id,
        source,
        _index=estimate_index,
    )
    preserved_excluded_join_keys = excluded & join_keys_on_path
    peak_columns = _filter_resolved_columns(
        target_columns,
        (
            column
            for column in target_columns.columns
            if column not in excluded or column in preserved_excluded_join_keys
        ),
    )
    peak_column_names = peak_columns.columns
    peak_width_column_names = tuple(
        peak_columns.width_columns.get(column, column) for column in peak_column_names
    )
    n_columns = max(len(peak_column_names), 1)

    logger.info(
        "schema_resolved",
        source_cols=source_cols,
        target_cols=n_columns,
        excluded=len(excluded),
        preserved_join_keys=sorted(preserved_excluded_join_keys),
    )

    # ── 3. Peak estimate ────────────────────────────────────────────
    base_bytes_per_row = _estimate_base_bytes_per_row(
        n_columns,
        target_columns=peak_column_names,
        target_width_columns=peak_width_column_names,
        sources=source_metadata.sources,
    )
    peak_bytes = _estimate_peak_bytes(
        total_rows,
        n_columns,
        base_bytes_per_row=base_bytes_per_row,
    )
    usable_ram = int(available * safety_factor)
    bytes_per_row = peak_bytes / total_rows if total_rows > 0 else 0

    logger.info(
        "ram_estimate",
        total_rows=total_rows,
        n_columns=n_columns,
        bytes_per_row=round(bytes_per_row, 1),
        peak_mb=round(peak_bytes / 1024**2, 1),
        available_mb=round(available / 1024**2, 1),
        usable_mb=round(usable_ram / 1024**2, 1),
    )

    # ── 5. Decision ──────────────────────────────────────────────────
    if peak_bytes <= usable_ram:
        return RamEstimate(
            safe_row_limit=None,
            total_rows=total_rows,
            estimated_bytes=peak_bytes,
            available_bytes=available,
            bytes_per_row=bytes_per_row,
            was_downsampled=False,
            warning=None,
            probe_columns=n_columns,
        )

    peak_per_row = peak_bytes / total_rows
    safe_rows = int(usable_ram / peak_per_row)
    safe_rows = max(safe_rows, _MIN_SAFE_ROWS)

    warning = (
        f"Dataset downsampled to {safe_rows:,} of {total_rows:,} rows to fit in "
        f"available RAM ({available / 1024**3:.1f} GB). "
        f"Estimated peak training memory: {peak_bytes / 1024**3:.1f} GB."
    )
    logger.warning("downsampling", safe_rows=safe_rows, total_rows=total_rows, warning=warning)

    return RamEstimate(
        safe_row_limit=safe_rows,
        total_rows=total_rows,
        estimated_bytes=peak_bytes,
        available_bytes=available,
        bytes_per_row=bytes_per_row,
        was_downsampled=True,
        warning=warning,
        probe_columns=n_columns,
    )


def estimate_materialisation_boundaries(
    graph: PipelineGraph,
    target_node_ids: Iterable[str],
    *,
    source: str = "live",
    edge_demands: Mapping[ProjectionEdgeKey, frozenset[str] | None] | None = None,
) -> Iterator[tuple[str, MaterialisationEstimate]]:
    """Yield boundary estimates through one request-local metadata index.

    Results stay lazy so a caller that cannot proceed after an unavailable
    boundary does not probe unrelated later sources. Iterating more than one
    result still shares all graph, schema, and source-metadata memoisation.
    """

    estimate_index = _EstimateGraphIndex.build(graph, source)
    for target_node_id in target_node_ids:
        yield (
            target_node_id,
            _estimate_materialisation_boundary_from_index(
                graph,
                target_node_id,
                source=source,
                estimate_index=estimate_index,
                edge_demands=edge_demands,
            ),
        )


def _estimate_materialisation_boundary_from_index(
    graph: PipelineGraph,
    target_node_id: str,
    *,
    source: str,
    estimate_index: _EstimateGraphIndex,
    edge_demands: Mapping[ProjectionEdgeKey, frozenset[str] | None] | None,
) -> MaterialisationEstimate:
    """Estimate one boundary using an already prepared request-local index."""

    source_metadata = _detailed_ancestor_source_metadata(
        graph,
        target_node_id,
        source,
        _index=estimate_index,
    )
    cardinality = estimate_index.resolve_cardinality(target_node_id)
    if not cardinality.available:
        assert cardinality.unavailable_reason is not None
        if cardinality.unavailable_reason == "source_row_count_unavailable":
            return MaterialisationEstimate.unavailable("source_row_count_unavailable")
        blocking_node_id = cardinality.blocking_node_id or target_node_id
        return MaterialisationEstimate.unavailable(
            f"row_cardinality_unavailable:{blocking_node_id}:{cardinality.unavailable_reason}"
        )
    assert cardinality.output_rows is not None and cardinality.peak_rows is not None
    total_rows = cardinality.peak_rows
    cardinality_assumptions = cardinality.evidence + (
        f"cardinality_output_upper_bound={cardinality.output_rows}",
        f"cardinality_peak_upper_bound={cardinality.peak_rows}",
    )
    incoming_edges = tuple(
        edge
        for edge in estimate_index.pruned_edges
        if edge.target == target_node_id and edge.source in estimate_index.node_map
    )
    resolved_inputs = tuple(
        (
            edge,
            estimate_index.resolve_columns(edge.source, edge.sourceHandle),
        )
        for edge in incoming_edges
    )
    if total_rows == 0:
        exact_zero_width_proof = (
            bool(incoming_edges)
            and edge_demands is not None
            and all(
                ProjectionEdgeKey.from_edge(edge) in edge_demands
                and edge_demands[ProjectionEdgeKey.from_edge(edge)] is not None
                and resolved is not None
                and bool(resolved.columns)
                and cast(
                    frozenset[str],
                    edge_demands[ProjectionEdgeKey.from_edge(edge)],
                ).issubset(resolved.columns)
                for edge, resolved in resolved_inputs
            )
        )
        return MaterialisationEstimate.available(
            0,
            assumptions=cardinality_assumptions,
            basis=(
                MaterialisationEstimateBasis.PROJECTED_COLUMNS
                if exact_zero_width_proof
                else MaterialisationEstimateBasis.COMPLETE_WIDTH_FALLBACK
            ),
        )

    if not incoming_edges:
        resolved_columns = _resolve_target_columns(
            graph,
            target_node_id,
            source,
            _index=estimate_index,
        )
        if resolved_columns is None or not resolved_columns.columns:
            return MaterialisationEstimate.unavailable("target_schema_unavailable")
        column_names = resolved_columns.columns
        width_column_names = tuple(
            resolved_columns.width_columns.get(column, column) for column in column_names
        )
        n_columns = len(column_names)
        basis = MaterialisationEstimateBasis.COMPLETE_WIDTH_FALLBACK
        projection_assumptions: tuple[str, ...] = ()
    else:
        exact_demands = edge_demands is not None and all(
            ProjectionEdgeKey.from_edge(edge) in edge_demands
            and edge_demands[ProjectionEdgeKey.from_edge(edge)] is not None
            for edge in incoming_edges
        )
        fallback_reason: str | None = None
        if exact_demands:
            for edge, resolved in resolved_inputs:
                assert edge_demands is not None
                demand = edge_demands[ProjectionEdgeKey.from_edge(edge)]
                if resolved is None or not resolved.columns:
                    fallback_reason = "complete_schema_unavailable"
                    break
                assert demand is not None
                if not demand.issubset(resolved.columns):
                    fallback_reason = "demanded_column_unmapped"
                    break

        if exact_demands and fallback_reason is None:
            selected: list[tuple[str, str]] = []
            carriers = 0
            for edge, resolved in resolved_inputs:
                assert edge_demands is not None and resolved is not None
                demand = edge_demands[ProjectionEdgeKey.from_edge(edge)]
                assert demand is not None
                names = tuple(column for column in resolved.columns if column in demand)
                if not names:
                    names = (resolved.columns[0],)
                    carriers += 1
                selected.extend((name, resolved.width_columns.get(name, name)) for name in names)
            column_names = tuple(name for name, _width in selected)
            width_column_names = tuple(width for _name, width in selected)
            n_columns = len(selected)
            basis = MaterialisationEstimateBasis.PROJECTED_COLUMNS
            projection_assumptions = (f"projected_column_count={n_columns}",) + (
                (f"cardinality_carrier_columns={carriers}",) if carriers else ()
            )
        else:
            if any(resolved is None or not resolved.columns for _edge, resolved in resolved_inputs):
                return MaterialisationEstimate.unavailable("target_schema_unavailable")
            complete: list[tuple[str, str]] = []
            for _edge, resolved in resolved_inputs:
                assert resolved is not None
                complete.extend(
                    (name, resolved.width_columns.get(name, name)) for name in resolved.columns
                )
            column_names = tuple(name for name, _width in complete)
            width_column_names = tuple(width for _name, width in complete)
            n_columns = len(complete)
            basis = MaterialisationEstimateBasis.COMPLETE_WIDTH_FALLBACK
            projection_assumptions = (
                (f"projection_fallback_reason={fallback_reason}",)
                if fallback_reason == "demanded_column_unmapped"
                else ()
            )

    base_bytes_per_row = _estimate_base_bytes_per_row(
        n_columns,
        target_columns=column_names,
        target_width_columns=width_column_names,
        sources=source_metadata.sources,
    )
    return MaterialisationEstimate.available(
        _estimate_peak_bytes(
            total_rows,
            n_columns,
            base_bytes_per_row=base_bytes_per_row,
        ),
        assumptions=(
            *cardinality_assumptions,
            f"full-boundary overhead multiplier={_OVERHEAD_MULTIPLIER:g}",
        )
        + projection_assumptions,
        basis=basis,
    )
