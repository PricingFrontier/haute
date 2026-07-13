"""Shared projection planning facade.

This module is the stable boundary for code that needs column projection
planning.  Routes and deploy callers should use this shared surface rather
than reaching into executor internals.
"""

from __future__ import annotations

import ast
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Any, NamedTuple

from haute._code_extraction import _strip_generated_boilerplate_from_code
from haute._contracts import Contract, get_column_contract
from haute._edge_join import (
    build_edge_join_kwargs,
    edge_join_key_columns_by_role,
    narrow_join_parent_demand,
    resolve_edge_join_role_indices,
)
from haute._execution_context import ExecutionProfile
from haute._graph_utils import _sanitize_func_name, build_parents_of
from haute._topo import ancestors, topo_sort_ids
from haute._types import GraphEdge, GraphNode, NodeType, PipelineGraph
from haute.errors import ContractMismatchError, ProjectionImpossibleError

__all__ = [
    "AllExcept",
    "AllExceptColumns",
    "ProjectionDiagnostics",
    "ProjectionPlan",
    "ProjectionRuleCoverage",
    "ProjectionRequest",
    "ProjectionReason",
    "SourceScanProjection",
    "UNPROJECTED_STREAMING_BOUNDARY_RULE_NAME",
    "builder_required_output_columns_by_node",
    "explain",
    "model_score_required_output_columns",
    "plan",
    "projection_rule_coverage_by_node_type",
    "ratebook_factor_required_columns",
    "with_runtime_inferred_streaming_edges",
    "simple_join_calls_for_parent_inputs",
    "source_scan_projection",
    "source_user_code_preserves_column_projection",
    "strict_projection_required",
    "validate_projection_rule_coverage",
]


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


@dataclass(frozen=True)
class ProjectionDiagnostics:
    """Lightweight diagnostics attached to a shared projection plan.

    Full per-column provenance is intentionally deferred until the rule
    extraction sub-slice.  This initial shape gives callers a stable place to
    read opaque boundaries without depending on executor-private details.
    """

    opaque_reasons: Mapping[str, ProjectionReason] = field(
        default_factory=lambda: MappingProxyType({})
    )
    node_reasons: Mapping[str, ProjectionReason] = field(
        default_factory=lambda: MappingProxyType({})
    )
    edge_reasons: Mapping[tuple[str, str], ProjectionReason] = field(
        default_factory=lambda: MappingProxyType({})
    )

    def to_dict(self) -> dict[str, Any]:
        return {
            "opaque_reasons": {
                node_id: reason.to_dict() for node_id, reason in sorted(self.opaque_reasons.items())
            },
            "node_reasons": {
                node_id: reason.to_dict() for node_id, reason in sorted(self.node_reasons.items())
            },
            "edge_reasons": {
                f"{parent_id}->{child_id}": reason.to_dict()
                for (parent_id, child_id), reason in sorted(
                    self.edge_reasons.items(),
                    key=lambda item: item[0],
                )
            },
        }


_MAX_STRATEGY_SUMMARY_NODES = 100


@dataclass(frozen=True)
class ProjectionPlan:
    """Column projection needs at nodes and parent-specific fan-in edges."""

    needed_by_node: Mapping[str, frozenset[str] | None]
    edge_demands: Mapping[tuple[str, str], frozenset[str] | None]
    materialisation_boundaries: frozenset[str] = frozenset()
    opaque_boundaries: frozenset[str] = frozenset()
    diagnostics: ProjectionDiagnostics = field(default_factory=ProjectionDiagnostics)

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
class SourceScanProjection:
    """Physical source scan projection plus schema-only validation columns."""

    columns: frozenset[str] | None
    validate_columns: frozenset[str] = frozenset()


@dataclass(frozen=True)
class AllExcept:
    """Schema-derived demand for targets that train on all non-excluded columns.

    This is intentionally not converted to an exact column set during static
    planning.  The concrete feature set is resolved from the materialised target
    schema, while `required_columns` names metadata such as target, weight,
    offset, split keys, and ids that must survive even if users also list them
    in `excluded_columns`.
    """

    required_columns: frozenset[str] = frozenset()
    excluded_columns: frozenset[str] = frozenset()

    @property
    def keep(self) -> frozenset[str]:
        return self.required_columns

    @property
    def exclude(self) -> frozenset[str]:
        return self.excluded_columns


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

    def for_parent(self, parent_id: str) -> set[str] | None:
        return self.by_parent.get(parent_id, self.default)


