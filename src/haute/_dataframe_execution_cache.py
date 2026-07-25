"""Reusable dataframe cache for materialized backend execution frames."""

from __future__ import annotations

import os
import threading
import uuid
import weakref
from collections.abc import Iterable, Iterator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
from typing import Any
from weakref import WeakValueDictionary

import polars as pl

from haute._cache import (
    CACHE_CONSUMER_CONTRACTS,
    CacheConsumer,
    GraphFingerprintMemo,
    canonical_json,
    checked_cache_inputs,
    graph_fingerprint,
)
from haute._execution_context import ExecutionProfile
from haute._graph_utils import upstream_node_ids
from haute._hashing import content_hash_bytes
from haute._logging import get_logger
from haute._lru_cache import LRUCache
from haute._polars_utils import bounded_sink, read_parquet_metadata
from haute._types import PipelineGraph

logger = get_logger(component="dataframe_execution_cache")

# Version of the dataframe-execution cache-key payload SCHEMA.  It does
# NOT need a bump when the fingerprint algorithm changes: the payload
# embeds ``lineage_fingerprint`` (a ``graph_fingerprint`` output carrying
# the ``"v<ALGO_VERSION>:"`` prefix), so every ALGO_VERSION bump — W1's
# edge-handle serialization (3→4) and W2.13's canonical-encoder
# unification (4→5) — rolls every cache key automatically.  Bump this
# only when the payload's own field set / semantics change.
DATAFRAME_EXECUTION_CACHE_VERSION = CACHE_CONSUMER_CONTRACTS[
    CacheConsumer.DATAFRAME_EXECUTION
].version
DEFAULT_DATAFRAME_EXECUTION_CACHE_MAX_BYTES: int | None = None
DEFAULT_DATAFRAME_EXECUTION_CACHE_MAX_ENTRIES = 16


def _optional_positive_int_from_env(name: str) -> int | None:
    raw = os.environ.get(name)
    if raw is None or raw == "":
        return None
    try:
        value = int(raw)
    except ValueError as exc:
        raise RuntimeError(f"{name} must be a positive integer when set") from exc
    if value < 1:
        raise RuntimeError(f"{name} must be a positive integer when set")
    return value


DATAFRAME_EXECUTION_CACHE_MAX_BYTES = _optional_positive_int_from_env(
    "HAUTE_DATAFRAME_EXECUTION_CACHE_MAX_BYTES",
)


def _release_pinned_scan(
    cache_ref: weakref.ReferenceType[DataFrameExecutionCache],
    cache_key: str,
    path: Path,
) -> None:
    cache = cache_ref()
    if cache is None:
        path.unlink(missing_ok=True)
        return
    cache._release_scan(cache_key, path)


class DataFrameExecutionCacheError(RuntimeError):
    """Base class for execution dataframe cache failures."""


class CacheArtifactMissingError(FileNotFoundError, DataFrameExecutionCacheError):
    """Raised when a cache entry points at a missing parquet artifact."""


class CacheArtifactCorruptError(DataFrameExecutionCacheError):
    """Raised when a cache entry points at an unreadable parquet artifact."""


class CacheArtifactTooLargeError(DataFrameExecutionCacheError):
    """Raised when a materialized frame exceeds the configured cache budget."""


@dataclass(frozen=True, slots=True)
class DataFrameExecutionCacheKey:
    """Exact identity for one reusable materialized execution frame."""

    cache_key: str
    namespace: str
    node_id: str
    lineage_fingerprint: str
    source: str
    profile: str
    input_fingerprint: str
    required_columns: tuple[str, ...] = ()
    extra_keys: tuple[str, ...] = ()
    execution_policy_fingerprint: str | None = None
    version: int = DATAFRAME_EXECUTION_CACHE_VERSION


