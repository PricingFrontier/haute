"""Provider-neutral, generation-based snapshots of external tabular inputs."""

from __future__ import annotations

import contextlib
import hashlib
import json
import os
import re
import shutil
import stat as stat_module
import threading
import time
import uuid
from collections.abc import Callable, Iterable, Iterator, Mapping
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import TYPE_CHECKING, Any, Final, Literal, Protocol, runtime_checkable

import polars as pl

from haute._cache import CacheConsumer, canonical_json, checked_cache_inputs, polars_version
from haute._chunked_writes import is_part_name, part_name, part_paths, scan_parts, write_parts
from haute._credential_security import is_credential_name, validate_credential_free_uri
from haute._env import float_env
from haute._file_lock import (
    FileLock,
    _acquire_file_lock,
    _assert_path_ancestors_plain,
    _is_reparse_point,
    _open_lock_file,
    _release_file_lock,
)
from haute._file_ops import atomic_write_text, ensure_disk_headroom
from haute._hashing import content_hash
from haute._logging import get_logger
from haute._map_columns import (
    arrow_holds_map,
    iter_parquet_map_entries,
    map_entries_frame,
    map_entries_table,
)

if TYPE_CHECKING:
    import pyarrow as pa

    from haute._execution_context import ExecutionContext

BuildClass = Literal["bounded", "admitted_eager", "unsupported"]
CacheState = Literal["missing", "building", "ready", "corrupt", "failed"]
CacheFreshness = Literal["fresh", "stale", "unknown"]
ReconcileOutcome = Literal[
    "published", "discarded_generation", "discarded_staging", "unremovable", "absent"
]
# (identity digest, generation id, ((part name, mtime_ns, size, digest), ...))
_VerifiedGeneration = tuple[str, str, tuple[tuple[str, int, int, str], ...]]
# A generation is meta.json plus ordered part files. Metadata from the
# single-file layout or from layout 2 (SHA-256 part digests) reads as absent
# and is rebuilt.
GENERATION_LAYOUT_VERSION = 3
_DIGEST_REGEX = re.compile(r"[0-9a-f]{16}")
_DEFAULT_STAGING_MAX_AGE_SECONDS = 7 * 24 * 60 * 60
_DEFAULT_RETIRE_GRACE_SECONDS = 30 * 60
# Provider of node-output snapshots; their publication, leases, and retirement
# live in ``haute._node_snapshots``.
# ``Final`` narrows this to its literal type, so it can be passed wherever a
# ``CacheBucket`` is expected rather than only compared against one.
NODE_OUTPUT_PROVIDER: Final = "node_output"
CacheBucket = Literal["node_output", "input"]
IdentityClassification = Literal["node_output", "input", "unknown"]
KNOWN_INPUT_PROVIDERS = frozenset(
    {"file", "lakehouse", "database", "databricks", "inline", "api_input"}
)
_LEASE_PREFIX = ".lease-"
# A retired generation is renamed to this prefix beside ``generations/`` under
# the lease lock, and its files are deleted once the lock is released.
_RETIRED_PREFIX = ".retired-"
_TOKEN_LENGTH = 12

logger = get_logger(component="source_cache")


class SourceCacheError(RuntimeError):
    """Base error for source snapshot cache failures."""


class SourceCacheCorruptError(SourceCacheError):
    """The selected cache generation is not a valid immutable snapshot."""


class SourceCacheGenerationMissingError(SourceCacheCorruptError):
    """A named generation does not exist: it was never published or was retired."""


class SourceCacheLegacyLayoutError(SourceCacheGenerationMissingError):
    """A generation in the retired single-file layout: absent, never corruption."""


class _MapPartsNeedReencoding(Exception):  # noqa: N818 - a signal, not an error
    """An input generation another Polars described holds a physical Parquet map column.

    Polars 2's Parquet reader would keep only the last value of a key the map
    repeats while the parts' digests still matched, so the generation is
    re-encoded rather than read (see :meth:`SourceCacheStore.open_generation`).
    """


class _Superseded(Exception):  # noqa: N818 - a signal, not an error
    """The generation a build was to replace is no longer current, so it publishes nothing.

    A re-encoded copy holds the data of the generation it copied; once another
    build has published, possibly in another process, the copy would replace
    newer data with older (see :meth:`SourceCacheStore._reencoded`).
    """


class SourceCacheBuildError(SourceCacheError):
    """A builder is not admissible for a source snapshot build."""


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
    # Supervised workers leave retirement to the parent after the build succeeds.
    # Cross-process lease markers protect readers during the handoff.
    defer_retirement: bool = False

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
class SourceCachePart:
    """One part file of a generation, in the generation's row order."""

    name: str
    size_bytes: int
    digest: str
    row_count: int

    def to_dict(self) -> dict[str, object]:
        return {
            "name": self.name,
            "size_bytes": self.size_bytes,
            "digest": self.digest,
            "row_count": self.row_count,
        }

    @classmethod
    def from_dict(cls, raw: object) -> SourceCachePart:
        if not isinstance(raw, dict):
            raise ValueError("generation part must be an object")
        name = raw["name"]
        size_bytes = raw["size_bytes"]
        digest = raw["digest"]
        row_count = raw["row_count"]
        if not isinstance(name, str) or not is_part_name(name):
            raise ValueError("generation part has an invalid name")
        for value in (size_bytes, row_count):
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                raise ValueError("generation part sizes must be non-negative integers")
        if not isinstance(digest, str) or not _DIGEST_REGEX.fullmatch(digest):
            raise ValueError("generation part digest is invalid")
        return cls(name, size_bytes, digest, row_count)


