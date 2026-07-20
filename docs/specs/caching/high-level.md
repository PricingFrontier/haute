# Caching — High-Level Specification

## Purpose

Haute re-executes the same pipeline graph, or the same slice of it, many times in a
single editing session: a preview re-runs after a small config tweak, a trace
re-derives intermediate values the preview already computed, an optimiser setup
re-materializes an upstream dataframe the next solve step also needs, and a JSON
data source is re-shredded into columnar form every time the schema editor asks for
a preview. Recomputing all of this from scratch on every request would make the
editor feel unresponsive and would repeat genuinely expensive work (large lazy
Polars plans, JSON shredding, remote artifact loads).

The caching component exists to make this reuse safe and operationally controlled:
a cached result is served when its deterministic key still matches the inputs the key
models (graph structure, node configs, edge wiring, preamble, runtime file state,
execution policy). In-process caches have bounded entry counts (and optional byte
budgets); the JSON cache maintains one replaceable `working/` and `committed/`
snapshot per source path with explicit clear/mirror lifecycle, but has no
process-wide disk quota.

## Scope

In scope:
- Deterministic fingerprinting of pipeline graphs and their execution inputs, used
  as cache keys (content hashing, canonical JSON encoding, graph structural digests).
- A generic in-process bounded LRU cache with pinning, used as the shared
  foundation for the preview/trace fingerprint cache and the dataframe execution
  cache.
- A parquet-artifact-backed cache for materialized backend execution frames
  (dataframe execution cache), including on-disk lifecycle (write, scan, evict,
  delete) of the artifacts it owns.
- A single-flight, stat-gated process cache for loaded file artifacts (external
  objects, optimiser/mlflow artifacts) that reload only when file metadata changes.
- The on-disk JSON-to-parquet cache exposed over HTTP (`/api/json-cache/*`):
  building, checking status of, deleting per-port shredded parquet caches for
  JSON/JSONL data sources, and preserving the compatibility cancel endpoint.

Out of scope (owned elsewhere, linked where relevant):
- What actually gets executed when a cache misses — the lazy Polars pipeline
  construction and bounded-sink checkpointing belong to the
  [execution engine](../execution-engine/high-level.md), not this component.
  This component only decides *whether* execution is needed and stores what came
  out of it.
- The JSON shredding algorithm and its working/committed storage layout (how
  records become per-table parquet columns and where those files live) belong to
  [json-shredding](../json-shredding/high-level.md) (`_json_shred.py`,
  `_json_flatten.py`); this component's `routes/json_cache.py` is the HTTP surface
  and dispatch/aggregation logic around them, not the shredder.
- HTTP request/response schemas, routing conventions, and error-response shape
  conventions belong to [server-api](../server-api/high-level.md); this component
  only supplies the json-cache route handlers themselves.
- Trace- and preview-specific cache *consumers* (what slots they store, when they
  invalidate on user edits) belong to the execution engine's trace/preview
  modules; this component supplies the `FingerprintCache` and `LRUCache` classes
  they are built on.

## Behaviour

- **Fingerprints are deterministic and execution-sensitive, not mathematically
  injective.** Node config, node type, edge wiring (including frame/port handles),
  preamble text, and imported `utility` content hashes are included in the key
  material. Structurally identical graphs produce the same fingerprint regardless
  of node/edge insertion order, dict key order, or set member order. The result is
  a 64-bit xxh64 digest, so the implementation avoids known serialization
  ambiguities but cannot promise collision-free hashing. Utility content is also
  subject to the documented `(mtime_ns, size)` stat-gate limitation below.
- **Fingerprints are versioned.** Every fingerprint is prefixed with an algorithm
  version tag (`v<N>:`). Changing the canonicalisation rules bumps the version, so
  old cache entries can never be silently reinterpreted under new rules — they
  simply become unreachable (a fingerprint under the new version never matches a
  stored key under the old one).
- **Caches are bounded and evict least-recently-used entries first,** except for
  entries a caller has explicitly pinned (e.g. a preview result the trace is still
  reading) or that are inside a protected write window (a dataframe artifact
  between being stored and its first consumer opening a scan). Pinned/protected
  entries can push a cache temporarily over its nominal size; entry-count and
  byte-budget enforcement resume once the pin/window clears.
- **A cache entry that points at a missing or unreadable backing resource is
  treated as a miss, not an error**, and is evicted so the next lookup
  regenerates it. This applies to the dataframe execution cache's parquet
  artifacts.
- **File-derived cache entries invalidate on file metadata change.** The
  stat-gated cache and the fingerprint's utility-file hashing — the latter now
  itself a `StatGatedCache` instance, not a parallel implementation — both key on
  `(mtime_ns, size)`; a change either dimension reloads. A byte-identical rewrite
  that happens to preserve both `mtime_ns` and `size` is below this resolution and
  is accepted as a documented trade-off, not a defect.
- **The JSON cache has two on-disk layers**, `working/` (the volatile layer a
  build call populates) and `committed/` (the durable layer, promoted from
  `working/` by a save operation elsewhere in the system). Deleting the JSON cache
  through the API only clears `working/`; `committed/` is untouched.
