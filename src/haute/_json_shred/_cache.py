"""Per-port cache lifecycle: prepare/commit/discard builds, manifest and bundle
validation, cache loading, and the runtime apiInput source loader."""

from __future__ import annotations

import stat as stat_module
from collections.abc import Mapping
from contextlib import AbstractContextManager, nullcontext
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import orjson
import polars as pl

from haute._api_input_schema import (
    ApiInputSchemaError,
)
from haute._api_input_schema import (
    sanitise_label_for_filesystem as _sanitise_label,
)
from haute._execution_context import (
    ExecutionCacheProofMissReason,
    current_execution_context,
)
from haute._json_shred import (
    _publication,
    _records,
    _runtime_storage,
    _shred,
    _source_proof,
    _writer,
)
from haute._json_shred._publication import _META_FILENAME
from haute._json_shred._shred import _EmittingTableSpec
from haute._logging import get_logger

logger = get_logger(component="json_shred")


class SourceChangedDuringCacheBuildError(RuntimeError):
    """The structured source no longer matches the generation a worker staged."""


@dataclass(frozen=True, slots=True)
class PreparedPerPortCacheBuild:
    """Pickle-safe evidence for one complete but not-yet-visible generation."""

    data_path: str
    cache_dir: str
    staging_dir: str | None  # pragma: no mutate
    schema_fingerprint: str
    data_file_signature: dict[str, Any]
    summary: dict[str, Any]
    no_op: bool = False


@dataclass(frozen=True)
class _CacheProbeFailure:
    reason: str
    label: str | None = None  # pragma: no mutate
    expected_schema: pl.Schema | None = None  # pragma: no mutate
    actual_schema: pl.Schema | None = None  # pragma: no mutate


def _cache_manifest_structure_failure(
    meta: dict[str, Any],
    *,  # pragma: no mutate
    expected_labels: tuple[str, ...] | None = None,  # pragma: no mutate
) -> _CacheProbeFailure | None:  # pragma: no mutate
    """Validate signed table entries and their derived parquet names.

    Manifest ``parquet`` values are checked for consistency but never trusted
    as paths: every artifact name is derived from its validated table label.
    Missing, extra, duplicate, or unsigned entries make the candidate
    unusable. Artifact bytes are deliberately checked by the caller: runtime
    probes must verify the exact payload they subsequently hand to Polars.
    """
    raw_tables = meta.get("tables")
    if not isinstance(raw_tables, list):
        return _CacheProbeFailure("malformed_manifest")

    entries_by_label: dict[str, dict[str, Any]] = {}
    seen_casefolded: set[str] = set()
    for raw_entry in raw_tables:
        if not isinstance(raw_entry, dict):
            return _CacheProbeFailure("malformed_manifest")
        label = raw_entry.get("label")
        if not isinstance(label, str) or not label:
            return _CacheProbeFailure("malformed_manifest")
        folded = label.casefold()
        if folded in seen_casefolded:
            return _CacheProbeFailure("duplicate_manifest_table", label=label)
        seen_casefolded.add(folded)
        entries_by_label[label] = raw_entry

    if expected_labels is not None:
        expected_set = set(expected_labels)
        actual_set = set(entries_by_label)
        if actual_set != expected_set:
            missing = next((label for label in expected_labels if label not in actual_set), None)
            extra = next((label for label in entries_by_label if label not in expected_set), None)
            return _CacheProbeFailure("manifest_table_mismatch", label=missing or extra)

    for label, entry in entries_by_label.items():
        signature = entry.get("content_signature")
        signature_parts = _source_proof._content_signature_parts(signature)
        if signature_parts is None:
            return _CacheProbeFailure("missing_content_signature", label=label)
        filename = f"{_sanitise_label(label)}.parquet"
        if entry.get("parquet") != filename:
            return _CacheProbeFailure("manifest_parquet_name_mismatch", label=label)
    return None


def _cache_manifest_failure(
    cache_dir: Path,
    meta: dict[str, Any],
    *,  # pragma: no mutate
    expected_labels: tuple[str, ...] | None = None,  # pragma: no mutate
) -> _CacheProbeFailure | None:  # pragma: no mutate
    """Validate one manifest and every path-backed artifact it signs."""
    structure_failure = _cache_manifest_structure_failure(
        meta,
        expected_labels=expected_labels,
    )
    if structure_failure is not None:
        return structure_failure

    for entry in meta["tables"]:
        label = entry["label"]
        signature = entry["content_signature"]
        signature_parts = _source_proof._content_signature_parts(signature)
        assert signature_parts is not None
        parquet_path = cache_dir / f"{_sanitise_label(label)}.parquet"
        if not parquet_path.exists():
            return _CacheProbeFailure("missing_frame", label=label)
        if not _source_proof._file_content_matches(signature, parquet_path):
            return _CacheProbeFailure("content_signature_mismatch", label=label)
    return None