def describe_parts(
    directory: Path,
    digests: Mapping[str, str] | None = None,
) -> tuple[SourceCachePart, ...]:
    """Name, size, digest, and row count of every part a write left in *directory*."""
    import pyarrow.parquet as pq

    recorded: Mapping[str, str] = {} if digests is None else digests
    paths = part_paths(directory)
    unknown = sorted(set(recorded) - {path.name for path in paths})
    if unknown:
        raise ValueError(f"recorded digests for unknown parts: {unknown}")

    parts = tuple(
        SourceCachePart(
            name=path.name,
            size_bytes=path.stat().st_size,
            digest=recorded[path.name] if path.name in recorded else content_hash(path),
            row_count=pq.read_metadata(path).num_rows,
        )
        for path in paths
    )
    if not parts:
        raise ValueError("a generation holds at least one part file")
    return parts


def generation_bytes(generation_dir: Path) -> int:
    """Bytes held by a generation's part files."""
    total = 0
    for path in generation_dir.glob("part-*.parquet"):
        try:
            total += path.stat().st_size
        except FileNotFoundError:
            continue
    return total


def _ensure_identity_marker(identity_dir: Path, provider: str) -> None:
    marker = identity_dir / "provider"
    if not marker.exists():
        atomic_write_text(marker, f"{provider}\n")


def classify_identity_marker(identity_dir: Path) -> IdentityClassification:
    """Classify an identity directory's dataset kind from its provider marker.

    Answers ``"node_output"``, ``"input"``, or ``"unknown"``. A missing,
    unreadable, or unrecognised marker answers ``"unknown"``.
    """
    marker = identity_dir / "provider"
    try:
        if not marker.is_file() or marker.is_symlink():
            return "unknown"
        text = marker.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return "unknown"
    lines = text.splitlines()
    if len(lines) != 1:
        return "unknown"
    provider = lines[0].strip()
    if provider == NODE_OUTPUT_PROVIDER:
        return "node_output"
    if provider in KNOWN_INPUT_PROVIDERS:
        return "input"
    return "unknown"


@dataclass(frozen=True, slots=True)
class SourceCacheMetadata:
    identity_digest: str
    identity: dict[str, object]
    schema_version: int
    generation_id: str
    source_signature: str | None
    parts: tuple[SourceCachePart, ...]
    size_bytes: int
    row_count: int
    column_count: int
    columns: dict[str, str]
    created_at: float
    profile: str
    build_class: BuildClass
    # Provider-specific generation facts (node-output column set and
    # dependencies). Absent for input snapshots, whose metadata is unchanged.
    node_output: Mapping[str, object] | None = None
    # Wall-clock seconds from allocating the staging directory to writing this
    # metadata: how long caching this actually took. Absent on a generation
    # published before it was recorded, which reads as unknown rather than zero.
    build_seconds: float | None = None
    # The Polars version whose scan described ``columns``. Absent on a generation
    # published before it was recorded, which reads as another version.
    polars_version: str | None = None

    def to_dict(self) -> dict[str, object]:
        payload: dict[str, object] = {
            "identity_digest": self.identity_digest,
            "identity": self.identity,
            "schema_version": self.schema_version,
            "generation_id": self.generation_id,
            "source_signature": self.source_signature,
            "layout_version": GENERATION_LAYOUT_VERSION,
            "parts": [part.to_dict() for part in self.parts],
            "size_bytes": self.size_bytes,
            "row_count": self.row_count,
            "column_count": self.column_count,
            "columns": self.columns,
            "created_at": self.created_at,
            "profile": self.profile,
            "build_class": self.build_class,
            "polars_version": self.polars_version,
        }
        if self.node_output is not None:
            payload["node_output"] = dict(self.node_output)
        if self.build_seconds is not None:
            payload["build_seconds"] = self.build_seconds
        return payload


@dataclass(frozen=True, slots=True)
class _MapEntriesCopy:
    """A generation's own parts, read through pyarrow with every map as entries."""

    parts: tuple[Path, ...]
    build_class: BuildClass = "bounded"

    def build(self, context: SourceCacheBuildContext) -> Iterator[pa.Table]:
        for part in self.parts:
            context.checkpoint()
            yield from iter_parquet_map_entries(part)


@dataclass(frozen=True, slots=True)
class SourceCacheGeneration:
    generation_id: str
    data_paths: tuple[Path, ...]
    metadata_path: Path
    metadata: SourceCacheMetadata

    @property
    def directory(self) -> Path:
        return self.metadata_path.parent

    @property
    def lazy_frame(self) -> pl.LazyFrame:
        return scan_parts(self.data_paths)


