"""Graph fingerprinting for cache invalidation."""

from __future__ import annotations

import ast as _ast
import json as _json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from haute._hashing import content_hash, content_hash_bytes
from haute._logging import get_logger
from haute._types import GraphNode, NodeType, PipelineGraph

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
# v4: edge serialization became port-aware — ``sourceHandle`` /
# ``targetHandle`` are now part of the digest material, so rewiring
# which port feeds a consumer invalidates previews/traces/dataframes
# cached under the old wiring.
ALGO_VERSION: int = 4


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


def _canonicalise(value: Any) -> Any:
    """Recursively convert *value* to a JSON-safe, order-independent form.

    The resulting structure is fed to ``json.dumps(..., sort_keys=True)``
    to produce a digest that is:

      * deterministic across runs (no ``repr()``-based fallbacks that
        depend on hash-seed or insertion order);
      * equal for sets / frozensets whose elements are the same regardless
        of the order they were inserted (unordered containers are sorted).

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
    if isinstance(value, dict):
        canon: dict[str, Any] = {}
        for k, v in value.items():
            if not isinstance(k, str):
                raise TypeError(
                    f"Cannot fingerprint dict with non-string key of type {type(k).__name__!r}",
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
    without relying on cross-type ``<`` support.
    """
    if value is None:
        return ("0_none", 0)
    if isinstance(value, bool):
        return ("1_bool", value)
    if isinstance(value, (int, float)):
        return ("2_num", value)
    if isinstance(value, str):
        return ("3_str", value)
    if isinstance(value, list):
        # Nested structures: sort by their JSON encoding.  ``sort_keys``
        # makes the encoding itself deterministic.
        return ("4_list", _json.dumps(value, sort_keys=True))
    if isinstance(value, dict):
        return ("5_dict", _json.dumps(value, sort_keys=True))
    raise TypeError(
        f"Cannot produce sort key for canonicalised value of type {type(value).__name__!r}",
    )


def _graph_base_fingerprint(graph: PipelineGraph) -> str:
    """Compute the base fingerprint of a graph's structure.

    Always recomputed to avoid serving stale cached results when the
    graph instance is mutated (e.g. node config changes).
    """
    parts: list[str] = []
    for n in sorted(graph.nodes, key=lambda n: n.id):
        canonical_config = _canonicalise(_node_config_for_execution_fingerprint(n))
        parts.append(
            f"{n.id}|{n.data.nodeType}|{_json.dumps(canonical_config, sort_keys=True)}",
        )
    # Edges: serialize the full wiring — ``sourceHandle``/``targetHandle``
    # select WHICH PORT of a multi-port node (or which edge-join role)
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
            _json.dumps(
                [e.source, e.sourceHandle, e.target, e.targetHandle],
                separators=(",", ":"),
            )
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


def _utility_candidates_for_dir(pipeline_dir: str | Path | None) -> list[Path]:
    bases: list[Path] = []
    if pipeline_dir is not None:
        bases.append(Path(pipeline_dir).resolve())
    bases.append(Path.cwd().resolve())

    seen: set[Path] = set()
    candidates: list[Path] = []
    for base in bases:
        for candidate in (base / "utility.py", base / "utility"):
            resolved = candidate.resolve()
            if resolved in seen:
                continue
            seen.add(resolved)
            candidates.append(resolved)
    return candidates


def _stat_key_for_utility_file(path: Path) -> _UtilityFileStatKey:
    stat = path.stat()
    return _UtilityFileStatKey(path=path.resolve(), mtime_ns=stat.st_mtime_ns, size=stat.st_size)


def _utility_file_hash(path: Path, memo: GraphFingerprintMemo | None) -> str:
    """Return a content hash for *path*.

    Without a caller-scoped memo this always reads the file so independent
    fingerprint calls cannot reuse stale digests when metadata is preserved.
    With a memo, unchanged metadata can reuse a digest inside the same request.
    """
    for _ in range(2):
        key = _stat_key_for_utility_file(path)
        cached = memo.utility_file_hashes.get(key) if memo is not None else None
        if cached is not None:
            return cached

        digest = content_hash(path)
        after_key = _stat_key_for_utility_file(path)
        if after_key == key:
            if memo is not None:
                memo.utility_file_hashes[after_key] = digest
            return digest

    raise RuntimeError(f"Utility file changed while hashing: {path!s}")


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
        utility_entries = [
            _hash_utility_candidate(candidate, memo)
            for candidate in _utility_candidates_for_dir(pipeline_dir)
        ]
        parts.append({"kind": "utility", "entries": utility_entries})
    payload = _json.dumps(parts, sort_keys=True, separators=(",", ":"))
    return content_hash_bytes(payload.encode())


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
        context_parts = [f"preamble_context:{context_fingerprint}"] if context_fingerprint else []
        combined = "\n".join([*extra_keys, *context_parts, base])
        digest = content_hash_bytes(combined.encode())
    else:
        digest = base
    fp = f"v{ALGO_VERSION}:{digest}"
    logger.debug("graph_fingerprint_computed", fingerprint=fp[:8], extra_keys=extra_keys)
    return fp