def _cache_manifest_files_match(cache_dir: Path, meta: dict[str, Any]) -> bool:
    """Return whether a self-contained cache manifest matches its artifacts."""
    return _cache_manifest_failure(cache_dir, meta) is None


def _cache_bundle_failure_in_place(
    cache_dir: Path,
    table_specs: tuple[_EmittingTableSpec, ...],
    meta: dict[str, Any],
) -> _CacheProbeFailure | None:  # pragma: no mutate
    """Validate a generation while an external publication lock makes it stable."""
    manifest_failure = _cache_manifest_failure(
        cache_dir,
        meta,
        expected_labels=tuple(spec.label for spec in table_specs),
    )
    if manifest_failure is not None:
        return manifest_failure
    for table_spec in table_specs:
        parquet_path = cache_dir / f"{_sanitise_label(table_spec.label)}.parquet"
        path_stat = parquet_path.lstat()
        if (
            not stat_module.S_ISREG(path_stat.st_mode)
            or stat_module.S_ISLNK(path_stat.st_mode)
            or _publication._is_reparse_point(path_stat)
        ):
            return _CacheProbeFailure("non_plain_frame", label=table_spec.label)
        expected_schema = _shred._declared_frame_schema(table_spec)
        try:
            actual_schema = pl.scan_parquet(parquet_path).collect_schema()
        except (OSError, pl.exceptions.PolarsError):
            return _CacheProbeFailure("unreadable_frame", label=table_spec.label)
        if dict(actual_schema.items()) != dict(expected_schema.items()):
            return _CacheProbeFailure(
                "schema_mismatch",
                label=table_spec.label,
                expected_schema=expected_schema,
                actual_schema=actual_schema,
            )
    return None


def _cache_is_valid_under_external_lock(
    cache_dir: Path,
    v2_config: dict[str, Any],
    *,  # pragma: no mutate
    data_path: Path,
    data_file_signature: Mapping[str, Any],
) -> bool:
    meta = _read_per_port_cache_meta_unlocked(cache_dir)
    if meta is None or not _cache_meta_matches_config_and_source(
        meta,
        v2_config,
        data_path=data_path,
        data_file_signature=data_file_signature,
    ):
        return False
    table_specs = _shred._emitting_table_specs(v2_config)
    return _cache_bundle_failure_in_place(cache_dir, table_specs, meta) is None


def _probe_cache_bundle(
    cache_dir: Path,
    table_specs: tuple[_EmittingTableSpec, ...],
    meta: dict[str, Any],
    *,  # pragma: no mutate
    complete_table_specs: tuple[_EmittingTableSpec, ...] | None = None,  # pragma: no mutate
    retain_snapshots: bool,
) -> tuple[dict[str, pl.LazyFrame], _CacheProbeFailure | None]:  # pragma: no mutate
    """Load cache frames whose name→dtype mappings match current specs.

    Physical parquet column order is deliberately irrelevant to the schema
    fingerprint. Each accepted lazy frame is projected into the current editor
    order, preserving that invariant without allowing missing/extra/renamed or
    differently typed columns through the fast path.

    Each requested parquet is pinned to a private file-backed snapshot before
    its signature is verified in bounded chunks. Polars receives that stable
    path, so native Parquet projection remains lazy and an already-returned plan
    is independent of later rebuilds, mirrors, or explicit cache deletion. The
    complete compressed payload is never retained in Python memory.
    """
    complete_specs = complete_table_specs or table_specs
    manifest_failure = _cache_manifest_structure_failure(
        meta,
        expected_labels=tuple(spec.label for spec in complete_specs),
    )
    if manifest_failure is not None:
        return {}, manifest_failure

    bundle: dict[str, pl.LazyFrame] = {}
    transient_snapshots: list[Path] = []
    manifest_entries = {entry["label"]: entry for entry in meta["tables"]}
    complete_specs_by_label = {spec.label: spec for spec in complete_specs}
    try:
        for table_spec in table_specs:
            complete_spec = complete_specs_by_label[table_spec.label]
            expected_schema = _shred._declared_frame_schema(complete_spec)
            entry = manifest_entries[table_spec.label]
            signature_parts = _source_proof._content_signature_parts(entry["content_signature"])
            assert signature_parts is not None
            parquet_path = cache_dir / f"{_sanitise_label(table_spec.label)}.parquet"
            try:
                snapshot_path = _runtime_storage._snapshot_cache_artifact(
                    cache_dir,
                    parquet_path,
                    entry["content_signature"],
                )
            except FileNotFoundError as exc:
                logger.warning(
                    "json_cache_snapshot_source_missing",
                    cache_dir=str(cache_dir),
                    parquet_path=str(parquet_path),
                    error=str(exc),
                )
                return bundle, _CacheProbeFailure(
                    "missing_frame",
                    label=table_spec.label,
                    expected_schema=expected_schema,
                )
            if snapshot_path is None:
                return bundle, _CacheProbeFailure(
                    "content_signature_mismatch",
                    label=table_spec.label,
                    expected_schema=expected_schema,
                )
            transient_snapshots.append(snapshot_path)
            frame = pl.scan_parquet(snapshot_path)
            actual_schema = frame.collect_schema()
            if dict(actual_schema.items()) != dict(expected_schema.items()):
                return bundle, _CacheProbeFailure(
                    "schema_mismatch",
                    label=table_spec.label,
                    expected_schema=expected_schema,
                    actual_schema=actual_schema,
                )
            bundle[table_spec.label] = frame.select(
                _shred._declared_frame_schema(table_spec).names()
            )
        if retain_snapshots:
            while transient_snapshots:
                _runtime_storage._retain_runtime_snapshot(transient_snapshots.pop())
        return bundle, None
    finally:
        for snapshot_path in transient_snapshots:
            _runtime_storage._release_runtime_snapshot(snapshot_path)


