"""API-input tables as shared input snapshots.

Each emitting table of a structured (JSON, JSONL, NDJSON, XML) API Input is one
input-snapshot identity in the shared store (``haute._source_cache``). Its
descriptor names the resolved source file, that table's own specification and
the shred-semantics version; sibling tables and the port label stay out of it,
so editing one table leaves the others' generations current.

A build shreds the source once into a private scratch directory, then publishes
every requested table through the store's ordinary build, which rewrites each
table's Parquet as bounded part files and validates it before its pointer moves.
"""

from __future__ import annotations

import hashlib
import shutil
import time
import uuid
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Final

import polars as pl

from haute._api_input_schema import sanitise_label_for_filesystem
from haute._cache import canonical_json
from haute._execution_context import ExecutionContext, ExecutionProfile
from haute._json_shred import _records, _shred, _source_proof, _writer
from haute._json_shred._shred import _EmittingTableSpec
from haute._logging import get_logger
from haute._source_cache import (
    SourceCacheBuildContext,
    SourceCacheGeneration,
    SourceCacheIdentity,
    SourceCacheStatus,
    SourceCacheStore,
    new_staging_token,
)

logger = get_logger(component="json_shred")

API_INPUT_PROVIDER: Final = "api_input"
# Raised whenever a change to the shred walk or the frame it writes would make
# an existing generation disagree with a fresh build of the same table.
SHRED_SEMANTICS_VERSION: Final = 1
# The scratch directory one build shreds into. Its children are named like
# staging directories so the store's stale-staging sweep reclaims a build that
# died, and its leading dot keeps it out of the cache inventory.
_SCRATCH_DIRNAME = ".shred"


class SourceChangedDuringCacheBuildError(RuntimeError):
    """The structured source changed while its tables were being built."""


@dataclass(frozen=True, slots=True)
class ApiInputTable:
    """One emitting table and the store identity its data is published under."""

    label: str
    spec: _EmittingTableSpec
    identity: SourceCacheIdentity


@dataclass(frozen=True, slots=True)
class ApiInputSnapshotSource:
    """A structured API Input's source file and its emitting tables, in schema order."""

    data_path: Path
    config: Mapping[str, Any]
    tables: tuple[ApiInputTable, ...]

    def table(self, label: str) -> ApiInputTable:
        for table in self.tables:
            if table.label == label:
                return table
        raise KeyError(f"API Input has no emitting table {label!r}")

    @property
    def group_digest(self) -> str:
        """One digest for the node's whole set of tables, keying node-level work."""
        payload = {
            "path": str(self.data_path),
            "tables": sorted({table.identity.digest for table in self.tables}),
        }
        return hashlib.sha256(canonical_json(payload).encode("utf-8")).hexdigest()


def _table_descriptor(data_path: Path, table: Mapping[str, Any]) -> dict[str, object]:
    return {
        "path": str(data_path),
        "table": {
            "path": table["path"],
            "columns": [
                [column["name"], column["path"], column["type"]]
                for column in table.get("columns") or []
                if column.get("selected")
            ],
        },
        "shred_version": SHRED_SEMANTICS_VERSION,
    }


def api_input_snapshot_source(
    config: Mapping[str, Any],
    data_path: str | Path,
) -> ApiInputSnapshotSource:
    """Validate *config* and name the store identity of each emitting table.

    *data_path* is the source file, already anchored by the caller exactly as
    execution anchors it.
    """
    resolved = Path(data_path).resolve()
    specs = _shred._emitting_table_specs(dict(config))
    emitting = [table for table in config["tables"] if _shred.table_is_emitting(table)]
    tables = tuple(
        ApiInputTable(
            label=spec.label,
            spec=spec,
            identity=SourceCacheIdentity(
                provider=API_INPUT_PROVIDER,
                descriptor=_table_descriptor(resolved, table),
            ),
        )
        for spec, table in zip(specs, emitting, strict=True)
    )
    return ApiInputSnapshotSource(data_path=resolved, config=config, tables=tables)


def api_input_source_signature(data_path: str | Path) -> str:
    """The content proof a table's generation records, or ``"missing"``.

    The SHA-256 of the whole file, memoised in-process against the file's
    native revision, so an unchanged source is hashed once per process.
    """
    path = Path(data_path)
    if not path.is_file():
        return "missing"
    signature = _source_proof._data_file_signature(path)
    return f"sha256:{signature['sha256']}:{signature['size']}"


def freshness_signature(source_signature: str | None) -> str | None:
    """The signature a table's freshness is judged against.

    A missing source proves nothing about a published table: its freshness is
    unknown, so the table stays usable (preparation reports it
    ``source_unavailable``) instead of turning stale and unbuildable.
    """
    return None if source_signature == "missing" else source_signature


