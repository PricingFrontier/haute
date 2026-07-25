# Caching — Low-Level Specification

## Module map

| File | Responsibility |
| --- | --- |
| `src/haute/_hashing.py` | Deterministic xxh64 content hashing for bytes and files (`content_hash_bytes`, `content_hash`). The primitive every other digest in this component builds on. |
| `src/haute/_cache.py` | Checked consumer/config-field contracts; `canonical_json()` — the shared canonical JSON encoder for graph/dataframe cache-key material; `graph_fingerprint()` / `preamble_execution_fingerprint()` — deterministic, versioned digests of a `PipelineGraph`'s execution-relevant inputs; `GraphFingerprintMemo` — request-scoped pin over the process-wide `StatGatedCache`-backed utility-file hash cache. |
| `src/haute/_lru_cache.py` | `LRUCache[K, V]` — thread-safe bounded LRU cache with optional TTL, pinning, and optional byte-budget eviction. The shared eviction/pinning core for the other in-process caches below. |
| `src/haute/_fingerprint_cache.py` | `FingerprintCache` — thin `LRUCache` subclass adding multi-slot dict-valued semantics (`store`, `try_get`, `update_slot`), used by the preview and trace caches (execution engine). |
| `src/haute/_dataframe_execution_cache.py` | `DataFrameExecutionCache` — parquet-artifact-backed `LRUCache` subclass for materialized backend dataframes, plus `dataframe_execution_cache_key()` and `materialize_lazy_frame_with_cache()`, the entry points backend callers use. |
| `src/haute/_stat_gated_cache.py` | `StatGatedCache[K, V]` — single-flight, `(mtime_ns, size)`-gated cache of loaded file artifacts; a generic primitive (not itself dataframe- or graph-specific) instantiated per use site — external-object/optimiser/mlflow artifact loading, and (`_cache.py`) the process-wide utility-file content-hash cache behind `_utility_file_hash`. |
| `src/haute/routes/json_cache.py` | FastAPI router (`/api/json-cache`) for building, polling status of, inferring a schema for, deleting the on-disk JSON→parquet shredded cache, and serving a path-validating compatibility cancel no-op. Delegates schema, shredding, and storage lifecycle to the JSON-shredding component. |

## Key types and data structures

- **`GraphFingerprintMemo`** (`_cache.py`) — `dict[_UtilityFileStatKey, str]` mapping
  a utility file's `(path, mtime_ns, size)` to its content hash. `_utility_file_hash`
  reads/writes it as a thin per-request pin on top of the module-level
  `_utility_file_hash_cache` (a `StatGatedCache[str, str]`, keyed by
  `artifact_cache_key`) — the memo's job is only to guarantee every
  `graph_fingerprint`/`execute_trace` call *within one request* agrees on the digest
  for a given gate, not to provide the primary caching (the process-wide cache
  already does that across requests). Scoped to one request/operation regardless;
  not safe to reuse across independent calls because file metadata alone cannot
  detect every edit (an editor can preserve both `mtime` and `size` while changing
  bytes).
- **`CacheConsumerContract` / `CheckedCacheInputs`** (`_cache.py`) — immutable,
  versioned exact-field contracts over the closed `CacheInputClass` set.
  `checked_cache_inputs()` rejects missing or unknown fields and exposes ordered
  raw values or a namespaced canonical payload.
- **`CACHE_CONFIG_FIELD_CLASSIFICATIONS`** (`_cache.py`) — immutable
  per-`NodeType` classification of every recognised config key as a logical
  execution input or a rationale-bearing presentation exclusion.
- **`ALGO_VERSION: int`** (`_cache.py`, currently `7`) — read dynamically inside
  `graph_fingerprint()` (not captured at import time) so tests can monkeypatch it
  to simulate a version bump. Embedded as a `"v<N>:"` prefix on every fingerprint.
- **`LRUCache[K, V]`** (`_lru_cache.py`) — `OrderedDict`-backed store (`_data`),
  a `set` of pinned keys (`_pinned`) plus optional constructor-level `pin_slots`,
  optional per-entry byte sizes (`_sizes`, `_current_bytes`) when `max_bytes` is
  configured, optional TTL timestamps (`_timestamps`). All access serialized by a
  single `threading.RLock` (`_lock`). Invariant: `_capacity_entry_count()` — the
  count of *unpinned* entries — never exceeds `max_size` except transiently while
  every live entry is pinned.
  TTL is insertion/update based rather than sliding: `get()` lazily removes an
  expired entry and returns `None`, even if it is pinned; `__contains__` is only a
  lightweight presence probe and neither checks TTL nor promotes the entry.
- **`FingerprintCache`** (`_fingerprint_cache.py`) — `LRUCache[str, dict[str, Any]]`
  where each value is a dict with a fixed set of declared `slots`. `_capacity_entry_count`
  is overridden to count *all* entries (not just unpinned ones) — a fully-pinned
  fingerprint cache rejects new fingerprints instead of growing unboundedly.
- **`DataFrameExecutionCacheKey`** (`_dataframe_execution_cache.py`, frozen
  dataclass) — exact identity for one reusable materialized frame: `cache_key`
  (the payload digest string), plus the individual components that fed it
  (`namespace`, `node_id`, `lineage_fingerprint`, `source`, `profile`,
  `input_fingerprint`, `required_columns`, `extra_keys`,
  `execution_policy_fingerprint`, schema `version`).
