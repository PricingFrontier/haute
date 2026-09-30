"""Provider dispatch for the canonical Data Input node.

This module is the only runtime branch on ``inputType``. Format-specific
Polars invocation stays in :mod:`haute._polars_io_registry`; generation
layout/publication stays in :mod:`haute._source_cache`.
"""

from __future__ import annotations

import threading
import weakref
from collections.abc import Callable, Iterator, Mapping
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import polars as pl

from haute._cache import canonical_json
from haute._execution_context import (
    ExecutionContext,
    ExecutionProfile,
    current_execution_context,
)
from haute._hashing import content_hash_bytes
from haute._json_shred._source_proof import file_signature
from haute._polars_io_registry import (
    PolarsIoConfigError,
    anchor_config_source_path,
    data_input_is_direct,
    format_for_config,
    read_polars_input,
    read_polars_input_for_snapshot,
    resolve_input_mode,
    scan_polars_input_for_schema,
    scanner_rejected_arguments,
    snapshot_input_plan,
    validate_data_input_config,
)
from haute._source_cache import (
    BuildClass,
    SourceCacheBuildContext,
    SourceCacheGeneration,
    SourceCacheIdentity,
    SourceCacheStore,
)


class InputSnapshotMissingError(PolarsIoConfigError):
    """A snapshot-backed input has no published generation to read."""


@dataclass(frozen=True, slots=True)
class InferredInputSchema:
    """How a Data Input's schema was inferred from its file for lack of a snapshot.

    ``inference_rows`` is the ``infer_schema_length`` the scanner received, or
    ``None`` when no type inference ran (file metadata or a declared schema).
    """

    format: str
    inference_rows: int | None


@dataclass(frozen=True, slots=True)
class DeclaredTableSchema:
    """An API Input table resolved from its declared contract for lack of a snapshot.

    ``column_count`` is the table's declared selected columns, whatever a
    consumer demanded.
    """

    column_count: int


@dataclass(frozen=True, slots=True)
class RecordedSchemaTiers:
    """The inputs a schema-only resolution resolved below the snapshot tier.

    ``inferred`` is keyed by Data Input node id; ``declared`` by API Input node
    id and table label.
    """

    inferred: dict[str, InferredInputSchema] = field(default_factory=dict)
    declared: dict[tuple[str, str], DeclaredTableSchema] = field(default_factory=dict)


_SCHEMA_TIERS: ContextVar[RecordedSchemaTiers | None] = ContextVar(
    "haute_schema_tiers", default=None
)

_PREVIEW_REMEDY = "Preview this input first, which builds its snapshot."


@contextmanager
def recording_schema_tiers() -> Iterator[RecordedSchemaTiers]:
    """Admit the inferred and declared schema tiers and collect what they resolved.

    Only a schema-only caller that reports the tiers opens this: without it a
    missing snapshot keeps its ``input_snapshot_missing`` rejection.
    """
    recorded = RecordedSchemaTiers()
    token = _SCHEMA_TIERS.set(recorded)
    try:
        yield recorded
    finally:
        _SCHEMA_TIERS.reset(token)


def schema_tier_recorder(schema_only: bool) -> RecordedSchemaTiers | None:
    """The active recorder for a schema-only resolution, else ``None``."""
    return _SCHEMA_TIERS.get() if schema_only else None


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
    return file_signature(path).source_signature


@dataclass(slots=True)
class _PolarsSnapshotBuilder:
    config: dict[str, Any]
    profile: ExecutionProfile
    build_class: BuildClass
    warning_code: str | None = None

    def build(self, context: SourceCacheBuildContext) -> pl.LazyFrame:
        context.checkpoint()
        frame, _warning_code = read_polars_input_for_snapshot(self.config)
        return frame


def _snapshot_builder(
    config: dict[str, Any],
    *,
    base_dir: str | Path | None,
    profile: ExecutionProfile,
    allow_admitted_eager: bool = False,
) -> tuple[object, BuildClass]:
    provider = config["inputType"]
    if provider in {"file", "lakehouse"}:
        anchored = _resolved_config_path(config, base_dir)
        _mode, build_class, warning_code = snapshot_input_plan(
            format_for_config(anchored), anchored
        )
        if (
            build_class == "admitted_eager"
            and not allow_admitted_eager
            and profile != ExecutionProfile.PREVIEW_EAGER
        ):
            raise PolarsIoConfigError(
                f"Format {anchored['format']!r} has an admitted-eager snapshot build; "
                "it cannot run in a bounded execution profile."
            )
        if build_class == "unsupported":
            raise PolarsIoConfigError(
                f"Format {anchored['format']!r} does not support snapshot builds."
            )
        return _PolarsSnapshotBuilder(anchored, profile, build_class, warning_code), build_class
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
    allow_admitted_eager: bool = False,
) -> BuildClass:
    """Return the effective build class without contacting the provider."""
    validated = validate_data_input_config(config)
    if data_input_is_direct(validated):
        raise PolarsIoConfigError("Direct Parquet Data Input does not support snapshot builds.")
    _, build_class = _snapshot_builder(
        validated,
        base_dir=base_dir,
        profile=profile,
        allow_admitted_eager=allow_admitted_eager,
    )
    return build_class