@dataclass(frozen=True, slots=True)
class SourceCacheStatus:
    state: CacheState
    freshness: CacheFreshness
    generation: SourceCacheGeneration | None = None


@dataclass(slots=True)
class _SourceCacheCoordination:
    """Process-local locks and leases shared by every handle to one cache root."""

    lease_lock: FileLock
    lock: threading.RLock = field(default_factory=threading.RLock)
    identity_locks: dict[str, threading.RLock] = field(default_factory=dict)
    leases: dict[tuple[str, str], int] = field(default_factory=dict)
    verified_generations: set[_VerifiedGeneration] = field(default_factory=set)
    guard: threading.Lock = field(default_factory=threading.Lock)
    publication_locks: dict[str, FileLock] = field(default_factory=dict)
    budget_lock: FileLock | None = None
    token: str | None = None
    token_handle: Any | None = None
    retired_cleaned: bool = False


def _verification_key(
    identity_digest: str,
    generation_id: str,
    parts: tuple[SourceCachePart, ...],
    part_stats: tuple[os.stat_result, ...],
) -> _VerifiedGeneration:
    """What a generation's verified digests are remembered against."""
    return (
        identity_digest,
        generation_id,
        tuple(
            (part.name, part_stat.st_mtime_ns, part_stat.st_size, part.digest)
            for part, part_stat in zip(parts, part_stats, strict=True)
        ),
    )