def _unprojected_boundary_demands(
    *,
    default: set[str] | None = None,
    by_parent: dict[str, set[str] | None] | None = None,
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
        rule_name=UNPROJECTED_STREAMING_BOUNDARY_RULE_NAME,
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


_STRICT_PROJECTION_PROFILES = frozenset(
    {
        ExecutionProfile.LAZY_SINK,
        ExecutionProfile.TRAINING_PREP,
        ExecutionProfile.OPTIMISER_SETUP,
        ExecutionProfile.EXPLORE_ANALYSIS,
        ExecutionProfile.AUTO_RANGE,
        ExecutionProfile.DEPLOY_BATCH,
        ExecutionProfile.CHUNKED_MAP_REDUCE,
    }
)


def strict_projection_required(
    profile: ExecutionProfile,
    required_columns_by_node: Mapping[str, Iterable[str] | AllExceptColumns] | None,
) -> bool:
    """Return whether projection-impossible cases must fail loudly."""
    _ = required_columns_by_node
    return profile in _STRICT_PROJECTION_PROFILES


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
    Callers that already strip generated boilerplate should pass the sanitized
    post-processing code so no-op scaffolding does not disable projection.
    """
    code = (
        _strip_generated_boilerplate_from_code(
            config.get("code") or "",
            kind="model_score",
            param_names=("df",),
        )
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
) -> SourceScanProjection:
    """Map logical source output demand to physical scan columns.

    Source builders apply ``selected_columns`` before ``column_renames``.
    Projection seeds are expressed in post-source logical output names, so a
    demanded logical column such as ``premium`` must be pushed down as its
    physical input name, for example ``raw_premium``.  Validation columns are
    checked against the source schema without being read, which lets bounded
    profiles stay narrow while still failing loudly on stale selections.
    """
    selected = _strict_string_list(config.get("selected_columns"), key="selected_columns")
    selected_set = frozenset(selected)
    renames = _strict_renames(config)

    if required_output_columns is None:
        return SourceScanProjection(
            columns=selected_set if selected else None,
            validate_columns=selected_set,
        )

    required = frozenset(required_output_columns)
    if not required:
        return SourceScanProjection(columns=frozenset(), validate_columns=selected_set)

    reverse: dict[str, str] = {}
    ambiguous_targets: set[str] = set()
    for source, target in renames.items():
        if target in reverse:
            ambiguous_targets.add(target)
            continue
        reverse[target] = source

    if renames and not selected:
        rename_outputs = set(reverse) | ambiguous_targets
        if required & rename_outputs:
            return SourceScanProjection(columns=None, validate_columns=selected_set)

    physical: set[str] = set()
    for logical_column in required:
        if logical_column in ambiguous_targets:
            return SourceScanProjection(
                columns=selected_set if selected else None,
                validate_columns=selected_set,
            )
        physical_column = reverse.get(logical_column, logical_column)
        if selected and physical_column not in selected_set:
            raise ValueError(
                "source projection requires a logical output column excluded by "
                f"selected_columns: {logical_column!r}"
            )
        physical.add(physical_column)

    return SourceScanProjection(
        columns=frozenset(physical),
        validate_columns=selected_set,
    )


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
    and must be described with a concrete contract in strict profiles.
    """
    stripped = _strip_generated_boilerplate_from_code(code, kind="data_source")
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


def overlay_declared_contract(node: GraphNode, builder: Contract) -> Contract:
    """Apply any user-declared contract fields over a builder contract."""
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
    inputs = declared.inputs if declared.inputs is not None else builder.inputs
    outputs = declared.outputs if declared.outputs is not None else builder.outputs
    if node.data.nodeType == NodeType.SCENARIO_EXPANDER and builder.outputs is not None:
        outputs = builder.outputs if outputs is None else outputs | builder.outputs
    return Contract(inputs=inputs, outputs=outputs)


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

    if node.data.nodeType == NodeType.SCENARIO_EXPANDER and builder.outputs:
        return True

    return _has_projection_user_code(node) and (builder.inputs is None or builder.outputs is None)


def projection_contract(node: GraphNode) -> Contract:
    """Return the column contract used by projection analysis.

    Unlike executor boundary checks, projection does not soften builder
    contract failures. A malformed concrete contract should be visible rather
    than quietly widening the graph.
    """
    if node.data.nodeType == NodeType.POLARS and not _has_user_polars_code(node):
        builder = Contract(inputs=frozenset(), outputs=frozenset())
    else:
        builder = Contract.from_tuple(get_column_contract(node.data.nodeType, node.data.config))
    return overlay_declared_contract(node, builder)


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
        parent_ids: Iterable[str],
        my_needed: set[str] | None,
        seeded_required: Mapping[str, set[str] | AllExceptColumns],
    ) -> ParentDemandResult | None:
        parent_set = set(parent_ids)
        if not parent_set or node.data.nodeType != NodeType.OPTIMISER:
            return None

        config = node.data.config
        configured_data_input = config.get("data_input")
        banding_source = config.get("banding_source")
        data_input = configured_data_input
        if not isinstance(data_input, str) or not data_input:
            data_input = next(iter(parent_set)) if len(parent_set) == 1 else None
        if not isinstance(data_input, str) or not data_input:
            raise ContractMismatchError(
                "Multi-parent optimiser projection requires a configured data_input.",
                node_id=node.id,
                node_type=node.data.nodeType.value,
                incoming_parent_ids=sorted(parent_set),
            )
        if data_input not in parent_set:
            raise ContractMismatchError(
                "Configured optimiser data_input is not connected to the optimiser node.",
                node_id=node.id,
                node_type=node.data.nodeType.value,
                data_input=data_input,
                incoming_parent_ids=sorted(parent_set),
            )

        seeded_data_input = seeded_required.get(data_input)
        data_input_columns = (
            set(seeded_data_input)
            if my_needed is None and isinstance(seeded_data_input, set)
            else my_needed
        )
        by_parent: dict[str, set[str] | None] = {parent_id: set() for parent_id in parent_set}
        by_parent[data_input] = None if data_input_columns is None else set(data_input_columns)

        if config.get("mode", "online") == "ratebook":
            if not isinstance(banding_source, str) or not banding_source:
                if len(parent_set) == 1:
                    return ParentDemandResult(
                        default=None,
                        by_parent=by_parent,
                        rule_name=self.name,
                    )
                raise ContractMismatchError(
                    "Ratebook optimiser projection requires a configured banding_source.",
                    node_id=node.id,
                    node_type=node.data.nodeType.value,
                    incoming_parent_ids=sorted(parent_set),
                )
            if banding_source not in parent_set:
                raise ContractMismatchError(
                    "Configured ratebook banding_source is not connected to the optimiser node.",
                    node_id=node.id,
                    node_type=node.data.nodeType.value,
                    banding_source=banding_source,
                    incoming_parent_ids=sorted(parent_set),
                )
            factor_columns = ratebook_factor_required_columns(config)
            existing = by_parent[banding_source]
            by_parent[banding_source] = None if existing is None else set(existing) | factor_columns

        return ParentDemandResult(
            default=None,
            by_parent=dict(by_parent),
            rule_name=self.name,
        )


_OPTIMISER_PARENT_DEMAND_RULE = OptimiserParentDemandRule()
POLARS_EXPRESSION_DEPENDENCY_RULE_NAME = "polars_expression_dependency"


def parent_demands_for_node(
    node: GraphNode,
    parent_ids: Iterable[str],
    my_needed: set[str] | None,
    seeded_required: Mapping[str, set[str] | AllExceptColumns],
) -> ParentDemandResult | None:
    """Return node-specific parent demands that the generic algebra cannot infer.

    This is a transitional rule bridge used by the executor while Slice 3
    extracts first-class projection rules.  Keeping optimiser/ratebook routing
    here lets future rule extraction happen behind the shared planner facade
    without route or executor call-site churn.
    """
    return _OPTIMISER_PARENT_DEMAND_RULE.parent_demands(
        node,
        parent_ids,
        my_needed,
        seeded_required,
    ) or _SINGLE_PARENT_POLARS_RULE.parent_demands(
        node,
        parent_ids,
        my_needed,
    )


