"""Lazy and eager graph execution — shared by executor, trace, and scorer."""

from __future__ import annotations

import ast
import gc
import re
import time
from collections.abc import Callable, Iterable, Mapping
from enum import StrEnum
from pathlib import Path
from typing import Any, NamedTuple

import polars as pl

from haute._builders import _passthrough_fn
from haute._contracts import Contract, get_column_contract
from haute._graph_utils import (
    _sanitize_func_name,
    build_parents_of,
    resolve_orig_source_names,
)
from haute._logging import get_logger
from haute._path_resolution import resolve_runtime_file_path
from haute._polars_utils import _malloc_trim, safe_sink
from haute._topo import ancestors, topo_sort_ids
from haute._types import (
    GraphEdge,
    GraphNode,
    NodeType,
    PipelineGraph,
    _Frame,
)
from haute.errors import ContractMismatchError

logger = get_logger(component="execute")


_PATH_CONFIG_BY_NODE_TYPE: dict[NodeType, str] = {
    NodeType.API_INPUT: "path",
    NodeType.DATA_SOURCE: "path",
    NodeType.EXTERNAL_FILE: "path",
    NodeType.DATA_SINK: "path",
}


def _resolve_graph_paths(graph: PipelineGraph) -> PipelineGraph:
    """Resolve project/pipeline-relative file paths before building node functions."""
    if not graph.source_file:
        return graph
    nodes: list[GraphNode] = []
    changed = False
    for node in graph.nodes:
        config = node.data.config
        key = _PATH_CONFIG_BY_NODE_TYPE.get(node.data.nodeType)
        if node.data.nodeType == NodeType.OPTIMISER_APPLY and config.get("sourceType") == "file":
            key = "artifact_path"
        if key is None:
            nodes.append(node)
            continue
        raw_path = config.get(key)
        if isinstance(raw_path, str) and raw_path:
            resolved = str(
                resolve_runtime_file_path(
                    raw_path,
                    source_file=graph.source_file,
                    prefer="project",
                )
            )
            if resolved != raw_path:
                data = node.data.model_copy(update={"config": {**config, key: resolved}})
                nodes.append(node.model_copy(update={"data": data}))
                changed = True
            else:
                nodes.append(node)
        else:
            nodes.append(node)
    if not changed:
        return graph
    return graph.model_copy(update={"nodes": nodes})


# ---------------------------------------------------------------------------
# Column contract enforcement
# ---------------------------------------------------------------------------


def _compute_boundary_check_exceptions() -> tuple[type[BaseException], ...]:
    """Exception classes the boundary contract check treats as recoverable.

    We only catch classes that describe genuine "can't resolve the
    contract right now" conditions — bad config, missing files, MLflow
    reachability.  Programmer bugs (``AttributeError``, ``TypeError``,
    ``KeyError``) propagate so they aren't silently masked.

    Narrowed deliberately:

    * ``RuntimeError`` is **not** included.  The
      ``"Persistently corrupt model artifact"`` ``RuntimeError`` raised by
      ``_load_with_bounded_retry`` is a real infrastructure problem the
      operator must see — swallowing it at contract-check time and
      falling back to opaque hides the failure until the node itself
      runs, by which point the log signal is buried under whatever
      follow-on noise the rewrap produced.
    * ``ImportError`` is **not** included.  A missing optional backend
      (catboost / rustystats) is a deploy-configuration bug and should
      surface loudly at the first site that notices it, not be silently
      downgraded to an opaque contract.

    MLflow's ``MlflowException`` covers the legitimate "tracking store
    unreachable" case and is included when the dep is importable.
    """
    from haute.errors import ConfigError

    exc_types: list[type[BaseException]] = [ConfigError, OSError]
    try:
        from mlflow.exceptions import MlflowException  # type: ignore[import-untyped]

        exc_types.append(MlflowException)
    except ImportError:
        pass
    return tuple(exc_types)


def _is_boundary_check_exception(exc: BaseException) -> bool:
    """Return whether *exc* should degrade contract checking to opaque."""
    from haute.errors import ConfigError

    if isinstance(exc, (ConfigError, OSError)):
        return True
    try:
        from mlflow.exceptions import MlflowException  # type: ignore[import-untyped]
    except ImportError:
        return False
    return isinstance(exc, MlflowException)


def _effective_contract(node: GraphNode) -> Contract:
    """Return the effective contract for a node at boundary-check time.

    Combines the builder-derived contract with any user-declared
    contract on the node's config so the executor has a single answer
    to "what columns does this node read / produce?".

    User-declared sides override the builder when they are concrete
    (non-None).  This lets a user tighten an opaque POLARS contract to
    a concrete set; the reverse — a user declaring opaque on top of a
    concrete builder contract — is accepted silently because the parser
    has already cross-checked against ``get_column_contract``.

    If the builder contract raises (MLflow unreachable, config mis-set
    in a way only the builder knows about), the executor treats the
    node as opaque rather than failing the whole run: the runtime path
    for such nodes is typically ``_passthrough_fn`` and the caller will
    still get the original error on the direct ``_model_score_columns``
    call path that the loud-errors suite exercises.  Silencing here is
    scoped strictly to the boundary check; it does not hide the
    configuration issue elsewhere in the system.
    """
    from haute.errors import ConfigError

    try:
        builder = Contract.from_tuple(get_column_contract(node.data.nodeType, node.data.config))
    except Exception as exc:
        if not _is_boundary_check_exception(exc):
            raise
        # Contract resolution for MODEL_SCORE etc. may touch MLflow /
        # external stores.  A transient or deploy-mode lookup failure
        # (ConfigError, OSError, MLflow REST) must not prevent the
        # pipeline from running — the fn builder path has its own
        # error reporting and will surface the real problem when the
        # node actually executes.  We fall back to opaque so the
        # boundary check is skipped for this node; the actual node
        # code path still runs and still fails loudly via whichever
        # error it has always produced.  Programmer errors
        # (AttributeError / TypeError / KeyError) propagate.
        if not isinstance(exc, ConfigError):
            logger.debug(
                "effective_contract_unresolved",
                node_id=node.id,
                node_type=node.data.nodeType.value,
                error=repr(exc),
            )
        builder = Contract.opaque()
    return _overlay_declared_contract(node, builder)


def _projection_contract(node: GraphNode) -> Contract:
    """Return the column contract used by projection analysis.

    Unlike :func:`_effective_contract`, this path does not soften builder
    contract failures. Projection happens before node execution, so a
    malformed concrete contract should be visible rather than quietly
    widening the graph. The only extra behaviour over the registry lookup is
    honoring a user-declared concrete contract on otherwise opaque transform
    nodes.
    """
    builder = Contract.from_tuple(get_column_contract(node.data.nodeType, node.data.config))
    return _overlay_declared_contract(node, builder)


def _overlay_declared_contract(node: GraphNode, builder: Contract) -> Contract:
    """Apply any user-declared contract fields over a builder contract."""
    declared_raw = node.data.config.get("contract")
    if declared_raw is None:
        return builder
    try:
        declared = Contract.from_user_declared(declared_raw)
    except ValueError as exc:
        # Malformed contract on a user's graph should raise up so the
        # mistake is visible.  The executor is the wrong place to
        # silently drop it.
        raise ContractMismatchError(
            "Node contract annotation is malformed and cannot be interpreted.",
            node_id=node.id,
            node_type=node.data.nodeType.value,
            reason=str(exc),
        ) from exc
    if declared is None:
        return builder
    inputs = declared.inputs if declared.inputs is not None else builder.inputs
    outputs = declared.outputs if declared.outputs is not None else builder.outputs
    return Contract(inputs=inputs, outputs=outputs)


