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
from typing import TYPE_CHECKING, Any, Final, Literal

from haute._column_lineage import _referenced_columns
from haute._edge_join import build_edge_join_kwargs
from haute._expression_parser import (
    AssignmentPhases,
    _locate_defining_expression,
    assignment_phases,
    parse_expression,
)
from haute._trace_correlation import _edge_join_key_pairs
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
class ColumnSource:
    """Where a value a step read was computed: a node and its column.

    ``before_code`` marks a value a node's rule generated before its code
    reassigned the column, so the node's row holds a later value.
    """

    node_id: str
    column: str
    before_code: bool = False


@dataclass(frozen=True)
class ColumnRead:
    """A column a computed column read, and every node that computed the value read.

    No source means the walk found none; more than one means the value may
    have come from any of them.
    """

    column: str
    sources: tuple[ColumnSource, ...]


@dataclass(frozen=True)
class ValueLineage:
    """The nodes a traced value's lineage reaches, and what each step computes for it.

    ``reads`` maps a node and a column it contributes to the columns that
    column was computed from, or to ``None`` when the walk could not tell
    which inputs it read.
    """

    reached: frozenset[str]
    contributed: Mapping[str, tuple[str, ...]]
    reads: Mapping[str, Mapping[str, tuple[ColumnRead, ...] | None]]

    def contributed_columns(self, node_id: str) -> list[str]:
        return list(self.contributed.get(node_id, ()))

    def column_reads(self, node_id: str, column: str) -> tuple[ColumnRead, ...] | None:
        return self.reads.get(node_id, {}).get(column, ())


@dataclass(frozen=True)
class JoinInputs:
    """An Edge Join's two inputs, each a parent node and the frame handle its edge reads."""

    base: tuple[str, str | None]
    join: tuple[str, str | None]


