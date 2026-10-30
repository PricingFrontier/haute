# IO layer roadmap

## Scope

Data Input providers and their freshness proofs, the input snapshot store and
its generations, the config path anchoring the registry and the engine share,
and the input preparation builds. Current behaviour is specified in
[the IO-layer specification](../io-layer/high-level.md) and
[caching](../caching/high-level.md). These packages come from the
[25 October 2026 codebase review](codebase-review-2026-10-25.md); the numbering
continues from the September 2026 review's packages, which are retired.

## Priorities

| Package | State | Priority | Outcome |
|---|---|---:|---|
| IO-R06 | Planned | P3 | A snapshot build validates its generation outside the store-wide lease lock, and the cache-usage endpoint stops walking every generation per preview. |

## Planned improvements

### IO-R06 — Lock hold and usage cost
**Why:** A snapshot build validates its new generation (every part's footer, Arrow schema
and Polars schema) while holding the store-wide cross-process lease lock, blocking every
lease and release in every process for the duration, against the specification's
instruction not to hold the global lease lock while building or validating; node-output
publication validates after releasing it. `GET /api/cache/usage`, called by the status bar
after every preview, constructs a store and walks every generation (part stats,
`meta.json`, pointer and slot-index reads), linear in the store's size.

**Plan:** Validate the staged directory before taking the lock and keep only the
supersession check, the rename and the pointer write under it; memoise the usage for a
short interval per process or keep running totals updated by publish and retire.

**Acceptance:** The lock is held for no part read during a build (a test with a slow
part reader); a repeated usage call within the interval reads no generation.

**Dependencies:** `CACHE-S17` owns the staging glob per store construction.

**Evidence:** `src/haute/_source_cache.py::SourceCacheStore` (the validation inside
`with self._lease_lock`); `src/haute/_node_snapshots.py` (publication after the lock);
`src/haute/routes/cache.py::cache_usage`; `src/haute/_node_snapshots.py::NodeSnapshotStore.usage`.