- **JSON-cache builds cannot be cancelled.** `POST /api/json-cache/cancel` is a
  compatibility no-op: after validating the requested path it returns
  `cancelled=false`. The editor's cancel action calls this endpoint but does not
  interrupt or stop the worker thread, which continues until success, failure,
  or timeout.
- **Cache-miss failures are loud.** A build request that cannot resolve a schema,
  fails path validation, or hits corrupt on-disk state returns a structured 4xx/5xx
  error rather than silently falling back to an empty or partial result. See
  [Failure model](#failure-model).

## Design rationale

- **One canonical JSON encoder.** Early in the codebase's history, graph
  fingerprinting and dataframe-execution-policy fingerprinting used two subtly
  different JSON-canonicalisation routines. Divergent encoders are how silent
  cache collisions and phantom invalidations happen, so both call sites were
  unified onto the shared `canonical_json()` (`_cache.py`). Graph fingerprints and
  dataframe execution-cache policy/key payloads route through it; unrelated
  digests may use another deterministic encoding appropriate to their own contract.
- **Content hashing over mtime-only invalidation.** File-backed cache keys use
  xxh64 content hashes (`_hashing.py`), not just file metadata, specifically to be
  TOCTOU-safe: an edit that reuses a file's old size/mtime by coincidence cannot
  silently poison the cache in the places where content hashing is used directly.
  The `StatGatedCache` (`_stat_gated_cache.py`) is a deliberate exception — it
  trades that last increment of safety for avoiding a full re-read on every
  lookup. Utility-file hashing (`_cache.py`) makes this same trade by construction:
  it is a process-wide `StatGatedCache` instance, with `GraphFingerprintMemo`
  layered on top only to pin one digest per gate within a single request.
- **Structural fingerprint scoped to upstream lineage, not the whole graph.** The
  dataframe execution cache keys a node's materialization on that node's upstream
  subgraph only (`_upstream_subgraph`), so editing a downstream node never
  invalidates an upstream cache entry it cannot possibly affect. This trades a
  slightly more expensive key-construction step (building a filtered subgraph) for
  much better cache reuse across incremental edits.
- **Disk-backed, not in-memory, dataframe cache.** Materialized dataframes can be
  large. Keeping them as parquet artifacts on disk instead of in-process Python
  objects keeps process memory bounded and matches the rest of the lazy engine's
  checkpointing strategy, rather than inventing a second materialization
  strategy. The intended payoff is avoiding repeated work for large dataframes,
  not raw scan speed — the cache has no byte cap by default for this reason;
  operators can opt into one at process startup with
  `HAUTE_DATAFRAME_EXECUTION_CACHE_MAX_BYTES`. That value is parsed when
  `_dataframe_execution_cache.py` is imported; malformed/non-positive settings
  fail import with `RuntimeError` rather than changing a live cache later.
- **Rejected: reusing temporary checkpoint files directly as cache artifacts.**
  Lazy execution already writes temporary checkpoints to break large Polars
  plans, and reusing those files would save an extra sink in some cases. But
  checkpoint shape is driven by execution safety, projection, and plan
  structure, not a stable cache contract — tying cache identity to checkpoint
  internals would make the executor harder to reason about and risk stale or
  too-narrow reuse.
- **Rejected: an optimiser-specific reuse layer.** The pain point that motivated
  the dataframe execution cache first appeared in optimiser setup (an optimiser
  setup action re-materializing the same upstream dataframe the next solve step
  needs), but a route-specific cache would create a parallel execution path and
  repeat the same invalidation logic in every other backend caller. The cache
  instead lives in the execution engine and is consumed by optimiser, training,
  explore, sink, and deploy paths through shared `DataFrameExecutionCacheRequest`
  builders.
- **A shared LRU/pinning core.** `LRUCache` (`_lru_cache.py`) consolidates
  eviction and pinning logic that used to be duplicated across the
  preview/trace fingerprint cache and the dataframe execution cache.
  `FingerprintCache` is now a thin subclass adding multi-slot dict semantics;
  the dataframe execution cache subclasses `LRUCache` directly and layers
  parquet-artifact lifecycle management on top.
- **Pinning is a caller-driven overlay, not automatic reference counting** (except
  for the dataframe cache's live-scan tracking, which is reference counted because
  a `pl.LazyFrame` scan can outlive the call that created it). This keeps the base
  `LRUCache` contract simple: unpinned entries behave like a plain bounded LRU.
- **Store-window protection in the dataframe cache.** A freshly stored artifact is
  exempt from eviction until its first consumer has opened a scan (or the window
  is explicitly released), because otherwise a concurrent store under byte
  pressure could evict an artifact before anything ever reads it. This exemption
  affects victim *selection* only — the entry still counts toward the entry-count
  budget, so unrelated entry-count eviction is not distorted by the window.

## Interactions

- Depends on [execution-engine](../execution-engine/high-level.md) for the
  `PipelineGraph`/`GraphNode` types that `_cache.py` fingerprints, for the
  bounded-sink materialization that `_dataframe_execution_cache.py` calls into on
  a cache miss, and for `ExecutionProfile` used to key the dataframe cache.
- Depends on [json-shredding](../json-shredding/high-level.md)
  (`_json_shred.py`, `_json_flatten.py`, `_api_input_schema.py`)
  for the actual build/status/inference logic that `routes/json_cache.py`
  dispatches to and aggregates responses from.
- Depended on by [server-api](../server-api/high-level.md): `routes/json_cache.py`
  is mounted as a FastAPI router (`/api/json-cache`) alongside the rest of the
  HTTP surface.
- Depended on by the preview and trace subsystems (execution engine), which use
  `graph_fingerprint()` and `FingerprintCache` to avoid recomputing preview
  DataFrames and trace values on unrelated edits.
- Depended on by optimiser, training, sink, and deploy backend callers (execution
  engine), which build `DataFrameExecutionCacheRequest` objects to opt individual
  nodes into materialized-frame reuse.

## Failure model

- **Unsupported fingerprint input types fail loudly.** `canonical_json()` raises
  `TypeError` on non-JSON-shaped values (bytes, complex numbers, non-string
  mapping keys, arbitrary iterables other than list/tuple/set/frozenset) rather
  than falling back to `repr()`. A drift in config shape is caught at
  fingerprint time, not silently hashed into a meaningless digest.
- **A torn read during file hashing raises, it does not retry silently forever.**
  `_utility_file_hash` (`_cache.py`) delegates to `StatGatedCache.get_or_load`
  (`_stat_gated_cache.py`), which retries once against a file whose stat metadata
  moved during the load, then raises `RuntimeError` if the second attempt is also
  torn — this is deliberately a hard failure, not an infinite retry loop.
- **OS-level errors from hashing propagate unchanged.** `content_hash`/
  `content_hash_bytes` (`_hashing.py`) do not catch `FileNotFoundError`,
  `PermissionError`, or other `OSError` subclasses; callers see the real error.
- **`StatGatedCache` never caches a loader's exception or a torn-read result.**
  If the loader raises, nothing is cached, so the next call retries against
  the (possibly now-fixed) file rather than serving a poisoned entry.
- **A cached dataframe artifact that has gone missing or become unreadable is
  evicted and treated as a miss**, logged as a warning
  (`dataframe_execution_cache_invalid_entry_evicted`); it does not raise back to
  the caller of `.get()`/`.scan()`, but `materialize_lazy_frame_with_cache`
  re-raises if the *freshly written* artifact fails the same validation, because
  that indicates a real write failure, not stale state.
  > NOTE: `store_artifact` raising `CacheArtifactTooLargeError` after a
  > successful sink is a legitimate operational outcome (the artifact was
  > written, then rejected and unlinked for being over budget), not a bug —
  > `materialize_lazy_frame_with_cache` propagates it to its immediate caller;
  > `_execute_lazy` deliberately catches it, logs
  > `dataframe_execution_cache_artifact_too_large_skip`, and continues with the
  > already-built lazy frame uncached.
- **The JSON cache routes fail loud on schema and data problems**, in a fixed
  precedence documented in `build_json_cache`'s docstring: path validation (400/403)
  before missing schema source (422 `ApiInputSchemaError`) before schema
  validation failure (422) before missing data file (404) before an unhandled
  internal error (500 with a sanitised detail message). A corrupt on-disk config
  file is distinguished from an absent one — corruption raises
  `ApiInputSchemaError` rather than being silently treated as "no schema yet,
  go use the editor," because collapsing the two would hide a real write bug
  behind a misleading migration prompt.
  > NOTE: the read-only `GET /api/json-cache/status` poll is the one place this
  > precedence is relaxed on purpose — a corrupt config or invalid schema there
  > is reported as `cached=False` rather than a 422, because a GET poll has no
  > user action to react to and the precise error is available on the
  > build/POST-status paths.

## Known limitations

- **Cold misses on the dataframe execution cache re-materialize the terminal
  cached frame, then continue downstream from a parquet scan.** This is robust
  but can duplicate sink work for callers whose target node is itself the
  terminal node — a future improvement could let those callers consume the
  freshly written artifact directly, if that can be done without creating a
  second execution path.
- **The dataframe execution cache has no global byte budget by default** —
  it retains at most 16 entries by default, but each parquet artifact can be
  arbitrarily large. Key changes create a distinct entry; old entries disappear
  only through replacement, explicit clear, or LRU eviction, not immediately on
  invalidation. If a real deployment needs a byte quota, the intended fix
  is explicit operator configuration (see `HAUTE_DATAFRAME_EXECUTION_CACHE_MAX_BYTES`
  above), not silently skipping the cache.
- **The JSON cache has no cross-project/global disk quota.** Each source path's
  working and committed snapshots are replaced atomically and can be deleted or
  propagated by the explicit lifecycle, but the component does not cap the aggregate
  size or number of cached source paths under `.haute_cache/`.
- **Narrow projected cache requests do not masquerade as broader reusable
  artifacts.** A cache request scoped to specific `required_columns` (e.g. an
  auto-range warm-up that only needs the columns a later solve step requires)
  only ever warms that projection — it intentionally will not satisfy a later
  request for a wider column set, even though both requests share the same
  upstream lineage.
