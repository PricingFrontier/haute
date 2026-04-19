"""Pure-function graph utilities decoupled from Pydantic model definitions.

These helpers operate on graph data (edges, labels, node references) but
do not need to live alongside the Pydantic models themselves.  Keeping
them here avoids bloating ``_types.py`` with implementation logic and
lets callers import utilities without pulling the full model module.
"""

from __future__ import annotations

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


def _resolve_sink_path(path: str, fmt: str) -> str:
    """Normalise a sink output path.

    Prepends ``outputs/`` when the path has no directory component and
    appends the format extension (``.parquet`` or ``.csv``) when missing.
    """
    ext = ".csv" if fmt == "csv" else ".parquet"
    if "/" not in path and "\\" not in path:
        path = f"outputs/{path}"
    if not path.endswith(ext):
        path = f"{path}{ext}"
    return path


def _sanitize_func_name(label: str) -> str:
    """Convert a human label to a valid Python function name (preserves casing).

    Uses ASCII-only matching to stay in sync with the frontend implementation
    in frontend/src/utils/sanitizeName.ts.
    """
    import keyword

    name = label.strip()
    name = name.replace(" ", "_").replace("-", "_")
    name = "".join(c for c in name if c.isascii() and (c.isalnum() or c == "_"))
    if name and name[0].isdigit():
        name = f"node_{name}"
    if keyword.iskeyword(name):
        name = f"node_{name}"
    return name or "unnamed_node"


def build_instance_mapping(
    orig_names: list[str],
    inst_names: list[str],
    explicit: dict[str, str] | None = None,
) -> dict[str, str]:
    """Map original input parameter names to instance input names.

    Priority: explicit mapping → exact name match → substring match → positional.
    Used by the executor (alias injection) and codegen (kwarg generation).
    The frontend mirrors this algorithm in NodePanel.tsx (InstanceConfig auto-mapping).

    Raises
    ------
    ConfigError
        If *explicit* contains stale entries — keys that do not appear in
        *orig_names* or non-empty values that do not appear in *inst_names*.
        Stale entries indicate the UI-stored ``inputMapping`` is out of
        sync with the current graph and would silently corrupt wiring.
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
    # Pass 2: substring match (e.g. "claims_aggregate" in "claims_aggregate_instance")
    for orig in orig_names:
        if orig in mapping:
            continue
        for i, inst in enumerate(inst_names):
            if i not in used and orig in inst:
                mapping[orig] = inst
                used.add(i)
                break
    # Pass 3: positional fallback for remaining
    unused = [i for i in range(len(inst_names)) if i not in used]
    unmatched = [o for o in orig_names if o not in mapping]
    for orig, i in zip(unmatched, unused):
        mapping[orig] = inst_names[i]

    return mapping


def resolve_orig_source_names(
    node: GraphNode,
    node_map: dict[str, GraphNode],
    all_parents: dict[str, list[str]],
    id_to_name: dict[str, str],
) -> list[str] | None:
    """For an instance node, return the sanitized names of the original's upstream inputs.

    Uses *all_parents* (built from the full edge list, not filtered by
    ``target_node_id``) so this works even when the original node isn't
    in the current execution subgraph.

    Returns ``None`` for non-instance nodes.
    """
    ref = node.data.config.get("instanceOf")
    if not ref or ref not in node_map:
        return None
    result: list[str] = []
    for pid in all_parents.get(ref, []):
        if pid in id_to_name:
            result.append(id_to_name[pid])
        else:
            n = node_map.get(pid)
            result.append(_sanitize_func_name(n.data.label) if n else pid)
    return result
