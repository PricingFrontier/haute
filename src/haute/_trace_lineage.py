"""The traced value's lineage: which columns of each node the value depends on.

A column trace walks back from the target asking, of every node, which of its
output columns the traced value depends on. A node that computes one of them
depends on what that column was computed from; a node that only carries one
passes it on to the parent it came from. The walk reads the steps the trace
already correlated and enriched, so it follows the same formulas, model
features and optimiser columns the trace panel shows.
"""

from __future__ import annotations

from collections.abc import Callable, Collection, Mapping, Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Final

from haute._edge_join import build_edge_join_kwargs
from haute._expression_parser import AssignmentPhases, assignment_phases, parse_expression
from haute._trace_enrichment import _effective_node_code, _wrap_node_code
from haute._types import NodeType

if TYPE_CHECKING:
    from haute._types import GraphNode
    from haute.trace import TraceStep


class _EveryColumn:
    """Every column of a node's output: how the demanded value was derived is unknown."""

    __slots__ = ()

    def __repr__(self) -> str:
        return "EVERY_COLUMN"


EVERY_COLUMN: Final = _EveryColumn()

_Demand = set[str] | _EveryColumn


class _Unreadable:
    """A column the node computes in a way the trace cannot read."""

    __slots__ = ()


_UNREADABLE: Final = _Unreadable()

# Nodes that compute nothing: every output column is a parent's, even one the
# step's schema diff shows as added because the parent holding it was not
# correlated.
_ROUTER_TYPES: Final = frozenset({NodeType.EDGE_JOIN, NodeType.LIVE_SWITCH, NodeType.DATA_OUTPUT})

# Joins whose output key columns hold the base row's key values.
_BASE_KEY_JOINS: Final = frozenset({"inner", "left", "semi", "anti"})


@dataclass(frozen=True)
class ValueLineage:
    """The nodes a traced value's lineage reaches, and what each step computes for it."""

    reached: frozenset[str]
    contributed: Mapping[str, tuple[str, ...]]

    def contributed_columns(self, node_id: str) -> list[str]:
        return list(self.contributed.get(node_id, ()))


def trace_value_lineage(
    *,
    column: str,
    target_node_id: str,
    steps: Sequence[TraceStep],
    order: Sequence[str],
    parents_of: Mapping[str, Sequence[str]],
    node_map: Mapping[str, GraphNode],
    attempted: Collection[str],
    output_columns: Callable[[str], set[str] | None],
    edge_join_roles: Mapping[str, tuple[str, str]],
) -> ValueLineage:
    """Walk *column*'s lineage back from *target_node_id* over the traced steps.

    *order* is topological, so visiting it in reverse settles every node's
    demand before the node is read. Demand only flows to parents that were
    correlated (a step) or *attempted* (unresolved); an unresolved node's own
    derivation is unknown, so it depends on every input column.
    *output_columns* gives a node's output columns, or ``None`` when unknown.
    """
    step_by_id = {step.node_id: step for step in steps}
    demand: dict[str, _Demand] = {target_node_id: {column}}
    contributed: dict[str, tuple[str, ...]] = {}
    reached: set[str] = set()
    for node_id in reversed(order):
        wanted = demand.get(node_id)
        if not wanted:
            continue
        reached.add(node_id)
        graph_parents = parents_of.get(node_id, ())
        parents = [p for p in graph_parents if p in step_by_id or p in attempted]
        step = step_by_id.get(node_id)
        needed: _Demand
        if step is None:
            needed = EVERY_COLUMN
        else:
            made, needed = _step_demand(
                step, node_map[node_id], wanted, node_map, is_source=not graph_parents
            )
            if made:
                contributed[node_id] = tuple(sorted(made))
        routed = _route(
            node_map[node_id], needed, parents, output_columns, edge_join_roles.get(node_id)
        )
        for parent_id, parent_demand in routed.items():
            _merge(demand, parent_id, parent_demand)
    return ValueLineage(reached=frozenset(reached), contributed=contributed)


def _merge(demand: dict[str, _Demand], node_id: str, more: _Demand) -> None:
    current = demand.get(node_id)
    if current is EVERY_COLUMN:
        return
    if isinstance(more, _EveryColumn) or current is None:
        demand[node_id] = more if isinstance(more, _EveryColumn) else set(more)
        return
    assert isinstance(current, set)
    current.update(more)