- **`DataFrameExecutionCacheEntry`** (frozen dataclass) — one owned parquet
  artifact: `key`, resolved `path`, `row_count`, `column_count`, `columns`,
  `size_bytes`, `uncompressed_size_bytes`.
- **`DataFrameExecutionCacheRequest`** (frozen dataclass, `__post_init__`
  validated) — a caller's opt-in materialization plan: `cache`, `keys_by_node`
  (mapping node ID → key, validated non-empty and internally consistent),
  optional `streaming_chunk_size`, `fast_checkpoint`. Every key must carry a
  non-`None` `execution_policy_fingerprint`.
- **`DataFrameExecutionCache`** (`LRUCache[str, DataFrameExecutionCacheEntry]`
  subclass) — adds `root` (artifact directory), `_materialize_locks`
  (per-cache-key `WeakValueDictionary` of `RLock`s for serialising same-key
  writes), `_scan_refcounts` (live `pl.scan_parquet` handles per `(key, path)`),
  `_store_pins` (open store+first-consume windows, reentrant count per key).
  Its default byte cap comes from `DATAFRAME_EXECUTION_CACHE_MAX_BYTES`, parsed
  once at module import from `HAUTE_DATAFRAME_EXECUTION_CACHE_MAX_BYTES`; unset or
  empty means `None`, while malformed/non-positive values raise `RuntimeError`.
- **`StatGatedCache[K, V]`** (`_stat_gated_cache.py`) — `_entries: dict[K, tuple[int, int, V]]`
  (gate `mtime_ns`, gate `size`, value) plus `_load_locks: dict[K, threading.Lock]`
  for single-flight loading, both guarded by one `threading.Lock`.
  `resolve_artifact_path()` (case-preserving, for stat/I/O) is distinct from
  `artifact_cache_key()` (case-folded via `os.path.normcase`, for the dict key
  only) — callers must not swap the two.
- **JSON cache route module state** — `_build_progress: dict[str, dict[str, Any]]`
  (module-level, guarded by `_build_progress_lock`), keyed by resolved absolute
  data path, tracking `started_at`, `active_count` (reentrant concurrent-build
  counter), `phase`. Not persisted; process-local, in-memory only.

## Control flow

### Graph fingerprinting (`_cache.py`)

1. `graph_fingerprint(graph, *extra_keys, memo=None)` reads
   `graph._haute_base_fingerprint` — a `cached_property` on `PipelineGraph` itself
   (defined outside this component) that memoises `_graph_base_fingerprint(graph)`
   for the lifetime of that graph instance. `model_copy` on a `PipelineGraph`
   produces a new instance and clears the memo, so structural edits (via the
   immutable `model_copy(update=...)` idiom used elsewhere) never serve a stale
   base digest. Direct in-place mutation after the cached property has been read is
   unsupported and can leave the graph instance's base fingerprint stale.
2. `_graph_base_fingerprint` builds the checked `GRAPH_STRUCTURE` payload.
   Nodes contain `id`, execution-relevant `label`, `nodeType`, and classified
   config; edges contain `source`, `sourceHandle`, `target`, and
   `targetHandle`. Presentation-only config fields and edge IDs are excluded.
   Nodes sort by ID, edges sort by canonical encoding, and the namespaced
   canonical payload is hashed with `content_hash_bytes`.
3. `graph_fingerprint` separately computes `preamble_execution_fingerprint` for
   the graph's preamble text (and, if the preamble imports the `utility`
   package, a digest of the resolved `utility` module/package's file contents —
   resolved via `importlib.machinery.PathFinder` against the same prioritised
   search path the executor installs at exec time).
4. `graph_fingerprint` builds the checked `GRAPH_EXECUTION` payload from the
   base digest, preamble/utility digest, `source_file`, and ordered
   `extra_keys`, then always hashes that namespaced canonical payload.
5. The result is returned as `f"v{ALGO_VERSION}:{digest}"`.

### Dataframe execution cache key construction

1. `dataframe_execution_cache_key(graph, node_id=..., namespace=..., source=...,
   profile=..., input_fingerprint=..., ...)` first validates all string inputs
   non-empty, normalises `required_columns` (deduped, sorted) and `extra_keys`
   (order-preserving), and resolves `profile` to its cache-identity string via
   `dataframe_execution_cache_profile` (`AUTO_RANGE` collapses to
   `OPTIMISER_SETUP` — the two profiles are cache-equivalent).
2. `_upstream_subgraph(graph, node_id)` builds a `PipelineGraph` containing only
   `node_id` and its transitive upstream ancestors (via `upstream_node_ids`),
   preserving the parent graph's `preamble`, `source_file`, etc.
3. `graph_fingerprint()` is called on that filtered subgraph to get
   `lineage_fingerprint` — this is what scopes cache reuse to upstream-only
   changes.
4. If `execution_policy` is given, `dataframe_execution_policy_fingerprint()`
   hashes its `canonical_json` encoding separately (stored as
   `execution_policy_fingerprint` on the key) *and* the raw policy is embedded
   directly in the checked `DATAFRAME_EXECUTION` payload (not just its
   fingerprint) before the namespaced canonical payload is hashed into
   `cache_key`.
5. The final `cache_key` string is `f"dfexec:v{DATAFRAME_EXECUTION_CACHE_VERSION}:{payload_digest}"`.

### Dataframe execution cache read/write (`materialize_lazy_frame_with_cache`)