@dataclass(frozen=True, slots=True)
class DataFrameExecutionCacheEntry:
    """One owned parquet artifact in the dataframe execution cache."""

    key: DataFrameExecutionCacheKey
    path: Path
    row_count: int
    column_count: int
    columns: Mapping[str, str]
    size_bytes: int
    uncompressed_size_bytes: int


@dataclass(frozen=True, slots=True)
class DataFrameExecutionCacheRequest:
    """Opt-in dataframe cache materialization request for lazy execution."""

    cache: DataFrameExecutionCache
    keys_by_node: Mapping[str, DataFrameExecutionCacheKey]
    streaming_chunk_size: int | None = None
    fast_checkpoint: bool = True

    def __post_init__(self) -> None:
        if not isinstance(self.cache, DataFrameExecutionCache):
            raise TypeError("cache must be a DataFrameExecutionCache")
        if isinstance(self.keys_by_node, str):
            raise TypeError("keys_by_node must be a mapping from node ID to cache key")

        keys_by_node = dict(self.keys_by_node)
        if not keys_by_node:
            raise ValueError("keys_by_node must contain at least one node cache key")
        for node_id, key in keys_by_node.items():
            _normalise_non_empty(node_id, field="keys_by_node node ID")
            if not isinstance(key, DataFrameExecutionCacheKey):
                raise TypeError("keys_by_node values must be DataFrameExecutionCacheKey")
            if key.node_id != node_id:
                raise ValueError(
                    "Dataframe cache request key is registered under the wrong node ID "
                    f"(mapping={node_id!r}, key.node_id={key.node_id!r})"
                )
            if key.execution_policy_fingerprint is None:
                raise ValueError(
                    "Dataframe cache request keys must include an execution_policy "
                    f"fingerprint (node_id={node_id!r})"
                )
        if self.streaming_chunk_size is not None and self.streaming_chunk_size < 1:
            raise ValueError("streaming_chunk_size must be a positive integer")

        object.__setattr__(self, "keys_by_node", MappingProxyType(keys_by_node))


def _normalise_required_columns(required_columns: Iterable[str] | None) -> tuple[str, ...]:
    if required_columns is None:
        return ()
    if isinstance(required_columns, str):
        raise TypeError("required_columns must be an iterable of column names, not a string")
    normalised: set[str] = set()
    for column in required_columns:
        if not isinstance(column, str) or not column:
            raise ValueError("required_columns must contain non-empty strings")
        normalised.add(column)
    return tuple(sorted(normalised))


def _normalise_extra_keys(extra_keys: Iterable[str] | None) -> tuple[str, ...]:
    if extra_keys is None:
        return ()
    if isinstance(extra_keys, str):
        raise TypeError("extra_keys must be an iterable of strings, not a string")
    normalised: list[str] = []
    for key in extra_keys:
        if not isinstance(key, str) or not key:
            raise ValueError("extra_keys must contain non-empty strings")
        normalised.append(key)
    return tuple(normalised)


def _normalise_non_empty(value: str, *, field: str) -> str:
    if not isinstance(value, str) or not value:
        raise ValueError(f"{field} must be a non-empty string")
    return value


def dataframe_execution_cache_profile(profile: ExecutionProfile | str) -> str:
    """Return the semantic execution profile used for dataframe cache identity."""

    if isinstance(profile, ExecutionProfile):
        value = profile
    else:
        value = ExecutionProfile(profile)
    if value == ExecutionProfile.AUTO_RANGE:
        return ExecutionProfile.OPTIMISER_SETUP.value
    return value.value


def _profile_value(profile: ExecutionProfile | str) -> str:
    return dataframe_execution_cache_profile(profile)


def dataframe_execution_policy_fingerprint(execution_policy: Mapping[str, object]) -> str:
    """Return the stable fingerprint for non-graph lazy execution policy.

    The policy is encoded with :func:`haute._cache.canonical_json` — the
    single canonical encoder for digest material — so this fingerprint can
    never drift from the encoding embedded in the cache-key payload.
    """

    return content_hash_bytes(canonical_json(execution_policy).encode())