def _literal_string_dict(node: ast.AST) -> dict[str, str] | None:
    if not isinstance(node, ast.Dict):
        return None
    result: dict[str, str] = {}
    for key, value in zip(node.keys, node.values, strict=True):
        if key is None:
            return None
        source = _literal_string(key)
        target = _literal_string(value)
        if source is None or target is None:
            return None
        result[source] = target
    return result


def _pl_col_name(node: ast.AST) -> str | None:
    if not isinstance(node, ast.Call):
        return None
    func = node.func
    if not (
        isinstance(func, ast.Attribute)
        and func.attr == "col"
        and isinstance(func.value, ast.Name)
        and func.value.id == "pl"
    ):
        return None
    if len(node.args) != 1 or node.keywords:
        return None
    return _literal_string(node.args[0])


def _referenced_polars_columns(node: ast.AST) -> set[str] | None:
    columns: set[str] = set()
    for ast_node in ast.walk(node):
        name = _pl_col_name(ast_node)
        if name is not None:
            columns.add(name)
            continue
        if (
            isinstance(ast_node, ast.Call)
            and isinstance(ast_node.func, ast.Attribute)
            and ast_node.func.attr == "col"
        ):
            return None
    return columns


def _alias_name(node: ast.AST) -> str | None:
    if not isinstance(node, ast.Call):
        return None
    func = node.func
    if not isinstance(func, ast.Attribute) or func.attr != "alias":
        return None
    if len(node.args) != 1:
        return None
    return _literal_string(node.args[0])


def _with_columns_outputs(call: ast.Call) -> set[str] | None:
    outputs: set[str] = set()
    for expr in call.args:
        alias = _alias_name(expr)
        if alias is not None:
            outputs.add(alias)
    for keyword in call.keywords:
        if keyword.arg is None:
            return None
        outputs.add(keyword.arg)
    return outputs


def _select_output_to_input(call: ast.Call) -> dict[str, set[str]] | None:
    """Map each select output column to the input columns its expression reads."""
    if call.keywords:
        return None
    output_to_input: dict[str, set[str]] = {}
    for expr in call.args:
        if isinstance(expr, ast.Constant) and isinstance(expr.value, str):
            output_to_input[expr.value] = {expr.value}
            continue
        if isinstance(expr, (ast.List, ast.Tuple)):
            for element in expr.elts:
                name = _literal_string(element)
                if name is None:
                    return None
                output_to_input[name] = {name}
            continue
        refs = _referenced_polars_columns(expr)
        if refs is None:
            return None
        alias = _alias_name(expr)
        if alias is not None:
            output_to_input[alias] = refs
            continue
        col_name = _pl_col_name(expr)
        if col_name is not None:
            output_to_input[col_name] = {col_name}
            continue
        return None
    return output_to_input


def _select_output_demands(call: ast.Call, output_columns: set[str]) -> set[str] | None:
    output_to_input = _select_output_to_input(call)
    if output_to_input is None:
        return None
    missing = output_columns - set(output_to_input)
    if missing:
        return None
    demands: set[str] = set()
    for column in output_columns:
        demands |= output_to_input[column]
    return demands


def _contains_rename_call(tree: ast.AST) -> bool:
    return any(
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "rename"
        for node in ast.walk(tree)
    )


# ``select_seq`` is ``select`` with sequential expression evaluation; both
# execute every output expression, so the demand analysis treats them alike.
_SELECT_METHOD_NAMES = frozenset({"select", "select_seq"})


def _contains_select_call(tree: ast.AST) -> bool:
    return any(
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr in _SELECT_METHOD_NAMES
        for node in ast.walk(tree)
    )


def _derived_output_columns(tree: ast.AST) -> set[str]:
    """Column names created by ``with_columns``/``select`` expressions in *tree*.

    Only genuinely derived names count: ``alias`` targets and keyword
    assignments.  Plain passthrough outputs such as ``select('a', 'b')`` come
    from the parent and must not be treated as derived.  (Select-bearing code
    is independently routed to the ordered analysis by
    :func:`_contains_select_call`, so this distinction now only steers
    select-free ``with_columns`` shapes.)  Unparseable output shapes
    (``**kwargs``) contribute nothing here; both demand walks bail on those
    calls anyway, so routing cannot differ.
    """
    derived: set[str] = set()
    for node in ast.walk(tree):
        if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)):
            continue
        if node.func.attr == "with_columns":
            outputs = _with_columns_outputs(node)
            if outputs:
                derived |= outputs
        elif node.func.attr == "select":
            for expr in node.args:
                alias = _alias_name(expr)
                if alias is not None:
                    derived.add(alias)
            for keyword in node.keywords:
                if keyword.arg is not None:
                    derived.add(keyword.arg)
    return derived


def _references_derived_column(tree: ast.AST) -> bool:
    """Return whether code reads a column name its own expressions create.

    This is the rename-free trigger for ordered backward propagation: the
    unordered union walk re-adds derived names to the parent demand, which
    hard-fails valid derive-then-reference pipelines at the edge projection.
    References are everything the union walk could re-add: ``pl.col`` names
    plus select input columns, which include plain-string passthroughs such
    as ``select('m', 'a')``. Statically unknowable references (a dynamic
    ``pl.col(...)`` or unparseable select) count as a match: preferring the
    ordered extractor is safe because it either bails (and the union walk
    decides, exactly as today) or proves the chain and yields the correct
    demand.
    """
    derived = _derived_output_columns(tree)
    if not derived:
        return False
    referenced = _referenced_polars_columns(tree)
    if referenced is None:
        return True
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "select"
        ):
            output_to_input = _select_output_to_input(node)
            if output_to_input is None:
                return True
            for input_columns in output_to_input.values():
                referenced |= input_columns
    return bool(derived & referenced)


