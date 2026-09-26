"""Code generator orchestration: graph JSON -> valid pipeline .py file.

The per-type ``_gen_*`` builders and the codegen-side registry live in
:mod:`haute._codegen_builders`.  This module keeps the graph-level
assembly logic (``graph_to_code`` / ``graph_to_code_multi``) and the
single-node dispatcher that drives the unified
:data:`haute._registry.NODE_REGISTRY`.
"""

from __future__ import annotations

import ast
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from typing import Literal

from haute._codegen_builders import (
    NodeSource,
    _params,
    _sanitize_description,
    render_node_source,
)
from haute._config_builder import resolve_parse_time_contract
from haute._config_validation import reject_removed_config_keys
from haute._contracts import (
    Contract,
    get_column_contract,
)
from haute._edge_join import resolve_edge_join_role_indices
from haute._graph_shape import (
    validate_graph_shape_contracts,
    validate_pipeline_graph_shape_contracts,
)
from haute._graph_utils import (
    _sanitize_func_name,
    build_instance_mapping,
    duplicate_input_names,
    edge_input_name,
    resolve_input_mapping_names,
)
from haute._logging import get_logger
from haute._registry import NODE_REGISTRY
from haute._source_layout import Doc, arguments, literal, print_doc, quote_string
from haute._submodel_instances import ResolvedSubmodelInstance, resolve_submodel_instances
from haute._submodel_paths import definition_pipeline_dir
from haute._topo import topo_sort_ids
from haute._types import (
    GraphEdge,
    GraphNode,
    NodeType,
    PipelineGraph,
    SubmodelDefinition,
)
from haute.errors import ConfigError, HauteError, ParseError

logger = get_logger(component="codegen")

ContractSource = Literal["builder", "offline", "declared"]
"""How generation derives a node's ``contract=`` annotation.

``"builder"`` derives it from the builder, as a save does. ``"offline"``
derives what the parse-time check compares against, never loading an
external model artifact (recovery). ``"declared"`` derives nothing and emits
only the node's declaration.
"""

__all__ = [
    "graph_to_code",
    "graph_to_code_multi",
]


# ---------------------------------------------------------------------------
# Single-node dispatch
# ---------------------------------------------------------------------------


def _is_codegen_infra_error(exc: BaseException) -> bool:
    """Whether *exc* is an environmental/infra failure safe to treat as opaque.

    Only an ``OSError`` (missing artifact file, refused connection), an
    exception raised from the ``mlflow`` package (server unreachable), or the
    ``MlflowDestinationUnconfigured`` marker (the node's explicit destination
    is merely not configured on this machine) counts. Everything else —
    ``TypeError``/``KeyError``/``ValueError`` bugs, any other ``HauteError``
    including every other ``MlflowConfigError`` (unknown key, rejected SDK
    mode) — is a real defect that must fail the save loudly.
    """
    from haute.modelling._mlflow_settings import MlflowDestinationUnconfigured

    if isinstance(exc, OSError):
        return True
    if isinstance(exc, MlflowDestinationUnconfigured):
        # The node names a destination this environment has not configured
        # (no server URL, no Databricks credentials). That is the authoring
        # machine's state, not a defect in the node: the executor resolves
        # the same destination at run time and fails loudly there, so the
        # save must not demand the remote just to document feature columns.
        return True
    return type(exc).__module__.split(".", 1)[0] == "mlflow"