def _upstream_subgraph(graph: PipelineGraph, node_id: str) -> PipelineGraph:
    if node_id not in graph.node_map:
        raise ValueError(f"Cannot build dataframe cache key for unknown node {node_id!r}")
    upstream_ids = set(upstream_node_ids(node_id, graph.parents_of))
    included = upstream_ids | {node_id}
    return PipelineGraph(
        nodes=[node for node in graph.nodes if node.id in included],
        edges=[edge for edge in graph.edges if edge.source in included and edge.target in included],
        pipeline_name=graph.pipeline_name,
        pipeline_description=graph.pipeline_description,
        preamble=graph.preamble,
        preserved_blocks=list(graph.preserved_blocks),
        source_file=graph.source_file,
        submodels=graph.submodels,
        warning=graph.warning,
        sources=list(graph.sources),
        active_source=graph.active_source,
    )


def dataframe_execution_cache_key(
    graph: PipelineGraph,
    *,
    node_id: str,
    namespace: str,
    source: str,
    profile: ExecutionProfile | str,
    input_fingerprint: str,
    required_columns: Iterable[str] | None = None,
    extra_keys: Iterable[str] | None = None,
    execution_policy: Mapping[str, object] | None = None,
    memo: GraphFingerprintMemo | None = None,
) -> DataFrameExecutionCacheKey:
    """Build an exact cache key for a node output materialization.

    The graph part is scoped to the target node's upstream lineage. Downstream
    edits therefore do not churn upstream cache entries, while any upstream
    node/config/edge/preamble change produces a new key.
    """

    namespace = _normalise_non_empty(namespace, field="namespace")
    node_id = _normalise_non_empty(node_id, field="node_id")
    source = _normalise_non_empty(source, field="source")
    input_fingerprint = _normalise_non_empty(
        input_fingerprint,
        field="input_fingerprint",
    )
    profile_value = _profile_value(profile)
    required = _normalise_required_columns(required_columns)
    extra = _normalise_extra_keys(extra_keys)
    policy_fingerprint = (
        dataframe_execution_policy_fingerprint(execution_policy)
        if execution_policy is not None
        else None
    )
    lineage_graph = _upstream_subgraph(graph, node_id)
    lineage_fingerprint = graph_fingerprint(lineage_graph, memo=memo)

    # ``canonical_json`` canonicalises the embedded policy (set ordering,
    # mapping-key sorting) with the SAME rules as every other digest site,
    # so the raw policy goes straight into the payload.
    payload: dict[str, object] = {
        "namespace": namespace,
        "node_id": node_id,
        "lineage_fingerprint": lineage_fingerprint,
        "source": source,
        "profile": profile_value,
        "input_fingerprint": input_fingerprint,
        "required_columns": required,
        "extra_keys": extra,
        "execution_policy": execution_policy,
    }
    inputs = checked_cache_inputs(CacheConsumer.DATAFRAME_EXECUTION, payload)
    payload_digest = content_hash_bytes(inputs.canonical_bytes)
    cache_key = f"dfexec:v{DATAFRAME_EXECUTION_CACHE_VERSION}:{payload_digest}"
    return DataFrameExecutionCacheKey(
        cache_key=cache_key,
        namespace=namespace,
        node_id=node_id,
        lineage_fingerprint=lineage_fingerprint,
        source=source,
        profile=profile_value,
        input_fingerprint=input_fingerprint,
        required_columns=required,
        extra_keys=extra,
        execution_policy_fingerprint=policy_fingerprint,
    )


