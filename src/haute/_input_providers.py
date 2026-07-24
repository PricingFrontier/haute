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

from haute._execution_context import (
    ExecutionContext,
    ExecutionProfile,
    current_execution_context,
)
from haute._hashing import content_hash
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

    ``code`` and ``cacheMode`` intentionally do not participate: they affect
    post-read execution and generation selection, not the source bytes.
    """
    validated = validate_data_input_config(config)
    provider = str(validated["inputType"])
    if provider == "inline":
        raise PolarsIoConfigError("Inline Data Input does not support snapshots.")

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
    else:
        from haute._databricks_io import _canonical_table, _validate_select_clause

        query = validated.get("query")
        if query:
            _validate_select_clause(str(query))
        descriptor = {
            "http_path": str(validated["http_path"]),
            "table": _canonical_table(str(validated["table"])),
            "query": str(query).strip() if query else None,
            "arguments": dict(validated.get("arguments") or {}),
            "host_ref": "DATABRICKS_HOST",
            "token_ref": "DATABRICKS_TOKEN",
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
    raise PolarsIoConfigError("Inline Data Input does not support snapshots.")


def input_snapshot_build_class(
    config: Mapping[str, Any],
    *,
    base_dir: str | Path | None = None,
    profile: ExecutionProfile = ExecutionProfile.LAZY_SINK,
) -> BuildClass:
    """Return the declared build class without contacting the provider."""
    validated = validate_data_input_config(config)
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
    if validated.get("cacheMode") != "snapshot":
        raise PolarsIoConfigError("Snapshot build requires cacheMode 'snapshot'.")
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
    """Resolve direct input or an already-published snapshot.

    Snapshot execution never builds and never contacts the provider.
    """
    validated = validate_data_input_config(config)
    if validated["cacheMode"] == "snapshot":
        cache_store = store or SourceCacheStore(_cache_root())
        identity = source_cache_identity(validated, base_dir=base_dir)
        lease = cache_store.lease(identity)
        generation = lease.__enter__()
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
            weakref.finalize(frame, release_lease)
        return frame

    provider = validated["inputType"]
    if provider not in {"file", "lakehouse", "inline"}:
        raise PolarsIoConfigError(f"{provider.title()} input cannot execute directly.")
    anchored = (
        _resolved_config_path(validated, base_dir)
        if provider in {"file", "lakehouse"}
        else validated
    )
    return read_polars_input(anchored, profile=profile)


def resolve_data_input_from_config(
    config_path: str | Path,
    *,
    base_dir: str | Path | None = None,
    profile: ExecutionProfile | str | None = None,
) -> pl.LazyFrame:
    """Load a generated sidecar and resolve its direct or cached provider."""
    from haute._config_io import load_node_config

    base = _base_path(base_dir)
    config = load_node_config(config_path, base_dir=base)
    return resolve_data_input(config, base_dir=base, profile=profile)
