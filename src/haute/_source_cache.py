"""Provider-neutral, generation-based snapshots of external tabular inputs."""

from __future__ import annotations

import contextlib
import hashlib
import json
import os
import shutil
import threading
import time
import uuid
from collections.abc import Callable, Iterable, Iterator, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Literal, Protocol, runtime_checkable

import polars as pl

from haute._cache import CacheConsumer, canonical_json, checked_cache_inputs
from haute._credential_security import is_credential_name, validate_credential_free_uri
from haute._env import float_env, int_env
from haute._file_ops import atomic_write_text
from haute._logging import get_logger
from haute._polars_utils import bounded_sink

if TYPE_CHECKING:
    from haute._execution_context import ExecutionContext

BuildClass = Literal["bounded", "admitted_eager", "unsupported"]
CacheState = Literal["missing", "building", "ready", "corrupt", "failed"]
CacheFreshness = Literal["fresh", "stale", "unknown"]
ReconcileOutcome = Literal[
    "published", "discarded_generation", "discarded_staging", "unremovable", "absent"
]
_VerifiedGeneration = tuple[str, str, int, int, str]
_DEFAULT_STAGING_MAX_AGE_SECONDS = 7 * 24 * 60 * 60
_DEFAULT_RETIRE_GRACE_SECONDS = 30 * 60

logger = get_logger(component="source_cache")


class SourceCacheError(RuntimeError):
    """Base error for source snapshot cache failures."""


class SourceCacheCorruptError(SourceCacheError):
    """The selected cache generation is not a valid immutable snapshot."""


class SourceCacheBuildError(SourceCacheError):
    """A builder is not admissible for a source snapshot build."""


class SourceCacheQuotaExceededError(SourceCacheError):
    """Publishing a generation would exceed the configured store quota."""


def _reject_secrets(value: object) -> None:
    """Reject identity material that could disclose credentials on disk."""
    if isinstance(value, Mapping):
        for key, child in value.items():
            if not isinstance(key, str):
                raise TypeError("source-cache descriptor keys must be strings")
            is_reference = key.casefold().replace("-", "_").endswith("_ref")
            if is_credential_name(key) and not is_reference:
                raise ValueError(
                    "source-cache identity must not contain secret or credential material"
                )
            _reject_secrets(child)
    elif isinstance(value, (list, tuple, set, frozenset)):
        for child in value:
            _reject_secrets(child)
    elif isinstance(value, str):
        if "://" in value:
            try:
                validate_credential_free_uri(value)
            except ValueError as exc:
                raise ValueError(
                    "source-cache identity must not contain secret or credential URI material"
                ) from exc


@dataclass(frozen=True, slots=True)
class SourceCacheIdentity:
    """Versioned, redaction-safe identity for one provider source."""

    provider: str
    descriptor: Mapping[str, object]
    schema_version: int = 1

    def __post_init__(self) -> None:
        if not isinstance(self.provider, str) or not self.provider:
            raise ValueError("provider must be a non-empty string")
        if not isinstance(self.descriptor, Mapping):
            raise TypeError("descriptor must be a mapping")
        if (
            isinstance(self.schema_version, bool)
            or not isinstance(self.schema_version, int)
            or self.schema_version < 1
        ):
            raise ValueError("schema_version must be a positive integer")
        _reject_secrets(self.descriptor)
        # Exercise canonicalisation now, so invalid descriptor values fail at the boundary.
        canonical_json(self.payload)

    @property
    def payload(self) -> dict[str, object]:
        inputs = checked_cache_inputs(
            CacheConsumer.INPUT_SNAPSHOT,
            {
                "schema_version": self.schema_version,
                "provider": self.provider,
                "descriptor": dict(self.descriptor),
            },
        )
        return dict(inputs.values)

    @property
    def canonical_bytes(self) -> bytes:
        return canonical_json(self.payload).encode("utf-8")

    @property
    def digest(self) -> str:
        return hashlib.sha256(self.canonical_bytes).hexdigest()