class DataFrameExecutionCache(LRUCache[str, DataFrameExecutionCacheEntry]):
    """Parquet-backed LRU cache for materialized backend dataframes.

    The cache tracks live ``scan_parquet`` LazyFrames per (key, path) and
    defers artifact unlinking until the last scan is released *and* the
    entry has either been evicted or replaced.  Callers that compose
    derived LazyFrames from a cached scan must keep the source scan
    reference alive for as long as those derived frames may be collected.
    Once the source is garbage-collected and the entry is gone, the
    artifact is deleted.

    While a key's :meth:`materialization_lock` is held, that key is in
    its store+first-consume window and is never selected as an eviction
    victim (the just-written artifact must survive its own insertion and
    the gap until the first ``scan`` pins it — the dataframe analogue of
    the preview/trace caches' "just-stored entry is always MRU" rule).
    Byte pressure during the window falls on other unpinned entries; if
    none exist the cache temporarily exceeds its budget under the
    standing pinned-overflow allowance and is trimmed when the window
    closes or live scans are released.
    """

    __slots__ = (
        "root",
        "_materialize_locks",
        "_materialize_locks_guard",
        "_scan_refcounts",
        "_store_pins",
        "__weakref__",
    )

    def __init__(
        self,
        *,
        root: str | Path,
        max_entries: int = DEFAULT_DATAFRAME_EXECUTION_CACHE_MAX_ENTRIES,
        max_bytes: int | None = DATAFRAME_EXECUTION_CACHE_MAX_BYTES,
    ) -> None:
        self.root = Path(root).resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self._materialize_locks: WeakValueDictionary[str, threading.RLock] = WeakValueDictionary()
        self._materialize_locks_guard = threading.RLock()
        self._scan_refcounts: dict[tuple[str, Path], int] = {}
        # Keys inside an open store+first-consume window, counted so
        # nested/reentrant ``materialization_lock`` holds compose.
        self._store_pins: dict[str, int] = {}
        super().__init__(
            max_size=max_entries,
            max_bytes=max_bytes,
            size_of=(lambda entry: entry.size_bytes) if max_bytes is not None else None,
        )

    def path_for_key(self, key: DataFrameExecutionCacheKey) -> Path:
        artifact_name = content_hash_bytes(key.cache_key.encode())
        return self.root / f"{artifact_name}-{uuid.uuid4().hex}.parquet"

    def get(self, key: DataFrameExecutionCacheKey) -> DataFrameExecutionCacheEntry | None:  # type: ignore[override]
        with self._lock:
            entry = super().get(key.cache_key)
            if entry is None:
                return None
            if self._evict_if_invalid(entry):
                return None
            return entry

    def scan(self, key: DataFrameExecutionCacheKey) -> pl.LazyFrame | None:
        with self._lock:
            entry = super().get(key.cache_key)
            if entry is None:
                return None
            if self._evict_if_invalid(entry):
                return None
            scan_id = (key.cache_key, entry.path)
            self._scan_refcounts[scan_id] = self._scan_refcounts.get(scan_id, 0) + 1
            self.pin(key.cache_key)
        try:
            lazy_frame = pl.scan_parquet(entry.path)
        except BaseException:
            self._release_scan(key.cache_key, entry.path)
            raise
        weakref.finalize(
            lazy_frame,
            _release_pinned_scan,
            weakref.ref(self),
            key.cache_key,
            entry.path,
        )
        return lazy_frame

    def _evict_if_invalid(self, entry: DataFrameExecutionCacheEntry) -> bool:
        """Return True after evicting *entry* if its artifact is gone or
        unreadable.  Caller must hold ``self._lock``.

        A broken entry must not stay in the cache: subsequent lookups
        would keep failing for the same reason, and callers cannot
        differentiate a transient I/O failure from a permanently dead
        artifact.  Treating these as misses lets the next execution
        repopulate the cache normally.
        """
        try:
            self._validate_entry(entry)
            return False
        except (CacheArtifactMissingError, CacheArtifactCorruptError) as exc:
            logger.warning(
                "dataframe_execution_cache_invalid_entry_evicted",
                cache_key=entry.key.cache_key,
                node_id=entry.key.node_id,
                path=str(entry.path),
                error=str(exc),
            )
            self._remove_key(entry.key.cache_key)
            return True

    @contextmanager
    def materialization_lock(self, key: DataFrameExecutionCacheKey) -> Iterator[None]:
        """Serialise same-key artifact writes while allowing different keys.

        Holding the lock also opens the key's store+first-consume window:
        until it is released, the key's entry is exempt from being chosen
        as an eviction victim, so a fresh artifact can never be evicted by
        its own store (or by a concurrent store) before its first ``scan``
        pins it.  Eviction of OTHER entries is unaffected.  When the
        window closes, any byte-budget debt deferred by the exemption is
        settled immediately.
        """

        with self._materialize_locks_guard:
            lock = self._materialize_locks.setdefault(key.cache_key, threading.RLock())
        lock.acquire()
        with self._lock:
            self._store_pins[key.cache_key] = self._store_pins.get(key.cache_key, 0) + 1
        try:
            yield
        finally:
            # The settle can raise (eviction unlinks artifacts, and e.g.
            # a Windows sharing violation surfaces as PermissionError);
            # the per-key lock must be released regardless or any thread
            # blocked on it would hang forever.
            try:
                with self._lock:
                    remaining = self._store_pins[key.cache_key] - 1
                    if remaining:
                        self._store_pins[key.cache_key] = remaining
                    else:
                        del self._store_pins[key.cache_key]
                        # Window closed: the key is now scan-pinned by
                        # its first consumer or an ordinary LRU citizen,
                        # so any deferred over-budget state is trimmed
                        # right away.
                        self._evict_if_over_capacity()
            finally:
                lock.release()

    def store_artifact(
        self,
        key: DataFrameExecutionCacheKey,
        path: Path,
        metadata: Mapping[str, Any],
    ) -> DataFrameExecutionCacheEntry:
        entry = DataFrameExecutionCacheEntry(
            key=key,
            path=path.resolve(),
            row_count=int(metadata["row_count"]),
            column_count=int(metadata["column_count"]),
            columns=dict(metadata["columns"]),
            size_bytes=int(metadata["size_bytes"]),
            uncompressed_size_bytes=int(metadata["uncompressed_size_bytes"]),
        )
        if self._max_bytes is not None and entry.size_bytes > self._max_bytes:
            logger.warning(
                "dataframe_execution_cache_artifact_oversized",
                cache_key=key.cache_key,
                node_id=key.node_id,
                size_bytes=entry.size_bytes,
                max_bytes=self._max_bytes,
            )
            self.evict_where(lambda stored_key: stored_key == key.cache_key)
            entry.path.unlink(missing_ok=True)
            raise CacheArtifactTooLargeError(
                "Materialized dataframe cache artifact exceeds the configured byte budget "
                f"(node_id={key.node_id!r}, cache_key={key.cache_key!r}, "
                f"size_bytes={entry.size_bytes}, max_bytes={self._max_bytes})"
            )
        self.evict_where(lambda stored_key: stored_key == key.cache_key)
        self.put(key.cache_key, entry)
        stored = self.get(key)
        if stored is None:
            raise DataFrameExecutionCacheError(
                "Stored dataframe cache entry vanished immediately "
                f"(node_id={key.node_id!r}, cache_key={key.cache_key!r})"
            )
        return stored

    def clear(self) -> None:
        with self._materialize_locks_guard:
            locks = list(self._materialize_locks.values())
            for lock in locks:
                lock.acquire()
            try:
                with self._lock:
                    for key in list(self._data.keys()):
                        self._remove_key(key)
            finally:
                for lock in reversed(locks):
                    lock.release()

    invalidate = clear

    def _is_pinned(self, key: str) -> bool:
        """A key inside its store+first-consume window is never an
        eviction victim, on top of the base scan-pin rules."""
        return self._store_pins.get(key, 0) > 0 or super()._is_pinned(key)

    def _capacity_entry_count(self) -> int:
        """Store-window pins exempt a key from victim selection only.

        The entry still counts against ``max_size`` (unlike scan pins),
        so entry-count eviction of other entries fires at exactly the
        same time as it did before the window existed.
        """
        base_is_pinned = super()._is_pinned
        return sum(1 for key in self._data if not base_is_pinned(key))

    def _remove_key(self, key: str) -> DataFrameExecutionCacheEntry:
        # Unlink iff no live scans hold the artifact open.  Base pins
        # track live scans; a store-window pin is not a reader, so an
        # in-window entry removed for cause (missing/corrupt artifact,
        # explicit clear) must still have its file deleted.
        # ``_store_pins`` is deliberately NOT touched here: the window
        # belongs to the materialization-lock holder, and a same-key
        # replacement inside the window must keep protecting the
        # replacement entry.
        had_live_scans = super()._is_pinned(key)
        entry = super()._remove_key(key)
        if not had_live_scans:
            entry.path.unlink(missing_ok=True)
        return entry

    def _validate_entry(self, entry: DataFrameExecutionCacheEntry) -> None:
        if not entry.path.exists():
            raise CacheArtifactMissingError(
                "Cached dataframe artifact is missing "
                f"(node_id={entry.key.node_id!r}, cache_key={entry.key.cache_key!r}, "
                f"path={str(entry.path)!r})"
            )
        try:
            pl.scan_parquet(entry.path).collect_schema()
        except Exception as exc:
            raise CacheArtifactCorruptError(
                "Cached dataframe artifact is corrupt "
                f"(node_id={entry.key.node_id!r}, cache_key={entry.key.cache_key!r}, "
                f"path={str(entry.path)!r})"
            ) from exc

    def _release_scan(self, cache_key: str, path: Path) -> None:
        unlink_after_release = False
        with self._lock:
            scan_id = (cache_key, path)
            refs = self._scan_refcounts.get(scan_id, 0)
            if refs > 1:
                self._scan_refcounts[scan_id] = refs - 1
                return
            self._scan_refcounts.pop(scan_id, None)
            entry_before = self._data.get(cache_key)
            if not any(stored_key == cache_key for stored_key, _ in self._scan_refcounts):
                self._pinned.discard(cache_key)
                self._evict_if_over_capacity()
            entry_after = self._data.get(cache_key)
            # Single-unlink rule: ``_remove_key`` unlinks unpinned entries
            # itself, so only unlink here when the path was orphaned by an
            # earlier replacement and no longer belongs to any stored entry.
            if entry_before is None or entry_before.path != path:
                if entry_after is None or entry_after.path != path:
                    unlink_after_release = True
        if unlink_after_release:
            path.unlink(missing_ok=True)