1. Enter `cache.materialization_lock(key)` — acquires (or creates) a per-cache-key
   `RLock` via `_materialize_locks` (a `WeakValueDictionary`, so locks for keys no
   longer referenced anywhere are garbage collected), then increments
   `_store_pins[key.cache_key]` under `self._lock` to open the store+first-consume
   window. The RLock is reentrant so nested calls for the same key compose.
2. `cache.scan(key)` is tried first — a cache hit under the lock: promote the
   `LRUCache` entry, validate the artifact still exists/parses
   (`_evict_if_invalid` → `_validate_entry`, which does `entry.path.exists()`
   then `pl.scan_parquet(entry.path).collect_schema()`), increment
   `_scan_refcounts[(cache_key, path)]`, `pin()` the key, then open the actual
   `pl.scan_parquet` outside the lock and register a `weakref.finalize` on the
   returned `LazyFrame` that calls `_release_scan` when it is garbage collected.
3. On miss: `cache.path_for_key(key)` derives a filesystem path
   (`{content_hash(cache_key)}-{uuid4}.parquet` under `cache.root` — the UUID
   suffix means concurrent writers for logically-different scan attempts of the
   same key never collide on a path, even though the `RLock` already serialises
   same-key writers). `bounded_sink(lf, path, ...)` (execution engine) performs
   the actual materialization; `read_parquet_metadata(path)` reads back
   row/column/byte counts; `cache.store_artifact(key, path, metadata)` inserts
   the entry (evicting any existing entry under the same `cache_key` first, then
   checking the byte budget, then `put()`-ing and re-`get()`-ing to confirm the
   store held). Any exception during sink/metadata/store unlinks the partial
   `.parquet` and `.parquet.tmp` files and re-raises.
4. After a successful store, `cache.scan(key)` is called again (the same path a
   later hit would take) so the freshly written artifact is validated identically
   to a cache hit; a `None` result here (entry vanished) raises
   `DataFrameExecutionCacheError`.
5. The `finally` block of `materialization_lock` decrements `_store_pins`; when
   it reaches zero the window closes and `_evict_if_over_capacity()` runs
   immediately to settle any byte-budget debt that accumulated while the window
   exempted this key from eviction. The lock is always released even if the
   settle step raises (e.g. a Windows sharing-violation `PermissionError` during
   an unlink).

### Dataframe execution cache eviction and artifact lifecycle

- `_is_pinned(key)` = `_store_pins[key] > 0` OR base `LRUCache` pin (live scan
  refcount > 0 via `pin()`/`unpin()`). Both exempt a key from LRU victim
  selection.
- `_capacity_entry_count()` is overridden so a store-window pin (unlike a scan
  pin) still counts toward `max_size` — the window protects a key from being
  *chosen* as a victim, it does not exempt it from the entry-count budget the
  way `LRUCache`'s base pinning does.
- `_remove_key(key)`: unlinks the artifact file immediately unless the base
  `LRUCache` pin (live scan) is set — i.e. a store-window-only pin does not
  block deletion when the entry is removed for cause (invalid artifact,
  explicit `clear()`, LRU eviction), but a live scan does.
- `_release_scan(cache_key, path)`: decrements the refcount; at zero, unpins the
  key (subject to `_evict_if_over_capacity()` immediately reclaiming it) and
  unlinks the path itself only if no *currently stored* entry (before or after
  the state change) still owns that exact path — this "single-unlink rule"
  prevents double-deleting a path that a same-key replacement has already
  taken over.
- `clear()` acquires every live per-key materialize lock (to prevent a
  concurrent store racing the clear), then removes every entry under the shared
  lock, then releases the locks in reverse acquisition order.

### JSON cache route dispatch (`routes/json_cache.py`)

- **Schema source selection** (`_select_v2_config`): `body.volatile_schema is not
  None` wins over disk; `volatile_schema` is checked for `is not None` (not
  truthiness) so an explicit empty `{}` from the editor is treated as "user
  supplied this" and validated (and rejected) rather than silently falling
  through to disk. Otherwise `_resolve_config_path` + `_read_v2_config` reads
  the on-disk config, tolerating "file absent" and "valid JSON but not v2 shape"
  as `None` (migration path) while raising `ApiInputSchemaError` for unreadable
  or malformed-JSON files.
- **`build_json_cache`**: resolves the data path (400/403 on path-resolution
  failure), resolves the schema source (422 on missing/invalid), checks data
  file existence (404), then calls `build_per_port_cache` under
  `run_blocking_with_response_timeout` (env-configurable via
  `HAUTE_BUILD_TIMEOUT`, default 1800s) wrapped in `_start_build_progress`/
  `_finish_build_progress` bookkeeping. On success, calls
  `_mark_working_consulted(data_path)` — this arms the later save-time promotion
  of `working/` into `committed/`; a failed build deliberately does not call it.
- **Progress tracking** (`_build_progress`): keyed by resolved absolute path
  (`_progress_key`), `active_count` is incremented per concurrent build attempt
  for the same path and only removed from the dict when it reaches zero — so two
  overlapping builds for the same file both see `active=True` until both finish.
- **Status** (`GET`/`POST /status`): both funnel through `_v2_status_response`,
  which re-runs `validate_v2_schema` (so status enforces the same invariants as
  build) before checking `is_per_port_cache_valid` and reading back
  `read_per_port_cache_meta`. The `GET` variant is read-only/disk-only (no
  `volatile_schema`) and downgrades every schema/config error to `cached=False`
  rather than a 422, since a polling GET has no user action to react to.