# Row-only frame methods that neither read nor change columns; demand passes
# through them untouched in the ordered backward-propagation analysis.
_ROW_ONLY_CHAIN_METHODS = frozenset({"head", "tail", "limit", "slice"})


def _frame_chain_calls(expr: ast.AST, frame_name: str) -> list[ast.Call] | None:
    """Return the method calls of a chain rooted at *frame_name*, in execution order."""
    calls: list[ast.Call] = []
    current = expr
    while isinstance(current, ast.Call) and isinstance(current.func, ast.Attribute):
        calls.append(current)
        current = current.func.value
    if not (isinstance(current, ast.Name) and current.id == frame_name):
        return None
    calls.reverse()
    return calls


def _ordered_frame_operations(tree: ast.Module) -> list[ast.Call] | None:
    """Extract the linear ``df`` operation sequence in execution order.

    Rename namespace tracking is only sound when the operation order is
    provable, so anything other than a plain sequence of ``df = df.<m>(...)``
    statements (plus inert imports/docstrings/literal helper assignments)
    returns ``None`` and the caller keeps the safe full-width boundary.
    """
    operations: list[ast.Call] = []
    saw_frame_assignment = False
    for stmt in tree.body:
        if isinstance(stmt, (ast.Import, ast.ImportFrom, ast.Pass)):
            continue
        if isinstance(stmt, ast.Expr) and isinstance(stmt.value, ast.Constant):
            continue
        if not isinstance(stmt, ast.Assign) or len(stmt.targets) != 1:
            return None
        target = stmt.targets[0]
        if not isinstance(target, ast.Name):
            return None
        if target.id != "df":
            # Helper assignments must not capture the frame or hide column
            # references behind calls the column walkers cannot see through.
            if _expr_references_name(stmt.value, "df"):
                return None
            if any(isinstance(child, ast.Call) for child in ast.walk(stmt.value)):
                return None
            continue
        chain = _frame_chain_calls(stmt.value, "df")
        if chain is None:
            return None
        for call in chain:
            for argument in [*call.args, *(kw.value for kw in call.keywords)]:
                if _expr_references_name(argument, "df"):
                    return None
        operations.extend(chain)
        saw_frame_assignment = True
    if not saw_frame_assignment:
        return None
    return operations


def _frame_rename_mapping(call: ast.Call) -> dict[str, str] | None:
    """Return the literal rename mapping with no-op pairs dropped, or ``None``."""
    if len(call.args) != 1 or call.keywords:
        return None
    mapping = _literal_string_dict(call.args[0])
    if mapping is None:
        return None
    return {source: target for source, target in mapping.items() if source != target}


def _demand_before_rename(demand: set[str], renames: dict[str, str]) -> set[str] | None:
    """Translate post-rename demand into the pre-rename column namespace.

    Polars applies rename pairs simultaneously, so swaps resolve through the
    reverse mapping. Two sources renamed onto one target is a genuine
    ``DuplicateError`` at execution. A target that is not also renamed away
    could also collide with an unchanged upstream column the planner cannot see
    from syntax alone. Demanding a name the rename removed is a genuine
    missing-column error. Those cases return ``None`` so the full-width
    boundary lets execution raise the real failure instead of the planner
    projecting it into a misleading one.
    """
    reverse: dict[str, str] = {}
    for source, target in renames.items():
        if target in reverse:
            return None
        reverse[target] = source
    if set(reverse) - set(renames):
        return None
    renamed_away = {source for source in renames if source not in reverse}
    before: set[str] = set()
    for column in demand:
        if column in reverse:
            before.add(reverse[column])
        elif column in renamed_away:
            return None
        else:
            before.add(column)
    return before


def _call_argument_columns(call: ast.Call) -> set[str] | None:
    """Columns referenced by a call's own arguments, excluding chained inputs.

    Walking the whole call would also pick up references made by earlier
    operations in the same method chain (``call.func.value``), which live in a
    different column namespace once renames are involved.
    """
    columns: set[str] = set()
    for argument in [*call.args, *(kw.value for kw in call.keywords)]:
        refs = _referenced_polars_columns(argument)
        if refs is None:
            return None
        columns |= refs
    return columns


def _ordered_expression_demands(
    tree: ast.Module,
    output_columns: set[str],
) -> set[str] | None:
    """Propagate demand backward through ordered operations.

    Three shapes need execution order. A rename changes the column namespace
    mid-pipeline: upstream of it the parent must provide the pre-rename name,
    while downstream references use the post-rename name. A
    ``with_columns``/``select`` alias derives a new column mid-pipeline:
    later references read the derived name, which the parent never had. A
    ``select``/``select_seq`` executes every output expression regardless of
    downstream demand, so the inputs of all outputs must reach it even when
    downstream only wants a subset. The unordered union walk cannot express
    any of these — it re-adds post-rename and derived names to the parent
    demand and intersects select outputs with downstream demand — so such
    code is analysed as a provable linear operation sequence and the demand
    set is translated through each operation in reverse execution order.
    """
    operations = _ordered_frame_operations(tree)
    if operations is None:
        return None

    demand = set(output_columns)
    saw_supported_operation = False
    for call in reversed(operations):
        func = call.func
        assert isinstance(func, ast.Attribute)  # guaranteed by extraction
        method = func.attr
        if method in _ROW_ONLY_CHAIN_METHODS:
            continue
        if method == "rename":
            renames = _frame_rename_mapping(call)
            if renames is None:
                return None
            translated = _demand_before_rename(demand, renames)
            if translated is None:
                return None
            demand = translated | set(renames)
            saw_supported_operation = True
        elif method == "filter":
            if any(kw.arg is None for kw in call.keywords):
                return None
            refs = _call_argument_columns(call)
            if refs is None:
                return None
            # Keyword constraints (``df.filter(segment='A')``) name columns.
            demand |= refs | {kw.arg for kw in call.keywords if kw.arg is not None}
            saw_supported_operation = True
        elif method == "fill_null":
            refs = _call_argument_columns(call)
            if refs is None:
                return None
            demand |= refs
            saw_supported_operation = True
        elif method == "with_columns":
            refs = _call_argument_columns(call)
            outputs = _with_columns_outputs(call)
            if refs is None or outputs is None:
                return None
            # An un-aliased positional expression (``pl.lit(1)``,
            # ``pl.col('a').name.suffix('_2')``, ``pl.sum_horizontal(...)``)
            # creates a column whose name ``_with_columns_outputs`` cannot
            # see, so the in-node-created name would survive backward
            # propagation and be wrongly demanded from the parent.  A bare
            # ``pl.col('x')`` is the only un-aliased shape whose output name
            # is provably its own reference; anything else bails.
            if any(_alias_name(expr) is None and _pl_col_name(expr) is None for expr in call.args):
                return None
            # Every expression executes regardless of downstream demand, so
            # all referenced inputs stay required.
            demand = (demand - outputs) | refs
            saw_supported_operation = True
        elif method in _SELECT_METHOD_NAMES:
            output_to_input = _select_output_to_input(call)
            if output_to_input is None:
                return None
            if demand - set(output_to_input):
                # Downstream demands a column this select does not produce;
                # full width lets execution raise the real error.
                return None
            # The select executes every output expression regardless of
            # downstream demand, so the inputs of ALL outputs stay required.
            demand = set()
            for input_columns in output_to_input.values():
                demand |= input_columns
            saw_supported_operation = True
        else:
            return None

    if not saw_supported_operation:
        return None
    return demand


