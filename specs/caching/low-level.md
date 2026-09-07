# Caching — Low-Level Specification

## Module map

| File | Responsibility |
|---|---|
| `src/haute/_hashing.py` | Deterministic xxh64 byte/file hashing primitives. |
| `src/haute/_cache.py` | Canonical JSON, checked cache-input/config contracts, graph/preamble fingerprints, lineage-key factory, and utility-file hash memo/cache. |
| `src/haute/_lru_cache.py` | Thread-safe entry/TTL/byte bounded LRU with pinning. |
| `src/haute/_dataframe_execution_cache.py` | Dataframe cache key, Parquet artifact LRU, materialization, validation, scan pins, and cleanup. |
| `src/haute/_stat_gated_cache.py` | Bounded LRU, per-key single-flight cache gated by backing-file metadata. |
| `src/haute/routes/json_cache.py` | Structured API-input (JSON/JSONL/NDJSON/XML) cache infer/build/progress/status/delete HTTP surface. |
| `src/haute/_source_cache.py` | Cross-component dependency owned by [io-layer](../io-layer/low-level.md); IO-layer-owned source snapshot store consumed for canonical cache identity and immutable generations. |

The shared `_source_cache.py` relationship is recorded in `specs/ownership.toml`; IO
layer is primary and caching is a consumer.

## Key types and data structures

- `CacheConsumerContract` and `CheckedCacheInputs` define exact, versioned key fields.
- `CACHE_CONFIG_FIELD_CLASSIFICATIONS` classifies every recognised node config field as
  execution input or rationale-bearing presentation exclusion.
- `GraphFingerprintMemo` pins utility-file hashes consistently within one request while a
  process-wide `StatGatedCache` serves unchanged files across requests.
- `LineageCacheKeyRequest` carries graph, target node/port, upstream lineage, prepared
  runtime switch state, and utility-file evidence for `lineage_cache_key()`.
- `LRUCache` stores values in an `OrderedDict` with timestamps, optional sizes, and pins.
- `StatGatedCache` stores `(mtime_ns, size, value)` in an `OrderedDict`, plus participant-
  counted per-key load gates. The default maximum is 256 entries.
- `DataFrameExecutionCacheKey`, `DataFrameExecutionCacheEntry`, and
  `DataFrameExecutionCacheRequest` define artifact identity and validated metadata.
- `DataFrameExecutionCache` extends `LRUCache` with materialization locks, store-window pins,
  scan refcounts, and artifact unlinking.

### Checked cache-input inventory

Every maintained consumer has one closed payload shape and version. Construction
requires exactly the listed fields, preserves contract order, and rejects both
missing and unknown names before hashing:

| Consumer | Version | Complete field set |
|---|---:|---|
| `graph_structure` | 2 | `nodes`, `edges` |
| `graph_execution` | 8 | `base_fingerprint`, `preamble_fingerprint`, `source_file`, `extra_keys` |
| `preview_trace` | 3 | `preamble`, `source_file`, `nodes`, `edges`, `target_node_id`, `source`, `requested_columns`, `initial_column_limit`, `row_limit`, `port_label`, `contract_fingerprint`, `selected_live_switch_path`, `runtime_input_fingerprint`, `execution_semantics_version` |
| `dataframe_execution` | 2 | `namespace`, `node_id`, `lineage_fingerprint`, `source`, `profile`, `input_fingerprint`, `required_columns`, `extra_keys`, `execution_policy` |
| `runtime_graph_input` | 3 | `source`, `sources`, `json_cache_signature`, `preamble_fingerprint`, `extra` |
| `deploy_schema` | 1 | `graph_fingerprint`, `runtime_input_fingerprint`, `artifact_fingerprint`, `output_node_id`, `input_node_ids`, `source`, `row_limit`, `execution_policy` |
| `model_contract` | 1 | `feature_names`, `categorical_features`, `offset_column` |
| `input_snapshot` | 1 | `schema_version`, `provider`, `descriptor` |

The repeated records inside those payloads are separately closed and versioned:
`graph_node` v1 is `id`, `label`, `nodeType`, `config`; `graph_edge` v1 is
`source`, `sourceHandle`, `target`, `targetHandle`; `runtime_input_entry` v1
is `node_id`, `node_type`, `config`, `files`; and
`live_switch_selection` v1 is `switch_id`, `incoming_edges`.

Each consumer also classifies all ten logical input classes — node config,
upstream lineage, edge wiring, user code, source selection, row limit,
runtime files, artifacts, request shape, and execution policy — as either
consumed by named payload fields or deliberately excluded with a non-empty
rationale. A payload field that is not assigned to a class, or an input class
that is neither consumed nor explained, makes contract construction fail.

