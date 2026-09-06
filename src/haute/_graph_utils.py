"""Pure-function graph utilities decoupled from Pydantic model definitions.

These helpers operate on graph data (edges, labels, node references) but
do not need to live alongside the Pydantic models themselves.  Keeping
them here avoids bloating ``_types.py`` with implementation logic and
lets callers import utilities without pulling the full model module.
"""

from __future__ import annotations

from collections.abc import Mapping
from hashlib import blake2b
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from haute._types import GraphEdge, GraphNode, PipelineGraph


def build_parents_of(
    edges: list[GraphEdge],
    node_ids: set[str] | None = None,
) -> dict[str, list[str]]:
    """Build reverse adjacency list: node_id -> list of parent node_ids."""
    parents: dict[str, list[str]] = {nid: [] for nid in node_ids} if node_ids else {}
    for e in edges:
        if node_ids is None or e.target in parents:
            parents.setdefault(e.target, []).append(e.source)
    return parents


def upstream_node_ids(
    node_id: str,
    parents_of: Mapping[str, list[str]],
) -> list[str]:
    """Return all upstream node ids for *node_id*, nearest parents first."""
    result: list[str] = []
    seen: set[str] = set()
    stack = list(parents_of.get(node_id, []))
    while stack:
        current = stack.pop(0)
        if current in seen:
            continue
        seen.add(current)
        result.append(current)
        stack[0:0] = parents_of.get(current, [])
    return result


def _edge_id(
    source: str,
    target: str,
    source_port: str | None = None,
    target_port: str | None = None,
    *,
    hidden_source_port: str | None = None,
    hidden_target_port: str | None = None,
) -> str:
    """Return a stable React Flow edge id for all persisted port metadata.

    The common four-field form retains its historical IDs. Boundary edges can
    additionally carry authored ports in ``sourcePort`` / ``targetPort`` while
    ``sourceHandle`` / ``targetHandle`` hold synthetic submodel handles; those
    hidden fields participate in the identity whenever present.
    """
    has_hidden_ports = hidden_source_port is not None or hidden_target_port is not None
    if source_port is None and target_port is None and not has_hidden_ports:
        return f"e_{source}_{target}"
    if has_hidden_ports:
        payload = "\0".join(
            (
                source,
                target,
                source_port or "",
                target_port or "",
                hidden_source_port or "",
                hidden_target_port or "",
                "boundary",
            )
        ).encode()
    elif target_port is None:
        payload = "\0".join((source, target, source_port or "")).encode()
    else:
        payload = "\0".join((source, target, source_port or "", target_port)).encode()
    digest = blake2b(payload, digest_size=6).hexdigest()
    return f"e_{source}_{target}_{digest}"


def _sanitize_func_name(label: str) -> str:
    """Convert a human label to a valid Python function name (preserves casing).

    ASCII alnum / ``_`` survive unchanged.  Spaces and hyphens become
    underscores.  Every other character — punctuation, whitespace, and
    non-ASCII glyphs — is either dropped (ASCII punctuation, control
    characters) or encoded as ``_x<hex>_`` for non-ASCII. Because spaces and
    hyphens converge and ASCII punctuation is dropped, callers that admit
    multiple labels must still reject output collisions.

    Invariants (see tests/test_parser_sanitize_contracts.py):

    - ASCII inputs are unchanged from pre-fix behaviour.
    - Output is always a valid Python identifier (``str.isidentifier()``).
    - Non-ASCII code points remain distinguishable from ASCII text.
    - Idempotent: ``sanitize(sanitize(x)) == sanitize(x)``.  The encoded
      form ``_x<hex>_`` is itself all-ASCII alnum/underscore, so a second
      pass is a no-op.

    This is backend-owned executable identity; browser ``portableKey`` is not
    a Python-compatible twin.
    """
    import keyword

    name = _sanitize_identifier_characters(label)

    if name and name[0].isdigit():
        name = f"node_{name}"
    if keyword.iskeyword(name):
        name = f"node_{name}"
    return name or "unnamed_node"