def _contract_keyword(
    node: GraphNode,
    parent_name_by_id: dict[str, str] | None = None,
    *,
    contract_source: ContractSource = "builder",
) -> dict[str, object] | None:
    """The ``contract=`` keyword value for *node*, or ``None`` when it adds nothing.

    The contract is re-derived from the node's current config on every
    call.  A declared ``config["contract"]`` — usually the annotation the
    previous save generated, carried back by the parser — only supplies the
    sides the builder cannot derive, plus its fan-in ownership metadata; a
    concrete derived side replaces it, so a config edit never leaves a stale
    annotation that the post-save parse check rejects.

    *contract_source* picks the derivation (see :data:`ContractSource`).
    Instance nodes, and ``"declared"`` generation, start from the
    declaration alone: an instance's own config does not describe the
    columns it references.

    The keyword is emitted only when it tells the parser something it cannot
    derive offline from the node's config (:func:`resolve_parse_time_contract`):
    a concrete side that derivation leaves opaque, or ``inputs_by_parent``.
    An opaque contract, or one the settings already imply, is left out; no
    consumer tells it apart from an absent one.
    """
    config = node.data.config
    declared = Contract.from_user_declared(config.get("contract"))
    instance = bool(config.get("instanceOf"))
    if instance or contract_source == "declared":
        contract = declared
    else:
        derived = (
            _derive_contract_for_codegen(node)
            if contract_source == "builder"
            else resolve_parse_time_contract(node.data.nodeType, config)
        )
        contract = (
            derived
            if declared is None
            else replace(
                derived.fill_opaque_sides(declared),
                inputs_by_parent=declared.inputs_by_parent,
            )
        )
    if contract is None:
        return None
    value = _contract_value(contract, parent_name_by_id=parent_name_by_id)
    # The parser reads an instance as a Polars node, whose contract it cannot derive.
    offline = (
        Contract.opaque() if instance else resolve_parse_time_contract(node.data.nodeType, config)
    )
    adds_information = (
        "inputs_by_parent" in value
        or (value["inputs"] is not None and offline.inputs is None)
        or (value["outputs"] is not None and offline.outputs is None)
    )
    return value if adds_information else None


def _derive_contract_for_codegen(node: GraphNode) -> Contract:
    """Derive *node*'s builder contract for its generated annotation.

    Contract computation for some nodes (notably ``MODEL_SCORE``)
    loads an MLflow artifact to discover feature names — that load can
    fail at codegen time in disconnected environments or CI runs.  We
    treat *infrastructure* failures ONLY (see :func:`_is_codegen_infra_error`)
    as degraded rather than propagating: the annotation falls back to the
    contract the parser derives offline, which is exactly what the post-save
    parse check compares it against, and the executor still re-computes +
    enforces the contract at runtime from the actual model.  Forcing a
    running MLflow server just to save a pipeline would be a regression.

    Every OTHER exception fails loud at save time.  ``ConfigError``
    (misconfiguration — e.g. ``sourceType="run"`` with no ``run_id``) and
    any other ``HauteError`` (including ``ContractMismatchError``) propagate,
    as do plain ``TypeError`` / ``KeyError`` / ``ValueError`` bugs in the
    contract computation itself — emitting ``contract="opaque"`` for those
    would hide a real bug inside a file that silently runs, then blows up at
    execution far from the cause.
    """
    node_type = node.data.nodeType
    config = node.data.config
    try:
        return Contract.from_tuple(get_column_contract(node_type, config))
    except Exception as exc:
        if not _is_codegen_infra_error(exc):
            raise
        logger.warning(
            "contract_emit_offline_on_error",
            node=node.data.label,
            node_type=str(node_type),
            error=str(exc),
        )
    return resolve_parse_time_contract(node_type, config)