def api_input_table_statuses(
    source: ApiInputSnapshotSource,
    store: SourceCacheStore,
    *,
    source_signature: str | None,
) -> tuple[tuple[ApiInputTable, SourceCacheStatus], ...]:
    """Each emitting table's store status, judged against *source_signature*."""
    signature = freshness_signature(source_signature)
    return tuple(
        (table, store.status(table.identity, source_signature=signature)) for table in source.tables
    )


@dataclass(frozen=True, slots=True)
class TableBuildPlan:
    """The parent-chosen generation and staging a supervised build publishes."""

    generation_id: str
    staging_token: str


def new_table_build_plans(
    source: ApiInputSnapshotSource, labels: Sequence[str]
) -> dict[str, TableBuildPlan]:
    """Fresh plans for the distinct identities of *labels*, keyed by identity digest."""
    return {
        source.table(label).identity.digest: TableBuildPlan(
            generation_id=str(uuid.uuid4()), staging_token=new_staging_token()
        )
        for label in labels
    }


@dataclass(slots=True)
class _ParquetFileBuilder:
    """Hand the store one shredded table, which it rewrites as bounded parts."""

    path: Path
    build_class: str = "bounded"

    def build(self, context: SourceCacheBuildContext) -> pl.LazyFrame:
        context.checkpoint()
        return pl.scan_parquet(self.path)


def _distinct_tables(source: ApiInputSnapshotSource, labels: Sequence[str]) -> list[ApiInputTable]:
    """The tables named by *labels*, one per identity, in schema order."""
    wanted = set(labels)
    seen: set[str] = set()
    distinct: list[ApiInputTable] = []
    for table in source.tables:
        if table.label not in wanted or table.identity.digest in seen:
            continue
        seen.add(table.identity.digest)
        distinct.append(table)
    missing = wanted - {table.label for table in source.tables}
    if missing:
        raise KeyError(f"API Input has no emitting table(s) {sorted(missing)!r}")
    return distinct


def _shred_into(
    source: ApiInputSnapshotSource,
    tables: Sequence[ApiInputTable],
    scratch: Path,
) -> None:
    specs = tuple(table.spec for table in tables)
    data_path = source.data_path
    config = dict(source.config)
    ranges = (
        _records._jsonl_byte_ranges(data_path, _records._PARALLEL_CHUNK_BYTES)
        if _records._should_shred_in_parallel(data_path)
        else []
    )
    if len(ranges) > 1:
        skip_stats = _writer._write_tables_in_parallel(data_path, config, specs, scratch, ranges)
    else:
        skip_stats = _writer._write_tables_streaming(data_path, config, specs, scratch)
    if skip_stats.total:
        logger.warning(
            "json_shred_records_skipped",
            data_path=str(data_path),
            skipped_records=skip_stats.skipped_records,
            skipped_rows_by_table=skip_stats.skipped_rows_by_table,
        )


def scratch_directory(store: SourceCacheStore, token: str) -> Path:
    """The private directory one build shreds into."""
    return store.inputs_root / _SCRATCH_DIRNAME / f".staging-{token}"


def build_api_input_tables(
    source: ApiInputSnapshotSource,
    labels: Sequence[str],
    *,
    store: SourceCacheStore,
    profile: ExecutionProfile,
    cancellation: Callable[[], bool] | None = None,
    deadline: float | None = None,
    execution_context: ExecutionContext | None = None,
    plans: Mapping[str, TableBuildPlan] | None = None,
    scratch_token: str | None = None,
    defer_retirement: bool = False,
) -> dict[str, SourceCacheGeneration]:
    """Shred the source once and publish a fresh generation of each table in *labels*.

    Returns the published generations keyed by identity digest. A table that
    shares its identity with another is built once. *plans*, when given, names
    the generation and staging directory of every identity, so a supervising
    parent can settle exactly this build after a worker dies.
    """
    tables = _distinct_tables(source, labels)
    if not tables:
        return {}
    if plans is not None and set(plans) != {table.identity.digest for table in tables}:
        raise ValueError("build plans must name exactly the identities being built")
    signature = api_input_source_signature(source.data_path)
    if signature == "missing":
        raise FileNotFoundError(f"API Input source file does not exist: {source.data_path}")
    scratch = scratch_directory(store, scratch_token or new_staging_token())
    scratch.parent.mkdir(parents=True, exist_ok=True)
    scratch.mkdir()
    started = time.monotonic()
    try:
        if execution_context is not None:
            with execution_context.stage("api_input_shred"):
                _shred_into(source, tables, scratch)
        else:
            _shred_into(source, tables, scratch)
        if api_input_source_signature(source.data_path) != signature:
            raise SourceChangedDuringCacheBuildError(
                f"structured source changed while its tables were built: {source.data_path}"
            )
        generations: dict[str, SourceCacheGeneration] = {}
        for table in tables:
            plan = None if plans is None else plans[table.identity.digest]
            context = SourceCacheBuildContext(
                profile=profile,
                build_class="bounded",
                cancellation=cancellation,
                deadline=deadline,
                execution_context=execution_context,
                generation_id=None if plan is None else plan.generation_id,
                staging_token=None if plan is None else plan.staging_token,
                defer_retirement=defer_retirement,
            )
            generations[table.identity.digest] = store.build(
                table.identity,
                _ParquetFileBuilder(
                    scratch / f"{sanitise_label_for_filesystem(table.label)}.parquet"
                ),
                context=context,
                source_signature=signature,
                refresh=True,
            )
        logger.info(
            "api_input_tables_built",
            data_path=str(source.data_path),
            table_count=len(generations),
            elapsed_seconds=round(time.monotonic() - started, 3),
        )
        return generations
    finally:
        shutil.rmtree(scratch, ignore_errors=True)