def _sanitize_identifier_characters(label: str) -> str:
    """Apply the shared label-to-ASCII identifier character pipeline.

    This deliberately does not apply the function-name-specific empty,
    digit-leading, or keyword repairs.  Schema inference uses the same
    character mapping with frame-label repairs of its own.
    """
    name = label.strip()
    name = name.replace(" ", "_").replace("-", "_")

    out_chars: list[str] = []
    for c in name:
        if c.isascii():
            if c.isalnum() or c == "_":
                out_chars.append(c)
            # Drop ASCII punctuation and control characters.
        else:
            # Reversibly encode non-ASCII codepoints. The encoded form is
            # itself ASCII alphanumeric/underscore and therefore stable.
            out_chars.append(f"_x{ord(c):x}_")
    return "".join(out_chars)


def submodel_output_label(
    source_node: GraphNode,
    source_handle: str | None,
    submodels: Mapping[str, Any] | None,
) -> str:
    """Resolve one collapsed occurrence handle to its public output label."""
    prefix = "out__"
    if source_handle is None or not source_handle.startswith(prefix) or source_handle == prefix:
        raise ValueError("Submodel output handles must use the canonical 'out__<port_id>' form.")

    definition_id = source_node.data.config.get("definitionId")
    if not isinstance(definition_id, str) or not definition_id:
        raise ValueError(f"Submodel node {source_node.id!r} has no canonical definition identity.")
    if submodels is None:
        raise ValueError(
            f"Submodel node {source_node.id!r} requires its definition registry "
            "to resolve public output labels."
        )
    definition = submodels.get(definition_id)
    if definition is None:
        raise ValueError(
            f"Submodel node {source_node.id!r} references missing definition {definition_id!r}."
        )

    raw_ports = (
        definition.get("outputPorts")
        if isinstance(definition, Mapping)
        else getattr(definition, "output_ports", None)
    )
    if not isinstance(raw_ports, list):
        raise ValueError(f"Submodel definition {definition_id!r} has malformed output ports.")
    port_id = source_handle[len(prefix) :]
    for port in raw_ports:
        candidate_id = (
            port.get("portId") if isinstance(port, Mapping) else getattr(port, "port_id", None)
        )
        if candidate_id != port_id:
            continue
        label = port.get("label") if isinstance(port, Mapping) else getattr(port, "label", None)
        if not isinstance(label, str) or not label:
            raise ValueError(
                f"Submodel output port {port_id!r} in definition {definition_id!r} "
                "has no public label."
            )
        return label
    raise ValueError(
        f"Submodel output handle {source_handle!r} is not declared by definition {definition_id!r}."
    )


def submodel_output_port_count(
    source_node: GraphNode,
    submodels: Mapping[str, Any] | None,
) -> int:
    """Return the declared output-port count for a submodel occurrence."""
    definition_id = source_node.data.config.get("definitionId")
    if not isinstance(definition_id, str) or not definition_id:
        raise ValueError(f"Submodel node {source_node.id!r} has no canonical definition identity.")
    if submodels is None:
        raise ValueError(
            f"Submodel node {source_node.id!r} requires its definition registry "
            "to resolve public output labels."
        )
    definition = submodels.get(definition_id)
    if definition is None:
        raise ValueError(
            f"Submodel node {source_node.id!r} references missing definition {definition_id!r}."
        )

    raw_ports = (
        definition.get("outputPorts")
        if isinstance(definition, Mapping)
        else getattr(definition, "output_ports", None)
    )
    if not isinstance(raw_ports, list):
        raise ValueError(f"Submodel definition {definition_id!r} has malformed output ports.")
    return len(raw_ports)


def edge_input_label(
    edge: GraphEdge,
    source_node: GraphNode,
    *,
    submodels: Mapping[str, Any] | None = None,
) -> str:
    """Return the semantic frame label contributed by one incoming edge."""
    node_type = str(source_node.data.nodeType)
    if node_type == "apiInput":
        if edge.sourceHandle is None:
            raise ValueError(f"apiInput edge {edge.id!r} has no sourceHandle/frame label")
        return edge.sourceHandle
    if node_type == "submodel":
        return submodel_output_label(source_node, edge.sourceHandle, submodels)
    return source_node.data.label


