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
  schema-only bounded preparation of its sources succeeds. Data Inputs always
  pass, because they execute from a Parquet scan or from their prepared
  snapshot, and so do structured (JSON, NDJSON, XML) API Inputs, which read
  their Parquet cache or shred the file directly exactly as a bounded run
  does; an API Input reading a flat file passes when a schema-only bounded
  read of that file succeeds, so a CSV it reads must declare its dtypes. A
  lineage that is not admitted neither seeds nor captures and behaves as
  today. A preview's
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
  JSON caches) runs only for sources the seeded execution still reads. A
  node's signature already signs each snapshot-backed input's generation
  pointer and current source, so the backend resolves the plan first, then
  prepares the Data Inputs it executes, and resolves again if a pointer moved.
  The browser, which builds snapshots and API-input caches before a preview so
  it can show their progress, asks the backend which inputs the seeded
  execution reads and ensures only those.
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
  recomputes that node in its trace, as today; so does a listed generation
  that lacks columns the trace reads there (a capture written for a
  column-projected preview), together with every listed seed built from it. Before executing, the trace leases each named generation with
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

**Low-level contract.** What the steps implement, stated before the code exists so tests can
be written against it; each step folds its part into the component low-level specs in the
present tense, and this contract is deleted with the package.

- **Semantics.** `PREVIEW_SHARES_BOUNDED_SEMANTICS` in `_node_snapshots.py` is `True`:
  `PREVIEW_EAGER` reads the `bounded` class and writes it when `preview_admitted`. The seed
  planner accepts a `PREVIEW_EAGER` request, which only an admitted lineage builds.
- **Admission.** `_seed_plans.preview_lineage_admitted(graph, target_node_id, *, source) -> bool`
  decides per source node of the target's lineage, before any preparation: a Data Input is
  admitted (a direct Parquet scan, or a prepared snapshot); a structured API Input (JSON, NDJSON,
  XML) is admitted (its per-port Parquet cache or a direct shred, the same frames either way); a
  flat-file API Input is admitted when `read_data_source({..., "sourceType": "flat_file"},
  profile=LAZY_SINK)` followed by `collect_schema()` succeeds, which for a CSV reads its header.
  `BoundedMemoryUnsupportedError` from that probe means not admitted; any other failure of the
  probe also means not admitted and is left to the preview's own read, which reports it at
  the node as today.
- **Preview capture rule.** For a `PREVIEW_EAGER` request, `_Resolver.capture_points` makes an
  executed, non-pass-through `node_output` node a capture point when it has more than one
  effective parent (`STRUCTURAL`) or calls a materialising operation (`MATERIALISING`). Being
  consumed, fanning out, feeding a join, or being a Model Score does not by itself make a node a
  capture point; a consumed target, fan-out, or join feeder that is itself a join or
  materialisation is captured. A preview may seed its own target.
- **Preview preparation.** For a `PREVIEW_EAGER` request, `open_seed_plan` resolves against the
  inputs as they stand, prepares the snapshot-backed Data Inputs among
  `decision.executed_node_ids`, and resolves again when preparation moved a pointer, repeating
  while a resolution executes a Data Input not yet prepared, at most three rounds; after them it
  prepares every Data Input of the target's lineage and resolves once more. It never builds a
  structured API-input cache. `preview_input_node_ids(graph, target_node_id, *, source,
  required_columns_by_node)` runs admission and the first resolution without preparing or leasing
  and returns the snapshot-backed Data Inputs and structured API Inputs that resolution executes,
  or, for an unadmitted lineage, every such input of the target's lineage.
- **Listed plans.** `open_listed_seed_plan(request, entries, *, store)` builds the trace's plan
  from the preview's `seed_plan`. For every entry, in order, it checks that the identity's
  signature equals the signature the request's graph produces for that point and leases the
  generation; a missing or retired generation or a signature mismatch raises
  `SeedPlanExpiredError` (`preview_seed_plan_expired`, HTTP 409), and corruption and other storage
  failures propagate. It then seeds each listed point whose generation covers the request's
  demand there and drops the rest, and applies ancestry agreement to a fixed point: a listed seed
  whose recorded dependencies name a point the execution now runs is dropped. The plan captures
  nothing.
- **Eager engine.** `_execute_lazy._execute_eager_core(..., snapshot_plan=None)` runs under a plan
  through the same `_PlannedCaptures` as the lazy engine: nodes above a seed are neither built nor
  run; a seeded node's frame is `SeedPlan.seed_frame`; each capture point is sunk through
  `bounded_sink` before anything downstream is collected, and consumers read the publication or,
  on quota rejection or supersession, the request-owned artifact (`snapshot_capture_skipped`);
  the row limit applies only at collection; store errors propagate past `swallow_errors`.
