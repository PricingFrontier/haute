"""Code generator orchestration: graph JSON -> valid pipeline .py file.

The per-type ``_gen_*`` builders and the codegen-side registry live in
:mod:`haute._codegen_builders`.  This module keeps the graph-level
assembly logic (``graph_to_code`` / ``graph_to_code_multi``) and the
single-node dispatcher that drives the unified
:data:`haute._registry.NODE_REGISTRY`.
"""

from __future__ import annotations

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
from haute._graph_shape import validate_pipeline_graph_shape_contracts
from haute._graph_utils import _sanitize_func_name, build_instance_mapping
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
    treat *infrastructure* failures (MLflow unreachable, artifact
    missing) as "opaque at codegen time" rather than propagating: the
    purpose of the kwarg is documentation at the source-file level, and
    the executor still re-computes + enforces the contract at runtime
    from the actual model.  Forcing a running MLflow server just to
    save a pipeline would be a regression.

    Misconfiguration errors (``ConfigError``), however, MUST fail loud
    at save time — emitting ``contract="opaque"`` for a node whose
    ``sourceType="run"`` lacks a ``run_id`` would hide the real user
    error inside a file that silently runs, then blow up at execution
    far from the broken config.
    """
    config = node.data.config
    declared_raw = config.get("contract")
    if config.get("instanceOf") and declared_raw is None:
        return None
    if declared_raw is not None:
        declared = Contract.from_user_declared(declared_raw)
        if declared is not None:
            return _format_contract_source(declared, parent_name_by_id=parent_name_by_id)
    try:
        tup = get_column_contract(node.data.nodeType, config)
    except ConfigError:
        # Misconfiguration is a user bug, not an environmental one — let
        # it propagate so save fails at the source of the mistake.
        raise
    except Exception as exc:
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
        for parent_id, columns in sorted(contract.inputs_by_parent.items()):
            emitted_parent = parent_id
            if parent_name_by_id is not None:
                if parent_id in parent_name_by_id:
                    emitted_parent = parent_name_by_id[parent_id]
                elif parent_id in parent_names:
                    emitted_parent = parent_id
                else:
                    raise ParseError(
                        "inputs_by_parent references a parent that is not connected to this node.",
                        parent_id=parent_id,
                        connected_parent_ids=sorted(parent_name_by_id),
                        connected_parent_names=sorted(parent_names),
                    )
            inputs_by_parent[emitted_parent] = None if columns is None else sorted(columns)
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


def _inject_contract_kwarg(code: str, contract_kwarg: str) -> str:
    """Insert *contract_kwarg* into the first ``@pipeline.<type>(...)`` call.

    Finds the opening ``(`` of the decorator argument list on the first
    decorator line and inserts the kwarg just before the closing ``)``.
    Preserves any existing kwargs.

    Decorators without parentheses (``@pipeline.polars``) are rewritten
    to ``@pipeline.polars(contract=...)`` so the kwarg survives.
    """
    lines = code.splitlines()
    for i, line in enumerate(lines):
        stripped = line.lstrip()
        if not stripped.startswith("@pipeline.") and not stripped.startswith("@submodel."):
            continue

        if "(" not in stripped:
            # Bare decorator like "@pipeline.polars" — add parens + kwarg.
            lines[i] = line.rstrip() + f"({contract_kwarg})"
            return "\n".join(lines) + ("\n" if code.endswith("\n") else "")

        # Decorator with args — find the matching closing paren.  This
        # might span multiple lines (e.g. factors=[{...}]).  Track depth
        # from the opening paren forward until depth drops back to zero.
        open_idx = stripped.index("(")
        leading = len(line) - len(stripped)
        depth = 0
        end_line = i
        end_col: int | None = None
        for j in range(i, len(lines)):
            scan_line = lines[j]
            start_col = leading + open_idx + 1 if j == i else 0
            for col in range(start_col, len(scan_line)):
                ch = scan_line[col]
                if ch == "(":
                    depth += 1
                elif ch == ")":
                    if depth == 0:
                        end_line = j
                        end_col = col
                        break
                    depth -= 1
            if end_col is not None:
                break

        if end_col is None:
            # Reaching this branch means the decorator's argument list
            # has no matching closing paren.  That's a codegen bug —
            # the emitted file would be missing its contract kwarg and
            # the downstream parser check would catch it much later
            # with a confusing "contract mismatch" error.  Fail loud
            # here so the real cause is obvious.
            raise HauteError(
                "contract injection failed: decorator argument list has no "
                "matching closing paren — this is a codegen bug",
                reason="no_closing_paren",
            )

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
        result = "\n".join(lines)
        if code.endswith("\n") and not result.endswith("\n"):
            result += "\n"
        return result

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


def _optimiser_apply_ratebook_return_source(
    node: GraphNode,
    source_names: list[str],
    source_ids: list[str],
) -> str | None:
    """Resolve the configured ratebook input node id to a Python parameter name."""
    if node.data.nodeType != NodeType.OPTIMISER_APPLY:
        return None

    ratebook_input = node.data.config.get("ratebook_input")
    if not ratebook_input or not source_ids:
        return None
    optimiser_mode = node.data.config.get("optimiser_mode")
    if optimiser_mode == "online":
        return None
    if node.data.config.get("sourceType") in {"run", "registered"} and optimiser_mode != "ratebook":
        # MLflow source whose artifact mode has not yet been resolved by the
        # picker.  Skip wiring rather than emit code that may be wrong; the
        # ``ratebook_input`` value is preserved in config so the next codegen
        # pass picks it up once the picker resolves ``optimiser_mode``.
        return None

    if ratebook_input not in source_ids:
        raise ParseError(
            "optimiserApply ratebook_input does not match any connected input node id.",
            node_id=node.id,
            node_label=node.data.label,
            ratebook_input=ratebook_input,
            connected_input_node_ids=source_ids,
        )

    index = source_ids.index(ratebook_input)
    if index >= len(source_names):
        raise ParseError(
            "optimiserApply ratebook_input resolved outside the generated source list.",
            node_id=node.id,
            node_label=node.data.label,
            ratebook_input=ratebook_input,
            source_index=index,
            source_names=source_names,
        )
    return source_names[index]


def _rewrite_single_return_source(code: str, source_name: str) -> str:
    """Rewrite the single passthrough return line produced for optimiserApply."""
    lines = code.splitlines()
    return_lines = [
        idx
        for idx, line in enumerate(lines)
        if line.startswith("    return ") and not line.startswith("    return pl.")
    ]
    if len(return_lines) != 1:
        raise HauteError(
            "optimiserApply codegen expected exactly one passthrough return line.",
            return_line_count=len(return_lines),
        )
    lines[return_lines[0]] = f"    return {source_name}"
    result = "\n".join(lines)
    if code.endswith("\n"):
        result += "\n"
    return result


def _node_to_code(
    node: GraphNode,
    source_names: list[str] | None = None,
    source_ids: list[str] | None = None,
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

    code = _generate_node_code(node, source_names)
    ratebook_return_source = _optimiser_apply_ratebook_return_source(
        node,
        source_names,
        source_ids,
    )
    if ratebook_return_source is not None:
        code = _rewrite_single_return_source(code, ratebook_return_source)

    node_type = node.data.nodeType
    if has_config_folder(node_type):
        func_name = _sanitize_func_name(node.data.label)
        cfg_path = config_path_for_node(node_type, func_name).as_posix()
        try:
            dec_name = NODE_TYPE_TO_DECORATOR.get(node_type, "polars")
            def_idx = code.index("\ndef ")
            code = f"@pipeline.{dec_name}(config={_safe_path(cfg_path)})" + code[def_idx:]
        except ValueError:
            logger.warning("no_def_in_generated_code", node=node.data.label)

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

    Haute has no deployed user base that needs a migration path, so a
    collision is treated as a hard error.  It is a silent user-data-loss
    bug: codegen emits two ``def <name>(...)`` blocks and the second
    shadows the first at import time.  Failing at codegen time prevents
    corrupting the pipeline on disk.

    Pass a flat list of every label that will ultimately become a
    function name in any emitted file (root graph + every submodel).
    The raised :class:`ParseError` enumerates every colliding bucket
    so the user can fix them all in one editing pass.
    """
    buckets: dict[str, list[str]] = {}
    for label in labels:
        sanitized = _sanitize_func_name(label)
        buckets.setdefault(sanitized, []).append(label)

    # Only flag *distinct* source labels colliding — two graph nodes
    # with the exact same label are a different (legal) case that the
    # executor handles elsewhere.
    collisions = {
        sanitized: sorted(set(originals))
        for sanitized, originals in buckets.items()
        if len(set(originals)) > 1
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
        "Rename the offending nodes so each label produces a unique "
        "identifier:\n"
        f"{bullets}",
        collisions={k: list(v) for k, v in collisions.items()},
    )


