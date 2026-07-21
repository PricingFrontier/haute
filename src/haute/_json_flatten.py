"""Cache-layer infrastructure for JSON apiInput sources.

Historically this module also owned the v1 schema-aware flattening
codec (``flatten``, ``read_json_flat``, ``build_json_cache``, …). That
surface has been removed — the v2 per-frame shred in
:mod:`haute._json_shred` is the only JSON apiInput codec.

What remains here is the dual-layer (working/committed) cache directory
infrastructure that both v2 routes (build / status / delete) and the
save pipeline (mirror working → committed) depend on. Specifically:

  - :func:`_json_cache_dir` — resolves
    ``.haute_cache/<layer>/json_<hash>/`` for a JSON data file's cache.
  - :func:`clear_json_cache` — deletes the working-layer cache (DELETE
    endpoint).
  - :func:`mirror_cache_to_committed` — promotes working → committed at
    save time, or clears committed if working is gone.
  - :func:`cache_state_signature_for_graph` — composes a fingerprint
    fragment so a JSON-cache mutation invalidates the right preview-cache
    entries.

The on-disk layout is:

  ``.haute_cache/<layer>/json_<hash>/`` per data file, where ``<layer>``
  is ``working`` (volatile, in-session) or ``committed`` (durable, the
  source of truth post-restart). Each ``<hash>/`` directory contains
  per-frame parquets and a ``meta.json`` with ``{schema_mode,
  schema_fingerprint, tables}``; every table entry carries its parquet's
  size/SHA-256 content signature — written by
  :func:`haute._json_shred.build_per_port_cache`.
"""

from __future__ import annotations

import hashlib
import os
import shutil
from pathlib import Path
from typing import Any, cast

import orjson

from haute._logging import get_logger

logger = get_logger(component="json_cache")


# ---------------------------------------------------------------------------
# Layout constants
# ---------------------------------------------------------------------------
#
# The cache is split into two layers under `.haute_cache/<layer>/<hash>/`:
#
#  - `working/<hash>/` — written by the "Cache as Parquet" button. Volatile,
#    in-session. Reflects whatever the editor's in-memory schema was at click
#    time. Disposable.
#  - `committed/<hash>/` — written by Save, which mirrors `working/<hash>/`
#    into committed/ (including absence — if working/ doesn't exist, Save
#    ensures committed/ also doesn't exist). The durable contract that
#    survives a server restart.
#
# Each layer's `<hash>/` directory contains per-frame parquets (one per
# emit-true table in the v2 schema) and a `meta.json` sidecar carrying
# `{schema_mode, schema_fingerprint, tables}`. Each table entry includes a
# size/SHA-256 signature for its derived parquet path. Fingerprint, source-file
# identity, signed table manifest, and actual artifact bytes back the no-op
# trapdoors.

_CACHE_DIR = ".haute_cache"
_LAYER_WORKING = "working"
_LAYER_COMMITTED = "committed"
_META_FILENAME = "meta.json"


# Module-level session tracking. Empty per Python process. The save-time
# mirror consults `working/` only for data-file hashes in this set;
# otherwise it does nothing (a stale on-disk `working/` from a previous
# session must not be promoted automatically). The set is populated by the
# v2 build route (`routes/json_cache.py::build_json_cache`) on every
# SUCCESSFUL "Cache as Parquet" build — the C2 fix: without that call the
# mirror was dead code and `committed/` was never populated.
_session_consulted_hashes: set[str] = set()


def _path_hash(data_path: str | Path) -> str:
    """SHA-256 (32-char) hash of the canonical absolute data file path.

    Identical for any pair of relative/absolute paths that resolve to the
    same file, so the cache identity is stable across cwd changes.
    """
    canonical_path = os.path.normcase(str(Path(data_path).expanduser().resolve()))
    return hashlib.sha256(canonical_path.encode()).hexdigest()[:32]


def _json_cache_dir(data_path: str | Path, layer: str) -> Path:
    """Return the `<layer>/<hash>/` directory for a JSON data file's cache."""
    if layer not in (_LAYER_WORKING, _LAYER_COMMITTED):
        raise ValueError(f"Unknown cache layer: {layer!r}")
    return Path.cwd() / _CACHE_DIR / layer / f"json_{_path_hash(data_path)}"


