"""Graph fingerprinting for cache invalidation."""

from __future__ import annotations

import ast as _ast
import importlib as _importlib
import json as _json
import math as _math
import sys as _sys
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from importlib.machinery import PathFinder as _PathFinder
from pathlib import Path
from typing import Any

from haute._hashing import content_hash, content_hash_bytes
from haute._logging import get_logger
from haute._stat_gated_cache import StatGatedCache, artifact_cache_key
from haute._types import GraphEdge, GraphNode, NodeType, PipelineGraph

logger = get_logger(component="cache")

# ---------------------------------------------------------------------------
# Algorithm versioning
# ---------------------------------------------------------------------------

# Fingerprint-algorithm version.  Embedded as a ``"v<N>:"`` prefix on
# every :func:`graph_fingerprint` output so that a future
# canonicalisation tweak (node-attribute order, edge representation,
# hash family, etc.) cannot silently collide with digests produced by
# the previous algorithm.  Bumping this constant invalidates every
# previously-cached fingerprint-keyed entry in a single step.
#
# Read dynamically inside :func:`graph_fingerprint` so tests can
# ``monkeypatch.setattr(haute._cache, "ALGO_VERSION", ...)`` to
# simulate a bump and confirm cache entries do not collide across
# versions — pinned by
# ``tests/test_routes_hygiene.py::TestBumpVersionInvalidatesCache``.
#
# v4: edge serialization became frame-aware — ``sourceHandle`` /
# ``targetHandle`` are now part of the digest material, so rewiring
# which frame feeds a consumer invalidates previews/traces/dataframes
# cached under the old wiring.
#
# v5: canonical-JSON encoder unification (W2.13).  The two divergent
# encoders (``_canonicalise`` here vs ``_normalise_execution_policy``
# in ``_dataframe_execution_cache``) were replaced by the single
# :func:`canonical_json`.  Node-config digest material switched from
# spaced ``json.dumps`` separators to the canonical compact form, so
# every node with a non-empty config produces different digest bytes.
#
# v6: fingerprint-material framing became injective (W1-cache F164).
# The node line and the ``graph_fingerprint`` extra-keys/context join are
# now emitted through :func:`canonical_json` instead of raw ``|``/``\n``
# concatenation, so a node id or extra key that literally contains those
# separators can no longer collide with a logically-different graph.  The
# NaN sort order in :func:`_sort_key` also became total (F163).  The byte
# layout of every digest changed, so previously-cached entries invalidate
# in one step.
ALGO_VERSION: int = 6


@dataclass(frozen=True)
class _UtilityFileStatKey:
    """Metadata that identifies unchanged utility file bytes inside one memo."""

    path: Path
    mtime_ns: int
    size: int


@dataclass
class GraphFingerprintMemo:
    """Request-scoped memo for repeated graph fingerprint calculations.

    File metadata is not a complete correctness boundary: an editor or copy
    tool can preserve both size and mtime while changing bytes. Keep this memo
    scoped to one immutable request/operation and use fresh content hashes for
    independent calls.
    """

    utility_file_hashes: dict[_UtilityFileStatKey, str] = field(default_factory=dict)


def canonical_json(value: Any) -> str:
    """THE canonical-JSON encoding for digest material — the only one.

    Every byte of fingerprint/cache-key material in this codebase that is
    JSON-shaped must be produced by this function (graph node configs,
    edge wiring, preamble context, dataframe-execution payloads and
    policies).  Two encoders with subtly different rules is how silent
    cache collisions and phantom invalidations are born — do not add a
    second one; import this.

    Canonical rules:

      * Mappings (any :class:`collections.abc.Mapping`) require string
        keys (``TypeError`` otherwise — the empty string is a valid key)
        and serialize with keys sorted by code point.
      * ``list``/``tuple`` serialize as JSON arrays in element order.
        Other iterables (generators, ranges, NumPy arrays, ...) raise
        ``TypeError``: silently consuming arbitrary iterables would let
        non-JSON values masquerade as digest material.
      * ``set``/``frozenset`` members are ordered by ``(type-tag, value)``
        — ``None`` < ``bool`` < numbers (numeric order) < strings (code
        point order) < arrays < objects — see :func:`_sort_key`.
      * Scalars (``None``/``bool``/``int``/``float``/``str``) use
        ``json.dumps`` text forms; non-finite floats serialize as the
        deterministic ``Infinity``/``-Infinity``/``NaN`` tokens (this is
        digest material, not interchange JSON).
      * Output is compact (``(",", ":")`` separators) and ASCII-escaped.
      * Anything else raises ``TypeError`` — fail loud, never ``repr()``.
    """
    return _canonical_dumps(_canonicalise(value))