def _cache_summary(meta: Mapping[str, Any], cache_dir: Path) -> dict[str, Any]:
    return {
        "schema_mode": meta["schema_mode"],
        "schema_fingerprint": meta["schema_fingerprint"],
        "tables": meta["tables"],
        "data_file": meta["data_file"],
        "skipped": meta["skipped"],
        "cache_dir": str(cache_dir),
    }


def _remove_prepared_staging(staging: Path) -> None:
    try:
        _publication._remove_plain_cache_directory(staging)
    except FileNotFoundError:
        return


def prepare_per_port_cache(
    data_path: str | Path,  # pragma: no mutate
    v2_config: dict[str, Any],
    cache_dir: str | Path,  # pragma: no mutate
    *,  # pragma: no mutate
    staging_dir: str | Path,  # pragma: no mutate
) -> PreparedPerPortCacheBuild:
    """Materialise and validate a private generation without publishing it.

    A parent must hold :func:`per_port_cache_publication_lock` across this call
    (or across an isolated child invocation of it) and the later commit. The
    explicit staging path lets the parent clean up safely even when the worker
    times out or crashes before returning a manifest.
    """
    dp = _publication._normalised_build_path(data_path)
    cd = _publication._normalised_build_path(cache_dir)
    staging = _publication._validated_build_staging_dir(cd, staging_dir)
    table_specs = _shred._emitting_table_specs(v2_config)
    fingerprint = _shred._v2_fingerprint(v2_config)
    data_file_sig = _source_proof._data_file_signature(dp, rebind_persisted_proofs=False)

    if _cache_is_valid_under_external_lock(
        cd,
        v2_config,
        data_path=dp,
        data_file_signature=data_file_sig,
    ):
        existing_meta = _read_per_port_cache_meta_unlocked(cd)
        if existing_meta is None:
            raise RuntimeError("valid cache generation omitted its metadata")
        summary = _cache_summary(existing_meta, cd)
        logger.info(
            "json_shred_build_noop",
            data_path=str(dp),
            cache_dir=str(cd),
            fingerprint=fingerprint[:8],
        )
        return PreparedPerPortCacheBuild(
            data_path=str(dp),
            cache_dir=str(cd),
            staging_dir=None,
            schema_fingerprint=fingerprint,
            data_file_signature=dict(data_file_sig),
            summary=summary,
            no_op=True,
        )

    if staging.exists() or staging.is_symlink():
        raise FileExistsError(f"cache staging generation already exists: {staging}")
    staging.parent.mkdir(parents=True, exist_ok=True)
    staging.mkdir()
    try:
        ranges = (
            _records._jsonl_byte_ranges(dp, _records._PARALLEL_CHUNK_BYTES)
            if _records._should_shred_in_parallel(dp)
            else []
        )
        if len(ranges) > 1:
            table_summaries, skip_stats = _writer._write_tables_in_parallel(
                dp, v2_config, table_specs, staging, ranges
            )
        else:
            table_summaries, skip_stats = _writer._write_tables_streaming(
                dp,
                v2_config,
                table_specs,
                staging,
            )
        if _source_proof._data_file_signature(dp, rebind_persisted_proofs=False) != data_file_sig:
            raise SourceChangedDuringCacheBuildError(
                f"structured source changed while its cache was built: {dp}"
            )
        meta_payload = {
            "schema_mode": "v2",
            "schema_fingerprint": fingerprint,
            "tables": table_summaries,
            "data_file": data_file_sig,
            "skipped": skip_stats.as_meta(),
        }
        (staging / _META_FILENAME).write_bytes(orjson.dumps(meta_payload))
        failure = _cache_manifest_failure(
            staging,
            meta_payload,
            expected_labels=tuple(spec.label for spec in table_specs),
        )
        if failure is not None:
            raise RuntimeError(f"prepared cache manifest failed validation: {failure.reason}")
    except BaseException as exc:
        try:
            _remove_prepared_staging(staging)
        except BaseException as cleanup_exc:
            exc.add_note(f"cache staging cleanup failed: {cleanup_exc}")
        raise

    return PreparedPerPortCacheBuild(
        data_path=str(dp),
        cache_dir=str(cd),
        staging_dir=str(staging),
        schema_fingerprint=fingerprint,
        data_file_signature=dict(data_file_sig),
        summary=_cache_summary(meta_payload, cd),
    )


