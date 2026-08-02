"""Code generator orchestration: graph JSON -> valid pipeline .py file.

The per-type ``_gen_*`` builders and the codegen-side registry live in
:mod:`haute._codegen_builders`.  This module keeps the graph-level
assembly logic (``graph_to_code`` / ``graph_to_code_multi``) and the
single-node dispatcher that drives the unified
:data:`haute._registry.NODE_REGISTRY`.
"""

from __future__ import annotations

import ast
import io
import tokenize
from collections.abc import Callable

from haute._codegen_builders import (
    _build_params,
    _safe_path,
    _safe_str,
    _sanitize_description,
)
from haute._config_io import config_path_for_node, has_config_folder
from haute._contracts import (
    OPAQUE_CONTRACT_SENTINEL,
    Contract,
    get_column_contract,
)
from haute._edge_join import (
    build_edge_join_boundary_target_roles,
    resolve_edge_join_role_indices,
)
from haute._graph_shape import validate_pipeline_graph_shape_contracts
from haute._graph_utils import (
    _sanitize_func_name,
    build_instance_mapping,
    duplicate_input_names,
    edge_input_name,
)
from haute._logging import get_logger
from haute._registry import NODE_REGISTRY
from haute._topo import topo_sort_ids
from haute._types import (
    NODE_TYPE_TO_DECORATOR,
    GraphEdge,
    GraphNode,
    NodeType,
    PipelineGraph,
)
from haute.errors import ConfigError, HauteError, ParseError

logger = get_logger(component="codegen")

__all__ = [
    "graph_to_code",
    "graph_to_code_multi",
]


# ---------------------------------------------------------------------------
# Single-node dispatch
# ---------------------------------------------------------------------------


def _is_codegen_infra_error(exc: BaseException) -> bool:
    """Whether *exc* is an environmental/infra failure safe to treat as opaque.

    Only an ``OSError`` (missing artifact file, refused connection) or an
    exception raised from the ``mlflow`` package (server unreachable) counts.
    Everything else — ``TypeError``/``KeyError``/``ValueError`` bugs, any
    ``HauteError`` — is a real defect that must fail the save loudly.
    """
    if isinstance(exc, OSError):
        return True
    return type(exc).__module__.split(".", 1)[0] == "mlflow"


def _format_contract_kwarg(
    node: GraphNode,
    parent_name_by_id: dict[str, str] | None = None,
) -> str | None:
    """Return the ``contract=...`` decorator kwarg source, or ``None``.

    For concrete contracts, emits
    ``contract={"inputs": [...], "outputs": [...]}`` with columns sorted
    for deterministic round-tripping.  For opaque contracts, emits
    ``contract="opaque"`` — a short string sentinel that both survives
    JSON config round-tripping and is trivially human-readable.

    Returns ``None`` for instance nodes (their contract comes from the
    original node they reference, not from their own usually-empty
    config).

    Contract computation for some nodes (notably ``MODEL_SCORE``)
    loads an MLflow artifact to discover feature names — that load can
    fail at codegen time in disconnected environments or CI runs.  We
    treat *infrastructure* failures ONLY — an ``OSError`` (artifact file
    missing, connection refused) or any ``mlflow.*`` exception (MLflow
    unreachable) — as "opaque at codegen time" rather than propagating:
    the purpose of the kwarg is documentation at the source-file level,
    and the executor still re-computes + enforces the contract at runtime
    from the actual model.  Forcing a running MLflow server just to save a
    pipeline would be a regression.

    Every OTHER exception fails loud at save time.  ``ConfigError``
    (misconfiguration — e.g. ``sourceType="run"`` with no ``run_id``) and
    any other ``HauteError`` (including ``ContractMismatchError``) propagate,
    as do plain ``TypeError`` / ``KeyError`` / ``ValueError`` bugs in the
    contract computation itself — emitting ``contract="opaque"`` for those
    would hide a real bug inside a file that silently runs, then blows up at
    execution far from the cause.
    """
    config = node.data.config
    declared_raw = config.get("contract")
    if config.get("instanceOf") and declared_raw is None:
        return None
    if declared_raw is not None:
        # ``from_user_declared`` only returns None for a None input, and
        # declared_raw is non-None here — so declared is always a Contract.
        declared = Contract.from_user_declared(declared_raw)
        assert declared is not None
        return _format_contract_source(declared, parent_name_by_id=parent_name_by_id)
    try:
        tup = get_column_contract(node.data.nodeType, config)
    except ConfigError:
        # Misconfiguration is a user bug, not an environmental one — let
        # it propagate so save fails at the source of the mistake.
        raise
    except Exception as exc:
        if not _is_codegen_infra_error(exc):
            # A genuine contract-computation bug (TypeError, KeyError, a
            # HauteError such as ContractMismatchError, …) — fail loud
            # rather than masking it behind an opaque contract.
            raise
        logger.warning(
            "contract_emit_opaque_on_error",
            node=node.data.label,
            node_type=str(node.data.nodeType),
            error=str(exc),
        )
        return f'contract="{OPAQUE_CONTRACT_SENTINEL}"'
    contract = Contract.from_tuple(tup)
    if contract.inputs is None or contract.outputs is None:
        return f'contract="{OPAQUE_CONTRACT_SENTINEL}"'
    inputs_repr = repr(sorted(contract.inputs))
    outputs_repr = repr(sorted(contract.outputs))
    return f'contract={{"inputs": {inputs_repr}, "outputs": {outputs_repr}}}'


def _format_contract_source(
    contract: Contract,
    *,
    parent_name_by_id: dict[str, str] | None = None,
) -> str:
    """Format a declared contract while preserving fan-in ownership metadata."""
    if contract.inputs is None and contract.outputs is None and contract.inputs_by_parent is None:
        return f'contract="{OPAQUE_CONTRACT_SENTINEL}"'

    contract_dict: dict[str, object] = {
        "inputs": None if contract.inputs is None else sorted(contract.inputs),
        "outputs": None if contract.outputs is None else sorted(contract.outputs),
    }
    if contract.inputs_by_parent is not None:
        parent_names = set(parent_name_by_id.values()) if parent_name_by_id is not None else set()
        inputs_by_parent: dict[str, list[str] | None] = {}
        stale_inputs: list[tuple[str, frozenset[str] | None]] = []
        for parent_id, columns in sorted(contract.inputs_by_parent.items()):
            emitted_parent = parent_id
            if parent_name_by_id is not None:
                if parent_id in parent_name_by_id:
                    emitted_parent = parent_name_by_id[parent_id]
                elif parent_id in parent_names:
                    emitted_parent = parent_id
                else:
                    stale_inputs.append((parent_id, columns))
                    continue
            emitted_columns = None if columns is None else sorted(columns)
            if emitted_parent in inputs_by_parent and inputs_by_parent[emitted_parent] != (
                emitted_columns
            ):
                # Two distinct declared keys (a parent id and that parent's
                # emitted func-name) collapsing to the same emitted parent with
                # conflicting columns is a genuine ambiguity — fail loud rather
                # than silently keeping the last writer.
                raise ParseError(
                    "contract inputs_by_parent has two source keys that map to the "
                    "same emitted parent with conflicting columns.",
                    emitted_parent=emitted_parent,
                    existing=inputs_by_parent[emitted_parent],
                    conflicting=emitted_columns,
                )
            inputs_by_parent[emitted_parent] = emitted_columns
        if stale_inputs:
            # Edges and node bodies remain the source of truth for saving.
            # Stale ownership metadata (parent ids no longer connected after a
            # UI rewire) is optimization-only; omit it rather than guessing a
            # possibly-wrong parent — reassigning across a rewire re-attributes
            # columns to a parent we have no evidence owns them.
            logger.warning(
                "contract_inputs_by_parent_omitted_stale",
                stale_parent_ids=[parent_id for parent_id, _ in stale_inputs],
                connected_parent_ids=sorted(parent_name_by_id or {}),
                connected_parent_names=sorted(parent_names),
            )
            inputs_by_parent = {}
        if inputs_by_parent:
            contract_dict["inputs_by_parent"] = {
                parent_id: columns for parent_id, columns in sorted(inputs_by_parent.items())
            }
    return f"contract={contract_dict!r}"


