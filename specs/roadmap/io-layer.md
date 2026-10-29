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
| IO-R08 | Planned | P3 | The supervised-build mechanism exists once, and the IO layer's write-only fields are gone. |

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

### IO-R08 — One supervised build, no write-only fields
**Why:** The supervised-build mechanism (parent-chosen generation and staging token,
spawn, reconcile, open, retire) is written out three times across `_input_preparation.py`,
`routes/input_cache.py` and `_json_shred/_snapshots.py`, with four identical `stop_reason`
closures and capped worker-config blocks, three `InputPreparationRecord` literals beside
the existing `_reused_record` helper, a thirteen-keyword pass-through and the whole Data
Input flow repeated for API Inputs; jscpd reports the pairs. `PointResolution.build_key`
is written four times and never read, and `_SnapshotLeasePlan._finalizer` is a write-only
attribute (`weakref.finalize` keeps itself alive); the caching specification still names
the build key.

**Plan:** One `supervise_build(spawn, request, budget, *, reconcile)` in
`_input_preparation` used by all three sites, one capped worker-config helper, a
`_built_record` beside `_reused_record` and a local `fail_build` in the route; remove the
two write-only fields and the specification's "build key".

**Acceptance:** jscpd reports no clone between the three build sites; the fields are gone
and the input-preparation tests pass unchanged.

**Dependencies:** None.

**Evidence:** `src/haute/_input_preparation.py`; `src/haute/routes/input_cache.py`;
`src/haute/_json_shred/_snapshots.py`; `src/haute/_data_points.py::PointResolution`;
`src/haute/_input_providers.py::_SnapshotLeasePlan`.