def _validate_prepared_cache(
    prepared: PreparedPerPortCacheBuild,
    v2_config: dict[str, Any],
) -> tuple[Path, Path | None, dict[str, Any]]:  # pragma: no mutate
    if not isinstance(prepared, PreparedPerPortCacheBuild):
        raise TypeError("prepared must be a PreparedPerPortCacheBuild")
    dp = _publication._normalised_build_path(prepared.data_path)
    cd = _publication._normalised_build_path(prepared.cache_dir)
    expected_fingerprint = _shred._v2_fingerprint(v2_config)
    if prepared.schema_fingerprint != expected_fingerprint:
        raise ValueError("prepared cache schema fingerprint does not match the requested config")
    if _source_proof._data_file_signature(dp) != prepared.data_file_signature:
        raise SourceChangedDuringCacheBuildError(
            f"structured source changed before its cache could be published: {dp}"
        )
    if prepared.no_op:
        if prepared.staging_dir is not None:
            raise ValueError("no-op cache preparation must not name a staging directory")
        if not _cache_is_valid_under_external_lock(
            cd,
            v2_config,
            data_path=dp,
            data_file_signature=prepared.data_file_signature,
        ):
            raise RuntimeError("the no-op cache generation changed before publication")
        meta = _read_per_port_cache_meta_unlocked(cd)
        if meta is None or prepared.summary != _cache_summary(meta, cd):
            raise RuntimeError("the no-op cache summary does not match the visible generation")
        return cd, None, meta

    if prepared.staging_dir is None:
        raise ValueError("prepared cache generation omitted its staging directory")
    staging = _publication._validated_build_staging_dir(cd, prepared.staging_dir)
    _publication._plain_directory_stat(staging)
    meta = _read_per_port_cache_meta_unlocked(staging)
    expected_meta = {
        key: prepared.summary.get(key)
        for key in ("schema_mode", "schema_fingerprint", "tables", "data_file", "skipped")
    }
    if meta != expected_meta or meta.get("data_file") != prepared.data_file_signature:
        raise RuntimeError("prepared cache metadata does not match its returned manifest")
    table_specs = _shred._emitting_table_specs(v2_config)
    expected_names = {
        _META_FILENAME,
        *(f"{_sanitise_label(spec.label)}.parquet" for spec in table_specs),
    }
    children = tuple(staging.iterdir())
    if {child.name for child in children} != expected_names:
        raise RuntimeError("prepared cache generation contains unexpected or missing artifacts")
    for child in children:
        child_stat = child.lstat()
        if (
            not stat_module.S_ISREG(child_stat.st_mode)
            or stat_module.S_ISLNK(child_stat.st_mode)
            or _publication._is_reparse_point(child_stat)
        ):
            raise RuntimeError(f"prepared cache artifact is not a plain regular file: {child}")
    failure = _cache_bundle_failure_in_place(staging, table_specs, meta)
    if failure is not None:
        raise RuntimeError(f"prepared cache generation failed validation: {failure.reason}")
    return cd, staging, meta