def _unordered_expression_demands(
    tree: ast.Module,
    output_columns: set[str],
) -> set[str] | None:
    """Union-walk demand inference for rename-free single-parent Polars code.

    Without renames every operation reads and writes the same column
    namespace, so an unordered union of references and outputs is a safe
    over-approximation. Rename-bearing code must never reach this walk: the
    final ``| referenced_columns`` union would re-add post-rename names to the
    parent demand (see :func:`_ordered_expression_demands`). The same union
    re-adds derived names referenced later in the node, and its ``select``
    branch under-demands by intersecting select outputs with the downstream
    demand, so provable derived-reference and select-bearing chains are
    routed to the ordered analysis first and only the unprovable remainder
    lands here, deliberately keeping its loud over-demand (derived) and
    under-demand (select) failures.
    """
    demands = set(output_columns)
    produced_columns: set[str] = set()
    referenced_columns: set[str] = set()
    saw_supported_operation = False

    for ast_node in ast.walk(tree):
        if not isinstance(ast_node, ast.Call):
            continue
        func = ast_node.func
        if not isinstance(func, ast.Attribute):
            continue
        method = func.attr
        if method == "with_columns":
            refs = _referenced_polars_columns(ast_node)
            outputs = _with_columns_outputs(ast_node)
            if refs is None or outputs is None:
                return None
            produced_columns |= outputs
            referenced_columns |= refs
            demands |= refs
            saw_supported_operation = True
        elif method == "filter":
            if any(kw.arg is None for kw in ast_node.keywords):
                return None
            refs = _referenced_polars_columns(ast_node)
            if refs is None:
                return None
            # Keyword constraints (``df.filter(segment='A')``) name columns by
            # their kwarg, mirroring the ordered walk's filter branch. Without
            # this the parent is under-demanded and a downstream filter can lose
            # the very column it constrains on.
            kw_columns = {kw.arg for kw in ast_node.keywords if kw.arg is not None}
            referenced_columns |= refs | kw_columns
            demands |= refs | kw_columns
            saw_supported_operation = True
        elif method == "select":
            selected = _select_output_demands(ast_node, output_columns)
            if selected is None:
                return None
            demands = selected
            referenced_columns |= selected
            produced_columns = set(output_columns)
            saw_supported_operation = True
        elif method in {"alias", "cast", "is_not_null", "is_null", "fill_null"}:
            continue
        elif method in {"join", "group_by", "groupby", "agg", "sort", "unique", "explode"}:
            return None

    if not saw_supported_operation:
        return None
    return (demands - produced_columns) | referenced_columns


def _single_parent_polars_expression_demands(
    code: str,
    output_columns: set[str],
) -> set[str] | None:
    """Infer parent columns for common row-preserving single-parent Polars code.

    Ordered backward propagation is required when the code renames columns,
    calls ``select``/``select_seq``, or references a column its own
    expressions create. Renames and derived references make the unordered
    union walk re-add post-rename/derived names to the parent demand; a
    select makes it under-demand, because its narrowing intersects the
    select outputs with the downstream demand while the node still executes
    every select expression verbatim and reads all of their inputs. All
    other rename-free code keeps the union walk so its established
    narrowing (helper assignments) is preserved exactly. Unprovable
    rename-bearing code stays full width (``None``, 2.12's rule);
    unprovable select-bearing and derived-reference code falls back to the
    union walk, deliberately keeping today's loud failure at execution or
    the edge projection rather than widening or guessing a narrowing the
    planner cannot prove.
    """
    try:
        tree = ast.parse(code)
    except SyntaxError:
        return None
    if _contains_rename_call(tree):
        return _ordered_expression_demands(tree, output_columns)
    if _contains_select_call(tree) or _references_derived_column(tree):
        demand = _ordered_expression_demands(tree, output_columns)
        if demand is not None:
            return demand
    return _unordered_expression_demands(tree, output_columns)


@dataclass(frozen=True)
class SingleParentPolarsExpressionRule:
    """Projection rule for common single-parent Polars feature-engineering code."""

    name: str = POLARS_EXPRESSION_DEPENDENCY_RULE_NAME

    def parent_demands(
        self,
        node: GraphNode,
        parent_ids: Iterable[str],
        my_needed: set[str] | None,
    ) -> ParentDemandResult | None:
        parent_list = list(parent_ids)
        if node.data.nodeType != NodeType.POLARS or len(parent_list) != 1 or my_needed is None:
            return None
        produced, referenced = projection_contract(node).to_tuple()
        if produced is not None and referenced is not None:
            return None
        code = node.data.config.get("code")
        if not isinstance(code, str) or not code.strip():
            return None
        demand = _single_parent_polars_expression_demands(code, my_needed)
        if demand is None:
            return None
        return ParentDemandResult(
            default=set(demand),
            by_parent={parent_list[0]: set(demand)},
            rule_name=self.name,
        )


_SINGLE_PARENT_POLARS_RULE = SingleParentPolarsExpressionRule()