@dataclass(slots=True)
class SourceCacheBuildContext:
    """Execution constraints supplied to a snapshot builder."""

    profile: object
    build_class: BuildClass
    cancellation: threading.Event | Callable[[], bool] | None = None
    deadline: float | None = None
    progress: Callable[[int], None] | None = None
    execution_context: ExecutionContext | None = None
    progress_units: int = 0
    # Parent-chosen pair. A supervising parent names the generation the build
    # publishes and the staging directory it writes, so after the worker dies
    # it can reconcile exactly those two and never another build's.
    generation_id: str | None = None
    staging_token: str | None = None
    # A spawned build never retires superseded generations: its lease counts are
    # child-local, so a generation the parent process still leases would look
    # unreferenced. The supervising parent retires with its own lease counts.
    defer_retirement: bool = False
    # Generations the supervising parent still leases. Meaningful with
    # ``defer_retirement``: the child's lease table is empty, so without these
    # ids its quota projection would count the parent-leased current generation
    # as reclaimable and publish beyond the hard limit.
    retained_generation_ids: frozenset[str] = frozenset()

    def __post_init__(self) -> None:
        if self.build_class not in ("bounded", "admitted_eager", "unsupported"):
            raise ValueError("build_class must be bounded, admitted_eager, or unsupported")
        if (self.generation_id is None) != (self.staging_token is None):
            raise ValueError(
                "source-cache generation_id and staging_token must be set together or not at all"
            )
        if self.generation_id is not None:
            _validate_generation_id(self.generation_id)
        if self.staging_token is not None:
            _validate_staging_token(self.staging_token)
        if not isinstance(self.retained_generation_ids, (frozenset, set)):
            raise ValueError("retained_generation_ids must be a set of generation ids")
        for retained in self.retained_generation_ids:
            _validate_generation_id(retained)
        self.retained_generation_ids = frozenset(self.retained_generation_ids)

    def checkpoint(self) -> None:
        cancelled = self.cancellation
        if cancelled is not None:
            is_cancelled = cancelled() if callable(cancelled) else cancelled.is_set()
            if is_cancelled:
                raise SourceCacheBuildError("source-cache build was cancelled")
        if self.deadline is not None and time.monotonic() > self.deadline:
            raise SourceCacheBuildError("source-cache build exceeded its deadline")
        if self.execution_context is not None:
            self.execution_context.checkpoint(label="input_snapshot_build")

    @contextlib.contextmanager
    def stage(self, name: str) -> Iterator[None]:
        if self.execution_context is None:
            yield
            return
        with self.execution_context.stage(name):
            yield

    def advance(self, units: int = 1) -> None:
        if isinstance(units, bool) or not isinstance(units, int) or units < 0:
            raise ValueError("progress units must be a non-negative integer")
        self.progress_units += units
        if self.progress is not None:
            self.progress(units)
        self.checkpoint()


@runtime_checkable
class SourceCacheBuilder(Protocol):
    """Provider adapter capable of producing a bounded snapshot input."""

    def build(self, context: SourceCacheBuildContext) -> pl.LazyFrame | Iterable[object]: ...


@dataclass(frozen=True, slots=True)
class SourceCacheMetadata:
    identity_digest: str
    identity: dict[str, object]
    schema_version: int
    generation_id: str
    source_signature: str | None
    data_sha256: str
    size_bytes: int
    row_count: int
    column_count: int
    columns: dict[str, str]
    created_at: float
    profile: str
    build_class: BuildClass

    def to_dict(self) -> dict[str, object]:
        return {
            "identity_digest": self.identity_digest,
            "identity": self.identity,
            "schema_version": self.schema_version,
            "generation_id": self.generation_id,
            "source_signature": self.source_signature,
            "data_sha256": self.data_sha256,
            "size_bytes": self.size_bytes,
            "row_count": self.row_count,
            "column_count": self.column_count,
            "columns": self.columns,
            "created_at": self.created_at,
            "profile": self.profile,
            "build_class": self.build_class,
        }