def commit_prepared_per_port_cache(
    prepared: PreparedPerPortCacheBuild,
    v2_config: dict[str, Any],
    *,  # pragma: no mutate
    publication_guard: AbstractContextManager[None] | None = None,  # pragma: no mutate
) -> dict[str, Any]:
    """Validate and atomically publish a child-prepared cache generation."""
    cd = _publication._normalised_build_path(prepared.cache_dir)
    if not _publication._build_lock_for(cd).owned_by_current_thread():
        raise RuntimeError("cache publication requires the parent-owned build lock")
    cd, staging, meta = _validate_prepared_cache(prepared, v2_config)
    if staging is None:
        with publication_guard or nullcontext():
            return _cache_summary(meta, cd)
    with publication_guard or nullcontext():
        _publication._swap_dir_into_place(staging, cd)
    skipped = meta["skipped"]
    if skipped.get("records", 0) or skipped.get("rows_by_table"):
        logger.warning(
            "json_shred_records_skipped",
            data_path=prepared.data_path,
            cache_dir=str(cd),
            skipped_records=skipped.get("records", 0),
            skipped_rows_by_table=skipped.get("rows_by_table", {}),
        )
    logger.info(
        "json_shred_built",
        data_path=prepared.data_path,
        cache_dir=str(cd),
        table_count=len(meta["tables"]),
        fingerprint=prepared.schema_fingerprint[:8],
    )
    return _cache_summary(meta, cd)


def discard_prepared_per_port_cache(prepared: PreparedPerPortCacheBuild) -> None:
    """Remove only the exact unpublished staging generation named by *prepared*."""
    if prepared.staging_dir is None:
        return
    cd = _publication._normalised_build_path(prepared.cache_dir)
    staging = _publication._validated_build_staging_dir(cd, prepared.staging_dir)
    _remove_prepared_staging(staging)


def discard_per_port_cache_staging(
    cache_dir: str | Path,  # pragma: no mutate
    staging_dir: str | Path,  # pragma: no mutate
) -> None:
    """Remove one exact parent-selected staging path after a worker failure."""
    cd = _publication._normalised_build_path(cache_dir)
    if not _publication._build_lock_for(cd).owned_by_current_thread():
        raise RuntimeError("cache staging cleanup requires the parent-owned build lock")
    staging = _publication._validated_build_staging_dir(cd, staging_dir)
    _remove_prepared_staging(staging)


def build_per_port_cache(
    data_path: str | Path,  # pragma: no mutate
    v2_config: dict[str, Any],
    cache_dir: str | Path,  # pragma: no mutate
) -> dict[str, Any]:
    """Build one serialized generation through the shared prepare/commit path."""
    cd = _publication._normalised_build_path(cache_dir)
    with _publication.per_port_cache_publication_lock(cd):
        staging = _publication._unique_build_tmp_dir(cd)
        prepared: PreparedPerPortCacheBuild | None = None  # pragma: no mutate
        try:
            prepared = prepare_per_port_cache(
                data_path,
                v2_config,
                cd,
                staging_dir=staging,
            )
            return commit_prepared_per_port_cache(prepared, v2_config)
        finally:
            if prepared is not None:
                discard_prepared_per_port_cache(prepared)
            elif staging.exists() or staging.is_symlink():
                _remove_prepared_staging(staging)


def load_per_port_cache(
    cache_dir: str | Path,  # pragma: no mutate
    v2_config: dict[str, Any],
) -> dict[str, pl.LazyFrame]:
    """Return the complete signed snapshot bundle selected by ``meta.json``.

    "Emitting" uses the shared :func:`table_is_emitting` predicate (emit and
    at least one selected column), exactly the set the build writes. Every
    label-derived artifact must exist, match its signature, and expose the
    declared schema. Exact verified, generation-pinned paths seed the returned
    file-backed LazyFrames; a missing or mismatched member rejects the whole
    bundle and returns ``{}`` rather than serving a partial generation.

    Callers needing source-file freshness must additionally use
    :func:`is_per_port_cache_valid` or :func:`load_v2_api_source`.
    """
    cd = Path(cache_dir)
    with _publication._build_lock_for(cd):
        table_specs = _shred._emitting_table_specs(v2_config)
        meta = read_per_port_cache_meta(cd)
        if (
            meta is None
            or meta.get("schema_mode") != "v2"
            or meta.get("schema_fingerprint") != _shred._v2_fingerprint(v2_config)
        ):
            return {}
        try:
            bundle, failure = _probe_cache_bundle(
                cd,
                table_specs,
                meta,
                retain_snapshots=True,
            )
        except (OSError, pl.exceptions.PolarsError):
            return {}
        return bundle if failure is None else {}