def _step_demand(
    step: TraceStep,
    node: GraphNode,
    wanted: _Demand,
    node_map: Mapping[str, GraphNode],
    *,
    is_source: bool,
) -> tuple[set[str], _Demand]:
    """The columns *step* computes for the value, and what it needs from its inputs."""
    if node.data.nodeType in _ROUTER_TYPES:
        return set(), wanted
    diff = step.schema_diff
    produced = set(diff.columns_added) | set(diff.columns_modified)
    if is_source:
        return (produced if isinstance(wanted, _EveryColumn) else produced & wanted), set()
    if isinstance(wanted, _EveryColumn):
        return produced, EVERY_COLUMN
    derivation = _Derivation(step, node, produced, node_map)
    for column in sorted(wanted):
        if column in produced:
            derivation.contribute(column)
        else:
            derivation.read_input(column)
    return derivation.made, derivation.needed


class _Derivation:
    """What one step's computed columns were computed from."""

    def __init__(
        self,
        step: TraceStep,
        node: GraphNode,
        produced: set[str],
        node_map: Mapping[str, GraphNode],
    ) -> None:
        self.step = step
        self.node = node
        self.produced = produced
        config = node.data.config if isinstance(node.data.config, dict) else {}
        self.config: dict[str, Any] = config
        self.code = _wrap_node_code(_effective_node_code(config, node_map))
        self.made: set[str] = set()
        self.needed: _Demand = set()

    def read_input(self, column: str) -> None:
        if isinstance(self.needed, set):
            self.needed.add(column)

    def contribute(self, column: str) -> None:
        if column in self.made:
            return
        self.made.add(column)
        by_code = self._code_derivation(column)
        if isinstance(by_code, _Unreadable):
            self.needed = EVERY_COLUMN
            return
        if by_code is not None:
            references, phases = by_code
            for reference in references:
                self._code_reference(column, reference, phases)
            return
        by_rule = self._rule_references(column)
        if by_rule is None or isinstance(by_rule, _Unreadable):
            # A computed column nothing explains depends on every input.
            self.needed = EVERY_COLUMN
            return
        for reference in by_rule:
            self._rule_reference(reference)

    def _code_derivation(
        self, column: str
    ) -> tuple[list[str], AssignmentPhases | None] | _Unreadable | None:
        """The column's formula in the node's code; ``None`` when the code does not assign it."""
        if not self.code.strip():
            return None
        parsed = parse_expression(self.code, column)
        if parsed is None:
            return None
        if parsed.expression_type != "opaque":
            return list(parsed.referenced_columns), assignment_phases(self.code, column)
        # An opaque parse with no text found no assignment of the column.
        return None if not parsed.expression_text else _UNREADABLE

    def _code_reference(self, column: str, reference: str, phases: AssignmentPhases | None) -> None:
        if reference == column:
            # A formula reading its own column reads the value from before it.
            if phases is not None and column in phases.before:
                self.needed = EVERY_COLUMN
                return
            before_code = self._rule_references(column)
            if before_code is None:
                self.read_input(column)
            elif isinstance(before_code, _Unreadable):
                self.needed = EVERY_COLUMN
            else:
                for earlier in before_code:
                    self._rule_reference(earlier)
            return
        if reference not in self.produced:
            self.read_input(reference)
            return
        known = phases is not None and not phases.unresolved_before
        if known and phases is not None:
            assigned_before = reference in phases.before
            assigned_after = reference in phases.at_or_after
            if assigned_before and not assigned_after:
                self.contribute(reference)
                return
            if assigned_after and not assigned_before:
                self.read_input(reference)
                return
        self.contribute(reference)
        self.read_input(reference)

    def _rule_reference(self, reference: str) -> None:
        if reference in self.produced:
            self.contribute(reference)
        self.read_input(reference)

    def _rule_references(self, column: str) -> tuple[str, ...] | _Unreadable | None:
        """What a generated or detail-explained column reads; ``None`` when no rule explains it."""
        node_type = self.node.data.nodeType
        if node_type == NodeType.SCENARIO_EXPANDER and column in {
            self.config.get("column_name"),
            self.config.get("step_column", "scenario_index"),
        }:
            return ()
        detail = self.step.node_detail
        if not isinstance(detail, dict):
            return None
        detail_type = detail.get("detail_type")
        if detail_type not in {"model_score", "optimiser_apply", "rating_step", "banding"}:
            return None
        if "error" in detail:
            return _UNREADABLE
        if detail_type == "model_score":
            return _model_score_references(detail, column)
        if detail_type == "optimiser_apply":
            return _optimiser_apply_references(detail, column)
        if detail_type == "rating_step":
            return _rating_step_references(detail, column)
        return _banding_references(detail, column)


def _strings(values: Any) -> tuple[str, ...] | _Unreadable:
    if not isinstance(values, list | tuple) or not values:
        return _UNREADABLE
    if not all(isinstance(value, str) and value for value in values):
        return _UNREADABLE
    return tuple(values)


