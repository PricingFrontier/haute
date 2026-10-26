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
| IO-R04 | Planned | P2 | A generation whose directory is gone is reported missing, not corrupt, and a generation is retired by rename-and-defer instead of a bare `rmtree` under the lock. |
| IO-R05 | Planned | P2 | The registry and the engine anchor a relative config path the same way, and the specification states one rule. |
| IO-R06 | Planned | P3 | A snapshot build validates its generation outside the store-wide lease lock, and the cache-usage endpoint stops walking every generation per preview. |
| IO-R07 | Planned | P3 | A clear keeps a deferred generation indexed, the POSIX revision applies the settle rule on coarse clocks, and `catalog` is excluded from config by decision. |
| IO-R08 | Planned | P3 | The supervised-build mechanism exists once, and the IO layer's write-only fields are gone. |

## Planned improvements

### IO-R04 — Missing is not corrupt, and retirement defers
**Why:** `_metadata_from_path` raises `SourceCacheGenerationMissingError` when a
generation's directory has vanished (evicted by another process, cleared by hand), and
`slot_status` maps every `SourceCacheCorruptError` subclass to `corrupt`, so the editor
tells the analyst to clear a corrupt cache that is merely gone. Generation retirement
calls `shutil.rmtree(candidate)` under the lease lock; on Windows a reader holding a part
open makes the removal fail midway and leaves a half-deleted generation that later reads
as corrupt, while node outputs rename the generation aside and defer the delete.

**Plan:** Map the missing subclass to `missing` in `slot_status` (and the inventory);
retire a source-cache generation by renaming it to a tombstone under the lock and
deleting it outside the lock, retrying on the next sweep as node outputs do.

**Acceptance:** Deleting a generation directory by hand shows `missing` in the inventory
and a rebuild, not a corruption message; a retirement with one part held open leaves no
half-deleted directory in the store and completes on the next sweep.

**Dependencies:** None.

**Evidence:** `src/haute/_source_cache.py::_metadata_from_path`;
`src/haute/_node_snapshots.py::slot_status`;
`src/haute/_source_cache.py::SourceCacheStore` (the retirement under `_lease_lock`).

### IO-R05 — One anchoring rule for config paths
**Why:** `anchor_config_source_path` resolves a relative path with `prefer="pipeline"`
while the canonical graph build uses `prefer="project"`, so a relative path that exists
under both the pipeline's directory and the project root names two different files
between validation (the registry) and execution (the engine); the IO-layer low-level
specification states both rules.

**Plan:** Use one preference (the project root, as the canonical graph does) in both
places through one helper, and make the specification say so once.

**Acceptance:** A relative path present in both locations resolves to the same file in
the registry's validation and in the walker, with a test that places a decoy file in
the other location.

**Dependencies:** None.

**Evidence:** `src/haute/_polars_io_registry.py::anchor_config_source_path`;
`src/haute/execution.py` (the canonical graph's `prefer="project"` call).

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

### IO-R07 — Clear, settle and exclusion rules
**Why:** `clear_slot` and `clear` drop a slot's index even when a generation's retirement
was deferred by a Windows `PermissionError`, leaving an unpointed, unindexed generation
that only the budget pass finds, while publication keeps such an identity indexed on
purpose; the specification's "Clear and pin" and "Publication" sections disagree. On
POSIX the native revision is `st_ctime_ns`, whose granularity is the filesystem's, and the
two-second settle rule never applies to a regular file, so a same-size rewrite within one
tick on a coarse-clock filesystem keeps the cached proof. `catalog` is config-expressible
for `scan_iceberg` and `sink_iceberg` although it is a location-bearing object argument
that JSON cannot construct; it is unreachable while `pyiceberg` is absent.

**Plan:** Keep an identity indexed while generations remain in both clears; apply the
settle rule to a POSIX revision whose timestamps are whole seconds; add `catalog` to the
object-valued or remote-IO argument exclusions.

**Acceptance:** A deferred clear leaves the generation indexed and the next sweep retires
it; a coarse-clock rewrite within a tick is re-proved after the settle; `catalog` is
absent from `registry_capabilities()`.

**Dependencies:** None.

**Evidence:** `src/haute/_node_snapshots.py::NodeSnapshotStore.clear_slot`;
`src/haute/_node_snapshots.py::NodeSnapshotStore.clear`;
`src/haute/_json_shred/_source_proof.py::observe_freshness`;
`src/haute/_polars_io_registry.py::registry_capabilities`.

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
