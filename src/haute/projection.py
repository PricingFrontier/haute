"""Shared projection planning facade.

This module is the stable boundary for code that needs column projection
planning.  Routes and deploy callers should use this shared surface rather
than reaching into executor internals.
"""

from __future__ import annotations

import ast
import heapq
from collections.abc import Callable, Iterable, Iterator, Mapping
from dataclasses import dataclass, field, replace
from enum import StrEnum
from types import MappingProxyType
from typing import Any, Generic, Literal, NamedTuple, TypeVar, cast

from haute._cache import canonical_json
from haute._column_lineage import ColumnLineageAnalysis, analyze_polars_lineage
from haute._contracts import Contract, get_column_contract
from haute._edge_join import (
    build_edge_join_kwargs,
    edge_join_key_columns_by_role,
    narrow_join_parent_demand,
    resolve_edge_join_role_indices,
)
from haute._estimate_calibration import (
    CALIBRATION_BASE_BASIS_POINTS,
    CALIBRATION_MAX_BASIS_POINTS,
)
from haute._execution_context import ExecutionProfile
from haute._graph_utils import _sanitize_func_name, build_parents_of, edge_input_name
from haute._polars_operations import (
    EXPRESSION_NAMESPACE_NAMES,
    OperationReceiver,
    materialising_expression_methods,
    materialising_frame_methods,
    registered_names,
)
from haute._polars_selectors import preamble_selector_aliases
from haute._topo import ancestors, topo_sort_ids
from haute._types import GraphEdge, GraphNode, NodeType, PipelineGraph
from haute.errors import ContractMismatchError

__all__ = [
    "AllExcept",
    "AllExceptColumns",
    "ProjectionDiagnostics",
    "ProjectionEdgeKey",
    "ProjectionPlan",
    "ProjectionRuleCoverage",
    "ProjectionRequest",
    "ProjectionReason",
    "SourceScanProjection",
    "UNPROJECTED_STREAMING_BOUNDARY_RULE_NAME",
    "api_input_port_columns_by_node",
    "builder_required_output_columns_by_node",
    "has_configured_column_renames",
    "explain",
    "model_score_required_output_columns",
    "plan",
    "projection_rule_coverage_by_node_type",
    "ratebook_factor_required_columns",
    "with_runtime_inferred_streaming_edges",
    "simple_join_calls_for_parent_inputs",
    "source_scan_projection",
    "materialising_operator_sequences_by_input_names",
    "materialising_operator_sequences_by_node",
    "materialising_operators_by_input_names",
    "materialising_operators_by_node",
    "source_user_code_preserves_column_projection",
    "source_user_code_scan_columns",
    "validate_projection_rule_coverage",
    "with_api_input_port_projection_boundaries",
]


class ExecutionStrategy(StrEnum):
    """Closed V1 execution-strategy vocabulary."""

    PROJECTED = "projected"
    SCHEMA_ALL_EXCEPT = "schema-all-except"
    FULL_WIDTH_ADMITTED_EAGER = "full-width-admitted-eager"
    UNPROJECTED_STREAMING_BOUNDARY = "unprojected-streaming-boundary"
    MATERIALISATION_BOUNDARY = "materialisation-boundary"
    FULL_WIDTH_CONSERVATIVE = "full-width-conservative"
    UNSUPPORTED = "unsupported"
    NOT_PLANNED = "not-planned"


class ExecutionStrategyStatus(StrEnum):
    PROJECTED = "projected"
    ADMITTED_EAGER = "admitted_eager"
    BOUNDARY = "boundary"
    WARNED = "warned"
    REJECTED = "rejected"
    NOT_PLANNED = "not_planned"


class ExecutionBoundedness(StrEnum):
    BOUNDED = "bounded"
    UNBOUNDED = "unbounded"
    UNKNOWN = "unknown"


class DiagnosticDetailState(StrEnum):
    AVAILABLE = "available"
    UNAVAILABLE = "unavailable"
    TRUNCATED = "truncated"


_STATUS_BY_STRATEGY: Mapping[ExecutionStrategy, ExecutionStrategyStatus] = MappingProxyType(
    {
        ExecutionStrategy.PROJECTED: ExecutionStrategyStatus.PROJECTED,
        ExecutionStrategy.SCHEMA_ALL_EXCEPT: ExecutionStrategyStatus.PROJECTED,
        ExecutionStrategy.FULL_WIDTH_ADMITTED_EAGER: ExecutionStrategyStatus.ADMITTED_EAGER,
        ExecutionStrategy.UNPROJECTED_STREAMING_BOUNDARY: ExecutionStrategyStatus.BOUNDARY,
        ExecutionStrategy.MATERIALISATION_BOUNDARY: ExecutionStrategyStatus.BOUNDARY,
        ExecutionStrategy.FULL_WIDTH_CONSERVATIVE: ExecutionStrategyStatus.WARNED,
        ExecutionStrategy.UNSUPPORTED: ExecutionStrategyStatus.REJECTED,
        ExecutionStrategy.NOT_PLANNED: ExecutionStrategyStatus.NOT_PLANNED,
    }
)

_DETAIL_STATE_PRECEDENCE = {
    DiagnosticDetailState.AVAILABLE: 0,
    DiagnosticDetailState.UNAVAILABLE: 1,
    DiagnosticDetailState.TRUNCATED: 2,
}
_DIAGNOSTIC_MESSAGE_LIMIT = 512
_BOUNDARY_REASON_CAP = 32
_PROVENANCE_CAP = 128
_MAX_TOPOLOGICAL_RANK = 2**63 - 1


def _bounded_item_primary_key(item: Mapping[str, Any], kind: str) -> tuple[Any, ...]:
    if kind == "boundaries":
        return (
            item["topological_rank"],
            item["node_id"],
            item["operator"],
            item["boundary_kind"],
        )
    if kind == "reasons":
        rank = item.get("topological_rank")
        return (
            _MAX_TOPOLOGICAL_RANK if rank is None else rank,
            item.get("node_id") or "",
            item["reason_code"],
            item.get("operator") or "",
        )
    if kind == "provenance":
        return (
            item["column"],
            item["origin_kind"],
            item.get("source_node_id") or "",
            item.get("source_column") or "",
        )
    raise ValueError(f"unknown diagnostic collection kind: {kind!r}")


def _bounded_item(item: Mapping[str, Any]) -> Mapping[str, Any]:
    copied = dict(item)
    for field_name in ("message", "remediation"):
        value = copied.get(field_name)
        if isinstance(value, str):
            copied[field_name] = value[:_DIAGNOSTIC_MESSAGE_LIMIT]
    canonical_json(copied)
    return MappingProxyType(copied)


@dataclass(frozen=True)
class BoundedDiagnosticCollection:
    """A complete, deterministically truncated, or unavailable V1 detail list."""

    state: DiagnosticDetailState
    total_count: int | None
    items: tuple[Mapping[str, Any], ...]

    def __post_init__(self) -> None:
        if self.state is DiagnosticDetailState.UNAVAILABLE:
            if self.total_count is not None or self.items:
                raise ValueError("an unavailable collection has null count and no items")
            return
        if (
            not isinstance(self.total_count, int)
            or isinstance(self.total_count, bool)
            or self.total_count < 0
        ):
            raise ValueError("an available/truncated collection requires a non-negative count")
        if self.state is DiagnosticDetailState.AVAILABLE:
            if self.total_count != len(self.items):
                raise ValueError("an available collection count must equal its item count")
        elif self.total_count <= len(self.items):
            raise ValueError("a truncated collection count must exceed its retained item count")

    @classmethod
    def available(cls, items: Iterable[Mapping[str, Any]]) -> BoundedDiagnosticCollection:
        copied = tuple(_bounded_item(item) for item in items)
        return cls(DiagnosticDetailState.AVAILABLE, len(copied), copied)

    @classmethod
    def unavailable(cls) -> BoundedDiagnosticCollection:
        return cls(DiagnosticDetailState.UNAVAILABLE, None, ())

    @classmethod
    def from_items(
        cls,
        items: Iterable[Mapping[str, Any]],
        *,
        cap: int,
        sort_key: str,
        retain_one_by: str | None = None,
    ) -> BoundedDiagnosticCollection:
        if not isinstance(cap, int) or isinstance(cap, bool) or cap < 0:
            raise ValueError("diagnostic collection cap must be a non-negative integer")
        if retain_one_by is not None and (not isinstance(retain_one_by, str) or not retain_one_by):
            raise ValueError("retain_one_by must be a non-empty field name")
        copied = [_bounded_item(item) for item in items]
        copied.sort(
            key=lambda item: (
                _bounded_item_primary_key(item, sort_key),
                canonical_json(item),
            )
        )
        total_count = len(copied)
        if total_count <= cap or retain_one_by is None:
            retained = tuple(copied[:cap])
        else:
            representative_indexes: dict[str, int] = {}
            for index, item in enumerate(copied):
                if retain_one_by not in item:
                    raise ValueError(f"diagnostic item is missing grouping field {retain_one_by!r}")
                representative_indexes.setdefault(
                    canonical_json(item[retain_one_by]),
                    index,
                )
            if len(representative_indexes) > cap:
                raise ValueError(
                    "diagnostic collection cap is smaller than the number of retained groups"
                )
            retained_indexes = set(representative_indexes.values())
            remaining_slots = cap - len(retained_indexes)
            retained_indexes.update(
                [index for index in range(total_count) if index not in retained_indexes][
                    :remaining_slots
                ]
            )
            retained = tuple(copied[index] for index in sorted(retained_indexes))
        if total_count > len(retained):
            return cls(DiagnosticDetailState.TRUNCATED, total_count, retained)
        return cls(DiagnosticDetailState.AVAILABLE, total_count, retained)

    def to_dict(self) -> dict[str, Any]:
        return {
            "state": self.state.value,
            "total_count": self.total_count,
            "items": [dict(item) for item in self.items],
        }


@dataclass(frozen=True)
class ExecutionStrategyDiagnostic:
    """Versioned JSON-safe strategy diagnostic produced by the shared planner."""

    schema_version: int
    status: ExecutionStrategyStatus
    strategy: ExecutionStrategy
    profile: str
    boundedness: ExecutionBoundedness
    reason_code: str
    detail_state: DiagnosticDetailState
    boundaries: BoundedDiagnosticCollection
    reasons: BoundedDiagnosticCollection
    provenance: BoundedDiagnosticCollection
    blocking_node_id: str | None = None
    blocking_operator: str | None = None
    remediation: str | None = None
    estimated_peak_bytes: int | None = None
    raw_estimated_peak_bytes: int | None = None
    estimate_calibration_factor_basis_points: int | None = None
    estimate_admission_basis: str | None = None
    headroom_bytes: int | None = None
    assumptions: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if self.schema_version != 1:
            raise ValueError("the strategy producer supports schema_version=1 only")
        if self.status is not _STATUS_BY_STRATEGY[self.strategy]:
            raise ValueError("strategy status does not match the V1 mapping")
        expected_detail_state = max(
            (self.boundaries.state, self.reasons.state, self.provenance.state),
            key=_DETAIL_STATE_PRECEDENCE.__getitem__,
        )
        if self.detail_state is not expected_detail_state:
            raise ValueError("detail_state must be the worst bounded collection state")
        if len(self.remediation or "") > _DIAGNOSTIC_MESSAGE_LIMIT:
            raise ValueError("remediation exceeds the V1 512-character cap")
        for name, value in (
            ("estimated_peak_bytes", self.estimated_peak_bytes),
            ("raw_estimated_peak_bytes", self.raw_estimated_peak_bytes),
            (
                "estimate_calibration_factor_basis_points",
                self.estimate_calibration_factor_basis_points,
            ),
        ):
            if value is not None and (
                not isinstance(value, int) or isinstance(value, bool) or value < 0
            ):
                raise ValueError(f"{name} must be a non-negative integer or None")
        if self.estimate_admission_basis not in {
            None,
            "provided",
            "projected_columns",
            "complete_width_fallback",
        }:
            raise ValueError("estimate_admission_basis is not a supported V1 value")
        calibration_values = (
            self.raw_estimated_peak_bytes,
            self.estimate_calibration_factor_basis_points,
            self.estimate_admission_basis,
        )
        if any(value is not None for value in calibration_values):
            if self.estimated_peak_bytes is None or any(
                value is None for value in calibration_values
            ):
                raise ValueError(
                    "calibrated estimate evidence requires estimated/raw bytes, "
                    "factor, and admission basis together"
                )
            assert self.raw_estimated_peak_bytes is not None
            assert self.estimate_calibration_factor_basis_points is not None
            if self.estimate_calibration_factor_basis_points < CALIBRATION_BASE_BASIS_POINTS:
                raise ValueError("estimate calibration factor cannot reduce an estimate")
            if self.estimate_calibration_factor_basis_points > CALIBRATION_MAX_BASIS_POINTS:
                raise ValueError("estimate calibration factor exceeds the supported cap")
            expected = (
                self.raw_estimated_peak_bytes * self.estimate_calibration_factor_basis_points
                + CALIBRATION_BASE_BASIS_POINTS
                - 1
            ) // CALIBRATION_BASE_BASIS_POINTS
            if self.estimated_peak_bytes != expected:
                raise ValueError("calibrated estimate bytes do not match raw bytes and factor")

    @classmethod
    def create(
        cls,
        *,
        strategy: ExecutionStrategy,
        profile: ExecutionProfile | str,
        boundedness: ExecutionBoundedness,
        reason_code: str,
        boundaries: BoundedDiagnosticCollection,
        reasons: BoundedDiagnosticCollection,
        provenance: BoundedDiagnosticCollection,
        blocking_node_id: str | None = None,
        blocking_operator: str | None = None,
        remediation: str | None = None,
        estimated_peak_bytes: int | None = None,
        raw_estimated_peak_bytes: int | None = None,
        estimate_calibration_factor_basis_points: int | None = None,
        estimate_admission_basis: str | None = None,
        headroom_bytes: int | None = None,
        assumptions: Iterable[str] = (),
    ) -> ExecutionStrategyDiagnostic:
        detail_state = max(
            (boundaries.state, reasons.state, provenance.state),
            key=_DETAIL_STATE_PRECEDENCE.__getitem__,
        )
        profile_name = profile.value if isinstance(profile, ExecutionProfile) else profile
        return cls(
            schema_version=1,
            status=_STATUS_BY_STRATEGY[strategy],
            strategy=strategy,
            profile=profile_name,
            boundedness=boundedness,
            reason_code=reason_code,
            detail_state=detail_state,
            boundaries=boundaries,
            reasons=reasons,
            provenance=provenance,
            blocking_node_id=blocking_node_id,
            blocking_operator=blocking_operator,
            remediation=(remediation[:_DIAGNOSTIC_MESSAGE_LIMIT] if remediation else None),
            estimated_peak_bytes=estimated_peak_bytes,
            raw_estimated_peak_bytes=raw_estimated_peak_bytes,
            estimate_calibration_factor_basis_points=(estimate_calibration_factor_basis_points),
            estimate_admission_basis=estimate_admission_basis,
            headroom_bytes=headroom_bytes,
            assumptions=tuple(str(item) for item in assumptions),
        )

    def to_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "schema_version": self.schema_version,
            "status": self.status.value,
            "strategy": self.strategy.value,
            "profile": self.profile,
            "boundedness": self.boundedness.value,
            "reason_code": self.reason_code,
            "detail_state": self.detail_state.value,
            "boundaries": self.boundaries.to_dict(),
            "reasons": self.reasons.to_dict(),
            "provenance": self.provenance.to_dict(),
        }
        optional = {
            "blocking_node_id": self.blocking_node_id,
            "blocking_operator": self.blocking_operator,
            "remediation": self.remediation,
            "estimated_peak_bytes": self.estimated_peak_bytes,
            "raw_estimated_peak_bytes": self.raw_estimated_peak_bytes,
            "estimate_calibration_factor_basis_points": (
                self.estimate_calibration_factor_basis_points
            ),
            "estimate_admission_basis": self.estimate_admission_basis,
            "headroom_bytes": self.headroom_bytes,
        }
        payload.update({key: value for key, value in optional.items() if value is not None})
        if self.assumptions:
            payload["assumptions"] = list(self.assumptions)
        canonical_json(payload)
        return payload


@dataclass(frozen=True)
class ProjectionRequest:
    """Inputs required to compute a projection plan for a graph target."""

    graph: PipelineGraph
    target_node_id: str | None
    profile: ExecutionProfile
    required_columns_by_node: Mapping[str, Iterable[str] | AllExceptColumns] | None = None
    source: str = "live"


@dataclass(frozen=True)
class ProjectionReason:
    """Provenance for a node or edge projection demand."""

    rule: str
    message: str
    details: Mapping[str, Any] = field(default_factory=lambda: MappingProxyType({}))

    def to_dict(self) -> dict[str, Any]:
        return {
            "rule": self.rule,
            "message": self.message,
            "details": dict(self.details),
        }


@dataclass(frozen=True, slots=True)
class ProjectionEdgeKey:
    """Complete immutable identity for one graph edge in a projection plan.

    A source/target pair is insufficient: one multi-frame source may connect
    several ports to the same target.  The persisted id and every visible or
    retained port field participate so those demands cannot overwrite one
    another.
    """

    edge_id: str
    source: str
    target: str
    source_handle: str | None = None
    target_handle: str | None = None
    source_port: str | None = None
    target_port: str | None = None

    @classmethod
    def from_edge(cls, edge: GraphEdge) -> ProjectionEdgeKey:
        return cls(
            edge_id=edge.id,
            source=edge.source,
            target=edge.target,
            source_handle=edge.sourceHandle,
            target_handle=edge.targetHandle,
            source_port=edge.sourcePort,
            target_port=edge.targetPort,
        )

    def sort_key(self) -> tuple[str, ...]:
        return (
            self.source,
            self.target,
            self.source_handle or "",
            self.target_handle or "",
            self.source_port or "",
            self.target_port or "",
            self.edge_id,
        )

    def to_dict(self) -> dict[str, str | None]:
        return {
            "edge_id": self.edge_id,
            "source": self.source,
            "target": self.target,
            "source_handle": self.source_handle,
            "target_handle": self.target_handle,
            "source_port": self.source_port,
            "target_port": self.target_port,
        }


_EdgeValue = TypeVar("_EdgeValue")


class _EdgeIdentityMapping(Mapping[ProjectionEdgeKey, _EdgeValue], Generic[_EdgeValue]):
    """Immutable edge-key map addressed only by complete edge identities.

    A ``(source, target)`` pair is lossy — one multi-frame source can connect
    several ports to the same target — so every lookup and construction key
    must be a complete :class:`ProjectionEdgeKey`.
    """

    def __init__(
        self,
        values: Mapping[Any, _EdgeValue] = MappingProxyType({}),
    ) -> None:
        normalised: dict[ProjectionEdgeKey, _EdgeValue] = {}
        for raw_key, value in values.items():
            if not isinstance(raw_key, ProjectionEdgeKey):
                raise TypeError("projection edge mappings require complete edge keys")
            normalised[raw_key] = value
        self._values = MappingProxyType(normalised)

    def __getitem__(self, key: ProjectionEdgeKey) -> _EdgeValue:
        if isinstance(key, ProjectionEdgeKey):
            return self._values[key]
        raise KeyError(key)

    def __iter__(self) -> Iterator[ProjectionEdgeKey]:
        return iter(self._values)

    def __len__(self) -> int:
        return len(self._values)