def _parent_name_by_id(
    source_ids: list[str],
    source_names: list[str],
) -> dict[str, str]:
    """Map incoming parent node ids to emitted Python function names."""
    return {
        source_id: source_names[index]
        for index, source_id in enumerate(source_ids)
        if index < len(source_names)
    }


def _matching_close_paren(code: str, open_line: int, open_col: int) -> tuple[int, int]:
    """Locate the ``)`` matching the ``(`` at (*open_line*, *open_col*).

    Uses :mod:`tokenize` rather than a character scan so parentheses
    inside string literals and comments are invisible: user-controlled
    decorator kwargs (a column named ``"price (gbp)"`` or ``":)"``)
    must not shift the injection point into the middle of a string —
    that mis-positioning silently corrupted the emitted file.

    Token consumption is lazy: iteration stops at the matching paren,
    so malformed *body* code after the decorator (rejected later by
    the final-emission parse gate) cannot make this helper fail.

    *open_line* / the returned line are 0-based indices into
    ``code.split("\\n")``; columns are 0-based.  Raises
    :class:`HauteError` when the decorator argument list never closes
    or its region cannot be tokenized — both mean codegen emitted a
    malformed decorator.
    """
    depth = 0
    seen_open = False
    try:
        for tok in tokenize.generate_tokens(io.StringIO(code).readline):
            if tok.type != tokenize.OP or tok.string not in {"(", ")"}:
                continue
            row = tok.start[0] - 1  # tokenize rows are 1-based
            if not seen_open:
                if row == open_line and tok.start[1] == open_col and tok.string == "(":
                    seen_open = True
                    depth = 1
                continue
            if tok.string == "(":
                depth += 1
            else:
                depth -= 1
                if depth == 0:
                    return row, tok.start[1]
    except (tokenize.TokenError, IndentationError, SyntaxError) as exc:
        raise HauteError(
            "contract injection failed: decorator argument list does not "
            "tokenize — this is a codegen bug",
            reason="decorator_untokenizable",
            error=str(exc),
        ) from exc
    raise HauteError(
        "contract injection failed: decorator argument list has no "
        "matching closing paren — this is a codegen bug",
        reason="no_closing_paren",
    )


def _inject_contract_kwarg(code: str, contract_kwarg: str) -> str:
    """Insert *contract_kwarg* into the first ``@pipeline.<type>(...)`` call.

    Finds the opening ``(`` of the decorator argument list on the first
    decorator line and inserts the kwarg just before the matching closing
    ``)`` (located with a string- and comment-aware token scan — see
    :func:`_matching_close_paren`).  Preserves any existing kwargs.

    Decorators without parentheses (``@pipeline.polars``) are rewritten
    to ``@pipeline.polars(contract=...)`` so the kwarg survives.
    """
    # split("\n") (not splitlines) so indices line up with tokenize's row
    # numbering: Python source only breaks lines at newlines, while
    # str.splitlines also splits at form-feed / NEL / LINE SEPARATOR --
    # characters that can legally appear inside emitted string literals.
    lines = code.split("\n")
    for i, line in enumerate(lines):
        stripped = line.lstrip()
        if not stripped.startswith("@pipeline.") and not stripped.startswith("@submodel."):
            continue

        if "(" not in stripped:
            # Bare decorator like "@pipeline.polars" — add parens + kwarg.
            lines[i] = line.rstrip() + f"({contract_kwarg})"
            return "\n".join(lines)

        # Decorator with args — find the matching closing paren.  This
        # might span multiple lines (e.g. factors=[{...}]).
        open_idx = stripped.index("(")
        leading = len(line) - len(stripped)
        end_line, end_col = _matching_close_paren(code, i, leading + open_idx)

        target = lines[end_line]
        # Determine whether the decorator currently has any arguments so
        # we know if we need a leading comma.  If the range between the
        # opening ``(`` and closing ``)`` is empty (only whitespace or
        # newlines), there are no existing kwargs.
        if end_line == i:
            between = target[leading + open_idx + 1 : end_col]
        else:
            parts = [lines[i][leading + open_idx + 1 :]]
            parts.extend(lines[i + 1 : end_line])
            parts.append(lines[end_line][:end_col])
            between = "\n".join(parts)
        has_args = bool(between.strip())

        prefix = ", " if has_args else ""
        lines[end_line] = target[:end_col] + prefix + contract_kwarg + target[end_col:]
        return "\n".join(lines)

    # Same reasoning as the no-closing-paren branch above: reaching
    # this point means the generated code had no ``@pipeline.*`` or
    # ``@submodel.*`` decorator at all.  Silently returning the code
    # unchanged would emit a file with no contract kwarg and surface
    # as an opaque downstream failure — raise instead.
    raise HauteError(
        "contract injection failed: no @pipeline.* or @submodel.* "
        "decorator found in generated code — this is a codegen bug",
        reason="no_decorator",
    )