def _validate_generation_files(generation_dir: Path, *artifacts: Path) -> None:
    """Reject links, reparse points, hard links, and escapes before trusting a generation."""
    generation_stat = generation_dir.lstat()
    if (
        not stat_module.S_ISDIR(generation_stat.st_mode)
        or stat_module.S_ISLNK(generation_stat.st_mode)
        or _is_reparse_point(generation_stat)
    ):
        raise ValueError("source-cache generation is not a plain directory")
    for path in artifacts:
        artifact_stat = path.lstat()
        if (
            not stat_module.S_ISREG(artifact_stat.st_mode)
            or stat_module.S_ISLNK(artifact_stat.st_mode)
            or _is_reparse_point(artifact_stat)
        ):
            raise ValueError("source-cache generation contains a non-regular artifact")
        if artifact_stat.st_nlink != 1:
            raise ValueError("source-cache generation artifact must not be hard-linked")
    resolved_generation = generation_dir.resolve(strict=True)
    if resolved_generation.parent != generation_dir.parent.resolve(strict=True):
        raise ValueError("source-cache generation escapes its identity directory")
    for path in artifacts:
        resolved = path.resolve(strict=True)
        if resolved.parent != resolved_generation or not resolved.is_file():
            raise ValueError("source-cache artifact escapes its generation")


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
    _coordination_by_root: dict[tuple[Path, int], _SourceCacheCoordination] = {}

    def __init__(
        self,
        root: str | Path,
        *,
        retire_grace_seconds: float | None = None,
    ) -> None:
        self.root = Path(root).resolve()
        if self.root.parent == self.root:
            # A cache at a filesystem root would be written outside any project
            # and, in tests, outside the sandbox: the caller's project root is
            # wrong, and failing here says so instead of polluting the drive.
            raise ValueError(
                f"source-cache root must be a project directory, not the filesystem root: "
                f"{self.root}"
            )
        self.inputs_root = self.root / ".haute_cache" / "inputs"
        self.inputs_root.mkdir(parents=True, exist_ok=True)
        if retire_grace_seconds is None:
            retire_grace_seconds = float_env(
                "HAUTE_INPUT_CACHE_RETIRE_GRACE_SECONDS",
                _DEFAULT_RETIRE_GRACE_SECONDS,
            )
        if not isinstance(retire_grace_seconds, (int, float)) or retire_grace_seconds < 0:
            raise ValueError("source-cache retire_grace_seconds must be zero or positive")
        # Keep the existing handoff grace in addition to cross-process leases:
        # readers already holding a marker remain protected beyond this delay.
        self.retire_grace_seconds = float(retire_grace_seconds)
        self.staging_max_age_seconds = float_env(
            "HAUTE_INPUT_CACHE_STAGING_MAX_AGE_SECONDS",
            _DEFAULT_STAGING_MAX_AGE_SECONDS,
        )
        self._locks_dir = self.inputs_root / ".locks"
        self._processes_dir = self.inputs_root / ".processes"
        # A fork inherits Python memory but not a safe process-token handle.
        # Keying local coordination by PID gives the child a fresh token/count table.
        coordination_key = (self.inputs_root.resolve(), os.getpid())
        with self._coordination_lock:
            coordination = self._coordination_by_root.get(coordination_key)
            if coordination is None:
                coordination = _SourceCacheCoordination(
                    lease_lock=FileLock(self._locks_dir / "leases.lock")
                )
                self._coordination_by_root[coordination_key] = coordination
        self._coordination = coordination
        self._lease_lock = coordination.lease_lock
        self._lock = coordination.lock
        self._identity_locks = coordination.identity_locks
        self._leases = coordination.leases
        self._verified_generations = coordination.verified_generations
        self._cleanup_stale_staging()
        # The sweep globs the whole store, and a preview builds several stores
        # for one root, so it runs once per process.
        with coordination.guard:
            already_cleaned = coordination.retired_cleaned
            coordination.retired_cleaned = True
        if not already_cleaned:
            self._cleanup_retired()

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

    def _own_token(self) -> str:
        coordination = self._coordination
        with coordination.guard:
            if coordination.token is None:
                token = uuid.uuid4().hex[:_TOKEN_LENGTH]
                path = self._processes_dir / f"{token}.lock"
                _assert_path_ancestors_plain(path)
                path.parent.mkdir(parents=True, exist_ok=True)
                handle = _open_lock_file(path)
                try:
                    handle.write(b"\0")
                    handle.flush()
                    if not _acquire_file_lock(handle, blocking=False):
                        raise RuntimeError("a fresh lease-owner token file is already locked")
                except BaseException:
                    handle.close()
                    raise
                coordination.token = token
                coordination.token_handle = handle
            return coordination.token

    def _token_alive(self, token: str) -> bool:
        if token == self._coordination.token:
            return True
        if len(token) != _TOKEN_LENGTH or any(
            character not in "0123456789abcdef" for character in token
        ):
            return False
        path = self._processes_dir / f"{token}.lock"
        if not path.exists():
            return False
        handle = _open_lock_file(path)
        try:
            if not _acquire_file_lock(handle, blocking=False):
                return True
            _release_file_lock(handle)
        finally:
            handle.close()
        with contextlib.suppress(OSError):
            path.unlink()
        return False

    def _in_process_lease_count(self, identity_digest: str, generation_id: str) -> int:
        with self._lock:
            return self._leases.get((identity_digest, generation_id), 0)

    def _has_live_holders_locked(self, generation_dir: Path) -> bool:
        """Whether any live process leases *generation_dir*. Caller holds the lease lock."""
        try:
            markers = tuple(
                entry for entry in generation_dir.iterdir() if entry.name.startswith(_LEASE_PREFIX)
            )
        except FileNotFoundError:
            return False
        identity_digest = generation_dir.parent.parent.name
        generation_id = generation_dir.name
        live = False
        own = self._coordination.token
        for marker in markers:
            token = marker.name.removeprefix(_LEASE_PREFIX)
            if token == own:
                if self._in_process_lease_count(identity_digest, generation_id) > 0:
                    live = True
                else:
                    marker.unlink(missing_ok=True)
                continue
            if self._token_alive(token):
                live = True
                continue
            marker.unlink(missing_ok=True)
            logger.info(
                "source_cache_dead_lease_marker_removed",
                identity_digest=identity_digest,
                generation_id=generation_id,
            )
        return live

    def _pointer_path(self, identity: SourceCacheIdentity) -> Path:
        return self.identity_path(identity) / "current.json"

    def _pointed_generation_id(self, identity: SourceCacheIdentity) -> str | None:
        """The current generation's id, or None without a pointer."""
        try:
            return self._read_pointer(identity)
        except FileNotFoundError:
            return None

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
            metadata_path = generation_dir / "meta.json"
            try:
                generation_dir.lstat()
            except FileNotFoundError as exc:
                raise SourceCacheGenerationMissingError(
                    "source-cache generation does not exist"
                ) from exc
            _validate_generation_files(generation_dir, metadata_path)
            raw = json.loads(metadata_path.read_text(encoding="utf-8"))
            if raw.get("layout_version") != GENERATION_LAYOUT_VERSION:
                if "parts" not in raw and "data_sha256" in raw:
                    raise SourceCacheLegacyLayoutError(
                        "source-cache generation uses the retired single-file layout"
                    )
                if raw.get("layout_version") == 2:
                    raise SourceCacheLegacyLayoutError(
                        "source-cache generation uses retired layout version 2"
                    )
                raise ValueError("unknown source-cache generation layout")
            parts = tuple(SourceCachePart.from_dict(part) for part in raw["parts"])
            if not parts or len({part.name for part in parts}) != len(parts):
                raise ValueError("generation parts must be present and distinct")
            data_paths = tuple(generation_dir / part.name for part in parts)
            _validate_generation_files(generation_dir, *data_paths)
            metadata = SourceCacheMetadata(
                identity_digest=raw["identity_digest"],
                identity=raw["identity"],
                schema_version=raw["schema_version"],
                generation_id=raw["generation_id"],
                source_signature=raw.get("source_signature"),
                parts=parts,
                size_bytes=raw["size_bytes"],
                row_count=raw["row_count"],
                column_count=raw["column_count"],
                columns=raw["columns"],
                created_at=raw["created_at"],
                profile=raw["profile"],
                build_class=raw["build_class"],
                node_output=raw.get("node_output"),
                build_seconds=(
                    float(raw["build_seconds"])
                    if isinstance(raw.get("build_seconds"), (int, float))
                    and not isinstance(raw.get("build_seconds"), bool)
                    else None
                ),
                polars_version=raw.get("polars_version"),
            )
            part_stats = tuple(path.stat() for path in data_paths)
            if (
                metadata.identity_digest != identity.digest
                or metadata.identity != identity.payload
                or metadata.schema_version != identity.schema_version
                or metadata.generation_id != generation_id
                or any(
                    part.size_bytes != part_stat.st_size
                    for part, part_stat in zip(parts, part_stats, strict=True)
                )
                or metadata.size_bytes != sum(part.size_bytes for part in parts)
                or metadata.row_count != sum(part.row_count for part in parts)
                or isinstance(metadata.row_count, bool)
                or not isinstance(metadata.row_count, int)
                or metadata.row_count < 0
                or isinstance(metadata.column_count, bool)
                or not isinstance(metadata.column_count, int)
                or metadata.column_count < 0
                or not isinstance(metadata.columns, dict)
                or metadata.column_count != len(metadata.columns)
                or not isinstance(metadata.created_at, (int, float))
                or (metadata.node_output is not None and not isinstance(metadata.node_output, dict))
                or (
                    metadata.polars_version is not None
                    and not isinstance(metadata.polars_version, str)
                )
            ):
                raise ValueError("metadata does not match snapshot")
            verification_key = _verification_key(identity.digest, generation_id, parts, part_stats)
            with self._lock:
                verified = verification_key in self._verified_generations
            if not verified:
                for part, path in zip(parts, data_paths, strict=True):
                    if content_hash(path) != part.digest:
                        raise ValueError("snapshot digest does not match metadata")
                with self._lock:
                    self._verified_generations.add(verification_key)
            # Read every footer/schema before exposing the scan; corrupt data never falls back.
            import pyarrow.parquet as pq

            # An input snapshot is a copy of its source, so another Polars only
            # describes it again (its digests proved the bytes above); a node
            # output's identity names the Polars that computed it.
            redescribe = (
                identity.provider != NODE_OUTPUT_PROVIDER
                and metadata.polars_version != polars_version()
            )
            expected_columns: dict[str, str] | None = None if redescribe else metadata.columns
            for part, path in zip(parts, data_paths, strict=True):
                parquet_metadata = pq.read_metadata(path)
                arrow_schema = pq.read_schema(path)
                if redescribe and any(arrow_holds_map(field.type) for field in arrow_schema):
                    raise _MapPartsNeedReencoding(generation_id)
                polars_schema = pl.scan_parquet(path).collect_schema()
                columns = {name: str(dtype) for name, dtype in polars_schema.items()}
                if expected_columns is None:
                    expected_columns = columns
                if (
                    parquet_metadata.num_rows != part.row_count
                    or arrow_schema.names != polars_schema.names()
                    or columns != expected_columns
                ):
                    raise ValueError("metadata schema or row count does not match snapshot")
            if redescribe:
                assert expected_columns is not None
                metadata = self._redescribed(metadata_path, metadata, expected_columns)
        except FileNotFoundError as exc:
            raise SourceCacheCorruptError("source-cache generation is corrupt") from exc
        except OSError:
            raise
        except Exception as exc:
            # Polars/PyArrow use implementation-specific exception types.
            if isinstance(exc, SourceCacheCorruptError | _MapPartsNeedReencoding):
                raise
            raise SourceCacheCorruptError("source-cache generation is corrupt") from exc
        return SourceCacheGeneration(generation_id, data_paths, metadata_path, metadata)

    @staticmethod
    def _redescribed(
        metadata_path: Path,
        metadata: SourceCacheMetadata,
        columns: dict[str, str],
    ) -> SourceCacheMetadata:
        """Record this Polars' description of a generation another Polars described.

        Its data and part digests are unchanged; the column descriptions are this
        Polars' scan of them, so the generation is re-described, never corrupt.
        """
        redescribed = replace(
            metadata,
            columns=columns,
            column_count=len(columns),
            polars_version=polars_version(),
        )
        atomic_write_text(metadata_path, canonical_json(redescribed.to_dict()))
        logger.info(
            "source_cache_generation_redescribed",
            identity_digest=metadata.identity_digest,
            generation_id=metadata.generation_id,
            described_by=metadata.polars_version,
            polars_version=redescribed.polars_version,
            changed_columns=sorted(
                name
                for name in set(columns) | set(metadata.columns)
                if columns.get(name) != metadata.columns.get(name)
            ),
        )
        return redescribed

    def _reencoded(
        self, identity: SourceCacheIdentity, generation_id: str
    ) -> SourceCacheGeneration | None:
        """Publish a copy of the current generation, whose parts hold a physical Parquet map.

        pyarrow reads the generation's own parts one row group at a time, every map
        becoming a list of key-value structs with all its entries, so nothing is
        fetched from the provider. The old generation is leased from the moment it
        is found current until the copy is published, then retired like any
        superseded generation. The copy is published only while the old generation
        is still current, checked under the lease lock every process shares, so a
        generation another build published meanwhile is never replaced by older
        data. Returns None when the old generation is no longer current.
        """
        if not self._acquire_input_lease(identity, generation_id, require_current=True):
            return None
        try:
            from haute._execution_context import ExecutionProfile

            generation_dir = self.identity_path(identity) / "generations" / generation_id
            raw = json.loads((generation_dir / "meta.json").read_text(encoding="utf-8"))
            parts = tuple(
                generation_dir / SourceCachePart.from_dict(part).name for part in raw["parts"]
            )
            generation = self.build(
                identity,
                _MapEntriesCopy(parts),
                context=SourceCacheBuildContext(
                    profile=ExecutionProfile.LAZY_SINK, build_class="bounded"
                ),
                source_signature=raw.get("source_signature"),
                refresh=True,
                replacing=generation_id,
            )
        except _Superseded:
            logger.info(
                "source_cache_reencoding_superseded",
                identity_digest=identity.digest,
                generation_id=generation_id,
            )
            return None
        finally:
            self._release_input_lease(identity, generation_id)
        logger.info(
            "source_cache_generation_reencoded",
            identity_digest=identity.digest,
            generation_id=generation_id,
            reencoded_as=generation.generation_id,
            described_by=raw.get("polars_version"),
        )
        return generation

    def open_generation(self, identity: SourceCacheIdentity) -> SourceCacheGeneration:
        with self._identity_lock(identity):
            try:
                generation_id = self._read_pointer(identity)
            except FileNotFoundError:
                raise
            try:
                return self._metadata_from_path(identity, generation_id)
            except SourceCacheGenerationMissingError as exc:
                # A generation in a retired layout, or one whose directory is
                # gone (evicted by another process, deleted by hand), reads as
                # absent, so status reports it missing and a build replaces it.
                raise FileNotFoundError(
                    f"source-cache generation {generation_id} is not available: {exc}"
                ) from exc
            except _MapPartsNeedReencoding:
                # None when another build published meanwhile: open that one.
                return self._reencoded(identity, generation_id) or self.open_generation(identity)

    def _write_output(
        self,
        output: pl.LazyFrame | Iterable[object],
        directory: Path,
        context: SourceCacheBuildContext,
    ) -> Mapping[str, str]:
        if isinstance(output, pl.LazyFrame):
            # Sliced a chunk at a time where the source can be sliced. A map
            # column becomes a list of key-value structs with the entries Polars
            # read (see haute._map_columns).
            written = write_parts(directory, map_entries_frame(output), fast_checkpoint=True)
            return written.digests
        path = directory / part_name(0)
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
                # Every map entry survives as a list of key-value structs; Polars'
                # Parquet reader would keep only a repeated key's last value.
                table = map_entries_table(table)
                if writer is None:
                    writer = pq.ParquetWriter(path, table.schema)
                ensure_disk_headroom(directory, table.nbytes)
                writer.write_table(table)
                context.advance(table.num_rows)
            if writer is None:
                raise SourceCacheBuildError("builder yielded no Arrow batches")
        finally:
            if writer is not None:
                writer.close()
        return {}

    def build(
        self,
        identity: SourceCacheIdentity,
        builder: SourceCacheBuilder,
        *,
        context: SourceCacheBuildContext,
        source_signature: str | None = None,
        refresh: bool = False,
        replacing: str | None = None,
    ) -> SourceCacheGeneration:
        """Build a generation of *identity* and publish it as current.

        With *replacing*, the build publishes only while that generation is current,
        and raises :class:`_Superseded` otherwise, having published nothing.
        """
        if context.build_class == "unsupported":
            raise SourceCacheBuildError("unsupported source-cache build class")
        if identity.provider == NODE_OUTPUT_PROVIDER:
            raise SourceCacheBuildError("node-output snapshots are published, not built here")
        declared = getattr(builder, "build_class", context.build_class)
        if declared != context.build_class:
            raise SourceCacheBuildError("builder build class does not match source-cache context")
        lock = self._identity_lock(identity)
        with lock:
            if not refresh:
                try:
                    return self.open_generation(identity)
                except (FileNotFoundError, SourceCacheLegacyLayoutError):
                    pass
            identity_dir = self.identity_path(identity)
            identity_dir.mkdir(parents=True, exist_ok=True)
            _ensure_identity_marker(identity_dir, identity.provider)
            generations_dir = identity_dir / "generations"
            generations_dir.mkdir(parents=True, exist_ok=True)
            ensure_disk_headroom(identity_dir)
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
                build_started = time.monotonic()
                context.checkpoint()
                with context.stage("input_snapshot_read"):
                    output = builder.build(context)
                context.checkpoint()
                with context.stage("input_snapshot_write"):
                    written_digests = self._write_output(output, staging, context)
                context.checkpoint()
                # Validate before publication, including every footer and the scan schema.
                parts = describe_parts(staging, digests=written_digests)
                schema = scan_parts(part_paths(staging)).collect_schema()
                columns = {name: str(dtype) for name, dtype in schema.items()}
                metadata = SourceCacheMetadata(
                    identity.digest,
                    identity.payload,
                    identity.schema_version,
                    generation_id,
                    source_signature,
                    parts,
                    sum(part.size_bytes for part in parts),
                    sum(part.row_count for part in parts),
                    len(columns),
                    columns,
                    time.time(),
                    getattr(context.profile, "value", str(context.profile)),
                    context.build_class,
                    build_seconds=time.monotonic() - build_started,
                    polars_version=polars_version(),
                )
                metadata_path = staging / "meta.json"
                atomic_write_text(metadata_path, canonical_json(metadata.to_dict()))
                context.checkpoint()
                # Self-validate the staged directory using the same strict validator after rename.
                with self._retiring() as retired:
                    if replacing is not None and self._pointed_generation_id(identity) != replacing:
                        raise _Superseded(replacing)
                    if not context.defer_retirement:
                        retired.extend(self._retire_unleased_locked(identity))
                    final_dir = generations_dir / generation_id
                    staging.replace(final_dir)
                    with self._lock:
                        self._verified_generations.add(
                            _verification_key(
                                identity.digest,
                                generation_id,
                                parts,
                                tuple((final_dir / part.name).stat() for part in parts),
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
                    self.retire_unleased(identity)
                return generation
            except BaseException:
                shutil.rmtree(staging, ignore_errors=True)
                if final_dir is not None and not published:
                    with self._lease_lock:
                        if not self._has_live_holders_locked(final_dir):
                            self._forget_verified(identity.digest, generation_id)
                            shutil.rmtree(final_dir, ignore_errors=True)
                raise

    @contextlib.contextmanager
    def lease(self, identity: SourceCacheIdentity) -> Iterator[SourceCacheGeneration]:
        with self._identity_lock(identity):
            while True:
                generation_id = self._read_pointer(identity)
                try:
                    if not self._acquire_input_lease(identity, generation_id, require_current=True):
                        continue
                except SourceCacheGenerationMissingError as exc:
                    # The pointer still names a generation whose directory is gone.
                    raise FileNotFoundError(
                        f"source-cache generation {generation_id} is not available: {exc}"
                    ) from exc
                try:
                    generation = self._metadata_from_path(identity, generation_id)
                except _MapPartsNeedReencoding:
                    # Still leased while it is copied; the loop then leases what is current.
                    try:
                        self._reencoded(identity, generation_id)
                    finally:
                        self._release_input_lease(identity, generation_id)
                    continue
                except BaseException as exc:
                    self._release_input_lease(identity, generation_id)
                    if isinstance(exc, SourceCacheGenerationMissingError):
                        raise FileNotFoundError(
                            f"source-cache generation {generation_id} is not available: {exc}"
                        ) from exc
                    raise
                break
        try:
            yield generation
        finally:
            with self._identity_lock(identity):
                self._release_input_lease(identity, generation_id)

    @contextlib.contextmanager
    def lease_generation(
        self, identity: SourceCacheIdentity, generation_id: str
    ) -> Iterator[SourceCacheGeneration]:
        """Pin and yield exactly *generation_id*, current or not.

        A spawned worker reads the generation its parent leased, never whatever
        is current. Validation uses the same metadata, digest, and verified-memo
        path as :meth:`lease`; an unknown or retired generation raises
        :class:`SourceCacheGenerationMissingError`.
        """
        with self._identity_lock(identity):
            self._acquire_input_lease(identity, generation_id, require_current=False)
            try:
                generation = self._metadata_from_path(identity, generation_id)
            except _MapPartsNeedReencoding as exc:
                # Superseded: opening the identity publishes its re-encoded copy.
                self._release_input_lease(identity, generation_id)
                raise SourceCacheGenerationMissingError(
                    "source-cache generation is re-encoded when its identity is opened"
                ) from exc
            except BaseException:
                self._release_input_lease(identity, generation_id)
                raise
        try:
            yield generation
        finally:
            with self._identity_lock(identity):
                self._release_input_lease(identity, generation_id)

    def leased_generation_ids(self, identity: SourceCacheIdentity) -> frozenset[str]:
        """Return the generations of *identity* this process currently leases.

        This is an inspection utility for the supervising process.
        """
        with self._identity_lock(identity):
            with self._lock:
                return frozenset(
                    generation_id
                    for (digest, generation_id), count in self._leases.items()
                    if digest == identity.digest and count > 0
                )

    def _acquire_input_lease(
        self, identity: SourceCacheIdentity, generation_id: str, *, require_current: bool
    ) -> bool:
        """Record an input lease before validating it, under the store-wide lock."""
        _validate_generation_id(generation_id)
        with self._lease_lock:
            if require_current and self._read_pointer(identity) != generation_id:
                return False
            generation_dir = self.identity_path(identity) / "generations" / generation_id
            if not generation_dir.is_dir():
                self._forget_verified(identity.digest, generation_id)
                raise SourceCacheGenerationMissingError("source-cache generation does not exist")
            key = (identity.digest, generation_id)
            with self._lock:
                previous = self._leases.get(key, 0)
                self._leases[key] = previous + 1
            if previous == 0:
                try:
                    (generation_dir / f"{_LEASE_PREFIX}{self._own_token()}").touch()
                except BaseException:
                    with self._lock:
                        del self._leases[key]
                    raise
            return True

    def _release_input_lease(self, identity: SourceCacheIdentity, generation_id: str) -> None:
        with self._retiring() as retired:
            key = (identity.digest, generation_id)
            with self._lock:
                remaining = self._leases[key] - 1
                if remaining:
                    self._leases[key] = remaining
                    return
                del self._leases[key]
            generation_dir = self.identity_path(identity) / "generations" / generation_id
            # The acquisition that took this lease made the process's token.
            (generation_dir / f"{_LEASE_PREFIX}{self._own_token()}").unlink(missing_ok=True)
            retired.extend(self._retire_unleased_locked(identity))

    def retire_unleased(self, identity: SourceCacheIdentity) -> None:
        """Delete every generation of *identity* that is neither current nor leased.

        The supervising parent of a spawned build calls this after publication:
        the child deferred retirement because only this process knows which
        generations its own executions still lease.
        """
        with self._identity_lock(identity), self._retiring() as retired:
            retired.extend(self._retire_unleased_locked(identity))

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

    def _retire_unleased_locked(
        self, identity: SourceCacheIdentity, *, force: bool = False
    ) -> list[Path]:
        """Retire unpointed, unheld generations. Caller holds the lease lock.

        Returns the renamed directories, for the caller to delete once the lock
        is released (:meth:`_delete_retired`).
        """
        generations_dir = self.identity_path(identity) / "generations"
        if not generations_dir.exists():
            return []
        try:
            current = self._read_pointer(identity)
        except FileNotFoundError:
            current = None
        if not (force or self._retire_grace_elapsed(identity)):
            return []
        retired: list[Path] = []
        for candidate in generations_dir.iterdir():
            if not candidate.is_dir() or candidate.name == current:
                continue
            if self._has_live_holders_locked(candidate):
                continue
            path = self._retire_generation_locked(identity.digest, candidate.name)
            if path is not None:
                retired.append(path)
        return retired

    def _generation_dir(self, identity_digest: str, generation_id: str) -> Path:
        return self.inputs_root / identity_digest / "generations" / generation_id

    def _retire_generation_locked(self, identity_digest: str, generation_id: str) -> Path | None:
        """Rename one generation out of selection. Caller holds the lease lock.

        Its files are deleted after the lock is released, so a reader's open
        handle can never leave a half-deleted generation behind: on Windows such
        a handle refuses the rename, and the generation stays whole, unpointed
        and selectable by id until a later retirement renames it.
        """
        generation_dir = self._generation_dir(identity_digest, generation_id)
        retired = generation_dir.parent.parent / f"{_RETIRED_PREFIX}{uuid.uuid4().hex}"
        try:
            generation_dir.rename(retired)
        except FileNotFoundError:
            return None
        except PermissionError as exc:
            logger.warning(
                "source_cache_retirement_deferred",
                identity_digest=identity_digest,
                generation_id=generation_id,
                error=str(exc),
            )
            return None
        self._forget_verified(identity_digest, generation_id)
        return retired

    @contextlib.contextmanager
    def _retiring(self) -> Iterator[list[Path]]:
        """Hold the lease lock, then delete what was retired under it once it is released."""
        retired: list[Path] = []
        try:
            with self._lease_lock:
                yield retired
        finally:
            self._delete_retired(retired)

    @staticmethod
    def _delete_retired(paths: list[Path]) -> None:
        """Delete retired directories; one that survives is swept at the next start."""
        for path in paths:
            shutil.rmtree(path, ignore_errors=True)
            if path.exists():
                logger.warning("source_cache_retired_delete_failed", path=str(path))

    def _cleanup_retired(self) -> None:
        for retired in self.inputs_root.glob(f"*/{_RETIRED_PREFIX}*"):
            if retired.is_dir() and not retired.is_symlink():
                shutil.rmtree(retired, ignore_errors=True)

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
            with self._lease_lock:
                try:
                    if self._read_pointer(identity) == generation_id:
                        return "published"
                except (FileNotFoundError, SourceCacheCorruptError):
                    pass
                removed_generation = False
                generation_dir = identity_dir / "generations" / generation_id
                if generation_dir.is_dir() and not self._has_live_holders_locked(generation_dir):
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

    def published_generation_id(self, identity: SourceCacheIdentity) -> str:
        """The published generation's id, read from metadata alone.

        Reads the current pointer, validates its generation id and identity
        digest, and checks that the generation's directory and ``meta.json``
        exist. Unlike :meth:`open_generation` it takes no identity lock and
        opens no part, so it never compares part sizes, reads a footer or
        hashes content: the generation is published, not verified. Raises
        ``FileNotFoundError`` when no pointer or generation exists and
        :class:`SourceCacheCorruptError` when the pointer cannot be read.
        """
        generation_id = self._read_pointer(identity)
        metadata_path = self.identity_path(identity) / "generations" / generation_id / "meta.json"
        if not metadata_path.is_file():
            raise FileNotFoundError(metadata_path)
        return generation_id

    def clear(self, identity: SourceCacheIdentity) -> None:
        with self._identity_lock(identity), self._retiring() as retired:
            self._pointer_path(identity).unlink(missing_ok=True)
            retired.extend(self._retire_unleased_locked(identity, force=True))

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