def _contract_value(
    contract: Contract,
    *,
    parent_name_by_id: dict[str, str] | None = None,
) -> dict[str, object]:
    """A contract as the literal the decorator carries, keeping fan-in ownership metadata."""
    value: dict[str, object] = {
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
            value["inputs_by_parent"] = dict(sorted(inputs_by_parent.items()))
    return value


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


def _render(
    node: GraphNode,
    source: NodeSource,
    contract: dict[str, object] | None,
    receiver: str,
) -> str:
    """Print one node's function, naming the node in any printing failure."""
    extra = (("contract", contract),) if contract is not None else ()
    try:
        return render_node_source(
            source,
            func_name=_sanitize_func_name(node.data.label),
            receiver=receiver,
            extra_keywords=extra,
        )
    except HauteError as exc:
        exc.context.setdefault("node_id", node.id)
        exc.context.setdefault("node_label", node.data.label)
        exc.context.setdefault("node_type", str(node.data.nodeType))
        raise


def _node_to_code(
    node: GraphNode,
    source_names: list[str] | None = None,
    source_ids: list[str] | None = None,
    *,
    contract_source: ContractSource = "builder",
    receiver: str = "pipeline",
) -> str:
    """Generate the function for a single node.

    Delegates to :func:`_generate_node_code` for the type-specific description
    — a config-backed builder names its sidecar as the decorator's ``config=``
    — then adds the column contract as the decorator's last keyword when it
    carries information the parser cannot derive.
    """
    if source_names is None:
        source_names = []
    if source_ids is None:
        source_ids = []
    source = _generate_node_code(node, source_names)
    contract = _contract_keyword(
        node,
        parent_name_by_id=_parent_name_by_id(source_ids, source_names),
        contract_source=contract_source,
    )
    return _render(node, source, contract, receiver)


def _generate_node_code(node: GraphNode, source_names: list[str] | None = None) -> NodeSource:
    """Dispatch to the type-specific codegen builder via the unified registry.

    Fails loudly if no codegen builder is registered for the node's
    ``NodeType`` — per :data:`haute._registry.NODE_REGISTRY` contract, every
    ``NodeType`` must have a codegen entry; an absent one is a wiring bug
    worth crashing over, not a condition to silently paper over with a
    fallback to ``_gen_transform`` that hid misregistered types historically.
    """
    if source_names is None:
        source_names = []

    reject_removed_config_keys(node.data.nodeType, node.data.config)

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
    *,
    receiver: str = "pipeline",
) -> str:
    """Generate the declaration of an instance node.

    The decorator names the original node (``of=``) and persists an explicit
    ``inputMapping``; the executor runs the original's logic on the
    instance's inputs. An inconsistent mapping fails here rather than later.
    """
    data = node.data
    if source_names is None:
        source_names = []
    explicit_map = data.config.get("inputMapping")
    if explicit_map is not None and not isinstance(explicit_map, dict):
        raise ConfigError(
            "inputMapping must be an object mapping original input names to instance input names.",
            node_id=node.id,
            input_mapping=explicit_map,
        )
    if orig_source_names and source_names:
        # Stale or ambiguous pairings fail loudly now, not at execution.
        build_instance_mapping(
            orig_source_names, source_names, dict(explicit_map) if explicit_map else None
        )
    keywords: list[tuple[str, object]] = [("of", original_func_name)]
    if explicit_map is not None:
        keywords.append(("inputMapping", explicit_map))
    source = NodeSource(
        "instance", tuple(keywords), _params(source_names), description=data.description
    )
    contract = _contract_keyword(
        node,
        parent_name_by_id=_parent_name_by_id(source_ids or [], source_names),
    )
    return _render(node, source, contract, receiver)


# ---------------------------------------------------------------------------
# Pipeline assembly helpers
# ---------------------------------------------------------------------------


def _topo_sort(nodes: list[GraphNode], edges: list[GraphEdge]) -> list[GraphNode]:
    """Sort nodes in topological order based on edges."""
    node_map = {n.id: n for n in nodes}
    order = topo_sort_ids(list(node_map.keys()), edges)
    return [node_map[nid] for nid in order if nid in node_map]


def _emit_preserved_block(block: str) -> str:
    """Wrap one preserved block in its start/end markers."""
    return "\n".join(["# haute:preserve-start", block, "# haute:preserve-end"])


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


def _edge_input_name_for_codegen(
    edge: GraphEdge,
    source_node: GraphNode,
    *,
    submodels: Mapping[str, SubmodelDefinition] | None = None,
) -> str:
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
    return edge_input_name(edge, source_node, submodels=submodels)


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
) -> list[GraphEdge]:
    """Order each edgeJoin node's incoming edges as base then join."""
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
        target_handles = [incoming.targetHandle for incoming in group]
        base_index, join_index = resolve_edge_join_role_indices(target_handles)
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


_ConnectPair = tuple[str, str, str | None, str | None]

_WIRE_COMMENT = "# Wire nodes together - edges define data flow"


def _emission_order(nodes: list[GraphNode], edges: list[GraphEdge]) -> list[GraphNode]:
    """*nodes* in topological order, each input-less source just before its first consumer.

    Only edges between *nodes* count. A node with inputs keeps its position;
    a source that feeds one of *nodes* moves down to directly before the first
    of them to consume it (the sources of one consumer in its edge order); a
    source that feeds none of them keeps its topological position.
    """
    ids = {node.id for node in nodes}
    parents: dict[str, list[str]] = {}
    consumed: set[str] = set()
    for edge in edges:
        if edge.source in ids and edge.target in ids:
            parents.setdefault(edge.target, []).append(edge.source)
            consumed.add(edge.source)
    deferred = {node.id for node in nodes if node.id not in parents and node.id in consumed}
    by_id = {node.id: node for node in nodes}
    order: list[GraphNode] = []
    emitted: set[str] = set()
    for node in nodes:
        if node.id in deferred:
            continue
        for parent in parents.get(node.id, []):
            if parent in deferred and parent not in emitted:
                order.append(by_id[parent])
                emitted.add(parent)
        order.append(node)
        emitted.add(node.id)
    return order