def _node_to_code(
    node: GraphNode,
    source_names: list[str] | None = None,
    source_ids: list[str] | None = None,
    source_func_names: list[str] | None = None,
) -> str:
    """Generate code for a single node.

    Delegates to :func:`_generate_node_code` for the type-specific body,
    then replaces the decorator line with a ``config=`` file reference for
    node types that use external JSON config files, and finally injects
    the column contract as an additional decorator kwarg so reviewers
    and the parser can cross-check it without running the pipeline.
    """
    if source_names is None:
        source_names = []
    if source_ids is None:
        source_ids = []

    original_source_ids = source_ids
    source_names, source_ids = _role_order_node_sources(node, source_names, source_ids)
    if source_func_names is not None:
        source_func_names, _ = _role_order_node_sources(
            node,
            source_func_names,
            original_source_ids,
        )

    code = _generate_node_code(node, source_names)

    # Edge-join role metadata identifies the connected source functions so
    # the parser can reconstruct the graph.  The function parameters above
    # are intentionally the edge-derived input names (an apiInput frame is a
    # frame label), so keep those names in the signature/body while emitting
    # source-function names in the role kwargs.
    if node.data.nodeType == NodeType.EDGE_JOIN and source_func_names is not None:
        if len(source_func_names) != 2 or len(source_names) != 2:
            raise ParseError(
                "edgeJoin codegen source function names and input names are out of sync.",
                node_id=node.id,
                node_label=node.data.label,
                source_names=source_names,
                source_func_names=source_func_names,
            )
        code = code.replace(
            f"base_input={_safe_str(source_names[0])}",
            f"base_input={_safe_str(source_func_names[0])}",
            1,
        ).replace(
            f"join_input={_safe_str(source_names[1])}",
            f"join_input={_safe_str(source_func_names[1])}",
            1,
        )

    node_type = node.data.nodeType
    if has_config_folder(node_type):
        func_name = _sanitize_func_name(node.data.label)
        cfg_path = config_path_for_node(node_type, func_name).as_posix()
        try:
            dec_name = NODE_TYPE_TO_DECORATOR[node_type]
        except KeyError as exc:
            raise HauteError(
                "config-backed node has no registered decorator; this is a codegen bug",
                node_id=node.id,
                node_label=node.data.label,
                node_type=str(node_type),
            ) from exc
        try:
            def_idx = code.index("\ndef ")
        except ValueError as exc:
            raise HauteError(
                "config-backed node code has no function definition; this is a codegen bug",
                node_id=node.id,
                node_label=node.data.label,
                node_type=str(node_type),
            ) from exc
        code = f"@pipeline.{dec_name}(config={_safe_path(cfg_path)})" + code[def_idx:]

    contract_kwarg = _format_contract_kwarg(
        node,
        parent_name_by_id=_parent_name_by_id(source_ids, source_names),
    )
    if contract_kwarg is not None:
        try:
            code = _inject_contract_kwarg(code, contract_kwarg)
        except HauteError as exc:
            # Enrich the error with the offending node's identity so
            # the saved-pipeline error message names exactly which node
            # triggered the codegen bug, not just the shape of the bug.
            exc.context.setdefault("node_id", node.id)
            exc.context.setdefault("node_label", node.data.label)
            exc.context.setdefault("node_type", str(node.data.nodeType))
            raise

    return code


def _role_order_node_sources(
    node: GraphNode,
    source_names: list[str],
    source_ids: list[str],
) -> tuple[list[str], list[str]]:
    """Return source names/ids in role order for role-sensitive node types."""
    if node.data.nodeType != NodeType.EDGE_JOIN:
        return source_names, source_ids
    if len(source_names) != len(source_ids):
        raise ParseError(
            "edgeJoin codegen source names and ids are out of sync.",
            node_id=node.id,
            node_label=node.data.label,
            source_names=source_names,
            source_ids=source_ids,
        )
    base_index, join_index = resolve_edge_join_role_indices(node.data.config, source_ids)
    order = [base_index, join_index]
    return [source_names[index] for index in order], [source_ids[index] for index in order]


def _generate_node_code(node: GraphNode, source_names: list[str] | None = None) -> str:
    """Dispatch to the type-specific codegen builder via the unified registry.

    Fails loudly if no codegen builder is registered for the node's
    ``NodeType`` — per :data:`haute._registry.NODE_REGISTRY` contract, every
    ``NodeType`` must have a codegen entry; an absent one is a wiring bug
    worth crashing over, not a condition to silently paper over with a
    fallback to ``_gen_transform`` that hid misregistered types historically.
    """
    if source_names is None:
        source_names = []

    entry = NODE_REGISTRY.get(node.data.nodeType)
    if entry is None or entry.codegen is None:
        raise KeyError(
            f"no codegen builder registered for {node.data.nodeType!r} "
            f"(node id={node.id!r} label={node.data.label!r}). "
            "Every NodeType must register an exec builder AND a codegen "
            "builder — see haute._registry.validate_registry_complete.",
        )
    return entry.codegen(node, source_names)


def _instance_to_code(
    node: GraphNode,
    original_func_name: str,
    source_names: list[str] | None = None,
    source_ids: list[str] | None = None,
    orig_source_names: list[str] | None = None,
) -> str:
    """Generate code for an instance node that delegates to the original function.

    When *orig_source_names* is provided the wrapper emits keyword arguments so
    that each original parameter receives the correct instance input regardless
    of edge ordering.
    """
    data = node.data
    label = data.label
    description = _sanitize_description(data.description or f"Instance of {original_func_name}")
    func_name = _sanitize_func_name(label)

    if source_names is None:
        source_names = []
    if source_ids is None:
        source_ids = []

    params = _build_params(source_names)

    # Prefer explicit inputMapping from config (set via the UI)
    explicit_map = data.config.get("inputMapping")

    if orig_source_names and source_names:
        explicit = dict(explicit_map) if explicit_map and isinstance(explicit_map, dict) else None
        mapping = build_instance_mapping(orig_source_names, source_names, explicit)
        args = ", ".join(f"{orig}={mapping[orig]}" for orig in orig_source_names if orig in mapping)
    else:
        args = ", ".join(source_names) if source_names else "df"

    code = (
        f'@pipeline.instance(of="{original_func_name}")\n'
        f"def {func_name}({params}) -> pl.LazyFrame:\n"
        f'    """{description}"""\n'
        f"    return {original_func_name}({args})\n"
    )
    contract_kwarg = _format_contract_kwarg(
        node,
        parent_name_by_id=_parent_name_by_id(source_ids, source_names),
    )
    if contract_kwarg is not None:
        code = _inject_contract_kwarg(code, contract_kwarg)
    return code


# ---------------------------------------------------------------------------
# Pipeline assembly helpers
# ---------------------------------------------------------------------------


def _topo_sort(nodes: list[GraphNode], edges: list[GraphEdge]) -> list[GraphNode]:
    """Sort nodes in topological order based on edges."""
    node_map = {n.id: n for n in nodes}
    order = topo_sort_ids(list(node_map.keys()), edges)
    return [node_map[nid] for nid in order if nid in node_map]


def _emit_preserved_blocks(preserved_blocks: list[str]) -> list[str]:
    """Wrap each preserved block in start/end markers and return as lines."""
    lines: list[str] = []
    for block in preserved_blocks:
        lines.append("# haute:preserve-start")
        lines.append(block)
        lines.append("# haute:preserve-end")
        lines.append("")
    return lines


def _build_id_to_func(sorted_nodes: list[GraphNode]) -> dict[str, str]:
    """Map node.id -> sanitized function name for sorted nodes."""
    return {node.id: _sanitize_func_name(node.data.label) for node in sorted_nodes}