@dataclass(frozen=True, slots=True)
class ApiInputBuildRequest:
    """Picklable request for a supervised build of some of an API Input's tables."""

    config: dict[str, Any]
    data_path: str
    labels: tuple[str, ...]
    cache_root: str
    project_root: str
    profile: ExecutionProfile
    plans: dict[str, TableBuildPlan]
    scratch_token: str


@dataclass(frozen=True, slots=True)
class ApiInputBuildOutcome:
    """Picklable outcome: each published identity's generation id."""

    generation_ids: dict[str, str]


def build_api_input_tables_worker(
    request: ApiInputBuildRequest,
    budget: Any,
) -> ApiInputBuildOutcome:
    """Spawn entrypoint: build the requested tables under the child's own hard cap."""
    from haute._execution_admission import create_isolated_execution_context
    from haute._sandbox import set_project_root

    set_project_root(Path(request.project_root))
    context: ExecutionContext | None = None
    try:
        context = create_isolated_execution_context(budget)
        context.checkpoint(label="api_input_snapshot_build")
        source = api_input_snapshot_source(request.config, request.data_path)
        generations = build_api_input_tables(
            source,
            request.labels,
            store=SourceCacheStore(request.cache_root),
            profile=request.profile,
            execution_context=context,
            plans=request.plans,
            scratch_token=request.scratch_token,
            defer_retirement=True,
        )
        return ApiInputBuildOutcome(
            generation_ids={
                digest: generation.generation_id for digest, generation in generations.items()
            }
        )
    finally:
        if context is not None:
            context.release_admission(preserve_primary_error=True)


def run_supervised_api_input_build(
    source: ApiInputSnapshotSource,
    labels: Sequence[str],
    *,
    store: SourceCacheStore,
    profile: ExecutionProfile,
    budget: Any,
    worker_config: Any,
    spawn: Callable[..., Any],
) -> dict[str, SourceCacheGeneration]:
    """Build *labels* in a spawned worker and settle exactly that build afterwards.

    The parent chooses every generation id and staging token, so after a
    worker failure or death it reconciles each one: a generation the child
    already published stays current, and anything unpublished is removed
    without touching a previous current generation. The scratch directory is
    removed either way. When every table was published before the worker
    stopped the build counts as done; otherwise the worker's failure is raised.
    """
    from haute._sandbox import _get_project_root

    tables = _distinct_tables(source, labels)
    plans = new_table_build_plans(source, [table.label for table in tables])
    scratch_token = new_staging_token()
    request = ApiInputBuildRequest(
        config=dict(source.config),
        data_path=str(source.data_path),
        labels=tuple(table.label for table in tables),
        cache_root=str(store.root),
        project_root=str(_get_project_root()),
        profile=profile,
        plans=plans,
        scratch_token=scratch_token,
    )
    by_digest = {table.identity.digest: table for table in tables}
    try:
        spawn(build_api_input_tables_worker, request, budget, config=worker_config)
    except BaseException as exc:
        settled = {
            digest: store.reconcile_unpublished(
                by_digest[digest].identity, plan.generation_id, plan.staging_token
            )
            for digest, plan in plans.items()
        }
        shutil.rmtree(scratch_directory(store, scratch_token), ignore_errors=True)
        # Settle first so nothing is left behind, but never convert a base
        # exception (an interrupt, a system exit) into this build's success.
        if not isinstance(exc, Exception) or any(
            outcome != "published" for outcome in settled.values()
        ):
            raise
    generations = {digest: store.open_generation(by_digest[digest].identity) for digest in plans}
    # The child deferred retirement; retire here, where this process's own
    # lease counts are visible.
    for table in tables:
        store.retire_unleased(table.identity)
    return generations