def _call(head: str, entries: Sequence[Doc]) -> str:
    """One call statement, laid out as ruff lays it out."""
    return print_doc([head, arguments("(", list(entries), ")")])


def _keyword(name: str, value: object) -> Doc:
    return [name, "=", literal(value)]


def _connect_call(obj_name: str, pair: _ConnectPair) -> str:
    src_func, tgt_func, source_port, target_port = pair
    entries: list[Doc] = [quote_string(src_func), quote_string(tgt_func)]
    if source_port:
        entries.append(_keyword("source_port", source_port))
    if target_port:
        entries.append(_keyword("target_port", target_port))
    return _call(f"{obj_name}.connect", entries)


@dataclass(frozen=True, slots=True)
class _Block:
    """One run of top-level statements, and whether it opens or closes with a definition."""

    text: str
    starts_def: bool = False
    ends_def: bool = False


def _authored_block(text: str) -> _Block:
    """Authored module text, with the definition boundaries ruff spaces around."""
    try:
        body = ast.parse(text).body
    except SyntaxError:
        body = []
    definitions = (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)
    return _Block(
        text,
        starts_def=bool(body) and isinstance(body[0], definitions),
        ends_def=bool(body) and isinstance(body[-1], definitions),
    )


def _join_blocks(blocks: list[_Block]) -> str:
    """Join blocks with ruff's blank lines: two beside a definition, one otherwise."""
    out = blocks[0].text
    for previous, block in zip(blocks, blocks[1:], strict=False):
        gap = 2 if previous.ends_def or block.starts_def else 1
        out += "\n" * (gap + 1) + block.text
    return out + "\n"


def _refers_to_pl(text: str) -> bool:
    """Whether module *text* uses the name ``pl`` (so it needs ``import polars as pl``)."""
    try:
        tree = ast.parse(text)
    except SyntaxError:
        return True
    return any(isinstance(node, ast.Name) and node.id == "pl" for node in ast.walk(tree))


def _node_functions(
    nodes: list[GraphNode],
    edges: list[GraphEdge],
    *,
    id_to_func: dict[str, str],
    node_sources: dict[str, list[str]],
    node_source_ids: dict[str, list[str]] | None,
    receiver: str,
) -> list[str]:
    """Every node's function: originals in emission order, then instances."""
    instance_of_map = _build_instance_of_map(nodes)
    node_by_id = {node.id: node for node in nodes}
    originals = [n for n in nodes if n.id not in instance_of_map]
    instances = [n for n in nodes if n.id in instance_of_map]
    source_ids = node_source_ids or {}
    functions = [
        _node_to_code(
            node,
            node_sources.get(node.id, []),
            source_ids.get(node.id, []),
            receiver=receiver,
        )
        for node in _emission_order(originals, edges)
    ]
    for node in instances:
        orig_id = instance_of_map[node.id]
        orig_src = node_sources.get(orig_id, [])
        original = node_by_id.get(orig_id)
        if original is not None and original.data.nodeType == NodeType.POLARS:
            input_mapping = original.data.config.get("inputMapping")
            if input_mapping is not None:
                orig_src = resolve_input_mapping_names(orig_src, input_mapping)
        functions.append(
            _instance_to_code(
                node,
                id_to_func.get(orig_id, orig_id),
                source_names=node_sources.get(node.id, []),
                source_ids=source_ids.get(node.id, []),
                orig_source_names=orig_src,
                receiver=receiver,
            )
        )
    return functions