def trace_value_lineage(
    *,
    column: str,
    target_node_id: str,
    steps: Sequence[TraceStep],
    order: Sequence[str],
    parents_of: Mapping[str, Sequence[str]],
    node_map: Mapping[str, GraphNode],
    attempted: Collection[str],
    output_columns: Callable[[str, str | None], set[str] | None],
    source_handles: Mapping[tuple[str, str], Sequence[str | None]],
    join_inputs: Mapping[str, JoinInputs],
) -> ValueLineage:
    """Walk *column*'s lineage back from *target_node_id* over the traced steps.

    *order* is topological, so visiting it in reverse settles every node's
    demand before the node is read. Demand only flows to parents that were
    correlated (a step) or *attempted* (unresolved); an unresolved node's own
    derivation is unknown, so it depends on every input column.
    *output_columns* gives the columns of a node's output — of the frame a
    handle names, for a multi-frame output — or ``None`` when unknown;
    *source_handles* lists the handles each (parent, child) pair's edges read.
    """
    step_by_id = {step.node_id: step for step in steps}
    demand: dict[str, _Demand] = {target_node_id: {column}}
    contributed: dict[str, tuple[str, ...]] = {}
    reached: set[str] = set()
    made_by: dict[str, set[str]] = {}
    local_reads: dict[str, _LocalReads] = {}

    def edge_columns(parent_id: str, child_id: str) -> set[str] | None:
        columns: set[str] = set()
        for handle in source_handles.get((parent_id, child_id)) or (None,):
            frame_columns = output_columns(parent_id, handle)
            if frame_columns is None:
                return None
            columns |= frame_columns
        return columns

    def lineage_parents(node_id: str) -> list[str]:
        return [p for p in parents_of.get(node_id, ()) if p in step_by_id or p in attempted]

    def route(node_id: str, needed: set[str]) -> dict[str, _Demand]:
        """The parents *needed* columns come from, each with its own names for them."""
        parents = lineage_parents(node_id)
        roles = join_inputs.get(node_id)
        if node_map[node_id].data.nodeType == NodeType.EDGE_JOIN and roles is not None:
            return _route_edge_join(node_map[node_id], needed, parents, output_columns, roles)
        routed: dict[str, _Demand] = {}
        for parent_id in parents:
            columns = edge_columns(parent_id, node_id)
            carried = set(needed) if columns is None else needed & columns
            if carried:
                routed[parent_id] = carried
        return routed

    for node_id in reversed(order):
        wanted = demand.get(node_id)
        if not wanted:
            continue
        reached.add(node_id)
        graph_parents = parents_of.get(node_id, ())
        parents = lineage_parents(node_id)
        step = step_by_id.get(node_id)
        needed: _Demand
        if step is None:
            needed = EVERY_COLUMN
        else:
            made, needed, local_reads[node_id] = _step_demand(
                step, node_map[node_id], wanted, node_map, is_source=not graph_parents
            )
            made_by[node_id] = made
            # A helper the step dropped is not in its row, so only what the row
            # holds is reported.
            shown = made & set(step.output_values)
            if shown:
                contributed[node_id] = tuple(sorted(shown))
        if not parents or not needed:
            continue
        routed: dict[str, _Demand] = (
            {parent_id: EVERY_COLUMN for parent_id in parents}
            if isinstance(needed, _EveryColumn)
            else route(node_id, needed)
        )
        for parent_id, parent_demand in routed.items():
            _merge(demand, parent_id, parent_demand)

    resolved: dict[tuple[str, str], tuple[ColumnSource, ...]] = {}

    def computed_by(node_id: str, column: str) -> tuple[ColumnSource, ...]:
        """The nodes that computed the value *node_id* read as *column* from its inputs.

        The value is followed up the same routes the walk sent the demand, past
        every node that only carried it, to the nodes that computed it. A node
        the trace could not correlate ends the walk as the source.
        """
        key = (node_id, column)
        if key not in resolved:
            resolved[key] = ()
            found: set[ColumnSource] = set()
            for parent_id, parent_columns in route(node_id, {column}).items():
                assert isinstance(parent_columns, set)
                for parent_column in parent_columns:
                    if parent_id not in step_by_id or parent_column in made_by.get(parent_id, ()):
                        found.add(ColumnSource(parent_id, parent_column))
                    else:
                        found.update(computed_by(parent_id, parent_column))
            resolved[key] = tuple(sorted(found, key=lambda s: (s.node_id, s.column)))
        return resolved[key]

    reads: dict[str, dict[str, tuple[ColumnRead, ...] | None]] = {}
    for node_id, shown_columns in contributed.items():
        node_reads = local_reads[node_id]
        reads[node_id] = {}
        for shown_column in shown_columns:
            local = node_reads.get(shown_column, [])
            if local is None:
                reads[node_id][shown_column] = None
                continue
            sources: dict[str, set[ColumnSource]] = {}
            for reference, kind in local:
                if kind == "input":
                    sources.setdefault(reference, set()).update(computed_by(node_id, reference))
                else:
                    sources.setdefault(reference, set()).add(
                        ColumnSource(node_id, reference, before_code=kind == "before_code")
                    )
            reads[node_id][shown_column] = tuple(
                ColumnRead(
                    reference,
                    tuple(sorted(found, key=lambda s: (s.node_id, s.column, s.before_code))),
                )
                for reference, found in sorted(sources.items())
            )
    return ValueLineage(reached=frozenset(reached), contributed=contributed, reads=reads)


def _merge(demand: dict[str, _Demand], node_id: str, more: _Demand) -> None:
    current = demand.get(node_id)
    if current is EVERY_COLUMN:
        return
    if isinstance(more, _EveryColumn) or current is None:
        demand[node_id] = more if isinstance(more, _EveryColumn) else set(more)
        return
    assert isinstance(current, set)
    current.update(more)


_ReadKind = Literal["here", "before_code", "input"]
# Per computed column, the columns it read and where each read resolves: this
# node's own value ("here"), a value its rule generated before its code
# rewrote it ("before_code"), or its inputs ("input"). ``None`` when the
# column's inputs could not be told apart.
_LocalReads = dict[str, list[tuple[str, _ReadKind]] | None]


def _step_demand(
    step: TraceStep,
    node: GraphNode,
    wanted: _Demand,
    node_map: Mapping[str, GraphNode],
    *,
    is_source: bool,
) -> tuple[set[str], _Demand, _LocalReads]:
    """The columns *step* computes for the value, what it needs from its inputs,
    and what each computed column read."""
    if node.data.nodeType in _ROUTER_TYPES:
        return set(), wanted, {}
    diff = step.schema_diff
    changed = set(diff.columns_added) | set(diff.columns_modified)
    if is_source:
        loaded = changed if isinstance(wanted, _EveryColumn) else changed & wanted
        return loaded, set(), {column: [] for column in loaded}
    if isinstance(wanted, _EveryColumn):
        return changed, EVERY_COLUMN, dict.fromkeys(changed)
    derivation = _Derivation(step, node, changed, node_map)
    for column in sorted(wanted):
        if derivation.computes(column):
            derivation.contribute(column)
        else:
            derivation.read_input(column)
    return derivation.made, derivation.needed, derivation.local_reads()


