"""Node-output snapshots in the shared source snapshot store.

A node's full output is stored once per *snapshot identity* — a slot
``(pipeline source file, node, source, semantics class)`` plus the node's
checked data signature — as immutable generations beside the input snapshots
of :class:`haute._source_cache.SourceCacheStore`. Both kinds share the store
root, byte quota, generation cap, and environment variables.

Node-output generations can be published, leased, evicted, and cleared by
several processes at once (the server, spawned workers, and CLI runs), so this
module adds two cross-process file locks on top of the store's process-local
state:

- a per-identity **publication lock**, held by one writer from its freshness
  re-check to its first lease;
- a store-wide **lease lock**, always taken after a publication lock, under
  which lease acquisition, publication through the publisher's first lease,
  and retirement are each atomic.

Every lease is also a marker file inside its generation naming the owning
process's token. A process holds an exclusive lock on its own token file for
its lifetime, so a marker whose token lock can be acquired belongs to a
process that has exited. Retirement renames a generation to a private name
under the lease lock and deletes its files only after the lock is released.
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import os
import shutil
import threading
import time
import uuid
from collections.abc import Iterator, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

import polars as pl

from haute._cache import (
    CacheConsumer,
    GraphFingerprintMemo,
    canonical_json,
    checked_cache_inputs,
    graph_fingerprint,
)
from haute._execution_context import ExecutionProfile
from haute._file_lock import _acquire_file_lock, _release_file_lock
from haute._file_ops import atomic_write_text, remove_tree
from haute._json_shred._publication import (
    _assert_cache_path_ancestors_plain,
    _open_cache_lock_file,
)
from haute._logging import get_logger
from haute._source_cache import (
    NODE_OUTPUT_PROVIDER,
    SourceCacheCorruptError,
    SourceCacheError,
    SourceCacheGeneration,
    SourceCacheGenerationMissingError,
    SourceCacheIdentity,
    SourceCacheMetadata,
    SourceCacheQuotaExceededError,
    SourceCacheStore,
    _sha256_file,
    _validate_generation_id,
    _validate_staging_token,
    new_staging_token,
)
from haute._types import PipelineGraph

logger = get_logger(component="node_snapshots")

NODE_SNAPSHOT_METADATA_VERSION = 2
NODE_SNAPSHOT_EXECUTION_SEMANTICS_VERSION = "node-snapshot:v1"
BOUNDED_SEMANTICS_CLASS = "bounded"
_SLOT_INDEX_SCHEMA_VERSION = 1
_LAST_USED_UPDATE_INTERVAL_SECONDS = 60.0
# Lease markers sit directly in the generation directory under short names:
# identity digest, generation id, and marker together must stay inside the
# traditional Windows path limit beneath long temporary roots.
_LEASE_PREFIX = ".lease-"
_TOKEN_LENGTH = 12
_RETIRED_PREFIX = ".retired-"

Retention = Literal["pinned", "automatic"]
SlotState = Literal["current", "stale", "missing", "corrupt"]
PublicationOutcome = Literal["published", "superseded"]

# CACHE-S06 measured that an interactive preview carries the same rows and
# schema as a bounded execution but not the same row order, and row order is
# not part of the snapshot contract. An admitted preview therefore reads and
# writes the ``bounded`` class; this constant changes only the two mappings below.
PREVIEW_SHARES_BOUNDED_SEMANTICS = True

_BOUNDED_SNAPSHOT_PROFILES = frozenset(
    {
        ExecutionProfile.TRAINING_PREP,
        ExecutionProfile.OPTIMISER_SETUP,
        ExecutionProfile.EXPLORE_ANALYSIS,
        ExecutionProfile.AUTO_RANGE,
        ExecutionProfile.LAZY_SINK,
        ExecutionProfile.CHUNKED_MAP_REDUCE,
        ExecutionProfile.NODE_SNAPSHOT,
    }
)


def _fault_point(name: str) -> None:
    """Deterministic pause point for cross-process interleaving tests."""
    del name


class NodeSnapshotMultiFrameUnsupportedError(SourceCacheError):
    """A multi-frame output reached a node-output snapshot write."""

    error_code = "node_snapshot_multi_frame_unsupported"


class NodeSnapshotQuotaRejectedError(SourceCacheQuotaExceededError):
    """Quota still rejects a node-output publication after automatic eviction.

    The completed staged artifact is not deleted: :attr:`artifact` now belongs
    to the caller, which reads it for the rest of its request and closes it.
    """

    def __init__(self, message: str, artifact: NodeSnapshotArtifact) -> None:
        super().__init__(message)
        self.artifact = artifact


def snapshot_write_class(
    profile: ExecutionProfile | str,
    *,
    preview_admitted: bool = False,
) -> str | None:
    """Return the semantics class a profile writes, or ``None`` when it never writes."""
    checked = ExecutionProfile(profile)
    if checked in _BOUNDED_SNAPSHOT_PROFILES:
        return BOUNDED_SEMANTICS_CLASS
    if (
        checked == ExecutionProfile.PREVIEW_EAGER
        and preview_admitted
        and PREVIEW_SHARES_BOUNDED_SEMANTICS
    ):
        return BOUNDED_SEMANTICS_CLASS
    return None


def snapshot_read_classes(profile: ExecutionProfile | str) -> frozenset[str]:
    """Return the semantics classes a profile may read."""
    checked = ExecutionProfile(profile)
    if checked in _BOUNDED_SNAPSHOT_PROFILES:
        return frozenset({BOUNDED_SEMANTICS_CLASS})
    if checked == ExecutionProfile.PREVIEW_EAGER and PREVIEW_SHARES_BOUNDED_SEMANTICS:
        return frozenset({BOUNDED_SEMANTICS_CLASS})
    return frozenset()


@dataclass(frozen=True, slots=True)
class NodeSnapshotColumns:
    """The column set a generation holds or a reader demands (``None`` means all)."""

    names: frozenset[str] | None

    @classmethod
    def all(cls) -> NodeSnapshotColumns:
        return cls(None)

    @classmethod
    def of(cls, names: object) -> NodeSnapshotColumns:
        if isinstance(names, str):
            raise TypeError("column names must be an iterable of strings, not a string")
        checked = frozenset(names)  # type: ignore[call-overload]
        if any(not isinstance(name, str) or not name for name in checked):
            raise ValueError("column names must be non-empty strings")
        return cls(checked)

    @property
    def is_all(self) -> bool:
        return self.names is None

    def covers(self, demand: NodeSnapshotColumns) -> bool:
        if self.names is None:
            return True
        if demand.names is None:
            return False
        return demand.names <= self.names

    def strictly_contains(self, other: NodeSnapshotColumns) -> bool:
        return self.covers(other) and not other.covers(self)

    def union(self, other: NodeSnapshotColumns) -> NodeSnapshotColumns:
        if self.names is None or other.names is None:
            return NodeSnapshotColumns.all()
        return NodeSnapshotColumns(self.names | other.names)

    def to_json(self) -> str | list[str]:
        return "all" if self.names is None else sorted(self.names)

    @classmethod
    def from_json(cls, value: object) -> NodeSnapshotColumns:
        if value == "all":
            return cls.all()
        if not isinstance(value, list):
            raise ValueError("node-output column set must be 'all' or a list")
        if len(set(value)) != len(value):
            raise ValueError("node-output column set must not repeat columns")
        return cls.of(value)


@dataclass(frozen=True, slots=True)
class NodeSnapshotSlot:
    """Consumer-independent location of one node's output data."""

    pipeline_source_file: str
    node_id: str
    source: str
    semantics_class: str

    def __post_init__(self) -> None:
        for name in ("pipeline_source_file", "node_id", "source", "semantics_class"):
            value = getattr(self, name)
            if not isinstance(value, str) or not value:
                raise ValueError(f"node snapshot slot {name} must be a non-empty string")

    @property
    def descriptor(self) -> dict[str, object]:
        return {
            "pipeline_source_file": self.pipeline_source_file,
            "node_id": self.node_id,
            "source": self.source,
            "semantics_class": self.semantics_class,
        }

    @property
    def digest(self) -> str:
        payload = {"schema_version": _SLOT_INDEX_SCHEMA_VERSION, **self.descriptor}
        return hashlib.sha256(canonical_json(payload).encode("utf-8")).hexdigest()

    def identity(self, signature: str) -> SourceCacheIdentity:
        if not isinstance(signature, str) or not signature:
            raise ValueError("node snapshot signature must be a non-empty string")
        return SourceCacheIdentity(
            provider=NODE_OUTPUT_PROVIDER,
            # The descriptor key avoids "signature", which identity redaction
            # treats as credential material.
            descriptor={**self.descriptor, "data_fingerprint": signature},
        )

    @classmethod
    def from_identity(cls, identity: SourceCacheIdentity) -> tuple[NodeSnapshotSlot, str]:
        if identity.provider != NODE_OUTPUT_PROVIDER:
            raise ValueError("identity is not a node-output snapshot identity")
        descriptor = dict(identity.descriptor)
        signature = descriptor.pop("data_fingerprint", None)
        if set(descriptor) != {"pipeline_source_file", "node_id", "source", "semantics_class"}:
            raise ValueError("node-output identity descriptor has an unexpected shape")
        if not isinstance(signature, str) or not signature:
            raise ValueError("node-output identity has no signature")
        slot = cls(**{key: str(value) for key, value in descriptor.items()})
        return slot, signature