@dataclass(frozen=True, slots=True)
class SourceCacheGeneration:
    generation_id: str
    data_path: Path
    metadata_path: Path
    metadata: SourceCacheMetadata

    @property
    def lazy_frame(self) -> pl.LazyFrame:
        return pl.scan_parquet(self.data_path)


@dataclass(frozen=True, slots=True)
class SourceCacheStatus:
    state: CacheState
    freshness: CacheFreshness
    generation: SourceCacheGeneration | None = None


@dataclass(slots=True)
class _SourceCacheCoordination:
    """Process-local locks and leases shared by every handle to one cache root."""

    lock: threading.RLock = field(default_factory=threading.RLock)
    identity_locks: dict[str, threading.RLock] = field(default_factory=dict)
    leases: dict[tuple[str, str], int] = field(default_factory=dict)
    verified_generations: set[_VerifiedGeneration] = field(default_factory=set)


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _validate_generation_id(value: object) -> str:
    if not isinstance(value, str):
        raise ValueError("invalid source-cache generation id")
    try:
        parsed = uuid.UUID(value)
    except (AttributeError, TypeError, ValueError) as exc:
        raise ValueError("invalid source-cache generation id") from exc
    if str(parsed) != value:
        raise ValueError("invalid source-cache generation id")
    return value


def _validate_staging_token(value: object) -> str:
    """Accept exactly eight lower-case hex characters.

    The staging directory is a sibling of the generations tree and
    ``atomic_write_text`` appends its own unique suffix beneath it, so the
    token is kept short deliberately: a full UUID name can exceed Windows'
    traditional path limit under a long temporary root.
    """
    if not isinstance(value, str) or len(value) != 8:
        raise ValueError("invalid source-cache staging token")
    if any(character not in "0123456789abcdef" for character in value):
        raise ValueError("invalid source-cache staging token")
    return value


def new_staging_token() -> str:
    """Return a fresh parent-chosen staging token."""
    return uuid.uuid4().hex[:8]