@dataclass(frozen=True)
class ProjectionDiagnostics:
    """Lightweight diagnostics attached to a shared projection plan."""

    opaque_reasons: Mapping[str, ProjectionReason] = field(
        default_factory=lambda: MappingProxyType({})
    )
    node_reasons: Mapping[str, ProjectionReason] = field(
        default_factory=lambda: MappingProxyType({})
    )
    edge_reasons: Mapping[ProjectionEdgeKey, ProjectionReason] = field(
        default_factory=_EdgeIdentityMapping
    )

    def __post_init__(self) -> None:
        if not isinstance(self.edge_reasons, _EdgeIdentityMapping):
            object.__setattr__(self, "edge_reasons", _EdgeIdentityMapping(self.edge_reasons))

    def to_dict(self) -> dict[str, Any]:
        return {
            "opaque_reasons": {
                node_id: reason.to_dict() for node_id, reason in sorted(self.opaque_reasons.items())
            },
            "node_reasons": {
                node_id: reason.to_dict() for node_id, reason in sorted(self.node_reasons.items())
            },
            "edge_reasons": self._edge_reasons_payload(),
        }

    def _edge_reasons_payload(self) -> dict[str, dict[str, Any]]:
        pair_counts: dict[tuple[str, str], int] = {}
        for key in self.edge_reasons:
            pair = (key.source, key.target)
            pair_counts[pair] = pair_counts.get(pair, 0) + 1
        payload: dict[str, dict[str, Any]] = {}
        for key, reason in sorted(
            self.edge_reasons.items(),
            key=lambda item: item[0].sort_key(),
        ):
            if pair_counts[(key.source, key.target)] == 1:
                label = f"{key.source}->{key.target}"
            else:
                label = canonical_json(key.to_dict())
            payload[label] = reason.to_dict()
        return payload


_MAX_STRATEGY_SUMMARY_NODES = 100


@dataclass(frozen=True)
class ProjectionPlan:
    """Column projection needs at nodes and parent-specific fan-in edges."""

    needed_by_node: Mapping[str, frozenset[str] | None]
    edge_demands: Mapping[ProjectionEdgeKey, frozenset[str] | None]
    materialisation_boundaries: frozenset[str] = frozenset()
    opaque_boundaries: frozenset[str] = frozenset()
    diagnostics: ProjectionDiagnostics = field(default_factory=ProjectionDiagnostics)

    def __post_init__(self) -> None:
        if not isinstance(self.edge_demands, _EdgeIdentityMapping):
            object.__setattr__(self, "edge_demands", _EdgeIdentityMapping(self.edge_demands))

    def demand_for_edge(self, edge: GraphEdge) -> frozenset[str] | None:
        """Return the demand for exactly *edge*, or ``None`` when absent/opaque."""
        return self.edge_demands.get(ProjectionEdgeKey.from_edge(edge))

    def reason_for_edge(self, edge: GraphEdge) -> ProjectionReason | None:
        """Return the diagnostic reason for exactly *edge*."""
        return self.diagnostics.edge_reasons.get(ProjectionEdgeKey.from_edge(edge))

    def _node_strategy(self, node_id: str) -> str:
        reason = self.diagnostics.node_reasons.get(
            node_id,
            self.diagnostics.opaque_reasons.get(node_id),
        )
        if reason is not None and reason.rule == "schema_all_except":
            return "schema_all_except"
        if node_id in self.materialisation_boundaries:
            return "materialisation_boundary"
        if node_id in self.opaque_boundaries:
            return "unprojected_streaming_boundary"
        if self.needed_by_node.get(node_id) is None:
            return "full_width"
        return "projected"

    def strategy_summary_payload(
        self,
        *,
        profile: str | None = None,
        max_nodes: int = _MAX_STRATEGY_SUMMARY_NODES,
    ) -> dict[str, Any]:
        """Return a bounded summary of execution-planning decisions.

        The full diagnostics keep rule messages available for debugging.  This
        summary is deliberately compact so route payloads can expose the
        strategy shape without shipping large column sets to the UI.
        """
        node_ids = sorted(
            set(self.needed_by_node)
            | set(self.materialisation_boundaries)
            | set(self.opaque_boundaries)
            | set(self.diagnostics.node_reasons)
            | set(self.diagnostics.opaque_reasons)
        )
        counts: dict[str, int] = {}
        strategies: list[dict[str, str | None]] = []
        for node_id in node_ids:
            strategy = self._node_strategy(node_id)
            counts[strategy] = counts.get(strategy, 0) + 1
            if len(strategies) < max_nodes:
                reason = self.diagnostics.node_reasons.get(
                    node_id,
                    self.diagnostics.opaque_reasons.get(node_id),
                )
                strategies.append(
                    {
                        "node_id": node_id,
                        "strategy": strategy,
                        "reason_rule": reason.rule if reason is not None else None,
                    }
                )

        retained = len(strategies)
        total = len(node_ids)
        return {
            "profile": profile,
            "node_strategy_counts": counts,
            "opaque_boundary_count": len(self.opaque_boundaries),
            "materialisation_boundary_count": len(self.materialisation_boundaries),
            "node_strategy_count": total,
            "retained_node_strategy_count": retained,
            "truncated_node_strategy_count": max(0, total - retained),
            "node_strategies_truncated": retained < total,
            "node_strategies": strategies,
        }

    def diagnostics_payload(self, *, profile: str | None = None) -> dict[str, Any]:
        payload = self.diagnostics.to_dict()
        payload["strategy_summary"] = self.strategy_summary_payload(profile=profile)
        return payload


@dataclass(frozen=True)
class ExecutionStrategyResult:
    """Facade result pairing the internal projection plan with its V1 diagnostic."""

    projection_plan: ProjectionPlan
    diagnostic: ExecutionStrategyDiagnostic

    @property
    def schema_version(self) -> int:
        return self.diagnostic.schema_version

    @property
    def status(self) -> ExecutionStrategyStatus:
        return self.diagnostic.status

    @property
    def strategy(self) -> ExecutionStrategy:
        return self.diagnostic.strategy

    @property
    def profile(self) -> str:
        return self.diagnostic.profile

    @property
    def boundedness(self) -> ExecutionBoundedness:
        return self.diagnostic.boundedness

    @property
    def reason_code(self) -> str:
        return self.diagnostic.reason_code

    @property
    def detail_state(self) -> DiagnosticDetailState:
        return self.diagnostic.detail_state

    @property
    def needed_by_node(self) -> Mapping[str, frozenset[str] | None]:
        return self.projection_plan.needed_by_node

    @property
    def edge_demands(self) -> Mapping[ProjectionEdgeKey, frozenset[str] | None]:
        return self.projection_plan.edge_demands

    @property
    def materialisation_boundaries(self) -> frozenset[str]:
        return self.projection_plan.materialisation_boundaries

    @property
    def opaque_boundaries(self) -> frozenset[str]:
        return self.projection_plan.opaque_boundaries

    @property
    def diagnostics(self) -> ProjectionDiagnostics:
        return self.projection_plan.diagnostics


def build_execution_strategy_result(
    projection_plan: ProjectionPlan,
    *,
    profile: ExecutionProfile,
    order: Iterable[str],
    children_of: Mapping[str, Iterable[str]],
    node_map: Mapping[str, GraphNode],
    has_projection_seed: bool,
    required_columns_by_node: Mapping[str, Iterable[str] | AllExceptColumns] | None = None,
    strategy: ExecutionStrategy | None = None,
    reason_code: str | None = None,
    boundary_operators: Mapping[str, str] | None = None,
    remediation: str | None = None,
    estimated_peak_bytes: int | None = None,
    raw_estimated_peak_bytes: int | None = None,
    estimate_calibration_factor_basis_points: int | None = None,
    estimate_admission_basis: str | None = None,
    headroom_bytes: int | None = None,
    assumptions: Iterable[str] = (),
) -> ExecutionStrategyResult:
    """Build the deterministic V1 diagnostic for one projection plan."""
    canonical_order = tuple(order)
    ranks = _canonical_topological_ranks(canonical_order, children_of)
    schema_all_except = any(
        reason.rule == "schema_all_except"
        for reason in projection_plan.diagnostics.node_reasons.values()
    )
    if strategy is None:
        if not canonical_order:
            strategy = ExecutionStrategy.NOT_PLANNED
        elif projection_plan.materialisation_boundaries:
            strategy = ExecutionStrategy.MATERIALISATION_BOUNDARY
        elif schema_all_except:
            strategy = ExecutionStrategy.SCHEMA_ALL_EXCEPT
        elif projection_plan.opaque_boundaries:
            strategy = (
                ExecutionStrategy.FULL_WIDTH_ADMITTED_EAGER
                if profile in {ExecutionProfile.PREVIEW_EAGER, ExecutionProfile.DEPLOY_LIVE}
                and not has_projection_seed
                else ExecutionStrategy.UNPROJECTED_STREAMING_BOUNDARY
            )
        else:
            strategy = ExecutionStrategy.PROJECTED

    if reason_code is None:
        reason_code = {
            ExecutionStrategy.PROJECTED: "projection_available",
            ExecutionStrategy.SCHEMA_ALL_EXCEPT: "schema_all_except",
            ExecutionStrategy.FULL_WIDTH_ADMITTED_EAGER: "full_width_admitted",
            ExecutionStrategy.UNPROJECTED_STREAMING_BOUNDARY: ("unprojected_streaming_boundary"),
            ExecutionStrategy.MATERIALISATION_BOUNDARY: "materialisation_admitted",
            ExecutionStrategy.FULL_WIDTH_CONSERVATIVE: (
                "materialisation_estimate_unavailable_conservative"
            ),
            ExecutionStrategy.UNSUPPORTED: "unsupported",
            ExecutionStrategy.NOT_PLANNED: "not_planned",
        }[strategy]

    if remediation is None:
        remediation = {
            ExecutionStrategy.PROJECTED: (
                "No change is needed; the requested columns are projected through the graph."
            ),
            ExecutionStrategy.SCHEMA_ALL_EXCEPT: (
                "Review the training exclusions if a narrower feature projection is required."
            ),
            ExecutionStrategy.FULL_WIDTH_ADMITTED_EAGER: (
                "Select required columns or add column contracts to avoid "
                "full-width eager execution."
            ),
            ExecutionStrategy.UNPROJECTED_STREAMING_BOUNDARY: (
                "Add an explicit column contract or narrow the requested output "
                "at the blocking node."
            ),
            ExecutionStrategy.MATERIALISATION_BOUNDARY: (
                "Keep the materialisation within the reported memory headroom or narrow its input."
            ),
            ExecutionStrategy.FULL_WIDTH_CONSERVATIVE: (
                "The run continued under its full reserved memory envelope because the "
                "materialisation estimate was unavailable. Provide readable source metadata "
                "or rewrite the blocking operator so Haute can prove the estimate."
            ),
            ExecutionStrategy.UNSUPPORTED: (
                "Narrow the input or remove the unsupported operator before running this profile."
            ),
            ExecutionStrategy.NOT_PLANNED: (
                "Provide an executable target so Haute can plan the run."
            ),
        }[strategy]

    boundedness = {
        ExecutionStrategy.PROJECTED: ExecutionBoundedness.BOUNDED,
        ExecutionStrategy.SCHEMA_ALL_EXCEPT: ExecutionBoundedness.BOUNDED,
        ExecutionStrategy.UNPROJECTED_STREAMING_BOUNDARY: ExecutionBoundedness.BOUNDED,
        ExecutionStrategy.FULL_WIDTH_ADMITTED_EAGER: ExecutionBoundedness.UNBOUNDED,
        ExecutionStrategy.MATERIALISATION_BOUNDARY: ExecutionBoundedness.UNBOUNDED,
        ExecutionStrategy.FULL_WIDTH_CONSERVATIVE: ExecutionBoundedness.UNBOUNDED,
        ExecutionStrategy.UNSUPPORTED: ExecutionBoundedness.UNKNOWN,
        ExecutionStrategy.NOT_PLANNED: ExecutionBoundedness.UNKNOWN,
    }[strategy]

    operator_overrides = dict(boundary_operators or {})
    boundary_items: list[dict[str, Any]] = []
    boundary_node_ids = set(projection_plan.opaque_boundaries) | set(
        projection_plan.materialisation_boundaries
    )
    for node_id in boundary_node_ids:
        node = node_map.get(node_id)
        operator = operator_overrides.get(
            node_id,
            node.data.nodeType.value if node is not None else "unknown",
        )
        boundary_items.append(
            {
                "topological_rank": ranks.get(node_id, _MAX_TOPOLOGICAL_RANK),
                "node_id": node_id,
                "operator": operator,
                "boundary_kind": (
                    ExecutionStrategy.MATERIALISATION_BOUNDARY.value
                    if node_id in projection_plan.materialisation_boundaries
                    else ExecutionStrategy.UNPROJECTED_STREAMING_BOUNDARY.value
                ),
            }
        )

    reason_items: list[dict[str, Any]] = []
    for node_id, reason in projection_plan.diagnostics.node_reasons.items():
        node = node_map.get(node_id)
        reason_items.append(
            {
                "topological_rank": ranks.get(node_id),
                "node_id": node_id,
                "operator": node.data.nodeType.value if node is not None else None,
                "reason_code": reason.rule,
                "message": reason.message,
            }
        )
    for edge_key, reason in projection_plan.diagnostics.edge_reasons.items():
        parent_id = edge_key.source
        child_id = edge_key.target
        node = node_map.get(child_id)
        reason_items.append(
            {
                "topological_rank": ranks.get(child_id),
                "node_id": child_id,
                "operator": node.data.nodeType.value if node is not None else None,
                "reason_code": reason.rule,
                "message": reason.message,
                "parent_node_id": parent_id,
                "edge_id": edge_key.edge_id,
                "source_handle": edge_key.source_handle,
                "target_handle": edge_key.target_handle,
            }
        )

    boundaries = BoundedDiagnosticCollection.from_items(
        boundary_items,
        cap=_BOUNDARY_REASON_CAP,
        sort_key="boundaries",
        retain_one_by="boundary_kind",
    )
    reasons = BoundedDiagnosticCollection.from_items(
        reason_items,
        cap=_BOUNDARY_REASON_CAP,
        sort_key="reasons",
    )
    provenance = BoundedDiagnosticCollection.from_items(
        _execution_strategy_provenance_items(
            projection_plan,
            order=canonical_order,
            node_map=node_map,
            required_columns_by_node=required_columns_by_node,
        ),
        cap=_PROVENANCE_CAP,
        sort_key="provenance",
    )
    primary_boundary_kind = {
        ExecutionStrategy.MATERIALISATION_BOUNDARY: (
            ExecutionStrategy.MATERIALISATION_BOUNDARY.value
        ),
        # A conservative run still materialises at the group-by; its blocking
        # boundary is that materialisation, not an earlier projection boundary.
        ExecutionStrategy.FULL_WIDTH_CONSERVATIVE: (
            ExecutionStrategy.MATERIALISATION_BOUNDARY.value
        ),
        ExecutionStrategy.FULL_WIDTH_ADMITTED_EAGER: (
            ExecutionStrategy.UNPROJECTED_STREAMING_BOUNDARY.value
        ),
        ExecutionStrategy.UNPROJECTED_STREAMING_BOUNDARY: (
            ExecutionStrategy.UNPROJECTED_STREAMING_BOUNDARY.value
        ),
    }.get(strategy)
    primary_candidates = [
        item for item in boundary_items if item.get("boundary_kind") == primary_boundary_kind
    ]
    primary_boundary = min(
        primary_candidates or boundary_items,
        key=lambda item: (
            _bounded_item_primary_key(item, "boundaries"),
            canonical_json(item),
        ),
        default=None,
    )
    diagnostic = ExecutionStrategyDiagnostic.create(
        strategy=strategy,
        profile=profile,
        boundedness=boundedness,
        reason_code=reason_code,
        boundaries=boundaries,
        reasons=reasons,
        provenance=provenance,
        blocking_node_id=(str(primary_boundary["node_id"]) if primary_boundary else None),
        blocking_operator=(str(primary_boundary["operator"]) if primary_boundary else None),
        remediation=remediation,
        estimated_peak_bytes=estimated_peak_bytes,
        raw_estimated_peak_bytes=raw_estimated_peak_bytes,
        estimate_calibration_factor_basis_points=(estimate_calibration_factor_basis_points),
        estimate_admission_basis=estimate_admission_basis,
        headroom_bytes=headroom_bytes,
        assumptions=assumptions,
    )
    return ExecutionStrategyResult(projection_plan=projection_plan, diagnostic=diagnostic)


def _execution_strategy_provenance_items(
    projection_plan: ProjectionPlan,
    *,
    order: Iterable[str],
    node_map: Mapping[str, GraphNode],
    required_columns_by_node: Mapping[str, Iterable[str] | AllExceptColumns] | None,
) -> tuple[Mapping[str, Any], ...]:
    """Return deterministic, data-free per-column demand origins.

    A provenance row names the node where a demand entered the reverse sweep.
    Exact demands are attributed to a caller seed, a node expression, a join
    key, or the ordinary column contract. Opaque demand is represented by the
    reserved ``*`` column and never guesses at a source schema.
    """
    canonical_order = tuple(order)
    seeded = normalise_required_columns_by_node(
        required_columns_by_node,
        list(canonical_order),
    )
    items: dict[tuple[str, str, str, str], Mapping[str, Any]] = {}
    attributed: set[tuple[str, str]] = set()

    def add(
        column: str,
        origin_kind: str,
        source_node_id: str,
        source_column: str | None = None,
    ) -> None:
        item: dict[str, Any] = {
            "column": column,
            "origin_kind": origin_kind,
            "source_node_id": source_node_id,
        }
        if source_column is not None:
            item["source_column"] = source_column
        key = (column, origin_kind, source_node_id, source_column or "")
        items[key] = item
        attributed.add((source_node_id, column))

    for node_id in canonical_order:
        demand = projection_plan.needed_by_node.get(node_id)
        if demand is None:
            add("*", "conservative_boundary", node_id)

        seed = seeded.get(node_id)
        if seed is not None:
            seed_columns = seed.keep if isinstance(seed, AllExceptColumns) else seed
            # Later rule evaluation may replace the node's headline reason
            # (for example with a lineage result), so reason identity is not a
            # reliable record of whether the seed contributed.  A concrete
            # final demand containing the requested columns is the invariant;
            # an opaque/blocked demand is deliberately not labelled applied.
            seed_applied = demand is not None and set(seed_columns) <= set(demand)
            if seed_applied:
                for column in sorted(seed_columns):
                    add(column, "seed", node_id, column)

        node = node_map.get(node_id)
        if node is None:
            continue
        # An opaque demand has no column-level expression provenance to
        # inspect.  In particular, dynamic contracts such as modelScore may
        # resolve external artifacts; diagnostics must not perform that work
        # after the planner has already classified the node as conservative.
        if demand is None:
            continue
        # A modelScore contract can resolve an external model artifact.  The
        # planner has already incorporated that contract into ``demand``;
        # provenance must describe the resulting demand without loading the
        # same artifact a second time.
        if node.data.nodeType is NodeType.MODEL_SCORE:
            continue
        produced, referenced = projection_contract(node).to_tuple()
        if produced is not None and referenced is not None:
            for column in sorted(referenced):
                add(column, "expression", node_id, column)

        if node.data.nodeType is NodeType.EDGE_JOIN:
            base_keys, join_keys = edge_join_key_columns_by_role(node.data.config)
            for column in sorted(base_keys | join_keys):
                add(column, "join_key", node_id, column)

    for node_id in canonical_order:
        demand = projection_plan.needed_by_node.get(node_id)
        if demand is None:
            continue
        for column in sorted(demand):
            if (node_id, column) not in attributed:
                add(column, "contract", node_id, column)

    return tuple(items.values())