def _json_cache_meta_path(cache_dir: Path) -> Path:
    """Return the `meta.json` sidecar path inside a `<layer>/<hash>/` directory."""
    return cache_dir / _META_FILENAME


def _mark_working_consulted(data_path: str | Path) -> None:
    """Record that working/ is authoritative for this data file in this process."""
    _session_consulted_hashes.add(_path_hash(data_path))


def _is_working_consulted(data_path: str | Path) -> bool:
    """True if working/ has been written for this data file in this process."""
    return _path_hash(data_path) in _session_consulted_hashes


def _clear_session() -> None:
    """Test-only hook: simulate a process restart by clearing the consulted-hashes set."""
    _session_consulted_hashes.clear()


def _wipe_legacy_flat_cache(data_path: str | Path) -> bool:
    """Remove pre-dual-cache `.haute_cache/json_<hash>.parquet` flat-layout artifacts.

    Pre-dual-cache, the cache was a single parquet at
    `.haute_cache/json_<hash>.parquet` with a sidecar `.meta.json`. The
    dual-cache migration policy is wipe-on-first-run: on the first
    dual-cache operation for a given data file, legacy artifacts get
    unlinked. Runtime execution continues directly from JSON; the optional
    Cache button can prewarm the new layout afterward.

    Returns True if anything was deleted (so callers can log).
    """
    cache_root = Path.cwd() / _CACHE_DIR
    legacy_stem = f"json_{_path_hash(data_path)}"
    artifacts = [
        cache_root / f"{legacy_stem}.parquet",
        cache_root / f"{legacy_stem}.parquet.meta.json",
        cache_root / f"{legacy_stem}.parquet.tmp",
        cache_root / f"{legacy_stem}.raw.parquet",
        cache_root / f"{legacy_stem}.raw.parquet.tmp",
    ]
    deleted = False
    for artifact in artifacts:
        if artifact.exists() and artifact.is_file():
            artifact.unlink()
            deleted = True
    if deleted:
        logger.info("legacy_flat_cache_wiped", data_path=str(data_path))
    return deleted


def _read_cache_meta(cache_dir: Path) -> dict[str, object] | None:
    """Read `meta.json` from a layer's `<hash>/` directory, or return None if absent."""
    meta_path = _json_cache_meta_path(cache_dir)
    if not meta_path.exists():
        return None
    payload = orjson.loads(meta_path.read_bytes())
    if not isinstance(payload, dict):
        raise ValueError(f"JSON cache metadata must be an object: {meta_path}")
    return cast(dict[str, object], payload)


def _read_cache_meta_lenient(cache_dir: Path) -> dict[str, object] | None:
    """Read a layer's ``meta.json`` for mirroring, treating
    a corrupt or non-dict payload as ``None`` (mismatch) rather than raising.

    ``_read_cache_meta`` fails loud on garbled metadata — correct for a cache
    we intend to *serve*. Mirroring instead needs to preserve a healthy
    committed layer when working metadata is bad, and repair a corrupt
    committed layer from healthy working state. Both cases degrade to stale
    exactly like :func:`haute._json_shred.is_per_port_cache_valid` (F307).
    """
    if not cache_dir.exists():
        return None
    try:
        return _read_cache_meta(cache_dir)
    except (OSError, ValueError):
        return None


def _maybe_meta_stat(p: Path) -> tuple[int, int]:
    """Return ``(st_mtime_ns, st_size)`` of *p*, or ``(0, 0)`` if absent.

    Used by :func:`cache_state_signature_for_graph` to compose the
    preview-cache fingerprint key without raising on missing files. Keying
    on nanosecond mtime *and* size (rather than a millisecond mtime bucket)
    disambiguates two ``meta.json`` rebuilds that land in the same
    millisecond — a coarser key would collide and leave the preview cache
    serving stale entries (F012).
    """
    try:
        st = p.stat()
    except OSError:
        return (0, 0)
    return (st.st_mtime_ns, st.st_size)