def _error_on_name_collisions(labels: list[str]) -> None:
    """Raise :class:`ParseError` on any pair of labels that sanitize to the
    same identifier.

    A collision is a silent user-data-loss bug: codegen emits two
    ``def <name>(...)`` blocks and the second shadows the first at import
    time. Failing at codegen time prevents corrupting the pipeline on disk.

    Pass a flat list of every label that will ultimately become a
    function name in any emitted file (root graph + every submodel).
    The scope is deliberately GLOBAL, not per-file: a root node and a
    submodel node emit ``def``s into different Python modules (legal as
    files), but at run/preview/trace time ``flatten_graph`` dissolves
    every submodel into ONE graph keyed by ``node.id`` — which
    round-trips to the sanitised function name — so a cross-module
    duplicate silently shadows its twin in ``PipelineGraph.node_map``.
    Do NOT relax this to per-file bucketing without changing how the
    flattened execution graph is keyed.

    The raised :class:`ParseError` enumerates every colliding bucket
    so the user can fix them all in one editing pass.
    """
    buckets: dict[str, list[str]] = {}
    for label in labels:
        sanitized = _sanitize_func_name(label)
        buckets.setdefault(sanitized, []).append(label)

    # Every emitted function name must be unique, including exact label
    # duplicates: either form would shadow one node in generated Python.
    collisions = {
        sanitized: sorted(originals)
        for sanitized, originals in buckets.items()
        if len(originals) > 1
    }
    if not collisions:
        return

    bullets = "\n".join(
        f"  - `{sanitized}` is produced by: {', '.join(repr(o) for o in originals)}"
        for sanitized, originals in sorted(collisions.items())
    )
    logger.error(
        "sanitize_name_collision",
        collisions={k: list(v) for k, v in collisions.items()},
    )
    raise ParseError(
        "Multiple node labels sanitize to the same Python function name. "
        "Node names must be unique across the whole pipeline, including "
        "its submodels: submodels run in one flattened namespace with the "
        "main pipeline, so a duplicate would silently shadow its twin at "
        "execution time. Rename the offending nodes so each label "
        "produces a unique identifier:\n"
        f"{bullets}",
        collisions={k: list(v) for k, v in collisions.items()},
    )


def _edge_input_name_for_codegen(edge: GraphEdge, source_node: GraphNode) -> str:
    """Derive one emitted parameter name from an incoming graph edge.

    ``edge_input_name`` is the single source of truth shared with execution.
    Codegen adds the parser-facing error context for the one malformed graph
    state that the editor cannot create: an apiInput edge without a frame
    handle.
    """
    if source_node.data.nodeType == NodeType.API_INPUT and not edge.sourceHandle:
        raise ParseError(
            "apiInput edge has no source_port/sourceHandle.",
            edge_id=edge.id,
            source_node=source_node.id,
            source_node_label=source_node.data.label,
        )
    return edge_input_name(edge, source_node)


def _build_node_input_metadata(
    edges: list[GraphEdge],
    source_nodes: dict[str, GraphNode],
) -> tuple[dict[str, list[str]], dict[str, list[str]]]:
    """Build per-target input names and source IDs in edge declaration order."""
    names_by_target: dict[str, list[str]] = {}
    ids_by_target: dict[str, list[str]] = {}
    for edge in edges:
        source_node = source_nodes[edge.source]
        names_by_target.setdefault(edge.target, []).append(
            _edge_input_name_for_codegen(edge, source_node)
        )
        ids_by_target.setdefault(edge.target, []).append(edge.source)
    return names_by_target, ids_by_target


def _validate_duplicate_node_inputs(
    node_sources: dict[str, list[str]],
    target_nodes: dict[str, GraphNode],
) -> None:
    """Reject ambiguous per-edge parameters before any builder is called."""
    for target_id, names in node_sources.items():
        duplicates = duplicate_input_names(names)
        if not duplicates:
            continue
        target_node = target_nodes[target_id]
        raise ParseError(
            "Duplicate derived input name for one node.",
            node_id=target_node.id,
            node_label=target_node.data.label,
            input_name=duplicates[0],
            duplicate_names=duplicates,
        )


def _order_edge_join_incoming_edges(
    edges: list[GraphEdge],
    node_map: dict[str, GraphNode],
    *,
    source_id_for_edge: Callable[[GraphEdge], str] | None = None,
) -> list[GraphEdge]:
    """Order each edgeJoin node's incoming edges as base then join."""
    # A resolver changes only the identity used for role validation; the
    # returned edges retain their canonical placeholder endpoints and handles.
    incoming_by_target: dict[str, list[GraphEdge]] = {}
    for edge in edges:
        incoming_by_target.setdefault(edge.target, []).append(edge)

    ordered: list[GraphEdge] = []
    emitted_edge_join_targets: set[str] = set()
    for edge in edges:
        target_node = node_map.get(edge.target)
        if target_node is None or target_node.data.nodeType != NodeType.EDGE_JOIN:
            ordered.append(edge)
            continue
        if edge.target in emitted_edge_join_targets:
            continue
        group = incoming_by_target.get(edge.target, [])
        if len(group) != 2:
            ordered.extend(group)
            emitted_edge_join_targets.add(edge.target)
            continue
        source_ids = [
            source_id_for_edge(incoming) if source_id_for_edge is not None else incoming.source
            for incoming in group
        ]
        target_handles = [incoming.targetHandle for incoming in group]
        base_index, join_index = resolve_edge_join_role_indices(
            target_node.data.config,
            source_ids,
            target_handles,
        )
        ordered.extend([group[base_index], group[join_index]])
        emitted_edge_join_targets.add(edge.target)
    return ordered


def _build_instance_of_map(sorted_nodes: list[GraphNode]) -> dict[str, str]:
    """Map instance node ID -> original node ID for nodes with ``instanceOf``."""
    result: dict[str, str] = {}
    for node in sorted_nodes:
        ref = node.data.config.get("instanceOf")
        if ref:
            result[node.id] = ref
    return result


#: Type alias for a function that generates code for a single node.
_NodeCodeFn = Callable[
    [GraphNode, list[str] | None, list[str] | None, list[str] | None],
    str,
]
_ConnectPair = tuple[str, str, str | None, str | None]


