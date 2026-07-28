"""Provider dispatch for the canonical Data Input node.

This module is the only runtime branch on ``inputType``. Format-specific
Polars invocation stays in :mod:`haute._polars_io_registry`; generation
layout/publication stays in :mod:`haute._source_cache`.
"""

from __future__ import annotations

import threading
import weakref
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import polars as pl

from haute._cache import canonical_json
from haute._execution_context import (
    ExecutionContext,
    ExecutionProfile,
    current_execution_context,
)
from haute._hashing import content_hash, content_hash_bytes
from haute._polars_io_registry import (
    PolarsIoConfigError,
    _snapshot_build,
    anchor_config_source_path,
    format_for_config,
    read_polars_input,
    resolve_input_mode,
    validate_data_input_config,
)
from haute._source_cache import (
    BuildClass,
    SourceCacheBuildContext,
    SourceCacheGeneration,
    SourceCacheIdentity,
    SourceCacheStore,
)


def _base_path(base_dir: str | Path | None) -> Path:
    return Path(base_dir).resolve() if base_dir is not None else Path.cwd().resolve()


def _cache_root() -> Path:
    from haute._sandbox import _get_project_root

    return _get_project_root().resolve()


def _resolved_config_path(config: Mapping[str, Any], base_dir: str | Path | None) -> dict[str, Any]:
    return anchor_config_source_path(config, _base_path(base_dir))


def source_cache_identity(
    config: Mapping[str, Any],
    *,
    base_dir: str | Path | None = None,
) -> SourceCacheIdentity:
    """Build the redacted identity of one external source.

    ``code`` does not participate because it runs after the snapshot opens.
    This is only used by snapshot-backed inputs; canonical direct Parquet
    inputs bypass the cache entirely.
    """
    validated = validate_data_input_config(config)
    provider = str(validated["inputType"])
    descriptor: dict[str, object]
    if provider in {"file", "lakehouse"}:
        anchored = _resolved_config_path(validated, base_dir)
        fmt = format_for_config(anchored)
        descriptor = {
            "format": fmt.name,
            "mode": resolve_input_mode(fmt, anchored),
            "path": str(Path(str(anchored["path"])).resolve()),
            "arguments": dict(anchored.get("arguments") or {}),
        }
    elif provider == "database":
        from haute._database_io import canonical_database_locator, validate_read_query

        descriptor = {
            "format": "database",
            "query": validate_read_query(str(validated["query"])),
            "arguments": dict(validated.get("arguments") or {}),
        }
        if "connection" in validated:
            descriptor["connection"] = str(validated["connection"])
        else:
            uri = str(validated["uri"])
            descriptor["uri"] = canonical_database_locator(uri, base_dir=base_dir)
    elif provider == "databricks":
        from haute._databricks_io import _canonical_table, _validate_select_clause

        query = validated.get("query")
        if query:
            _validate_select_clause(str(query))
        descriptor = {
            "http_path": str(validated["http_path"]),
            "table": _canonical_table(str(validated["table"])),
            "query": str(query).strip() if query else None,
            "host_ref": "DATABRICKS_HOST",
            "token_ref": "DATABRICKS_TOKEN",
        }
    else:
        # Inline records are source material, but must never be persisted in
        # cache identity metadata. Hash the complete source-semantic payload
        # and retain only a non-sensitive summary in the descriptor.
        inline_source = {
            "format": validated["format"],
            "mode": resolve_input_mode(format_for_config(validated), validated),
            "arguments": dict(validated.get("arguments") or {}),
            "records": validated["records"],
        }
        descriptor = {
            "format": str(validated["format"]),
            "content_digest": content_hash_bytes(canonical_json(inline_source).encode("utf-8")),
            "row_count": len(validated["records"]),
        }
    return SourceCacheIdentity(provider=provider, descriptor=descriptor)


def source_signature(
    config: Mapping[str, Any],
    *,
    base_dir: str | Path | None = None,
) -> str | None:
    """Return a verified local-file signature when the provider has one."""
    validated = validate_data_input_config(config)
    # Lakehouse locators are tables/directories whose freshness needs a
    # provider version token. Treating a directory as a missing file made an
    # unchanged string falsely report "fresh" forever.
    if validated["inputType"] != "file":
        return None
    anchored = _resolved_config_path(validated, base_dir)
    path = Path(str(anchored["path"]))
    if not path.is_file():
        return "missing"
    stat = path.stat()
    return f"xxh64:{content_hash(path)}:{stat.st_size}"


@dataclass(slots=True)
class _PolarsSnapshotBuilder:
    config: dict[str, Any]
    profile: ExecutionProfile
    build_class: BuildClass

    def build(self, context: SourceCacheBuildContext) -> pl.LazyFrame:
        context.checkpoint()
        return read_polars_input(self.config, profile=self.profile)