class _Derivation:
    """What one step's computed columns were computed from.

    A step computes a column its row shows added or changed, its code assigns
    (even to the value it already held, or to a helper column it later drops),
    or a rule of the node generates. Rules (a scenario expander's generated
    columns, a model's prediction, a rating table, a banding factor, an
    optimiser apply's output) run before the node's code.
    """

    def __init__(
        self,
        step: TraceStep,
        node: GraphNode,
        changed: set[str],
        node_map: Mapping[str, GraphNode],
    ) -> None:
        self.step = step
        self.node = node
        self.changed = changed
        config = node.data.config if isinstance(node.data.config, dict) else {}
        self.config: dict[str, Any] = config
        self.code = _wrap_node_code(_effective_node_code(config, node_map))
        self.made: set[str] = set()
        self.needed: _Demand = set()
        # Columns whose final value, and whose value from before the code,
        # were already followed: one column can be both.
        self.final: set[str] = set()
        self.before_code: set[str] = set()
        # The computed column whose reads are being followed, innermost last;
        # ``None`` for a generated value the code then rewrote, whose reads
        # the row does not show.
        self._deriving: list[str | None] = []
        self._reads: dict[str, list[tuple[str, _ReadKind]]] = {}
        self._reads_unknown: set[str] = set()

    def local_reads(self) -> _LocalReads:
        return {
            column: None if column in self._reads_unknown else list(self._reads.get(column, ()))
            for column in self.made
        }

    def _note(self, reference: str, kind: _ReadKind) -> None:
        if self._deriving and self._deriving[-1] is not None:
            self._reads.setdefault(self._deriving[-1], []).append((reference, kind))

    def read_input(self, column: str) -> None:
        self._note(column, "input")
        if isinstance(self.needed, set):
            self.needed.add(column)

    def depend_on_every_input(self) -> None:
        if self._deriving and self._deriving[-1] is not None:
            self._reads_unknown.add(self._deriving[-1])
        self.needed = EVERY_COLUMN

    def computes(self, column: str) -> bool:
        return (
            column in self.changed
            or self._code_writes(column)
            or isinstance(self._rule_references(column), tuple)
        )

    def contribute(self, column: str) -> None:
        self._note(column, "here")
        if column in self.final:
            return
        self.final.add(column)
        self.made.add(column)
        self._deriving.append(column)
        try:
            self._derive(column)
        finally:
            self._deriving.pop()

    def _derive(self, column: str) -> None:
        by_code = self._code_derivation(column)
        if isinstance(by_code, _Unreadable):
            self.depend_on_every_input()
            return
        if by_code is not None:
            references, phases = by_code
            for reference in sorted(references):
                self._code_reference(column, reference, phases)
            return
        by_rule = self._rule_references(column)
        if not isinstance(by_rule, tuple):
            # A computed column nothing explains depends on every input.
            self.depend_on_every_input()
            return
        for reference in by_rule:
            self._value_before_code(reference)

    def _code_writes(self, column: str) -> bool:
        return bool(self.code.strip()) and (
            assignment_phases(self.code, column) is not None
            or _locate_defining_expression(self.code, column) is not None
        )

    def _code_derivation(
        self, column: str
    ) -> tuple[set[str], AssignmentPhases | None] | _Unreadable | None:
        """The columns the code's last assignment of *column* reads.

        ``None`` when the code does not assign the column. References come from
        the fail-closed column lineage of the defining expression, so a column
        read by name only (``over("region")``) counts, and an expression whose
        columns cannot all be named is unreadable.
        """
        if not self.code.strip():
            return None
        parsed = parse_expression(self.code, column)
        if parsed is None:
            return None
        if parsed.expression_type == "opaque":
            # An opaque parse with no text found no assignment of the column.
            return None if not parsed.expression_text else _UNREADABLE
        defining = _locate_defining_expression(self.code, column)
        references = None if defining is None else _referenced_columns(defining)
        if references is None:
            return _UNREADABLE
        return set(references) | set(parsed.referenced_columns), assignment_phases(
            self.code, column
        )

    def _code_reference(self, column: str, reference: str, phases: AssignmentPhases | None) -> None:
        """Resolve a column an assignment of *column* reads to the value it saw."""
        if reference == column:
            # A formula reading its own column reads the value from before it.
            if phases is not None and column in phases.before:
                self.depend_on_every_input()
            else:
                self._value_before_code(column, phases)
            return
        if not self._code_writes(reference):
            if phases is not None and phases.unresolved_before and self.computes(reference):
                self.depend_on_every_input()
            elif self.computes(reference):
                self.contribute(reference)
            else:
                self.read_input(reference)
            return
        if phases is None or phases.unresolved_before:
            self.depend_on_every_input()
            return
        assigned_before = reference in phases.before
        assigned_after = reference in phases.at_or_after
        if assigned_before and not assigned_after:
            # The code's last assignment of the reference is the one it saw.
            self.contribute(reference)
        elif assigned_after and not assigned_before:
            self._value_before_code(reference, phases)
        else:
            # Which of several assignments it saw is not followed.
            self.depend_on_every_input()

    def _value_before_code(self, column: str, phases: AssignmentPhases | None = None) -> None:
        """Resolve the value *column* held before the node's code ran."""
        if phases is not None and phases.unresolved_before:
            self.depend_on_every_input()
            return
        by_rule = self._rule_references(column)
        if by_rule is None:
            self.read_input(column)
            return
        if isinstance(by_rule, _Unreadable):
            self.depend_on_every_input()
            return
        # The node's rule computed it before the code ran.
        self.made.add(column)
        rewritten = self._code_writes(column)
        self._note(column, "before_code" if rewritten else "here")
        if column in self.before_code:
            return
        self.before_code.add(column)
        # When the code rewrote the value, the row holds the later one, so what
        # the rule read is followed for the demand but not reported.
        self._deriving.append(None if rewritten else column)
        try:
            for reference in by_rule:
                if reference == column:
                    # An in-place rule (a banding factor overwriting its input)
                    # reads the value from before it: the input's.
                    self.read_input(column)
                else:
                    self._value_before_code(reference)
        finally:
            self._deriving.pop()

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