def edge_input_name(
    edge: GraphEdge,
    source_node: GraphNode,
    *,
    submodels: Mapping[str, Any] | None = None,
) -> str:
    """Return the one input name contributed by an incoming edge.

    API-input edges use their persisted frame handle verbatim. Submodel
    outputs use the occurrence's own name (or <alias>__<port_id> when
    the referenced definition declares more than one output port). Every
    ordinary edge uses the sanitised source-node label; source handles on ordinary
    nodes identify an output port and are not input names.
    """
    node_type = str(source_node.data.nodeType)
    input_label = edge_input_label(edge, source_node, submodels=submodels)
    alias = source_node.data.config.get("alias") if node_type == "submodel" else None
    output_port_count = (
        submodel_output_port_count(source_node, submodels) if node_type == "submodel" else None
    )
    return executable_input_name(
        node_type=source_node.data.nodeType,
        label=source_node.data.label,
        source_handle=edge.sourceHandle,
        source_handle_label=input_label if node_type == "submodel" else None,
        alias=alias,
        output_port_count=output_port_count,
    )


def incoming_edge_bindings(
    graph: PipelineGraph,
    node_id: str,
) -> list[tuple[GraphEdge, str]]:
    """Return each incoming physical edge with its executable input name.

    The edge, rather than its source node, is the identity boundary: two API
    frames can arrive from the same source node and remain distinct inputs.
    Missing endpoints and malformed frame handles fail at the shared
    :func:`edge_input_name` derivation instead of being translated to node ids.
    """
    node_map = graph.node_map
    bindings: list[tuple[GraphEdge, str]] = []
    for edge in graph.edges:
        if edge.target != node_id:
            continue
        source_node = node_map.get(edge.source)
        if source_node is None:
            raise ValueError(
                f"Incoming edge {edge.id!r} references missing source node {edge.source!r}."
            )
        bindings.append(
            (
                edge,
                edge_input_name(edge, source_node, submodels=graph.submodels),
            )
        )
    return bindings


def incoming_edge_for_name(
    graph: PipelineGraph,
    node_id: str,
    input_name: str,
) -> GraphEdge:
    """Resolve one exact executable input name to its physical incoming edge."""
    bindings = incoming_edge_bindings(graph, node_id)
    matches = [edge for edge, name in bindings if name == input_name]
    if len(matches) != 1:
        available = [name for _edge, name in bindings]
        if not matches:
            raise ValueError(
                f"Input {input_name!r} is not connected to node {node_id!r}; "
                f"available inputs: {available!r}."
            )
        raise ValueError(
            f"Input {input_name!r} is ambiguous for node {node_id!r}; "
            f"matching edge ids: {[edge.id for edge in matches]!r}."
        )
    return matches[0]


def select_edge_source_output(source_output: Any, edge: GraphEdge) -> Any:
    """Select the frame delivered by one physical edge from a source output."""
    if not isinstance(source_output, dict):
        return source_output
    if not source_output:
        raise RuntimeError(
            f"Source node {edge.source!r} emitted no frames. Check the node's "
            "configuration: at least one emit-true table with selected columns "
            "is required for a multi-frame apiInput."
        )
    source_handle = edge.sourceHandle
    if source_handle is None:
        raise ValueError(
            f"Edge from multi-frame node {edge.source!r} has no sourceHandle. "
            f"Expected one of: {sorted(source_output)}."
        )
    if source_handle not in source_output:
        raise KeyError(
            f"Edge from {edge.source!r} references frame {source_handle!r}, "
            f"but the source emits: {sorted(source_output)}."
        )
    return source_output[source_handle]


def executable_input_name(
    *,
    node_type: object,
    label: str,
    source_handle: str | None,
    source_handle_label: str | None = None,
    alias: str | None = None,
    output_port_count: int | None = None,
) -> str:
    """Derive one executable input identity without mutating graph data.

    Submodel outputs derive their name from the occurrence's alias
    (or ``f"{_sanitize_func_name(alias)}__{port_id}"`` when the definition
    declares more than one output port). Public submodel input ports derive
    their name from the sanitised port ID (``source_handle``).
    """
    kind = str(node_type)
    if kind == "apiInput":
        if source_handle is None:
            raise ValueError("API input handles are required for executable identities.")
        return source_handle
    if kind == "submodel":
        prefix = "out__"
        if (
            source_handle is None
            or not source_handle.startswith(prefix)
            or len(source_handle) == len(prefix)
        ):
            raise ValueError(
                "Submodel output handles must use the canonical 'out__<port_id>' form."
            )
        if not isinstance(alias, str) or not alias:
            raise ValueError(f"Submodel node {label!r} requires an occurrence alias.")
        if output_port_count is None or output_port_count < 1:
            raise ValueError(f"Submodel node {label!r} requires an output port count.")
        sanitized_alias = _sanitize_func_name(alias)
        if output_port_count > 1:
            port_id = _sanitize_func_name(source_handle[len(prefix) :])
            return f"{sanitized_alias}__{port_id}"
        return sanitized_alias
    if kind == "submodelPort":
        if source_handle is None or not isinstance(source_handle, str) or not source_handle:
            raise ValueError("Submodel input identities require a source handle.")
        return _sanitize_func_name(source_handle)
    return _sanitize_func_name(label)