@dataclass(frozen=True)
class OpaqueContractRule:
    """Projection rule for nodes whose column contract is opaque."""

    name: str = "opaque_contract"

    def parent_demands(
        self,
        node: GraphNode,
        parent_ids: Iterable[str],
        *,
        strict_projection: bool,
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
        if strict_projection and len(parent_set) > 1 and node.data.nodeType == NodeType.POLARS:
            return _unprojected_boundary_demands()
        if strict_projection and _user_code_has_unbounded_projection_contract(node):
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
        NodeType.DATA_SOURCE,
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
    if node.data.nodeType == NodeType.DATA_SOURCE and source_user_code_preserves_column_projection(
        str(node.data.config.get("code") or "")
    ):
        return False
    produced, referenced = projection_contract(node).to_tuple()
    return produced is None or referenced is None


def _must_run_source_user_code_unprojected(node: GraphNode) -> bool:
    """Return whether a source must scan full width before post-load code.

    Source post-load code runs inside the source builder before any downstream
    edge projection.  If that code may inspect columns outside the downstream
    demand, pushing scan projection into the builder would be incorrect.  The
    safe bounded strategy is to scan full width, run the source code, then let
    downstream edges/checkpoints narrow the frame again.
    """
    return node.data.nodeType in {
        NodeType.API_INPUT,
        NodeType.DATA_SOURCE,
        NodeType.EXTERNAL_FILE,
    } and _user_code_has_unbounded_projection_contract(node)


def _raise_if_unbounded_user_code_is_terminal(
    node: GraphNode,
    parent_ids: Iterable[str],
    *,
    strict_projection: bool,
) -> None:
    _ = node, parent_ids, strict_projection
    return


def opaque_contract_demands_for_node(
    node: GraphNode,
    parent_ids: Iterable[str],
    *,
    strict_projection: bool,
) -> ParentDemandResult:
    """Return parent demand for an opaque projection contract.

    Opaque nodes usually force their parents opaque.  The exception is a
    malformed fan-in declaration: if a node declares `inputs_by_parent`, then
    projection also needs concrete `inputs` and `outputs` so it can decide what
    each parent owns.
    """
    return _OPAQUE_CONTRACT_RULE.parent_demands(
        node,
        parent_ids,
        strict_projection=strict_projection,
    )


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
        *,
        strict_projection: bool,
    ) -> ParentDemandResult | None:
        parent_set = set(parent_ids)
        if len(parent_set) <= 1 or node.data.nodeType != NodeType.POLARS:
            return None

        parent_inputs = declared_inputs_by_parent(node, parent_set)
        if parent_inputs is None:
            _ = strict_projection
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

        joins = _join_calls_for_parent_inputs(
            node,
            parent_set,
            strict_projection=strict_projection,
        )
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
                strict_projection=strict_projection,
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
    *,
    strict_projection: bool,
) -> ParentDemandResult | None:
    """Return routed demands for concrete multi-parent fan-in nodes."""
    return _POLARS_FAN_IN_RULE.parent_demands(
        node,
        parent_ids,
        base_contribution,
        referenced,
        strict_projection=strict_projection,
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
        parent_ids: Iterable[str],
        base_contribution: set[str],
        referenced: set[str],
        parent_produced: Mapping[str, set[str] | None],
        *,
        strict_projection: bool,
    ) -> ParentDemandResult:
        _ = (referenced, strict_projection)
        parent_set = set(parent_ids)
        # Resolve roles and validate config; fail loudly on stale/missing roles.
        source_ids = sorted(parent_set)
        base_index, join_index = resolve_edge_join_role_indices(
            node.data.config,
            source_ids,
        )
        base_parent = source_ids[base_index]
        join_parent = source_ids[join_index]

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
    parent_ids: Iterable[str],
    base_contribution: set[str],
    referenced: set[str],
    parent_produced: Mapping[str, set[str] | None],
    *,
    strict_projection: bool,
) -> ParentDemandResult | None:
    """Return routed demands for an opaque-contract edge-join fan-in node."""
    if node.data.nodeType != NodeType.EDGE_JOIN or len(set(parent_ids)) <= 1:
        return None
    return _EDGE_JOIN_FAN_IN_RULE.parent_demands(
        node,
        parent_ids,
        base_contribution,
        referenced,
        parent_produced,
        strict_projection=strict_projection,
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
            NodeType.DATA_SOURCE: _coverage(NodeType.DATA_SOURCE, _SOURCE_SCAN_RULE_NAME),
            NodeType.EXTERNAL_FILE: _coverage(NodeType.EXTERNAL_FILE, _SOURCE_SCAN_RULE_NAME),
            NodeType.CONSTANT: _coverage(NodeType.CONSTANT, _SOURCE_SCAN_RULE_NAME),
            NodeType.POLARS: _coverage(
                NodeType.POLARS,
                _GENERIC_CONTRACT_RULE_NAME,
                _POLARS_FAN_IN_RULE.name,
            ),
            NodeType.EDGE_JOIN: _coverage(
                NodeType.EDGE_JOIN,
                _EDGE_JOIN_FAN_IN_RULE.name,
                note=(
                    "edge joins keep an opaque column contract but route demand "
                    "concretely via parent produced-column ownership"
                ),
            ),
            NodeType.DATA_INPUT: _coverage(
                NodeType.DATA_INPUT,
                _OPAQUE_CONTRACT_RULE.name,
                opaque=True,
                note=(
                    "registry-driven source with an opaque column contract; lazy-scan "
                    "formats get projection pushdown from downstream selects inside "
                    "the polars plan, eager formats read full width (bounded profiles "
                    "refuse them before parse)"
                ),
            ),
            NodeType.DATA_OUTPUT: _coverage(NodeType.DATA_OUTPUT, _GENERIC_CONTRACT_RULE_NAME),
            NodeType.BANDING: _coverage(NodeType.BANDING, _GENERIC_CONTRACT_RULE_NAME),
            NodeType.RATING_STEP: _coverage(NodeType.RATING_STEP, _GENERIC_CONTRACT_RULE_NAME),
            NodeType.OUTPUT: _coverage(NodeType.OUTPUT, _GENERIC_CONTRACT_RULE_NAME),
            NodeType.DATA_SINK: _coverage(NodeType.DATA_SINK, _GENERIC_CONTRACT_RULE_NAME),
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


def _join_calls_for_parent_inputs(
    node: GraphNode,
    parent_ids: Iterable[str],
    *,
    strict_projection: bool = False,
) -> list[_JoinCallInfo]:
    """Infer simple Polars join calls between incoming parents from node code."""
    code = node.data.config.get("code")
    if not isinstance(code, str) or ".join" not in code:
        return []
    parent_set = set(parent_ids)
    try:
        tree = ast.parse(code)
    except SyntaxError as exc:
        if strict_projection:
            raise ProjectionImpossibleError(
                "Fan-in join projection could not be parsed.",
                node_id=node.id,
                node_type=node.data.nodeType.value,
                reason=str(exc),
                line=exc.lineno,
                offset=exc.offset,
            ) from exc
        return []

    aliases = _parent_aliases(tree, parent_set)
    joins: list[_JoinCallInfo] = []

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
            if strict_projection:
                raise ProjectionImpossibleError(
                    "Fan-in join projection requires literal how/suffix arguments.",
                    node_id=node.id,
                    node_type=node.data.nodeType.value,
                )
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
    return joins


def simple_join_calls_for_parent_inputs(
    node: GraphNode,
    parent_ids: Iterable[str],
    *,
    strict_projection: bool = False,
) -> tuple[_JoinCallInfo, ...]:
    """Return simple inferred Polars joins between incoming parent frames."""
    return tuple(
        _join_calls_for_parent_inputs(
            node,
            parent_ids,
            strict_projection=strict_projection,
        )
    )


def join_parent_demands(
    node: GraphNode,
    parent_ids: Iterable[str],
    output_columns: set[str],
    *,
    strict_projection: bool = False,
) -> tuple[dict[str, set[str]], set[str]]:
    """Return parent input columns inferred from simple Polars join output columns."""
    joins = _join_calls_for_parent_inputs(
        node,
        parent_ids,
        strict_projection=strict_projection,
    )
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
) -> list[GraphEdge]:
    """Remove edges to live-switch nodes from inputs inactive for *source*."""
    switch_nodes = {
        nid: node for nid, node in node_map.items() if node.data.nodeType == NodeType.LIVE_SWITCH
    }
    if not switch_nodes:
        return edges

    exclude: set[tuple[str, str]] = set()
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
            parent_name = _sanitize_func_name(parent.data.label)
            mapped = input_scenario_map.get(parent_name)
            if mapped is not None and mapped != source:
                exclude.add((edge.source, nid))

    if not exclude:
        return edges
    return [edge for edge in edges if (edge.source, edge.target) not in exclude]