- **`_aggregate_v2_tables`** is the single aggregation core both the build
  response and the status response reduce their per-port `tables[]` list
  through, so the two response shapes cannot drift independently.
- **Cancel** (`POST /cancel`) resolves and validates `body.path`, then always
  returns `JsonCacheCancelResponse(cancelled=False, data_path=body.path)`. There
  is no cancellation token or build-registry stop signal: the compatibility
  endpoint and the editor action that calls it do not stop an in-progress build.

## Edge cases and invariants

- **Canonical JSON set ordering is total, including `NaN`.** `_sort_key`
  segregates `NaN` into a fixed terminal bucket `("2_num", (1, 0.0))` distinct
  from every finite/`inf` value, so a set containing `NaN` canonicalises
  identically regardless of insertion order — `NaN != NaN` would otherwise make
  `sorted()` order-dependent.
- **Fingerprint framing is unambiguous before hashing.** Node lines, edge lines, and the
  extra-keys/context/base join in `graph_fingerprint` are all framed through
  `canonical_json` (JSON arrays), specifically so a node id or extra key
  containing a literal `|` or `\n` cannot collide with a logically different
  graph under the old raw-concatenation scheme (fixed at `ALGO_VERSION` bump to
  6, tracked as "W1-cache F164"/"F163" in the version-history comment in
  `_cache.py`). The final xxh64 digest remains a finite 64-bit hash and therefore
  is not mathematically collision-free.
- **Utility-file hashing now routes through `StatGatedCache.get_or_load` itself**,
  rather than an independent re-stat-after-reading implementation: `_utility_file_hash`
  delegates to the process-wide `_utility_file_hash_cache`, which retries once on a
  torn load (metadata changed between the pre- and post-load stats) before raising
  `RuntimeError`. This makes the retry discipline literally shared code with every
  other `StatGatedCache` consumer, not merely the same pattern reimplemented
  independently.
- **`GraphFingerprintMemo` is explicitly NOT a complete correctness boundary** —
  its own docstring states an editor that preserves both `mtime` and `size`
  while changing bytes defeats it; it must be scoped to one immutable
  request/operation, never reused across independent calls.
- **`LRUCache.put` on an oversized value (with `max_bytes` configured) does not
  cache it at all**, and if a key being replaced happens to already exist in the
  cache, that stale existing entry is removed rather than left stranded.
- **`LRUCache.pin`/`unpin` on an unknown key is a silent no-op** — deliberately,
  so a pin racing a rollback/eviction doesn't need coordination.
- **`FingerprintCache.store` constructs every declared slot atomically** before
  delegating to the shared LRU implementation, so supported cache entries are
  always complete and `try_get` can return the fixed slot mapping directly.
- **`FingerprintCache.update_slot` avoids remeasuring unchanged heavy slots**:
  updating a slot outside `size_sensitive_slots` under a byte cap preserves the
  stored byte estimate and just moves the entry to MRU, rather than re-running
  `size_of` (which could drift under allocator-dependent measurement) on an
  object that didn't change. A custom `size_sensitive_slots` declaration is
  accepted only with an active `max_bytes`/`size_of` pair; that declaration is
  the caller's explicit assertion of which slot changes can affect `size_of`.
- **`DataFrameExecutionCache.path_for_key` includes a UUID suffix** specifically
  so concurrent same-key store attempts (racing before the `RLock` was
  introduced, or across separate cache instances pointed at the same directory)
  cannot collide on a filename.
- **`artifact_cache_key` vs `resolve_artifact_path`**: the former case-folds via
  `os.path.normcase` (a no-op on POSIX, folds on Windows) and must only be used
  as a dict key, never for `stat`/`open` — a folded spelling need not exist on a
  case-sensitive filesystem. The module docstring notes macOS (case-insensitive
  filesystem, case-preserving API) as a residual gap: two case-spellings of one
  path can still occupy two `StatGatedCache` slots there.
- **`DATAFRAME_EXECUTION_CACHE_VERSION` does not need bumping when
  `ALGO_VERSION` (graph fingerprint algorithm) bumps** — the dataframe cache
  payload embeds the *versioned* `lineage_fingerprint` string, so a graph
  fingerprint algorithm change already rolls every dataframe cache key
  transitively. The dataframe cache's own version only needs bumping when the
  payload's field set/semantics change independently.
- **`_no_schema_source_response` and `_api_input_schema_error_response` share the
  exact same body shape** (`{"detail": ..., "type": "ApiInputSchemaError"}`) so
  the frontend has one discriminator for both "no schema at all" and "schema
  present but invalid."

## Error handling