def _render_module(
    *,
    kind: Literal["pipeline", "submodel"],
    name: str,
    description: str,
    preamble: str,
    functions: list[str],
    connect_pairs: list[_ConnectPair],
    preserved_blocks: list[str] | None = None,
    registrations: list[str] | None = None,
    constructor_keywords: Sequence[tuple[str, object]] = (),
    dedup_connects: bool = False,
) -> str:
    """Assemble a pipeline or submodel file, laid out as ``ruff format`` would.

    The header docstring, the imports, the authored preamble, the object's
    construction, preserved blocks, one function per node, submodel
    registrations and the connect calls, separated by ruff's blank lines.
    ``import polars as pl`` is emitted only when the rest of the module
    refers to ``pl``.
    """
    obj_name = "pipeline" if kind == "pipeline" else "submodel"
    title = "Pipeline" if kind == "pipeline" else "Submodel"
    # The name lands between the docstring's triple quotes, so it shares the
    # description sanitizer; the parser recovers it from the constructor.
    header = _Block(f'"""{title}: {_sanitize_description(name)}"""')
    constructor: list[Doc] = [quote_string(name)]
    if description:
        constructor.append(_keyword("description", description))
    constructor.extend(_keyword(key, value) for key, value in constructor_keywords)

    body: list[_Block] = []
    if preamble.strip():
        body.append(_authored_block(preamble.strip("\n").rstrip()))
    body.append(_Block(_call(f"{obj_name} = haute.{title}", constructor)))
    for block in preserved_blocks or []:
        shape = _authored_block(block)
        body.append(_Block(_emit_preserved_block(block), shape.starts_def, shape.ends_def))
    body.extend(_Block(code.rstrip("\n"), starts_def=True, ends_def=True) for code in functions)
    if registrations:
        body.append(_Block("\n".join(registrations)))
    pairs = list(dict.fromkeys(connect_pairs)) if dedup_connects else connect_pairs
    if pairs:
        calls = [_connect_call(obj_name, pair) for pair in pairs]
        body.append(_Block("\n".join([_WIRE_COMMENT, *calls])))

    uses_pl = _refers_to_pl("\n\n".join(block.text for block in body))
    imports = "import haute\nimport polars as pl" if uses_pl else "import haute"
    return _join_blocks([header, _Block(imports), *body])


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


def _canonical_port_name(
    handle: str | None,
    *,
    prefix: str,
    edge: GraphEdge,
    endpoint: str,
) -> str:
    """Return a validated public port name from a canonical boundary handle."""
    if not handle or not handle.startswith(prefix) or handle == prefix:
        raise ParseError(
            "Canonical submodel edge has a malformed public-port handle.",
            edge_id=edge.id,
            endpoint=endpoint,
            handle=handle,
            expected=f"{prefix}<name>",
        )
    return handle.removeprefix(prefix)


def _canonical_definition_source_metadata(
    definition: SubmodelDefinition,
    ordered_edges: list[GraphEdge],
    node_map: dict[str, GraphNode],
) -> tuple[
    dict[str, list[str]],
    dict[str, list[str]],
]:
    """Build child-node inputs, ordering edge-join bindings by target role."""
    bindings_by_target: dict[str, list[tuple[str, str, str | None]]] = {}

    def add_binding(
        target_id: str,
        name: str,
        source_id: str,
        target_handle: str | None,
    ) -> None:
        bindings_by_target.setdefault(target_id, []).append((name, source_id, target_handle))

    node_sources: dict[str, list[str]] = {}
    node_source_ids: dict[str, list[str]] = {}

    for port in definition.input_ports:
        parameter_name = port.name
        for target in port.targets:
            add_binding(target.node_id, parameter_name, port.name, target.handle_id)

    for edge in ordered_edges:
        source_node = node_map[edge.source]
        add_binding(
            edge.target,
            _edge_input_name_for_codegen(edge, source_node),
            edge.source,
            edge.targetHandle,
        )

    for target_id, bindings in bindings_by_target.items():
        target_node = node_map[target_id]
        if target_node.data.nodeType == NodeType.EDGE_JOIN:
            base_index, join_index = resolve_edge_join_role_indices(
                [binding[2] for binding in bindings]
            )
            bindings = [bindings[base_index], bindings[join_index]]
        node_sources[target_id] = [binding[0] for binding in bindings]
        node_source_ids[target_id] = [binding[1] for binding in bindings]

    _validate_duplicate_node_inputs(node_sources, node_map)
    return node_sources, node_source_ids