def _canonical_dumps(canonical_value: Any) -> str:
    """Serialize an already-canonicalised value with the one true format."""
    return _json.dumps(
        canonical_value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
    )


def _canonicalise(value: Any) -> Any:
    """Recursively convert *value* to a JSON-safe, order-independent form.

    The resulting structure is fed to :func:`_canonical_dumps` to produce
    a digest that is:

      * deterministic across runs (no ``repr()``-based fallbacks that
        depend on hash-seed or insertion order);
      * equal for sets / frozensets whose elements are the same regardless
        of the order they were inserted (unordered containers are sorted);
      * equal for mappings regardless of key insertion order.

    Unsupported types raise ``TypeError`` loudly rather than silently
    reducing to ``repr()``.  This ensures a drift in config shape is
    caught at fingerprint time instead of producing quietly-wrong digests.
    """
    if value is None or isinstance(value, (str, int, float, bool)):
        # ``bool`` is a subclass of ``int`` but that's fine for our use —
        # both survive ``json.dumps`` losslessly.  We intentionally reject
        # ``bytes`` and ``complex`` below because neither has a canonical
        # JSON text form.
        return value
    if isinstance(value, (list, tuple)):
        return [_canonicalise(v) for v in value]
    if isinstance(value, (set, frozenset)):
        # Canonicalise members first so mixed-type sets raise loudly on
        # unsupported members rather than hitting the ``sorted`` TypeError
        # with a confusing message.
        members = [_canonicalise(v) for v in value]
        try:
            return sorted(members, key=_sort_key)
        except TypeError as exc:  # heterogeneous unsortable set
            raise TypeError(
                f"Cannot fingerprint set with unsortable members: {exc}",
            ) from exc
    if isinstance(value, Mapping):
        canon: dict[str, Any] = {}
        for k, v in value.items():
            if not isinstance(k, str):
                raise TypeError(
                    f"Cannot fingerprint mapping with non-string key of type {type(k).__name__!r}",
                )
            canon[k] = _canonicalise(v)
        return canon
    raise TypeError(
        f"Cannot fingerprint value of type {type(value).__name__!r} — "
        f"no deterministic canonical form is defined",
    )


def _sort_key(value: Any) -> tuple[str, Any]:
    """Key function for sorting canonicalised set members.

    Produces a tuple of (type-tag, value) so mixed-type canonical values
    (all of which are JSON-safe by construction) can be ordered stably
    without relying on cross-type ``<`` support.  Strings order by raw
    code point — never by their ASCII-escaped JSON text, which would
    flip the order of non-ASCII members.
    """
    if value is None:
        return ("0_none", 0)
    if isinstance(value, bool):
        return ("1_bool", value)
    if isinstance(value, (int, float)):
        # ``NaN`` compares False against everything (including itself), which
        # makes ``sorted`` order-dependent — a set containing NaN would then
        # canonicalise differently per insertion order, breaking the
        # unordered-container determinism contract.  Segregate NaN into a
        # fixed terminal bucket with a constant secondary value so every NaN
        # is byte-identical and can never displace a finite member.  Finite
        # values (and +/-inf, which order correctly) keep their natural
        # numeric order via bucket ``0``.
        if isinstance(value, float) and _math.isnan(value):
            return ("2_num", (1, 0.0))
        return ("2_num", (0, value))
    if isinstance(value, str):
        return ("3_str", value)
    if isinstance(value, list):
        # Nested structures: sort by their canonical JSON encoding (the
        # members are already canonicalised, so this is deterministic).
        return ("4_list", _canonical_dumps(value))
    if isinstance(value, dict):
        return ("5_dict", _canonical_dumps(value))
    raise TypeError(
        f"Cannot produce sort key for canonicalised value of type {type(value).__name__!r}",
    )