def _assert_inputs_satisfy_contract(
    node: GraphNode,
    contract: Contract,
    upstream_columns: frozenset[str],
) -> None:
    """Raise ``ContractMismatchError`` if *upstream_columns* is missing
    any column the node's contract says it reads.

    No-op when the contract's input side is opaque (``None``).
    """
    if contract.inputs is None:
        return
    missing = contract.inputs - upstream_columns
    if not missing:
        return
    raise ContractMismatchError(
        "Input columns required by the node's contract are missing from the upstream frame.",
        node_id=node.id,
        node_type=node.data.nodeType.value,
        missing=sorted(missing),
        extra=sorted(upstream_columns - contract.inputs),
        declared_inputs=sorted(contract.inputs),
        upstream_columns=sorted(upstream_columns),
    )


def _assert_outputs_satisfy_contract(
    node: GraphNode,
    contract: Contract,
    output_columns: frozenset[str],
) -> None:
    """Raise ``ContractMismatchError`` if *output_columns* is missing
    any column the node's contract promised to produce.

    We check ⊇ (outputs must be present) rather than == because
    pass-through style nodes legitimately carry additional columns
    through from their input.  A declared output that is absent is a
    bug (typo or buggy user code); an extra column is expected.

    No-op when the contract's output side is opaque (``None``).
    """
    if contract.outputs is None:
        return
    missing = contract.outputs - output_columns
    if not missing:
        return
    raise ContractMismatchError(
        "Output columns promised by the node's contract are missing from the node's result.",
        node_id=node.id,
        node_type=node.data.nodeType.value,
        missing=sorted(missing),
        extra=sorted(output_columns - contract.outputs),
        declared_outputs=sorted(contract.outputs),
        observed_columns=sorted(output_columns),
    )


def _should_check_contract(contract: Contract) -> bool:
    """Return ``True`` iff either side of *contract* is concrete.

    A fully-opaque contract cannot be disproven, so skipping the check
    saves the per-node column-set computation entirely.  This matters
    for the <5% overhead bound when a pipeline is dominated by opaque
    nodes (user polars transforms).
    """
    return contract.inputs is not None or contract.outputs is not None


def _normalise_required_columns_by_node(
    required_columns_by_node: Mapping[str, Iterable[str]] | None,
    order: list[str],
) -> dict[str, set[str]]:
    """Validate caller-provided projection seeds for concrete node outputs."""
    if not required_columns_by_node:
        return {}

    executable_ids = set(order)
    normalised: dict[str, set[str]] = {}
    for node_id, raw_columns in required_columns_by_node.items():
        if not isinstance(node_id, str) or not node_id:
            raise ValueError("required_columns_by_node keys must be non-empty node ids.")
        if node_id not in executable_ids:
            raise ValueError(
                f"required_columns_by_node references node {node_id!r}, "
                "but that node is not in the lazy execution target."
            )
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


# ---------------------------------------------------------------------------
# Checkpoint projection — backward column analysis
# ---------------------------------------------------------------------------


class _ProjectionContribution(NamedTuple):
    """Columns a child contributes to its parents during projection analysis."""

    default: set[str] | None
    by_parent: dict[str, set[str] | None]

    def for_parent(self, parent_id: str) -> set[str] | None:
        return self.by_parent.get(parent_id, self.default)


class _ProjectionPlan(NamedTuple):
    """Column projection needs at nodes and parent-specific fan-in edges."""

    needed_by_node: dict[str, set[str] | None]
    edge_demands: dict[tuple[str, str], set[str] | None]


