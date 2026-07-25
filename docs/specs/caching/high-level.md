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
  injective.** Execution-relevant node labels/config/type, edge endpoints and
  frame/port handles, preamble text, pipeline source location, and imported
  `utility` content hashes are included in key material. Presentation-only edge
  IDs and canvas metadata are excluded. Structurally identical graphs produce
  the same fingerprint regardless of node/edge insertion order, dict key order,
  or set member order. The result is a 64-bit xxh64 digest, so the
  implementation avoids known serialization ambiguities but cannot promise
  collision-free hashing. Utility content is also subject to the documented
  `(mtime_ns, size)` stat-gate limitation below.
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

## Polars backend contracts (0.6.0)

The [caching roadmap](../../roadmap/caching.md) has no open package following
the checked-identity audit and evidence-gated performance pass.

- **Preview and trace identity is lineage-scoped.** A preview or trace result is
  identified from a deterministic canonical payload for the requested target's
  execution-relevant upstream lineage, not from unrelated nodes elsewhere in the
  graph. The payload contains the ordered relevant nodes and execution wiring
  plus graph-level `preamble` and `source_file`; display metadata,
  `preserved_blocks`, available `sources`, and `active_source` are excluded.
  The selected execution source is an explicit key field. Downstream and
  disconnected state is excluded, so edits outside the payload preserve reuse
  while any execution-affecting change within it invalidates reuse.
- **There is one mandatory normalised key factory.** Preview and trace callers
  pass the original graph, the source-pruned result of
  `prepare_graph(graph, target, source)`, and explicit values for target,
  source/active source, requested columns, initial width, row limit, port/frame
  handle, contract fingerprint, selected live-switch path, runtime/input
  fingerprint, and execution-semantics version. The factory owns normalisation
  and canonicalisation; callers cannot omit a dimension or reconstruct an
  equivalent key ad hoc.
- **Invalidation is exact and fail-loud.** The factory includes every input that
  can change the materialised result, and excludes unrelated graph state. An
  unsupported identity input is an error, not a best-effort key. Cache invalidation
  must not rely on broad whole-graph clearing as a substitute for key correctness.
- **Store admission is side-effect-free until accepted.** Oversize is assessed
  before cache mutation. Rejection retains any old entry under the same key and
  performs no eviction, lease acquisition, or pin. An accepted store commits
  atomically under the cache lock, so readers observe either the complete old
  value or the complete new value; among concurrent accepted stores for one key,
  the last completed store wins and entry/byte accounting remains coherent.
- **Only retained values can be protected.** A request lease or pin may be
  acquired only after the store outcome confirms retention, and is always
  released in `finally`. Oversize rejection is an explicit operational outcome;
  unexpected store errors propagate rather than being translated into a miss or
  a best-effort uncached path.
- **Runtime input fingerprints share the stat gate.** Runtime path fingerprints
  reuse the established `StatGatedCache` discipline for content-hash loading;
  independent stat-gated hash implementations are not introduced. The stat gate
  may avoid rereading unchanged bytes, but it is not a validity shortcut for
  JSON: a JSON-derived result still requires the content/semantic checks its own
  operation needs and may not be accepted merely because `(mtime_ns, size)` match.
- **Hash and serialisation work remains canonical.** Cache-key JSON uses the one
  shared canonical encoder. Dataframe row hashes use the benchmark-approved,
  versioned little-endian UInt64 buffer; equal frames retain equal identities and
  the representation tag deliberately cold-starts keys created by the obsolete
  decimal-list encoding.
- **Cleanup is contractual, not cosmetic.** Retire unreachable collection
  strategies, dead sink helpers, no-op projection guards, and duplicate
  canonical-JSON paths once their callers have migrated. Removal must leave one
  discoverable implementation for each surviving responsibility.

Non-goals: this change does not promise a global disk quota, cache persistence
across algorithm versions, approximate/stale preview reuse, or a stat-only
cross-operation assertion that JSON-derived work is valid. It also does not
replace the dataframe execution cache's distinct artifact lifecycle.

Required tests cover: lineage-local reuse and invalidation for both preview and
trace; independent mutation of every key-factory argument and every canonical
lineage payload field; presentation-only stability; invariance to node, edge,
map, and set insertion ordering; exclusion of downstream/disconnected edits;
oversize rejection preserving an old same-key value without eviction or
protection; atomic concurrent replacement and coherent byte/entry accounting;
lease release on success and exceptions; loud unexpected store failures;
stat-gated runtime hash reuse and changed-file invalidation; a regression proving
unchanged file metadata alone cannot validate JSON work for a different
operation; cleanup imports/callers; and benchmark-gated semantic identity plus
version-tag tests for the direct row-hash buffer.

## Approved change contract — 0.7.0 shared input snapshots