- **Preview execution.** `executor.execute_graph(..., shared_snapshots=False)`; the preview route
  passes `True`. With it and an admitted lineage, execution computes the lineage runtime-input
  identity once, opens the plan, and keys the preview cache by that identity hashed with
  `extra={"seed_plan": seed_plan_fingerprint(seeds)}` when it seeds anything (a preview
  seeding nothing is keyed like one without a plan). A cache hit leases every generation its
  entry lists and confirms each identity is current for the graph's signature at that point; a
  missing or retired generation or a non-current identity evicts the entry and executes, and every
  other store error propagates. A plan without captures stores under its key. A plan with captures
  never stores under its pre-execution key: after execution it recomputes the runtime-input
  identity and stores nothing if it moved, then resolves the plan a new request would choose and
  stores under the key built from the pre-execution identity and that plan's fingerprint only when
  every generation in that plan is one the execution read. A partial cache hit under a plan with
  captures executes as a miss.
- **Workers.** The preview route issues a staging token per request and passes it to the
  interactive worker, whose plan stages captures under it; the route calls
  `discard_node_output_staging(token)` after the worker returns, fails, times out, or is
  superseded.
- **Schemas.** `PreviewSeedPlanEntry`: `node_id: str`, `port_label: str | None` (always `null`),
  `node_label: str`, `identity_digest: str`, `generation_id: str`, `columns: list[str] | None`
  (`null` means all), `created_at: str` (ISO-8601 UTC), `kind: "seeded" | "captured"`.
  `PreviewNodeResponse.seed_plan: list[PreviewSeedPlanEntry]` lists, in topological order, every
  generation the collected rows were computed from: every seed the plan read and every capture
  published and then read, never a request-owned artifact. `TraceSeedPlanEntry`: `node_id`,
  `port_label`, `identity_digest`, `generation_id`; `TraceRequest.seed_plan:
  list[TraceSeedPlanEntry]` is required and may be empty. `POST /api/pipeline/preview/inputs`
  takes `PreviewInputsRequest` (`graph`, `node_id`, `source`, `requested_preview_columns`,
  `port_label`) and returns `PreviewInputsResponse` (`input_node_ids: list[str]`).
- **Trace.** `trace.execute_trace(..., seed_plan)` opens a listed plan and executes under it; its
  cache key carries the fingerprint of the points it seeds. Correlation stops at a seeded point,
  whose uncapped plan is its seed's scan. Each node the execution skipped because of seeding is an
  omission with reason `snapshot_seed`, linked to a correlation diagnostic with code and reason
  `snapshot_seed`, severity `info`, and `seed_node_ids` naming the seeds it was skipped through;
  column-relevance pruning applies as to every omission.
- **Frontend.** `useNodeResultsStore.setPreview` records the node-data epoch a request was sent
  under and a stored preview matches its request context only at that epoch. A response with
  `captured` entries increments the epoch after it is applied; the stored preview is re-stamped
  with the new epoch only when the epoch still equals its request epoch. Preview call sites ask
  `preview/inputs` which inputs to ensure. `useTracing` sends the displayed preview's `seed_plan`
  and includes it and the epoch in its semantic context.

**Testing.**

- Planner: preview capture points are joins and materialisations only, including a join or
  group-by target and a join feeding a join; a preview may seed its target; an API Input over an
  undeclared-dtype CSV is not admitted while a Data Input over one is; a failing probe leaves
  the lineage unadmitted without raising; a preview prepares only the inputs its execution reads; a stale input drops the
  seeds below it before preparation; a moved pointer re-resolves; exhausted rounds prepare the
  whole lineage once; a listed plan leases exactly its generations, expires on a retired
  generation or changed signature, propagates corruption, skips a generation that does not cover
  its demand, and drops a seed built from a point it recomputes.
- Eager engine: a seeded node builds nothing above it; a capture under a row limit is the full
  output, including a join below a limited Model Score; the limit-boundary example below; a
  quota-rejected capture continues from its own artifact; a filter and rename capture nothing; a
  capture store error propagates instead of becoming a node error.
- Preview: every acceptance bullet below; an input change after a capture stores nothing; an
  input change between the re-check and storage keys the entry by the executed identity, so the
  next preview misses it; a post-capture plan naming a generation the preview did not read stores
  nothing; a partial hit under captures executes as a miss; a killed preview worker leaves no
  capture staging; `preview/inputs` lists only the inputs the seeded execution reads, and an
  unused unavailable input is neither built nor fails the preview.
- Trace: every trace acceptance bullet below; a trace of a column-projected capture executes that
  point; a trace reuses the preview entry stored under the plan it seeds.

**Acceptance:**

- Pipeline `policies + claims → join → banding`: the first banding preview
  captures `join` (the response lists the capture) and returns exactly the rows
  of an unadmitted preview; a second preview of a different node below `join`
  seeds from that capture, and neither source is scanned.
- A training run after that preview seeds from the preview's `join` capture.
- A preview through only a filter and a rename captures nothing.
- A preview of a lineage in which an API Input reads an undeclared-dtype CSV
  seeds and captures nothing and returns today's rows; a Data Input reading
  the same CSV is admitted, because it executes from its snapshot.
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
- An admitted preview reads and writes `bounded` generations, which runs read
  too; a preview whose lineage is not admitted neither reads nor writes any.
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