def cache_state_signature_for_graph(graph: Any) -> str:
    """Deterministic string capturing the JSON-cache state for every apiInput
    node in *graph*. Used as an extra fingerprint key so a JSON-cache mutation
    (build, delete, mirror to committed) invalidates affected preview-cache
    entries without thrashing unrelated ones.

    Per apiInput in the graph the signature contains: the node id, a short
    data-file-path hash, and the ``(mtime_ns, size)`` of the working and
    committed layers' ``meta.json`` sidecars. A missing sidecar contributes
    ``0:0``. Entries are sorted by node id for stability.

    Returns ``""`` when the graph has no apiInputs; the caller should pass
    the empty string verbatim or skip including it in the key.
    """
    from haute._types import NodeType

    parts: list[str] = []
    for node in sorted(graph.nodes, key=lambda n: n.id):
        if node.data.nodeType != NodeType.API_INPUT:
            continue
        data_path = node.data.config.get("path")
        if not isinstance(data_path, str) or not data_path:
            continue
        try:
            cache_hash = _path_hash(data_path)
        except (OSError, ValueError, RuntimeError):
            # RuntimeError: Path.expanduser() on an unresolvable ``~`` path
            # ('Could not determine home directory'). Skip any apiInput whose
            # path can't be hashed so the key stays total (F306).
            continue
        w_ns, w_size = _maybe_meta_stat(
            _json_cache_meta_path(_json_cache_dir(data_path, _LAYER_WORKING)),
        )
        c_ns, c_size = _maybe_meta_stat(
            _json_cache_meta_path(_json_cache_dir(data_path, _LAYER_COMMITTED)),
        )
        parts.append(f"{node.id}={cache_hash[:8]}:{w_ns}:{w_size}:{c_ns}:{c_size}")
    if not parts:
        return ""
    return "json_cache=" + "|".join(parts)


def clear_json_cache(
    data_path: str | Path,
    *,
    layer: str = _LAYER_WORKING,
) -> bool:
    """Delete cached parquet artifacts for a JSON data file in one layer.

    Default is the volatile working/ layer — used by the DELETE endpoint.
    Always wipes any pre-dual-cache flat-layout artifacts too.

    The consulted-hashes flag is intentionally NOT cleared. The user is
    still in the same process, so they remain authoritative for this
    data file. On the next save, :func:`mirror_cache_to_committed` sees
    consulted=True + working/ absent and propagates the absence to
    committed/.

    Returns True if anything was deleted.
    """
    _wipe_legacy_flat_cache(data_path)
    cache_dir = _json_cache_dir(data_path, layer)
    if not cache_dir.exists():
        return False
    shutil.rmtree(cache_dir)
    return True