def _canonical_topological_ranks(
    order: Iterable[str],
    children_of: Mapping[str, Iterable[str]],
) -> Mapping[str, int]:
    """Return canonical Kahn ranks with lexical node-id tie breaks."""
    node_ids = set(order)
    in_degree = dict.fromkeys(node_ids, 0)
    canonical_children: dict[str, tuple[str, ...]] = {}
    for parent_id in node_ids:
        children = tuple(
            sorted(
                {child_id for child_id in children_of.get(parent_id, ()) if child_id in node_ids}
            )
        )
        canonical_children[parent_id] = children
        for child_id in children:
            in_degree[child_id] += 1

    ready = [node_id for node_id, degree in in_degree.items() if degree == 0]
    heapq.heapify(ready)
    canonical_order: list[str] = []
    while ready:
        node_id = heapq.heappop(ready)
        canonical_order.append(node_id)
        for child_id in canonical_children[node_id]:
            in_degree[child_id] -= 1
            if in_degree[child_id] == 0:
                heapq.heappush(ready, child_id)
    if len(canonical_order) != len(node_ids):
        raise RuntimeError("execution strategy diagnostics received a cyclic prepared graph")
    return MappingProxyType({node_id: rank for rank, node_id in enumerate(canonical_order)})


def with_materialisation_boundaries(
    projection_plan: ProjectionPlan,
    node_ids: Iterable[str],
) -> ProjectionPlan:
    """Return a plan whose named nodes are explicit full-materialisation boundaries."""
    boundaries = frozenset(node_ids)
    return replace(
        projection_plan,
        materialisation_boundaries=projection_plan.materialisation_boundaries | boundaries,
        opaque_boundaries=projection_plan.opaque_boundaries - boundaries,
    )


@dataclass(frozen=True)
class SourceScanProjection:
    """Physical columns a source scan reads, or ``None`` for the full width."""

    columns: frozenset[str] | None


@dataclass(frozen=True)
class AllExcept:
    """Schema-derived demand for targets that train on all non-excluded columns.

    The planner resolves this to an exact set as soon as it has a proven target
    schema; otherwise it remains conservative until that schema is available.
    `required_columns` names metadata such as target, weight, offset, split keys,
    and ids that must survive even if users also list them in
    `excluded_columns`.
    """

    required_columns: frozenset[str] = frozenset()
    excluded_columns: frozenset[str] = frozenset()

    @property
    def keep(self) -> frozenset[str]:
        return self.required_columns

    @property
    def exclude(self) -> frozenset[str]:
        return self.excluded_columns

    def resolve(self, exact_columns: Iterable[str]) -> frozenset[str]:
        """Resolve this schema-relative request against an exact output schema."""
        return frozenset(
            (set(exact_columns) - set(self.excluded_columns)) | set(self.required_columns)
        )


AllExceptColumns = AllExcept


@dataclass(frozen=True)
class ProjectionRuleCoverage:
    """Registry entry describing how a node type participates in projection."""

    node_type: NodeType
    rules: frozenset[str]
    opaque: bool = False
    note: str = ""


@dataclass(frozen=True)
class ParentDemandResult:
    """How a node contributes column demand to its parents."""

    default: set[str] | None
    by_parent: dict[str, set[str] | None]
    rule_name: str = "projection_rule"
    resolved_output: set[str] | None = None
    message: str | None = None

    def for_parent(self, parent_id: str) -> set[str] | None:
        return self.by_parent.get(parent_id, self.default)


def _unprojected_boundary_demands(
    *,
    default: set[str] | None = None,
    by_parent: dict[str, set[str] | None] | None = None,
    rule_name: str | None = None,
    message: str | None = None,
) -> ParentDemandResult:
    """Return a demand result for an explicit bounded full-width boundary.

    This is deliberately not a broad eager fallback.  It tells the lazy engine
    to avoid pushing an unsafe column projection through a node whose
    dependencies cannot be proven yet.  The downstream sink/checkpoint still
    runs through the bounded Polars streaming contract, so unsupported operator
    shapes fail loudly at execution rather than silently collecting.
    """
    return ParentDemandResult(
        default=default,
        by_parent={} if by_parent is None else by_parent,
        rule_name=UNPROJECTED_STREAMING_BOUNDARY_RULE_NAME if rule_name is None else rule_name,
        message=message,
    )


class PreparedGraph(NamedTuple):
    """Graph lookups needed by projection and execution planning."""

    node_map: dict[str, GraphNode]
    order: list[str]
    parents_of: dict[str, list[str]]
    id_to_name: dict[str, str]
    # The post-pruning, ancestor-filtered edge list used to build
    # ``parents_of``. Exposed so callers (notably the executor's
    # frame-aware binding) can index incoming edges per child without
    # re-deriving the prune set themselves.
    relevant_edges: list[GraphEdge]
    # Public port labels remain definition-owned while a graph still contains
    # collapsed submodel occurrences. Keep that registry with the prepared
    # edges so planner-side input-name derivation uses the same identity.
    submodels: Mapping[str, Any] | None


def normalise_required_columns_by_node(
    required_columns_by_node: Mapping[str, Iterable[str] | AllExceptColumns] | None,
    order: list[str],
) -> dict[str, set[str] | AllExceptColumns]:
    """Validate caller-provided projection seeds for concrete node outputs."""
    if not required_columns_by_node:
        return {}

    executable_ids = set(order)
    normalised: dict[str, set[str] | AllExceptColumns] = {}
    for node_id, raw_columns in required_columns_by_node.items():
        if not isinstance(node_id, str) or not node_id:
            raise ValueError("required_columns_by_node keys must be non-empty node ids.")
        if node_id not in executable_ids:
            raise ValueError(
                f"required_columns_by_node references node {node_id!r}, "
                "but that node is not in the lazy execution target."
            )
        if isinstance(raw_columns, AllExceptColumns):
            normalised[node_id] = raw_columns
            continue
        if raw_columns is None or isinstance(raw_columns, (str, bytes)):
            raise ValueError(
                f"required_columns_by_node[{node_id!r}] must be an iterable of column names."
            )

        columns: set[str] = set()
        for column in raw_columns:
            if not isinstance(column, str) or not column:
                raise ValueError(
                    f"required_columns_by_node[{node_id!r}] must contain "
                    "non-empty string column names."
                )
            columns.add(column)
        normalised[node_id] = columns
    return normalised


def model_score_required_output_columns(
    config: Mapping[str, Any],
    required_output_columns: Iterable[str] | None,
    *,
    post_processing_code: str | None = None,
) -> frozenset[str] | None:
    """Return the builder output demand for a configured modelScore node.

    User post-code and renames can alter the final scored schema after the
    scorer runs, so the scorer cannot safely shrink its write projection in
    those shapes.  When the caller has no explicit projection seed, return
    ``None``: config-level ``selected_columns`` may contain stale columns and
    is applied later with optional semantics by the executor.
    """
    code = (
        str(config.get("code") or "").strip()
        if post_processing_code is None
        else post_processing_code
    )
    if code or config.get("column_renames"):
        return None
    if required_output_columns is None:
        return None
    return frozenset(str(column) for column in required_output_columns)


def _strict_string_list(raw: object, *, key: str) -> list[str]:
    if raw is None:
        return []
    if not isinstance(raw, list):
        raise ValueError(f"source {key!r} must be a list of column names")
    ordered: list[str] = []
    seen: set[str] = set()
    for value in raw:
        if not isinstance(value, str) or not value:
            raise ValueError(f"source {key!r} must contain non-empty string names")
        if value in seen:
            raise ValueError(f"source {key!r} contains duplicate column {value!r}")
        ordered.append(value)
        seen.add(value)
    return ordered


def _strict_renames(config: Mapping[str, Any]) -> dict[str, str]:
    raw = config.get("column_renames")
    if raw is None:
        return {}
    if not isinstance(raw, Mapping):
        raise ValueError("source 'column_renames' must be a mapping")
    renames: dict[str, str] = {}
    for source, target in raw.items():
        if not isinstance(source, str) or not source:
            raise ValueError("source 'column_renames' keys must be non-empty strings")
        if not isinstance(target, str) or not target:
            raise ValueError("source 'column_renames' values must be non-empty strings")
        if source != target:
            renames[source] = target
    return renames


def source_scan_projection(
    config: Mapping[str, Any],
    required_output_columns: Iterable[str] | None,
    *,
    code: str = "",
    source_columns: Iterable[str] | None = None,
) -> SourceScanProjection:
    """Map logical source output demand to physical scan columns.

    The physical scan is driven by planner demand alone.  ``selected_columns``
    has exactly one interpreter — the executor's post-call filter, which runs
    after a source node's code in every profile — so it is never pushed into
    the physical read and never validated against the file schema here.  It
    still bounds what a source can legitimately be asked for: demand for a
    logical column the selection excludes is a planning error.

    Projection seeds are expressed in post-source logical output names, so a
    demanded logical column such as ``premium`` must be pushed down as its
    physical input name, for example ``raw_premium``; the source's post-load
    ``code`` then carries that demand back to the columns the scan has to read
    (:func:`source_user_code_scan_columns`).  ``source_columns`` is the opened
    scan's schema.  Without it a scan under projection-opaque code stays full
    width, because only a known schema lets lineage keep a row carrier.
    """
    selected = _strict_string_list(config.get("selected_columns"), key="selected_columns")
    selected_set = frozenset(selected)
    _strict_renames(config)

    if required_output_columns is None:
        return SourceScanProjection(columns=None)

    required = frozenset(required_output_columns)
    pre_shaping_names = _source_pre_shaping_names(config, required)
    if selected and pre_shaping_names is not None:
        excluded = sorted(
            logical_column
            for logical_column, column in pre_shaping_names.items()
            if column not in selected_set
        )
        if excluded:
            raise ValueError(
                "source projection requires a logical output column excluded by "
                f"selected_columns: {excluded[0]!r}"
            )
    if source_columns is None and not source_user_code_preserves_column_projection(code):
        return SourceScanProjection(columns=None)
    return SourceScanProjection(
        columns=source_user_code_scan_columns(
            config,
            code,
            required,
            source_columns=source_columns,
        )
    )


def _source_pre_shaping_names(
    config: Mapping[str, Any],
    output_columns: frozenset[str],
) -> dict[str, str] | None:
    """Map demanded output names to the names a source produces before its renames.

    ``None`` means a demanded name cannot be attributed to exactly one
    pre-rename column, so the scan has to stay full width.
    """
    selected = _strict_string_list(config.get("selected_columns"), key="selected_columns")
    renames = _strict_renames(config)
    reverse: dict[str, str] = {}
    ambiguous_targets: set[str] = set()
    for source, target in renames.items():
        if target in reverse:
            ambiguous_targets.add(target)
            continue
        reverse[target] = source

    if renames and not selected and output_columns & (set(reverse) | ambiguous_targets):
        return None
    if output_columns & ambiguous_targets:
        return None
    return {column: reverse.get(column, column) for column in output_columns}


def has_configured_column_renames(node: GraphNode) -> bool:
    """Whether output shaping changes the builder's column namespace."""
    renames = node.data.config.get("column_renames") or {}
    return any(source != target for source, target in renames.items())


_SOURCE_PROJECTION_TRANSPARENT_METHODS = frozenset({"limit", "head", "tail", "slice"})


def _expr_references_name(expr: ast.AST, name: str) -> bool:
    return any(isinstance(node, ast.Name) and node.id == name for node in ast.walk(expr))


def _is_projection_transparent_source_chain(expr: ast.AST) -> bool:
    if isinstance(expr, ast.Name):
        return expr.id == "df"
    if not isinstance(expr, ast.Call) or not isinstance(expr.func, ast.Attribute):
        return False
    if expr.func.attr not in _SOURCE_PROJECTION_TRANSPARENT_METHODS:
        return False
    return _is_projection_transparent_source_chain(expr.func.value)


def source_user_code_preserves_column_projection(code: str) -> bool:
    """Return whether data-source post-load code can safely accept scan projection.

    Data-source code runs after the declarative source scan.  A small set of
    source-level row limiting operations does not inspect or create columns, so
    reading only downstream-required columns is equivalent to reading the full
    source and then applying the same code.  Anything that might depend on
    column values, alter the schema, or obscure the frame flow remains opaque
    and keeps a visible full-width boundary in every profile unless a concrete
    contract describes it.
    """
    stripped = code.strip()
    if not stripped:
        return True
    try:
        tree = ast.parse(stripped)
    except SyntaxError:
        return False

    saw_df_assignment = False
    for stmt in tree.body:
        if isinstance(stmt, (ast.Import, ast.ImportFrom, ast.Pass)):
            continue
        if isinstance(stmt, ast.Expr) and isinstance(stmt.value, ast.Constant):
            continue
        if isinstance(stmt, ast.Assign):
            if len(stmt.targets) != 1 or not isinstance(stmt.targets[0], ast.Name):
                return False
            target_name = stmt.targets[0].id
            if target_name != "df":
                if _expr_references_name(stmt.value, "df"):
                    return False
                continue
            if not _is_projection_transparent_source_chain(stmt.value):
                return False
            saw_df_assignment = True
            continue
        if isinstance(stmt, ast.AnnAssign) and isinstance(stmt.target, ast.Name):
            if stmt.target.id == "df":
                return False
            if stmt.value is not None and _expr_references_name(stmt.value, "df"):
                return False
            continue
        return False

    return saw_df_assignment


def source_user_code_scan_columns(
    config: Mapping[str, Any],
    code: str,
    output_columns: Iterable[str],
    *,
    source_columns: Iterable[str] | None,
) -> frozenset[str] | None:
    """Return the scan columns a source needs to produce logical *output_columns*.

    Output shaping runs after the post-load ``code``, so demanded names are
    first mapped back through the configured renames.  Projection-transparent
    code then passes that demand through unchanged; other code is carried back
    through the closed column lineage model, so the scan never reads a column
    the code creates and always reads the columns it consumes.  ``None`` means
    the scan must stay full width.  The planner, source builders, and runtime
    join refinement all decide source projection through this one rule.

    ``source_columns`` is the opened scan's schema, and a caller choosing
    physical columns must supply it: only a known schema lets lineage keep a
    row carrier when the code drops every demanded column, and lets a rename
    that would collide on the full output keep the scan full width, so the
    collision raises in every profile.  Planning passes ``None`` to ask only
    whether projection is provable.
    """
    pre_shaping_names = _source_pre_shaping_names(config, frozenset(output_columns))
    if pre_shaping_names is None:
        return None
    demand = frozenset(pre_shaping_names.values())
    schema = None if source_columns is None else frozenset(source_columns)
    if source_user_code_preserves_column_projection(code):
        scan_columns, pre_shaping_output = demand, schema
    else:
        lineage = analyze_polars_lineage(code, {"df": schema}, demand)
        if not lineage.supported:
            return None
        scan_columns = lineage.demands_by_input["df"]
        pre_shaping_output = lineage.exact_output_columns
    if pre_shaping_output is not None and (
        _configured_output_schema(config, pre_shaping_output) is None
    ):
        return None
    return scan_columns


_MaterialisingCall = tuple[int, int, int, str]
"""``(evaluation_index, lineno, col_offset, attribute)`` for one boundary call.

The evaluation index leads because source position does not order chained calls:
``df.unique(...).reverse()`` gives both calls the same ``(lineno, col_offset)``,
which left the operator to a lexical tie-break. The classifier walks in Python
evaluation order, so the order it records is the order the frame is transformed.
"""

_COMPREHENSION_TYPES = (ast.ListComp, ast.SetComp, ast.GeneratorExp, ast.DictComp)

# What a value provably is: a frame chain, provably not a frame, or unresolvable.
_BindingFact = Literal["frame", "non_frame", "namespace", "unknown"]

# ``pl.<name>(...)`` calls that build expressions rather than frames. Any other
# ``pl`` attribute call (``pl.concat``, ``pl.scan_parquet``, ``pl.DataFrame``)
# may construct a frame and is therefore unresolvable.
_POLARS_EXPRESSION_FUNCTIONS = registered_names(OperationReceiver.POLARS_FUNCTION)


def _mutation_root_name(target: ast.AST) -> str | None:
    """Return the name whose object an attribute/subscript assignment mutates."""
    current = target
    while isinstance(current, ast.Attribute | ast.Subscript):
        current = current.value
    return current.id if isinstance(current, ast.Name) else None


def _combine_facts(facts: Iterable[_BindingFact]) -> _BindingFact:
    """Return the fact of a value that may be any one of ``facts``.

    Equal facts stay; a non-frame and an expression namespace are both
    non-frames; any other mix may be a frame or may not be one.
    """
    collected = set(facts)
    if not collected:
        return "non_frame"
    if len(collected) == 1:
        return next(iter(collected))
    if collected <= {"non_frame", "namespace"}:
        return "non_frame"
    return "unknown"


def _is_non_frame(fact: _BindingFact) -> bool:
    return fact in ("non_frame", "namespace")