def _graph_base_fingerprint(graph: PipelineGraph) -> str:
    """Compute the base structural digest of a graph (node configs + edges).

    The result is memoised once per :class:`PipelineGraph` instance via the
    :attr:`PipelineGraph._haute_base_fingerprint` cached_property — this
    function is the raw computation behind that cache, not something recomputed
    on every call.  Freshness across edits is guaranteed by the immutable
    ``model_copy(update=...)`` idiom: ``model_copy`` produces a new instance
    and clears the memo (see ``PipelineGraph._HAUTE_CACHED_PROPERTY_NAMES``),
    so a structurally-different graph never serves a stale digest.
    """
    parts: list[str] = []
    for n in sorted(graph.nodes, key=lambda n: n.id):
        # Frame the node line with ``canonical_json`` — exactly as edges are
        # (below) — so the digest is injective.  Raw ``id|type|config``
        # concatenation joined by ``\n`` is NOT: a node id containing ``|`` or
        # ``\n`` would collide against the field/record separators.  A compact
        # JSON array quotes/escapes every field, ruling out separator-content
        # collisions by construction.
        parts.append(
            canonical_json(
                [n.id, str(n.data.nodeType), _node_config_for_execution_fingerprint(n)],
            ),
        )
    # Edges: serialize the full wiring — ``sourceHandle``/``targetHandle``
    # select WHICH FRAME of a multi-frame node (or which edge-join role)
    # feeds the consumer, so they are digest material just like the
    # endpoints.  A compact JSON array is unambiguous by construction:
    # quoting/escaping rules out separator-content collisions, and the
    # absent handle (``null``) stays distinct from every real handle
    # string, including ports literally named ``"None"`` or ``"null"``.
    # Sorting the serialized lines themselves keeps the digest independent
    # of edge insertion order with no tie-breaking gap — equal sort keys
    # imply byte-identical lines.
    parts.extend(
        sorted(
            canonical_json([e.source, e.sourceHandle, e.target, e.targetHandle])
            for e in graph.edges
        ),
    )
    return content_hash_bytes("\n".join(parts).encode())


def _node_config_for_execution_fingerprint(node: GraphNode) -> dict[str, Any]:
    """Return the node config fields that affect executor/cache output."""

    config = node.data.config
    if node.data.nodeType == NodeType.EXPLORE:
        return {key: value for key, value in config.items() if key != "overview"}
    return config


# Preview lineage keys deliberately do not use ``graph_fingerprint``: a preview
# must be invalidated by precisely the portion of the graph that can execute
# for its selected target/source, not by unrelated canvas state.
LINEAGE_CACHE_KEY_VERSION = 1


@dataclass(frozen=True)
class LineageCacheKeyRequest:
    """All dimensions which identify a lineage-scoped preview result.

    ``prepared`` is structural on purpose.  Importing ``PreparedGraph`` here
    would create a cache/projection import cycle, so its small public shape is
    checked by :func:`lineage_cache_key` instead.
    """

    graph: PipelineGraph
    prepared: Any
    target_node_id: str | None
    source: str
    requested_columns: Iterable[str] | None
    initial_column_limit: int | None
    row_limit: int | None
    port_label: str | None
    contract_fingerprint: str
    selected_live_switch_path: tuple[dict[str, object], ...]
    runtime_input_fingerprint: str
    execution_semantics_version: str


def _lineage_node_identity(node: GraphNode) -> dict[str, Any]:
    return {
        "id": node.id,
        "label": node.data.label,
        "nodeType": str(node.data.nodeType),
        "config": _node_config_for_execution_fingerprint(node),
    }


