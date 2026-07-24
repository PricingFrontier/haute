# Caching roadmap

## Scope

Owns generic cache-key composition, invalidation, eviction, artifact lifetime,
and concurrency at shared cache boundaries. JSON source-cache semantics remain
with [I/O](io-layer.md); deploy and tracing consume the shared identity
contracts. Current behaviour is specified in
[caching](../specs/caching/high-level.md).

## Priorities

| Package | State | Priority | Outcome |
|---|---|---|---|
| `AUD-CACHE-01` | Reverify | P1 | Replace ad-hoc cache-key audits with one checked fingerprint-completeness contract. |
| `CACHE-PERF-01` | Decision | P2 | Measure deferred hashing, LRU/stat, and graph-fingerprint optimisations before changing cache semantics. |

## Planned improvements

### AUD-CACHE-01 — Checked fingerprint completeness

**Why:** Output-affecting dimensions are currently assembled at several cache
call sites. Individual regression tests cover known failures, but a new config
field, edge identity, runtime file, or artifact class can still be omitted
without a central gate.

**Plan:**

- Define one structured node/runtime input signature that enumerates config,
  upstream fingerprints, edge handles, user code, source selection, row-limit
  semantics, and every file/artifact identity that can affect output.
- Make cache consumers compose their keys from that structure instead of
  rebuilding overlapping tuples.
- Add a registry describing which config and runtime input classes each cache
  consumes; require an explicit exclusion rationale for non-output fields.
- Keep stat-gated hashing and request-local memoisation as performance details
  beneath the complete logical identity.

**Acceptance:**

- Reflective coverage fails when a new config/runtime input field has no key
  classification.
- A mutation matrix changes the relevant key for every output-affecting
  dimension and keeps it stable for presentation-only changes.
- Separator, ordering, `None`, set/NaN, edge-handle, source, row-limit, and
  artifact-path collision probes are injective.
- Preview, trace, deploy-schema, model-contract, and input-cache tests use the
  shared contract without weakening immediate invalidation.

**Dependencies:** I/O owns source identity and generation semantics; deploy
owns artifact resolution; tracing owns trace-specific target/row dimensions.

**Evidence:** `src/haute/_cache.py`, `src/haute/execution.py`,
`src/haute/_fingerprint_cache.py`, `tests/test_cache_fingerprint_injectivity.py`,
`tests/test_runtime_input_cache_invalidation.py`,
`tests/test_caching_correctness.py`, and
`tests/test_graph_fingerprint_cached.py`.

### CACHE-PERF-01 — Evidence-gated cache optimisation

**Why:** Direct row-hash buffers, LRU/stat-cache micro-optimisations, and
cross-request whole-graph fingerprint memoisation were deliberately deferred.
Configured bounds are small, and memoising at the wrong scope could weaken
lineage-local invalidation for little measurable benefit.

**Plan:**

- Benchmark representative live and large-frame identity workloads after the
  complete logical identity in `AUD-CACHE-01` is fixed.
- Separately measure row-hash conversion, bounded LRU/stat operations, and
  canonical lineage-payload serialisation.
- Implement only a material bottleneck, preserving canonical bytes, versioning,
  exact invalidation, thread safety, and configured bounds.
- Record a no-change decision as the expected outcome when evidence is weak.

**Acceptance:**

- Each gate records workload, environment, artifact, threshold, and
  implement/no-change decision.
- Any accepted optimisation has semantic identity and concurrency regressions
  plus before/after evidence.
- A cross-request memo cannot retain frames/plans, cross project or algorithm
  versions, or turn a changed lineage input into a hit.

**Dependencies:** `AUD-CACHE-01`; execution owns target-lineage preparation.

**Evidence:** `src/haute/_cache.py`, `src/haute/_lru_cache.py`,
`src/haute/_fingerprint_cache.py`, `src/haute/execution.py`,
`tests/test_graph_fingerprint_cached.py`,
`tests/test_cache_fingerprint_injectivity.py`, and
`tests/performance/`.