def materialize_lazy_frame_with_cache(
    lf: pl.LazyFrame,
    *,
    cache: DataFrameExecutionCache,
    key: DataFrameExecutionCacheKey,
    profile: ExecutionProfile | str,
    streaming_chunk_size: int | None = None,
    fast_checkpoint: bool = True,
) -> pl.LazyFrame:
    """Return a cache-backed scan for *lf*, materializing it on cache miss."""

    _profile_value(profile)
    with cache.materialization_lock(key):
        cached = cache.scan(key)
        if cached is not None:
            return cached

        path = cache.path_for_key(key)
        try:
            bounded_sink(
                lf,
                path,
                fast_checkpoint=fast_checkpoint,
                streaming_chunk_size=streaming_chunk_size,
            )
            metadata = read_parquet_metadata(path)
            cache.store_artifact(key, path, metadata)
        except BaseException:
            path.unlink(missing_ok=True)
            path.with_suffix(".parquet.tmp").unlink(missing_ok=True)
            raise

        # Validate the just-written artifact through the same path as a later hit.
        cached_after_store = cache.scan(key)
        if cached_after_store is None:
            raise DataFrameExecutionCacheError(
                "Stored dataframe cache entry vanished immediately "
                f"(node_id={key.node_id!r}, cache_key={key.cache_key!r})"
            )
        return cached_after_store
