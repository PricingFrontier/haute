"""Pure-function graph utilities decoupled from Pydantic model definitions.

These helpers operate on graph data (edges, labels, node references) but
do not need to live alongside the Pydantic models themselves.  Keeping
them here avoids bloating ``_types.py`` with implementation logic and
lets callers import utilities without pulling the full model module.
"""

from __future__ import annotations

from collections.abc import Mapping
from hashlib import blake2b
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from haute._types import GraphEdge, GraphNode


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
    characters) or reversibly encoded as ``_x<hex>_`` for non-ASCII so
    distinct labels produce distinct identifiers.

    Invariants (see tests/test_parser_sanitize_contracts.py):

    - ASCII inputs are unchanged from pre-fix behaviour.
    - Output is always a valid Python identifier (``str.isidentifier()``).
    - Different inputs produce different outputs (collisions across the
      ASCII/non-ASCII boundary are eliminated).
    - Idempotent: ``sanitize(sanitize(x)) == sanitize(x)``.  The encoded
      form ``_x<hex>_`` is itself all-ASCII alnum/underscore, so a second
      pass is a no-op.

    Stays in sync with the frontend implementation in
    ``frontend/src/utils/sanitizeName.ts``.
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
    reversible character mapping with frame-label repairs of its own.
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


def edge_input_name(edge: GraphEdge, source_node: GraphNode) -> str:
    """Return the one input name contributed by an incoming edge.

    API-input edges use their persisted frame handle verbatim. Every other
    edge uses the sanitised source-node label; source handles on ordinary
    nodes identify an output port and are not input names.
    """
    if source_node.data.nodeType == "apiInput":
        if edge.sourceHandle is None:
            raise ValueError(
                f"apiInput edge {edge.id!r} has no sourceHandle/frame label",
            )
        return edge.sourceHandle
    return _sanitize_func_name(source_node.data.label)


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
    (kwarg generation). The frontend mirrors this algorithm in NodePanel.tsx
    (InstanceConfig auto-mapping).

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
        return [
            edge_input_name(edge, node_map[edge.source])
            for edge in incoming_edges_by_target.get(ref, [])
        ]

    input_mapping = node.data.config.get("inputMapping")
    if node.data.nodeType != "polars" or input_mapping is None:
        return None
    source_names = [
        edge_input_name(edge, node_map[edge.source])
        for edge in incoming_edges_by_target.get(node.id, [])
    ]
    return resolve_input_mapping_names(source_names, input_mapping)
