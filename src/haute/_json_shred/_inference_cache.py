"""Bounded reuse of complete schemas proven against unchanged source revisions."""

from __future__ import annotations

import os
import threading
from collections.abc import Callable
from concurrent.futures import Future
from dataclasses import dataclass
from pathlib import Path
from typing import Any, cast

import orjson

from haute._api_input_schema import ApiInputSchemaError
from haute._json_shred import _source_proof
from haute._json_shred._source_proof import _StrongFileRevision
from haute._lru_cache import LRUCache

_CacheKey = tuple[str, int]
_FlightKey = tuple[_CacheKey, _StrongFileRevision]


@dataclass(frozen=True, slots=True)
class _CachedSchema:
    revision: _StrongFileRevision
    payload: bytes


def _changed_source(path: Path) -> ApiInputSchemaError:
    return ApiInputSchemaError(
        "data file changed while its schema was inferred; retry inference",
        path=str(path),
    )


def _decode(payload: bytes) -> dict[str, Any]:
    # Only successful inference mappings are serialized into this private cache.
    return cast(dict[str, Any], orjson.loads(payload))


class InferenceCache:
    """Share successful complete scans, keeping caller edits out of the cache.

    Native source proofs are mandatory for reuse. Unsupported filesystems keep
    the ordinary inference path, without an extra full-file hash. Active scans
    share a future, including failures; futures are removed on completion.
    """

    def __init__(self, *, max_entries: int = 32, max_bytes: int = 16 * 1024 * 1024) -> None:
        self._max_entries = max_entries
        self._max_bytes = max_bytes
        self._reset_process_state()

    def _reset_process_state(self) -> None:
        self._process_id = os.getpid()
        self._lock = threading.Lock()
        self._entries: LRUCache[_CacheKey, _CachedSchema] = LRUCache(
            max_size=self._max_entries,
            max_bytes=self._max_bytes,
            size_of=lambda entry: len(entry.payload),
        )
        self._flights: dict[_FlightKey, Future[bytes]] = {}

    def _ensure_current_process(self) -> None:
        if self._process_id != os.getpid():
            # A fork can inherit locks held by threads that no longer exist.
            # Replace them without ever acquiring the inherited locks.
            self._reset_process_state()

    def get(
        self,
        data_path: Path,
        *,
        record_limit: int,
        loader: Callable[[Path], dict[str, Any]],
    ) -> dict[str, Any]:
        self._ensure_current_process()
        # Freeze the working directory, while preserving the supplied extension
        # (which selects the parser) and avoiding a different symlink target path.
        path = data_path.absolute()
        key = (os.path.normcase(str(path)), record_limit)
        revision = _source_proof._strong_file_revision(path)
        if revision is None:
            return loader(path)

        flight_key = (key, revision)
        with self._lock:
            entry = self._entries.get(key)
            if entry is not None and entry.revision == revision:
                payload = entry.payload
                flight = None
                owner = False
            else:
                payload = None
                flight = self._flights.get(flight_key)
                owner = flight is None
                if flight is None:
                    flight = Future()
                    self._flights[flight_key] = flight

        if payload is not None:
            return _decode(payload)
        assert flight is not None
        if not owner:
            payload = flight.result()
            if _source_proof._strong_file_revision(path) != revision:
                raise _changed_source(path)
            return _decode(payload)

        try:
            result = loader(path)
            if _source_proof._strong_file_revision(path) != revision:
                raise _changed_source(path)
            payload = orjson.dumps(result)
            self._entries.put(key, _CachedSchema(revision, payload))
        except BaseException as exc:
            # Release waiters even when the owner is interrupted; the exception
            # is propagated, never converted into a cached result.
            flight.set_exception(exc)
            raise
        else:
            flight.set_result(payload)
        finally:
            with self._lock:
                del self._flights[flight_key]
        return _decode(payload)

    def clear(self) -> None:
        """Drop retained results without splitting an active shared scan."""
        self._ensure_current_process()
        self._entries.clear()

    def __len__(self) -> int:
        self._ensure_current_process()
        return len(self._entries)


_INFERENCE_CACHE = InferenceCache()