def duplicate_input_names(names: list[str]) -> list[str]:
    """Return repeated names once, ordered by their first duplicate occurrence."""
    seen: set[str] = set()
    reported: set[str] = set()
    duplicates: list[str] = []
    for name in names:
        if name in seen:
            if name not in reported:
                reported.add(name)
                duplicates.append(name)
        else:
            seen.add(name)
    return duplicates


def build_instance_mapping(
    orig_names: list[str],
    inst_names: list[str],
    explicit: dict[str, str] | None = None,
) -> dict[str, str]:
    """Map original input parameter names to instance input names.

    Priority: explicit mapping → exact name match → unambiguous substring
    match → positional. Used by the executor (alias injection) and codegen
    (kwarg generation). The frontend's InstanceConfig auto-mapping consumes
    authoritative identity metadata.

    Raises
    ------
    ConfigError
        If *explicit* contains stale entries — keys that do not appear in
        *orig_names* or non-empty values that do not appear in *inst_names*.
        Stale entries indicate the UI-stored ``inputMapping`` is out of
        sync with the current graph and would silently corrupt wiring.
    ConfigError
        If a substring pairing is ambiguous — an original name matching
        several instance sources, or one instance source matching several
        originals. The old greedy first-fit resolved these silently and
        could bind frames to the WRONG parameters (swapped inputs, clean
        run, wrong prices); ambiguity now requires an explicit
        ``inputMapping`` on the instance node.
    """
    from haute.errors import ConfigError

    mapping: dict[str, str] = {}
    if explicit:
        orig_set = set(orig_names)
        inst_set = set(inst_names)
        stale_keys = [k for k in explicit if k not in orig_set]
        stale_values = [(k, v) for k, v in explicit.items() if v and v not in inst_set]
        if stale_keys or stale_values:
            raise ConfigError(
                "inputMapping contains stale entries that no longer match the "
                "current graph; remove or update them in the node config.",
                stale_keys=stale_keys,
                stale_values=stale_values,
                orig_names=list(orig_names),
                inst_names=list(inst_names),
            )
        mapping = {k: v for k, v in explicit.items() if v}

    used: set[int] = set()
    for v in mapping.values():
        for i, inst in enumerate(inst_names):
            if inst == v and i not in used:
                used.add(i)
                break
    # Pass 1: exact match
    for orig in orig_names:
        if orig in mapping:
            continue
        for i, inst in enumerate(inst_names):
            if i not in used and inst == orig:
                mapping[orig] = inst
                used.add(i)
                break
    # Pass 2: substring match (e.g. "claims_aggregate" in
    # "claims_aggregate_instance") — assigned ONLY when the pairing is
    # unambiguous in both directions. Substring containment is
    # many-to-one: with originals ``rate``/``base_rate`` and instance
    # sources ``x_base_rate``/``x_rate``, the old greedy first-fit gave
    # ``rate`` → ``x_base_rate`` and left ``base_rate`` to pick up
    # ``x_rate`` positionally — the two frames bound CROSSWISE, the
    # pipeline ran clean and priced wrong. A contested pairing now
    # raises so the user sets ``inputMapping`` explicitly instead of
    # receiving silently-swapped frames.
    remaining_origs = [o for o in orig_names if o not in mapping]
    remaining_idx = [i for i in range(len(inst_names)) if i not in used]
    cand_by_orig = {o: [i for i in remaining_idx if o in inst_names[i]] for o in remaining_origs}
    cand_by_idx = {i: [o for o in remaining_origs if o in inst_names[i]] for i in remaining_idx}
    ambiguous = {
        o: [inst_names[i] for i in cands]
        for o, cands in cand_by_orig.items()
        if cands and not (len(cands) == 1 and len(cand_by_idx[cands[0]]) == 1)
    }
    if ambiguous:
        raise ConfigError(
            "instance input mapping is ambiguous: name matching cannot "
            "uniquely pair these original inputs with the instance's "
            "upstream sources — set the input mapping explicitly on the "
            "instance node.",
            ambiguous_originals=ambiguous,
            orig_names=list(orig_names),
            inst_names=list(inst_names),
        )
    for o, cands in cand_by_orig.items():
        if len(cands) == 1:
            mapping[o] = inst_names[cands[0]]
            used.add(cands[0])
    # Pass 3: positional fallback for remaining (no substring evidence
    # at all — e.g. fully renamed sources; order follows the wiring)
    unused = [i for i in range(len(inst_names)) if i not in used]
    unmatched = [o for o in orig_names if o not in mapping]
    for orig, i in zip(unmatched, unused):
        mapping[orig] = inst_names[i]

    return mapping