## Control flow

### Graph and lineage identity

1. Callers classify inputs through `checked_cache_inputs()`.
2. `graph_fingerprint()` canonicalises execution-relevant graph/config/code/utility-file
   evidence and applies `ALGO_VERSION`.
3. `lineage_cache_key()` builds the shared preview/trace key from its request, including
   selected live-switch paths and upstream lineage.
4. Executor and trace use the factory; graph fingerprint alone is not their cache key.

Utility-file hashes use a request memo in front of a process-wide `StatGatedCache`.
Execution's runtime-path fingerprint cache is a separate `StatGatedCache` instance owned by
execution, so it shares the primitive's bounds and single-flight discipline without moving
the call-site policy into this component.

### Stat-gated loading

1. Stat the case-preserved resolved path and form `(mtime_ns, size)`.
2. Return/move-to-MRU when the keyed entry matches.
3. Otherwise join the per-key load gate and recheck after acquiring it.
4. Load, restat, and cache only if the gate remained stable.
5. Retry one moving gate; then raise.
6. Evict LRU entries above `max_entries` and remove idle load gates.

The real runtime consumers are utility-file hashing (`src/haute/_cache.py`), runtime-path
fingerprints (`src/haute/execution.py`), deploy scorer models
(`src/haute/deploy/_scorer.py`), and modelling feature contracts
(`src/haute/modelling/_feature_contract.py`).

### Dataframe execution cache

1. `dataframe_execution_cache_key()` validates checked logical inputs and hashes them.
2. `materialize_lazy_frame_with_cache()` takes the same-key materialization lock.
3. `scan()` handles an ordinary hit, validates the stored artifact, and pins its scan.
4. On miss, `bounded_sink()` writes Parquet and `read_parquet_metadata()` validates it.
5. `store_artifact()` rejects an oversized new artifact before removing an old same-key
   entry; otherwise it replaces and admits the new entry.
6. `scan_stored_entry()` opens exactly the stored entry without repeating ordinary-hit
   corruption validation and creates the first scan pin.
7. LRU eviction/clear unlinks only unpinned artifacts; orphaned replaced paths unlink after
   their final scan release.

The artifact root is a process-local temporary directory created by
`src/haute/execution.py` and removed at interpreter exit. Startup housekeeping deliberately
does not reap it; execution is the lifecycle owner.

### Structured API-input cache

1. Path containment is checked.
2. The source extension is routed through the structured decoder
   (`.json`, `.jsonl`, `.ndjson`, or `.xml`), and volatile or persisted schema is
   selected and validated.
3. Missing schema returns 422; only then does a missing data file return 404.
4. The library path shreds in-process with a response timeout and process-local
   progress; the HTTP route delegates to a one-shot child process that prepares the
   staging directory while the parent, holding the cross-process build lock, owns
   validation, cancellation, publication, and cleanup (the owning contract is the
   server-api JSON cache build transaction).
5. Successful builds mark the working cache consulted so save-time promotion can occur.
6. Status validates the same schema and storage metadata; delete removes the
   `working/` layer only and leaves `committed/` intact.

There is no separate GUI cancel endpoint; the build is cancelled cooperatively
by request cancellation through the isolated-worker cancellation gate, which
stops the worker and discards staging.

### Source snapshots

`SourceCacheIdentity` uses `checked_cache_inputs(CacheConsumer.INPUT_SNAPSHOT, ...)`.
Generation layout, integrity, publication, quota, lease, and concurrency rules are owned
and tested by the [IO layer](../io-layer/low-level.md).

## Edge cases and invariants

- **Namespace and dataframe-cache identity share one snapshot.** An operation that
  materialises dataframe-cache artifacts compiles its preamble under the dependency
  fingerprint it pinned at admission; cache entries written by that operation therefore
  describe the same helper contents the namespace was compiled from, and a helper edit
  changes both for the next operation.
- `canonical_json()` is the sole encoder for JSON-shaped transient digest and
  cache-key material. The one deliberate exception is the persisted modelling
  feature-contract hash: its historical compact sort-keyed JSON plus SHA-256
  encoding remains byte-stable so previously published `contract_hash` values
  continue to verify.
- Every logical cache input is present exactly once; unknown fields fail.
- LRU oversized rejection retains a previous same-key entry.
- Stat-gated caches never exceed `max_entries` after a completed insertion.
- Loader failure never stores a value or strands an idle load gate.
- `scan_stored_entry()` requires object identity with the current stored entry.
- Live scans prevent artifact unlink; store-window pins protect store-to-first-consume.
- Dataframe byte accounting matches retained entries and excludes scan-orphaned paths.
- JSON build/status use the same v2 schema validation.
- Structured-input **status resolves `working/` then `committed/`** — the same
  order `load_v2_api_source` uses at run time, because the badge answers "will a
  run read from cache?". A valid `working/` wins since that is what the next run
  reads, and `cached=False` requires both layers to be invalid.