def node_snapshot_signature(
    graph: PipelineGraph,
    node_id: str,
    *,
    source: str,
    semantics_class: str,
    enforce_contracts: bool,
    memo: GraphFingerprintMemo | None = None,
) -> str:
    """Return the checked data signature of *node_id*'s output.

    It never contains snapshot generations or column sets: generations are
    recorded as dependencies and column sets widen a generation.
    """
    from haute._builders import resolve_instance_nodes
    from haute._dataframe_execution_cache import _upstream_subgraph
    from haute.execution import (
        canonical_dataframe_execution_graph,
        dataframe_graph_input_fingerprint,
    )

    if type(enforce_contracts) is not bool:
        raise TypeError("enforce_contracts must be a bool")
    # An instance node executes its original's config, so its signature does too.
    canonical = canonical_dataframe_execution_graph(resolve_instance_nodes(graph))
    lineage = _upstream_subgraph(canonical, node_id)
    inputs = checked_cache_inputs(
        CacheConsumer.NODE_SNAPSHOT_SIGNATURE,
        {
            "lineage_fingerprint": graph_fingerprint(lineage, memo=memo),
            "runtime_input_fingerprint": dataframe_graph_input_fingerprint(
                canonical,
                target_node_id=node_id,
                source=source,
                memo=memo,
            ),
            "source": source,
            "semantics_class": semantics_class,
            "enforce_contracts": enforce_contracts,
            "preamble_supplied": bool(canonical.preamble),
            "execution_semantics_version": NODE_SNAPSHOT_EXECUTION_SEMANTICS_VERSION,
        },
    )
    digest = hashlib.sha256(inputs.canonical_bytes).hexdigest()
    return f"node-snapshot:v{inputs.contract.version}:{digest}"


def pipeline_source_file_key(graph: PipelineGraph) -> str:
    """Return the resolved pipeline file that scopes a graph's snapshot slots."""
    from haute._path_resolution import _infer_project_root

    project_root = _infer_project_root(project_root=None, source_file=graph.source_file)
    source_file = Path(graph.source_file or "")
    if not source_file.is_absolute():
        source_file = project_root / source_file
    return str(source_file.resolve())


@dataclass(frozen=True, slots=True)
class NodeSnapshotGeneration:
    """One validated node-output generation and its freshness facts."""

    identity: SourceCacheIdentity
    generation: SourceCacheGeneration
    columns: NodeSnapshotColumns
    dependencies: Mapping[str, str]
    fresh: bool
    retention: Retention
    last_used: float

    @property
    def generation_id(self) -> str:
        return self.generation.generation_id

    @property
    def lazy_frame(self) -> pl.LazyFrame:
        return self.generation.lazy_frame


@dataclass(frozen=True, slots=True)
class NodeSnapshotSlotStatus:
    state: SlotState
    identity: SourceCacheIdentity
    generation: NodeSnapshotGeneration | None = None


