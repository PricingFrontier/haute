# IO layer roadmap

## Scope

File writes and file locks used by the IO layer and the stores built on it.
Current behaviour is specified in
[the IO-layer specification](../io-layer/high-level.md). This package comes
from the [23 September 2026 codebase review](codebase-review-2026-09-23.md).

## Priorities

| Package | State | Priority | Outcome |
|---|---|---:|---|
| IO-R02 | Planned | P3 | One atomic-write primitive and one file-lock primitive. |

## Planned improvements

### IO-R02 — One atomic write and one file lock
**Why:** `_polars_utils.atomic_write` stages to a fixed
`dest.with_suffix(".parquet.tmp")`, so two writers to one destination
collide, a CSV gets a `.parquet.tmp` stage, and nothing is flushed with
`fsync`. `_file_ops.atomic_write_bytes` already uses unique staging names,
`fsync` and a Windows contention retry. File locks are wrapped three times,
each with its own timeout and stale-lock loop: the project mutation lock, the
source-cache store lock and the JSON-cache build lock.

**Plan:** Build `atomic_write` on the `_file_ops` primitive and delete the
fixed-suffix staging. Provide one file-lock helper with timeout and stale-owner
handling and use it for the three locks.

**Acceptance:** Two concurrent writes to one destination leave one complete
file and no stray staging file under test; one lock helper remains; the
store, JSON-cache and project-lock tests pass.

**Dependencies:** `CACHE-S08` (caching) removes the JSON-cache lock if taken
first.

**Evidence:** `src/haute/_polars_utils.py::atomic_write`;
`src/haute/_file_ops.py::atomic_write_bytes`;
`src/haute/_project_mutation_lock.py::ProjectMutationLock`;
`src/haute/_source_cache.py::_StoreFileLock`;
`src/haute/_json_shred/_publication.py::_CacheBuildLock`;
`src/haute/_file_lock.py`.