def _require_routed_input_port(
    instance: ResolvedSubmodelInstance,
    edge: GraphEdge,
    port_name: str,
) -> None:
    """Reject a parent binding whose public input has no internal route.

    ``flatten_graph`` raises the same contextual error at expansion time, but
    codegen must not emit a parseable file for a graph that cannot execute:
    the connect call would name a port that binds nothing.
    """
    for port in instance.definition.input_ports:
        if port.name == port_name and not port.targets:
            raise ParseError(
                "Submodel input port bound by a parent edge has no internal targets.",
                edge_id=edge.id,
                instance_id=instance.node.id,
                definition_id=instance.config.definition_id,
                port_name=port_name,
            )


def _graph_to_code_multi_instances(
    graph: PipelineGraph,
    *,
    pipeline_name: str,
    description: str,
    preamble: str,
    source_file: str,
    preserved_blocks: list[str] | None,
) -> dict[str, str]:
    """Emit one canonical definition file and one registration per occurrence."""
    definitions = graph.submodels or {}
    instances = resolve_submodel_instances(graph)
    referenced_definition_ids = {instance.config.definition_id for instance in instances.values()}
    unused_definitions = sorted(set(definitions) - referenced_definition_ids)
    if unused_definitions:
        raise ParseError(
            "Submodel definition registry contains unreferenced definitions; "
            "saving would lose their parent-source registration.",
            definition_ids=unused_definitions,
        )

    definition_order: list[str] = []
    for node in graph.nodes:
        instance = instances.get(node.id)
        if instance is not None and instance.config.definition_id not in definition_order:
            definition_order.append(instance.config.definition_id)

    files_by_identity: dict[str, str] = {}
    for definition_id in definition_order:
        definition = definitions[definition_id]
        if not definition.file:
            raise ParseError(
                "Reusable submodel definition has no source file.",
                definition_id=definition_id,
            )
        normalised_file = definition.file.replace("\\", "/")
        file_identity = normalised_file.casefold()
        previous = files_by_identity.get(file_identity)
        if previous is not None and previous != definition_id:
            raise ParseError(
                "Distinct submodel definitions cannot share one source file.",
                file=normalised_file,
                definition_ids=[previous, definition_id],
            )
        files_by_identity[file_identity] = definition_id

    root_nodes = [node for node in graph.nodes if node.id not in instances]
    root_node_ids = {node.id for node in root_nodes}
    validate_graph_shape_contracts(graph, graph_label=pipeline_name)

    collision_labels = [node.data.label for node in root_nodes]
    collision_labels.extend(instance.config.alias for instance in instances.values())
    for definition_id in definition_order:
        collision_labels.extend(node.data.label for node in definitions[definition_id].graph.nodes)
    _error_on_name_collisions(collision_labels)

    files: dict[str, str] = {}
    for definition_id in definition_order:
        definition = definitions[definition_id]
        child_graph = definition.graph
        child_node_map = {node.id: node for node in child_graph.nodes}
        child_edges = _order_edge_join_incoming_edges(
            list(child_graph.edges),
            child_node_map,
        )
        sorted_child_nodes = _topo_sort(child_graph.nodes, child_edges)
        child_id_to_func = _build_id_to_func(sorted_child_nodes)
        child_node_sources, child_node_source_ids = _canonical_definition_source_metadata(
            definition,
            child_edges,
            child_node_map,
        )

        incoming_context: dict[str, list[str]] = {}
        for input_port in definition.input_ports:
            for target in input_port.targets:
                incoming_context.setdefault(target.node_id, []).append(f"public:{input_port.name}")
        outgoing_context: dict[str, list[str]] = {}
        for output_port in definition.output_ports:
            outgoing_context.setdefault(output_port.source.node_id, []).append(
                f"public:{output_port.name}"
            )
        validate_graph_shape_contracts(
            child_graph,
            graph_label=f"{pipeline_name}:{definition_id}",
            extra_incoming_by_node=incoming_context,
            extra_outgoing_by_node=outgoing_context,
        )

        child_connect_pairs = [
            (
                child_id_to_func[edge.source],
                child_id_to_func[edge.target],
                edge.sourceHandle or None,
                edge.targetHandle or None,
            )
            for edge in child_edges
        ]
        constructor_keywords: list[tuple[str, object]] = [
            ("definition_id", definition_id),
            (
                "input_ports",
                [
                    port.model_dump(mode="json", by_alias=True, exclude_none=False)
                    for port in definition.input_ports
                ],
            ),
            (
                "output_ports",
                [
                    port.model_dump(mode="json", by_alias=True, exclude_none=False)
                    for port in definition.output_ports
                ],
            ),
        ]
        # Its config= paths belong to the owning pipeline, which a standalone run
        # finds from this file through pipeline_dir.
        pipeline_dir = definition_pipeline_dir(definition.file)
        if pipeline_dir != ".":
            constructor_keywords.append(("pipeline_dir", pipeline_dir))
        files[definition.file.replace("\\", "/")] = _render_module(
            kind="submodel",
            name=child_graph.pipeline_name or definition_id,
            description=child_graph.pipeline_description or "",
            preamble=child_graph.preamble or "",
            functions=_node_functions(
                sorted_child_nodes,
                child_edges,
                id_to_func=child_id_to_func,
                node_sources=child_node_sources,
                node_source_ids=child_node_source_ids,
                receiver="submodel",
            ),
            connect_pairs=child_connect_pairs,
            preserved_blocks=child_graph.preserved_blocks or None,
            constructor_keywords=constructor_keywords,
        )

    node_map = {node.id: node for node in graph.nodes}
    for edge in graph.edges:
        for endpoint, node_id in (("source", edge.source), ("target", edge.target)):
            if node_id not in root_node_ids and node_id not in instances:
                raise ParseError(
                    "Pipeline edge references a node that is not part of the parent graph; "
                    "definition-owned child ids are never parent endpoints.",
                    edge_id=edge.id,
                    endpoint=endpoint,
                    node_id=node_id,
                )

    ordered_parent_edges = _order_edge_join_incoming_edges(list(graph.edges), node_map)
    sorted_root_nodes = _topo_sort(
        root_nodes,
        [
            edge
            for edge in ordered_parent_edges
            if edge.source in root_node_ids and edge.target in root_node_ids
        ],
    )
    root_id_to_func = _build_id_to_func(sorted_root_nodes)
    root_node_sources: dict[str, list[str]] = {}
    root_node_source_ids: dict[str, list[str]] = {}

    for edge in ordered_parent_edges:
        if edge.target not in root_node_ids:
            continue
        source_instance = instances.get(edge.source)
        if source_instance is None:
            source_node = node_map[edge.source]
            source_name = _edge_input_name_for_codegen(
                edge,
                source_node,
                submodels=graph.submodels,
            )
            source_id = edge.source
        else:
            source_identity = _edge_input_name_for_codegen(
                edge,
                source_instance.node,
                submodels=graph.submodels,
            )
            source_name = source_identity
            source_id = source_identity
        root_node_sources.setdefault(edge.target, []).append(source_name)
        root_node_source_ids.setdefault(edge.target, []).append(source_id)

    _validate_duplicate_node_inputs(root_node_sources, node_map)

    connect_pairs: list[_ConnectPair] = []
    for edge in ordered_parent_edges:
        source_instance = instances.get(edge.source)
        target_instance = instances.get(edge.target)
        source_func = (
            source_instance.config.alias
            if source_instance is not None
            else root_id_to_func[edge.source]
        )
        target_func = (
            target_instance.config.alias
            if target_instance is not None
            else root_id_to_func[edge.target]
        )
        source_port = (
            _canonical_port_name(
                edge.sourceHandle,
                prefix="out__",
                edge=edge,
                endpoint="source",
            )
            if source_instance is not None
            else edge.sourceHandle or None
        )
        target_port = (
            _canonical_port_name(
                edge.targetHandle,
                prefix="in__",
                edge=edge,
                endpoint="target",
            )
            if target_instance is not None
            else edge.targetHandle or None
        )
        if target_instance is not None and target_port is not None:
            _require_routed_input_port(target_instance, edge, target_port)
        connect_pairs.append((source_func, target_func, source_port, target_port))

    registrations: list[str] = []
    for node in graph.nodes:
        instance = instances.get(node.id)
        if instance is None:
            continue
        name = instance.config.alias
        if instance.config.instance_of is not None:
            owner_ref = instance.config.instance_of
            owner_instance = instances.get(owner_ref)
            if owner_instance is None:
                raise ParseError(
                    "Submodel instance references an owner occurrence that does not exist.",
                    instance_id=instance.node.id,
                    definition_id=instance.config.definition_id,
                    instance_of=owner_ref,
                )
            owner_name = owner_instance.config.alias
            registrations.append(
                _call(
                    "pipeline.submodel",
                    [
                        quote_string(instance.definition.file.replace("\\", "/")),
                        quote_string(name),
                        _keyword("instance_of", owner_name),
                    ],
                )
            )
        else:
            registrations.append(
                _call(
                    "pipeline.submodel",
                    [
                        quote_string(instance.definition.file.replace("\\", "/")),
                        quote_string(name),
                    ],
                )
            )
    root_edges = [
        edge
        for edge in ordered_parent_edges
        if edge.source in root_node_ids and edge.target in root_node_ids
    ]
    files[source_file or f"{pipeline_name}.py"] = _render_module(
        kind="pipeline",
        name=pipeline_name,
        description=description,
        preamble=preamble,
        functions=_node_functions(
            sorted_root_nodes,
            root_edges,
            id_to_func=root_id_to_func,
            node_sources=root_node_sources,
            node_source_ids=root_node_source_ids,
            receiver="pipeline",
        ),
        connect_pairs=connect_pairs,
        preserved_blocks=(
            preserved_blocks if preserved_blocks is not None else graph.preserved_blocks or None
        ),
        registrations=registrations,
        dedup_connects=True,
    )
    logger.info(
        "code_generated",
        pipeline_name=pipeline_name,
        node_count=len(sorted_root_nodes),
        submodel_definition_count=len(definition_order),
        submodel_occurrence_count=len(instances),
    )
    return _assert_emitted_files_parse(files)