def _snapshot_builder(
    config: dict[str, Any],
    *,
    base_dir: str | Path | None,
    profile: ExecutionProfile,
) -> tuple[object, BuildClass]:
    provider = config["inputType"]
    if provider in {"file", "lakehouse"}:
        anchored = _resolved_config_path(config, base_dir)
        build_class = _snapshot_build(format_for_config(anchored))
        if build_class == "admitted_eager" and profile != ExecutionProfile.PREVIEW_EAGER:
            raise PolarsIoConfigError(
                f"Format {anchored['format']!r} has an admitted-eager snapshot build; "
                "it cannot run in a bounded execution profile."
            )
        if build_class == "unsupported":
            raise PolarsIoConfigError(
                f"Format {anchored['format']!r} does not support snapshot builds."
            )
        return _PolarsSnapshotBuilder(anchored, profile, build_class), build_class
    if provider == "database":
        from haute._database_io import DatabaseSnapshotBuilder

        return DatabaseSnapshotBuilder(config, base_dir=base_dir), "bounded"
    if provider == "databricks":
        from haute._databricks_io import DatabricksSnapshotBuilder

        return DatabricksSnapshotBuilder(config), "bounded"
    if provider == "inline":
        return _PolarsSnapshotBuilder(config, profile, "bounded"), "bounded"
    raise PolarsIoConfigError(f"Unsupported Data Input provider {provider!r}.")


def input_snapshot_build_class(
    config: Mapping[str, Any],
    *,
    base_dir: str | Path | None = None,
    profile: ExecutionProfile = ExecutionProfile.LAZY_SINK,
) -> BuildClass:
    """Return the declared build class without contacting the provider."""
    validated = validate_data_input_config(config)
    if validated["cacheMode"] == "direct":
        raise PolarsIoConfigError("Direct Parquet Data Input does not support snapshot builds.")
    _, build_class = _snapshot_builder(validated, base_dir=base_dir, profile=profile)
    return build_class


def build_input_snapshot(
    config: Mapping[str, Any],
    *,
    store: SourceCacheStore,
    base_dir: str | Path | None = None,
    profile: ExecutionProfile = ExecutionProfile.LAZY_SINK,
    refresh: bool = False,
    cancellation: object = None,
    deadline: float | None = None,
    progress: Callable[[int], None] | None = None,
    execution_context: ExecutionContext | None = None,
) -> SourceCacheGeneration:
    """Explicitly build or refresh one shared input snapshot."""
    validated = validate_data_input_config(config)
    if validated["cacheMode"] == "direct":
        raise PolarsIoConfigError("Direct Parquet Data Input does not support snapshot builds.")
    identity = source_cache_identity(validated, base_dir=base_dir)
    builder, build_class = _snapshot_builder(validated, base_dir=base_dir, profile=profile)
    context = SourceCacheBuildContext(
        profile=profile,
        build_class=build_class,
        cancellation=cancellation,  # type: ignore[arg-type]
        deadline=deadline,
        progress=progress,
        execution_context=execution_context,
    )
    return store.build(
        identity,
        builder,  # type: ignore[arg-type]
        context=context,
        source_signature=source_signature(validated, base_dir=base_dir),
        refresh=refresh,
    )


def resolve_data_input(
    config: Mapping[str, Any],
    *,
    store: SourceCacheStore | None = None,
    base_dir: str | Path | None = None,
    profile: ExecutionProfile | str | None = None,
) -> pl.LazyFrame:
    """Resolve canonical direct Parquet or an already-published snapshot."""
    validated = validate_data_input_config(config)
    if validated["cacheMode"] == "direct":
        anchored = _resolved_config_path(validated, base_dir)
        return read_polars_input(anchored, profile=profile)

    cache_store = store or SourceCacheStore(_cache_root())
    identity = source_cache_identity(validated, base_dir=base_dir)
    lease = cache_store.lease(identity)
    try:
        generation = lease.__enter__()
    except FileNotFoundError:
        raise PolarsIoConfigError(
            "input_snapshot_missing: This Data Input runs from a snapshot "
            "that has not been built yet. Build the snapshot (or run a "
            "preview, which builds it automatically) and try again."
        ) from None
    release_lock = threading.Lock()
    released = False

    def release_lease() -> None:
        nonlocal released
        with release_lock:
            if released:
                return
            released = True
        lease.__exit__(None, None, None)

    frame = generation.lazy_frame
    execution_context = current_execution_context()
    if execution_context is not None:
        try:
            execution_context.add_cleanup(release_lease)
        except BaseException:
            release_lease()
            raise
    else:
        frame = frame.map_batches(
            _SnapshotLeasePlan(release_lease),
            streamable=True,
        )
    return frame


def resolve_data_input_from_config(
    config_path: str | Path,
    *,
    base_dir: str | Path | None = None,
    profile: ExecutionProfile | str | None = None,
) -> pl.LazyFrame:
    """Load a generated sidecar and resolve its direct or snapshot-backed input."""
    from haute._config_io import load_node_config

    base = _base_path(base_dir)
    config = load_node_config(config_path, base_dir=base)
    return resolve_data_input(config, base_dir=base, profile=profile)


class _SnapshotLeasePlan:
    """Identity plan callback whose lifetime owns the fallback snapshot lease.

    This fallback is used only outside an ``ExecutionContext``. LazyFrames do
    not cross the worker protocol, so the module-level callable need not be
    serialised; lease release remains intentionally tied to local plan GC.
    """

    __slots__ = ("_finalizer", "__weakref__")

    def __init__(self, release: Callable[[], None]) -> None:
        self._finalizer = weakref.finalize(self, release)

    def __call__(self, batch: pl.DataFrame) -> pl.DataFrame:
        """Preserve the batch while keeping this lease owner in the plan."""
        return batch
