# Caching roadmap

## Scope

Full data at any pipeline point is computed once, stored once, and reused by
every consumer: one snapshot store, one data-point resolver, one build service,
one analysis-result surface, and one execution rule under which every
full-data materialisation both reads and writes that store. Current behaviour is
specified in [caching](../caching/high-level.md), the
[IO layer](../io-layer/high-level.md), and the
[execution engine](../execution-engine/high-level.md). Explore consumers of
these packages are owned by the [Explore / EDA roadmap](explore-eda.md).

## Priorities

| Package | State | Priority | Outcome |
|---|---|---:|---|
| CACHE-S09 | Planned | P2 | Make previews and traces seed from and capture into shared snapshots, readable by runs. |
| CACHE-S08 | Deferred | P3 | Fold the API-input table cache into the shared snapshot store with per-table validity. |

## Planned improvements

`CACHE-S09` is next; `CACHE-S08` is deferred. A package must
not bypass the resolver, lease, signature, or capture contracts of an earlier
one. Every package builds on the node-output snapshot store (signature, slot
index, column widening, retention, cross-process leases, and the publication
rule) specified in the [IO layer](../io-layer/low-level.md#node-output-snapshots) and
the data-point resolver and leased reads specified in
[caching](../caching/low-level.md#data-points), the node-data build service, the data
profile job, and the request-time analysis helper specified in the
[server API](../server-api/low-level.md#node-data-builds), the analysis-result store
specified in [caching](../caching/low-level.md#analysis-results), and the shared frontend
data cache specified in
[frontend shared](../frontend-shared/low-level.md#the-shared-data-cache).

### CACHE-S09 — Previews and traces seed from and capture into shared snapshots

**Why:** A preview shows the first rows of a node's full-data output: the row
limit applies when each node is collected, not to sources. A preview below a
join or a materialising operation such as a group-by or sort therefore pays for
that upstream work over the full data, and pays again on every backend preview
cache miss, while nothing it computes is available to later previews, runs, or
editors.

**Plan:**

- **Admission.** A preview lineage is admitted to shared snapshots when a
  schema-only bounded preparation of its sources succeeds (every CSV it reads
  has declared dtypes and it reads no plain JSON). A lineage that is not
  admitted neither seeds nor captures and behaves as today. A preview's
  captures are written into the `bounded` class and a run may seed from them:
  a preview's materialisations are over the full data, because the preview row
  limit applies when each node is collected rather than to its sources, so what
  it captures is the same data a run would have computed.
- **Row order is not part of the snapshot contract.** The execution-profile
  semantics proof found the interactive preview does not reproduce a join's row
  order between runs, while the bounded sink does. Rows carry the meaning in
  this domain and their order does not, so a preview capture is seeded like any
  other generation and nothing is gated on which execution wrote it. The
  operations that read row position — the registry's `ORDER_DEPENDENT` class,
  such as `forward_fill`, `unique`, `first` and the `cum_*` family — are
  written against an order the pipeline itself established with a `sort`,
  which a seed cannot disturb; one written against an incidental order was
  already answering arbitrarily before any of this.
- **Seeding.** An admitted preview resolves a seed plan, as bounded executions do (upstream of
  or equal to the target, freshness, column coverage, ancestry agreement
  including recomputed branches). Seeded nodes produce their snapshot frame for
  downstream nodes; the row limit still applies only when each node is
  collected.
- **Capture.** An admitted preview captures, inline and before collecting
  downstream rows, the full output of each unseeded node on its path that is
  either a join (a node with more than one input, as the existing checkpoint
  rule defines it) or a materialisation boundary (a node whose code calls a
  frame operation the operation registry classifies as materialising, such as
  `group_by` or `sort`), with the preview's demand at that node, as an
  `automatic` generation under the node-output publication rule. Downstream
  nodes then read the preview's own artifact: the generation it published,
  or a request-owned temporary file when it did not publish. Other nodes are never
  captured, because the row limit already stops their reads early; a Model
  Score node under a row limit scores row-locally and is not a capture point.
  Column negotiation happens before planning, as in a bounded execution. A capture
  rejected for quota after automatic eviction, or not published under the
  publication rule, continues from the preview's own staged artifact as a
  request-owned temporary file without recomputing, and is reported as
  `snapshot_capture_skipped` in the preview diagnostics.
- The plan fingerprint joins the `preview_trace` consumer's
  `runtime_input_fingerprint`, so a preview cached for one seed generation is
  never served after that seed is replaced, widened, cleared, or first created.
- A preview that captured never stores its response under the key it computed
  before executing, because that key's plan did not contain its captures.
  After its captures publish, it resolves the seed plan a new request would now
  choose; when every generation in that plan is one the preview read, it
  stores the response under that plan's key, and otherwise it stores nothing.
- Every backend preview-cache hit re-validates the generations listed in the
  stored `seed_plan` by leasing each with `lease_generation` and confirming its
  identity is still current for the graph's signature. Only the expected
  states are cache misses: a generation that is retired or missing, or an
  identity that is no longer current. Those evict the entry and the preview
  executes again. A corrupt generation or any other storage or I/O failure
  propagates as the store's error, exactly as a direct read would, and is never
  hidden by recomputation. A hit therefore never returns a retired generation
  to the trace handoff.
- Leases are held until the preview or trace result is assembled; when the
  execution runs in an isolated worker, the resolver's parent-lease handoff
  applies.
- Input preparation before a preview (snapshot-backed Data Inputs, API-input
  JSON caches) runs only for sources the seeded execution still reads.
- The preview response carries `seed_plan`: every snapshot generation the
  preview's collected rows were computed from — the seeds it read and the
  generations it captured and then read — each with its point, node label,
  snapshot identity, generation id, columns, creation time, and whether it was
  seeded or captured; the list is empty when the preview read no snapshot.
  Request-owned temporary artifacts from skipped captures are not listed. The
  preview panel shows "Using cached data from" with the seeded node labels when
  any entry was seeded.
- A trace never resolves its own plan and never captures. The trace request
  carries the `seed_plan` of the preview it explains, and the trace uses
  exactly those generations, seeded and captured alike: an empty list means no
  seeding even if snapshots now exist. A preview that skipped a capture
  recomputes that node in its trace, as today. Before executing, the trace leases each named generation with
  `lease_generation` and checks that its identity's signature equals the
  signature the trace's graph produces for that point. If a generation has
  been retired or is missing, or its signature no longer matches, the trace
  returns HTTP 409 `preview_seed_plan_expired` and the user refreshes the
  preview; a corrupt generation or other storage failure propagates as the
  store's error.
- Trace correlation stops at each seeded input boundary: the seeded point is a
  trace step whose row comes from the snapshot. Only nodes that the execution
  skipped because of seeding are reported as trace omissions, with reason
  `snapshot_seed` naming the seed; a node that still executed for another
  branch stays traceable through that branch.
- Frontend preview results record the CACHE-S05 node-data epoch they were
  fetched under and are refetched when it changes; a refetch whose seeds did
  not change hits the backend preview cache. A preview's own captures
  increment the epoch after its response is applied, without refetching that
  preview.
- Trace validity in the frontend includes the preview's identity (its node,
  source, row limit, and `seed_plan`) and the node-data epoch. When either
  changes, the displayed trace and row highlight are hidden immediately, an
  in-flight trace request is aborted, and a late trace response for the
  previous identity is discarded.

**Acceptance:**

- Pipeline `policies + claims → join → banding`: the first banding preview
  captures `join` (the response lists the capture) and returns exactly the rows
  of an unadmitted preview; a second preview of a different node below `join`
  seeds from that capture, and neither source is scanned.
- A training run after that preview seeds from the preview's `join` capture.
- A preview through only a filter and a rename captures nothing.
- A preview of a lineage reading an undeclared-dtype CSV seeds and captures
  nothing and returns today's rows.
- Refreshing the `join` snapshot to a new generation makes the next banding
  preview miss its backend cache entry and return the new generation's rows.
- A stale `join` snapshot is not seeded; the next preview captures under the
  new signature.
- Limit boundary: a snapshot of a point holding `x = 0..99` in order seeds a
  preview of `filter(x >= 50)` with row limit 3, which returns `[50, 51, 52]`,
  and a preview of `sum(x)` with row limit 3, which returns `4950`.
- A trace from the seeded banding preview returns the preview's row, shows
  `join` as a step whose row comes from the snapshot, and reports `policies`
  and `claims` as `snapshot_seed` omissions naming `join`.
- First preview then trace: with `policies` sampled without a seed upstream of
  `join`, the first banding preview captures `join` and lists it in `seed_plan`;
  a trace of its first row returns the identical row, reads the captured
  generation, and scans neither source.
- Capture, then clear or evict: the first banding preview captures `join` as
  `J1`; a repeat preview hits the backend cache with `seed_plan` naming `J1`.
  After `J1` is cleared, or evicted under quota, the next preview does not
  return the cached response: it executes, captures `J2`, and its `seed_plan`
  names `J2`, never `J1`.
- Cache-hit failures: with a cached response whose `seed_plan` names `J1`,
  corrupting `J1`'s data file makes the next preview fail with the store's
  corrupt error, and an injected permission error on its metadata makes it fail
  with that error; in both cases the preview is not executed again.
- Diamond `A → B`, `A → C`, `B + C → D` with only `B` cached: a trace of `D`
  reports no omission for `A`, which stays traceable through `C`, and stops
  the `B` branch at the `B` snapshot step.
- Trace handoff: a snapshot published between an unseeded preview and its
  trace leaves the trace unseeded; a refresh between a seeded preview and its
  trace still traces the preview's generation while it is retained. A clear
  removes the slot's identities but does not revoke a generation another
  operation still leases: a trace after a clear while another job holds a lease
  on that generation still traces it; a trace after a clear with no remaining
  lease, or after a refresh or widening whose previous generation has been
  retired, returns 409 `preview_seed_plan_expired`; a graph edit that changes
  the seed's signature returns 409.
- Previews and traces seed and capture only within their own semantics class:
  the execution-profile semantics proof found the interactive preview does not
  promise the row order a bounded execution produces, so a preview neither
  reads nor writes a `bounded` generation.
- Frontend tests: a preview fetched before a snapshot publishes is refetched
  after the epoch increments; a preview's own capture does not refetch it; the
  panel lists the seeded node labels; a completed trace is hidden, and an
  in-flight trace is aborted and its late response discarded, when the snapshot
  it depends on is refreshed or cleared.

**Dependencies:** CACHE-S05; the seed plans bounded executions run under
([execution engine](../execution-engine/high-level.md)); the preview/trace lineage
key and trace omission contracts.

**Evidence:** `src/haute/executor.py`; `src/haute/execution.py`;
`src/haute/trace.py`; `src/haute/_execute_lazy.py`; `src/haute/projection.py`;
`src/haute/schemas.py`; `src/haute/_model_scorer.py`;
`frontend/src/hooks/usePipelineAPI.ts`;
`frontend/src/hooks/useTracing.ts`;
`frontend/src/stores/useNodeResultsStore.ts`;
`tests/test_runtime_input_cache_invalidation.py`;
`tests/test_trace_banding_lineage.py`;
`frontend/src/hooks/__tests__/useTracing.test.ts`.

### CACHE-S08 — One store for API-input tables

**Why:** API-input tables are cached per port by the JSON-shredding cache in its
own `working/` and `committed/` layers, a second durable store beside the
shared snapshot store, and that cache is built and validated for all of a
node's emitting tables together, so editing one table's schema makes the
node's other tables unusable until the next build.

**Plan:** Evaluate moving per-port table caches into the shared snapshot store
while preserving the committed layer used by deployment, and evaluate
per-table validity so an edit to one table leaves the node's other tables
current. A shred reads the whole source whatever the number of tables, so the
evaluation measures how much of a build's cost is table materialisation rather
than the source traversal.

**Activation trigger:** The data-point resolver serves API-input tables in
production use and a measured maintenance or disk cost from running two
durable stores.

**Acceptance:** A decision record, and if implemented, JSON-cache route, deploy
bundling, and data-point resolver tests pass against one store.

**Dependencies:** The caching data-point resolver.

**Evidence:** `src/haute/_json_shred/_cache.py`; `src/haute/routes/json_cache.py`.