- Dataframe cache artifacts are not part of persistent startup reaping.

## Error handling

Contract/key errors are `ValueError`/`TypeError` at construction. `StatGatedCache` propagates
stat and loader exceptions and raises `RuntimeError` after two moving gates.

`CacheArtifactMissingError` and `CacheArtifactCorruptError` cause ordinary-hit eviction.
`CacheArtifactTooLargeError` rejects the new artifact while retaining any previous same-key
entry. `DataFrameExecutionCacheError` reports impossible identity/store-window states.

Structured-input cache routes preserve structured schema/parse/path errors, return 409
when the source changed during build or the worker stopped, return 507 on
memory-limit exhaustion, unsupported caps, or admission rejection, return 504 on
response timeout, and log unexpected errors before a generic 500.

### Boundary failure ordering

1. Checked input construction validates the consumer enum, mapping shape,
   exact field set, nested-record shape, and logical-class completeness before
   canonical JSON or hashing. A caller cannot produce a best-effort key with an
   omitted or extra dimension.
2. Stat-gated loading stats before lookup, joins the per-key single-flight gate,
   rechecks after acquiring it, loads, and restats before insertion. A loader
   failure caches nothing; one moving gate retries and a second raises. Eviction
   and idle-gate cleanup happen only after a stable insertion.
3. A dataframe ordinary hit validates the Parquet artifact before it creates a
   scan pin; missing/corrupt artifacts are evicted and reported as misses.
   Materialisation validates the new artifact, checks oversize before removing a
   same-key entry, stores it under a store-window pin, and first-consumes that
   exact object without a second corruption pass. Replacement/clear may detach
   an artifact, but unlink waits for the final scan pin.
4. Structured-input cache routes perform path containment, then select/validate schema,
   then check the data file, then start blocking shred work. Consequently
   missing schema is 422 even when the data path is absent; file absence is
   404 only after schema succeeds; response timeout is 504 without being
   presented as cooperative cancellation.
5. Input-snapshot identity is checked here before storage selection; pointer,
   lease, integrity, staging, quota, refresh, and clear failure order remains
   owned by [io-layer](../io-layer/low-level.md#boundary-failure-ordering).

### Depth-review questions

The operational review checks that this specification answers: What is the
complete field set and schema version for every maintained consumer and nested
record? How is every logical input class consumed or deliberately excluded?
When are cache values admitted, pinned, detached, and unlinked? In what order do
contract, stat/load, artifact, JSON-route, and source-snapshot failures surface?
The checked-input inventory, control-flow narratives, invariants, and boundary
ordering above are the answers and must be updated together when a consumer or
cache lifecycle changes.

## Testing

- `tests/test_runtime_input_cache_invalidation.py` — preview/trace cache keys invalidate on runtime file/artifact edits or disappearance, preserve stat-gate semantics, and share file signatures across preview/trace.

- `tests/test_cache_identity_contract.py`, `tests/test_cache_fingerprint_injectivity.py`,
  `tests/test_caching_correctness.py`, `tests/test_cache_unification.py`,
  `tests/test_graph_fingerprint_cached.py`, and `tests/test_hashing.py` cover canonical
  encoding, injectivity, field completeness, versioning, live switches, utility files,
  memoisation, and shared primitive behaviour.
- `tests/test_lru_cache.py` covers entry/byte/TTL eviction, pins, oversized retention, and
  concurrency.
- `tests/test_stat_gated_cache.py` covers hit/reload, LRU bounds, single flight, moving
  gates, exceptions, clear, and load-gate reclamation.
- `tests/test_dataframe_execution_cache.py` covers identity, artifact lifecycle,
  corruption, first consume, oversized replacement retention, pinning, and concurrency.
- `tests/test_cache_materialize_guard.py` guards `_execute_lazy._lazy_frame_for_cache()`,
  the input boundary feeding materialization.
- `tests/test_json_cache_routes.py`, `tests/test_json_cache_integrity.py`,
  `tests/test_json_cache_corrupt_and_errors.py`, and `tests/test_json_cache_mut_witnesses.py`
  cover schema precedence, progress, build/status, promotion, corruption, path errors, and
  deletion.
- `tests/performance/test_cache_identity_perf.py` records bounded LRU/stat-gate and lineage
  key performance evidence.