def resolve_input_mapping_names(
    source_names: list[str],
    input_mapping: object,
) -> list[str]:
    """Return stable logical names aligned with current incoming edges.

    Ordinary Polars transforms normally address each input by its current
    edge-derived name.  A structural rewrite can replace a parent without
    changing what that input means to the authored code; ``inputMapping``
    records that relationship as ``logical_name -> current_edge_name``.

    The relation is deliberately one-to-one.  Accepting stale values,
    duplicate values, or colliding logical names would either leave a name
    unbound or make two positional frames indistinguishable, so those states
    fail loudly instead of falling back to a guessed ordering.
    """
    from haute.errors import ConfigError

    if not isinstance(input_mapping, dict):
        raise ConfigError(
            "inputMapping must be an object mapping logical input names to "
            "current edge input names.",
            input_mapping=input_mapping,
        )

    invalid_entries = [
        (logical, current)
        for logical, current in input_mapping.items()
        if not isinstance(logical, str)
        or not logical
        or _sanitize_func_name(logical) != logical
        or not isinstance(current, str)
        or not current
    ]
    if invalid_entries:
        raise ConfigError(
            "inputMapping entries must use non-empty canonical Haute input "
            "identifiers for both logical names and current edge names.",
            invalid_entries=invalid_entries,
        )

    mapping: dict[str, str] = input_mapping
    source_set = set(source_names)
    stale_values = [
        (logical, current) for logical, current in mapping.items() if current not in source_set
    ]
    duplicate_values = duplicate_input_names(list(mapping.values()))
    if stale_values or duplicate_values:
        raise ConfigError(
            "inputMapping must map each logical input to one distinct current edge input name.",
            stale_values=stale_values,
            duplicate_values=duplicate_values,
            source_names=list(source_names),
        )

    logical_by_current = {current: logical for logical, current in mapping.items()}
    resolved = [logical_by_current.get(current, current) for current in source_names]
    duplicate_logical_names = duplicate_input_names(resolved)
    if duplicate_logical_names:
        raise ConfigError(
            "inputMapping produces duplicate logical input names.",
            duplicate_logical_names=duplicate_logical_names,
            source_names=list(source_names),
        )
    return resolved


def resolve_orig_source_names(
    node: GraphNode,
    node_map: dict[str, GraphNode],
    incoming_edges_by_target: Mapping[str, list[GraphEdge]],
    *,
    submodels: Mapping[str, Any] | None = None,
) -> list[str] | None:
    """Return logical input names that differ from the current edge names.

    For an instance, each name comes from the referenced original's incoming
    edge.  For an ordinary Polars transform with ``inputMapping``, the mapping
    preserves stable authored names across a parent-replacement rewrite.

    Returns ``None`` when no aliasing is required.
    """
    ref = node.data.config.get("instanceOf")
    if ref:
        if ref not in node_map:
            return None
        original = node_map[ref]
        source_names = [
            edge_input_name(
                edge,
                node_map[edge.source],
                submodels=submodels,
            )
            for edge in incoming_edges_by_target.get(ref, [])
        ]
        input_mapping = original.data.config.get("inputMapping")
        if original.data.nodeType == "polars" and input_mapping is not None:
            return resolve_input_mapping_names(source_names, input_mapping)
        return source_names

    input_mapping = node.data.config.get("inputMapping")
    if node.data.nodeType != "polars" or input_mapping is None:
        return None
    source_names = [
        edge_input_name(
            edge,
            node_map[edge.source],
            submodels=submodels,
        )
        for edge in incoming_edges_by_target.get(node.id, [])
    ]
    return resolve_input_mapping_names(source_names, input_mapping)