def _generate_pipeline_lines(
    *,
    kind: str,
    name: str,
    description: str,
    preamble: str,
    sorted_nodes: list[GraphNode],
    id_to_func: dict[str, str],
    node_sources: dict[str, list[str]],
    connect_pairs: list[_ConnectPair],
    node_source_func_names: dict[str, list[str]] | None = None,
    node_source_ids: dict[str, list[str]] | None = None,
    preserved_blocks: list[str] | None = None,
    outputs: list[str] | None = None,
    submodel_imports: list[str] | None = None,
    node_to_code_fn: _NodeCodeFn = _node_to_code,
    dedup_connects: bool = False,
    obj_name: str = "pipeline",
) -> list[str]:
    """Generate the body of a pipeline or submodel file as a list of lines.

    Shared by the no-submodel and multi-submodel paths in
    ``graph_to_code_multi`` to eliminate duplicated header / node / connect
    generation logic.
    """
    # Header ----------------------------------------------------------------
    # The name is user-controlled and lands between the docstring's triple
    # quotes, so it shares the description sanitizer: one mechanism for
    # every "text between triple quotes" interpolation.  A name containing
    # ``"""`` or ending in a backslash must neither break the file nor
    # escape the docstring into executable module-level code.  The parse
    # side recovers the exact name from the ``haute.Pipeline``/``Submodel``
    # constructor literal below, so re-saving is a fixpoint.
    if kind == "submodel":
        outputs_kwarg = (
            ""
            if outputs is None
            else f", outputs=[{', '.join(_safe_str(output) for output in outputs)}]"
        )
        lines = [
            f'"""Submodel: {_sanitize_description(name)}"""',
            "",
            "import polars as pl",
            "import haute",
        ]
        if preamble.strip():
            lines.append("")
            lines.append(preamble.rstrip())
        lines += [
            "",
            (
                f"{obj_name} = haute.Submodel({_safe_str(name)}, "
                f"description={description!r}{outputs_kwarg})"
            ),
            "",
            "",
        ]
    else:
        lines = [
            f'"""Pipeline: {_sanitize_description(name)}"""',
            "",
            "import polars as pl",
            "import haute",
        ]
        if preamble.strip():
            lines.append("")
            lines.append(preamble.rstrip())
        lines += [
            "",
            f"{obj_name} = haute.Pipeline({_safe_str(name)}, description={description!r})",
            "",
            "",
        ]

    # Preserved blocks ------------------------------------------------------
    if preserved_blocks:
        lines.extend(_emit_preserved_blocks(preserved_blocks))
        lines.append("")

    # Nodes: originals then instances --------------------------------------
    instance_of_map = _build_instance_of_map(sorted_nodes)
    originals = [n for n in sorted_nodes if n.id not in instance_of_map]
    instances = [n for n in sorted_nodes if n.id in instance_of_map]

    for node in originals:
        srcs = node_sources.get(node.id, [])
        src_ids = (node_source_ids or {}).get(node.id, [])
        src_func_names = (node_source_func_names or {}).get(node.id, [])
        lines.append(node_to_code_fn(node, srcs, src_ids, src_func_names))
        lines.append("")

    for node in instances:
        srcs = node_sources.get(node.id, [])
        orig_id = instance_of_map[node.id]
        orig_func = id_to_func.get(orig_id, orig_id)
        orig_src = node_sources.get(orig_id, [])
        inst_code = _instance_to_code(
            node,
            orig_func,
            source_names=srcs,
            source_ids=(node_source_ids or {}).get(node.id, []),
            orig_source_names=orig_src,
        )
        # Inside submodel files the decorator prefix must be @submodel.*
        if obj_name != "pipeline":
            inst_code = inst_code.replace("@pipeline.", f"@{obj_name}.", 1)
        lines.append(inst_code)
        lines.append("")

    # Submodel imports (pipeline files only) -------------------------------
    if submodel_imports:
        for imp in submodel_imports:
            lines.append(imp)
        lines.append("")

    # Connect calls --------------------------------------------------------
    # Each pair carries an optional source_port. When present (non-empty
    # string), emit the multi-frame form
    # `pipeline.connect("a", "b", source_port="p")`. Otherwise emit the
    # single-frame bare form. Per MULTI_FRAME_PLAN.md §6.
    #
    # Use ``json.dumps`` for the frame literal so user-controlled labels
    # containing quotes / backslashes / non-ASCII characters survive
    # round-trip without producing invalid Python (the adversarial
    # review's C2 finding — bare f-string interpolation breaks on
    # ``label = 'a"b'``). Func names are codegen-derived sanitised
    # identifiers so the bare-string form is safe for those.
    import json as _json

    def _format_connect(
        src_func: str,
        tgt_func: str,
        source_port: str | None,
        target_port: str | None,
    ) -> str:
        kwargs: list[str] = []
        if source_port:
            kwargs.append(f"source_port={_json.dumps(source_port)}")
        if target_port:
            kwargs.append(f"target_port={_json.dumps(target_port)}")
        if kwargs:
            return f'{obj_name}.connect("{src_func}", "{tgt_func}", {", ".join(kwargs)})'
        return f'{obj_name}.connect("{src_func}", "{tgt_func}")'

    if connect_pairs:
        lines.append("")
        lines.append("# Wire nodes together - edges define data flow")
        if dedup_connects:
            seen: set[_ConnectPair] = set()
            for src_func, tgt_func, source_port, target_port in connect_pairs:
                key = (src_func, tgt_func, source_port, target_port)
                if key not in seen:
                    seen.add(key)
                    lines.append(_format_connect(src_func, tgt_func, source_port, target_port))
        else:
            for src_func, tgt_func, source_port, target_port in connect_pairs:
                lines.append(_format_connect(src_func, tgt_func, source_port, target_port))
        lines.append("")

    return lines


# ---------------------------------------------------------------------------
# Public orchestration API
# ---------------------------------------------------------------------------


def _assert_emitted_files_parse(files: dict[str, str]) -> dict[str, str]:
    """Final emission gate: every generated file must be valid Python.

    Codegen interpolates user-authored text (descriptions, labels, node
    code bodies) into source files; any remaining bug — or a node code
    block that is itself invalid Python — must fail the save loudly
    instead of silently writing a corrupt ``.py`` that the AST parser
    can never load again.  The save route is transactional (rollback +
    error surface, ``ConfigError`` -> HTTP 400), so raising here means
    no partial state lands on disk.
    """
    for rel_path, code in files.items():
        try:
            ast.parse(code)
        except SyntaxError as exc:
            offending = (exc.text or "").strip()
            raise ConfigError(
                f"generated pipeline file {rel_path!r} is not valid Python "
                f"(line {exc.lineno}: {exc.msg}). Refusing to emit a corrupt "
                "file — check the node code blocks for syntax errors.",
                file=rel_path,
                line=exc.lineno,
                offending_text=offending or None,
            ) from exc
    return files


def graph_to_code(
    graph: PipelineGraph,
    pipeline_name: str = "main",
    description: str = "",
    preamble: str = "",
    preserved_blocks: list[str] | None = None,
) -> str:
    """Convert a React Flow graph to a valid haute pipeline .py file.

    Delegates to :func:`graph_to_code_multi` and returns the single generated
    file's code.
    """
    files = graph_to_code_multi(
        graph,
        pipeline_name=pipeline_name,
        description=description,
        preamble=preamble,
        preserved_blocks=preserved_blocks,
    )
    # graph_to_code_multi returns {filename: code}; this single-file API is
    # only valid when there are no submodels.  A submodel graph produces
    # multiple files (submodels first, main last), so silently returning the
    # sole value would hand back the FIRST submodel, not the main pipeline —
    # fail loud and direct the caller to graph_to_code_multi instead.
    if len(files) != 1:
        raise ConfigError(
            "graph_to_code() is single-file only, but this graph produced "
            f"{len(files)} files (it has submodels). Use graph_to_code_multi().",
            file_count=len(files),
            files=sorted(files),
        )
    return next(iter(files.values()))