def _materialising_calls_in_source_order(
    tree: ast.Module,
    input_names: frozenset[str],
    materialising: frozenset[str],
    materialising_expressions: frozenset[str] = frozenset(),
) -> list[_MaterialisingCall]:
    """Classify materialising calls with the receiver state at each evaluation.

    The pass walks statements in program order and expressions in Python
    evaluation order, holding one fact for every simple name: a *proven frame*
    (one of the node's bound input names, ``df``, or a name definitely bound
    from a proven frame), a *provable non-frame* (``pl`` itself, a literal, a
    comparison result, a ``pl``-rooted expression chain, a function or lambda
    object, or a name definitely rebound to one of those), an *expression
    namespace* (``pl.col("l").list``, or a name bound to one), or a *may-frame*
    (every other name: a preamble name, a function parameter, or a name bound
    from a call the analyser cannot see through). A frame-method call is a
    boundary unless its receiver is a provable non-frame or a namespace, so a
    preamble frame's ``group_by``, a helper's returned frame, and a parameter
    inside a user function all admit a boundary, while
    ``pl.col(...).list.group_by(...)`` never does.

    Binding model. Every simple-name binding form is applied in evaluation
    order: plain, chained (``a = b = value``), annotated, and element-wise
    unpacked assignments, walrus bindings, loop, ``with``, and comprehension
    targets, imports, and function/class definitions. The fact of each
    right-hand value is captured when that value is evaluated, and one
    assignment binds all of its targets from those captured facts afterwards
    (Python's parallel semantics). Function and lambda bodies are analysed in
    a scoped environment whose parameters are may-frames; names that may hold
    a frame inside the body remain may-frames outside it. A *definite*
    rebinding (a top-level statement, or a walrus in a position that is
    always evaluated) to a provable non-frame stops the name from being a
    frame only for the code that follows. Bindings inside nested blocks,
    short-circuit operands (later operands of ``and``/``or`` and later
    comparators of a chained comparison), conditional branches, lambda and
    function bodies, and comprehensions are may-bindings: they combine the
    name's fact with the bound value's, so they can add a frame possibility
    but never remove one. An ``and``/``or``, conditional expression, or
    operator whose operands' facts differ is a may-frame, except that a
    non-frame and a namespace combine to a non-frame. Values the analyser cannot resolve
    (unpacking from an unknown value, starred, loop, ``with``, and
    comprehension targets, calls it cannot see through) and every mutable
    container (a list, set, or dict display or comprehension, whatever it
    holds, since it may receive a frame later) yield may-frames; a tuple is a
    provable non-frame only when every element is. A subscript, attribute, or
    augmented assignment marks the root name it mutates as a may-frame. So an
    unsupported shape can only add a boundary, never hide one.

    Frame methods in ``materialising`` are classified by receiver as described
    above. ``materialising_expressions`` holds expression-level boundary
    methods (``over``, ``shift``, ``diff``, ``pct_change``), written on
    expressions rather than frames: they are a boundary on any receiver except
    a proven frame, which cannot be an expression, and an expression namespace
    (``.list.shift``, directly or through a bound name), whose same-named method
    works within each row's value. A may-frame receiver -- a helper's parameter,
    or a value that may be a frame or an expression -- admits both rules.
    """
    facts_by_name: dict[str, _BindingFact] = {name: "frame" for name in (*input_names, "df")}
    facts_by_name["pl"] = "non_frame"
    found: list[_MaterialisingCall] = []

    def expression_boundary(attribute: ast.Attribute, receiver: _BindingFact) -> bool:
        # A proven frame has no expression methods, and ``expr.list.shift`` works
        # within each row's value rather than being ``Expr.shift``.
        if attribute.attr not in materialising_expressions or receiver in ("frame", "namespace"):
            return False
        value = attribute.value
        return not (isinstance(value, ast.Attribute) and value.attr in EXPRESSION_NAMESPACE_NAMES)

    def record(node: ast.AST, attr: str) -> None:
        found.append(
            (
                len(found),
                getattr(node, "lineno", _MAX_TOPOLOGICAL_RANK),
                getattr(node, "col_offset", _MAX_TOPOLOGICAL_RANK),
                attr,
            )
        )

    def name_fact(name: str) -> _BindingFact:
        return facts_by_name.get(name, "unknown")

    def apply_facts(facts: Iterable[tuple[str, _BindingFact]], *, definite: bool) -> None:
        for name, fact in facts:
            # A may-binding keeps what the name held before as a possibility.
            facts_by_name[name] = fact if definite else _combine_facts((name_fact(name), fact))

    def collect(
        target: ast.AST,
        fact: _BindingFact,
        element_facts: list[_BindingFact] | None,
        facts: list[tuple[str, _BindingFact]],
    ) -> None:
        """Pair every simple name bound by ``target`` with its captured fact."""
        if isinstance(target, ast.Name):
            facts.append((target.id, fact))
        elif isinstance(target, ast.Starred):
            collect(target.value, "unknown", None, facts)
        elif isinstance(target, ast.Tuple | ast.List):
            elements = target.elts
            if (
                element_facts is not None
                and len(element_facts) == len(elements)
                and not any(isinstance(item, ast.Starred) for item in elements)
            ):
                for element, element_fact in zip(elements, element_facts, strict=True):
                    collect(element, element_fact, None, facts)
            else:
                for element in elements:
                    collect(element, "unknown", None, facts)
        else:
            # ``obj.attr = value`` / ``obj[key] = value`` mutate whatever the
            # root name holds, which may now contain a frame.
            root = _mutation_root_name(target)
            if root is not None:
                facts.append((root, "unknown"))

    def evaluate(node: ast.AST, *, definite: bool) -> _BindingFact:
        """Walk ``node`` in evaluation order and return the fact of its value.

        Walrus bindings take effect and materialising calls are recorded as
        they are reached, so a name's fact is read exactly when Python reads it.
        """
        if isinstance(node, ast.Constant):
            return "non_frame"
        if isinstance(node, ast.Name):
            return name_fact(node.id)
        if isinstance(node, ast.NamedExpr):
            fact = evaluate(node.value, definite=definite)
            bound: list[tuple[str, _BindingFact]] = []
            collect(node.target, fact, None, bound)
            apply_facts(bound, definite=definite)
            return fact
        if isinstance(node, ast.Attribute):
            fact = evaluate(node.value, definite=definite)
            # A materialising method taken as a value (``g = df.group_by``) is
            # recorded where it is bound: the later ``g(...)`` call has a plain
            # name for its callee and cannot be classified by receiver.
            if (node.attr in materialising and not _is_non_frame(fact)) or expression_boundary(
                node, fact
            ):
                record(node, node.attr)
            if (
                isinstance(node.value, ast.Name)
                and node.value.id == "pl"
                and node.attr not in _POLARS_EXPRESSION_FUNCTIONS
            ):
                # ``pl.LazyFrame`` / ``pl.DataFrame`` are frame classes whose
                # unbound methods take a frame as their first argument.
                return "unknown"
            if _is_non_frame(fact):
                # ``pl.col("l").list`` is a namespace; a namespace's method is not.
                return "namespace" if node.attr in EXPRESSION_NAMESPACE_NAMES else "non_frame"
            return fact
        if isinstance(node, ast.Subscript):
            fact = evaluate(node.value, definite=definite)
            evaluate(node.slice, definite=definite)
            return fact
        if isinstance(node, ast.Call):
            materialises = False
            if isinstance(node.func, ast.Attribute):
                receiver = evaluate(node.func.value, definite=definite)
                materialises = (
                    node.func.attr in materialising and not _is_non_frame(receiver)
                ) or expression_boundary(node.func, receiver)
                if not _is_non_frame(receiver):
                    fact = receiver
                elif (
                    isinstance(node.func.value, ast.Name)
                    and node.func.value.id == "pl"
                    and node.func.attr not in _POLARS_EXPRESSION_FUNCTIONS
                ):
                    fact = "unknown"
                else:
                    fact = "non_frame"
            else:
                # Any other callable (a user function, a preamble helper, a
                # builtin) may return a frame.
                evaluate(node.func, definite=definite)
                fact = "unknown"
            for argument in node.args:
                evaluate(argument, definite=definite)
            for keyword in node.keywords:
                evaluate(keyword.value, definite=definite)
            if materialises:
                # Python evaluates the receiver, then the arguments, then the
                # call. Recording before the arguments reported
                # ``left.join(right.sort(...))`` as join-then-sort, which is the
                # reverse of the order the frames are actually transformed in.
                assert isinstance(node.func, ast.Attribute)
                record(node, node.func.attr)
            return fact
        if isinstance(node, ast.BoolOp):
            first, *rest = node.values
            operand_facts = [evaluate(first, definite=definite)]
            operand_facts.extend(evaluate(value, definite=False) for value in rest)
            return _combine_facts(operand_facts)
        if isinstance(node, ast.IfExp):
            evaluate(node.test, definite=definite)
            return _combine_facts(
                (
                    evaluate(node.body, definite=False),
                    evaluate(node.orelse, definite=False),
                )
            )
        if isinstance(node, ast.Compare):
            # Only the first comparison is always evaluated; later comparators
            # short-circuit like ``and`` operands.
            evaluate(node.left, definite=definite)
            for index, comparator in enumerate(node.comparators):
                evaluate(comparator, definite=definite and index == 0)
            return "non_frame"
        if isinstance(node, ast.BinOp):
            left = evaluate(node.left, definite=definite)
            return _combine_facts((left, evaluate(node.right, definite=definite)))
        if isinstance(node, ast.UnaryOp):
            return _combine_facts((evaluate(node.operand, definite=definite),))
        if isinstance(node, ast.Tuple):
            content = _combine_facts(evaluate(item, definite=definite) for item in node.elts)
            return "non_frame" if _is_non_frame(content) else "unknown"
        if isinstance(node, ast.List | ast.Set):
            # A mutable container may receive a frame after it is built.
            for item in node.elts:
                evaluate(item, definite=definite)
            return "unknown"
        if isinstance(node, ast.Dict):
            for key, value in zip(node.keys, node.values, strict=True):
                if key is not None:
                    evaluate(key, definite=definite)
                evaluate(value, definite=definite)
            return "unknown"
        if isinstance(node, _COMPREHENSION_TYPES):
            # The first iterable is evaluated in the enclosing scope; every
            # other part runs zero or more times inside the comprehension, and
            # each generator target may hold a frame drawn from its iterable.
            for index, generator in enumerate(node.generators):
                evaluate(generator.iter, definite=definite and index == 0)
                bind_targets((generator.target,), None, definite=False)
                for condition in generator.ifs:
                    evaluate(condition, definite=False)
            if isinstance(node, ast.DictComp):
                evaluate(node.key, definite=False)
                evaluate(node.value, definite=False)
            else:
                evaluate(node.elt, definite=False)
            return "unknown"
        if isinstance(node, ast.Lambda):
            analyse_function(node.args, node.body, definite=definite)
            return "non_frame"
        if isinstance(node, ast.JoinedStr | ast.FormattedValue | ast.Slice):
            for child in ast.iter_child_nodes(node):
                evaluate(child, definite=definite)
            return "non_frame"
        if isinstance(node, ast.Starred):
            return evaluate(node.value, definite=definite)
        for child in ast.iter_child_nodes(node):
            evaluate(child, definite=definite)
        return "unknown"

    def bind_targets(
        targets: Iterable[ast.AST],
        value: ast.AST | None,
        *,
        definite: bool,
    ) -> None:
        """Evaluate ``value`` once, then bind all ``targets`` from its captured facts."""
        element_facts: list[_BindingFact] | None = None
        if value is None:
            fact: _BindingFact = "unknown"
        elif isinstance(value, ast.Tuple | ast.List) and not any(
            isinstance(item, ast.Starred) for item in value.elts
        ):
            element_facts = [evaluate(item, definite=definite) for item in value.elts]
            fact = (
                "non_frame"
                if isinstance(value, ast.Tuple) and _is_non_frame(_combine_facts(element_facts))
                else "unknown"
            )
        else:
            fact = evaluate(value, definite=definite)
        facts: list[tuple[str, _BindingFact]] = []
        for target in targets:
            collect(target, fact, element_facts, facts)
        apply_facts(facts, definite=definite)

    def analyse_function(
        args: ast.arguments,
        body: list[ast.stmt] | ast.expr,
        *,
        definite: bool,
    ) -> None:
        """Analyse a function or lambda body with its parameters as may-frames."""
        nonlocal facts_by_name
        for default in (*args.defaults, *args.kw_defaults):
            if default is not None:
                evaluate(default, definite=definite)
        parameters = {
            argument.arg for argument in (*args.posonlyargs, *args.args, *args.kwonlyargs)
        }
        for variadic in (args.vararg, args.kwarg):
            if variadic is not None:
                parameters.add(variadic.arg)
        outer_facts = facts_by_name
        facts_by_name = {**outer_facts, **dict.fromkeys(parameters, "unknown")}
        try:
            if isinstance(body, list):
                for stmt in body:
                    visit(stmt, definite=False)
            else:
                evaluate(body, definite=False)
        finally:
            inner_facts = facts_by_name
            facts_by_name = outer_facts
        # A name that may hold a frame inside the body (a nonlocal or global
        # write, or simply a local the analyser cannot scope) stays a
        # may-frame outside it; the parameters themselves do not escape.
        escaped = (
            (name, fact)
            for name, fact in inner_facts.items()
            if name not in parameters and not _is_non_frame(fact)
        )
        apply_facts(escaped, definite=False)

    def visit(node: ast.AST, *, definite: bool) -> None:
        if isinstance(node, ast.Assign):
            bind_targets(node.targets, node.value, definite=definite)
            return
        if isinstance(node, ast.AnnAssign):
            if node.value is not None:
                bind_targets((node.target,), node.value, definite=definite)
            return
        if isinstance(node, ast.AugAssign):
            # Python reads the target before it evaluates the right-hand side,
            # so the target's fact is captured first; the store happens last.
            target_fact: _BindingFact | None = None
            if isinstance(node.target, ast.Name):
                target_fact = name_fact(node.target.id)
            elif isinstance(node.target, ast.Attribute):
                evaluate(node.target.value, definite=definite)
            elif isinstance(node.target, ast.Subscript):
                evaluate(node.target.value, definite=definite)
                evaluate(node.target.slice, definite=definite)
            value_fact = evaluate(node.value, definite=definite)
            if isinstance(node.target, ast.Name) and target_fact is not None:
                combined = _combine_facts((target_fact, value_fact))
                apply_facts(((node.target.id, combined),), definite=definite)
            else:
                root = _mutation_root_name(node.target)
                if root is not None:
                    apply_facts(((root, "unknown"),), definite=definite)
            return
        if isinstance(node, ast.For | ast.AsyncFor):
            evaluate(node.iter, definite=definite)
            bind_targets((node.target,), None, definite=False)
            for stmt in (*node.body, *node.orelse):
                visit(stmt, definite=False)
            return
        if isinstance(node, ast.With | ast.AsyncWith):
            for item in node.items:
                evaluate(item.context_expr, definite=definite)
                if item.optional_vars is not None:
                    bind_targets((item.optional_vars,), None, definite=False)
            for stmt in node.body:
                visit(stmt, definite=False)
            return
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
            for decorator in node.decorator_list:
                evaluate(decorator, definite=definite)
            analyse_function(node.args, node.body, definite=definite)
            if node.returns is not None:
                evaluate(node.returns, definite=definite)
            apply_facts(((node.name, "non_frame"),), definite=definite)
            return
        if isinstance(node, ast.ClassDef):
            for expression in (*node.decorator_list, *node.bases):
                evaluate(expression, definite=definite)
            for keyword in node.keywords:
                evaluate(keyword.value, definite=definite)
            for stmt in node.body:
                visit(stmt, definite=False)
            apply_facts(((node.name, "non_frame"),), definite=definite)
            return
        if isinstance(node, ast.Import | ast.ImportFrom):
            apply_facts(
                (((alias.asname or alias.name).split(".")[0], "non_frame") for alias in node.names),
                definite=definite,
            )
            return
        # Header expressions are evaluated before nested statements run.
        for child in ast.iter_child_nodes(node):
            if isinstance(child, ast.stmt | ast.ExceptHandler | ast.match_case):
                visit(child, definite=False)
            else:
                evaluate(child, definite=definite)

    for stmt in tree.body:
        visit(stmt, definite=True)
    return found


def materialising_operator_sequences_by_node(
    order: Iterable[str],
    node_map: Mapping[str, GraphNode],
    *,
    relevant_edges: Iterable[GraphEdge],
    submodels: Mapping[str, Any] | None = None,
) -> Mapping[str, tuple[str, ...]]:
    """Return materialisation-boundary operators in deterministic execution order.

    The operator names come from the operation registry (the frame methods whose
    policy is a materialisation boundary), so the planner cannot disagree with
    the other analysers about which operations materialise. Only actual AST
    call attributes are classified; comments and string literals containing
    ``group_by`` cannot accidentally trigger the boundary. Syntax failures
    remain the owning code validator's error rather than being broadened
    through a textual fallback.

    Classification is receiver-aware and evaluation-ordered: a call
    materialises unless its receiver is provably not a frame at that point
    (``pl`` itself, a literal, a ``pl``-rooted expression chain, or a name
    definitely rebound to one of those). The node's input frame names (as
    ``_build_funcs`` binds them), ``df``, names derived from them, and every
    name the analyser cannot resolve (a preamble name, a function parameter,
    the result of a call it cannot see through) admit a boundary, so
    ``pl.col(...).list.group_by(...)`` never materialises while a preamble
    frame's ``group_by`` does, and rebinding an alias after its group-by
    cannot hide one.
    """
    input_names_by_node: dict[str, set[str]] = {}
    for edge in relevant_edges:
        source_node = node_map.get(edge.source)
        if source_node is None:
            continue
        try:
            name = edge_input_name(edge, source_node, submodels=submodels)
        except ValueError:
            # A malformed edge (an apiInput edge with no frame label) has no
            # input name to contribute. Skipping it stays conservative: an
            # unnamed input never hides a boundary, and the node builder is the
            # fail-loud point that reports the malformed edge to the user.
            continue
        input_names_by_node.setdefault(edge.target, set()).add(name)
    return materialising_operator_sequences_by_input_names(order, node_map, input_names_by_node)


def materialising_operator_sequences_by_input_names(
    order: Iterable[str],
    node_map: Mapping[str, GraphNode],
    input_names_by_node: Mapping[str, Iterable[str]],
) -> Mapping[str, tuple[str, ...]]:
    """Classify materialising operators from pre-derived input frame names.

    Callers that hold the prepared graph's edges use
    :func:`materialising_operator_sequences_by_node`; this entry point serves the prepared
    planner when edges were not supplied and the input names come from the
    parent labels instead.
    """
    materialising = materialising_frame_methods()
    materialising_expressions = materialising_expression_methods()
    found: dict[str, tuple[str, ...]] = {}
    for node_id in order:
        node = node_map[node_id]
        if node.data.nodeType is not NodeType.POLARS:
            continue
        code = node.data.config.get("code")
        if not isinstance(code, str) or not code.strip():
            continue
        try:
            tree = ast.parse(code)
        except SyntaxError:
            continue
        calls = _materialising_calls_in_source_order(
            tree,
            frozenset(input_names_by_node.get(node_id, ())),
            materialising,
            materialising_expressions,
        )
        if calls:
            # Evaluation order, deduplicated: the first entry is the operator the
            # diagnostic blames, and the whole tuple is what the estimator costs.
            found[node_id] = tuple(dict.fromkeys(call[3] for call in sorted(calls)))
    return MappingProxyType(found)