def _build_node_sources(
    edges: list[GraphEdge],
    id_to_func: dict[str, str],
) -> dict[str, list[str]]:
    """Map target node ID -> list of source function names."""
    sources: dict[str, list[str]] = {}
    for edge in edges:
        src_name = id_to_func.get(edge.source, edge.source)
        sources.setdefault(edge.target, []).append(src_name)
    return sources


def _build_node_source_ids(edges: list[GraphEdge]) -> dict[str, list[str]]:
    """Map target node ID -> list of source node IDs in edge order."""
    sources: dict[str, list[str]] = {}
    for edge in edges:
        sources.setdefault(edge.target, []).append(edge.source)
    return sources


def _build_instance_of_map(sorted_nodes: list[GraphNode]) -> dict[str, str]:
    """Map instance node ID -> original node ID for nodes with ``instanceOf``."""
    result: dict[str, str] = {}
    for node in sorted_nodes:
        ref = node.data.config.get("instanceOf")
        if ref:
            result[node.id] = ref
    return result


#: Type alias for a function that generates code for a single node.
_NodeCodeFn = Callable[[GraphNode, list[str] | None, list[str] | None], str]


def _generate_pipeline_lines(
    *,
    kind: str,
    name: str,
    description: str,
    preamble: str,
    sorted_nodes: list[GraphNode],
    id_to_func: dict[str, str],
    node_sources: dict[str, list[str]],
    connect_pairs: list[tuple[str, str, str | None]],
    node_source_ids: dict[str, list[str]] | None = None,
    preserved_blocks: list[str] | None = None,
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
    if kind == "submodel":
        lines = [
            f'"""Submodel: {name.replace(chr(34), "")}"""',
            "",
            "import polars as pl",
            "import haute",
            "",
            "",
            f"{obj_name} = haute.Submodel({_safe_str(name)})",
            "",
            "",
        ]
    else:
        lines = [
            f'"""Pipeline: {name}"""',
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
        lines.append(node_to_code_fn(node, srcs, src_ids))
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
    # string), emit the multi-port form
    # `pipeline.connect("a", "b", source_port="p")`. Otherwise emit the
    # single-port bare form. Per MULTI_FRAME_PLAN.md §6.
    #
    # Use ``json.dumps`` for the port literal so user-controlled labels
    # containing quotes / backslashes / non-ASCII characters survive
    # round-trip without producing invalid Python (the adversarial
    # review's C2 finding — bare f-string interpolation breaks on
    # ``label = 'a"b'``). Func names are codegen-derived sanitised
    # identifiers so the bare-string form is safe for those.
    import json as _json

    def _format_connect(src_func: str, tgt_func: str, port: str | None) -> str:
        if port:
            return (
                f"{obj_name}.connect("
                f'"{src_func}", "{tgt_func}", '
                f"source_port={_json.dumps(port)})"
            )
        return f'{obj_name}.connect("{src_func}", "{tgt_func}")'

    if connect_pairs:
        lines.append("")
        lines.append("# Wire nodes together - edges define data flow")
        if dedup_connects:
            seen: set[tuple[str, str, str | None]] = set()
            for src_func, tgt_func, port in connect_pairs:
                key = (src_func, tgt_func, port)
                if key not in seen:
                    seen.add(key)
                    lines.append(_format_connect(src_func, tgt_func, port))
        else:
            for src_func, tgt_func, port in connect_pairs:
                lines.append(_format_connect(src_func, tgt_func, port))
        lines.append("")

    return lines


# ---------------------------------------------------------------------------
# Public orchestration API
# ---------------------------------------------------------------------------


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
    # graph_to_code_multi returns {filename: code}; extract the sole value.
    return next(iter(files.values()))


def _submodel_node_to_code(
    node: GraphNode,
    source_names: list[str] | None = None,
    source_ids: list[str] | None = None,
) -> str:
    """Generate code for a single node inside a submodel file.

    Identical to ``_node_to_code`` but uses ``@submodel.<type>`` instead of
    ``@pipeline.<type>``.
    """
    code = _node_to_code(node, source_names=source_names, source_ids=source_ids)
    return code.replace("@pipeline.", "@submodel.", 1)


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
    submodels = graph.submodels or {}
    validate_pipeline_graph_shape_contracts(graph, graph_label=pipeline_name)

    # Detect colliding labels across the whole graph once, eagerly, so
    # duplicate-function-name collisions are reported even when the
    # file-generation path short-circuits.
    collision_labels: list[str] = [n.data.label for n in graph.nodes]
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
        edges = graph.edges
        sorted_nodes = _topo_sort(nodes, edges)

        id_to_func = _build_id_to_func(sorted_nodes)
        node_sources = _build_node_sources(edges, id_to_func)
        node_source_ids = _build_node_source_ids(edges)

        all_preserved = preserved_blocks if preserved_blocks is not None else graph.preserved_blocks

        # Build connect pairs from edges. Each pair is
        # (src_func, tgt_func, source_port) where source_port is the
        # edge's `sourceHandle` if set, otherwise None (single-port).
        connect_pairs = [
            (
                id_to_func.get(e.source, e.source),
                id_to_func.get(e.target, e.target),
                e.sourceHandle or None,
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
            node_source_ids=node_source_ids,
            preserved_blocks=all_preserved or None,
            node_to_code_fn=_node_to_code,
        )

        logger.info("code_generated", pipeline_name=pipeline_name, node_count=len(sorted_nodes))
        return {main_key: "\n".join(lines)}

    # Separate nodes into root-level vs submodel children ----------------
    all_child_ids: set[str] = set()
    submodel_node_ids: set[str] = set()
    submodel_child_ids: dict[str, set[str]] = {}
    for sm_name, sm_meta in submodels.items():
        child_ids = set(sm_meta.get("childNodeIds", []))
        all_child_ids.update(child_ids)
        submodel_node_id = f"submodel__{sm_name}"
        submodel_node_ids.add(submodel_node_id)
        submodel_child_ids[submodel_node_id] = child_ids

    nodes = graph.nodes
    edges = graph.edges

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

        sorted_sm_nodes = _topo_sort(sm_nodes, sm_edges)
        sm_id_to_func = _build_id_to_func(sorted_sm_nodes)
        sm_node_sources = _build_node_sources(sm_edges, sm_id_to_func)
        sm_node_source_ids = _build_node_source_ids(sm_edges)

        # Also include cross-boundary inputs from parent graph edges.
        # Every edge targeting the submodel placeholder must carry a valid
        # ``in__<child_id>`` handle — anything else indicates a malformed
        # edge and we fail loudly rather than silently dropping it.
        sm_node_id = f"submodel__{sm_name}"
        sm_child_ids = {n.id for n in sm_nodes}
        submodel_child_ids[sm_node_id] = sm_child_ids
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
            src_name = root_id_to_func.get(edge.source, _sanitize_func_name(edge.source))
            existing = sm_node_sources.setdefault(child_id, [])
            existing_ids = sm_node_source_ids.setdefault(child_id, [])
            if src_name not in existing:
                existing.append(src_name)
                existing_ids.append(edge.source)
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
        # submodel-internal multi-port edges (if any) survive a save.
        sm_connect_pairs = [
            (
                sm_id_to_func.get(e.source, e.source),
                sm_id_to_func.get(e.target, e.target),
                e.sourceHandle or None,
            )
            for e in sm_edges
        ]

        sm_lines = _generate_pipeline_lines(
            kind="submodel",
            name=sm_name,
            description="",
            preamble="",
            sorted_nodes=sorted_sm_nodes,
            id_to_func=sm_id_to_func,
            node_sources=sm_node_sources,
            connect_pairs=sm_connect_pairs,
            node_source_ids=sm_node_source_ids,
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

    # Build source names per root node from root-level edges AND
    # cross-boundary edges (resolving submodel handles to child node names).
    root_node_sources: dict[str, list[str]] = {}
    root_node_source_ids: dict[str, list[str]] = {}
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
        src_name = root_id_to_func.get(actual_src, _sanitize_func_name(actual_src))
        root_node_sources.setdefault(actual_tgt, []).append(src_name)
        root_node_source_ids.setdefault(actual_tgt, []).append(edge.source)

    # Build connect pairs for ALL edges (cross-boundary use real node names).
    # Triple shape: (src_func, tgt_func, source_port).
    # When the edge originates at a submodel boundary, the sourceHandle
    # carries the `out__<child_id>` marker (resolved above into the
    # child's func name) — no user-facing port name to forward, so the
    # third element is None. For non-boundary edges, the edge's
    # sourceHandle is the user-facing port string (or None for
    # single-port).
    root_connect_pairs: list[tuple[str, str, str | None]] = []
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
        # Submodel-boundary `out__<id>` handles aren't user-facing port
        # names; only forward sourceHandle as source_port when it's not
        # a submodel-boundary edge (i.e. the source isn't a submodel
        # placeholder node). Per the adversarial review's S1: gating on
        # the prefix alone would also silently drop a regular apiInput
        # table labelled e.g. "out__claims".
        is_submodel_boundary = src in submodel_node_ids
        source_port = (
            edge.sourceHandle if edge.sourceHandle and not is_submodel_boundary else None
        )
        root_connect_pairs.append((src_func, tgt_func, source_port))

    # Submodel import lines
    sm_imports = []
    for sm_name, sm_meta in submodels.items():
        sm_path = sm_meta.get("file", f"modules/{sm_name}.py").replace("\\", "/")
        sm_imports.append(f'pipeline.submodel("{sm_path}")')

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
        node_source_ids=root_node_source_ids,
        preserved_blocks=all_preserved or None,
        submodel_imports=sm_imports,
        node_to_code_fn=_node_to_code,
        dedup_connects=True,
    )

    main_key = source_file or f"{pipeline_name}.py"
    files[main_key] = "\n".join(main_lines)
    return files