| Exception | Raised by | Propagates to |
| --- | --- | --- |
| `TypeError` | `canonical_json`/`_canonicalise` on non-JSON-shaped values, non-string mapping keys, non-list/tuple/set iterables | Any fingerprinting call site; not caught internally |
| `RuntimeError` | `_utility_file_hash` (torn read after retry), `StatGatedCache.get_or_load` (torn gate after retry) | Caller of `graph_fingerprint`/`get_or_load` |
| `RuntimeError` | `_dataframe_execution_cache.py` import with a malformed/non-positive `HAUTE_DATAFRAME_EXECUTION_CACHE_MAX_BYTES` | Importing caller; no default cache is constructed |
| `FileNotFoundError`/`IsADirectoryError`/`PermissionError`/`OSError` | `_hashing.content_hash` (via `Path.open`), `_stat_gated_cache` stat calls | Propagate unchanged — not wrapped |
| `ValueError` | `LRUCache.__init__` (bad `max_size`/`max_bytes`/`size_of` combination), `LRUCache.put` size-callback contract violation, `FingerprintCache.__init__`/`.store`/`.update_slot` (unknown slot names, empty `slots` tuple), `DataFrameExecutionCacheRequest.__post_init__`, `_normalise_required_columns`/`_normalise_extra_keys`/`_normalise_non_empty` | Caller constructing the cache/request |
| `TypeError` | `DataFrameExecutionCacheRequest.__post_init__` (wrong types for `cache`/`keys_by_node`/key values) | Caller constructing the request |
| `DataFrameExecutionCacheError` (base) | `_dataframe_execution_cache.py` | Caller of `materialize_lazy_frame_with_cache`/`store_artifact` |
| `CacheArtifactMissingError` (`DataFrameExecutionCacheError`, `FileNotFoundError`) | `_validate_entry` | `_evict_if_invalid` (caught, treated as eviction+miss) or re-raised to caller when it's the just-written artifact failing validation |
| `CacheArtifactCorruptError` (`DataFrameExecutionCacheError`) | `_validate_entry` (unreadable parquet) | Same as above |
| `CacheArtifactTooLargeError` (`DataFrameExecutionCacheError`) | `store_artifact` when `size_bytes > max_bytes` | Propagates through `materialize_lazy_frame_with_cache` after the artifact is unlinked; `_execute_lazy` catches it and continues uncached |
| `ApiInputSchemaError` | `_read_v2_config`, `_select_v2_config`, `validate_v2_schema` (via `_v2_status_response`), `build_per_port_cache`, `infer_v2_schema_from_data` | Caught at each route handler and turned into a 422 JSON response (or, on the read-only GET status path, downgraded to `cached=False`) |
| `HTTPException` | `_resolve_data_path`/`_resolve_config_path` (400/403), `build_json_cache`/`infer_json_cache_schema` (404/422/500/504) | FastAPI's standard exception handling |
| `orjson.JSONDecodeError` | `build_per_port_cache`/`infer_v2_schema_from_data` (unparseable data file) | Caught in the route handler, turned into 422 |
| `TimeoutError` | `run_blocking_with_response_timeout` when a build exceeds `HAUTE_BUILD_TIMEOUT` | Caught in `build_json_cache`, turned into 504 |

## Testing

Tests live in `tests/`, one file (or a small cluster) per module, plus several
cross-cutting regression/property files:

- **`tests/test_hashing.py`** — unit tests on `_hashing.py`: algorithm constant
  pinning (`TestHashAlgoConstant`), known-vector digests, digest shape/length,
  file-vs-bytes round trip equivalence, determinism across repeated calls,
  change detection (any byte flip changes the digest), streamed reads for large
  files, missing-file/directory-input/unicode-filename edge cases.
- **`tests/test_lru_cache.py`** and **`tests/test_lru_cache_contracts.py`** —
  unit and contract tests for `LRUCache`: init validation, get/put, entry-count
  and byte-aware eviction, TTL (including edge cases and `__contains__`
  interaction), dunder methods, thread-safety under concurrent access, `None`
  values/keys, `clear()` reuse, large-cache behaviour, `evict_where` (atomicity
  under lock, return values, LRU-order preservation, hit/miss stats). The
  contracts file also pins the migration invariant that specific call sites
  either use `LRUCache` or `functools.lru_cache` as designed
  (`TestCategoryAFilesDropLRUCacheImport`, `TestCategoryBFilesKeepLRUCache`),
  and that `StatGatedCache`-style memoisation contracts hold for
  external-object/optimiser/mlflow artifact loading.
- **`tests/test_fingerprint_cache.py`** — construction/validation, basic
  set/get, miss behaviour, `invalidate()`, `update_slot` (including edge cases
  and duplicate stores), thread safety, `repr`, pinning, the `fingerprint`
  (MRU) property, and a `TestGraphFingerprint` class covering fingerprint
  determinism from this cache's perspective.
- **`tests/test_cache_fingerprint_injectivity.py`** and
  **`tests/test_graph_fingerprint_cached.py`** — the fingerprint-correctness
  core: `NaN` set-ordering determinism, separator injectivity (ids/keys
  containing `|`/`\n` do not collide), fingerprint completeness (every
  execution-relevant field is covered), edge-handle sensitivity, `TypeError`
  for unknown types, canonical-JSON encoder unit and property tests, encoder
  unification regression tests, preamble utility-import detection, and the
  `PipelineGraph._haute_base_fingerprint` `cached_property` contract
  (recompute-on-mutation via `model_copy`, extra-keys are NOT baked into the
  cached base fingerprint, a `TestFingerprintRecomputeSpy` guarding against
  redundant recomputation, and a benchmark class).