def first_materialising_operators(
    sequences: Mapping[str, tuple[str, ...]],
) -> Mapping[str, str]:
    """Reduce boundary sequences to the operator each node's diagnostic blames.

    The first entry is the first materialising call reached in evaluation order,
    which is the one that turns the node into a boundary.
    """
    return MappingProxyType(
        {node_id: operators[0] for node_id, operators in sequences.items() if operators}
    )


def materialising_operators_by_node(
    order: Iterable[str],
    node_map: Mapping[str, GraphNode],
    *,
    relevant_edges: Iterable[GraphEdge],
    submodels: Mapping[str, Any] | None = None,
) -> Mapping[str, str]:
    """Return each boundary node's first materialising operator."""
    return first_materialising_operators(
        materialising_operator_sequences_by_node(
            order,
            node_map,
            relevant_edges=relevant_edges,
            submodels=submodels,
        )
    )


def materialising_operators_by_input_names(
    order: Iterable[str],
    node_map: Mapping[str, GraphNode],
    input_names_by_node: Mapping[str, Iterable[str]],
) -> Mapping[str, str]:
    """Return each boundary node's first materialising operator."""
    return first_materialising_operators(
        materialising_operator_sequences_by_input_names(order, node_map, input_names_by_node)
    )


def builder_required_output_columns_by_node(
    node_map: Mapping[str, GraphNode],
    needed_by_node: Mapping[str, Iterable[str] | AllExceptColumns | None],
    *,
    preserve_eager_model_score_inputs: bool,
) -> dict[str, frozenset[str] | None]:
    """Return output-column demands to pass into node builders.

    Eager preview keeps modelScore builder demand broad so the executor can
    report the full passthrough schema before applying the preview projection.
    Lazy/batch paths pass the planner demand into modelScore so parquet batch
    scoring can avoid writing unused passthrough columns.
    """
    demands: dict[str, frozenset[str] | None] = {}
    for node_id, required_columns in needed_by_node.items():
        node = node_map[node_id]
        if isinstance(required_columns, AllExceptColumns):
            demands[node_id] = None
            continue
        if node.data.nodeType == NodeType.MODEL_SCORE:
            demands[node_id] = (
                None
                if preserve_eager_model_score_inputs
                else model_score_required_output_columns(
                    node.data.config,
                    required_columns,
                )
            )
            continue
        demands[node_id] = None if required_columns is None else frozenset(required_columns)
    return demands


def _declared_api_input_port_columns(
    node: GraphNode,
) -> dict[str, frozenset[str]] | None:
    """Return selected columns by emitting v2 port without touching payload data.

    This deliberately mirrors the v2 loader's emit predicate (``emit`` plus
    at least one selected column). Malformed/flat configs return ``None`` so
    execution keeps its established full-source path and the owning loader can
    report the authoritative validation error.
    """
    if node.data.nodeType is not NodeType.API_INPUT:
        return None
    tables = node.data.config.get("tables")
    if not isinstance(tables, list):
        return None
    ports: dict[str, frozenset[str]] = {}
    for table in tables:
        if not isinstance(table, Mapping):
            return None
        columns = table.get("columns") or []
        if not isinstance(columns, list):
            return None
        selected: set[str] = set()
        for column in columns:
            if not isinstance(column, Mapping):
                return None
            if not column.get("selected"):
                continue
            name = column.get("name")
            if not isinstance(name, str) or not name:
                return None
            selected.add(name)
        if not table.get("emit") or not selected:
            continue
        label = table.get("label")
        if not isinstance(label, str) or not label or label in ports:
            return None
        ports[label] = frozenset(selected)
    return ports or None


def api_input_port_columns_by_node(
    node_map: Mapping[str, GraphNode],
    relevant_edges: Iterable[GraphEdge],
    projection_plan: ProjectionPlan,
) -> dict[str, dict[str, frozenset[str] | None]]:
    """Translate proven edge demands into per-port API-input load demands.

    Only ports used by the prepared graph are included. Concrete consumers of
    the same port are unioned; one opaque consumer keeps that port full-width.
    A demand outside the selected schema also stays full-width instead of
    silently dropping a column or handing an invalid projection to the loader.
    """
    demands: dict[str, dict[str, frozenset[str] | None]] = {}
    unavailable_sources: set[str] = set()
    declared_by_node: dict[str, dict[str, frozenset[str]] | None] = {}
    for edge in relevant_edges:
        source = node_map.get(edge.source)
        if source is None or source.data.nodeType is not NodeType.API_INPUT:
            continue
        if edge.source not in declared_by_node:
            declared_by_node[edge.source] = _declared_api_input_port_columns(source)
        declared = declared_by_node[edge.source]
        port = edge.sourceHandle
        if declared is None or port is None or port not in declared:
            unavailable_sources.add(edge.source)
            demands.pop(edge.source, None)
            continue
        if edge.source in unavailable_sources:
            continue

        edge_key = ProjectionEdgeKey.from_edge(edge)
        edge_demand = projection_plan.edge_demands.get(edge_key)
        if (
            edge_key in projection_plan.edge_demands
            and edge_demand is not None
            and edge_demand <= declared[port]
        ):
            requested: frozenset[str] | None = frozenset(edge_demand)
        else:
            requested = None
        by_port = demands.setdefault(edge.source, {})
        existing = by_port.get(port, frozenset())
        if existing is None or requested is None:
            by_port[port] = None
        else:
            by_port[port] = frozenset(existing | requested)
    return demands


def with_api_input_port_projection_boundaries(
    projection_plan: ProjectionPlan,
    node_map: Mapping[str, GraphNode],
    relevant_edges: Iterable[GraphEdge],
) -> ProjectionPlan:
    """Keep unprovable per-port narrowing visible in the public strategy."""
    port_demands = api_input_port_columns_by_node(node_map, relevant_edges, projection_plan)
    newly_opaque = {
        node_id
        for node_id, by_port in port_demands.items()
        if projection_plan.needed_by_node.get(node_id) is not None
        and any(columns is None for columns in by_port.values())
    }
    if not newly_opaque:
        return projection_plan

    needed_by_node = dict(projection_plan.needed_by_node)
    node_reasons = dict(projection_plan.diagnostics.node_reasons)
    opaque_reasons = dict(projection_plan.diagnostics.opaque_reasons)
    for node_id in newly_opaque:
        needed_by_node[node_id] = None
        reason = ProjectionReason(
            rule=UNPROJECTED_STREAMING_BOUNDARY_RULE_NAME,
            message="API-input edge demand could not be proven within its selected port schema",
        )
        node_reasons[node_id] = reason
        opaque_reasons[node_id] = reason
    return ProjectionPlan(
        needed_by_node=MappingProxyType(needed_by_node),
        edge_demands=projection_plan.edge_demands,
        materialisation_boundaries=projection_plan.materialisation_boundaries,
        opaque_boundaries=projection_plan.opaque_boundaries | newly_opaque,
        diagnostics=ProjectionDiagnostics(
            opaque_reasons=MappingProxyType(opaque_reasons),
            node_reasons=MappingProxyType(node_reasons),
            edge_reasons=projection_plan.diagnostics.edge_reasons,
        ),
    )


def overlay_declared_contract(node: GraphNode, builder: Contract) -> Contract:
    """Fill a builder contract's opaque sides from the node's declared contract."""
    declared_raw = node.data.config.get("contract")
    if declared_raw is None:
        return builder
    try:
        declared = Contract.from_user_declared(declared_raw)
    except ValueError as exc:
        raise ContractMismatchError(
            "Node contract annotation is malformed and cannot be interpreted.",
            node_id=node.id,
            node_type=node.data.nodeType.value,
            reason=str(exc),
        ) from exc
    if declared is None:
        return builder
    if _empty_declared_contract_should_defer_to_builder(node, builder, declared):
        return builder
    return builder.fill_opaque_sides(declared)


def _empty_declared_contract_should_defer_to_builder(
    node: GraphNode,
    builder: Contract,
    declared: Contract,
) -> bool:
    """Return whether an empty/default declaration would erase safer knowledge."""
    if declared.inputs != frozenset() or declared.outputs != frozenset():
        return False
    if declared.inputs_by_parent:
        return False

    return _has_projection_user_code(node) and (builder.inputs is None or builder.outputs is None)


def projection_contract(node: GraphNode) -> Contract:
    """Return the column contract used by projection analysis.

    Unlike executor boundary checks, projection does not soften builder
    contract failures. A malformed concrete contract should be visible rather
    than quietly widening the graph.
    """
    registered = Contract.from_tuple(get_column_contract(node.data.nodeType, node.data.config))
    return _projection_contract_from_registered(node, registered)


def _projection_contract_from_registered(
    node: GraphNode,
    registered: Contract,
) -> Contract:
    """Apply projection-specific interpretation to one registered contract."""
    contract = overlay_declared_contract(node, registered)
    if node.data.nodeType == NodeType.POLARS and not _has_user_polars_code(node):
        # A code-free Polars node is a passthrough: undeclared sides read and
        # produce nothing. This is a projection default, not a config-derived
        # builder side, so it must not displace the node's declaration.
        contract = contract.fill_opaque_sides(Contract(inputs=frozenset(), outputs=frozenset()))
    return contract


# Builders that run their optional ``code`` over their own output as ``df``.
_POST_CODE_BUILDER_TYPES = frozenset(
    {NodeType.MODEL_SCORE, NodeType.RATING_STEP, NodeType.SCENARIO_EXPANDER}
)


def _builder_post_code(node: GraphNode) -> str | None:
    """Return a builder's post-code, which runs over the builder output as ``df``."""
    if node.data.nodeType not in _POST_CODE_BUILDER_TYPES:
        return None
    code = str(node.data.config.get("code") or "").strip()
    return code or None


def _pre_post_code_contract(node: GraphNode, effective: Contract) -> Contract:
    """Return the contract of a builder's output before its post-code runs.

    A declared contract describes the whole node, post-code included, so it
    cannot fill the builder's own sides. A Rating Step or Scenario Expander
    derives its code-free contract from config alone. A Model Score's registered
    output is already the scorer's, and its unknown model inputs are filled from
    the declared inputs rather than by loading the model.
    """
    if node.data.nodeType is NodeType.MODEL_SCORE:
        return effective
    config = {key: value for key, value in node.data.config.items() if key != "code"}
    return Contract.from_tuple(get_column_contract(node.data.nodeType, config))


def ratebook_factor_required_columns(config: Mapping[str, Any]) -> frozenset[str]:
    """Return factor-side columns required from a ratebook banding source."""
    columns: set[str] = {str(config.get("quote_id", "quote_id"))}
    raw_factor_columns = config.get("factor_columns") or []
    for group in raw_factor_columns:
        if isinstance(group, str) or not isinstance(group, Iterable):
            raise ValueError("ratebook factor_columns must be lists of column names")
        for column in group:
            if not isinstance(column, str) or not column:
                raise ValueError("ratebook factor_columns must contain non-empty string names")
            columns.add(column)
    return frozenset(columns)


@dataclass(frozen=True)
class OptimiserParentDemandRule:
    """Projection rule for configured optimiser parent ownership."""

    name: str = "optimiser_parent_demand"

    def parent_demands(
        self,
        node: GraphNode,
        incoming_edges: Iterable[GraphEdge],
        node_map: Mapping[str, GraphNode],
        my_needed: set[str] | None,
        seeded_required: Mapping[str, set[str] | AllExceptColumns],
        *,
        submodels: Mapping[str, Any] | None = None,
    ) -> ParentDemandResult | None:
        incoming = list(incoming_edges)
        parent_set = {edge.source for edge in incoming}
        edge_count_by_parent: dict[str, int] = {}
        for edge in incoming:
            edge_count_by_parent[edge.source] = edge_count_by_parent.get(edge.source, 0) + 1
        if not parent_set or node.data.nodeType != NodeType.OPTIMISER:
            return None

        config = node.data.config
        configured_data_input = config.get("data_input")
        banding_source = config.get("banding_source")
        named_edges = [
            (
                edge,
                edge_input_name(
                    edge,
                    node_map[edge.source],
                    submodels=submodels,
                ),
            )
            for edge in incoming
        ]

        if configured_data_input in (None, "") and len(named_edges) == 1:
            data_edge = named_edges[0][0]
        elif not isinstance(configured_data_input, str) or not configured_data_input:
            raise ContractMismatchError(
                "Multi-parent optimiser projection requires a configured data_input.",
                node_id=node.id,
                node_type=node.data.nodeType.value,
                incoming_input_names=sorted(name for _edge, name in named_edges),
            )
        else:
            data_matches = [edge for edge, name in named_edges if name == configured_data_input]
            if len(data_matches) != 1:
                raise ContractMismatchError(
                    "Configured optimiser data_input is not one exact connected input name.",
                    node_id=node.id,
                    node_type=node.data.nodeType.value,
                    data_input=configured_data_input,
                    incoming_input_names=sorted(name for _edge, name in named_edges),
                )
            data_edge = data_matches[0]

        data_parent = data_edge.source
        seeded_data_input = seeded_required.get(data_parent)
        data_input_columns = (
            set(seeded_data_input)
            if my_needed is None and isinstance(seeded_data_input, set)
            else my_needed
        )
        by_parent: dict[str, set[str] | None] = {parent_id: set() for parent_id in parent_set}
        by_parent[data_parent] = None if data_input_columns is None else set(data_input_columns)

        if config.get("mode", "online") == "ratebook":
            if not isinstance(banding_source, str) or not banding_source:
                raise ContractMismatchError(
                    "Ratebook optimiser projection requires a configured banding_source.",
                    node_id=node.id,
                    node_type=node.data.nodeType.value,
                    incoming_input_names=sorted(name for _edge, name in named_edges),
                )
            banding_matches = [edge for edge, name in named_edges if name == banding_source]
            if len(banding_matches) != 1:
                raise ContractMismatchError(
                    "Configured ratebook banding_source is not one exact connected input name.",
                    node_id=node.id,
                    node_type=node.data.nodeType.value,
                    banding_source=banding_source,
                    incoming_input_names=sorted(name for _edge, name in named_edges),
                )
            banding_parent = banding_matches[0].source
            factor_columns = ratebook_factor_required_columns(config)
            existing = by_parent[banding_parent]
            by_parent[banding_parent] = None if existing is None else set(existing) | factor_columns

        # ParentDemandResult is keyed by source node, so it cannot express
        # different column sets for parallel frames from one multi-frame
        # source. Keep those physical edges full-width after validating their
        # exact selectors instead of applying either frame's demand to both.
        for parent_id, edge_count in edge_count_by_parent.items():
            if edge_count > 1:
                by_parent[parent_id] = None

        return ParentDemandResult(
            default=None,
            by_parent=dict(by_parent),
            rule_name=self.name,
        )


_OPTIMISER_PARENT_DEMAND_RULE = OptimiserParentDemandRule()
POLARS_COLUMN_LINEAGE_RULE_NAME = "polars_column_lineage"


def parent_demands_for_node(
    node: GraphNode,
    incoming_edges: Iterable[GraphEdge],
    node_map: Mapping[str, GraphNode],
    my_needed: set[str] | None,
    seeded_required: Mapping[str, set[str] | AllExceptColumns],
    *,
    submodels: Mapping[str, Any] | None = None,
) -> ParentDemandResult | None:
    """Return node-specific parent demands that the generic algebra cannot infer.

    Return optimiser-specific parent demands when configured.
    """
    return _OPTIMISER_PARENT_DEMAND_RULE.parent_demands(
        node,
        incoming_edges,
        node_map,
        my_needed,
        seeded_required,
        submodels=submodels,
    )


@dataclass(frozen=True)
class OpaqueContractRule:
    """Projection rule for nodes whose column contract is opaque."""

    name: str = "opaque_contract"

    def parent_demands(
        self,
        node: GraphNode,
        parent_ids: Iterable[str],
    ) -> ParentDemandResult:
        parent_set = set(parent_ids)
        parent_inputs = declared_inputs_by_parent(node, parent_set)
        if parent_inputs is not None:
            raise ContractMismatchError(
                "Fan-in projection contract requires concrete 'inputs' "
                "and 'outputs' on the node contract.",
                node_id=node.id,
                node_type=node.data.nodeType.value,
            )
        if len(parent_set) > 1 and node.data.nodeType == NodeType.POLARS:
            return _unprojected_boundary_demands()
        if _user_code_has_unbounded_projection_contract(node):
            return _unprojected_boundary_demands()
        return ParentDemandResult(
            default=None,
            by_parent={},
            rule_name=self.name,
        )


_OPAQUE_CONTRACT_RULE = OpaqueContractRule()


_USER_CODE_NODE_TYPES = frozenset(
    {
        NodeType.API_INPUT,
        NodeType.DATA_INPUT,
        NodeType.EXTERNAL_FILE,
        NodeType.MODEL_SCORE,
        NodeType.OPTIMISER_APPLY,
        NodeType.POLARS,
        NodeType.RATING_STEP,
        NodeType.SCENARIO_EXPANDER,
    }
)


def _has_projection_user_code(node: GraphNode) -> bool:
    if node.data.nodeType not in _USER_CODE_NODE_TYPES:
        return False
    code = node.data.config.get("code")
    return isinstance(code, str) and bool(code.strip())


def _has_user_polars_code(node: GraphNode) -> bool:
    if node.data.nodeType != NodeType.POLARS:
        return False
    code = node.data.config.get("code")
    return isinstance(code, str) and bool(code.strip())


def _user_code_has_unbounded_projection_contract(node: GraphNode) -> bool:
    if not _has_projection_user_code(node):
        return False
    if node.data.nodeType == NodeType.DATA_INPUT and source_user_code_preserves_column_projection(
        str(node.data.config.get("code") or "")
    ):
        return False
    produced, referenced = projection_contract(node).to_tuple()
    return produced is None or referenced is None


def _must_run_source_user_code_unprojected(node: GraphNode, demand: set[str] | None) -> bool:
    """Return whether a source must scan full width before post-load code.

    Source post-load code runs inside the source builder before any downstream
    edge projection.  If that code may inspect columns outside the downstream
    demand, pushing scan projection into the builder would be incorrect.  The
    safe bounded strategy is to scan full width, run the source code, then let
    downstream edges/checkpoints narrow the frame again.  A Data Input whose
    code the column lineage model proves for a known *demand* is the exception:
    its builder reads exactly the columns that code consumes.  An External File
    opens no scan, so its code is analysed as a transform instead.
    """
    if node.data.nodeType not in {
        NodeType.API_INPUT,
        NodeType.DATA_INPUT,
    } or not _user_code_has_unbounded_projection_contract(node):
        return False
    return not (
        node.data.nodeType == NodeType.DATA_INPUT
        and demand is not None
        and source_user_code_scan_columns(
            node.data.config,
            str(node.data.config["code"]),
            demand,
            source_columns=None,
        )
        is not None
    )


def opaque_contract_demands_for_node(
    node: GraphNode,
    parent_ids: Iterable[str],
) -> ParentDemandResult:
    """Return parent demand for an opaque projection contract.

    Opaque nodes usually force their parents opaque.  The exception is a
    malformed fan-in declaration: if a node declares `inputs_by_parent`, then
    projection also needs concrete `inputs` and `outputs` so it can decide what
    each parent owns.
    """
    return _OPAQUE_CONTRACT_RULE.parent_demands(node, parent_ids)