def graph_to_code_multi(
    graph: PipelineGraph,
    pipeline_name: str = "main",
    description: str = "",
    preamble: str = "",
    source_file: str = "",
    preserved_blocks: list[str] | None = None,
) -> dict[str, str]:
    """Generate canonical Python source for a pipeline and its definitions."""
    if not description and graph.pipeline_description:
        description = graph.pipeline_description
    if not preamble and graph.preamble:
        preamble = graph.preamble

    has_occurrences = any(node.data.nodeType == NodeType.SUBMODEL for node in graph.nodes)
    if graph.submodels or has_occurrences:
        return _graph_to_code_multi_instances(
            graph,
            pipeline_name=pipeline_name,
            description=description,
            preamble=preamble,
            source_file=source_file,
            preserved_blocks=preserved_blocks,
        )

    validate_pipeline_graph_shape_contracts(graph, graph_label=pipeline_name)
    _error_on_name_collisions([node.data.label for node in graph.nodes])

    main_key = source_file or f"{pipeline_name}.py"
    node_map = {node.id: node for node in graph.nodes}
    edges = _order_edge_join_incoming_edges(graph.edges, node_map)
    sorted_nodes = _topo_sort(graph.nodes, edges)
    id_to_func = _build_id_to_func(sorted_nodes)
    node_sources, node_source_ids = _build_node_input_metadata(edges, node_map)
    _validate_duplicate_node_inputs(node_sources, node_map)
    connect_pairs = [
        (
            id_to_func.get(edge.source, edge.source),
            id_to_func.get(edge.target, edge.target),
            edge.sourceHandle or None,
            edge.targetHandle or None,
        )
        for edge in edges
    ]
    all_preserved = preserved_blocks if preserved_blocks is not None else graph.preserved_blocks
    code = _render_module(
        kind="pipeline",
        name=pipeline_name,
        description=description,
        preamble=preamble,
        functions=_node_functions(
            sorted_nodes,
            edges,
            id_to_func=id_to_func,
            node_sources=node_sources,
            node_source_ids=node_source_ids,
            receiver="pipeline",
        ),
        connect_pairs=connect_pairs,
        preserved_blocks=all_preserved or None,
    )
    logger.info(
        "code_generated",
        pipeline_name=pipeline_name,
        node_count=len(sorted_nodes),
    )
    return _assert_emitted_files_parse({main_key: code})