- **`tests/test_dataframe_execution_cache.py`** — the largest single test file
  for this component: cache-key construction (upstream-subgraph scoping, edge
  handle rewire sensitivity, explicit input-fingerprint requirement, execution
  policy partitioning, `AUTO_RANGE`/`OPTIMISER_SETUP` profile equivalence, set
  ordering/injectivity of the policy fingerprint, `ALGO_VERSION` bump rolling
  dataframe cache keys without a schema version bump), materialize/scan/store
  round trips, stale-artifact-after-rewire non-reuse, failed-collect
  non-caching, eviction removing owned artifacts, replacement removing old
  artifacts, live-scan pinning during eviction (including multiple concurrent
  scans and derived-LazyFrame-keeps-source-alive), `clear()` semantics
  (including preserving a live scan until release), oversized-artifact
  rejection, repeated scan-then-release path deletion, missing/corrupt artifact
  eviction-as-miss, and an extensive concurrency section (byte-pressure store
  never evicting itself, store-window protection ending after first consume,
  concurrent stores under byte pressure each surviving their own window,
  materialization-lock release on settle failure, oversized-artifact policy
  staying correct under concurrency, serialised same-key materialization,
  unrelated keys not blocking each other, and in-flight locks staying
  discoverable through `clear()`).
- **`tests/test_cache_unification.py`** — regression guards from the
  `LRUCache`/`FingerprintCache` consolidation: pre-refactor behavioural parity,
  the `graph_fingerprint` helper's stability, the unified cache's `pinning`
  kwarg and pin/unpin methods, `FingerprintCache`'s retirement-tolerant
  contract (`TestFingerprintCacheRetired` — accepts either module removal or a
  thin `LRUCache` alias), and thread safety of the unified cache.
- **`tests/test_cache_perf_fixes.py`** and
  **`tests/test_preview_cache_byte_awareness.py`** — preamble-cache correctness
  and benchmarks (pre- and post-refactor), the xxh64 migration and cache-key
  algorithm contract, and fingerprint-cache byte-limit/preview-cache-sizing
  behaviour.
- **`tests/test_preview_cache_hint.py`** and
  **`tests/test_caching_correctness.py`** — consumer-level correctness of
  preview/trace caching built on this component: diamond-shaped graphs reading
  a shared source once, diamond results unchanged by caching, memory neutrality
  on a realistic graph, and where the cache-hint call site lives.
- **`tests/test_cache_materialize_guard.py`** — a small focused module (rejects
  multi-port dict inputs with a named error, passes a `LazyFrame` through by
  identity, lazifies a bare `DataFrame`) guarding
  `materialize_lazy_frame_with_cache`'s input contract.
- **`tests/test_json_cache_routes.py`, `test_json_cache_coverage_uplift.py`,
  `test_json_cache_mut_witnesses.py`, `test_json_cache_corrupt_and_errors.py`** —
  the JSON cache route surface:
  build/status/delete/infer happy paths, the cancel endpoint's validated
  `cancelled=false` compatibility response, error precedence, path
  traversal/null-byte rejection (400/403), build-progress accounting (balanced
  start/finish, overlapping builds), aggregation correctness (missing vs
  present parquet files, summed counts across tables), corrupt-vs-absent config
  distinction (422 vs migration `None`) and route-level surfacing of shred/build
  failures. `tests/test_json_cache_integrity.py` and the schema/shred assertions
  inside these cross-component files are indexed by
  [json-shredding](../json-shredding/low-level.md); they verify the storage and
  shredding modules consumed by this router rather than cache primitives owned here.

Known coverage gaps: none identified from a read of the test file list and
class/function names above — the JSON-cache and dataframe-execution-cache
surfaces in particular have unusually thorough concurrency and corruption-path
coverage relative to typical cache modules. This spec does not itself execute
the suite; treat file/class presence as an index, not a substitute for running
`pytest tests/test_dataframe_execution_cache.py tests/test_json_cache_routes.py`
etc. when changing this component.

## Polars backend contracts (0.6.0)

The implementation satisfies this low-level contract. The
[caching roadmap](../../roadmap/caching.md) has no open package following the
identity audit and evidence-gated performance pass.

### Preview/trace key construction and lifecycle

- One shared, versioned preview/trace key-factory API exists at the cache boundary.
  Its mandatory inputs are the original graph; the source-pruned
  `prepare_graph(graph, target, source)` result; explicit target;
  selected source; normalised requested columns; initial width; row limit;
  port/frame handle; contract fingerprint; selected live-switch path;
  runtime/input fingerprint; and execution-semantics version. `None`, empty,
  default, and named values are encoded as distinct explicit fields. Preview and
  trace modules consume this API and do not own parallel key formats.
- The factory derives a canonical lineage payload containing deterministically
  ordered execution-relevant nodes and edge endpoints/handles, including
  relevant node labels/config, plus graph-level `preamble` and `source_file`.
  It excludes presentation metadata (`preserved_blocks`, available `sources`,
  `active_source`, edge IDs), downstream nodes, and disconnected graph state;
  selected source is an explicit checked field. All map/set and graph
  insertion-order differences normalise before `canonical_json()`; unsupported
  fields propagate its `TypeError` rather than falling back to `repr()`.
- Cache store returns an explicit retained-or-rejected outcome. It computes size
  and rejects an oversized candidate before mutating the cache. Rejection leaves
  an existing value under the same key untouched, performs no victim eviction,
  and cannot create a request lease or pin.
- An accepted store replaces/inserts atomically while holding the cache lock.
  Readers see a complete old or complete new entry, never a partially updated
  value or accounting state. Concurrent accepted same-key stores use completion
  order: the last completed store wins. Entry count and byte count are updated in
  the same critical section and remain coherent through replacement and eviction.