@dataclass(frozen=True)
class PolarsFanInRule:
    """Projection rule for concrete multi-parent Polars fan-in nodes."""

    name: str = "polars_fan_in"

    def parent_demands(
        self,
        node: GraphNode,
        parent_ids: Iterable[str],
        base_contribution: set[str],
        referenced: set[str],
    ) -> ParentDemandResult | None:
        parent_set = set(parent_ids)
        if len(parent_set) <= 1 or node.data.nodeType != NodeType.POLARS:
            return None

        parent_inputs = declared_inputs_by_parent(node, parent_set)
        if parent_inputs is None:
            return _unprojected_boundary_demands()

        opaque_parent_ids = [
            parent_id
            for parent_id, parent_columns in parent_inputs.items()
            if parent_columns is None
        ]
        if opaque_parent_ids:
            raise ContractMismatchError(
                "Fan-in projection contract inputs_by_parent must be fully concrete.",
                node_id=node.id,
                node_type=node.data.nodeType.value,
                opaque_parent_ids=sorted(opaque_parent_ids),
            )

        covered: set[str] = set()
        by_parent: dict[str, set[str]] = {}
        for parent_id, parent_columns in parent_inputs.items():
            assert parent_columns is not None
            covered |= parent_columns
            by_parent[parent_id] = base_contribution & parent_columns

        inference = _join_calls_for_parent_inputs(node, parent_set)
        if inference.unprovable_rule is not None:
            # The join cannot be proven mechanically, so keep the full-width
            # boundary and record why rather than guessing a narrower demand.
            return _unprojected_boundary_demands(
                rule_name=inference.unprovable_rule,
                message=inference.unprovable_message,
            )
        joins = inference.joins
        for join in joins:
            for left_key, right_key in join.key_pairs:
                by_parent[join.left_parent].add(left_key)
                by_parent[join.right_parent].add(right_key)

        missing = base_contribution - covered
        if missing:
            join_demands, handled_missing = join_parent_demands(
                node,
                parent_set,
                missing,
            )
            for parent_id, extra_columns in join_demands.items():
                parent_demand = by_parent[parent_id]
                assert parent_demand is not None
                parent_demand |= extra_columns
            missing -= handled_missing
            if missing:
                passthrough_parent = simple_left_join_passthrough_parent(joins)
                if passthrough_parent is None:
                    passthrough_parent = unambiguous_passthrough_parent(
                        parent_inputs,
                        referenced,
                    )
                if passthrough_parent is not None:
                    parent_demand = by_parent[passthrough_parent]
                    assert parent_demand is not None
                    parent_demand |= missing
                    missing = set()
            if missing:
                raise ContractMismatchError(
                    "Fan-in projection contract does not cover columns required by the node.",
                    node_id=node.id,
                    node_type=node.data.nodeType.value,
                    missing=sorted(missing),
                    declared_inputs_by_parent={
                        pid: sorted(cols) if cols is not None else None
                        for pid, cols in parent_inputs.items()
                    },
                )

        result_by_parent: dict[str, set[str] | None] = dict(by_parent)
        return ParentDemandResult(
            default=None,
            by_parent=result_by_parent,
            rule_name=self.name,
        )


_POLARS_FAN_IN_RULE = PolarsFanInRule()


def fan_in_demands_for_node(
    node: GraphNode,
    parent_ids: Iterable[str],
    base_contribution: set[str],
    referenced: set[str],
) -> ParentDemandResult | None:
    """Return routed demands for concrete multi-parent fan-in nodes."""
    return _POLARS_FAN_IN_RULE.parent_demands(
        node,
        parent_ids,
        base_contribution,
        referenced,
    )


@dataclass(frozen=True)
class EdgeJoinFanInRule:
    """Projection rule for opaque-contract edge-join fan-in nodes.

    The edge-join's *output* schema is opaque (it depends on both inputs), so a
    demanded column cannot be attributed using the join's own contract. Instead
    the rule routes demand through the shared
    :func:`haute._edge_join.narrow_join_parent_demand`, using the parents'
    produced-column contracts as the per-parent schemas: the join keys are
    demanded from both roles, each non-key demanded column is routed to the
    parent that produces it, and suffix-renamed duplicates (``<col><suffix>``)
    are mapped back to ``<col>`` on both parents so Polars still emits them.

    When the join cannot be narrowed mechanically — an opaque parent, a
    ``cross``/``full``/``right`` join, or a demanded column no parent produces —
    the rule keeps the FULL-WIDTH boundary (:func:`_unprojected_boundary_demands`)
    rather than guess, so it can never silently drop a needed column.
    """

    name: str = "edge_join_fan_in"

    def parent_demands(
        self,
        node: GraphNode,
        incoming_edges: Iterable[GraphEdge],
        base_contribution: set[str],
        referenced: set[str],
        parent_produced: Mapping[str, set[str] | None],
    ) -> ParentDemandResult:
        _ = referenced
        incoming = list(incoming_edges)
        parent_set = {edge.source for edge in incoming}
        # Resolve roles and validate config; fail loudly on stale/missing roles.
        base_index, join_index = resolve_edge_join_role_indices(
            [edge.targetHandle for edge in incoming],
        )
        base_parent = incoming[base_index].source
        join_parent = incoming[join_index].source

        if base_parent == join_parent:
            # Two distinct frames from a multi-output node can legitimately
            # occupy the two roles. Parent-level contracts cannot express a
            # different demand for each physical edge, so validate the join
            # and keep both edges full-width instead of overwriting one role's
            # demand with the other in ``by_parent``.
            build_edge_join_kwargs(node.data.config)
            return ParentDemandResult(
                default=None,
                by_parent={base_parent: None},
                rule_name=self.name,
            )

        # An opaque parent (produced is None) cannot prove column ownership, so
        # keep the boundary full-width rather than risk dropping a column.
        if any(parent_produced.get(parent_id) is None for parent_id in parent_set):
            return _unprojected_boundary_demands()

        kwargs = build_edge_join_kwargs(node.data.config)
        base_keys, join_keys = edge_join_key_columns_by_role(node.data.config)
        routed = narrow_join_parent_demand(
            base_contribution,
            left_keys=set(base_keys),
            right_keys=set(join_keys),
            left_schema=set(parent_produced[base_parent] or set()),
            right_schema=set(parent_produced[join_parent] or set()),
            how=str(kwargs["how"]),
            suffix=str(kwargs["suffix"]),
        )
        if routed is None:
            # cross/full/right joins, or a demanded column that can't be mapped
            # to a producing parent — keep full width rather than guess/drop.
            return _unprojected_boundary_demands()
        base_demand, join_demand = routed

        by_parent: dict[str, set[str] | None] = {parent_id: set() for parent_id in parent_set}
        by_parent[base_parent] = base_demand
        by_parent[join_parent] = join_demand
        return ParentDemandResult(
            default=None,
            by_parent=by_parent,
            rule_name=self.name,
        )


_EDGE_JOIN_FAN_IN_RULE = EdgeJoinFanInRule()


def _parent_produced_columns(parent: GraphNode) -> set[str] | None:
    """Return the columns a parent node produces, or ``None`` if opaque."""
    produced, _ = projection_contract(parent).to_tuple()
    return produced


def edge_join_fan_in_demands_for_node(
    node: GraphNode,
    incoming_edges: Iterable[GraphEdge],
    base_contribution: set[str],
    referenced: set[str],
    parent_produced: Mapping[str, set[str] | None],
) -> ParentDemandResult | None:
    """Return routed demands for an opaque-contract edge-join fan-in node."""
    incoming = list(incoming_edges)
    if node.data.nodeType != NodeType.EDGE_JOIN or len(incoming) <= 1:
        return None
    return _EDGE_JOIN_FAN_IN_RULE.parent_demands(
        node,
        incoming,
        base_contribution,
        referenced,
        parent_produced,
    )


_SOURCE_SCAN_RULE_NAME = "source_scan"
_GENERIC_CONTRACT_RULE_NAME = "generic_contract"
_MODEL_SCORE_BUILDER_DEMAND_RULE_NAME = "model_score_builder_demand"
_LIVE_SWITCH_PRUNE_RULE_NAME = "live_switch_prune"
UNPROJECTED_STREAMING_BOUNDARY_RULE_NAME = "unprojected_streaming_boundary"
RUNTIME_INFERRED_STREAMING_RULE_NAME = "runtime_inferred_streaming"


def _coverage(
    node_type: NodeType,
    *rules: str,
    opaque: bool = False,
    note: str = "",
) -> ProjectionRuleCoverage:
    return ProjectionRuleCoverage(
        node_type=node_type,
        rules=frozenset(rules),
        opaque=opaque,
        note=note,
    )


_PROJECTION_RULE_COVERAGE_BY_NODE_TYPE: Mapping[NodeType, ProjectionRuleCoverage] = (
    MappingProxyType(
        {
            NodeType.API_INPUT: _coverage(NodeType.API_INPUT, _SOURCE_SCAN_RULE_NAME),
            NodeType.DATA_INPUT: _coverage(NodeType.DATA_INPUT, _SOURCE_SCAN_RULE_NAME),
            NodeType.EXTERNAL_FILE: _coverage(
                NodeType.EXTERNAL_FILE,
                _GENERIC_CONTRACT_RULE_NAME,
                POLARS_COLUMN_LINEAGE_RULE_NAME,
            ),
            NodeType.CONSTANT: _coverage(NodeType.CONSTANT, _SOURCE_SCAN_RULE_NAME),
            NodeType.POLARS: _coverage(
                NodeType.POLARS,
                _GENERIC_CONTRACT_RULE_NAME,
                _POLARS_FAN_IN_RULE.name,
                POLARS_COLUMN_LINEAGE_RULE_NAME,
            ),
            NodeType.EDGE_JOIN: _coverage(
                NodeType.EDGE_JOIN,
                _EDGE_JOIN_FAN_IN_RULE.name,
                note=(
                    "edge joins keep an opaque column contract but route demand "
                    "concretely via parent produced-column ownership"
                ),
            ),
            NodeType.DATA_OUTPUT: _coverage(NodeType.DATA_OUTPUT, _GENERIC_CONTRACT_RULE_NAME),
            NodeType.BANDING: _coverage(NodeType.BANDING, _GENERIC_CONTRACT_RULE_NAME),
            NodeType.RATING_STEP: _coverage(NodeType.RATING_STEP, _GENERIC_CONTRACT_RULE_NAME),
            NodeType.OUTPUT: _coverage(NodeType.OUTPUT, _GENERIC_CONTRACT_RULE_NAME),
            NodeType.EXPLORE: _coverage(NodeType.EXPLORE, _GENERIC_CONTRACT_RULE_NAME),
            NodeType.MODELLING: _coverage(NodeType.MODELLING, _GENERIC_CONTRACT_RULE_NAME),
            NodeType.SCENARIO_EXPANDER: _coverage(
                NodeType.SCENARIO_EXPANDER,
                _GENERIC_CONTRACT_RULE_NAME,
            ),
            NodeType.OPTIMISER_APPLY: _coverage(
                NodeType.OPTIMISER_APPLY,
                _GENERIC_CONTRACT_RULE_NAME,
            ),
            NodeType.MODEL_SCORE: _coverage(
                NodeType.MODEL_SCORE,
                _MODEL_SCORE_BUILDER_DEMAND_RULE_NAME,
            ),
            NodeType.OPTIMISER: _coverage(
                NodeType.OPTIMISER,
                _GENERIC_CONTRACT_RULE_NAME,
                _OPTIMISER_PARENT_DEMAND_RULE.name,
            ),
            NodeType.LIVE_SWITCH: _coverage(
                NodeType.LIVE_SWITCH,
                _GENERIC_CONTRACT_RULE_NAME,
                _LIVE_SWITCH_PRUNE_RULE_NAME,
            ),
            NodeType.SUBMODEL: _coverage(
                NodeType.SUBMODEL,
                _OPAQUE_CONTRACT_RULE.name,
                opaque=True,
                note="submodel boundaries are opaque until expanded into a concrete graph",
            ),
            NodeType.SUBMODEL_PORT: _coverage(
                NodeType.SUBMODEL_PORT,
                _OPAQUE_CONTRACT_RULE.name,
                opaque=True,
                note="submodel ports inherit their concrete contract from the expanded subgraph",
            ),
        }
    )
)


def projection_rule_coverage_by_node_type() -> Mapping[NodeType, ProjectionRuleCoverage]:
    """Return the immutable node-type projection coverage registry."""
    return _PROJECTION_RULE_COVERAGE_BY_NODE_TYPE


def validate_projection_rule_coverage(
    coverage: Mapping[NodeType, ProjectionRuleCoverage] | None = None,
) -> None:
    """Fail loudly if projection rule coverage drifts from known node types."""
    registry = coverage or _PROJECTION_RULE_COVERAGE_BY_NODE_TYPE
    expected = set(NodeType)
    observed = set(registry)
    missing = expected - observed
    extra = observed - expected
    if missing or extra:
        raise RuntimeError(
            "Projection rule coverage must mention every node type exactly once. "
            f"Missing={sorted(node.value for node in missing)}; "
            f"extra={sorted(node.value for node in extra)}"
        )

    for node_type, entry in registry.items():
        if entry.node_type != node_type:
            raise RuntimeError(
                "Projection rule coverage entry is keyed under the wrong node type. "
                f"key={node_type.value!r}, entry={entry.node_type.value!r}"
            )
        if not entry.rules:
            raise RuntimeError(
                f"Projection rule coverage for node type {node_type.value!r} has no rules."
            )
        if entry.opaque and _OPAQUE_CONTRACT_RULE.name not in entry.rules:
            raise RuntimeError(
                f"Opaque projection coverage for node type {node_type.value!r} "
                f"must include {_OPAQUE_CONTRACT_RULE.name!r}."
            )
        if not entry.opaque and entry.rules == frozenset({_OPAQUE_CONTRACT_RULE.name}):
            raise RuntimeError(
                f"Node type {node_type.value!r} is implicitly opaque; mark it opaque=True "
                "or attach a concrete projection rule."
            )


validate_projection_rule_coverage()


def declared_inputs_by_parent(
    node: GraphNode,
    parent_ids: Iterable[str],
) -> dict[str, set[str] | None] | None:
    """Return explicit fan-in ownership metadata for *node*, if declared."""
    declared_raw = node.data.config.get("contract")
    if declared_raw is None:
        return None
    try:
        declared = Contract.from_user_declared(declared_raw)
    except ValueError as exc:
        raise ContractMismatchError(
            "Node contract annotation is malformed and cannot be interpreted.",
            node_id=node.id,
            node_type=node.data.nodeType.value,
            reason=str(exc),
        ) from exc
    if declared is None or declared.inputs_by_parent is None:
        return None

    parent_set = set(parent_ids)
    declared_set = set(declared.inputs_by_parent)
    unknown = declared_set - parent_set
    missing = parent_set - declared_set
    if unknown or missing:
        raise ContractMismatchError(
            "Fan-in projection contract references unknown parent(s) or omits incoming parent(s).",
            node_id=node.id,
            node_type=node.data.nodeType.value,
            unknown_parent_ids=sorted(unknown),
            missing_parent_ids=sorted(missing),
            incoming_parent_ids=sorted(parent_set),
        )

    return {
        parent_id: None if columns is None else set(columns)
        for parent_id, columns in declared.inputs_by_parent.items()
    }


def unambiguous_passthrough_parent(
    parent_inputs: Mapping[str, set[str] | None],
    referenced: set[str],
) -> str | None:
    """Return the sole parent whose declaration contains only node input keys."""
    candidates = [
        parent_id
        for parent_id, columns in parent_inputs.items()
        if columns is not None and columns <= referenced
    ]
    if len(candidates) != 1:
        return None
    return candidates[0]


def simple_left_join_passthrough_parent(
    joins: Iterable[_JoinCallInfo],
) -> str | None:
    """Return the left parent when simple Polars joins prove passthrough ownership."""
    passthrough_parents: set[str] = set()
    saw_join = False
    for join in joins:
        saw_join = True
        if join.how.lower() not in {"left", "semi", "anti"}:
            return None
        passthrough_parents.add(join.left_parent)
    if not saw_join or len(passthrough_parents) != 1:
        return None
    return next(iter(passthrough_parents))


def _literal_string(node: ast.AST) -> str | None:
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    return None


def _literal_string_tuple(node: ast.AST) -> tuple[str, ...] | None:
    value = _literal_string(node)
    if value is not None:
        return (value,)
    if not isinstance(node, (ast.List, ast.Tuple)):
        return None
    values: list[str] = []
    for element in node.elts:
        item = _literal_string(element)
        if item is None:
            return None
        values.append(item)
    return tuple(values)


class _JoinCallInfo(NamedTuple):
    left_parent: str
    right_parent: str
    how: str
    suffix: str
    key_pairs: tuple[tuple[str, str], ...] = ()


def _join_key_pairs_from_call(ast_node: ast.Call) -> tuple[tuple[str, str], ...]:
    on_columns: tuple[str, ...] | None = None
    left_columns: tuple[str, ...] | None = None
    right_columns: tuple[str, ...] | None = None
    for kw in ast_node.keywords:
        if kw.arg == "on":
            on_columns = _literal_string_tuple(kw.value)
        elif kw.arg == "left_on":
            left_columns = _literal_string_tuple(kw.value)
        elif kw.arg == "right_on":
            right_columns = _literal_string_tuple(kw.value)

    if on_columns is not None:
        return tuple((column, column) for column in on_columns)
    if (
        left_columns is not None
        and right_columns is not None
        and len(left_columns) == len(right_columns)
    ):
        return tuple(zip(left_columns, right_columns, strict=True))
    return ()


def _parent_id_from_expr(
    expr: ast.AST,
    aliases: Mapping[str, str],
    parent_set: set[str],
) -> str | None:
    if not isinstance(expr, ast.Name):
        return None
    parent_id = aliases.get(expr.id, expr.id)
    return parent_id if parent_id in parent_set else None


def _parent_aliases(tree: ast.AST, parent_set: set[str]) -> dict[str, str]:
    aliases: dict[str, str] = {}
    for ast_node in ast.walk(tree):
        if not isinstance(ast_node, ast.Assign) or not isinstance(ast_node.value, ast.Name):
            continue
        parent_id = aliases.get(ast_node.value.id, ast_node.value.id)
        if parent_id not in parent_set:
            continue
        for target in ast_node.targets:
            if isinstance(target, ast.Name):
                aliases[target.id] = parent_id
    return aliases


FAN_IN_JOIN_UNPARSED_RULE_NAME = "fan_in_join_unparsed"
FAN_IN_JOIN_DYNAMIC_ARGUMENTS_RULE_NAME = "fan_in_join_dynamic_arguments"