def _cache_meta_matches_config_and_source(
    meta: dict[str, Any],
    v2_config: dict[str, Any],
    *,  # pragma: no mutate
    data_path: str | Path,  # pragma: no mutate
    data_file_signature: Mapping[str, Any] | None = None,  # pragma: no mutate
) -> bool:
    """Return whether captured metadata identifies this schema and source."""
    if meta.get("schema_mode") != "v2":
        return False
    try:
        expected_fingerprint = _shred._v2_fingerprint(v2_config)
    except ApiInputSchemaError:
        # Preserve the bool contract for status/save callers that probe a
        # malformed in-memory config. Build and load boundaries validate loud.
        return False
    return meta.get(
        "schema_fingerprint"
    ) == expected_fingerprint and _source_proof._data_file_matches(
        meta.get("data_file"),
        Path(data_path),
        data_file_signature=data_file_signature,
    )


def _read_matching_cache_meta_unlocked(
    cd: Path,
    v2_config: dict[str, Any],
    *,  # pragma: no mutate
    data_path: str | Path,  # pragma: no mutate
    data_file_signature: Mapping[str, Any] | None,  # pragma: no mutate
) -> dict[str, Any] | None:  # pragma: no mutate
    meta_path = cd / _META_FILENAME
    try:
        meta = orjson.loads(meta_path.read_bytes())
    except (OSError, ValueError):
        return None
    if not isinstance(meta, dict):
        return None
    if not _cache_meta_matches_config_and_source(
        meta,
        v2_config,
        data_path=data_path,
        data_file_signature=data_file_signature,
    ):
        return None
    return meta