def _lineage_edge_identity(edge: GraphEdge) -> dict[str, Any]:
    return {
        "id": edge.id,
        "source": edge.source,
        "sourceHandle": edge.sourceHandle,
        "target": edge.target,
        "targetHandle": edge.targetHandle,
    }


def _normalise_requested_columns(columns: Iterable[str] | None) -> tuple[str, ...] | None:
    if columns is None:
        return None
    if isinstance(columns, (str, bytes)):
        raise TypeError("requested_columns must be an iterable of non-empty strings")

    normalised: list[str] = []
    seen: set[str] = set()
    for column in columns:
        if not isinstance(column, str) or not column:
            raise ValueError("requested_columns must contain only non-empty strings")
        if column not in seen:
            seen.add(column)
            normalised.append(column)
    return tuple(normalised)


def _validate_lineage_request(
    request: LineageCacheKeyRequest,
) -> tuple[dict[str, GraphNode], list[GraphEdge]]:
    if not isinstance(request.graph, PipelineGraph):
        raise TypeError("graph must be a PipelineGraph")
    for name in (
        "source",
        "contract_fingerprint",
        "runtime_input_fingerprint",
        "execution_semantics_version",
    ):
        if not isinstance(getattr(request, name), str):
            raise TypeError(f"{name} must be a string")
    if request.target_node_id is not None and not isinstance(request.target_node_id, str):
        raise TypeError("target_node_id must be a string or None")
    if request.port_label is not None and not isinstance(request.port_label, str):
        raise TypeError("port_label must be a string or None")
    for name in ("initial_column_limit", "row_limit"):
        value = getattr(request, name)
        if value is not None and (
            isinstance(value, bool) or not isinstance(value, int) or value < 0
        ):
            raise ValueError(f"{name} must be a non-negative integer or None")

    prepared = request.prepared
    if not all(hasattr(prepared, name) for name in ("node_map", "order", "relevant_edges")):
        raise ValueError("prepared graph does not match: missing lineage fields")
    if not isinstance(prepared.node_map, Mapping):
        raise ValueError("prepared graph does not match: node_map is invalid")

    graph_nodes = request.graph.node_map
    relevant_ids = list(prepared.order)
    if len(set(relevant_ids)) != len(relevant_ids):
        raise ValueError("prepared graph does not match: duplicate node id")
    relevant_nodes: dict[str, GraphNode] = {}
    for node_id in relevant_ids:
        prepared_node = prepared.node_map.get(node_id)
        graph_node = graph_nodes.get(node_id)
        if prepared_node is None or graph_node is None:
            raise ValueError("prepared graph does not match: missing relevant node")
        if canonical_json(_lineage_node_identity(prepared_node)) != canonical_json(
            _lineage_node_identity(graph_node),
        ):
            raise ValueError("prepared graph does not match: relevant node differs")
        relevant_nodes[node_id] = graph_node

    graph_edges = {canonical_json(_lineage_edge_identity(edge)) for edge in request.graph.edges}
    relevant_edges = list(prepared.relevant_edges)
    for edge in relevant_edges:
        if canonical_json(_lineage_edge_identity(edge)) not in graph_edges:
            raise ValueError("prepared graph does not match: relevant edge differs")
        if edge.source not in relevant_nodes or edge.target not in relevant_nodes:
            raise ValueError("prepared graph does not match: edge leaves lineage")
    if request.target_node_id is not None and request.target_node_id not in relevant_nodes:
        raise ValueError("prepared graph does not match: target is not relevant")
    return relevant_nodes, relevant_edges


def selected_live_switch_path(prepared: Any) -> tuple[dict[str, object], ...]:
    """Return the source-selected incoming wiring of relevant live switches."""
    relevant_ids = set(prepared.order)
    switches: list[dict[str, object]] = []
    for switch_id in sorted(relevant_ids):
        node = prepared.node_map.get(switch_id)
        if node is None or node.data.nodeType != NodeType.LIVE_SWITCH:
            continue
        incoming = [
            _lineage_edge_identity(edge)
            for edge in prepared.relevant_edges
            if edge.target == switch_id and edge.source in relevant_ids
        ]
        switches.append(
            {
                "switch_id": switch_id,
                "incoming_edges": tuple(sorted(incoming, key=canonical_json)),
            },
        )
    return tuple(switches)