def prepare_graph(
    graph: PipelineGraph,
    target_node_id: str | None = None,
    *,
    source: str = "live",
) -> PreparedGraph:
    """Prepare graph lookups used by projection planning."""
    node_map = graph.node_map
    edges = prune_live_switch_edges(graph.edges, node_map, source)

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
    )


def compute_prepared_plan(
    order: list[str],
    children_of: Mapping[str, Iterable[str]],
    node_map: Mapping[str, GraphNode],
    required_columns_by_node: Mapping[str, Iterable[str] | AllExceptColumns] | None = None,
    *,
    strict_projection: bool = False,
) -> ProjectionPlan:
    """Run the reverse topological projection sweep on a prepared graph."""
    needed: dict[str, set[str] | None] = {}
    edge_demands: dict[tuple[str, str], set[str] | None] = {}
    node_reasons: dict[str, ProjectionReason] = {}
    edge_reasons: dict[tuple[str, str], ProjectionReason] = {}
    seeded_required = normalise_required_columns_by_node(required_columns_by_node, order)
    parents_by_child: dict[str, set[str]] = {node_id: set() for node_id in order}
    for parent_id, child_ids in children_of.items():
        for child_id in child_ids:
            if child_id in parents_by_child:
                parents_by_child[child_id].add(parent_id)

    contribution: dict[str, ParentDemandResult] = {}
    for node_id in reversed(order):
        node = node_map[node_id]
        children = list(children_of.get(node_id, ()))

        has_seed = node_id in seeded_required
        seed = seeded_required.get(node_id, set())
        if not children:
            if node.data.nodeType == NodeType.OUTPUT:
                mapping = node.data.config.get("outputMapping") or []
                source_cols = {e["source_column"] for e in mapping if e.get("enabled", True)}
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
            for child_id in children:
                child_contrib = contribution[child_id].for_parent(node_id)
                if child_contrib is None:
                    accumulated = None
                    break
                assert accumulated is not None
                accumulated |= child_contrib
            needed[node_id] = accumulated
            node_reasons[node_id] = ProjectionReason(
                rule="child_demand",
                message="downstream child demand",
            )

        if has_seed:
            if isinstance(seed, AllExceptColumns):
                if needed[node_id] is not None:
                    existing = needed[node_id]
                    if existing is None:
                        raise RuntimeError("concrete projection branch unexpectedly became opaque")
                    existing |= set(seed.keep)
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
                if len(children) <= 1:
                    needed[node_id] = set(seed)
                    node_reasons[node_id] = ProjectionReason(
                        rule="projection_seed",
                        message="caller required columns",
                    )
                elif strict_projection:
                    raise ProjectionImpossibleError(
                        "Projection seed cannot replace opaque demand from "
                        "multiple downstream consumers.",
                        node_id=node_id,
                        node_type=node.data.nodeType.value,
                        seeded_columns=sorted(seed),
                        child_node_ids=sorted(children),
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

        if strict_projection and _must_run_source_user_code_unprojected(node):
            needed[node_id] = None
            node_reasons[node_id] = ProjectionReason(
                rule=UNPROJECTED_STREAMING_BOUNDARY_RULE_NAME,
                message="source user code requires full-width source scan",
            )

        my_needed = needed[node_id]
        parent_ids = parents_by_child.get(node_id, set())
        routed_demands = parent_demands_for_node(
            node,
            parent_ids,
            my_needed,
            seeded_required,
        )
        if routed_demands is not None:
            for parent_id, parent_demand in routed_demands.by_parent.items():
                edge_demands[(parent_id, node_id)] = (
                    None if parent_demand is None else set(parent_demand)
                )
                edge_reasons[(parent_id, node_id)] = ProjectionReason(
                    rule=routed_demands.rule_name,
                    message="node-specific parent demand",
                )
            contribution[node_id] = ParentDemandResult(
                default=routed_demands.default,
                by_parent=routed_demands.by_parent,
                rule_name=routed_demands.rule_name,
            )
            continue

        if my_needed is None:
            _raise_if_unbounded_user_code_is_terminal(
                node,
                parent_ids,
                strict_projection=strict_projection,
            )
            contribution[node_id] = ParentDemandResult(
                default=None,
                by_parent={},
                rule_name="opaque_demand",
            )
            continue

        produced, referenced = projection_contract(node).to_tuple()
        if produced is None or referenced is None:
            parent_produced = {
                parent_id: _parent_produced_columns(node_map[parent_id]) for parent_id in parent_ids
            }
            edge_join_demands = edge_join_fan_in_demands_for_node(
                node,
                parent_ids,
                set(my_needed),
                set(),
                parent_produced,
                strict_projection=strict_projection,
            )
            if edge_join_demands is not None:
                for parent_id, parent_demand in edge_join_demands.by_parent.items():
                    edge_demands[(parent_id, node_id)] = (
                        None if parent_demand is None else set(parent_demand)
                    )
                    edge_reasons[(parent_id, node_id)] = ProjectionReason(
                        rule=edge_join_demands.rule_name,
                        message="edge-join fan-in ownership rule",
                    )
                contribution[node_id] = ParentDemandResult(
                    default=edge_join_demands.default,
                    by_parent=edge_join_demands.by_parent,
                    rule_name=edge_join_demands.rule_name,
                )
                continue
            opaque_demands = opaque_contract_demands_for_node(
                node,
                parent_ids,
                strict_projection=strict_projection,
            )
            contribution[node_id] = ParentDemandResult(
                default=opaque_demands.default,
                by_parent=opaque_demands.by_parent,
                rule_name=opaque_demands.rule_name,
            )
            continue

        base_contribution = (my_needed - produced) | referenced
        fan_in_demands = fan_in_demands_for_node(
            node,
            parent_ids,
            base_contribution,
            referenced,
            strict_projection=strict_projection,
        )
        if fan_in_demands is not None:
            for parent_id, parent_demand in fan_in_demands.by_parent.items():
                edge_demands[(parent_id, node_id)] = (
                    None if parent_demand is None else set(parent_demand)
                )
                edge_reasons[(parent_id, node_id)] = ProjectionReason(
                    rule=fan_in_demands.rule_name,
                    message="fan-in ownership rule",
                )
            contribution[node_id] = ParentDemandResult(
                default=fan_in_demands.default,
                by_parent=fan_in_demands.by_parent,
                rule_name=fan_in_demands.rule_name,
            )
            continue

        contribution[node_id] = ParentDemandResult(
            default=base_contribution,
            by_parent={},
            rule_name="contract_algebra",
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
    edge_demands: Mapping[tuple[str, str], set[str] | None],
    *,
    node_reasons: Mapping[str, ProjectionReason] | None = None,
    edge_reasons: Mapping[tuple[str, str], ProjectionReason] | None = None,
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
        edge_reasons=MappingProxyType(dict(edge_reasons or {})),
    )
    return ProjectionPlan(
        needed_by_node=MappingProxyType(frozen_needed),
        edge_demands=MappingProxyType(frozen_edges),
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

    for (parent_id, child_id), columns in projection_plan.edge_demands.items():
        if node_id is not None and child_id != node_id and parent_id != node_id:
            continue
        if column is not None and columns is not None and column not in columns:
            continue
        reason = projection_plan.diagnostics.edge_reasons.get(
            (parent_id, child_id),
            ProjectionReason(rule="edge_demand", message="edge demand"),
        )
        lines.append(
            f"{parent_id} -> {child_id}: {reason.rule}: {reason.message} [{_column_text(columns)}]"
        )

    return tuple(lines)


def with_runtime_inferred_streaming_edges(
    projection_plan: ProjectionPlan,
    *,
    child_id: str,
    demands_by_parent: Mapping[str, Iterable[str]],
) -> ProjectionPlan:
    """Return *projection_plan* annotated with runtime-inferred join demands."""
    if not demands_by_parent:
        return projection_plan

    edge_demands = dict(projection_plan.edge_demands)
    edge_reasons = dict(projection_plan.diagnostics.edge_reasons)
    for parent_id, columns in demands_by_parent.items():
        frozen_columns = frozenset(columns)
        edge = (parent_id, child_id)
        edge_demands[edge] = frozen_columns
        edge_reasons[edge] = ProjectionReason(
            rule=RUNTIME_INFERRED_STREAMING_RULE_NAME,
            message="runtime-inferred streaming join demand",
            details={
                "strategy": RUNTIME_INFERRED_STREAMING_RULE_NAME,
                "columns": tuple(sorted(frozen_columns)),
            },
        )

    return ProjectionPlan(
        needed_by_node=projection_plan.needed_by_node,
        edge_demands=MappingProxyType(edge_demands),
        materialisation_boundaries=projection_plan.materialisation_boundaries,
        opaque_boundaries=projection_plan.opaque_boundaries,
        diagnostics=ProjectionDiagnostics(
            opaque_reasons=projection_plan.diagnostics.opaque_reasons,
            node_reasons=projection_plan.diagnostics.node_reasons,
            edge_reasons=MappingProxyType(edge_reasons),
        ),
    )


def plan(request: ProjectionRequest) -> ProjectionPlan:
    """Compute a shared projection plan for *request*."""
    prepared = prepare_graph(
        request.graph,
        request.target_node_id,
        source=request.source,
    )
    return compute_prepared_plan(
        prepared.order,
        _children_of(prepared.order, prepared.parents_of),
        prepared.node_map,
        required_columns_by_node=request.required_columns_by_node,
        strict_projection=strict_projection_required(
            request.profile,
            request.required_columns_by_node,
        ),
    )