def mirror_cache_to_committed(
    data_path: str | Path,
    v2_config: dict[str, Any],
) -> bool:
    """Promote `working/<hash>/` → `committed/<hash>/` on Save (DUAL_CACHE.md §4).

    Behaviour (the user's test plan governs):
      - If the current process has NOT cached this data file (i.e. not in
        ``_session_consulted_hashes``), this is a no-op. This guards against
        save inadvertently promoting a stale on-disk working/ from a
        previous session (cross-restart vulnerability mitigation).
      - If working/ exists: mirror it byte-for-byte into committed/ only when
        its top-level v2 identity is well formed, its recorded source signature
        still matches *data_path*, and every signed table artifact is intact.
        No-op trapdoor: skip the copy only when both manifests agree on schema,
        source-file identity, and signed table entries, and both layers' actual
        parquet bytes match those signatures. This also upgrades an old unsigned
        committed manifest and repairs externally damaged bytes.
      - If working/ does not exist: ensure committed/ also does not exist.

    Returns True if the on-disk committed/ state changed.
    """
    # Reuse the shred subsystem's per-cache build lock + Windows-safe atomic
    # swap so the mirror (a) serializes against a concurrent build of the SAME
    # working dir and (b) survives transient rename handle-locks on win32,
    # exactly like the shred's own publish path (F010). Imported lazily to
    # avoid an import cycle (`_json_shred` imports `_json_cache_dir` from here).
    import polars as pl

    from haute._api_input_schema import ApiInputSchemaError
    from haute._json_shred import (
        _build_lock_for,
        _cache_meta_matches_config_and_source,
        _cache_manifest_files_match,
        _emitting_table_specs,
        _probe_cache_bundle,
        _swap_dir_into_place,
        _unique_build_tmp_dir,
    )

    _wipe_legacy_flat_cache(data_path)
    if not _is_working_consulted(data_path):
        # Stale on-disk working/ from a previous session; or no cache ever.
        return False

    working_dir = _json_cache_dir(data_path, _LAYER_WORKING)
    committed_dir = _json_cache_dir(data_path, _LAYER_COMMITTED)

    # Hold the build lock across the whole read-meta + populate + swap so a
    # build of working_dir can't interleave with this promotion.
    with _build_lock_for(working_dir):
        if not working_dir.exists():
            if committed_dir.exists():
                shutil.rmtree(committed_dir)
                logger.info(
                    "json_cache_committed_cleared",
                    data_path=str(data_path),
                    committed_dir=str(committed_dir),
                )
                return True
            return False

        working_meta = _read_cache_meta_lenient(working_dir)
        committed_meta = _read_cache_meta_lenient(committed_dir)
        table_specs = ()
        working_valid = False
        try:
            table_specs = _emitting_table_specs(v2_config)
            if (
                working_meta is not None
                and table_specs
                and _cache_meta_matches_config_and_source(
                    cast(dict[str, Any], working_meta),
                    v2_config,
                    data_path=data_path,
                )
            ):
                _working_bundle, probe_failure = _probe_cache_bundle(
                    working_dir,
                    table_specs,
                    cast(dict[str, Any], working_meta),
                )
                working_valid = probe_failure is None
        except (ApiInputSchemaError, OSError, pl.exceptions.PolarsError):
            working_valid = False

        if not working_valid:
            logger.warning(
                "json_cache_working_invalid_not_mirrored",
                data_path=str(data_path),
                working_dir=str(working_dir),
                committed_dir=str(committed_dir),
            )
            return False
        if (
            committed_meta is not None
            and working_meta.get("schema_fingerprint") == committed_meta.get("schema_fingerprint")
            and working_meta.get("schema_mode") == committed_meta.get("schema_mode")
            and working_meta.get("data_file") == committed_meta.get("data_file")
            and working_meta.get("tables") == committed_meta.get("tables")
            and _cache_manifest_files_match(
                committed_dir,
                cast(dict[str, Any], committed_meta),
            )
        ):
            logger.info(
                "json_cache_save_noop",
                data_path=str(data_path),
                committed_dir=str(committed_dir),
            )
            return False

        # Atomic replacement: copytree into a `.tmp` sibling, then revalidate
        # that staged copy against the exact manifest captured above before
        # swapping. The source can be edited by another process despite our
        # process-local build lock; a mixed/partial copy must never replace a
        # healthy committed generation.
        committed_dir.parent.mkdir(parents=True, exist_ok=True)
        legacy_tmp_dir = committed_dir.with_name(committed_dir.name + ".tmp")
        if legacy_tmp_dir.exists():
            shutil.rmtree(legacy_tmp_dir)
        tmp_dir = _unique_build_tmp_dir(committed_dir)
        try:
            shutil.copytree(working_dir, tmp_dir)
        except BaseException:
            shutil.rmtree(tmp_dir, ignore_errors=True)
            raise
        staged_meta = _read_cache_meta_lenient(tmp_dir)
        staged_valid = False
        try:
            if staged_meta == working_meta:
                _staged_bundle, probe_failure = _probe_cache_bundle(
                    tmp_dir,
                    table_specs,
                    cast(dict[str, Any], working_meta),
                )
                # Recheck source identity after the copy and full staged probe,
                # immediately before publish. A source edit during either step
                # makes this generation stale and must preserve committed.
                staged_valid = (
                    probe_failure is None
                    and _cache_meta_matches_config_and_source(
                        cast(dict[str, Any], working_meta),
                        v2_config,
                        data_path=data_path,
                    )
                )
        except (OSError, pl.exceptions.PolarsError):
            staged_valid = False
        if not staged_valid:
            shutil.rmtree(tmp_dir, ignore_errors=True)
            logger.warning(
                "json_cache_staged_mirror_invalid_not_published",
                data_path=str(data_path),
                working_dir=str(working_dir),
                committed_dir=str(committed_dir),
            )
            return False
        _swap_dir_into_place(tmp_dir, committed_dir)
        logger.info(
            "json_cache_committed_mirrored",
            data_path=str(data_path),
            working_dir=str(working_dir),
            committed_dir=str(committed_dir),
        )
        return True