def lineage_cache_key(request: LineageCacheKeyRequest) -> str:
    """Build a deterministic, lineage-scoped preview cache key."""
    if not isinstance(request, LineageCacheKeyRequest):
        raise TypeError("request must be a LineageCacheKeyRequest")
    relevant_nodes, relevant_edges = _validate_lineage_request(request)
    requested_columns = _normalise_requested_columns(request.requested_columns)

    payload = {
        "graph": {
            "preamble": request.graph.preamble,
            "source_file": request.graph.source_file,
            "preserved_blocks": request.graph.preserved_blocks,
            "sources": request.graph.sources,
            "active_source": request.graph.active_source,
            "nodes": [
                _lineage_node_identity(relevant_nodes[node_id])
                for node_id in sorted(relevant_nodes)
            ],
            "edges": sorted(
                (_lineage_edge_identity(edge) for edge in relevant_edges),
                key=canonical_json,
            ),
        },
        "target_node_id": request.target_node_id,
        "source": request.source,
        "requested_columns": requested_columns,
        "initial_column_limit": request.initial_column_limit,
        "row_limit": request.row_limit,
        "port_label": request.port_label,
        "contract_fingerprint": request.contract_fingerprint,
        "selected_live_switch_path": request.selected_live_switch_path,
        "runtime_input_fingerprint": request.runtime_input_fingerprint,
        "execution_semantics_version": request.execution_semantics_version,
    }
    return f"lineage-preview:v{LINEAGE_CACHE_KEY_VERSION}:" + content_hash_bytes(
        canonical_json(payload).encode(),
    )


def _is_utility_module_name(value: str) -> bool:
    return value == "utility" or value.startswith("utility.")


def _string_contains_utility_import(value: str) -> bool:
    stripped = value.strip()
    return (
        _is_utility_module_name(stripped)
        or "import utility" in stripped
        or "from utility" in stripped
    )


def _call_imports_utility(node: _ast.Call) -> bool:
    if not node.args:
        return False
    first_arg = node.args[0]
    if not isinstance(first_arg, _ast.Constant) or not isinstance(first_arg.value, str):
        return False

    func = node.func
    if isinstance(func, _ast.Name) and func.id == "__import__":
        return _is_utility_module_name(first_arg.value)
    if (
        isinstance(func, _ast.Attribute)
        and func.attr == "import_module"
        and isinstance(func.value, _ast.Name)
        and func.value.id == "importlib"
    ):
        return _is_utility_module_name(first_arg.value)
    if isinstance(func, _ast.Name) and func.id == "exec":
        return _string_contains_utility_import(first_arg.value)
    return False


def preamble_imports_utility(preamble: str) -> bool:
    """Return whether *preamble* imports the project ``utility`` package."""
    if not preamble.strip():
        return False
    try:
        tree = _ast.parse(preamble)
    except SyntaxError:
        # The executor will surface the syntax error later.  For cache-key
        # purposes, keep invalid preambles that mention utility sensitive
        # to utility edits instead of serving stale error/output entries.
        return "utility" in preamble

    for node in _ast.walk(tree):
        if isinstance(node, _ast.Import):
            for alias in node.names:
                if _is_utility_module_name(alias.name):
                    return True
        elif isinstance(node, _ast.ImportFrom):
            module = node.module or ""
            if _is_utility_module_name(module):
                return True
        elif isinstance(node, _ast.Call):
            if _call_imports_utility(node):
                return True
    return False


def _pipeline_dir(graph: PipelineGraph) -> Path | None:
    source_file = graph.source_file
    if not source_file:
        return None
    path = Path(source_file)
    if not path.is_absolute():
        path = Path.cwd() / path
    return path.resolve().parent