def _model_score_references(
    detail: Mapping[str, Any], column: str
) -> tuple[str, ...] | _Unreadable | None:
    if column != detail.get("prediction_column"):
        return None
    return _strings(detail.get("feature_columns"))


def _optimiser_apply_references(
    detail: Mapping[str, Any], column: str
) -> tuple[str, ...] | _Unreadable | None:
    if column != detail.get("output_column"):
        return None
    if detail.get("mode") == "online":
        objective = detail.get("objective_column")
        if not isinstance(objective, str) or not objective:
            return _UNREADABLE
        constraints = detail.get("constraints")
        read = [
            detail.get("quote_id_column"),
            detail.get("scenario_index_column"),
            detail.get("scenario_value_column"),
            *(constraints if isinstance(constraints, dict) else {}),
        ]
        return (objective, *(name for name in read if isinstance(name, str) and name))
    if detail.get("mode") == "ratebook":
        factors = detail.get("factors")
        if not isinstance(factors, list) or not factors:
            return _UNREADABLE
        columns: list[str] = []
        for factor in factors:
            inputs = _strings(factor.get("input_columns") if isinstance(factor, dict) else None)
            if isinstance(inputs, _Unreadable):
                return _UNREADABLE
            columns.extend(inputs)
        return tuple(columns)
    return _UNREADABLE


def _rating_step_references(
    detail: Mapping[str, Any], column: str
) -> tuple[str, ...] | _Unreadable | None:
    for table in detail.get("tables") or []:
        if isinstance(table, dict) and table.get("output_column") == column:
            factors = table.get("factors") or []
            return _strings([f.get("column") if isinstance(f, dict) else None for f in factors])
    for combined in detail.get("combined_outputs") or []:
        if isinstance(combined, dict) and combined.get("column") == column:
            inputs = combined.get("input_values")
            return _strings(list(inputs) if isinstance(inputs, dict) else None)
    return None


def _banding_references(
    detail: Mapping[str, Any], column: str
) -> tuple[str, ...] | _Unreadable | None:
    for factor in detail.get("factors") or []:
        if isinstance(factor, dict) and factor.get("output_column") == column:
            return _strings([factor.get("input_column")])
    return None


def _route(
    node: GraphNode,
    needed: _Demand,
    parents: Sequence[str],
    output_columns: Callable[[str], set[str] | None],
    join_roles: tuple[str, str] | None,
) -> dict[str, _Demand]:
    """Send the columns a node needs from its inputs to the parents that supply them."""
    if not parents or not needed:
        return {}
    if isinstance(needed, _EveryColumn):
        return {parent_id: EVERY_COLUMN for parent_id in parents}
    if node.data.nodeType == NodeType.EDGE_JOIN and join_roles is not None:
        return _route_edge_join(node, needed, parents, output_columns, join_roles)
    routed: dict[str, _Demand] = {}
    for parent_id in parents:
        columns = output_columns(parent_id)
        wanted = set(needed) if columns is None else needed & columns
        if wanted:
            routed[parent_id] = wanted
    return routed


def _route_edge_join(
    node: GraphNode,
    needed: set[str],
    parents: Sequence[str],
    output_columns: Callable[[str], set[str] | None],
    join_roles: tuple[str, str],
) -> dict[str, _Demand]:
    """Route a join's needed columns to the side whose value its output holds.

    Polars keeps the base's copy of a colliding column under its own name and
    names the join side's copy ``<col><suffix>``. Key columns of an inner or
    left join hold the base row's values; the join side's key only chose which
    join row matched, so it is not part of the value.
    """
    base_id, join_id = join_roles
    kwargs = build_edge_join_kwargs(node.data.config)
    how: str = kwargs["how"]
    suffix: str = kwargs["suffix"]
    base_columns = output_columns(base_id)
    join_columns = output_columns(join_id)
    to_base: set[str] = set()
    to_join: set[str] = set()
    for column in needed:
        if base_columns is None or join_columns is None:
            to_base.add(column)
            to_join.add(column)
            continue
        original = column[: -len(suffix)] if suffix and column.endswith(suffix) else None
        if original and original in base_columns and original in join_columns:
            to_join.add(original)
            if how not in _BASE_KEY_JOINS:
                to_base.add(original)
            continue
        if column in base_columns:
            to_base.add(column)
            if how not in _BASE_KEY_JOINS and column in join_columns:
                to_join.add(column)
        elif column in join_columns:
            to_join.add(column)
    routed: dict[str, _Demand] = {}
    for parent_id, wanted in ((base_id, to_base), (join_id, to_join)):
        if wanted and parent_id in parents:
            _merge(routed, parent_id, wanted)
    return routed