@dataclass(frozen=True)
class _JoinInference:
    """Inferred fan-in joins plus why the inference could not be proven."""

    joins: list[_JoinCallInfo]
    unprovable_rule: str | None = None
    unprovable_message: str | None = None


def _join_calls_for_parent_inputs(
    node: GraphNode,
    parent_ids: Iterable[str],
) -> _JoinInference:
    """Infer simple Polars join calls between incoming parents from node code."""
    code = node.data.config.get("code")
    if not isinstance(code, str) or ".join" not in code:
        return _JoinInference(joins=[])
    parent_set = set(parent_ids)
    try:
        tree = ast.parse(code)
    except SyntaxError as exc:
        return _JoinInference(
            joins=[],
            unprovable_rule=FAN_IN_JOIN_UNPARSED_RULE_NAME,
            unprovable_message=(
                f"fan-in join projection for node {node.id!r} could not be parsed: {exc}"
            ),
        )

    aliases = _parent_aliases(tree, parent_set)
    joins: list[_JoinCallInfo] = []
    dynamic_arguments = False

    for ast_node in ast.walk(tree):
        if not isinstance(ast_node, ast.Call):
            continue
        func = ast_node.func
        if not isinstance(func, ast.Attribute) or func.attr != "join":
            continue
        if not ast_node.args:
            continue

        left_parent = _parent_id_from_expr(func.value, aliases, parent_set)
        right_parent = _parent_id_from_expr(ast_node.args[0], aliases, parent_set)
        if left_parent is None or right_parent is None or left_parent == right_parent:
            continue

        how = "inner"
        suffix = "_right"
        unsupported_dynamic_keyword = False
        for kw in ast_node.keywords:
            if kw.arg == "how":
                literal_how = _literal_string(kw.value)
                if literal_how is None:
                    unsupported_dynamic_keyword = True
                else:
                    how = literal_how
            elif kw.arg == "suffix":
                literal_suffix = _literal_string(kw.value)
                if literal_suffix is None:
                    unsupported_dynamic_keyword = True
                else:
                    suffix = literal_suffix
        if unsupported_dynamic_keyword:
            dynamic_arguments = True
            continue
        key_pairs = () if how == "cross" else _join_key_pairs_from_call(ast_node)
        joins.append(
            _JoinCallInfo(
                left_parent=left_parent,
                right_parent=right_parent,
                how=how,
                suffix=suffix,
                key_pairs=key_pairs,
            )
        )
    if dynamic_arguments:
        return _JoinInference(
            joins=joins,
            unprovable_rule=FAN_IN_JOIN_DYNAMIC_ARGUMENTS_RULE_NAME,
            unprovable_message=(
                f"fan-in join projection for node {node.id!r} requires literal how/suffix arguments"
            ),
        )
    return _JoinInference(joins=joins)


def simple_join_calls_for_parent_inputs(
    node: GraphNode,
    parent_ids: Iterable[str],
) -> tuple[_JoinCallInfo, ...]:
    """Return simple inferred Polars joins between incoming parent frames."""
    return tuple(_join_calls_for_parent_inputs(node, parent_ids).joins)


def join_parent_demands(
    node: GraphNode,
    parent_ids: Iterable[str],
    output_columns: set[str],
) -> tuple[dict[str, set[str]], set[str]]:
    """Return parent input columns inferred from simple Polars join output columns."""
    joins = _join_calls_for_parent_inputs(node, parent_ids).joins
    if not joins:
        return {}, set()

    demands: dict[str, set[str]] = {}
    handled: set[str] = set()

    for join in joins:
        if not join.suffix:
            continue
        for column in output_columns - handled:
            if not column.endswith(join.suffix):
                continue
            parent_column = column[: -len(join.suffix)]
            if not parent_column:
                continue
            demands.setdefault(join.left_parent, set()).add(parent_column)
            demands.setdefault(join.right_parent, set()).add(parent_column)
            handled.add(column)

    return demands, handled


def _children_of(
    order: list[str],
    parents_of: Mapping[str, Iterable[str]],
) -> dict[str, list[str]]:
    children_of: dict[str, list[str]] = {node_id: [] for node_id in order}
    for child_id, parent_ids in parents_of.items():
        for parent_id in parent_ids:
            if parent_id in children_of:
                children_of[parent_id].append(child_id)
    return children_of


def prune_live_switch_edges(
    edges: list[GraphEdge],
    node_map: Mapping[str, GraphNode],
    source: str,
    *,
    submodels: Mapping[str, Any] | None = None,
) -> list[GraphEdge]:
    """Remove edges to live-switch nodes from inputs inactive for *source*."""
    switch_nodes = {
        nid: node for nid, node in node_map.items() if node.data.nodeType == NodeType.LIVE_SWITCH
    }
    if not switch_nodes:
        return edges

    exclude_edge_ids: set[str] = set()
    for nid, node in switch_nodes.items():
        input_scenario_map = node.data.config.get("input_scenario_map", {})
        if not input_scenario_map:
            continue
        if source not in input_scenario_map.values():
            continue
        for edge in edges:
            if edge.target != nid:
                continue
            parent = node_map.get(edge.source)
            if parent is None:
                continue
            input_name = edge_input_name(edge, parent, submodels=submodels)
            mapped = input_scenario_map.get(input_name)
            if mapped is not None and mapped != source:
                exclude_edge_ids.add(edge.id)

    if not exclude_edge_ids:
        return edges
    return [edge for edge in edges if edge.id not in exclude_edge_ids]


def prepare_graph(
    graph: PipelineGraph,
    target_node_id: str | None = None,
    *,
    source: str = "live",
) -> PreparedGraph:
    """Prepare graph lookups used by projection planning."""
    node_map = graph.node_map
    edges = prune_live_switch_edges(
        graph.edges,
        node_map,
        source,
        submodels=graph.submodels,
    )

    all_ids = set(node_map)
    if target_node_id:
        needed = ancestors(target_node_id, edges, all_ids)
    else:
        needed = all_ids

    relevant_edges = [edge for edge in edges if edge.source in needed and edge.target in needed]
    order = topo_sort_ids([node_id for node_id in node_map if node_id in needed], relevant_edges)
    parents_of = build_parents_of(relevant_edges, set(order))
    id_to_name = {node_id: _sanitize_func_name(node_map[node_id].data.label) for node_id in order}
    return PreparedGraph(
        node_map=node_map,
        order=order,
        parents_of=parents_of,
        id_to_name=id_to_name,
        relevant_edges=relevant_edges,
        submodels=graph.submodels,
    )


@dataclass(frozen=True, slots=True)
class _LineageInputBinding:
    name: str
    edge: GraphEdge
    key: ProjectionEdgeKey
    exact_columns: frozenset[str] | None


def _projection_edges(
    order: Iterable[str],
    children_of: Mapping[str, Iterable[str]],
    relevant_edges: Iterable[GraphEdge] | None,
) -> tuple[GraphEdge, ...]:
    """Return authoritative edges, synthesising identity for legacy callers."""
    known = set(order)
    if relevant_edges is not None:
        return tuple(
            edge for edge in relevant_edges if edge.source in known and edge.target in known
        )

    # ``compute_prepared_plan`` predates port-aware planning and remains a
    # useful low-level API for adjacency-only tests/callers.  Give every
    # adjacency occurrence a deterministic complete identity.  Runtime paths
    # always pass the real GraphEdge objects.
    occurrences: dict[tuple[str, str], int] = {}
    synthesised: list[GraphEdge] = []
    for source in order:
        for target in children_of.get(source, ()):
            if target not in known:
                continue
            pair = (source, target)
            ordinal = occurrences.get(pair, 0)
            occurrences[pair] = ordinal + 1
            edge_id = f"e_{source}_{target}" if ordinal == 0 else f"e_{source}_{target}_{ordinal}"
            synthesised.append(GraphEdge(id=edge_id, source=source, target=target))
    return tuple(synthesised)


def _edges_by_endpoint(
    order: Iterable[str],
    edges: Iterable[GraphEdge],
) -> tuple[dict[str, list[GraphEdge]], dict[str, list[GraphEdge]]]:
    incoming: dict[str, list[GraphEdge]] = {node_id: [] for node_id in order}
    outgoing: dict[str, list[GraphEdge]] = {node_id: [] for node_id in order}
    for edge in edges:
        if edge.target in incoming and edge.source in outgoing:
            incoming[edge.target].append(edge)
            outgoing[edge.source].append(edge)
    return incoming, outgoing


def _exact_columns_for_parent_edge(
    edge: GraphEdge,
    node_map: Mapping[str, GraphNode],
    exact_output_by_node: Mapping[str, frozenset[str]],
    known_port_columns: Mapping[tuple[str, str | None], frozenset[str]] | None = None,
) -> frozenset[str] | None:
    known = (known_port_columns or {}).get((edge.source, edge.sourceHandle))
    if known is not None:
        return known
    parent = node_map[edge.source]
    if parent.data.nodeType is NodeType.API_INPUT:
        declared = _declared_api_input_port_columns(parent)
        if declared is None or edge.sourceHandle is None:
            return None
        return declared.get(edge.sourceHandle)
    return exact_output_by_node.get(edge.source)


# Node kinds whose ``code`` runs over their incoming frames and is analysed with
# compositional column lineage. An External File also binds its loaded
# artifact as ``obj``, which lineage treats as a value like any preamble name.
_LINEAGE_CODE_NODE_TYPES = frozenset({NodeType.POLARS, NodeType.EXTERNAL_FILE})


def _lineage_input_bindings(
    node: GraphNode,
    incoming_edges: Iterable[GraphEdge],
    node_map: Mapping[str, GraphNode],
    exact_output_by_node: Mapping[str, frozenset[str]],
    *,
    submodels: Mapping[str, Any] | None = None,
    known_port_columns: Mapping[tuple[str, str | None], frozenset[str]] | None = None,
) -> tuple[_LineageInputBinding, ...] | None:
    by_name: dict[str, _LineageInputBinding] = {}
    for edge in incoming_edges:
        try:
            name = edge_input_name(
                edge,
                node_map[edge.source],
                submodels=submodels,
            )
        except (KeyError, ValueError):
            return None
        binding = _LineageInputBinding(
            name=name,
            edge=edge,
            key=ProjectionEdgeKey.from_edge(edge),
            exact_columns=_exact_columns_for_parent_edge(
                edge,
                node_map,
                exact_output_by_node,
                known_port_columns,
            ),
        )
        previous = by_name.get(name)
        if previous is not None and previous.key != binding.key:
            return None
        by_name[name] = binding

    raw_mapping = node.data.config.get("inputMapping")
    if raw_mapping:
        if not isinstance(raw_mapping, Mapping):
            return None
        for alias, current_name in raw_mapping.items():
            if not isinstance(alias, str) or not alias or not isinstance(current_name, str):
                return None
            current = by_name.get(current_name)
            if current is None:
                return None
            existing = by_name.get(alias)
            if existing is not None and existing.key != current.key:
                return None
            by_name[alias] = replace(current, name=alias)
    return tuple(by_name.values())


def _analyse_polars_node_lineage(
    node: GraphNode,
    incoming_edges: Iterable[GraphEdge],
    node_map: Mapping[str, GraphNode],
    exact_output_by_node: Mapping[str, frozenset[str]],
    demanded_output: set[str] | None,
    contract: Contract,
    *,
    submodels: Mapping[str, Any] | None = None,
    selector_aliases: frozenset[str] = frozenset(),
    known_port_columns: Mapping[tuple[str, str | None], frozenset[str]] | None = None,
) -> tuple[ColumnLineageAnalysis, tuple[_LineageInputBinding, ...]] | None:
    if node.data.nodeType not in _LINEAGE_CODE_NODE_TYPES:
        return None
    produced, referenced = contract.to_tuple()
    if produced is not None and referenced is not None:
        return None
    code = node.data.config.get("code")
    if not isinstance(code, str) or not code.strip():
        return None
    edges = tuple(incoming_edges)
    bindings = _lineage_input_bindings(
        node,
        edges,
        node_map,
        exact_output_by_node,
        submodels=submodels,
        known_port_columns=known_port_columns,
    )
    if not bindings:
        return None
    schemas: dict[str, frozenset[str] | None] = {}
    for binding in bindings:
        schemas[binding.name] = binding.exact_columns
    df_input: str | None = None
    value_names: frozenset[str] = frozenset()
    if node.data.nodeType is NodeType.EXTERNAL_FILE:
        # The loaded artifact comes from JSON, the restricted unpickler, or a
        # model loader, so ``obj`` is a value and never a Polars expression.
        value_names = frozenset({"obj"})
        # The builder binds the first incoming frame to ``df`` over any input of
        # that name, so an input called ``df`` has no single meaning here.
        if "df" in schemas:
            return None
        first_key = ProjectionEdgeKey.from_edge(edges[0])
        df_input = next(binding.name for binding in bindings if binding.key == first_key)
    return (
        analyze_polars_lineage(
            code,
            schemas,
            demanded_output,
            selector_aliases=selector_aliases,
            df_input=df_input,
            value_names=value_names,
        ),
        bindings,
    )


def _exact_registered_contract_output(
    contract: Contract,
    input_columns: frozenset[str],
) -> frozenset[str] | None:
    """Transfer an exact single-input schema through a registered contract.

    The backward contract algebra already defines every non-produced output
    column as passthrough.  Its sound forward counterpart is therefore the
    exact input schema plus the columns the runtime builder produces.  Only
    the registered builder contract is evidence here: a user declaration on
    arbitrary code must not manufacture an exact output schema.
    """
    produced, referenced = contract.to_tuple()
    if produced is None or referenced is None:
        return None
    if not set(referenced) <= set(input_columns):
        return None
    return frozenset(set(input_columns) | set(produced))


def _exact_structural_outputs(
    order: Iterable[str],
    incoming_by_target: Mapping[str, Iterable[GraphEdge]],
    node_map: Mapping[str, GraphNode],
    registered_contract_for: Callable[[GraphNode], Contract],
    effective_contract_for: Callable[[GraphNode], Contract],
    *,
    submodels: Mapping[str, Any] | None = None,
    selector_aliases: frozenset[str] = frozenset(),
) -> dict[str, frozenset[str]]:
    """Propagate every mechanically proven exact schema topologically."""
    exact: dict[str, frozenset[str]] = {}
    for node_id in order:
        incoming = tuple(incoming_by_target.get(node_id, ()))
        analysed = _analyse_polars_node_lineage(
            node_map[node_id],
            incoming,
            node_map,
            exact,
            set(),
            effective_contract_for(node_map[node_id]),
            submodels=submodels,
            selector_aliases=selector_aliases,
        )
        if analysed is not None:
            result, _bindings = analysed
            if result.supported and result.exact_output_columns is not None:
                shaped = _configured_output_schema(
                    node_map[node_id].data.config, result.exact_output_columns
                )
                if shaped is not None:
                    exact[node_id] = shaped
                continue

        if len(incoming) != 1:
            continue
        input_columns = _exact_columns_for_parent_edge(
            incoming[0],
            node_map,
            exact,
        )
        if input_columns is None:
            continue
        output_columns = _exact_registered_contract_output(
            registered_contract_for(node_map[node_id]),
            input_columns,
        )
        if output_columns is not None:
            shaped = _configured_output_schema(node_map[node_id].data.config, output_columns)
            if shaped is not None:
                exact[node_id] = shaped
    return exact


def _configured_output_schema(
    config: Mapping[str, Any], columns: frozenset[str]
) -> frozenset[str] | None:
    """Apply executor output shaping to a proven schema, without hiding collisions."""
    selected = set(config.get("selected_columns") or [])
    kept = columns & selected
    if kept:
        columns = frozenset(kept)
    renames = config.get("column_renames") or {}
    renamed = frozenset(renames.get(column, column) for column in columns)
    return renamed if len(renamed) == len(columns) else None