class SourceCacheStore:
    """Own immutable source snapshots rooted at ``root/.haute_cache/inputs``."""

    _coordination_lock = threading.Lock()
    _staging_cleanup_lock = threading.Lock()
    _coordination_by_root: dict[Path, _SourceCacheCoordination] = {}

    def __init__(
        self,
        root: str | Path,
        *,
        max_bytes: int | None = None,
        max_generations: int | None = None,
        retire_grace_seconds: float | None = None,
    ) -> None:
        self.root = Path(root).resolve()
        self.inputs_root = self.root / ".haute_cache" / "inputs"
        self.inputs_root.mkdir(parents=True, exist_ok=True)
        if max_bytes is None:
            max_bytes = int_env("HAUTE_INPUT_CACHE_MAX_BYTES", 20 * 1024 * 1024 * 1024)
        if isinstance(max_bytes, bool) or not isinstance(max_bytes, int) or max_bytes <= 0:
            raise ValueError("source-cache max_bytes must be a positive integer")
        self.max_bytes = max_bytes
        if max_generations is None:
            max_generations = int_env("HAUTE_INPUT_CACHE_MAX_GENERATIONS", 64)
        if (
            isinstance(max_generations, bool)
            or not isinstance(max_generations, int)
            or max_generations <= 0
        ):
            raise ValueError("source-cache max_generations must be a positive integer")
        self.max_generations = max_generations
        if retire_grace_seconds is None:
            retire_grace_seconds = float_env(
                "HAUTE_INPUT_CACHE_RETIRE_GRACE_SECONDS",
                _DEFAULT_RETIRE_GRACE_SECONDS,
            )
        if not isinstance(retire_grace_seconds, (int, float)) or retire_grace_seconds < 0:
            raise ValueError("source-cache retire_grace_seconds must be zero or positive")
        # Leases are process-local, so a superseded generation another process
        # is still scanning must survive long enough for that read to finish.
        self.retire_grace_seconds = float(retire_grace_seconds)
        self.staging_max_age_seconds = float_env(
            "HAUTE_INPUT_CACHE_STAGING_MAX_AGE_SECONDS",
            _DEFAULT_STAGING_MAX_AGE_SECONDS,
        )
        coordination_key = self.inputs_root.resolve()
        with self._coordination_lock:
            coordination = self._coordination_by_root.setdefault(
                coordination_key,
                _SourceCacheCoordination(),
            )
        self._lock = coordination.lock
        self._identity_locks = coordination.identity_locks
        self._leases = coordination.leases
        self._verified_generations = coordination.verified_generations
        self._cleanup_stale_staging()

    @staticmethod
    def _tree_latest_activity(path: Path) -> float | None:
        try:
            latest = path.lstat().st_mtime
            for root, directories, files in os.walk(path, followlinks=False):
                root_path = Path(root)
                directories[:] = [
                    name for name in directories if not (root_path / name).is_symlink()
                ]
                for name in (*directories, *files):
                    latest = max(latest, (root_path / name).lstat().st_mtime)
            return latest
        except OSError:
            return None

    def _cleanup_stale_staging(self) -> None:
        with self._staging_cleanup_lock:
            staging_paths = list(self.inputs_root.glob("*/.staging-*"))
            if not staging_paths:
                return
            cutoff = time.time() - self.staging_max_age_seconds
            for staging in staging_paths:
                if not staging.is_dir() or staging.is_symlink():
                    continue
                latest_activity = self._tree_latest_activity(staging)
                if latest_activity is None or latest_activity >= cutoff:
                    continue
                try:
                    shutil.rmtree(staging)
                except OSError:
                    # A racing or unreadable directory is not proven abandoned.
                    continue

    def identity_path(self, identity: SourceCacheIdentity) -> Path:
        return self.inputs_root / identity.digest

    def _identity_lock(self, identity: SourceCacheIdentity) -> threading.RLock:
        with self._lock:
            return self._identity_locks.setdefault(identity.digest, threading.RLock())

    def _pointer_path(self, identity: SourceCacheIdentity) -> Path:
        return self.identity_path(identity) / "current.json"

    def _read_pointer(self, identity: SourceCacheIdentity) -> str:
        path = self._pointer_path(identity)
        if not path.exists():
            raise FileNotFoundError(path)
        try:
            pointer = json.loads(path.read_text(encoding="utf-8"))
            generation_id = _validate_generation_id(pointer["generation_id"])
            if pointer.get("identity_digest") != identity.digest:
                raise ValueError("invalid current pointer")
            return generation_id
        except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError) as exc:
            raise SourceCacheCorruptError("source-cache current pointer is corrupt") from exc

    def _metadata_from_path(
        self, identity: SourceCacheIdentity, generation_id: str
    ) -> SourceCacheGeneration:
        try:
            generation_id = _validate_generation_id(generation_id)
            generation_dir = self.identity_path(identity) / "generations" / generation_id
            data_path = generation_dir / "data.parquet"
            metadata_path = generation_dir / "meta.json"
            raw = json.loads(metadata_path.read_text(encoding="utf-8"))
            metadata = SourceCacheMetadata(
                identity_digest=raw["identity_digest"],
                identity=raw["identity"],
                schema_version=raw["schema_version"],
                generation_id=raw["generation_id"],
                source_signature=raw.get("source_signature"),
                data_sha256=raw["data_sha256"],
                size_bytes=raw["size_bytes"],
                row_count=raw["row_count"],
                column_count=raw["column_count"],
                columns=raw["columns"],
                created_at=raw["created_at"],
                profile=raw["profile"],
                build_class=raw["build_class"],
            )
            data_stat = data_path.stat()
            if (
                metadata.identity_digest != identity.digest
                or metadata.identity != identity.payload
                or metadata.schema_version != identity.schema_version
                or metadata.generation_id != generation_id
                or metadata.size_bytes != data_stat.st_size
                or not isinstance(metadata.data_sha256, str)
                or len(metadata.data_sha256) != 64
                or isinstance(metadata.row_count, bool)
                or not isinstance(metadata.row_count, int)
                or metadata.row_count < 0
                or isinstance(metadata.column_count, bool)
                or not isinstance(metadata.column_count, int)
                or metadata.column_count < 0
                or not isinstance(metadata.columns, dict)
                or metadata.column_count != len(metadata.columns)
                or not isinstance(metadata.created_at, (int, float))
            ):
                raise ValueError("metadata does not match snapshot")
            verification_key = (
                identity.digest,
                generation_id,
                data_stat.st_mtime_ns,
                data_stat.st_size,
                metadata.data_sha256,
            )
            with self._lock:
                verified = verification_key in self._verified_generations
            if not verified:
                if _sha256_file(data_path) != metadata.data_sha256:
                    raise ValueError("snapshot digest does not match metadata")
                with self._lock:
                    self._verified_generations.add(verification_key)
            # Read the footer/schema before exposing scan_parquet; corrupt data never falls back.
            import pyarrow.parquet as pq

            parquet_metadata = pq.read_metadata(data_path)
            arrow_schema = pq.read_schema(data_path)
            polars_schema = pl.scan_parquet(data_path).collect_schema()
            if (
                parquet_metadata.num_rows != metadata.row_count
                or arrow_schema.names != polars_schema.names()
                or {name: str(dtype) for name, dtype in polars_schema.items()} != metadata.columns
            ):
                raise ValueError("metadata schema or row count does not match snapshot")
        except FileNotFoundError as exc:
            raise SourceCacheCorruptError("source-cache generation is corrupt") from exc
        except OSError:
            raise
        except Exception as exc:
            # Polars/PyArrow use implementation-specific exception types.
            if isinstance(exc, SourceCacheCorruptError):
                raise
            raise SourceCacheCorruptError("source-cache generation is corrupt") from exc
        return SourceCacheGeneration(generation_id, data_path, metadata_path, metadata)

    def open_generation(self, identity: SourceCacheIdentity) -> SourceCacheGeneration:
        with self._identity_lock(identity):
            try:
                generation_id = self._read_pointer(identity)
            except FileNotFoundError:
                raise
            return self._metadata_from_path(identity, generation_id)

    def _write_output(
        self, output: pl.LazyFrame | Iterable[object], path: Path, context: SourceCacheBuildContext
    ) -> None:
        if isinstance(output, pl.LazyFrame):
            bounded_sink(output, path, fast_checkpoint=True)
            return
        if isinstance(output, pl.DataFrame) or not isinstance(output, Iterable):
            raise SourceCacheBuildError("builder must return a LazyFrame or Arrow batches/tables")
        import pyarrow as pa
        import pyarrow.parquet as pq

        writer: pq.ParquetWriter | None = None
        try:
            for item in output:
                context.checkpoint()
                if isinstance(item, pa.Table):
                    table = item
                elif isinstance(item, pa.RecordBatch):
                    table = pa.Table.from_batches([item])
                else:
                    raise SourceCacheBuildError(
                        "builder iterator must yield Arrow RecordBatch or Table"
                    )
                if writer is None:
                    writer = pq.ParquetWriter(path, table.schema)
                writer.write_table(table)
                context.advance(table.num_rows)
            if writer is None:
                raise SourceCacheBuildError("builder yielded no Arrow batches")
        finally:
            if writer is not None:
                writer.close()

    def _generation_bytes(self) -> int:
        total = 0
        for path in self.inputs_root.glob("*/generations/*/data.parquet"):
            try:
                total += path.stat().st_size
            except FileNotFoundError:
                continue
        return total

    def _staging_bytes(self, *, exclude: Path | None = None) -> int:
        total = 0
        for staging in self.inputs_root.glob("*/.staging-*"):
            if (
                not staging.is_dir()
                or staging.is_symlink()
                or (exclude is not None and staging == exclude)
            ):
                continue
            for root, directories, files in os.walk(staging, followlinks=False):
                root_path = Path(root)
                directories[:] = [
                    name for name in directories if not (root_path / name).is_symlink()
                ]
                for name in files:
                    path = root_path / name
                    if path.is_symlink():
                        continue
                    try:
                        total += path.stat().st_size
                    except FileNotFoundError:
                        continue
        return total

    def _generation_count(self) -> int:
        return sum(1 for _ in self.inputs_root.glob("*/generations/*/data.parquet"))

    def _admit_publication_within_quota(
        self,
        identity: SourceCacheIdentity,
        *,
        new_size_bytes: int,
        staging_path: Path,
        retained_generation_ids: frozenset[str] = frozenset(),
    ) -> None:
        current_size = self._generation_bytes() + self._staging_bytes(exclude=staging_path)
        current_count = self._generation_count()
        reclaimable = 0
        reclaimable_count = 0
        try:
            current_id = self._read_pointer(identity)
            current_path = (
                self.identity_path(identity) / "generations" / current_id / "data.parquet"
            )
            if (
                self._leases.get((identity.digest, current_id), 0) == 0
                and current_id not in retained_generation_ids
            ):
                reclaimable = current_path.stat().st_size
                reclaimable_count = 1
        except (FileNotFoundError, SourceCacheCorruptError):
            pass

        projected_bytes = current_size - reclaimable + new_size_bytes
        projected_count = current_count - reclaimable_count + 1
        if projected_bytes <= self.max_bytes and projected_count <= self.max_generations:
            return

        raise SourceCacheQuotaExceededError(
            "source-cache quota exceeded: existing snapshots are kept until "
            "explicitly refreshed or cleared. Clear an unused Data Input "
            "snapshot or raise the cache quota."
        )

    def build(
        self,
        identity: SourceCacheIdentity,
        builder: SourceCacheBuilder,
        *,
        context: SourceCacheBuildContext,
        source_signature: str | None = None,
        refresh: bool = False,
    ) -> SourceCacheGeneration:
        if context.build_class == "unsupported":
            raise SourceCacheBuildError("unsupported source-cache build class")
        declared = getattr(builder, "build_class", context.build_class)
        if declared != context.build_class:
            raise SourceCacheBuildError("builder build class does not match source-cache context")
        lock = self._identity_lock(identity)
        with lock:
            if not refresh:
                try:
                    return self.open_generation(identity)
                except FileNotFoundError:
                    pass
            identity_dir = self.identity_path(identity)
            generations_dir = identity_dir / "generations"
            generations_dir.mkdir(parents=True, exist_ok=True)
            # Keep the staging sibling deliberately short: ``atomic_write_text``
            # appends its own unique suffix, and Windows' traditional path limit can
            # otherwise be exceeded beneath pytest's long temporary roots.
            staging_token = context.staging_token or uuid.uuid4().hex[:8]
            staging = identity_dir / f".staging-{staging_token}"
            generation_id = context.generation_id or str(uuid.uuid4())
            final_dir: Path | None = None
            published = False
            try:
                staging.mkdir()
                data_path = staging / "data.parquet"
                context.checkpoint()
                with context.stage("input_snapshot_read"):
                    output = builder.build(context)
                context.checkpoint()
                with context.stage("input_snapshot_write"):
                    self._write_output(output, data_path, context)
                context.checkpoint()
                # Validate before publication, including its footer and scan schema.
                data_sha256 = _sha256_file(data_path)
                import pyarrow.parquet as pq

                parquet_metadata = pq.read_metadata(data_path)
                schema = pl.scan_parquet(data_path).collect_schema()
                columns = {name: str(dtype) for name, dtype in schema.items()}
                metadata = SourceCacheMetadata(
                    identity.digest,
                    identity.payload,
                    identity.schema_version,
                    generation_id,
                    source_signature,
                    data_sha256,
                    data_path.stat().st_size,
                    parquet_metadata.num_rows,
                    len(columns),
                    columns,
                    time.time(),
                    getattr(context.profile, "value", str(context.profile)),
                    context.build_class,
                )
                metadata_path = staging / "meta.json"
                atomic_write_text(metadata_path, canonical_json(metadata.to_dict()))
                context.checkpoint()
                # Self-validate the staged directory using the same strict validator after rename.
                with self._lock:
                    if not context.defer_retirement:
                        self._retire_unleased(identity)
                    try:
                        self._admit_publication_within_quota(
                            identity,
                            new_size_bytes=metadata.size_bytes,
                            staging_path=staging,
                            retained_generation_ids=context.retained_generation_ids,
                        )
                    except SourceCacheQuotaExceededError:
                        # Quota pressure outranks the reader grace: reclaim the
                        # graced generations once and admit again, or fail.
                        self._retire_unleased(identity, force=True)
                        logger.warning(
                            "source_cache_grace_reclaimed_under_quota_pressure",
                            identity_digest=identity.digest,
                        )
                        self._admit_publication_within_quota(
                            identity,
                            new_size_bytes=metadata.size_bytes,
                            staging_path=staging,
                            retained_generation_ids=context.retained_generation_ids,
                        )
                    final_dir = generations_dir / generation_id
                    staging.replace(final_dir)
                    published_stat = (final_dir / "data.parquet").stat()
                    self._verified_generations.add(
                        (
                            identity.digest,
                            generation_id,
                            published_stat.st_mtime_ns,
                            published_stat.st_size,
                            data_sha256,
                        )
                    )
                    generation = self._metadata_from_path(identity, generation_id)
                    context.checkpoint()
                    atomic_write_text(
                        self._pointer_path(identity),
                        canonical_json(
                            {"identity_digest": identity.digest, "generation_id": generation_id}
                        ),
                    )
                    published = True
                if not context.defer_retirement:
                    self._retire_unleased(identity)
                return generation
            except BaseException:
                shutil.rmtree(staging, ignore_errors=True)
                if final_dir is not None and not published:
                    self._forget_verified(identity.digest, generation_id)
                    shutil.rmtree(final_dir, ignore_errors=True)
                raise

    @contextlib.contextmanager
    def lease(self, identity: SourceCacheIdentity) -> Iterator[SourceCacheGeneration]:
        with self._identity_lock(identity):
            generation = self.open_generation(identity)
            key = (identity.digest, generation.generation_id)
            with self._lock:
                self._leases[key] = self._leases.get(key, 0) + 1
        try:
            yield generation
        finally:
            with self._identity_lock(identity):
                with self._lock:
                    self._leases[key] -= 1
                    if self._leases[key] == 0:
                        del self._leases[key]
                self._retire_unleased(identity)

    def leased_generation_ids(self, identity: SourceCacheIdentity) -> frozenset[str]:
        """Return the generations of *identity* this process currently leases.

        A supervising parent hands these to a spawned build, whose own lease
        table is empty, so the child's quota projection treats them as retained
        instead of reclaimable.
        """
        with self._identity_lock(identity):
            with self._lock:
                return frozenset(
                    generation_id
                    for (digest, generation_id), count in self._leases.items()
                    if digest == identity.digest and count > 0
                )

    def retire_unleased(self, identity: SourceCacheIdentity) -> None:
        """Delete every generation of *identity* that is neither current nor leased.

        The supervising parent of a spawned build calls this after publication:
        the child deferred retirement because only this process knows which
        generations its own executions still lease.
        """
        with self._identity_lock(identity):
            self._retire_unleased(identity)

    def _retire_grace_elapsed(self, identity: SourceCacheIdentity) -> bool:
        """Whether the current generation has been published long enough.

        Measured from the current pointer's mtime: a reader in another process
        opened its generation before that write, so the grace bounds how long
        such a scan may still be running. Without a pointer there is no current
        generation to protect a reader against, and retirement proceeds.
        """
        try:
            published_at = self._pointer_path(identity).stat().st_mtime
        except OSError:
            return True
        return time.time() - published_at >= self.retire_grace_seconds

    def _retire_unleased(self, identity: SourceCacheIdentity, *, force: bool = False) -> None:
        generations_dir = self.identity_path(identity) / "generations"
        if not generations_dir.exists():
            return
        try:
            current = self._read_pointer(identity)
        except FileNotFoundError:
            current = None
        grace_elapsed = force or self._retire_grace_elapsed(identity)
        for candidate in generations_dir.iterdir():
            if not candidate.is_dir() or candidate.name == current:
                continue
            with self._lock:
                leased = self._leases.get((identity.digest, candidate.name), 0)
            if not leased and grace_elapsed:
                self._forget_verified(identity.digest, candidate.name)
                shutil.rmtree(candidate)

    def _forget_verified(self, identity_digest: str, generation_id: str) -> None:
        with self._lock:
            stale = {
                key
                for key in self._verified_generations
                if key[:2] == (identity_digest, generation_id)
            }
            self._verified_generations.difference_update(stale)

    def _unremovable(self, path: Path, identity: SourceCacheIdentity) -> ReconcileOutcome:
        """Report a reconcile removal that silently left its directory behind."""
        logger.warning(
            "source_cache_reconcile_removal_failed",
            path=str(path),
            identity_digest=identity.digest,
        )
        return "unremovable"

    def reconcile_unpublished(
        self,
        identity: SourceCacheIdentity,
        generation_id: str,
        staging_token: str,
    ) -> ReconcileOutcome:
        """Settle exactly one supervised build's generation and staging.

        Called by the parent after a spawned build failed or died. It never
        touches the current generation, a leased generation, or another
        build's staging directory.
        """
        _validate_generation_id(generation_id)
        _validate_staging_token(staging_token)
        identity_dir = self.identity_path(identity)
        with self._identity_lock(identity):
            try:
                if self._read_pointer(identity) == generation_id:
                    return "published"
            except (FileNotFoundError, SourceCacheCorruptError):
                pass
            removed_generation = False
            generation_dir = identity_dir / "generations" / generation_id
            if generation_dir.is_dir():
                with self._lock:
                    leased = self._leases.get((identity.digest, generation_id), 0)
                if not leased:
                    self._forget_verified(identity.digest, generation_id)
                    shutil.rmtree(generation_dir, ignore_errors=True)
                    if generation_dir.exists():
                        return self._unremovable(generation_dir, identity)
                    removed_generation = True
            removed_staging = False
            staging = identity_dir / f".staging-{staging_token}"
            if staging.is_dir() and not staging.is_symlink():
                shutil.rmtree(staging, ignore_errors=True)
                if staging.exists():
                    return self._unremovable(staging, identity)
                removed_staging = True
            if removed_generation:
                return "discarded_generation"
            if removed_staging:
                return "discarded_staging"
            return "absent"

    def clear(self, identity: SourceCacheIdentity) -> None:
        with self._identity_lock(identity):
            self._pointer_path(identity).unlink(missing_ok=True)
            self._retire_unleased(identity, force=True)

    def status(
        self, identity: SourceCacheIdentity, *, source_signature: str | None = None
    ) -> SourceCacheStatus:
        try:
            generation = self.open_generation(identity)
        except FileNotFoundError:
            return SourceCacheStatus("missing", "unknown")
        except SourceCacheCorruptError:
            return SourceCacheStatus("corrupt", "unknown")
        freshness: CacheFreshness
        if source_signature is None or generation.metadata.source_signature is None:
            freshness = "unknown"
        elif source_signature == generation.metadata.source_signature:
            freshness = "fresh"
        else:
            freshness = "stale"
        return SourceCacheStatus("ready", freshness, generation)