def _declared_inputs_by_parent(
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
            "Fan-in projection contract references unknown parent(s) or "
            "omits incoming parent(s).",
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


def _unambiguous_passthrough_parent(
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


def _literal_string(node: ast.AST) -> str | None:
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    return None


class _JoinCallInfo(NamedTuple):
    left_parent: str
    right_parent: str
    how: str
    suffix: str


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
) -> list[_JoinCallInfo]:
    """Infer simple Polars join calls between incoming parents from node code."""
    code = node.data.config.get("code")
    if not isinstance(code, str) or ".join" not in code:
        return []
    parent_set = set(parent_ids)
    try:
        tree = ast.parse(code)
    except SyntaxError:
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
        for kw in ast_node.keywords:
            if kw.arg == "how":
                how = _literal_string(kw.value) or how
            elif kw.arg == "suffix":
                suffix = _literal_string(kw.value) or suffix
        joins.append(
            _JoinCallInfo(
                left_parent=left_parent,
                right_parent=right_parent,
                how=how,
                suffix=suffix,
            )
        )
    return joins


def _join_parent_demands(
    node: GraphNode,
    parent_ids: Iterable[str],
    output_columns: set[str],
) -> tuple[dict[str, set[str]], set[str]]:
    """Return parent input columns inferred from simple Polars join output columns."""
    joins = _join_calls_for_parent_inputs(node, parent_ids)
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

    remaining = output_columns - handled
    if not remaining:
        return demands, handled
    for join in joins:
        preserved_parent: str | None = None
        if join.how == "left":
            preserved_parent = join.left_parent
        elif join.how == "right":
            preserved_parent = join.right_parent
        if preserved_parent is None:
            continue
        demands.setdefault(preserved_parent, set()).update(remaining)
        handled |= remaining
        break

    return demands, handled


def _ratebook_factor_required_columns(config: Mapping[str, Any]) -> set[str]:
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
    return columns


def _optimiser_parent_demands(
    node: GraphNode,
    parent_ids: Iterable[str],
    data_input_columns: set[str] | None,
) -> dict[str, set[str] | None] | None:
    """Return configured parent-specific demands for multi-parent optimiser nodes."""
    parent_set = set(parent_ids)
    if len(parent_set) <= 1 or node.data.nodeType != NodeType.OPTIMISER:
        return None

    config = node.data.config
    data_input = config.get("data_input")
    banding_source = config.get("banding_source")
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

    by_parent: dict[str, set[str] | None] = {parent_id: set() for parent_id in parent_set}
    by_parent[data_input] = None if data_input_columns is None else set(data_input_columns)

    if config.get("mode", "online") == "ratebook":
        if not isinstance(banding_source, str) or not banding_source:
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
        factor_columns = _ratebook_factor_required_columns(config)
        existing = by_parent[banding_source]
        by_parent[banding_source] = (
            set(factor_columns) if existing is None else set(existing) | factor_columns
        )

    return by_parent


def _compute_projection_plan(
    order: list[str],
    children_of: dict[str, list[str]],
    node_map: dict[str, GraphNode],
    required_columns_by_node: Mapping[str, Iterable[str]] | None = None,
) -> _ProjectionPlan:
    """Single reverse-topological sweep computing per-node column needs.

    For each node *n*, ``needed[n]`` is the set of columns from *n*'s
    output that any downstream consumer actually uses.  ``None`` means
    "all columns" (the requirement cannot be determined — an opaque
    node is downstream, or an OUTPUT asks for everything).

    Each node also has a *contribution* — the set a parent must union
    in for this node as a child, namely
    ``(needed[n] - produced_n) | referenced_n``.  Contributions are
    cached per node so a parent with fan-in ``k`` folds ``k``
    pre-computed sets instead of re-running contract lookup and set
    algebra ``k`` times.  This turns the pass from
    ``O(edges × contract_lookups)`` into ``O(V + E)`` with one contract
    lookup per node.

    Opaque contribution (``None``) from any child forces the parent's
    ``needed`` to ``None`` as a short-circuit.  Multi-parent POLARS nodes
    still fence parent propagation unless the contract includes
    ``inputs_by_parent`` ownership metadata.  When that metadata is present,
    the pass records a separate demand for each parent edge so sibling
    branches are not asked for each other's columns.
    """
    needed: dict[str, set[str] | None] = {}
    edge_demands: dict[tuple[str, str], set[str] | None] = {}
    seeded_required = _normalise_required_columns_by_node(required_columns_by_node, order)
    parents_by_child: dict[str, set[str]] = {nid: set() for nid in order}
    for parent_id, child_ids in children_of.items():
        for child_id in child_ids:
            if child_id in parents_by_child:
                parents_by_child[child_id].add(parent_id)
    # Per-node contribution to parents.  ``None`` means "parent must
    # fall to None" — either this node is opaque or any of its
    # descendants is.  Each entry is written exactly once per node.
    contribution: dict[str, _ProjectionContribution] = {}

    for nid in reversed(order):
        node = node_map[nid]
        children = children_of.get(nid, [])

        has_seed = nid in seeded_required
        seed = seeded_required.get(nid, frozenset())
        if not children:
            # Terminal node — determine what it needs from its input.
            if node.data.nodeType == NodeType.OUTPUT:
                fields = node.data.config.get("fields") or []
                needed[nid] = set(fields) if fields else None
            else:
                needed[nid] = None
        else:
            # Union of pre-computed child contributions.  Each
            # contribution was set in this same loop when the child
            # was visited (reverse topo order guarantees children are
            # processed before parents).  A single ``None`` child
            # contribution short-circuits the union to ``None``.
            acc: set[str] | None = set()
            for cid in children:
                child_contrib = contribution[cid].for_parent(nid)
                if child_contrib is None:
                    acc = None
                    break
                acc |= child_contrib  # type: ignore[operator]
            needed[nid] = acc
        if has_seed:
            if needed[nid] is None:
                # A seed can replace the opaque terminal demand from the
                # caller that consumes this node directly (for example the
                # optimiser data_input).  It must not override an unrelated
                # opaque sibling branch, because that sibling will still be
                # executed and may need columns outside the seed.
                if len(children) <= 1:
                    needed[nid] = set(seed)
            else:
                needed[nid] |= seed

        # Cache this node's contribution to its parents.  Computed
        # once here; every parent that visits this node as a child
        # reads the cached value instead of re-fetching the contract
        # and re-doing the set algebra.
        my_needed = needed[nid]
        parent_ids = parents_by_child.get(nid, set())
        if node.data.nodeType == NodeType.OPTIMISER:
            data_input_id = node.data.config.get("data_input")
            seeded_data_input = (
                set(seeded_required[data_input_id])
                if isinstance(data_input_id, str) and data_input_id in seeded_required
                else None
            )
            routed_demands = _optimiser_parent_demands(
                node,
                parent_ids,
                my_needed if my_needed is not None else seeded_data_input,
            )
            if routed_demands is not None:
                for parent_id, parent_demand in routed_demands.items():
                    edge_demands[(parent_id, nid)] = (
                        None if parent_demand is None else set(parent_demand)
                    )
                contribution[nid] = _ProjectionContribution(
                    default=None,
                    by_parent=routed_demands,
                )
                continue

        if my_needed is None:
            contribution[nid] = _ProjectionContribution(default=None, by_parent={})
            continue
        produced, referenced = _projection_contract(node).to_tuple()
        if produced is None or referenced is None:
            parent_inputs = _declared_inputs_by_parent(node, parents_by_child.get(nid, ()))
            if parent_inputs is not None:
                raise ContractMismatchError(
                    "Fan-in projection contract requires concrete 'inputs' "
                    "and 'outputs' on the node contract.",
                    node_id=nid,
                    node_type=node.data.nodeType.value,
                )
            contribution[nid] = _ProjectionContribution(default=None, by_parent={})
            continue

        base_contribution = (my_needed - produced) | referenced
        if len(parent_ids) > 1 and node.data.nodeType == NodeType.POLARS:
            parent_inputs = _declared_inputs_by_parent(node, parent_ids)
            if parent_inputs is None:
                contribution[nid] = _ProjectionContribution(default=None, by_parent={})
                continue

            opaque_parent_ids = [
                parent_id for parent_id, parent_columns in parent_inputs.items()
                if parent_columns is None
            ]
            if opaque_parent_ids:
                raise ContractMismatchError(
                    "Fan-in projection contract inputs_by_parent must be fully concrete.",
                    node_id=nid,
                    node_type=node.data.nodeType.value,
                    opaque_parent_ids=sorted(opaque_parent_ids),
                )

            covered: set[str] = set()
            by_parent: dict[str, set[str] | None] = {}
            for parent_id, parent_columns in parent_inputs.items():
                assert parent_columns is not None
                covered |= parent_columns
                parent_demand = base_contribution & parent_columns
                by_parent[parent_id] = parent_demand
                edge_demands[(parent_id, nid)] = set(parent_demand)

            missing = base_contribution - covered
            if missing:
                join_demands, handled_missing = _join_parent_demands(
                    node,
                    parent_ids,
                    missing,
                )
                for parent_id, extra_columns in join_demands.items():
                    parent_demand = by_parent[parent_id]
                    assert parent_demand is not None
                    parent_demand |= extra_columns
                    edge_demands[(parent_id, nid)] = set(parent_demand)
                missing -= handled_missing
                if missing:
                    passthrough_parent = _unambiguous_passthrough_parent(
                        parent_inputs,
                        referenced,
                    )
                    if passthrough_parent is not None:
                        parent_demand = by_parent[passthrough_parent]
                        assert parent_demand is not None
                        parent_demand |= missing
                        edge_demands[(passthrough_parent, nid)] = set(parent_demand)
                        missing = set()
                if missing:
                    raise ContractMismatchError(
                        "Fan-in projection contract does not cover columns "
                        "required by the node.",
                        node_id=nid,
                        node_type=node.data.nodeType.value,
                        missing=sorted(missing),
                        declared_inputs_by_parent={
                            pid: sorted(cols) if cols is not None else None
                            for pid, cols in parent_inputs.items()
                        },
                    )

            contribution[nid] = _ProjectionContribution(default=None, by_parent=by_parent)
            continue

        contribution[nid] = _ProjectionContribution(default=base_contribution, by_parent={})

    return _ProjectionPlan(needed_by_node=needed, edge_demands=edge_demands)


def _compute_needed_columns(
    order: list[str],
    children_of: dict[str, list[str]],
    node_map: dict[str, GraphNode],
    required_columns_by_node: Mapping[str, Iterable[str]] | None = None,
) -> dict[str, set[str] | None]:
    """Return per-node output needs from the full projection plan."""
    return _compute_projection_plan(
        order,
        children_of,
        node_map,
        required_columns_by_node=required_columns_by_node,
    ).needed_by_node


# ---------------------------------------------------------------------------
# Adaptive checkpoint strategy
# ---------------------------------------------------------------------------

# Number of checkpoints between gc.collect() + _malloc_trim() calls.
# Polars objects use Rust Arc refcounting and are freed immediately on
# ``del``; Python gc.collect() only helps with cyclic garbage (rare here).
# Batching avoids the overhead of scanning all Python objects per checkpoint.
_GC_BATCH_INTERVAL = 3


class _CheckpointAction(StrEnum):
    """What to do at a potential checkpoint boundary."""

    SKIP = "skip"
    """Keep the LazyFrame as-is — no materialization needed."""

    COLLECT_LAZY = "collect_lazy"
    """Materialize in RAM via ``collect().lazy()`` to break plan
    duplication without disk I/O.  Only used when the estimated
    intermediate fits comfortably in available memory."""

    PARQUET = "parquet"
    """Sink to a temp parquet file and replace with ``scan_parquet``.
    The safest option — frees RAM and isolates the query plan."""


def _checkpoint_decision(
    nid: str,
    is_source: bool,
    n_parents: int,
    n_children: int,
    feeds_join: bool,
    node_map: dict[str, GraphNode],
    scenario: str,
) -> _CheckpointAction:
    """Decide whether and how to checkpoint a node's output.

    Uses the same three structural triggers as before (joins, fan-outs,
    join-feeders) but skips MODEL_SCORE nodes in batch mode because
    the batched scorer already sinks to temp parquet and returns
    ``scan_parquet(scored_path)`` — an implicit checkpoint.  Adding
    another parquet round-trip on top is pure waste.
    """
    if is_source:
        return _CheckpointAction.SKIP

    needs_checkpoint = n_parents > 1 or n_children > 1 or feeds_join
    if not needs_checkpoint:
        return _CheckpointAction.SKIP

    # MODEL_SCORE in batch mode already returns scan_parquet — skip.
    node = node_map.get(nid)
    if node is not None and node.data.nodeType == NodeType.MODEL_SCORE and scenario != "live":
        return _CheckpointAction.SKIP

    return _CheckpointAction.PARQUET


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------


def _apply_column_renames(
    frame: pl.LazyFrame | pl.DataFrame,
    config: dict[str, Any],
) -> pl.LazyFrame | pl.DataFrame:
    """Apply column renames from *config*'s ``column_renames``.

    ``column_renames`` is a ``dict[str, str]`` mapping original column names
    to new names.  Only renames for columns that actually exist in the frame
    are applied.  A no-op when the dict is absent or empty.
    """
    renames: dict[str, str] | None = config.get("column_renames")
    if not renames:
        return frame

    if isinstance(frame, pl.LazyFrame):
        all_cols = set(frame.collect_schema().names())
    else:
        all_cols = set(frame.columns)

    valid = {old: new for old, new in renames.items() if old in all_cols and old != new}
    if valid:
        return frame.rename(valid)
    return frame


def _apply_selected_columns(
    frame: pl.LazyFrame | pl.DataFrame,
    config: dict[str, Any],
) -> pl.LazyFrame | pl.DataFrame:
    """Filter *frame* to only the columns listed in *config*'s ``selected_columns``.

    If ``selected_columns`` is absent, empty, or names no valid columns the
    frame is returned unchanged.  Only columns that actually exist in the
    frame are kept, and the filter is a no-op when every column is selected
    (avoids an unnecessary projection).
    """
    sel_cols: list[str] | None = config.get("selected_columns")
    if not sel_cols:
        return frame

    if isinstance(frame, pl.LazyFrame):
        all_cols = frame.collect_schema().names()
    else:
        all_cols = frame.columns

    seen: set[str] = set()
    valid = []
    for c in sel_cols:
        if c in all_cols and c not in seen:
            valid.append(c)
            seen.add(c)
    if valid and len(valid) < len(all_cols):
        return frame.select(valid)
    return frame


def _prune_live_switch_edges(
    edges: list[GraphEdge],
    node_map: dict[str, GraphNode],
    source: str,
) -> list[GraphEdge]:
    """Remove edges to live_switch nodes from inputs inactive for *source*.

    A live_switch node's config contains ``input_scenario_map`` which maps
    each input name to the scenario it serves.  Only edges from inputs
    matching the active source are kept; the unused branch is pruned so
    it is neither executed nor shown in profilers.
    """
    switch_nodes = {
        nid: node for nid, node in node_map.items() if node.data.nodeType == NodeType.LIVE_SWITCH
    }
    if not switch_nodes:
        return edges

    exclude: set[tuple[str, str]] = set()
    for nid, node in switch_nodes.items():
        ism: dict[str, str] = node.data.config.get(
            "input_scenario_map",
            {},
        )
        if not ism:
            continue
        # If no input matches the active source, keep all edges
        # so the runtime fallback in switch_fn still works.
        if source not in ism.values():
            continue
        # For each direct parent edge, check if its name maps to a
        # different source — if so, exclude the edge.
        for e in edges:
            if e.target != nid:
                continue
            parent = node_map.get(e.source)
            if parent is None:
                continue
            parent_name = _sanitize_func_name(parent.data.label)
            mapped = ism.get(parent_name)
            if mapped is not None and mapped != source:
                exclude.add((e.source, nid))

    if not exclude:
        return edges
    return [e for e in edges if (e.source, e.target) not in exclude]


def _prepare_graph(
    graph: PipelineGraph,
    target_node_id: str | None = None,
    source: str = "live",
) -> tuple[
    dict[str, GraphNode],  # node_map
    list[str],  # order (topo-sorted node IDs)
    dict[str, list[str]],  # parents_of
    dict[str, str],  # id_to_name
]:
    """Shared graph preparation: filter, topo-sort, and build lookups.

    Returns (node_map, order, parents_of, id_to_name).
    """
    node_map = graph.node_map
    edges = _prune_live_switch_edges(graph.edges, node_map, source)

    # ``node_map`` is an insertion-ordered ``dict``; deriving the ID list
    # by iterating it preserves that order all the way into
    # ``topo_sort_ids``'s insertion-order tie-break.  Going through a
    # ``set`` would have introduced hash-randomisation into sibling
    # execution order.
    all_ids = set(node_map)
    if target_node_id:
        needed = ancestors(target_node_id, edges, all_ids)
    else:
        needed = all_ids

    relevant_edges = [e for e in edges if e.source in needed and e.target in needed]
    order = topo_sort_ids([nid for nid in node_map if nid in needed], relevant_edges)

    parents_of = build_parents_of(relevant_edges, set(order))

    id_to_name: dict[str, str] = {}
    for nid in order:
        label = node_map[nid].data.label
        id_to_name[nid] = _sanitize_func_name(label)

    return node_map, order, parents_of, id_to_name


def _execute_lazy(
    graph: PipelineGraph,
    build_node_fn: Callable,
    target_node_id: str | None = None,
    preamble_ns: dict | None = None,
    source: str = "live",
    checkpoint_dir: Path | None = None,
    enforce_contracts: bool = False,
    preserve_node_ids: set[str] | frozenset[str] | None = None,
    required_columns_by_node: Mapping[str, Iterable[str]] | None = None,
) -> tuple[dict[str, _Frame], list[str], dict[str, list[str]], dict[str, str]]:
    """Execute a graph lazily and return per-node LazyFrames.

    Used by execute_sink (batch writes) and score_graph (deploy scoring)
    where Polars can optimise the full lazy plan end-to-end.
    Interactive paths (preview, trace) use eager execution with caching
    instead — see executor._eager_execute and trace.execute_trace.

    Args:
        graph: React Flow graph with "nodes" and "edges".
        build_node_fn: Function (node_dict, source_names) -> (name, fn, is_source).
        target_node_id: If set, only execute ancestors of this node.
        source: Active execution source (``"live"`` = eager scoring).
        checkpoint_dir: If set, multi-input nodes (joins) and fan-out
            nodes (>1 downstream consumer) are checkpointed to parquet
            files in this directory and replaced with ``scan_parquet``
            references.  This breaks both chained-join memory
            accumulation and plan duplication across branches
            (GitHub pola-rs/polars#24206).
        preserve_node_ids: Non-source intermediate outputs that must remain
            available to the caller after their final downstream consumer has
            executed. Optimiser ratebook solves use this for the selected
            banding source side input.
        required_columns_by_node: Optional exact output-column demand for
            caller-consumed nodes.  These seeds supplement concrete
            descendant-derived projection for the named nodes, and replace
            opaque descendant demand so callers that consume a non-OUTPUT
            node directly can avoid terminal "all columns" propagation.
        enforce_contracts: When ``True`` (see ``executor.ENFORCE_CONTRACTS``
            for the default), assert declared column contracts at each
            node boundary via ``.collect_schema()``.  Polars computes
            schemas without executing the query, so this stays cheap.
            Production code paths (batch sink, deploy scoring, training,
            optimiser) run through here — enforcement on the lazy path
            is what makes contract coverage real end-to-end.

    Returns:
        (lazy_outputs, order, parents_of, id_to_name)
    """
    graph = _resolve_graph_paths(graph)
    preserved_outputs = frozenset(preserve_node_ids or ())
    node_map, order, parents_of, id_to_name = _prepare_graph(
        graph,
        target_node_id,
        source=source,
    )
    normalised_required_columns = _normalise_required_columns_by_node(
        required_columns_by_node,
        order,
    )

    # Count downstream consumers per node so we can checkpoint fan-out
    # points (nodes whose output feeds >1 consumer).  Without this,
    # Polars duplicates the entire upstream plan for each branch —
    # e.g. a 38 GB JSONL scan runs twice when two siblings share a parent.
    children_count: dict[str, int] = {nid: 0 for nid in order}
    children_of: dict[str, list[str]] = {nid: [] for nid in order}
    for nid, pids in parents_of.items():
        for pid in pids:
            if pid in children_count:
                children_count[pid] += 1
                children_of[pid].append(nid)

    # Backward column analysis: compute the minimal set of columns
    # needed at each node's output so checkpoints can project away
    # unneeded columns before writing to parquet.  Batch MODEL_SCORE
    # nodes also consume this demand locally so their internal temp
    # parquet write can avoid unused passthrough columns even when the
    # outer checkpoint layer skips model-score nodes.
    needs_projection_analysis = (
        checkpoint_dir is not None or source != "live" or bool(normalised_required_columns)
    )
    projection_plan: _ProjectionPlan | None = (
        _compute_projection_plan(
            order,
            children_of,
            node_map,
            required_columns_by_node=normalised_required_columns,
        )
        if needs_projection_analysis
        else None
    )
    needed_cols: dict[str, set[str] | None] = (
        projection_plan.needed_by_node if projection_plan is not None else {}
    )
    edge_demands: dict[tuple[str, str], set[str] | None] = (
        projection_plan.edge_demands if projection_plan is not None else {}
    )

    # Full parent lookup from ALL edges for instance resolution
    all_parents = graph.parents_of

    # Build executable functions — delegates to _build_funcs with
    # row_limit=None (lazy path never caps source output).
    funcs = _build_funcs(
        order,
        node_map,
        parents_of,
        id_to_name,
        all_parents,
        build_node_fn,
        row_limit=None,
        preamble_ns=preamble_ns,
        source=source,
        required_output_columns_by_node=needed_cols,
    )

    # Execute - all intermediate results stay lazy
    lazy_outputs: dict[str, _Frame] = {}

    # Separate mutable counter for tracking remaining downstream consumers.
    # Decremented at checkpoint time so we know when a parent's LazyFrame
    # can be safely deleted (freeing Polars/Rust Arrow buffers).
    remaining: dict[str, int] = dict(children_count)

    # Batch gc.collect() calls — Polars objects use Rust Arc refcounting
    # and are freed immediately on ``del``.  gc.collect() only helps with
    # cyclic Python garbage (rare here) and adds 50-200 ms per call.
    checkpoints_since_gc = 0

    # Per-node column sets used by the boundary contract checks.  Polars
    # computes schema without executing the query, so collect_schema()
    # is cheap; caching keeps repeated lookups free when the same
    # upstream feeds multiple consumers.
    column_cache: dict[str, frozenset[str]] = {}

    def _schema_names_of(frame: pl.LazyFrame | pl.DataFrame) -> list[str]:
        lazy_frame = frame if isinstance(frame, pl.LazyFrame) else frame.lazy()
        return lazy_frame.collect_schema().names()

    def _columns_of(frame: pl.LazyFrame | pl.DataFrame) -> frozenset[str]:
        return frozenset(_schema_names_of(frame))

    def _apply_edge_projection(
        child_id: str,
        parent_id: str,
        frame: _Frame,
    ) -> tuple[_Frame, frozenset[str] | None]:
        if (parent_id, child_id) not in edge_demands:
            return frame, None
        demand = edge_demands[(parent_id, child_id)]
        if demand is None:
            return frame, None

        lazy_frame = frame if isinstance(frame, pl.LazyFrame) else frame.lazy()
        schema_cols = _schema_names_of(lazy_frame)
        schema_set = set(schema_cols)
        missing = demand - schema_set
        if missing:
            raise ContractMismatchError(
                "Columns required by a fan-in projection contract are "
                "missing from the parent frame.",
                node_id=child_id,
                parent_id=parent_id,
                missing=sorted(missing),
                required_columns=sorted(demand),
                parent_columns=sorted(schema_set),
            )

        ordered = [column for column in schema_cols if column in demand]
        return lazy_frame.select(ordered), frozenset(ordered)

    for nid in order:
        fn, is_source = funcs[nid]
        node = node_map[nid]
        contract = _effective_contract(node) if enforce_contracts else None
        check_here = bool(contract) and _should_check_contract(contract)  # type: ignore[arg-type]
        # Builder-wired ``_passthrough_fn`` means the node is in a stub
        # state (MODEL_SCORE without a model, OPTIMISER_APPLY without
        # an artifact).  Its declared contract describes the configured
        # shape the runtime does not produce yet; skip the output check
        # to preserve the "configure later" UX while still enforcing
        # contracts the moment a real function is wired.
        is_passthrough_runtime = fn is _passthrough_fn

        if is_source:
            lf = fn()
        else:
            input_ids = parents_of.get(nid, [])
            missing = [pid for pid in input_ids if pid not in lazy_outputs]
            if missing:
                raise ValueError(
                    f"Node '{nid}' is missing input(s) from: {missing}. "
                    "Upstream node(s) may have failed or not been registered."
                )
            input_lfs = [lazy_outputs[pid] for pid in input_ids]
            if not input_lfs:
                raise ValueError(f"No input data available for node '{nid}'")

            projected_input_lfs: list[_Frame] = []
            projected_input_columns: list[frozenset[str] | None] = []
            for input_id, input_lf in zip(input_ids, input_lfs, strict=True):
                projected_lf, projected_cols = _apply_edge_projection(nid, input_id, input_lf)
                projected_input_lfs.append(projected_lf)
                projected_input_columns.append(projected_cols)
            input_lfs = projected_input_lfs

            if check_here and contract is not None and contract.inputs is not None:
                upstream_col_sets: list[frozenset[str]] = []
                for upstream_pid, upstream_lf, projected_cols in zip(
                    input_ids,
                    input_lfs,
                    projected_input_columns,
                    strict=True,
                ):
                    if projected_cols is not None:
                        upstream_cols = projected_cols
                    else:
                        upstream_cols = column_cache.get(upstream_pid)
                        if upstream_cols is None:
                            upstream_cols = _columns_of(upstream_lf)
                            column_cache[upstream_pid] = upstream_cols
                    upstream_col_sets.append(upstream_cols)
                upstream_cols = frozenset().union(*upstream_col_sets)
                _assert_inputs_satisfy_contract(node, contract, upstream_cols)

            lf = fn(*input_lfs)

        if isinstance(lf, pl.DataFrame):
            lf = lf.lazy()

        # Apply selected_columns filter first (uses pre-rename names),
        # then column renames on the surviving columns.
        lf = _apply_selected_columns(lf, node_map[nid].data.config)
        lf = _apply_column_renames(lf, node_map[nid].data.config)

        if (
            check_here
            and contract is not None
            and contract.outputs is not None
            and not is_passthrough_runtime
        ):
            out_cols = _columns_of(lf)
            column_cache[nid] = out_cols
            _assert_outputs_satisfy_contract(node, contract, out_cols)

        # Adaptive checkpoint to break Polars plan duplication and
        # chained-join memory accumulation (pola-rs/polars#24206).
        #
        # Three structural triggers (joins, fan-outs, join-feeders) are
        # evaluated by _checkpoint_decision which chooses the cheapest
        # safe strategy:
        #   PARQUET      — disk round-trip, safest, frees RAM
        #   COLLECT_LAZY — in-memory materialization, no I/O, breaks
        #                  plan duplication but holds data in RAM
        #   SKIP         — keep the LazyFrame as-is (source nodes,
        #                  batch MODEL_SCORE which already checkpoints
        #                  internally, or nodes that don't need it)
        n_parents = len(parents_of.get(nid, []))
        n_children = children_count.get(nid, 0)
        feeds_join = any(len(parents_of.get(cid, [])) > 1 for cid in children_of.get(nid, []))

        action = _checkpoint_decision(
            nid,
            is_source,
            n_parents,
            n_children,
            feeds_join,
            node_map,
            source or "live",
        )

        if checkpoint_dir is not None and action == _CheckpointAction.PARQUET:
            tmp = checkpoint_dir / f"{nid}.parquet"

            # Project to only the columns needed downstream before
            # writing the checkpoint.  This avoids writing (and later
            # re-reading) columns that no downstream node will use —
            # e.g. 100 source columns when the model only needs 8.
            sink_lf = lf if isinstance(lf, pl.LazyFrame) else lf.lazy()
            projection = needed_cols.get(nid)
            if projection is not None:
                schema_cols = sink_lf.collect_schema().names()
                schema_set = set(schema_cols)
                missing = projection - schema_set
                if missing:
                    raise ContractMismatchError(
                        "Checkpoint projection references columns missing "
                        "from the node output schema.",
                        node_id=nid,
                        node_type=node.data.nodeType.value,
                        missing=sorted(missing),
                        required_columns=sorted(projection),
                        output_columns=sorted(schema_set),
                    )
                valid = [c for c in schema_cols if c in projection]
                if valid and len(valid) < len(schema_cols):
                    logger.info(
                        "checkpoint_projection",
                        node_id=nid,
                        total_cols=len(schema_cols),
                        projected_cols=len(valid),
                    )
                    sink_lf = sink_lf.select(valid)
                    column_cache[nid] = frozenset(valid)

            safe_sink(sink_lf, tmp, fast_checkpoint=True)

            # Drop the old LazyFrame (and any cached Arrow buffers it
            # holds) before replacing with a fresh scan reference.
            del lf
            # Drop parent LazyFrame refs that have no remaining consumers
            # downstream — lets Polars/Rust release the backing buffers.
            # Source nodes are kept: they hold cheap scan_* references and
            # callers may need them (e.g. optimiser extracting banding factors).
            for pid in parents_of.get(nid, []):
                remaining[pid] -= 1
                _, pid_is_source = funcs.get(pid, (None, False))
                if (
                    remaining[pid] <= 0
                    and pid in lazy_outputs
                    and not pid_is_source
                    and pid not in preserved_outputs
                ):
                    del lazy_outputs[pid]

            checkpoints_since_gc += 1
            if checkpoints_since_gc >= _GC_BATCH_INTERVAL:
                gc.collect()
                _malloc_trim()
                checkpoints_since_gc = 0

            lf = pl.scan_parquet(tmp)
            logger.info("checkpoint_parquet", node_id=nid, path=str(tmp))

        lazy_outputs[nid] = lf

    return lazy_outputs, order, parents_of, id_to_name


# ---------------------------------------------------------------------------
# Eager execution core — shared by executor (preview) and trace
# ---------------------------------------------------------------------------


def _build_funcs(
    order: list[str],
    node_map: dict[str, GraphNode],
    parents_of: dict[str, list[str]],
    id_to_name: dict[str, str],
    all_parents: dict[str, list[str]],
    build_node_fn: Callable,
    *,
    row_limit: int | None = None,
    preamble_ns: dict | None = None,
    source: str = "live",
    required_output_columns_by_node: Mapping[str, set[str] | None] | None = None,
    reuse_loaded_model_by_node: Mapping[str, bool] | None = None,
) -> dict[str, tuple[Callable, bool]]:
    """Build per-node executable functions from the graph.

    Shared between eager and lazy paths.  ``row_limit`` is forwarded to
    ``build_node_fn`` so Databricks sources can push LIMIT into SQL.
    ``preamble_ns`` is a compiled namespace of user-defined helpers from
    the pipeline file's preamble section.
    ``source`` is the active execution source forwarded to build_node_fn.
    ``reuse_loaded_model_by_node`` opts selected modelScore nodes into
    scorer-instance model reuse for chunked callers.
    """
    funcs: dict[str, tuple[Callable, bool]] = {}
    for nid in order:
        src_ids = [pid for pid in parents_of.get(nid, []) if pid in id_to_name]
        src_names = [id_to_name[pid] for pid in src_ids]
        orig_src_names = resolve_orig_source_names(
            node_map[nid],
            node_map,
            all_parents,
            id_to_name,
        )
        _, fn, is_source = build_node_fn(
            node_map[nid],
            source_names=src_names,
            source_ids=src_ids,
            row_limit=row_limit,
            node_map=node_map,
            orig_source_names=orig_src_names,
            preamble_ns=preamble_ns,
            source=source,
            required_output_columns=(
                required_output_columns_by_node.get(nid)
                if required_output_columns_by_node is not None
                else None
            ),
            reuse_loaded_model=(
                bool(reuse_loaded_model_by_node.get(nid))
                if reuse_loaded_model_by_node is not None
                else False
            ),
        )
        funcs[nid] = (fn, is_source)
    return funcs


def _extract_error_line(exc: Exception) -> int | None:
    """Extract user-code line number from an exception, if available.

    - SyntaxError: use .lineno (already adjusted by _exec_user_code).
    - _user_code_line attr: set by _exec_user_code from the traceback
      for runtime errors like NameError that don't embed line info
      in their message string.
    - Fallback: parse 'line N' from the error message
      (already adjusted by _exec_user_code's regex substitution).
    - Returns None when no line info is available.
    """
    if isinstance(exc, SyntaxError) and exc.lineno is not None:
        return exc.lineno
    user_line: int | None = getattr(exc, "_user_code_line", None)
    if user_line is not None:
        return int(user_line)
    match = re.search(r"\bline (\d+)\b", str(exc))
    if match:
        return int(match.group(1))
    return None


class EagerResult(NamedTuple):
    """Result of eager graph execution."""

    outputs: dict[str, pl.DataFrame | None]
    order: list[str]
    parents_of: dict[str, list[str]]
    node_map: dict[str, GraphNode]
    id_to_name: dict[str, str]
    errors: dict[str, str]
    timings: dict[str, float]
    memory_bytes: dict[str, int]
    error_lines: dict[str, int]
    available_columns: dict[str, list[tuple[str, str]]]
    output_columns: dict[str, list[tuple[str, str]]]


def _execute_eager_core(
    graph: PipelineGraph,
    build_node_fn: Callable,
    target_node_id: str | None = None,
    row_limit: int | None = None,
    swallow_errors: bool = False,
    preamble_ns: dict | None = None,
    source: str = "live",
    enforce_contracts: bool = True,
    required_columns_by_node: Mapping[str, Iterable[str]] | None = None,
) -> EagerResult:
    """Execute the graph eagerly in topo order and collect DataFrames.

    Shared core for the preview executor and the trace engine.

    Args:
        graph: React Flow graph.
        build_node_fn: ``(node, source_names=..., ...) -> (name, fn, is_source)``.
        target_node_id: If set, only execute ancestors of this node.
        row_limit: Cap source-node output to this many rows.
        swallow_errors: If ``True``, record per-node errors and continue
            (preview behaviour).  If ``False``, raise immediately (trace).
        source: Active execution source (``"live"`` = eager scoring).
        enforce_contracts: If ``True`` (default), assert each node's
            column contract at its input and output boundaries.  A
            mismatch always raises ``ContractMismatchError`` regardless
            of *swallow_errors* — the contract is an API-level claim
            and a silent error would defeat the adoption effort.
        required_columns_by_node: Optional exact output-column demand for
            caller-consumed nodes.  Eager preview uses this to collect only
            the visible target columns while still reporting the full schema.

    Returns:
        An ``EagerResult`` with named fields for outputs, order,
        parents_of, node_map, id_to_name, errors, timings, and
        memory_bytes.
    """
    graph = _resolve_graph_paths(graph)
    node_map, order, parents_of, id_to_name = _prepare_graph(
        graph,
        target_node_id,
        source=source,
    )
    normalised_required_columns = _normalise_required_columns_by_node(
        required_columns_by_node,
        order,
    )

    # Full parent lookup from ALL edges for instance resolution
    all_parents = graph.parents_of

    # Fan-out count per node — how many direct children consume this
    # node's output.  Used to add a Polars ``.cache()`` hint when the
    # parent feeds >1 consumer so the optimiser reuses one materialized
    # plan across branches (diamond graphs) instead of duplicating the
    # upstream work.  Each eager_outputs entry is already a concrete
    # DataFrame, so the cache hint only matters once we re-enter a
    # LazyFrame via ``.lazy()`` for the next node's inputs.
    children_count: dict[str, int] = dict.fromkeys(order, 0)
    children_of: dict[str, list[str]] = {nid: [] for nid in order}
    for _nid, _pids in parents_of.items():
        for _pid in _pids:
            if _pid in children_count:
                children_count[_pid] += 1
                children_of[_pid].append(_nid)

    projection_plan: _ProjectionPlan | None = (
        _compute_projection_plan(
            order,
            children_of,
            node_map,
            required_columns_by_node=normalised_required_columns,
        )
        if normalised_required_columns
        else None
    )
    needed_cols: dict[str, set[str] | None] = (
        projection_plan.needed_by_node if projection_plan is not None else {}
    )
    builder_needed_cols: dict[str, set[str] | None] = {}
    if needed_cols:
        builder_needed_cols = {
            nid: (
                None
                if node_map[nid].data.nodeType == NodeType.MODEL_SCORE
                else required_columns
            )
            for nid, required_columns in needed_cols.items()
        }

    funcs = _build_funcs(
        order,
        node_map,
        parents_of,
        id_to_name,
        all_parents,
        build_node_fn,
        row_limit=row_limit,
        preamble_ns=preamble_ns,
        source=source,
        required_output_columns_by_node=builder_needed_cols,
    )

    eager_outputs: dict[str, pl.DataFrame | None] = {}
    errors: dict[str, str] = {}
    error_lines: dict[str, int] = {}
    timings: dict[str, float] = {}
    memory_bytes: dict[str, int] = {}
    available_columns: dict[str, list[tuple[str, str]]] = {}
    output_columns: dict[str, list[tuple[str, str]]] = {}

    # Per-node column sets used by the boundary contract checks.  We
    # compute each frame's column set exactly once and reuse it — both
    # as an output check for the producing node and as an input check
    # for its consumer(s).  Polars' ``.columns`` is O(n) in the number
    # of columns, but frozenset construction dominates anyway; caching
    # keeps the contract-enforced path within the <5% budget.
    column_cache: dict[str, frozenset[str]] = {}

    def _schema_items_of(frame: pl.LazyFrame | pl.DataFrame) -> list[tuple[str, str]]:
        lazy_frame = frame if isinstance(frame, pl.LazyFrame) else frame.lazy()
        schema = lazy_frame.collect_schema()
        return [(name, str(schema[name])) for name in schema.names()]

    def _full_model_score_schema(
        node_id: str,
        node: GraphNode,
        actual_columns: list[tuple[str, str]],
    ) -> list[tuple[str, str]]:
        config = node.data.config
        if (
            node.data.nodeType != NodeType.MODEL_SCORE
            or config.get("code")
            or config.get("column_renames")
            or config.get("selected_columns")
        ):
            return actual_columns

        parent_ids = parents_of.get(node_id, [])
        if not parent_ids:
            return actual_columns
        parent_columns = output_columns.get(parent_ids[0])
        if parent_columns is None:
            return actual_columns

        actual_by_name = dict(actual_columns)
        generated_names = [str(config.get("output_column") or "prediction")]
        proba_col = f"{generated_names[0]}_proba"
        if proba_col in actual_by_name:
            generated_names.append(proba_col)

        seen: set[str] = set()
        full_columns: list[tuple[str, str]] = []
        for name, dtype in parent_columns:
            full_columns.append((name, actual_by_name.get(name, dtype)))
            seen.add(name)
        for name in generated_names:
            if name in seen or name not in actual_by_name:
                continue
            full_columns.append((name, actual_by_name[name]))
            seen.add(name)
        return full_columns

    for nid in order:
        fn, is_source = funcs[nid]
        node = node_map[nid]
        contract = _effective_contract(node) if enforce_contracts else None
        check_here = bool(contract) and _should_check_contract(contract)  # type: ignore[arg-type]
        # A node that the builder chose to wire to ``_passthrough_fn`` is
        # running in a stub/unconfigured state (MODEL_SCORE without a
        # loaded model, OPTIMISER_APPLY without an artifact, etc.).  Its
        # contract describes the *configured* shape, which the runtime
        # intentionally does not produce yet.  Skip the output-side
        # check to preserve the "drag node onto canvas, configure later"
        # UX while still enforcing contracts the moment a real function
        # is wired in.
        is_passthrough_runtime = fn is _passthrough_fn
        t0 = time.perf_counter()
        try:
            if is_source:
                result = fn()
                if row_limit and isinstance(result, (pl.LazyFrame, pl.DataFrame)):
                    result = result.head(row_limit)
            else:
                input_ids = parents_of.get(nid, [])
                missing_parents = [pid for pid in input_ids if pid not in eager_outputs]
                if missing_parents:
                    raise ValueError(
                        f"Node '{nid}' is missing input(s) from: {missing_parents}. "
                        "Upstream node(s) may not have been registered."
                    )
                failed_parents = [pid for pid in input_ids if eager_outputs[pid] is None]
                if failed_parents:
                    eager_outputs[nid] = None
                    continue
                # Add ``.cache()`` on parents that feed >1 consumer so a
                # downstream ``.collect()`` re-uses the materialised plan
                # across branches instead of duplicating upstream work.
                # This is the diamond optimisation: src -> (left, right)
                # -> sink should compute src's plan once, not twice.
                # Parents with exactly one consumer skip the hint — it's
                # cheap but non-zero overhead and adds no value there.
                input_lfs = []
                for pid in input_ids:
                    if pid not in eager_outputs:
                        continue
                    parent_df = eager_outputs[pid]
                    if parent_df is None:
                        continue
                    parent_lf = parent_df.lazy()
                    if children_count.get(pid, 0) > 1:
                        parent_lf = parent_lf.cache()
                    input_lfs.append(parent_lf)
                if not input_lfs:
                    raise ValueError(
                        f"No input data available for node '{nid}'",
                    )

                # Input-side contract check: every column the node's
                # contract says it reads must be present upstream.
                # Using the union across all parents matches how the
                # node's function receives inputs — multi-input joins
                # combine them before the contract columns are read.
                if check_here and contract.inputs is not None:  # type: ignore[union-attr]
                    upstream_cols: frozenset[str] = frozenset().union(
                        *(column_cache[pid] for pid in input_ids if pid in column_cache)
                    )
                    _assert_inputs_satisfy_contract(node, contract, upstream_cols)  # type: ignore[arg-type]

                result = fn(*input_lfs)

            if not isinstance(result, (pl.LazyFrame, pl.DataFrame)):
                raise TypeError(
                    f"Node '{nid}' returned {type(result).__name__}; expected a Polars frame."
                )

            result_lf = result if isinstance(result, pl.LazyFrame) else result.lazy()

            # Capture full column set before selected_columns filtering
            available_columns[nid] = _schema_items_of(result_lf)

            # Apply selected_columns filter first (uses pre-rename names),
            # then column renames on the surviving columns.
            filtered = _apply_selected_columns(result_lf, node_map[nid].data.config)
            renamed = _apply_column_renames(filtered, node_map[nid].data.config)
            output_lf = renamed if isinstance(renamed, pl.LazyFrame) else renamed.lazy()
            full_output_columns = _schema_items_of(output_lf)
            full_output_columns = _full_model_score_schema(nid, node, full_output_columns)
            if (
                node.data.nodeType == NodeType.MODEL_SCORE
                and not node.data.config.get("code")
                and not node.data.config.get("column_renames")
                and not node.data.config.get("selected_columns")
            ):
                available_columns[nid] = full_output_columns
            output_columns[nid] = full_output_columns
            output_column_names = [name for name, _dtype in full_output_columns]
            output_column_set = set(output_column_names)

            # Output-side contract check: every column the node promises
            # to produce must be present on the result.  We check the
            # post-rename/post-select frame because that's what
            # downstream consumers actually see.  Passthrough-runtime
            # nodes are exempt — see the ``is_passthrough_runtime``
            # note above.
            final_cols = frozenset(output_column_names)
            if (
                check_here
                and contract.outputs is not None  # type: ignore[union-attr]
                and not is_passthrough_runtime
            ):
                _assert_outputs_satisfy_contract(node, contract, final_cols)  # type: ignore[arg-type]

            projection = needed_cols.get(nid)
            if projection is not None:
                missing = projection - output_column_set
                if missing and nid not in normalised_required_columns:
                    raise ContractMismatchError(
                        "Eager projection references columns missing "
                        "from the node output schema.",
                        node_id=nid,
                        node_type=node.data.nodeType.value,
                        missing=sorted(missing),
                        required_columns=sorted(projection),
                        output_columns=sorted(output_column_set),
                    )
                projected_columns = [c for c in output_column_names if c in projection]
                if len(projected_columns) < len(output_column_names):
                    logger.info(
                        "eager_projection",
                        node_id=nid,
                        total_cols=len(output_column_names),
                        projected_cols=len(projected_columns),
                    )
                    output_lf = output_lf.select(projected_columns)
                    final_cols = frozenset(projected_columns)
            column_cache[nid] = final_cols

            df = output_lf.collect(engine="streaming")
            eager_outputs[nid] = df
            memory_bytes[nid] = int(df.estimated_size("b"))
        except ContractMismatchError:
            # Contract errors are API-level — raise even in swallow mode
            # so GUI users see the crisp error instead of a silent
            # per-node "failed" status card.
            raise
        except Exception as exc:
            if not swallow_errors:
                raise
            logger.error("node_failed", node_id=nid, error=str(exc))
            eager_outputs[nid] = None
            errors[nid] = str(exc)
            error_line = _extract_error_line(exc)
            if error_line is not None:
                error_lines[nid] = error_line
        timings[nid] = round((time.perf_counter() - t0) * 1000, 1)

    return EagerResult(
        eager_outputs,
        order,
        parents_of,
        node_map,
        id_to_name,
        errors,
        timings,
        memory_bytes,
        error_lines,
        available_columns,
        output_columns,
    )