def _utility_search_path(pipeline_dir: str | Path | None) -> list[str]:
    """Return the search path the executor uses to resolve the ``utility`` import.

    Mirrors :func:`haute.executor._prioritise_preamble_import_paths`: the
    pipeline directory and the current working directory are searched first,
    then the rest of ``sys.path``.  Hashing whatever *this* path resolves keeps
    the fingerprint aligned with the module that actually executes — a two-dir
    scan silently misses a ``utility`` resolved elsewhere on ``sys.path``.
    """
    prioritised: list[str] = []
    if pipeline_dir is not None:
        prioritised.append(str(Path(pipeline_dir).resolve()))
    prioritised.append(str(Path.cwd().resolve()))

    seen = set(prioritised)
    ordered = list(prioritised)
    for entry in _sys.path:
        # Skip the empty string (means "cwd", already covered) and duplicates
        # so the resolution order matches the executor exactly.
        if not entry or entry in seen:
            continue
        seen.add(entry)
        ordered.append(entry)
    return ordered


def _resolve_utility_locations(pipeline_dir: str | Path | None) -> list[Path] | None:
    """Resolve the top-level ``utility`` module/package the preamble will import.

    Uses :class:`importlib.machinery.PathFinder` against the same prioritised
    search path the executor installs at exec time, so the bytes we hash are the
    bytes that will run.  ``PathFinder.find_spec`` performs pure resolution — it
    neither imports the module nor mutates ``sys.modules`` — and we invalidate
    the finder caches first so a freshly created/edited ``utility`` is seen.

    Returns the filesystem location(s) to hash (a single file for a module, one
    or more directories for a package / namespace package), or ``None`` when
    ``utility`` is not importable from the current path (recorded as
    ``"missing"`` so a later creation still invalidates the digest).
    """
    _importlib.invalidate_caches()
    spec = _PathFinder.find_spec("utility", _utility_search_path(pipeline_dir))
    if spec is None:
        return None
    search_locations = spec.submodule_search_locations
    if search_locations:
        return [Path(loc).resolve() for loc in search_locations]
    origin = spec.origin
    if origin and origin not in ("built-in", "frozen", "namespace"):
        return [Path(origin).resolve()]
    return None


def _stat_key_for_utility_file(path: Path) -> _UtilityFileStatKey:
    stat = path.stat()
    return _UtilityFileStatKey(path=path.resolve(), mtime_ns=stat.st_mtime_ns, size=stat.st_size)


# Process-wide stat-gated memo over utility file content hashes, so EVERY
# preamble fingerprint caller (supersession keys, execute_trace, preview
# keys, future call sites) hits the memo by construction rather than by
# parameter-threading etiquette.  Same invalidation contract as
# :func:`haute.execution._stat_gated_runtime_path_fingerprint`: a digest is
# reused while ``(st_mtime_ns, st_size)`` is unchanged; any metadata change
# re-hashes content; a gate that moves during the read is retried once and
# then fails loudly.  A rewrite that preserves both mtime_ns and size is
# below the gate's resolution — the documented trade the deploy path
# already accepts.
_utility_file_hash_cache: StatGatedCache[str, str] = StatGatedCache(
    artifact_kind="Preamble utility file"
)


def _utility_file_hash(path: Path, memo: GraphFingerprintMemo | None) -> str:
    """Return a content hash for *path* via the process-wide stat-gated memo.

    The optional request-scoped *memo* additionally pins the FIRST digest
    observed for a given ``(path, mtime_ns, size)`` within one request, so a
    file changing mid-request cannot make one fingerprint call disagree with
    an earlier one inside the same operation.
    """
    key = _stat_key_for_utility_file(path)
    cached = memo.utility_file_hashes.get(key) if memo is not None else None
    if cached is not None:
        return cached

    resolved = key.path
    digest = _utility_file_hash_cache.get_or_load(
        artifact_cache_key(resolved),
        str(resolved),
        lambda: content_hash(resolved),
    )
    if memo is not None:
        # Re-stat for the memo slot: if the gate moved during the load the
        # StatGatedCache already retried against the settled state, so the
        # digest belongs to the CURRENT gate, not the pre-load one.
        memo.utility_file_hashes[_stat_key_for_utility_file(path)] = digest
    return digest