def load_v2_api_source(
    data_path: str,
    config: dict[str, Any],
    *,  # pragma: no mutate
    port_columns: Mapping[str, frozenset[str] | set[str] | None] | None = None,  # pragma: no mutate
) -> dict[str, pl.LazyFrame]:  # pragma: no mutate
    """Load a v2 apiInput as an emit-gated per-port frame bundle.

    The shared frame-bundle loader reached through
    :func:`haute._node_apply.resolve_api_input_from_config` by both executor
    and generated/deploy code, so those paths cannot drift. Validates *config*
    itself so direct callers receive the same typed schema errors as the
    executor and generated module boundaries.

    Behaviour:

    - 0 emit-true tables → ``RuntimeError`` (tick an ``emit`` toggle).
    - emit-true tables but none with a selected column → ``RuntimeError``.
    - prefers a valid, readable, schema-matching ``working/`` parquet cache,
      then ``committed/`` (the deploy / fresh-server case).
    - when neither cache can serve the current schema and source signature,
      shreds JSON, JSONL, or XML directly for this run without writing cache state.
    - 1+ emitting labels → a ``dict[port_label, LazyFrame]`` in schema order.

    Frame resolution uses the shared :func:`table_is_emitting` predicate, so
    an emit-true table with zero selected columns contributes no frame and —
    crucially — no longer wedges validity (W2 item 2.5).
    """
    from haute._json_flatten import _json_cache_dir

    # Parse the complete config before considering a demand. Cache identity and
    # every opened parquet remain governed by this full schema; the demand only
    # controls which ports and columns are materialised for this caller.
    complete_table_specs = _shred._emitting_table_specs(config)
    tables = config["tables"]
    emit_true_tables = [t for t in tables if t.get("emit")]
    if not emit_true_tables:
        raise RuntimeError(
            "API Input has no emitting tables. Open the node, tick the 'emit' "
            "toggle on at least one table, then preview again.",
        )
    emit_labels = [spec.label for spec in complete_table_specs]
    if not emit_labels:
        labels = [t["label"] for t in emit_true_tables]
        raise RuntimeError(
            "API Input has emit-true tables but none has any selected columns. "
            f"Open the node and tick at least one column on the emitting "
            f"table(s): {labels}, then preview again.",
        )

    table_specs = complete_table_specs
    if port_columns is not None:
        if not isinstance(port_columns, Mapping) or not port_columns:
            raise ValueError("port_columns must be a non-empty mapping")
        complete_by_label = {spec.label: spec for spec in complete_table_specs}
        projected_specs: list[_EmittingTableSpec] = []
        for label, requested_columns in port_columns.items():
            if label not in complete_by_label:
                raise ValueError(f"port_columns requests unknown emitting port {label!r}")
            if requested_columns is not None and not isinstance(
                requested_columns,
                (frozenset, set),
            ):
                raise ValueError(
                    f"port_columns[{label!r}] must be None or a frozenset/set",
                )
            complete_spec = complete_by_label[label]
            if requested_columns is None:
                projected_specs.append(complete_spec)
                continue
            if any(not isinstance(column, str) or not column for column in requested_columns):
                raise ValueError(
                    f"port_columns[{label!r}] must contain non-empty string column names",
                )
            available = {name for name, _leaf, _type, _depth in complete_spec.columns}
            missing = set(requested_columns) - available
            if missing:
                raise ValueError(
                    f"port_columns[{label!r}] requests missing declared column(s): "
                    f"{sorted(missing)!r}",
                )
            # Preserve config order, not demand-set iteration order. An empty
            # logical demand is cardinality-only; one physical carrier keeps
            # Polars from collapsing the frame to zero rows.
            physical_columns = (
                set(requested_columns) if requested_columns else {complete_spec.columns[0][0]}
            )
            projected_specs.append(
                _EmittingTableSpec(
                    label=complete_spec.label,
                    segments=complete_spec.segments,
                    columns=tuple(
                        column for column in complete_spec.columns if column[0] in physical_columns
                    ),
                ),
            )
        # Preserve v2 schema order even when the caller supplied its mapping in
        # a different order.
        requested_by_label = {spec.label: spec for spec in projected_specs}
        table_specs = tuple(
            requested_by_label[spec.label]
            for spec in complete_table_specs
            if spec.label in requested_by_label
        )
    # Defer the complete source proof until a cache layer has plausible schema
    # metadata. A cold uncached execution should parse the source once, not hash
    # the whole file first merely to prove that two absent caches are absent.
    expected_fingerprint = _shred._v2_fingerprint(config)
    data_file_sig: dict[str, Any] | None = None  # pragma: no mutate
    execution_context = current_execution_context()
    # A valid parquet cache is an optimization, not a runtime prerequisite.
    # Prefer the user's current working cache, then the saved/deployable
    # committed cache. If either disappears between validation and scanning,
    # continue to the next candidate rather than restoring the old hard cache
    # dependency.
    for layer in ("working", "committed"):
        cache_dir = _json_cache_dir(data_path, layer)
        with _publication._build_lock_for(cache_dir):
            candidate_meta = _read_per_port_cache_meta_unlocked(cache_dir)
            plausible_meta = (
                candidate_meta is not None
                and candidate_meta.get("schema_mode") == "v2"
                and candidate_meta.get("schema_fingerprint") == expected_fingerprint
            )
            cache_meta: dict[str, Any] | None = None  # pragma: no mutate
            if plausible_meta:
                assert candidate_meta is not None
                if data_file_sig is None:
                    data_file_sig = _source_proof._data_file_signature(Path(data_path))
                if _cache_meta_matches_config_and_source(
                    candidate_meta,
                    config,
                    data_path=data_path,
                    data_file_signature=data_file_sig,
                ):
                    cache_meta = candidate_meta
            if cache_meta is None:
                if execution_context is not None:
                    reason = (
                        ExecutionCacheProofMissReason.METADATA_SOURCE_MISMATCH
                        if (cache_dir / _META_FILENAME).is_file()
                        else ExecutionCacheProofMissReason.PROOF_UNAVAILABLE
                    )
                    execution_context.record_cache_proof_miss(reason)
                continue
            try:
                bundle, probe_failure = _probe_cache_bundle(
                    cache_dir,
                    table_specs,
                    cache_meta,
                    complete_table_specs=complete_table_specs,
                    retain_snapshots=True,
                )
            except (OSError, pl.exceptions.PolarsError) as exc:
                if execution_context is not None:
                    execution_context.record_cache_proof_miss(
                        ExecutionCacheProofMissReason.UNREADABLE_ARTIFACT
                    )
                logger.warning(
                    "json_shred_cache_candidate_rejected",
                    data_path=data_path,
                    cache_dir=str(cache_dir),
                    layer=layer,
                    reason="unreadable_parquet",
                    error_type=type(exc).__name__,
                )
                continue
            if probe_failure is not None:
                if execution_context is not None:
                    execution_context.record_cache_proof_miss(
                        ExecutionCacheProofMissReason.ARTIFACT_INTEGRITY_SCHEMA_FAILURE
                    )
                logger.warning(
                    "json_shred_cache_candidate_rejected",
                    data_path=data_path,
                    cache_dir=str(cache_dir),
                    layer=layer,
                    reason=probe_failure.reason,
                    label=probe_failure.label,
                    expected_schema=(
                        str(probe_failure.expected_schema)
                        if probe_failure.expected_schema is not None
                        else None
                    ),
                    actual_schema=(
                        str(probe_failure.actual_schema)
                        if probe_failure.actual_schema is not None
                        else None
                    ),
                )
                continue
            if execution_context is not None:
                execution_context.record_cache_proof_hit()
            return {table_spec.label: bundle[table_spec.label] for table_spec in table_specs}

    # Neither cache can serve the current post-schema shape. Shred the source
    # for this execution only; do not write, refresh, or promote cache state.
    direct_bundle, skip_stats = _writer._shred_data_file_to_direct_spill(
        Path(data_path),
        config,
        table_specs,
        _json_cache_dir(data_path, "working"),
    )
    if execution_context is not None:
        execution_context.record_cache_direct_fallback()
    if skip_stats.total:
        logger.warning(
            "json_shred_direct_records_skipped",
            data_path=data_path,
            skipped_records=skip_stats.skipped_records,
            skipped_rows_by_table=skip_stats.skipped_rows_by_table,
        )
    logger.info(
        "json_shred_loaded_direct",
        data_path=data_path,
        table_count=len(table_specs),
    )
    return direct_bundle