- Only a confirmed retained outcome may acquire the request lease/pin used by a
  consumer. Every acquired lease/pin is released from `finally`, including when
  the consumer raises. The oversized outcome may continue with the already
  computed value only where the caller's existing contract allows it; any other
  store exception propagates unchanged and is not downgraded to a miss.

### Runtime fingerprints, hashing, and cleanup

- Runtime data-path fingerprinting delegates unchanged-path reuse to the
  shared `StatGatedCache` loader pattern used for utility hashes. The loader still
  computes the required content hash on a cache miss and preserves the existing
  torn-read failure semantics.
- A matching `(mtime_ns, size)` is only a gate for reusing the same operation's
  loaded content hash. It must not bypass JSON parsing, schema/version validation,
  source selection, or any other semantic check required by a distinct JSON
  operation. No stat-only cross-operation JSON-validity cache is permitted.
- All maintained cache-key payloads use `_cache.canonical_json()`. The
  benchmark-approved dataframe row-hash path uses a tagged canonical
  little-endian UInt64 buffer and preserves equal-frame/different-frame identity
  for supported shapes, including nulls and empty frames. Its representation tag
  documents the deliberate one-time invalidation of the old allocation-heavy
  decimal encoding.
- Obsolete `COLLECT_LAZY` selection paths, dead sink helpers, and no-op projection
  guards are absent, with tests pinning no observable execution or diagnostic
  effect. Do not retain compatibility aliases with no
  live caller merely to conceal an incomplete migration.

### Required automated evidence

- Unit and consumer tests prove preview and trace reuse survives downstream,
  disconnected, and presentation-only edits and is invalidated by each
  execution-relevant lineage/config/wiring change plus `preamble`,
  `source_file`, and explicit source. Ordering permutations of relevant nodes,
  edges, mappings, and sets produce the same key.
- A field-by-field matrix independently mutates the original graph, prepared
  source-pruned graph, target, source/active source, requested columns, initial
  width, row limit, port/frame handle, contract fingerprint, selected live-switch
  path, runtime/input fingerprint, and execution-semantics version. Every semantic
  mutation changes identity; canonical equivalents and ordering-only changes do
  not.
- Store tests prove oversize is assessed before mutation; a rejected same-key
  replacement preserves the old value, triggers no eviction or pin, and leaves
  accounting unchanged. Concurrency tests prove readers observe only complete
  old/new values, last-completed accepted same-key store wins, and byte/entry
  counts remain coherent.
- Lease tests prove only retained stores acquire protection and every lease/pin is
  released in `finally`. Fault-injection tests prove unexpected store exceptions
  propagate and are never reported as an oversize rejection or cache miss.
- `StatGatedCache` tests prove no content re-read on an unchanged path, reload on
  either metadata dimension changing, and preserve loader/torn-read failures.
  JSON route/component tests prove matching metadata alone cannot make one
  operation accept another operation's JSON-derived result.
- The direct row-hash-buffer path has equivalence tests plus a repeatable
  benchmark with the 20% material-improvement threshold and recorded decision.

## Approved change contract — 0.7.0 shared input snapshots

Remaining source-cache improvement work is tracked in the
[caching roadmap](../../roadmap/caching.md).

### New source-cache module

- Add `src/haute/_source_cache.py` as this component's primary owner. It defines
  `SourceCacheIdentity`, `SourceCacheMetadata`, `SourceCacheStatus`,
  `SourceCacheGeneration`, `SourceCacheBuildContext`, `SourceCacheBuilder` (protocol),
  `SourceCacheStore`, and the typed cache error family. Provider adapters are registered by the
  I/O and Databricks components; `_source_cache.py` has no connector imports.
- `SourceCacheIdentity` canonical bytes use the repository's canonical JSON/hash primitives and
  a versioned identity schema. The digest selects
  `.haute_cache/inputs/<digest>/`. `meta.json` records the full redacted canonical identity and
  its schema version so hash collisions or version drift fail validation rather than selecting
  an unrelated artifact.
- Each identity directory contains immutable `generations/<generation-id>/data.parquet` and
  `meta.json` plus one atomically replaceable `current.json` pointer. A builder writes into a
  unique sibling staging directory, fsyncs/closes its data, computes signatures and metadata,
  renames the generation into place, then atomically replaces `current.json`. Publication never
  edits an already-visible generation.
- A per-identity single-flight lock admits one builder while allowing independent identities to
  build concurrently. Reader leases pin the generation id they validated. Refresh, clear, quota
  eviction, and process-start orphan cleanup may delete only unleased non-current generations;
  current-generation replacement retires the old generation and deletes it after its final
  lease.
- `HAUTE_INPUT_CACHE_MAX_BYTES` (default 20 GiB) and
  `HAUTE_INPUT_CACHE_MAX_GENERATIONS` (default 64) are positive project-store limits.
  Publication deterministically evicts the oldest unleased current generations, with identity
  digest and generation id as stable tie-breakers; when pinned generations prevent enough
  reclamation, publication fails with the typed quota error and leaves them intact.
- Builder output is an iterator/stream of Arrow record batches or an already-lazy bounded source,
  never an untyped callback returning an arbitrary `DataFrame`. `SourceCacheBuildContext`
  carries execution profile, cancellation/deadline checkpoints, progress counters, and the
  declared `bounded | admitted_eager | unsupported` build class. The store rejects a mismatch
  before invoking the builder.