def _submodel_node_to_code(
    node: GraphNode,
    source_names: list[str] | None = None,
    source_ids: list[str] | None = None,
    source_func_names: list[str] | None = None,
) -> str:
    """Generate code for a single node inside a submodel file.

    Identical to ``_node_to_code`` but uses ``@submodel.<type>`` instead of
    ``@pipeline.<type>``.
    """
    code = _node_to_code(
        node,
        source_names=source_names,
        source_ids=source_ids,
        source_func_names=source_func_names,
    )
    code = code.replace("@pipeline.", "@submodel.", 1)
    if node.data.nodeType == NodeType.EDGE_JOIN:
        code = code.replace("pipeline._apply_edge_join(", "submodel._apply_edge_join(")
    return code


def graph_to_code_multi(
    graph: PipelineGraph,
    pipeline_name: str = "main",
    description: str = "",
    preamble: str = "",
    source_file: str = "",
    preserved_blocks: list[str] | None = None,
) -> dict[str, str]:
    """Generate code for a pipeline with submodels.

    Returns a dict mapping relative file path -> generated Python code.
    E.g. ``{"main.py": "...", "modules/model_scoring.py": "..."}``.

    If the graph has no submodels, the result contains only the main file.
    """
    # Fall back to graph-level description when caller doesn't supply one
    if not description and graph.pipeline_description:
        description = graph.pipeline_description
    if not preamble and graph.preamble:
        preamble = graph.preamble
    submodels = graph.submodels or {}
    validate_pipeline_graph_shape_contracts(graph, graph_label=pipeline_name)

    # Detect colliding labels across the whole graph once, eagerly, so
    # duplicate-function-name collisions are reported even when the
    # file-generation path short-circuits.
    # Hierarchical payloads may retain a transport copy of each child in
    # ``graph.nodes`` as well as the authoritative copy in the embedded
    # submodel graph. Codegen assigns every ``childNodeIds`` entry to the
    # child module, so do not count that same logical node twice here.
    represented_child_ids = {
        child_id for sm_meta in submodels.values() for child_id in sm_meta.get("childNodeIds", [])
    }
    collision_labels: list[str] = [
        node.data.label for node in graph.nodes if node.id not in represented_child_ids
    ]
    for sm_meta in submodels.values():
        for raw in sm_meta.get("graph", {}).get("nodes", []):
            if isinstance(raw, dict):
                label = raw.get("data", {}).get("label", "")
            else:
                label = raw.data.label
            collision_labels.append(label)
    _error_on_name_collisions(collision_labels)

    if not submodels:
        # No submodels — single-file output
        main_key = source_file or f"{pipeline_name}.py"
        nodes = graph.nodes
        node_map = {node.id: node for node in nodes}
        edges = _order_edge_join_incoming_edges(graph.edges, node_map)
        sorted_nodes = _topo_sort(nodes, edges)

        id_to_func = _build_id_to_func(sorted_nodes)
        node_sources, node_source_ids = _build_node_input_metadata(edges, node_map)
        _validate_duplicate_node_inputs(node_sources, node_map)
        node_source_func_names = {
            target_id: [id_to_func[source_id] for source_id in source_ids]
            for target_id, source_ids in node_source_ids.items()
        }

        all_preserved = preserved_blocks if preserved_blocks is not None else graph.preserved_blocks

        # Build connect pairs from edges. Each pair is
        # (src_func, tgt_func, source_port) where source_port is the
        # edge's `sourceHandle` if set, otherwise None (single-frame).
        connect_pairs = [
            (
                id_to_func.get(e.source, e.source),
                id_to_func.get(e.target, e.target),
                e.sourceHandle or None,
                e.targetHandle or None,
            )
            for e in edges
        ]

        lines = _generate_pipeline_lines(
            kind="pipeline",
            name=pipeline_name,
            description=description,
            preamble=preamble,
            sorted_nodes=sorted_nodes,
            id_to_func=id_to_func,
            node_sources=node_sources,
            connect_pairs=connect_pairs,
            node_source_func_names=node_source_func_names,
            node_source_ids=node_source_ids,
            preserved_blocks=all_preserved or None,
            node_to_code_fn=_node_to_code,
        )

        logger.info("code_generated", pipeline_name=pipeline_name, node_count=len(sorted_nodes))
        return _assert_emitted_files_parse({main_key: "\n".join(lines)})

    # Separate nodes into root-level vs submodel children ----------------
    all_child_ids: set[str] = set()
    submodel_node_ids: set[str] = set()
    submodel_child_ids: dict[str, set[str]] = {}
    submodel_child_nodes: dict[str, dict[str, GraphNode]] = {}
    for sm_name, sm_meta in submodels.items():
        child_ids = set(sm_meta.get("childNodeIds", []))
        all_child_ids.update(child_ids)
        submodel_node_id = f"submodel__{sm_name}"
        submodel_node_ids.add(submodel_node_id)
        submodel_child_ids[submodel_node_id] = child_ids
        raw_child_nodes = sm_meta.get("graph", {}).get("nodes", [])
        parsed_child_nodes = [
            GraphNode.model_validate(raw) if isinstance(raw, dict) else raw
            for raw in raw_child_nodes
        ]
        submodel_child_nodes[submodel_node_id] = {child.id: child for child in parsed_child_nodes}

    nodes = graph.nodes
    node_map = {node.id: node for node in nodes}
    edges = list(graph.edges)

    # Root-level nodes: not children and not the submodel placeholder itself
    root_nodes = [n for n in nodes if n.id not in all_child_ids and n.id not in submodel_node_ids]

    # Root-level edges: only between root-level nodes OR crossing submodel boundary
    root_node_ids = {n.id for n in root_nodes}

    # Build id -> func_name for root nodes (needed by submodel cross-boundary resolution)
    root_id_to_func = _build_id_to_func(root_nodes)

    # Generate submodel files --------------------------------------------
    files: dict[str, str] = {}

    for sm_name, sm_meta in submodels.items():
        sm_graph = sm_meta.get("graph", {})
        sm_file = sm_meta.get("file", f"modules/{sm_name}.py").replace("\\", "/")
        raw_nodes = sm_graph.get("nodes", [])
        raw_edges = sm_graph.get("edges", [])
        sm_nodes = [GraphNode.model_validate(n) if isinstance(n, dict) else n for n in raw_nodes]
        sm_edges = [GraphEdge.model_validate(e) if isinstance(e, dict) else e for e in raw_edges]
        sm_node_map = {node.id: node for node in sm_nodes}
        sm_edges = _order_edge_join_incoming_edges(sm_edges, sm_node_map)

        sorted_sm_nodes = _topo_sort(sm_nodes, sm_edges)
        sm_id_to_func = _build_id_to_func(sorted_sm_nodes)
        sm_node_sources, sm_node_source_ids = _build_node_input_metadata(sm_edges, sm_node_map)
        sm_node_source_func_names = {
            target_id: [sm_id_to_func[source_id] for source_id in source_ids]
            for target_id, source_ids in sm_node_source_ids.items()
        }

        # Also include cross-boundary inputs from parent graph edges.
        # Every edge targeting the submodel placeholder must carry a valid
        # ``in__<child_id>`` handle — anything else indicates a malformed
        # edge and we fail loudly rather than silently dropping it.
        sm_node_id = f"submodel__{sm_name}"
        sm_child_ids = {n.id for n in sm_nodes}
        submodel_child_ids[sm_node_id] = sm_child_ids
        raw_output_ports = sm_meta.get("outputPorts")
        if raw_output_ports is None:
            inferred_output_ports: list[str] = []
            for edge in edges:
                if edge.source != sm_node_id:
                    continue
                output_handle = edge.sourceHandle or ""
                if not output_handle.startswith("out__"):
                    continue
                output_child_id = output_handle.removeprefix("out__")
                if output_child_id and output_child_id not in inferred_output_ports:
                    inferred_output_ports.append(output_child_id)
            raw_output_ports = inferred_output_ports
        if not isinstance(raw_output_ports, list):
            raise ParseError(
                "Submodel outputPorts must be an ordered list of child node ids.",
                submodel=sm_name,
                output_ports=raw_output_ports,
            )

        declared_output_ids: list[str] = []
        seen_output_ids: set[str] = set()
        for output_id in raw_output_ports:
            if not isinstance(output_id, str) or not output_id:
                raise ParseError(
                    "Each submodel output must be a non-empty child node id.",
                    submodel=sm_name,
                    output=output_id,
                )
            if output_id in seen_output_ids:
                raise ParseError(
                    "Submodel outputPorts contain a duplicate output.",
                    submodel=sm_name,
                    output=output_id,
                )
            if output_id not in sm_child_ids:
                raise ParseError(
                    "Submodel output references a child node that does not exist.",
                    submodel=sm_name,
                    output=output_id,
                    known_children=sorted(sm_child_ids),
                )
            seen_output_ids.add(output_id)
            declared_output_ids.append(output_id)
        declared_output_names = [sm_id_to_func[output_id] for output_id in declared_output_ids]
        for edge in edges:
            if edge.target != sm_node_id:
                continue
            handle = edge.targetHandle
            if not handle or not handle.startswith("in__"):
                raise ParseError(
                    "Submodel cross-boundary edge has missing or malformed "
                    "targetHandle; expected 'in__<child_id>'.",
                    edge_id=edge.id,
                    handle=handle,
                    submodel=sm_name,
                    source=edge.source,
                    target=edge.target,
                )
            child_id = handle[len("in__") :]
            if child_id not in sm_child_ids:
                raise ParseError(
                    "Submodel cross-boundary edge references a child node "
                    "that does not exist in the submodel.",
                    edge_id=edge.id,
                    handle=handle,
                    child_id=child_id,
                    submodel=sm_name,
                    known_children=sorted(sm_child_ids),
                )
            source_id = edge.source
            source_edge = edge
            if source_id in submodel_node_ids:
                source_handle = edge.sourceHandle or ""
                if not source_handle.startswith("out__"):
                    raise ParseError(
                        "Submodel cross-boundary edge has missing or malformed "
                        "sourceHandle; expected 'out__<child_id>'.",
                        edge_id=edge.id,
                        handle=edge.sourceHandle,
                        source=edge.source,
                        target=edge.target,
                    )
                source_id = source_handle.removeprefix("out__")
                source_node = submodel_child_nodes.get(edge.source, {}).get(source_id)
                if source_node is None:
                    raise ParseError(
                        "Submodel cross-boundary edge references a child node "
                        "that does not exist in the source submodel.",
                        edge_id=edge.id,
                        child_id=source_id,
                        submodel=edge.source.removeprefix("submodel__"),
                    )
                source_edge = edge.model_copy(
                    update={"sourceHandle": edge.sourcePort},
                )
            else:
                source_node = node_map[source_id]
            src_name = _edge_input_name_for_codegen(source_edge, source_node)
            existing = sm_node_sources.setdefault(child_id, [])
            existing_ids = sm_node_source_ids.setdefault(child_id, [])
            existing.append(src_name)
            existing_ids.append(source_id)
            sm_node_source_func_names.setdefault(child_id, []).append(
                root_id_to_func.get(source_id, _sanitize_func_name(source_node.data.label))
            )
        _validate_duplicate_node_inputs(sm_node_sources, sm_node_map)
        # Validate out__ cross-boundary edges early, during submodel-file
        # generation, so a malformed source handle fails with a clear
        # ParseError here rather than a confusing downstream error when the
        # mis-wired child node is later emitted.
        for edge in edges:
            if edge.source != sm_node_id:
                continue
            handle = edge.sourceHandle
            if not handle or not handle.startswith("out__"):
                raise ParseError(
                    "Submodel cross-boundary edge has missing or malformed "
                    "sourceHandle; expected 'out__<child_id>'.",
                    edge_id=edge.id,
                    handle=handle,
                    submodel=sm_name,
                    source=edge.source,
                    target=edge.target,
                )
            child_id = handle[len("out__") :]
            if child_id not in sm_child_ids:
                raise ParseError(
                    "Submodel cross-boundary edge references a child node "
                    "that does not exist in the submodel.",
                    edge_id=edge.id,
                    handle=handle,
                    child_id=child_id,
                    submodel=sm_name,
                    known_children=sorted(sm_child_ids),
                )

        # Build connect pairs from internal edges. Same triple shape as
        # the root-level construction — sourceHandle threads through so
        # submodel-internal multi-frame edges (if any) survive a save.
        sm_connect_pairs = [
            (
                sm_id_to_func.get(e.source, e.source),
                sm_id_to_func.get(e.target, e.target),
                e.sourceHandle or None,
                e.targetHandle or None,
            )
            for e in sm_edges
        ]

        sm_lines = _generate_pipeline_lines(
            kind="submodel",
            name=sm_name,
            description=sm_graph.get("pipeline_description") or "",
            preamble=sm_graph.get("preamble") or "",
            sorted_nodes=sorted_sm_nodes,
            id_to_func=sm_id_to_func,
            node_sources=sm_node_sources,
            connect_pairs=sm_connect_pairs,
            node_source_func_names=sm_node_source_func_names,
            node_source_ids=sm_node_source_ids,
            preserved_blocks=sm_graph.get("preserved_blocks") or None,
            outputs=declared_output_names,
            node_to_code_fn=_submodel_node_to_code,
            obj_name="submodel",
        )

        files[sm_file] = "\n".join(sm_lines)

    # Generate main pipeline file ----------------------------------------

    sorted_root = (
        _topo_sort(
            root_nodes,
            [e for e in edges if e.source in root_node_ids and e.target in root_node_ids],
        )
        if root_nodes
        else []
    )

    # Also map submodel child node IDs to func names (for edge generation)
    for sm_name, sm_meta in submodels.items():
        sm_graph = sm_meta.get("graph", {})
        for n in sm_graph.get("nodes", []):
            nd = GraphNode.model_validate(n) if isinstance(n, dict) else n
            root_id_to_func[nd.id] = _sanitize_func_name(nd.data.label)

    def _resolve_submodel_endpoint(
        edge: GraphEdge,
        node_id: str,
        handle: str,
        *,
        prefix: str,
        endpoint: str,
    ) -> str:
        """Resolve a submodel boundary handle to a child node id."""
        if node_id not in submodel_node_ids:
            return node_id
        if not handle or not handle.startswith(prefix):
            raise ParseError(
                "Submodel cross-boundary edge has missing or malformed "
                f"{endpoint}Handle; expected '{prefix}<child_id>'.",
                edge_id=edge.id,
                handle=handle or None,
                submodel=node_id.removeprefix("submodel__"),
                source=edge.source,
                target=edge.target,
            )
        child_id = handle[len(prefix) :]
        known_children = submodel_child_ids.get(node_id, set())
        if child_id not in known_children:
            raise ParseError(
                "Submodel cross-boundary edge references a child node "
                "that does not exist in the submodel.",
                edge_id=edge.id,
                handle=handle,
                child_id=child_id,
                submodel=node_id.removeprefix("submodel__"),
                known_children=sorted(known_children),
            )
        return child_id

    edges = _order_edge_join_incoming_edges(
        edges,
        node_map,
        source_id_for_edge=lambda edge: _resolve_submodel_endpoint(
            edge,
            edge.source,
            edge.sourceHandle or "",
            prefix="out__",
            endpoint="source",
        ),
    )

    # Build source names per root node from root-level edges AND
    # cross-boundary edges (resolving submodel handles to child node names).
    submodel_node_maps: dict[str, dict[str, GraphNode]] = {}
    for sm_name, sm_meta in submodels.items():
        sm_nodes = [
            GraphNode.model_validate(raw) if isinstance(raw, dict) else raw
            for raw in sm_meta.get("graph", {}).get("nodes", [])
        ]
        submodel_node_maps[f"submodel__{sm_name}"] = {node.id: node for node in sm_nodes}

    root_node_sources: dict[str, list[str]] = {}
    root_node_source_ids: dict[str, list[str]] = {}
    root_node_source_func_names: dict[str, list[str]] = {}
    for edge in edges:
        src = edge.source
        tgt = edge.target
        sh = edge.sourceHandle or ""
        th = edge.targetHandle or ""

        # Resolve submodel handles to actual child node names
        actual_src = _resolve_submodel_endpoint(
            edge,
            src,
            sh,
            prefix="out__",
            endpoint="source",
        )
        actual_tgt = _resolve_submodel_endpoint(
            edge,
            tgt,
            th,
            prefix="in__",
            endpoint="target",
        )

        # Only care about edges feeding into root nodes
        if actual_tgt not in root_node_ids:
            continue
        if src in submodel_node_ids:
            source_node = submodel_node_maps[src][actual_src]
            source_edge = edge.model_copy(
                update={"sourceHandle": edge.sourcePort},
            )
        else:
            source_node = node_map[src]
            source_edge = edge
        src_name = _edge_input_name_for_codegen(source_edge, source_node)
        root_node_sources.setdefault(actual_tgt, []).append(src_name)
        # Use the RESOLVED source id (child node id for a cross-boundary edge),
        # not the raw submodel placeholder id, so ids and names stay aligned
        # and _parent_name_by_id builds a correct id->func map.
        root_node_source_ids.setdefault(actual_tgt, []).append(actual_src)
        root_node_source_func_names.setdefault(actual_tgt, []).append(
            root_id_to_func.get(actual_src, _sanitize_func_name(actual_src))
        )

    _validate_duplicate_node_inputs(root_node_sources, node_map)

    # Build connect pairs for ALL edges (cross-boundary use real node names).
    # Four-tuple shape: (src_func, tgt_func, source_port, target_port).
    # Synthetic in__/out__ handles identify boundary children; sourcePort /
    # targetPort retain any authored connect ports hidden by those markers.
    submodel_edge_join_target_roles = build_edge_join_boundary_target_roles(submodels)
    root_connect_pairs: list[_ConnectPair] = []
    for edge in edges:
        src = edge.source
        tgt = edge.target
        sh = edge.sourceHandle or ""
        th = edge.targetHandle or ""

        # Resolve submodel handles to actual node names
        actual_src = _resolve_submodel_endpoint(
            edge,
            src,
            sh,
            prefix="out__",
            endpoint="source",
        )
        actual_tgt = _resolve_submodel_endpoint(
            edge,
            tgt,
            th,
            prefix="in__",
            endpoint="target",
        )

        src_func = root_id_to_func.get(actual_src, _sanitize_func_name(actual_src))
        tgt_func = root_id_to_func.get(actual_tgt, _sanitize_func_name(actual_tgt))
        # Submodel-boundary `out__<id>` handles aren't user-facing frame
        # names; only forward sourceHandle as source_port when it's not
        # a submodel-boundary edge (i.e. the source isn't a submodel
        # placeholder node). Per the adversarial review's S1: gating on
        # the prefix alone would also silently drop a regular apiInput
        # table labelled e.g. "out__claims".
        is_submodel_boundary = src in submodel_node_ids
        is_submodel_target_boundary = tgt in submodel_node_ids
        source_port = edge.sourcePort if is_submodel_boundary else edge.sourceHandle
        target_port: str | None
        if edge.targetHandle and not is_submodel_target_boundary:
            target_port = edge.targetHandle
        elif is_submodel_target_boundary:
            target_port = edge.targetPort
            if target_port is None:
                target_port = submodel_edge_join_target_roles.get((tgt, actual_tgt, actual_src))
        else:
            target_port = None
        root_connect_pairs.append((src_func, tgt_func, source_port, target_port))

    # Submodel import lines
    sm_imports = []
    for sm_name, sm_meta in submodels.items():
        sm_path = sm_meta.get("file", f"modules/{sm_name}.py").replace("\\", "/")
        sm_imports.append(f"pipeline.submodel({_safe_path(sm_path)})")

    all_preserved = preserved_blocks if preserved_blocks is not None else graph.preserved_blocks

    main_lines = _generate_pipeline_lines(
        kind="pipeline",
        name=pipeline_name,
        description=description,
        preamble=preamble,
        sorted_nodes=sorted_root,
        id_to_func=root_id_to_func,
        node_sources=root_node_sources,
        connect_pairs=root_connect_pairs,
        node_source_func_names=root_node_source_func_names,
        node_source_ids=root_node_source_ids,
        preserved_blocks=all_preserved or None,
        submodel_imports=sm_imports,
        node_to_code_fn=_node_to_code,
        dedup_connects=True,
    )

    main_key = source_file or f"{pipeline_name}.py"
    files[main_key] = "\n".join(main_lines)
    return _assert_emitted_files_parse(files)