Remaining source-cache improvement work is tracked in the
[caching roadmap](../../roadmap/caching.md).
This is the cache portion of the approved
[data I/O convergence contract](../io-layer/high-level.md#approved-change-contract-070-data-io-convergence).

- Caching gains one provider-neutral, single-table source-snapshot service used by
  `dataInput` file, database, lakehouse, and Databricks providers. It owns build/refresh,
  status/progress, clear, identity, metadata, disk layout, concurrency, atomic publication,
  validation, quota, and garbage collection. Provider components own acquisition and browsing;
  the execution engine only opens a validated published snapshot.
- A source identity is deterministic over the safe canonical provider configuration: provider,
  normalised locator/table/path, complete query, format, source-affecting arguments, schema
  declarations, and the name of the connection/secret reference. Resolved credentials,
  post-input Polars code, graph layout, preview limits, and downstream projection are excluded.
  Identity is content-addressed under `.haute_cache/inputs/`; two distinct queries against the
  same table can never share a generation.
- A generation consists of Parquet data and signed metadata containing schema, row/column
  counts, bytes, timestamps, identity digest, builder boundedness, and any provider revision or
  freshness token. A build is serialized per identity, uses a unique staging generation, and
  publishes only after artifact and metadata validation. Concurrent readers retain a coherent
  old generation or open the coherent new generation; they never observe an in-place rewrite.
- Refresh is explicit. Pipeline preview, batch, CI, and deploy never initiate remote I/O.
  Failure, timeout, cancellation, inconsistent database snapshot, retry ambiguity, disk-full,
  or schema mismatch preserves the last complete generation. A missing generation or one whose
  identity/signature does not match fails loudly.
- Readiness and external freshness are separate. `ready` means the local signed generation can
  be read. Freshness is `fresh`, `stale`, or `unknown`; it is `unknown` when a provider cannot
  cheaply prove a revision. A fetch timestamp alone never means fresh. Changed local-file
  signatures mark the generation stale; remote providers may supply snapshot/version tokens.
- Builders declare `bounded`, `admitted_eager`, or `unsupported`. The cache service enforces that
  declaration before acquisition. A Parquet result does not retroactively make an eager
  full-memory import bounded. Database and Databricks builders must stream bounded batches;
  lakehouse builders use bounded lazy scan/sink; eager-only local formats require memory
  admission or remain unsupported for the requested profile.
- The existing API-input JSON shredding cache and dataframe execution cache remain separate:
  the former is a multi-frame structural codec and the latter caches graph computation, while a
  source snapshot is a single-table external-boundary artifact.
- Cache metadata, logs, routes, diagnostics, and paths never contain resolved credentials.
  Project-wide byte quota and generation-count limits are explicit settings. Eviction never
  removes a leased generation; clear/eviction marks then removes only after readers release it.

Acceptance tests cover identity canonicalisation and query separation, secret exclusion,
boundedness admission, atomic refresh with readers on both sides of publication, same-identity
single flight, different-identity concurrency, cancellation and fault preservation, signed
artifact corruption, local-file invalidation, remote freshness-unknown reporting, leased
eviction, quota accounting, and direct-versus-snapshot schema/data equivalence.

## Checked fingerprint completeness

- Every maintained cache-key factory declares one checked input contract. The
  contract names its exact payload fields and classifies the shared logical
  input classes: node configuration, upstream lineage, edge wiring and handles,
  user code, source selection, row-limit semantics, runtime files, artifacts,
  request shape, and execution policy. A logical class is either consumed
  through named payload fields or excluded with a non-empty rationale.
- Checked payload construction rejects missing and unknown fields before
  hashing. Cache-specific namespaces and schema versions remain explicit, so a
  contract change causes a deliberate cold cache rather than reinterpreting an
  old key.
- The canonical node-config registry classifies every recognised config field
  as execution-affecting or excluded with a rationale. Adding a recognised
  field without updating that registry fails reflective coverage. Unknown
  legacy fields remain conservatively execution-affecting at runtime; they are
  never silently omitted.
- Execution graph identity includes node labels when they affect generated
  names or frame binding, relevant node type/config/user code, upstream order,
  full edge endpoints and handles, preamble/utility identity, and the pipeline
  source location used for relative resolution. React-Flow position/type,
  descriptions, edge IDs, pipeline display metadata, preserved source blocks,
  and the UI's available/active-source metadata are presentation-only. The
  selected execution source remains an explicit key input.
- Runtime file and artifact identities use the shared `StatGatedCache`
  discipline underneath the logical signature. Preview, trace, dataframe
  execution, and deploy-schema keys consume the same structured runtime
  identity; a direct input or bundled artifact change invalidates immediately
  at the documented stat-gate resolution.
- Deploy output-schema identity additionally includes the selected input/output
  nodes, the fixed one-row inference policy, and resolved bundled artifacts.
  Model-contract and input-snapshot caches keep their specialised hot-key/hash
  representations, but construct them from the same checked completeness
  contract so their deliberately excluded input classes remain reviewable.

Changing the graph-identity or consumer payload schema bumps the corresponding
algorithm version. Compatibility is cache-cold only: persisted source
generations and user data are not migrated or deleted.

The performance gate measures row-hash conversion, bounded LRU lookup/update,
stat-gated unchanged/changed lookup, and canonical lineage-signature
serialisation independently. Each benchmark records its workload, environment,
generated performance artifact, comparison threshold, and
implement/no-change decision. A micro-optimisation is accepted only when its
median improvement is at least 20% on the representative workload and semantic
identity/concurrency tests remain unchanged; otherwise the required outcome is
a recorded no-change decision. Cross-request graph memoisation is not accepted
unless it is bounded, contains no frame/plan references, is project- and
algorithm-version-scoped, and preserves immediate lineage invalidation.

The accepted row-hash path hashes a versioned, canonical little-endian UInt64
buffer instead of decimalising each Polars row hash. Configured-small LRU/stat
operations and cross-request lineage memoisation remain no-change decisions:
the former has no material semantics-preserving comparator, while the latter
has no demonstrated saving after paying the identity work needed to reject
changed graph and runtime inputs.