- Generation validation checks identity version/digest, Parquet existence and footer/schema,
  signed size/hash, metadata shape, and local-file signature where applicable before returning
  `pl.scan_parquet`. A corrupt or mismatched current pointer is a typed failure and never falls
  back to another generation silently.

### API and tests

- `src/haute/routes/input_cache.py` becomes the shared HTTP owner for build/refresh, progress,
  status, and clear. Requests carry a validated `dataInput` source descriptor; responses expose
  only redacted identity, state, progress, generation metadata, and typed failure information.
  The Databricks-specific cache routes are removed when their frontend callers move.
- `src/haute/schemas.py` and the frontend API guards add versioned input-cache request/status
  models. Status separates local state (`missing | building | ready | corrupt | failed`) from
  freshness (`fresh | stale | unknown`) and build boundedness.
- Add focused tests for identity canonicalisation, metadata/signature validation, publisher
  crash points, reader leases across refresh, clear/eviction races, per-key single flight,
  progress isolation, cancellation/deadline cleanup, quotas, redaction, and provider protocol
  conformance. Database, Databricks, lakehouse, and file integration suites supply their own
  builders against the same store contract.

## Checked fingerprint completeness

The shared implementation follows the high-level
[checked fingerprint completeness](high-level.md#checked-fingerprint-completeness)
contract through the following low-level boundaries.

### Checked consumer contracts

- `_cache.py` defines the closed logical-input enum, cache-consumer enum,
  immutable consumer-contract records, and the checked input-payload builder.
  Each consumer contract has a key-schema version, an exact ordered payload
  field set, and a total classification of logical input classes. Construction
  raises on a missing/extra payload field, an unknown referenced field, an
  input class classified twice, or an exclusion without a rationale.
- Maintained consumers include graph execution, preview/trace lineage,
  dataframe execution, runtime graph inputs, deploy output-schema inference,
  model feature-contract validation, and provider-neutral input snapshots.
  Consumer code passes mappings into the checked builder and hashes or keys the
  resulting canonical/raw values according to its existing persistence and
  hot-path needs; it does not reconstruct a parallel field list.
- Cache namespaces and versions are owned by the corresponding consumer
  contract. Graph identity is deliberately version-bumped when node labels,
  source-file resolution, or presentation exclusions change its canonical
  bytes. Dataframe and lineage payload versions are independently bumped when
  their own field sets change.

### Field and runtime classification

- `_cache.py` owns an explicit per-node-type classification for every field in
  `_config_validation.VALID_KEYS`, with universal fields classified once.
  Execution fields are retained in node identity; excluded fields carry a
  non-empty rationale. Runtime treatment is conservative for an unrecognised
  legacy key, but reflective tests compare the maintained registry to
  `VALID_KEYS` so a new official field cannot merge unclassified.
- Node identity contains `id`, execution-relevant `label`, `nodeType`, and the
  classified config. Edge identity contains `source`, `sourceHandle`, `target`,
  and `targetHandle`; edge `id` is excluded because selection and binding are
  determined before fingerprinting and do not depend on that display ID.
  Pipeline execution context contains `preamble`, its utility-content
  fingerprint, and `source_file`; pipeline display metadata,
  `preserved_blocks`, `sources`, and `active_source` are excluded because every
  consumer supplies the selected source explicitly.
- `execution.py` replaces its bespoke runtime-path dictionary/lock with a
  `StatGatedCache`. Its runtime-input registry distinguishes source data,
  snapshot generation pointers, external files, model artifacts, feature
  contracts, and optimiser artifacts. Preview/trace, dataframe graph-input,
  deploy-schema, and deploy scorer identity all consume the structured result.
  Missing paths remain explicit identity values and changing either stat-gate
  dimension reloads the content fingerprint.

### Required automated evidence

- Registry reflection adds/removes a recognised config field under test and
  proves the classification check fails until the registry changes. A consumer
  matrix proves every logical input class is consumed or has a rationale and
  every checked payload rejects missing/extra dimensions.
- Mutation tests cover node label/type/config/code, upstream lineage and order,
  edge endpoints/handles, preamble/utility, source file, explicit source,
  requested columns, row limits, contracts/policies, runtime files, and
  artifacts. Presentation-only mutations remain stable. Existing separator,
  ordering, `None`, set/NaN, and path-collision probes remain green.
- Consumer regressions prove preview/trace parity and immediate runtime-file
  invalidation, dataframe lineage invalidation, deploy-schema invalidation
  after direct-input or artifact replacement, model-contract schema/contract
  separation, and input-cache provider/query/secret semantics.
- `tests/performance/test_cache_identity_perf.py` records separate evidence for
  row-hash conversion, LRU operations, stat-gated path identity, and canonical
  lineage serialisation. Run it through `scripts/run_perf_suite.py` so
  `.cache/perf/perf-report.json` records the environment and artifact. Each
  comparison records a 20% materiality threshold and an
  implement/no-change decision; no optimisation is merged solely from an
  absolute wall-clock threshold.
- `execution.dataframe_frame_input_fingerprint` feeds
  `hash_rows(seed=0)` through a canonical little-endian UInt64 buffer tagged
  `polars-u64-le:v1`; the representation change deliberately cold-starts
  dependent keys. The LRU/stat and cross-request lineage-memo gates record
  `no_change` until a semantics-preserving candidate demonstrates the required
  relative improvement.