def compute_prepared_plan(
    order: list[str],
    children_of: Mapping[str, Iterable[str]],
    node_map: Mapping[str, GraphNode],
    required_columns_by_node: Mapping[str, Iterable[str] | AllExceptColumns] | None = None,
    *,
    relevant_edges: Iterable[GraphEdge] | None = None,
    submodels: Mapping[str, Any] | None = None,
    selector_aliases: frozenset[str] = frozenset(),
    known_output_columns: Mapping[tuple[str, str | None], frozenset[str]] | None = None,
) -> ProjectionPlan:
    """Run the reverse topological projection sweep on a prepared graph.

    ``known_output_columns`` are output schemas observed for built nodes, keyed
    by node and port (``None`` for a single-frame node). They stand in for a
    parent's schema where demand is routed (lineage input bindings and Edge Join
    ownership) and never replace an unknown demand.
    """
    prepared_edges = _projection_edges(order, children_of, relevant_edges)
    incoming_by_target, outgoing_by_source = _edges_by_endpoint(order, prepared_edges)
    registered_contracts: dict[str, Contract] = {}
    effective_contracts: dict[str, Contract] = {}

    def registered_contract_for(node: GraphNode) -> Contract:
        contract = registered_contracts.get(node.id)
        if contract is None:
            contract = Contract.from_tuple(
                get_column_contract(node.data.nodeType, node.data.config)
            )
            registered_contracts[node.id] = contract
        return contract

    def effective_contract_for(node: GraphNode) -> Contract:
        contract = effective_contracts.get(node.id)
        if contract is None:
            contract = _projection_contract_from_registered(
                node,
                registered_contract_for(node),
            )
            effective_contracts[node.id] = contract
        return contract

    exact_output_by_node = _exact_structural_outputs(
        order,
        incoming_by_target,
        node_map,
        registered_contract_for,
        effective_contract_for,
        submodels=submodels,
        selector_aliases=selector_aliases,
    )
    known_outputs = dict(known_output_columns or {})

    def produced_for_routing(edge: GraphEdge) -> set[str] | None:
        known = known_outputs.get((edge.source, edge.sourceHandle))
        if known is not None:
            return set(known)
        return _parent_produced_columns(node_map[edge.source])

    needed: dict[str, set[str] | None] = {}
    edge_demands: dict[ProjectionEdgeKey, set[str] | None] = {}
    node_reasons: dict[str, ProjectionReason] = {}
    edge_reasons: dict[ProjectionEdgeKey, ProjectionReason] = {}
    seeded_required = normalise_required_columns_by_node(required_columns_by_node, order)

    def store_parent_result(
        incoming: Iterable[GraphEdge],
        result: ParentDemandResult,
        *,
        message: str,
        details: Mapping[str, Any] | None = None,
    ) -> None:
        for edge in incoming:
            key = ProjectionEdgeKey.from_edge(edge)
            parent_demand = result.for_parent(edge.source)
            if parent_demand is not None:
                edge_demands[key] = set(parent_demand)
            edge_reasons[key] = ProjectionReason(
                rule=result.rule_name,
                message=message if result.message is None else result.message,
                details={} if details is None else details,
            )

    for node_id in reversed(order):
        node = node_map[node_id]
        outgoing = outgoing_by_source.get(node_id, [])
        incoming = incoming_by_target.get(node_id, [])

        has_seed = node_id in seeded_required
        seed = seeded_required.get(node_id, set())
        if not outgoing:
            if node.data.nodeType == NodeType.OUTPUT:
                # Keep the projection contract aligned with output assembly:
                # incomplete editor rows do not demand a blank source column.
                from haute._output_assembler import is_active_mapping_entry

                mapping = node.data.config.get("outputMapping") or []
                source_cols = {e["source_column"] for e in mapping if is_active_mapping_entry(e)}
                needed[node_id] = source_cols or None
                node_reasons[node_id] = ProjectionReason(
                    rule="terminal_output",
                    message=(
                        "terminal output mapping" if source_cols else "terminal opaque output"
                    ),
                )
            else:
                needed[node_id] = None
                node_reasons[node_id] = ProjectionReason(
                    rule="terminal_output",
                    message="terminal opaque output",
                )
        else:
            accumulated: set[str] | None = set()
            for edge in outgoing:
                edge_demand = edge_demands.get(ProjectionEdgeKey.from_edge(edge))
                if edge_demand is None:
                    accumulated = None
                    break
                assert accumulated is not None
                accumulated |= edge_demand
            needed[node_id] = accumulated
            node_reasons[node_id] = ProjectionReason(
                rule="child_demand",
                message="downstream child demand",
            )

        if has_seed:
            if isinstance(seed, AllExceptColumns):
                exact_output = exact_output_by_node.get(node_id)
                if exact_output is None:
                    # The caller consumes an unknown set of feature columns.
                    # Retaining only metadata would silently drop real model
                    # inputs, so unresolved all-except demand stays full-width.
                    needed[node_id] = None
                elif not outgoing:
                    # Terminal ``None`` means "the caller wants this node's
                    # output", not an opaque child.  The exact schema makes
                    # that schema-relative request concrete.
                    needed[node_id] = set(seed.resolve(exact_output))
                elif needed[node_id] is not None:
                    existing = needed[node_id]
                    assert existing is not None
                    existing |= set(seed.resolve(exact_output))
                    needed[node_id] = existing
                node_reasons[node_id] = ProjectionReason(
                    rule="schema_all_except",
                    message="schema-derived all-except demand",
                    details={
                        "exclude": tuple(sorted(seed.exclude)),
                        "keep": tuple(sorted(seed.keep)),
                    },
                )
            elif needed[node_id] is None:
                if len(outgoing) <= 1:
                    needed[node_id] = set(seed)
                    node_reasons[node_id] = ProjectionReason(
                        rule="projection_seed",
                        message="caller required columns",
                    )
                else:
                    node_reasons[node_id] = ProjectionReason(
                        rule="projection_seed_blocked_by_opaque_fan_out",
                        message=(
                            "caller projection seed could not safely replace opaque "
                            "demand from multiple downstream consumers"
                        ),
                        details={
                            "seeded_columns": tuple(sorted(seed)),
                            "child_node_ids": tuple(sorted({edge.target for edge in outgoing})),
                        },
                    )
            else:
                existing = needed[node_id]
                if existing is None:
                    raise RuntimeError("concrete projection branch unexpectedly became opaque")
                existing |= seed
                needed[node_id] = existing
                node_reasons[node_id] = ProjectionReason(
                    rule="projection_seed",
                    message="caller required columns",
                )

        if _must_run_source_user_code_unprojected(node, needed[node_id]):
            needed[node_id] = None
            node_reasons[node_id] = ProjectionReason(
                rule=UNPROJECTED_STREAMING_BOUNDARY_RULE_NAME,
                message="source user code requires full-width source scan",
            )

        my_needed = needed[node_id]
        if my_needed is None and node_id in exact_output_by_node:
            needed[node_id] = set(exact_output_by_node[node_id])
            my_needed = needed[node_id]
            node_reasons[node_id] = ProjectionReason(
                rule="registered_contract_schema",
                message="known full schema from structural contract transfer",
            )
        parent_ids = {edge.source for edge in incoming}

        if incoming and has_configured_column_renames(node):
            # Config renames run AFTER the builder/user code and optional selection.
            # Builder contracts and code lineage therefore describe a different
            # namespace. Keep inputs intact: inverse mapping without the full
            # pre-rename schema can also prune away a real duplicate-name error.
            store_parent_result(
                incoming,
                ParentDemandResult(
                    default=None,
                    by_parent={},
                    rule_name="configured_column_renames",
                ),
                message="configured output renames require intact builder inputs",
            )
            continue

        lineage = _analyse_polars_node_lineage(
            node,
            incoming,
            node_map,
            exact_output_by_node,
            my_needed,
            effective_contract_for(node),
            submodels=submodels,
            selector_aliases=selector_aliases,
            known_port_columns=known_outputs,
        )
        if lineage is not None:
            lineage_result, bindings = lineage
            if lineage_result.supported:
                node_reasons[node_id] = ProjectionReason(
                    rule=POLARS_COLUMN_LINEAGE_RULE_NAME,
                    message="compositional Polars column lineage",
                    details={
                        "exact_output": (
                            tuple(sorted(lineage_result.exact_output_columns))
                            if lineage_result.exact_output_columns is not None
                            else None
                        )
                    },
                )
                demands_by_edge: dict[ProjectionEdgeKey, set[str]] = {}
                bindings_by_name = {binding.name: binding for binding in bindings}
                for input_name, columns in lineage_result.demands_by_input.items():
                    binding = bindings_by_name[input_name]
                    demands_by_edge.setdefault(binding.key, set()).update(columns)
                for edge in incoming:
                    key = ProjectionEdgeKey.from_edge(edge)
                    # A supported analysis returns one demand (possibly the
                    # exact empty set) for every bound input. Binding aliases
                    # have already been unioned onto their physical edge.
                    edge_demands[key] = set(demands_by_edge[key])
                    edge_reasons[key] = ProjectionReason(
                        rule=POLARS_COLUMN_LINEAGE_RULE_NAME,
                        message="compositional Polars input dependency",
                        details={
                            "input_name": next(
                                (
                                    binding.name
                                    for binding in bindings
                                    if binding.key == key
                                    and binding.name in lineage_result.demands_by_input
                                ),
                                edge_input_name(
                                    edge,
                                    node_map[edge.source],
                                    submodels=submodels,
                                ),
                            )
                        },
                    )
            else:
                reason = ProjectionReason(
                    rule="polars_lineage_unsupported",
                    message="Polars code is outside the closed column-lineage model",
                    details={
                        "reason": lineage_result.reason,
                        "operation": lineage_result.unsupported_operation,
                    },
                )
                node_reasons[node_id] = ProjectionReason(
                    rule=reason.rule,
                    message=reason.message,
                    details=reason.details,
                )
                for edge in incoming:
                    key = ProjectionEdgeKey.from_edge(edge)
                    edge_reasons[key] = reason
            continue

        if len(incoming) != len(parent_ids):
            if node.data.nodeType == NodeType.EDGE_JOIN:
                parent_produced = {edge.source: produced_for_routing(edge) for edge in incoming}
                edge_join_demands = edge_join_fan_in_demands_for_node(
                    node,
                    incoming,
                    set() if my_needed is None else set(my_needed),
                    set(),
                    parent_produced,
                )
                # This block already establishes the helper's Edge Join and
                # multi-edge preconditions, so its optional case is unreachable.
                store_parent_result(
                    incoming,
                    cast(ParentDemandResult, edge_join_demands),
                    message="edge-join fan-in ownership rule",
                )
                continue
            if node.data.nodeType == NodeType.OPTIMISER:
                optimiser_demands = parent_demands_for_node(
                    node,
                    incoming,
                    node_map,
                    my_needed,
                    seeded_required,
                    submodels=submodels,
                )
                # The node type and non-empty parent set likewise make the
                # generic helper's optional case unreachable here.
                store_parent_result(
                    incoming,
                    cast(ParentDemandResult, optimiser_demands),
                    message="optimiser exact-input ownership rule",
                )
                continue
            # Every remaining rule addresses parents by node id.  Parallel
            # incoming edges from one source (typically different API ports)
            # cannot be routed by those contracts without conflating their
            # demands, so retain a visible boundary.  The edge-aware Polars
            # lineage path above is the only path allowed to narrow this shape.
            reason = ProjectionReason(
                rule="parallel_edge_contract_ambiguous",
                message="parent-id column contract cannot distinguish parallel input edges",
                details={"source_node_ids": tuple(sorted(parent_ids))},
            )
            node_reasons[node_id] = reason
            for edge in incoming:
                edge_reasons[ProjectionEdgeKey.from_edge(edge)] = reason
            continue

        routed_demands = parent_demands_for_node(
            node,
            incoming,
            node_map,
            my_needed,
            seeded_required,
            submodels=submodels,
        )
        if routed_demands is not None:
            store_parent_result(
                incoming,
                routed_demands,
                message="node-specific parent demand",
            )
            continue

        if my_needed is None:
            store_parent_result(
                incoming,
                ParentDemandResult(
                    default=None,
                    by_parent={},
                    rule_name="opaque_demand",
                ),
                message="opaque downstream demand",
            )
            continue

        contract = effective_contract_for(node)
        post_code = _builder_post_code(node)
        if post_code is not None:
            post_code_lineage = analyze_polars_lineage(
                post_code,
                {"df": None},
                my_needed,
                selector_aliases=selector_aliases,
            )
            if not post_code_lineage.supported:
                store_parent_result(
                    incoming,
                    ParentDemandResult(default=None, by_parent={}, rule_name="builder_post_code"),
                    message=(
                        "builder post-code is outside the closed column-lineage model: "
                        f"{post_code_lineage.reason}"
                    ),
                )
                continue
            my_needed = set(post_code_lineage.demands_by_input.get("df", frozenset()))
            contract = _pre_post_code_contract(node, contract)
        produced, referenced = contract.to_tuple()
        if produced is None or referenced is None:
            parent_produced = {edge.source: produced_for_routing(edge) for edge in incoming}
            edge_join_demands = edge_join_fan_in_demands_for_node(
                node,
                incoming,
                set(my_needed),
                set(),
                parent_produced,
            )
            if edge_join_demands is not None:
                store_parent_result(
                    incoming,
                    edge_join_demands,
                    message="edge-join fan-in ownership rule",
                )
                continue
            opaque_demands = opaque_contract_demands_for_node(node, parent_ids)
            store_parent_result(
                incoming,
                opaque_demands,
                message="opaque contract demand",
            )
            continue

        base_contribution = (my_needed - produced) | referenced
        fan_in_demands = fan_in_demands_for_node(
            node,
            parent_ids,
            base_contribution,
            referenced,
        )
        if fan_in_demands is not None:
            store_parent_result(
                incoming,
                fan_in_demands,
                message="fan-in ownership rule",
            )
            continue

        if len(parent_ids) > 1:
            # Ordinary contract algebra has one undifferentiated input set. It
            # cannot prove which parent of an otherwise-unhandled fan-in owns a
            # demanded column, so broadcasting that set would ask every parent
            # for columns it may never produce. Dedicated rules above are the
            # only routes allowed to narrow a multi-parent node.
            store_parent_result(
                incoming,
                _unprojected_boundary_demands(),
                message="ambiguous multi-parent ownership",
            )
            continue

        store_parent_result(
            incoming,
            ParentDemandResult(
                default=base_contribution,
                by_parent={},
                rule_name="contract_algebra",
            ),
            message="column contract algebra",
        )

    return _freeze_plan(
        needed,
        edge_demands,
        node_reasons=node_reasons,
        edge_reasons=edge_reasons,
    )


def _freeze_columns(columns: set[str] | frozenset[str] | None) -> frozenset[str] | None:
    return None if columns is None else frozenset(columns)


def _freeze_plan(
    needed_by_node: Mapping[str, set[str] | None],
    edge_demands: Mapping[ProjectionEdgeKey, set[str] | None],
    *,
    node_reasons: Mapping[str, ProjectionReason] | None = None,
    edge_reasons: Mapping[ProjectionEdgeKey, ProjectionReason] | None = None,
) -> ProjectionPlan:
    frozen_needed = {
        node_id: _freeze_columns(columns) for node_id, columns in needed_by_node.items()
    }
    frozen_edges = {edge: _freeze_columns(columns) for edge, columns in edge_demands.items()}
    opaque_boundaries = frozenset(
        node_id for node_id, columns in frozen_needed.items() if columns is None
    )
    diagnostics = ProjectionDiagnostics(
        opaque_reasons=MappingProxyType(
            {
                node_id: dict(node_reasons or {}).get(
                    node_id,
                    ProjectionReason(
                        rule="opaque_demand",
                        message="opaque demand",
                    ),
                )
                for node_id in opaque_boundaries
            }
        ),
        node_reasons=MappingProxyType(dict(node_reasons or {})),
        edge_reasons=_EdgeIdentityMapping(dict(edge_reasons or {})),
    )
    return ProjectionPlan(
        needed_by_node=MappingProxyType(frozen_needed),
        edge_demands=_EdgeIdentityMapping(frozen_edges),
        opaque_boundaries=opaque_boundaries,
        diagnostics=diagnostics,
    )


def explain(
    projection_plan: ProjectionPlan,
    *,
    column: str | None = None,
    node_id: str | None = None,
) -> tuple[str, ...]:
    """Return human-readable projection provenance lines.

    The output is intentionally compact and stable enough for tests/logging;
    it is not a UI contract.  Filters are conjunctive: when both *column* and
    *node_id* are supplied, only matching node and edge entries are returned.
    """

    def _column_text(columns: frozenset[str] | None) -> str:
        if columns is None:
            return "opaque"
        if column is not None:
            return column
        return ", ".join(sorted(columns))

    lines: list[str] = []
    for current_node_id, columns in projection_plan.needed_by_node.items():
        if node_id is not None and current_node_id != node_id:
            continue
        if column is not None and columns is not None and column not in columns:
            continue
        reason = projection_plan.diagnostics.node_reasons.get(
            current_node_id,
            ProjectionReason(rule="projection_demand", message="projection demand"),
        )
        lines.append(
            f"{current_node_id}: {reason.rule}: {reason.message} [{_column_text(columns)}]"
        )

    for edge_key, columns in projection_plan.edge_demands.items():
        parent_id = edge_key.source
        child_id = edge_key.target
        if node_id is not None and child_id != node_id and parent_id != node_id:
            continue
        if column is not None and columns is not None and column not in columns:
            continue
        reason = projection_plan.diagnostics.edge_reasons.get(
            edge_key,
            ProjectionReason(rule="edge_demand", message="edge demand"),
        )
        port = f" [{edge_key.source_handle}]" if edge_key.source_handle else ""
        lines.append(
            f"{parent_id}{port} -> {child_id}: {reason.rule}: "
            f"{reason.message} [{_column_text(columns)}]"
        )

    return tuple(lines)


def with_runtime_inferred_streaming_edges(
    projection_plan: ProjectionPlan,
    *,
    demands_by_edge: Mapping[ProjectionEdgeKey, Iterable[str]],
    resolved_parent_ids: Iterable[str] = (),
    relevant_edges: Iterable[GraphEdge] = (),
) -> ProjectionPlan:
    """Return a plan annotated with runtime-inferred edge demands.

    A source boundary is removed only when every outgoing edge in this
    execution has a concrete static or runtime demand.  Resolving one branch
    of an opaque fan-out must never hide the still-full-width sibling.
    """
    if not demands_by_edge:
        return projection_plan

    needed_by_node = dict(projection_plan.needed_by_node)
    edge_demands = dict(projection_plan.edge_demands)
    node_reasons = dict(projection_plan.diagnostics.node_reasons)
    opaque_reasons = dict(projection_plan.diagnostics.opaque_reasons)
    edge_reasons = dict(projection_plan.diagnostics.edge_reasons)
    candidate_parents = frozenset(resolved_parent_ids)
    relevant_edge_keys_by_parent: dict[str, set[ProjectionEdgeKey]] = {}
    for edge in relevant_edges:
        if edge.source in candidate_parents:
            relevant_edge_keys_by_parent.setdefault(edge.source, set()).add(
                ProjectionEdgeKey.from_edge(edge)
            )
    resolved_columns_by_parent: dict[str, set[str]] = {}
    for edge_key, columns in demands_by_edge.items():
        frozen_columns = frozenset(columns)
        reason = ProjectionReason(
            rule=RUNTIME_INFERRED_STREAMING_RULE_NAME,
            message="runtime-inferred streaming demand",
            details={
                "strategy": RUNTIME_INFERRED_STREAMING_RULE_NAME,
                "columns": tuple(sorted(frozen_columns)),
            },
        )
        edge_demands[edge_key] = frozen_columns
        edge_reasons[edge_key] = reason
    resolved_parents = frozenset(
        parent_id
        for parent_id, outgoing_keys in relevant_edge_keys_by_parent.items()
        if outgoing_keys
        and all(
            edge_key in edge_demands and edge_demands[edge_key] is not None
            for edge_key in outgoing_keys
        )
    )
    for parent_id in resolved_parents:
        for edge_key in relevant_edge_keys_by_parent[parent_id]:
            edge_columns = edge_demands[edge_key]
            assert edge_columns is not None
            resolved_columns_by_parent.setdefault(parent_id, set()).update(edge_columns)

    for parent_id, columns in resolved_columns_by_parent.items():
        frozen_columns = frozenset(columns)
        needed_by_node[parent_id] = frozen_columns
        node_reasons[parent_id] = ProjectionReason(
            rule=RUNTIME_INFERRED_STREAMING_RULE_NAME,
            message="runtime-inferred streaming demand",
            details={
                "strategy": RUNTIME_INFERRED_STREAMING_RULE_NAME,
                "columns": tuple(sorted(frozen_columns)),
            },
        )
        opaque_reasons.pop(parent_id, None)

    return ProjectionPlan(
        needed_by_node=MappingProxyType(needed_by_node),
        edge_demands=_EdgeIdentityMapping(edge_demands),
        materialisation_boundaries=projection_plan.materialisation_boundaries,
        opaque_boundaries=(projection_plan.opaque_boundaries - resolved_parents),
        diagnostics=ProjectionDiagnostics(
            opaque_reasons=MappingProxyType(opaque_reasons),
            node_reasons=MappingProxyType(node_reasons),
            edge_reasons=_EdgeIdentityMapping(edge_reasons),
        ),
    )


def plan(request: ProjectionRequest) -> ProjectionPlan:
    """Compute a shared projection plan for *request*."""
    prepared = prepare_graph(
        request.graph,
        request.target_node_id,
        source=request.source,
    )
    projection_plan = compute_prepared_plan(
        prepared.order,
        _children_of(prepared.order, prepared.parents_of),
        prepared.node_map,
        required_columns_by_node=request.required_columns_by_node,
        relevant_edges=prepared.relevant_edges,
        submodels=prepared.submodels,
        selector_aliases=preamble_selector_aliases(request.graph.preamble or ""),
    )
    return with_api_input_port_projection_boundaries(
        projection_plan,
        prepared.node_map,
        prepared.relevant_edges,
    )