def _hash_utility_candidate(
    path: Path,
    memo: GraphFingerprintMemo | None,
) -> dict[str, Any]:
    if not path.exists():
        return {"kind": "missing"}
    if path.is_file():
        return {"kind": "file", "hash": _utility_file_hash(path, memo)}
    if not path.is_dir():
        raise TypeError(f"Cannot fingerprint utility module root at {path!s}")

    files: list[dict[str, str]] = []
    for file_path in sorted(path.rglob("*.py")):
        if file_path.is_file():
            files.append(
                {
                    "path": file_path.relative_to(path).as_posix(),
                    "hash": _utility_file_hash(file_path, memo),
                }
            )
    return {"kind": "package", "files": files}


def preamble_execution_fingerprint(
    preamble: str | None,
    *,
    pipeline_dir: str | Path | None = None,
    memo: GraphFingerprintMemo | None = None,
) -> str | None:
    """Return a digest of preamble inputs that can affect execution.

    Empty preambles return ``None`` so callers can stay on their cheapest
    cache paths. Non-empty preambles always include the preamble text and,
    when they import the project ``utility`` package, the current contents
    of nearby ``utility.py`` / ``utility/**/*.py`` files.
    """
    preamble = preamble or ""
    if not preamble.strip():
        return None

    parts: list[dict[str, Any]] = [
        {"kind": "preamble", "hash": content_hash_bytes(preamble.encode())}
    ]
    if preamble_imports_utility(preamble):
        locations = _resolve_utility_locations(pipeline_dir)
        if locations is None:
            utility_entries: list[dict[str, Any]] = [{"kind": "missing"}]
        else:
            utility_entries = [_hash_utility_candidate(location, memo) for location in locations]
        parts.append({"kind": "utility", "entries": utility_entries})
    return content_hash_bytes(canonical_json(parts).encode())


def graph_fingerprint(
    graph: PipelineGraph,
    *extra_keys: str,
    memo: GraphFingerprintMemo | None = None,
) -> str:
    """Deterministic hash of graph execution inputs for cache invalidation.

    *extra_keys* are prepended (e.g. target_node_id, row_limit) so the
    same graph with different execution parameters gets a different hash.
    Used by both the trace cache (trace.py) and preview cache (executor.py).

    The graph's structural base fingerprint (node configs + edge topology)
    is computed once per ``PipelineGraph`` instance and cached via
    :attr:`PipelineGraph._haute_base_fingerprint`. Preamble text and imported
    project ``utility`` module content are mixed in dynamically so GUI edits
    cannot reuse stale preview/trace DataFrames.

    The returned value is prefixed with ``"v<ALGO_VERSION>:"`` so a
    future canonicalisation change (which bumps
    :data:`ALGO_VERSION`) cannot collide with stale cache entries.
    The constant is read **dynamically** on every call so tests (and
    emergency cache-busts) can monkeypatch it without re-importing.
    """
    base = graph._haute_base_fingerprint
    context_fingerprint = preamble_execution_fingerprint(
        graph.preamble,
        pipeline_dir=_pipeline_dir(graph),
        memo=memo,
    )
    if extra_keys or context_fingerprint:
        # Frame the combined material with ``canonical_json`` so the join is
        # injective: a raw ``"\n".join`` collides ``("a\nb",)`` with
        # ``("a", "b")`` and lets a ``context_fingerprint`` bleed into an
        # extra key.  Keeping ``extra_keys`` as a nested array (order-
        # significant) and ``context_fingerprint`` as its own element
        # (``null`` when absent, distinct from any digest string) removes
        # every separator-content ambiguity.
        combined = canonical_json([list(extra_keys), context_fingerprint, base])
        digest = content_hash_bytes(combined.encode())
    else:
        digest = base
    fp = f"v{ALGO_VERSION}:{digest}"
    logger.debug("graph_fingerprint_computed", fingerprint=fp[:8], extra_keys=extra_keys)
    return fp