def _constraint_columns(name: str, entry: Any) -> list[Any]:
    """A constraint reads its own column, or a ratio constraint its numerator and denominator."""
    spec = entry.get("spec") if isinstance(entry, dict) else None
    if isinstance(spec, dict) and ("numerator" in spec or "denominator" in spec):
        return [name, spec.get("numerator"), spec.get("denominator")]
    return [name]


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
        read: list[Any] = [
            detail.get("quote_id_column"),
            detail.get("scenario_index_column"),
            detail.get("scenario_value_column"),
        ]
        if isinstance(constraints, dict):
            for name, entry in constraints.items():
                read.extend(_constraint_columns(name, entry))
        if any(name is not None and not isinstance(name, str) for name in read):
            return _UNREADABLE
        return (objective, *(name for name in read if name))
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


def _route_edge_join(
    node: GraphNode,
    needed: set[str],
    parents: Sequence[str],
    output_columns: Callable[[str, str | None], set[str] | None],
    roles: JoinInputs,
) -> dict[str, _Demand]:
    """Route a join's needed columns to the side whose value its output holds.

    Polars keeps the base's copy of a colliding column under its own name and
    names the join side's copy ``<col><suffix>``. Key columns of an inner or
    left join hold the base row's values; the join side's key only chose which
    join row matched, so it is not part of the value. A right, full or cross
    join's key can hold either side's value, so a key goes to both sides under
    each side's own key name.
    """
    (base_id, base_handle), (join_id, join_handle) = roles.base, roles.join
    kwargs = build_edge_join_kwargs(node.data.config)
    how: str = kwargs["how"]
    suffix: str = kwargs["suffix"]
    key_pairs = _edge_join_key_pairs(kwargs)
    base_columns = output_columns(base_id, base_handle)
    join_columns = output_columns(join_id, join_handle)
    to_base: set[str] = set()
    to_join: set[str] = set()
    for column in needed:
        if base_columns is None or join_columns is None:
            to_base.add(column)
            to_join.add(column)
            continue
        if how not in _BASE_KEY_JOINS:
            pairs = [pair for pair in key_pairs if column in pair]
            if pairs:
                for left_key, right_key in pairs:
                    to_base.add(left_key)
                    to_join.add(right_key)
                continue
        original = column[: -len(suffix)] if suffix and column.endswith(suffix) else None
        if original and original in base_columns and original in join_columns:
            to_join.add(original)
            continue
        if column in base_columns:
            to_base.add(column)
        elif column in join_columns:
            to_join.add(column)
    routed: dict[str, _Demand] = {}
    for parent_id, wanted in ((base_id, to_base), (join_id, to_join)):
        if wanted and parent_id in parents:
            _merge(routed, parent_id, wanted)
    return routed