class NodeSnapshotArtifact:
    """A completed node-output artifact owned by the request that produced it."""

    def __init__(self, identity: SourceCacheIdentity, directory: Path) -> None:
        self.identity = identity
        self.directory = directory
        self.data_path = directory / "data.parquet"
        self._released = False

    def lazy_frame(self) -> pl.LazyFrame:
        if self._released:
            raise RuntimeError("node-output artifact is no longer owned by this request")
        return pl.scan_parquet(self.data_path)

    def _mark_published(self) -> None:
        self._released = True

    def close(self) -> None:
        if self._released:
            return
        self._released = True
        shutil.rmtree(self.directory, ignore_errors=True)
        if self.directory.exists():
            logger.warning(
                "node_snapshot_artifact_removal_failed",
                path=str(self.directory),
                identity_digest=self.identity.digest,
            )

    def __enter__(self) -> NodeSnapshotArtifact:
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()


class NodeSnapshotPublication:
    """The owned outcome of one publication attempt.

    ``published``: the writer holds a lease on the generation it published.
    ``superseded``: the writer keeps its own artifact as a request-owned file.
    Either way the writer continues from its own data and closes this handle
    when its request ends.
    """

    def __init__(
        self,
        outcome: PublicationOutcome,
        *,
        store: NodeSnapshotStore,
        identity: SourceCacheIdentity,
        generation: NodeSnapshotGeneration | None = None,
        artifact: NodeSnapshotArtifact | None = None,
    ) -> None:
        if (outcome == "published") != (generation is not None):
            raise ValueError("a published outcome carries exactly its generation")
        if (outcome == "superseded") != (artifact is not None):
            raise ValueError("a superseded outcome carries exactly its artifact")
        self.outcome = outcome
        self.identity = identity
        self.generation = generation
        self.artifact = artifact
        self._store = store
        self._closed = False

    @property
    def lazy_frame(self) -> pl.LazyFrame:
        if self.generation is not None:
            return self.generation.lazy_frame
        assert self.artifact is not None
        return self.artifact.lazy_frame()

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        if self.generation is not None:
            self._store._release_node_lease(self.identity, self.generation.generation_id)
        elif self.artifact is not None:
            self.artifact.close()

    def __enter__(self) -> NodeSnapshotPublication:
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()


class _StoreFileLock:
    """Thread-reentrant, cross-process exclusive lock on one plain lock file."""

    def __init__(self, path: Path) -> None:
        self._path = path
        self._thread_lock = threading.RLock()
        self._depth = 0
        self._handle: Any | None = None

    def __enter__(self) -> _StoreFileLock:
        self._thread_lock.acquire()
        if self._depth:
            self._depth += 1
            return self
        handle: Any | None = None
        try:
            _assert_cache_path_ancestors_plain(self._path)
            self._path.parent.mkdir(parents=True, exist_ok=True)
            handle = _open_cache_lock_file(self._path)
            handle.seek(0, os.SEEK_END)
            if handle.tell() == 0:
                handle.write(b"\0")
                handle.flush()
            _acquire_file_lock(handle)
            self._handle = handle
            self._depth = 1
            return self
        except BaseException:
            if handle is not None:
                handle.close()
            self._thread_lock.release()
            raise

    def __exit__(self, *exc_info: object) -> None:
        try:
            self._depth -= 1
            if self._depth:
                return
            handle, self._handle = self._handle, None
            if handle is None:
                raise RuntimeError("node snapshot lock lost its file handle")
            try:
                _release_file_lock(handle)
            finally:
                handle.close()
        finally:
            self._thread_lock.release()


@dataclass(slots=True)
class _NodeSnapshotCoordination:
    """Cross-process locks and this process's lease-owner token for one store root."""

    lease_lock: _StoreFileLock
    guard: threading.Lock = field(default_factory=threading.Lock)
    publication_locks: dict[str, _StoreFileLock] = field(default_factory=dict)
    token: str | None = None
    token_handle: Any | None = None


_COORDINATION_GUARD = threading.Lock()
_COORDINATION: dict[tuple[Path, int], _NodeSnapshotCoordination] = {}


def _is_token(value: str) -> bool:
    return len(value) == _TOKEN_LENGTH and all(
        character in "0123456789abcdef" for character in value
    )


def _is_identity_digest(value: str) -> bool:
    return len(value) == 64 and all(character in "0123456789abcdef" for character in value)