def is_per_port_cache_valid(
    cache_dir: str | Path,  # pragma: no mutate
    v2_config: dict[str, Any],
    *,  # pragma: no mutate
    data_path: str | Path,  # pragma: no mutate
    data_file_signature: Mapping[str, Any] | None = None,  # pragma: no mutate
) -> bool:
    """Return whether a complete, readable cache can serve the current input.

    ``meta.json`` must match the v2 schema fingerprint and recorded source-file
    signature (W2 item 2.4). Every emitting table must have exactly one signed
    manifest entry whose derived parquet matches its size/SHA-256, then expose
    the exact declared name-to-Polars-dtype mapping. Physical parquet column
    order does not affect validity; accepted frames are projected into current
    editor order by :func:`_probe_cache_bundle` at load time.

    The data-file check always compares the recorded content hash with an
    observed content proof. An unchanged strong native revision may reuse a
    prior full hash; ``mtime_ns`` alone cannot authorise that reuse, so a
    same-size, same-mtime byte-changing rewrite remains stale (matching
    :func:`_data_file_matches`). The committed-layer deploy fallback still
    survives file copies because the fresh hash matches when only metadata
    moved. Metadata without a recorded source or per-parquet signature is
    invalid.
    """
    cd = Path(cache_dir)
    with _publication._build_lock_for(cd):
        return _is_per_port_cache_valid_unlocked(
            cd,
            v2_config,
            data_path=data_path,
            data_file_signature=data_file_signature,
        )


def _is_per_port_cache_valid_unlocked(
    cache_dir: Path,
    v2_config: dict[str, Any],
    *,  # pragma: no mutate
    data_path: str | Path,  # pragma: no mutate
    data_file_signature: Mapping[str, Any] | None,  # pragma: no mutate
) -> bool:
    try:
        expected_fingerprint = _shred._v2_fingerprint(v2_config)
    except ApiInputSchemaError:
        return False
    cache_meta = _read_per_port_cache_meta_unlocked(cache_dir)
    if (
        cache_meta is None
        or cache_meta.get("schema_mode") != "v2"
        or cache_meta.get("schema_fingerprint") != expected_fingerprint
    ):
        return False
    try:
        signature = (
            _source_proof._data_file_signature(Path(data_path))
            if data_file_signature is None
            else data_file_signature
        )
    except OSError:
        return False
    if not _cache_meta_matches_config_and_source(
        cache_meta,
        v2_config,
        data_path=data_path,
        data_file_signature=signature,
    ):
        return False
    try:
        table_specs = _shred._emitting_table_specs(v2_config)
        _bundle, probe_failure = _probe_cache_bundle(
            cache_dir,
            table_specs,
            cache_meta,
            retain_snapshots=False,
        )
    except (ApiInputSchemaError, OSError, pl.exceptions.PolarsError):
        return False
    return probe_failure is None


def read_per_port_cache_meta(cache_dir: str | Path) -> dict[str, Any] | None:  # pragma: no mutate
    """Return the cached ``meta.json`` payload, or ``None`` if absent / corrupt.

    Used by the cache routes' status endpoint to report what's on disk
    without re-shredding.
    """
    cd = Path(cache_dir)
    with _publication._build_lock_for(cd):
        return _read_per_port_cache_meta_unlocked(cd)


def _read_per_port_cache_meta_unlocked(cd: Path) -> dict[str, Any] | None:  # pragma: no mutate
    meta_path = cd / _META_FILENAME
    try:
        meta = orjson.loads(meta_path.read_bytes())
    except (OSError, ValueError):
        return None
    if not isinstance(meta, dict):
        return None
    return meta