def input_snapshot_warning_code(
    config: Mapping[str, Any],
    *,
    base_dir: str | Path | None = None,
) -> str | None:
    """Return the build plan's warning code without contacting the provider."""
    validated = validate_data_input_config(config)
    if validated["inputType"] not in {"file", "lakehouse"}:
        return None
    anchored = _resolved_config_path(validated, base_dir)
    return snapshot_input_plan(format_for_config(anchored), anchored)[2]


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
    generation_id: str | None = None,
    staging_token: str | None = None,
    allow_admitted_eager: bool = False,
    defer_retirement: bool = False,
) -> SourceCacheGeneration:
    """Explicitly build or refresh one shared input snapshot.

    ``allow_admitted_eager`` is set by automatic preparation, whose build always
    runs inside a hard memory cap: containment comes from that cap rather than
    from the caller's execution profile.
    """
    validated = validate_data_input_config(config)
    if data_input_is_direct(validated):
        raise PolarsIoConfigError("Direct Parquet Data Input does not support snapshot builds.")
    identity = source_cache_identity(validated, base_dir=base_dir)
    builder, build_class = _snapshot_builder(
        validated,
        base_dir=base_dir,
        profile=profile,
        allow_admitted_eager=allow_admitted_eager,
    )
    context = SourceCacheBuildContext(
        profile=profile,
        build_class=build_class,
        cancellation=cancellation,  # type: ignore[arg-type]
        deadline=deadline,
        progress=progress,
        execution_context=execution_context,
        generation_id=generation_id,
        staging_token=staging_token,
        defer_retirement=defer_retirement,
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
    schema_only: bool = False,
    node_id: str | None = None,
) -> pl.LazyFrame:
    """Resolve canonical direct Parquet or an already-published snapshot.

    A schema-only resolution inside :func:`recording_schema_tiers` whose
    snapshot is missing resolves at the inferred schema tier instead, recorded
    under *node_id*.
    """
    validated = validate_data_input_config(config)
    if data_input_is_direct(validated):
        anchored = _resolved_config_path(validated, base_dir)
        return read_polars_input(anchored, profile=profile)

    cache_store = store or SourceCacheStore(_cache_root())
    try:
        return lease_input_generation(
            cache_store,
            source_cache_identity(validated, base_dir=base_dir),
            missing_message=(
                "input_snapshot_missing: This Data Input runs from a snapshot "
                "that has not been built yet. Build the snapshot (or run a "
                "preview, which builds it automatically) and try again."
            ),
        )
    except InputSnapshotMissingError:
        recorder = schema_tier_recorder(schema_only)
        if recorder is None:
            raise
    if node_id is None:
        raise ValueError("An inferred Data Input schema is recorded by node id; pass node_id.")
    frame, inferred = _infer_input_schema(validated, base_dir=base_dir)
    recorder.inferred[node_id] = inferred
    return frame


def _not_inferable(reason: str) -> InputSnapshotMissingError:
    return InputSnapshotMissingError(
        "input_snapshot_missing: This Data Input has no snapshot yet, and its schema "
        f"cannot be inferred from its file because {reason}. {_PREVIEW_REMEDY}"
    )


def _infer_input_schema(
    config: dict[str, Any],
    *,
    base_dir: str | Path | None,
) -> tuple[pl.LazyFrame, InferredInputSchema]:
    """Scan a local file input's schema without a snapshot, or refuse with the remedy."""
    provider = str(config["inputType"])
    if provider != "file":
        article = "an" if provider[0] in "aeiou" else "a"
        raise _not_inferable(f"{article} {provider} input has no local file to scan")
    anchored = _resolved_config_path(config, base_dir)
    fmt = format_for_config(anchored)
    if fmt.scanner is None:
        raise _not_inferable(f"format {fmt.name!r} reads only eagerly")
    rejected = scanner_rejected_arguments(fmt, anchored)
    if rejected:
        raise _not_inferable(f"only the eager reader accepts its argument(s) {rejected}")
    if not Path(str(anchored["path"])).is_file():
        raise PolarsIoConfigError(f"Data Input file {config['path']!r} does not exist.")
    frame, inference_rows = scan_polars_input_for_schema(anchored)
    return frame, InferredInputSchema(format=fmt.name, inference_rows=inference_rows)


def lease_input_generation(
    store: SourceCacheStore,
    identity: SourceCacheIdentity,
    *,
    missing_message: str,
) -> pl.LazyFrame:
    """Scan the current generation of *identity*, leased until execution cleanup.

    Inside an execution context the lease is released by the context's
    cleanup, after collection; outside one, the returned plan owns it. A
    missing generation raises ``InputSnapshotMissingError`` with *missing_message*.
    """
    lease = store.lease(identity)
    try:
        generation = lease.__enter__()
    except FileNotFoundError:
        raise InputSnapshotMissingError(missing_message) from None
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
    project_root: str | Path | None = None,
) -> pl.LazyFrame:
    """Load a sidecar and resolve its input with project-relative data paths.

    Generated pipelines can live below the Haute project root while Data Input
    paths are selected relative to that root. Resolve a configured path once
    before handing it to the direct/snapshot provider so standalone generated
    code uses the same anchor as canvas execution.
    """
    from haute._config_io import load_node_config

    base = _base_path(base_dir)
    config = load_node_config(config_path, base_dir=base)
    configured_path = config.get("path")
    if project_root is not None and isinstance(configured_path, (str, Path)) and configured_path:
        from haute._path_resolution import resolve_runtime_file_path

        config = dict(config)
        config["path"] = str(
            resolve_runtime_file_path(
                configured_path,
                pipeline_dir=base,
                project_root=project_root,
                prefer="project",
                enforce_project_root=True,
            )
        )
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