class NodeSnapshotStore(SourceCacheStore):
    """The shared snapshot store, including node-output snapshots."""

    def __init__(
        self,
        root: str | Path,
        *,
        max_bytes: int | None = None,
        max_generations: int | None = None,
        retire_grace_seconds: float | None = None,
    ) -> None:
        super().__init__(
            root,
            max_bytes=max_bytes,
            max_generations=max_generations,
            retire_grace_seconds=retire_grace_seconds,
        )
        self._locks_dir = self.inputs_root / ".locks"
        self._processes_dir = self.inputs_root / ".processes"
        self._slots_dir = self.inputs_root / ".node-slots"
        key = (self.inputs_root.resolve(), os.getpid())
        with _COORDINATION_GUARD:
            coordination = _COORDINATION.get(key)
            if coordination is None:
                coordination = _NodeSnapshotCoordination(
                    lease_lock=_StoreFileLock(self._locks_dir / "leases.lock")
                )
                _COORDINATION[key] = coordination
        self._coordination = coordination
        self._cleanup_retired()

    # ------------------------------------------------------------------ paths

    def _publication_lock(self, identity: SourceCacheIdentity) -> _StoreFileLock:
        coordination = self._coordination
        with coordination.guard:
            lock = coordination.publication_locks.get(identity.digest)
            if lock is None:
                lock = _StoreFileLock(self._locks_dir / f"publication-{identity.digest}.lock")
                coordination.publication_locks[identity.digest] = lock
            return lock

    def _generation_dir(self, identity_digest: str, generation_id: str) -> Path:
        return self.inputs_root / identity_digest / "generations" / generation_id

    def _slot_index_path(self, slot: NodeSnapshotSlot) -> Path:
        return self._slots_dir / f"{slot.digest}.json"

    @staticmethod
    def _require_node_output(identity: SourceCacheIdentity) -> None:
        if identity.provider != NODE_OUTPUT_PROVIDER:
            raise ValueError("identity is not a node-output snapshot identity")

    def _cleanup_retired(self) -> None:
        for retired in self.inputs_root.glob(f"*/{_RETIRED_PREFIX}*"):
            if retired.is_dir() and not retired.is_symlink():
                shutil.rmtree(retired, ignore_errors=True)

    # --------------------------------------------------------- process tokens

    def _own_token(self) -> str:
        coordination = self._coordination
        with coordination.guard:
            if coordination.token is None:
                token = uuid.uuid4().hex[:_TOKEN_LENGTH]
                path = self._processes_dir / f"{token}.lock"
                _assert_cache_path_ancestors_plain(path)
                path.parent.mkdir(parents=True, exist_ok=True)
                handle = _open_cache_lock_file(path)
                try:
                    handle.write(b"\0")
                    handle.flush()
                    if not _acquire_file_lock(handle, blocking=False):
                        raise RuntimeError("a fresh lease-owner token file is already locked")
                except BaseException:
                    handle.close()
                    raise
                # The handle stays open, and its lock held, for the life of the process.
                coordination.token = token
                coordination.token_handle = handle
            return coordination.token

    def _token_alive(self, token: str) -> bool:
        if token == self._coordination.token:
            return True
        if not _is_token(token):
            return False
        path = self._processes_dir / f"{token}.lock"
        if not path.exists():
            return False
        handle = _open_cache_lock_file(path)
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
                "node_snapshot_dead_lease_marker_removed",
                identity_digest=identity_digest,
                generation_id=generation_id,
            )
        return live

    # ------------------------------------------------------------- pointers

    def _current_generation_id(self, identity_digest: str) -> str | None:
        path = self.inputs_root / identity_digest / "current.json"
        try:
            raw = path.read_text(encoding="utf-8")
        except FileNotFoundError:
            return None
        try:
            pointer = json.loads(raw)
            generation_id = _validate_generation_id(pointer["generation_id"])
            if pointer.get("identity_digest") != identity_digest:
                raise ValueError("invalid current pointer")
            return generation_id
        except (ValueError, KeyError, TypeError, json.JSONDecodeError) as exc:
            raise SourceCacheCorruptError("source-cache current pointer is corrupt") from exc

    def _write_pointer_locked(self, identity: SourceCacheIdentity, generation_id: str) -> None:
        atomic_write_text(
            self._pointer_path(identity),
            canonical_json({"identity_digest": identity.digest, "generation_id": generation_id}),
        )

    # ----------------------------------------------------------- slot index

    def _read_slot_index(self, slot: NodeSnapshotSlot) -> dict[str, Any]:
        path = self._slot_index_path(slot)
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return {"identities": {}, "pinned_identity": None}
        except ValueError as exc:
            raise SourceCacheCorruptError("node snapshot slot index is corrupt") from exc
        if (
            not isinstance(raw, dict)
            or raw.get("schema_version") != _SLOT_INDEX_SCHEMA_VERSION
            or raw.get("slot_digest") != slot.digest
            or raw.get("slot") != slot.descriptor
            or not isinstance(raw.get("identities"), dict)
            or not (raw.get("pinned_identity") is None or isinstance(raw["pinned_identity"], str))
        ):
            raise SourceCacheCorruptError("node snapshot slot index is corrupt")
        return {"identities": dict(raw["identities"]), "pinned_identity": raw["pinned_identity"]}

    def _write_slot_index_locked(self, slot: NodeSnapshotSlot, index: Mapping[str, Any]) -> None:
        path = self._slot_index_path(slot)
        identities = dict(index["identities"])
        if not identities:
            path.unlink(missing_ok=True)
            return
        pinned = index["pinned_identity"]
        if pinned not in identities:
            pinned = None
        path.parent.mkdir(parents=True, exist_ok=True)
        atomic_write_text(
            path,
            canonical_json(
                {
                    "schema_version": _SLOT_INDEX_SCHEMA_VERSION,
                    "slot_digest": slot.digest,
                    "slot": slot.descriptor,
                    "identities": identities,
                    "pinned_identity": pinned,
                }
            ),
        )

    def _prune_identity_locked(self, identity: SourceCacheIdentity) -> None:
        """Drop *identity* from its slot index once it no longer has a current generation."""
        slot, _signature = NodeSnapshotSlot.from_identity(identity)
        index = self._read_slot_index(slot)
        if identity.digest in index["identities"]:
            del index["identities"][identity.digest]
            self._write_slot_index_locked(slot, index)

    # ------------------------------------------------------------ metadata

    def _node_output_facts(
        self, identity: SourceCacheIdentity, generation: SourceCacheGeneration
    ) -> tuple[NodeSnapshotColumns, dict[str, str]]:
        facts = generation.metadata.node_output
        slot, signature = NodeSnapshotSlot.from_identity(identity)
        try:
            if (
                not isinstance(facts, Mapping)
                or facts.get("metadata_version") != NODE_SNAPSHOT_METADATA_VERSION
                or facts.get("slot") != slot.descriptor
                or facts.get("slot_digest") != slot.digest
                or facts.get("signature") != signature
            ):
                raise ValueError("node-output metadata does not match its identity")
            columns = NodeSnapshotColumns.from_json(facts.get("column_set"))
            if columns.names is not None and not columns.names <= set(generation.metadata.columns):
                raise ValueError("node-output column set names columns the data does not hold")
            raw_dependencies = facts.get("dependencies")
            if not isinstance(raw_dependencies, Mapping):
                raise ValueError("node-output dependencies must be a mapping")
            dependencies: dict[str, str] = {}
            for digest, generation_id in raw_dependencies.items():
                if not isinstance(digest, str) or not _is_identity_digest(digest):
                    raise ValueError("node-output dependency identity is invalid")
                dependencies[digest] = _validate_generation_id(generation_id)
            if identity.digest in dependencies:
                raise ValueError("node-output generation depends on its own identity")
        except (TypeError, ValueError) as exc:
            raise SourceCacheCorruptError("node-output generation metadata is corrupt") from exc
        return columns, dependencies

    def _dependencies_current_or_cleared(self, dependencies: Mapping[str, str]) -> bool:
        for identity_digest, generation_id in dependencies.items():
            current = self._current_generation_id(identity_digest)
            if current is not None and current != generation_id:
                return False
        return True

    @staticmethod
    def _last_used(generation_dir: Path, fallback: float) -> float:
        """A generation's last-used time is its metadata file's modification time."""
        try:
            return (generation_dir / "meta.json").stat().st_mtime
        except OSError:
            return fallback

    def _touch_last_used(self, generation: SourceCacheGeneration) -> None:
        now = time.time()
        previous = self._last_used(generation.data_path.parent, 0.0)
        if now - previous < _LAST_USED_UPDATE_INTERVAL_SECONDS:
            return
        try:
            # An atomic filesystem-metadata write: no content change, no temporary file.
            os.utime(generation.metadata_path, (now, now))
        except OSError as exc:
            logger.warning(
                "node_snapshot_last_used_update_failed",
                generation_id=generation.generation_id,
                error=str(exc),
            )

    def _describe(
        self,
        identity: SourceCacheIdentity,
        generation: SourceCacheGeneration,
        *,
        pinned_identity: str | None,
    ) -> NodeSnapshotGeneration:
        columns, dependencies = self._node_output_facts(identity, generation)
        current = self._current_generation_id(identity.digest)
        retention: Retention = (
            "pinned"
            if pinned_identity == identity.digest and current == generation.generation_id
            else "automatic"
        )
        return NodeSnapshotGeneration(
            identity=identity,
            generation=generation,
            columns=columns,
            dependencies=dependencies,
            fresh=self._dependencies_current_or_cleared(dependencies),
            retention=retention,
            last_used=self._last_used(generation.data_path.parent, generation.metadata.created_at),
        )

    # ------------------------------------------------------------ reading

    def latest_generation(self, identity: SourceCacheIdentity) -> NodeSnapshotGeneration | None:
        """Return the identity's latest generation, fresh or stale, without leasing it."""
        self._require_node_output(identity)
        generation_id = self._current_generation_id(identity.digest)
        if generation_id is None:
            return None
        generation = self._metadata_from_path(identity, generation_id)
        slot, _signature = NodeSnapshotSlot.from_identity(identity)
        pinned = self._read_slot_index(slot)["pinned_identity"]
        return self._describe(identity, generation, pinned_identity=pinned)

    def describe_generation(
        self, identity: SourceCacheIdentity, generation: SourceCacheGeneration
    ) -> NodeSnapshotGeneration:
        self._require_node_output(identity)
        slot, _signature = NodeSnapshotSlot.from_identity(identity)
        pinned = self._read_slot_index(slot)["pinned_identity"]
        return self._describe(identity, generation, pinned_identity=pinned)

    def slot_status(self, slot: NodeSnapshotSlot, signature: str) -> NodeSnapshotSlotStatus:
        """Report ``current``, ``stale``, ``missing``, or ``corrupt`` for one signature."""
        identity = slot.identity(signature)
        try:
            latest = self.latest_generation(identity)
        except SourceCacheCorruptError:
            return NodeSnapshotSlotStatus("corrupt", identity)
        if latest is not None:
            return NodeSnapshotSlotStatus("current" if latest.fresh else "stale", identity, latest)
        index = self._read_slot_index(slot)
        for digest in index["identities"]:
            if digest != identity.digest and self._current_generation_id(digest) is not None:
                return NodeSnapshotSlotStatus("stale", identity)
        return NodeSnapshotSlotStatus("missing", identity)

    # ------------------------------------------------------------ leasing

    def _acquire_node_lease(
        self,
        identity: SourceCacheIdentity,
        generation_id: str,
        *,
        require_current: bool,
    ) -> bool:
        """Record one lease; return False when *require_current* and the pointer moved.

        The marker exists before the generation is validated, so a concurrent
        retirement can never remove files from under that validation.
        """
        _validate_generation_id(generation_id)
        with self._coordination.lease_lock:
            _fault_point("lease_before_marker")
            generation_dir = self._generation_dir(identity.digest, generation_id)
            if not generation_dir.is_dir():
                self._forget_verified(identity.digest, generation_id)
                raise SourceCacheGenerationMissingError("source-cache generation does not exist")
            if require_current and self._current_generation_id(identity.digest) != generation_id:
                return False
            key = (identity.digest, generation_id)
            with self._lock:
                previous = self._leases.get(key, 0)
                self._leases[key] = previous + 1
            if previous == 0:
                try:
                    (generation_dir / f"{_LEASE_PREFIX}{self._own_token()}").touch()
                except BaseException:
                    with self._lock:
                        self._leases[key] -= 1
                        if self._leases[key] == 0:
                            del self._leases[key]
                    raise
            return True

    def _release_node_lease(self, identity: SourceCacheIdentity, generation_id: str) -> None:
        retired: list[Path] = []
        try:
            with self._coordination.lease_lock:
                key = (identity.digest, generation_id)
                with self._lock:
                    remaining = self._leases[key] - 1
                    if remaining:
                        self._leases[key] = remaining
                    else:
                        del self._leases[key]
                if remaining:
                    return
                generation_dir = self._generation_dir(identity.digest, generation_id)
                token = self._coordination.token
                if token is not None:
                    (generation_dir / f"{_LEASE_PREFIX}{token}").unlink(missing_ok=True)
                if (
                    generation_dir.is_dir()
                    and self._current_generation_id(identity.digest) != generation_id
                    and not self._has_live_holders_locked(generation_dir)
                ):
                    retired_path = self._retire_generation_locked(identity.digest, generation_id)
                    if retired_path is not None:
                        retired.append(retired_path)
        finally:
            self._delete_retired(retired)

    @contextlib.contextmanager
    def lease(self, identity: SourceCacheIdentity) -> Iterator[SourceCacheGeneration]:
        if identity.provider != NODE_OUTPUT_PROVIDER:
            with super().lease(identity) as generation:
                yield generation
            return
        while True:
            generation_id = self._current_generation_id(identity.digest)
            if generation_id is None:
                raise FileNotFoundError(self._pointer_path(identity))
            try:
                if self._acquire_node_lease(identity, generation_id, require_current=True):
                    break
            except SourceCacheGenerationMissingError:
                # Superseded and retired between reading the pointer and
                # leasing: select again, unless the pointer itself is dangling.
                if self._current_generation_id(identity.digest) == generation_id:
                    raise
        try:
            generation = self._metadata_from_path(identity, generation_id)
            self._touch_last_used(generation)
            yield generation
        finally:
            self._release_node_lease(identity, generation_id)

    @contextlib.contextmanager
    def lease_generation(
        self, identity: SourceCacheIdentity, generation_id: str
    ) -> Iterator[SourceCacheGeneration]:
        if identity.provider != NODE_OUTPUT_PROVIDER:
            with super().lease_generation(identity, generation_id) as generation:
                yield generation
            return
        self._acquire_node_lease(identity, generation_id, require_current=False)
        try:
            generation = self._metadata_from_path(identity, generation_id)
            self._touch_last_used(generation)
            yield generation
        finally:
            self._release_node_lease(identity, generation_id)

    # --------------------------------------------------------- retirement

    def _retire_generation_locked(self, identity_digest: str, generation_id: str) -> Path | None:
        """Rename one generation out of selection. Caller holds the lease lock."""
        generation_dir = self._generation_dir(identity_digest, generation_id)
        retired = generation_dir.parent.parent / f"{_RETIRED_PREFIX}{uuid.uuid4().hex}"
        try:
            generation_dir.rename(retired)
        except FileNotFoundError:
            return None
        except PermissionError as exc:
            # A Windows handle still open inside the generation blocks the
            # rename; the generation stays selectable-by-id and is retried later.
            logger.warning(
                "node_snapshot_retirement_deferred",
                identity_digest=identity_digest,
                generation_id=generation_id,
                error=str(exc),
            )
            return None
        self._forget_verified(identity_digest, generation_id)
        return retired

    @staticmethod
    def _delete_retired(paths: list[Path]) -> None:
        for path in paths:
            shutil.rmtree(path, ignore_errors=True)
            if path.exists():
                logger.warning("node_snapshot_retired_delete_failed", path=str(path))

    def retire_unleased(self, identity: SourceCacheIdentity) -> None:
        if identity.provider != NODE_OUTPUT_PROVIDER:
            super().retire_unleased(identity)
            return
        retired: list[Path] = []
        try:
            with self._coordination.lease_lock:
                retired.extend(self._retire_non_current_locked(identity.digest))
        finally:
            self._delete_retired(retired)

    def _retire_non_current_locked(self, identity_digest: str) -> list[Path]:
        generations_dir = self.inputs_root / identity_digest / "generations"
        try:
            candidates = tuple(generations_dir.iterdir())
        except FileNotFoundError:
            return []
        current = self._current_generation_id(identity_digest)
        retired: list[Path] = []
        for candidate in candidates:
            if not candidate.is_dir() or candidate.name == current:
                continue
            if self._has_live_holders_locked(candidate):
                continue
            path = self._retire_generation_locked(identity_digest, candidate.name)
            if path is not None:
                retired.append(path)
        return retired

    def clear(self, identity: SourceCacheIdentity) -> None:
        if identity.provider != NODE_OUTPUT_PROVIDER:
            super().clear(identity)
            return
        retired: list[Path] = []
        try:
            with self._coordination.lease_lock:
                self._pointer_path(identity).unlink(missing_ok=True)
                self._prune_identity_locked(identity)
                retired.extend(self._retire_non_current_locked(identity.digest))
        finally:
            self._delete_retired(retired)

    def clear_slot(self, slot: NodeSnapshotSlot) -> None:
        """Remove every identity of *slot* and its pin; leased generations retire on release."""
        retired: list[Path] = []
        try:
            with self._coordination.lease_lock:
                index = self._read_slot_index(slot)
                for identity_digest in index["identities"]:
                    (self.inputs_root / identity_digest / "current.json").unlink(missing_ok=True)
                    retired.extend(self._retire_non_current_locked(identity_digest))
                self._slot_index_path(slot).unlink(missing_ok=True)
        finally:
            self._delete_retired(retired)

    def pin(self, identity: SourceCacheIdentity) -> None:
        """Pin the slot to *identity*, which must hold a current generation."""
        self._require_node_output(identity)
        slot, _signature = NodeSnapshotSlot.from_identity(identity)
        with self._coordination.lease_lock:
            if self._current_generation_id(identity.digest) is None:
                raise SourceCacheGenerationMissingError(
                    "cannot pin an identity without a generation"
                )
            index = self._read_slot_index(slot)
            index["identities"][identity.digest] = dict(identity.descriptor)
            index["pinned_identity"] = identity.digest
            self._write_slot_index_locked(slot, index)

    # --------------------------------------------------------- publication

    def stage_node_output(
        self, identity: SourceCacheIdentity, *, staging_token: str | None = None
    ) -> NodeSnapshotArtifact:
        """Allocate a request-owned staging directory to sink a node output into.

        A supervising parent passes its own ``staging_token`` so it can discard
        exactly this staging directory if the worker is killed before cleanup.
        """
        self._require_node_output(identity)
        token = (
            _validate_staging_token(staging_token)
            if staging_token is not None
            else new_staging_token()
        )
        identity_dir = self.identity_path(identity)
        identity_dir.mkdir(parents=True, exist_ok=True)
        staging = identity_dir / f".staging-{token}"
        staging.mkdir()
        return NodeSnapshotArtifact(identity, staging)

    def discard_node_output_staging(self, staging_token: str) -> None:
        """Remove a terminated worker's staging directory named by its parent's token."""
        token = _validate_staging_token(staging_token)
        for staging in self.inputs_root.glob(f"*/.staging-{token}"):
            if staging.is_dir() and not staging.is_symlink() and not remove_tree(staging):
                logger.warning("node_snapshot_staging_discard_failed", path=str(staging))

    def _staged_metadata(
        self,
        identity: SourceCacheIdentity,
        artifact: NodeSnapshotArtifact,
        *,
        columns: NodeSnapshotColumns,
        dependencies: Mapping[str, str],
        profile: ExecutionProfile | str,
    ) -> SourceCacheMetadata:
        import pyarrow.parquet as pq

        slot, signature = NodeSnapshotSlot.from_identity(identity)
        data_path = artifact.data_path
        data_sha256 = _sha256_file(data_path)
        parquet_metadata = pq.read_metadata(data_path)
        schema = pl.scan_parquet(data_path).collect_schema()
        schema_columns = {name: str(dtype) for name, dtype in schema.items()}
        if columns.names is not None and not columns.names <= set(schema_columns):
            missing = sorted(columns.names - set(schema_columns))
            raise ValueError(f"node-output artifact lacks declared columns {missing}")
        checked_dependencies = {
            str(digest): _validate_generation_id(generation_id)
            for digest, generation_id in dependencies.items()
        }
        if any(not _is_identity_digest(digest) for digest in checked_dependencies):
            raise ValueError("node-output dependencies must be keyed by identity digest")
        if identity.digest in checked_dependencies:
            raise ValueError("a node-output generation cannot depend on its own identity")
        return SourceCacheMetadata(
            identity_digest=identity.digest,
            identity=identity.payload,
            schema_version=identity.schema_version,
            generation_id=str(uuid.uuid4()),
            source_signature=None,
            data_sha256=data_sha256,
            size_bytes=data_path.stat().st_size,
            row_count=parquet_metadata.num_rows,
            column_count=len(schema_columns),
            columns=schema_columns,
            created_at=time.time(),
            profile=ExecutionProfile(profile).value,
            build_class="bounded",
            node_output={
                "metadata_version": NODE_SNAPSHOT_METADATA_VERSION,
                "slot": slot.descriptor,
                "slot_digest": slot.digest,
                "signature": signature,
                "column_set": columns.to_json(),
                "dependencies": dict(sorted(checked_dependencies.items())),
            },
        )

    def _should_publish_locked(
        self,
        identity: SourceCacheIdentity,
        *,
        columns: NodeSnapshotColumns,
        dependencies: Mapping[str, str],
        explicit: bool,
        refresh: bool,
    ) -> bool:
        """Apply the publication rule. Caller holds the identity's publication lock."""
        if not self._dependencies_current_or_cleared(dependencies):
            return False
        try:
            latest = self.latest_generation(identity)
        except SourceCacheCorruptError:
            # Only an explicit build replaces a corrupt generation; an automatic
            # capture surfaces the corruption instead of silently repairing it.
            if explicit:
                return True
            raise
        if latest is None:
            return True
        if not columns.covers(latest.columns):
            return False
        return not latest.fresh or columns.strictly_contains(latest.columns) or refresh

    def publish_node_output(
        self,
        identity: SourceCacheIdentity,
        artifact: NodeSnapshotArtifact,
        *,
        columns: NodeSnapshotColumns,
        dependencies: Mapping[str, str],
        explicit: bool,
        profile: ExecutionProfile | str,
        refresh: bool = False,
    ) -> NodeSnapshotPublication:
        """Publish *artifact* under the publication rule and lease what was published.

        ``explicit`` marks an explicit cache build: it pins the slot and may
        replace a corrupt generation; an automatic capture does neither.
        The writer always continues from its own data: on ``superseded`` the
        returned handle owns the artifact; a quota rejection after automatic
        eviction raises :class:`NodeSnapshotQuotaRejectedError` carrying it.
        """
        if refresh and not explicit:
            raise ValueError("only an explicit build can refresh a node-output snapshot")
        self._require_node_output(identity)
        if artifact.identity.digest != identity.digest or artifact.directory.parent != (
            self.identity_path(identity)
        ):
            raise ValueError("node-output artifact was not staged for this identity")
        metadata = self._staged_metadata(
            identity, artifact, columns=columns, dependencies=dependencies, profile=profile
        )
        atomic_write_text(artifact.directory / "meta.json", canonical_json(metadata.to_dict()))
        generation_id = metadata.generation_id

        with self._publication_lock(identity):
            _fault_point("publish_before_recheck")
            if not self._should_publish_locked(
                identity,
                columns=columns,
                dependencies=dependencies,
                explicit=explicit,
                refresh=refresh,
            ):
                logger.info(
                    "node_snapshot_capture_superseded",
                    identity_digest=identity.digest,
                )
                return NodeSnapshotPublication(
                    "superseded", store=self, identity=identity, artifact=artifact
                )
            retired: list[Path] = []
            try:
                with self._coordination.lease_lock:
                    _fault_point("publish_before_admission")
                    superseded_id = self._current_generation_id(identity.digest)
                    retired.extend(
                        self._admit_node_output_locked(
                            identity,
                            artifact,
                            new_size_bytes=metadata.size_bytes,
                            superseded_id=superseded_id,
                        )
                    )
                    final_dir = self._generation_dir(identity.digest, generation_id)
                    final_dir.parent.mkdir(parents=True, exist_ok=True)
                    artifact.directory.replace(final_dir)
                    artifact._mark_published()
                    key = (identity.digest, generation_id)
                    try:
                        published_stat = (final_dir / "data.parquet").stat()
                        with self._lock:
                            self._verified_generations.add(
                                (
                                    identity.digest,
                                    generation_id,
                                    published_stat.st_mtime_ns,
                                    published_stat.st_size,
                                    metadata.data_sha256,
                                )
                            )
                        # The publisher's lease exists before the pointer names
                        # the generation, so it is never selectable unleased.
                        (final_dir / f"{_LEASE_PREFIX}{self._own_token()}").touch()
                        with self._lock:
                            self._leases[key] = self._leases.get(key, 0) + 1
                        self._write_pointer_locked(identity, generation_id)
                    except BaseException:
                        with self._lock:
                            if self._leases.get(key):
                                del self._leases[key]
                        self._forget_verified(identity.digest, generation_id)
                        shutil.rmtree(final_dir, ignore_errors=True)
                        raise
                    try:
                        slot, _signature = NodeSnapshotSlot.from_identity(identity)
                        index = self._read_slot_index(slot)
                        index["identities"][identity.digest] = dict(identity.descriptor)
                        if explicit or index["pinned_identity"] is not None:
                            # A pin belongs to the slot and passes to its newest publication.
                            index["pinned_identity"] = identity.digest
                        self._write_slot_index_locked(slot, index)
                        if superseded_id is not None and superseded_id != generation_id:
                            superseded_dir = self._generation_dir(identity.digest, superseded_id)
                            if superseded_dir.is_dir() and not self._has_live_holders_locked(
                                superseded_dir
                            ):
                                path = self._retire_generation_locked(
                                    identity.digest, superseded_id
                                )
                                if path is not None:
                                    retired.append(path)
                    except BaseException:
                        # Ownership has not reached the caller: release the
                        # publisher's lease before surfacing the failure.
                        self._release_node_lease(identity, generation_id)
                        raise
            finally:
                self._delete_retired(retired)

        try:
            generation = self._metadata_from_path(identity, generation_id)
            described = self.describe_generation(identity, generation)
        except BaseException:
            self._release_node_lease(identity, generation_id)
            raise
        logger.info(
            "node_snapshot_published",
            identity_digest=identity.digest,
            generation_id=generation_id,
            size_bytes=metadata.size_bytes,
            retention=described.retention,
        )
        return NodeSnapshotPublication(
            "published", store=self, identity=identity, generation=described
        )

    def _admit_node_output_locked(
        self,
        identity: SourceCacheIdentity,
        artifact: NodeSnapshotArtifact,
        *,
        new_size_bytes: int,
        superseded_id: str | None,
    ) -> list[Path]:
        """Admit a publication, retiring unleased automatic generations LRU-first.

        Caller holds the lease lock. Returns the retired paths to delete after
        the lock is released; raises :class:`NodeSnapshotQuotaRejectedError`
        without evicting anything when even full eviction cannot make room.
        """
        size = self._generation_bytes() + self._staging_bytes(exclude=artifact.directory)
        count = self._generation_count()
        if superseded_id is not None:
            superseded_dir = self._generation_dir(identity.digest, superseded_id)
            if superseded_dir.is_dir() and not self._has_live_holders_locked(superseded_dir):
                try:
                    size -= (superseded_dir / "data.parquet").stat().st_size
                    count -= 1
                except FileNotFoundError:
                    pass
        projected_size = size + new_size_bytes
        projected_count = count + 1
        if projected_size <= self.max_bytes and projected_count <= self.max_generations:
            return []

        candidates = self._eviction_candidates_locked(exclude=(identity.digest, superseded_id))
        reclaimable_size = sum(candidate[2] for candidate in candidates)
        if (
            projected_size - reclaimable_size > self.max_bytes
            or projected_count - len(candidates) > self.max_generations
        ):
            raise NodeSnapshotQuotaRejectedError(
                "source-cache quota exceeded: pinned and in-use snapshots are kept until "
                "explicitly refreshed or cleared. Clear an unused snapshot or raise the "
                "cache quota.",
                artifact,
            )
        retired: list[Path] = []
        for identity_digest, generation_id, generation_size, _order in candidates:
            if projected_size <= self.max_bytes and projected_count <= self.max_generations:
                break
            _fault_point("evict_before_marker_check")
            generation_dir = self._generation_dir(identity_digest, generation_id)
            if self._has_live_holders_locked(generation_dir):
                continue
            was_current = self._current_generation_id(identity_digest) == generation_id
            path = self._retire_generation_locked(identity_digest, generation_id)
            if path is None:
                continue
            if was_current:
                (self.inputs_root / identity_digest / "current.json").unlink(missing_ok=True)
                evicted_identity = self._identity_from_generation_path(path)
                if evicted_identity is not None:
                    self._prune_identity_locked(evicted_identity)
            retired.append(path)
            projected_size -= generation_size
            projected_count -= 1
            logger.info(
                "node_snapshot_evicted",
                identity_digest=identity_digest,
                generation_id=generation_id,
                size_bytes=generation_size,
                was_current=was_current,
            )
        if projected_size > self.max_bytes or projected_count > self.max_generations:
            # A candidate gained a live holder between selection and retirement.
            self._delete_retired(retired)
            raise NodeSnapshotQuotaRejectedError(
                "source-cache quota exceeded: pinned and in-use snapshots are kept until "
                "explicitly refreshed or cleared. Clear an unused snapshot or raise the "
                "cache quota.",
                artifact,
            )
        return retired

    @staticmethod
    def _identity_from_generation_path(generation_dir: Path) -> SourceCacheIdentity | None:
        try:
            raw = json.loads((generation_dir / "meta.json").read_text(encoding="utf-8"))
            payload = raw["identity"]
            return SourceCacheIdentity(
                provider=payload["provider"],
                descriptor=payload["descriptor"],
                schema_version=payload["schema_version"],
            )
        except (OSError, ValueError, KeyError, TypeError):
            return None

    def _eviction_candidates_locked(
        self, *, exclude: tuple[str, str | None]
    ) -> list[tuple[str, str, int, tuple[int, float]]]:
        """Unleased automatic node-output generations, superseded first, then LRU."""
        candidates: list[tuple[str, str, int, tuple[int, float]]] = []
        pinned_by_slot: dict[str, str | None] = {}
        for identity_dir in self.inputs_root.iterdir():
            if not _is_identity_digest(identity_dir.name) or not identity_dir.is_dir():
                continue
            generations_dir = identity_dir / "generations"
            try:
                generation_dirs = tuple(generations_dir.iterdir())
            except FileNotFoundError:
                continue
            current = self._current_generation_id(identity_dir.name)
            for generation_dir in generation_dirs:
                if (identity_dir.name, generation_dir.name) == exclude:
                    continue
                try:
                    raw = json.loads((generation_dir / "meta.json").read_text(encoding="utf-8"))
                    if raw["identity"]["provider"] != NODE_OUTPUT_PROVIDER:
                        break
                    size = (generation_dir / "data.parquet").stat().st_size
                    created_at = float(raw["created_at"])
                    slot_digest = raw["node_output"]["slot_digest"]
                except (OSError, ValueError, KeyError, TypeError):
                    continue
                is_current = generation_dir.name == current
                if is_current:
                    if slot_digest not in pinned_by_slot:
                        pinned_by_slot[slot_digest] = self._pinned_identity_by_slot_digest(
                            slot_digest
                        )
                    if pinned_by_slot[slot_digest] == identity_dir.name:
                        continue
                if self._in_process_lease_count(identity_dir.name, generation_dir.name):
                    continue
                if self._has_live_holders_locked(generation_dir):
                    continue
                order = (1 if is_current else 0, self._last_used(generation_dir, created_at))
                candidates.append((identity_dir.name, generation_dir.name, size, order))
        candidates.sort(key=lambda candidate: candidate[3])
        return candidates

    def _pinned_identity_by_slot_digest(self, slot_digest: str) -> str | None:
        try:
            raw = json.loads((self._slots_dir / f"{slot_digest}.json").read_text(encoding="utf-8"))
        except FileNotFoundError:
            return None
        except ValueError as exc:
            raise SourceCacheCorruptError("node snapshot slot index is corrupt") from exc
        pinned = raw.get("pinned_identity") if isinstance(raw, dict) else None
        return pinned if isinstance(pinned, str) else None
