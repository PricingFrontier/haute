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
| CACHE-S07 | Planned | P1 | Make every bounded execution seed from and capture into shared snapshots, replacing private caches and temporary checkpoints. |
| CACHE-S09 | Decision | P2 | Previews cannot join the `bounded` class; choose how they share materialisations, if at all. |
| CACHE-S08 | Deferred | P3 | Fold the API-input table cache into the shared snapshot store with per-table validity. |

## Planned improvements

Delivery order is `CACHE-S07`; `CACHE-S09` needs a decision first. A later package must
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

### CACHE-S07 — Executions seed from and capture into shared snapshots

**Why:** Training preparation, training evaluation preview, optimiser setup
(including auto-range), and Data Output runs recompute upstream work that an
earlier run, preview, or explicit build already materialised. Each of them
also writes the full output of every join, fan-out, and join feeder to a
temporary Parquet checkpoint that is deleted when the run ends, and keeps its
target materialisation in a private process-local namespace. The same data is
therefore produced and written again on every run.

**Plan:**

- **Seeding.** A seed plan is resolved once per execution request, before any
  cache key is built, by walking upstream from the target. A candidate is a
  `node_output` point upstream of or equal to the target (for an explicit
  snapshot build, strictly upstream of the point being built) whose read class
  matches the request profile, whose generation for the current signature is
  fresh, and whose columns contain the execution's demand at that node. On each
  path the walk stops at the first candidate, so the plan holds only the
  most-downstream seeds and never consults points upstream of a seed. A
  repeated run whose target output is already captured therefore reads it
  without executing any upstream node.
- **Column negotiation before planning.** Seeds and capture columns are
  resolved together, before projection planning:
  1. Walk for seeds using the execution's own demand at each node.
  2. The capture points are the capture-rule nodes the execution would still
     recompute below those seeds. Each capture point's demand becomes the
     execution's demand there merged with the columns of that identity's
     latest generation, fresh or stale by its dependencies, if any, using
     the same merge the existing
     dataframe-cache demand path applies before planning.
  3. Propagate those negotiated demands upstream through the projection
     planner to every seed and source.
  4. Drop every seed whose columns do not contain its propagated demand, and
     continue the walk upstream of it with the propagated demand.
  5. Repeat from step 2 until no seed is dropped; then apply ancestry
     agreement. Resolution terminates because seeds only move upstream.
  Source and edge projection therefore keep every column a widened generation
  needs, every seed can supply them, and one execution publishes the complete
  union.
- Ancestry agreement is checked against the whole resulting execution, not
  only between seeds: for every dependency identity `X` recorded by a chosen
  seed, every consumer in the execution that reads `X`'s node data, whether
  through another seed or through recomputed nodes, must read the recorded
  generation. If `X`'s current generation equals the recorded one and covers
  those consumers' demand, `X` is added as a seed for the recomputed consumers.
  Otherwise (`X` cleared, replaced, widened, not covering the demand, or two
  seeds recording different generations of `X`'s slot), every seed recording
  `X` is dropped and the plan is resolved again without those seeds; the walk
  then continues upstream of them. Resolution terminates because a dropped seed
  is never re-added, and an added `X` seed carries only dependencies already in
  the recorded closure.
- The plan leases its generations; it is a context manager held by the caller
  for the whole job, including final collection, and passed into lazy
  execution. Lazy execution substitutes `scan().select(demand)` for a seeded
  node's output through the existing cached-seed path and does not build nodes
  needed only by seeded nodes.
- **Capture.** Every materialisation the execution performs over full data is
  written to the shared store instead of a temporary file or a private cache:
  the nodes the existing checkpoint rule selects (joins, fan-outs, join
  feeders), the nodes whose code calls a frame operation the operation registry
  classifies as materialising (such as `group_by` and `sort`), batch Model
  Score nodes (whose scored Parquet file becomes the staged artifact instead of
  a private temporary file), and the target the caller materialises. Each
  capture writes the negotiated planning demand at that node under the
  node-output publication rule as an `automatic` generation. It writes it
  through the bounded sink, never from collected streaming batches: the
  execution-profile semantics proof found that batch collection does not
  reproduce a join's row order between runs while the sink does, so a
  generation published from batches would hold an order the next execution
  need not produce. It then continues
  from its own artifact (a lease on the generation it published, or a
  request-owned temporary file when it did not publish), exactly where the
  temporary checkpoint scan is used today. Its `dependencies` are the closure of
  the seeds and captures scanned upstream of that node in this execution.
- The temporary checkpoint directory and the private dataframe-cache namespaces
  `training_prep`, `training_evaluation_preview`, `optimiser_setup`, and
  `data_output` are removed. Deploy scoring keeps its process-local cache,
  because deployed scorers have no project snapshot store.
- When a capture's publication is rejected for quota after automatic eviction,
  or the node-output publication rule does not publish it, the execution
  continues from its
  own completed staged artifact, now a request-owned temporary file, without
  recomputing the node, and records `snapshot_capture_skipped` with reason
  `quota` or `snapshot_capture_superseded` in its execution metrics and
  warnings. Any other store failure fails the execution.
- The plan fingerprint (`seed identity → generation_id`, seeds only) is added
  to every `dataframe_graph_input_fingerprint` and lineage key the request
  builds, so a seed replaced by a new generation changes every downstream key.
- Seeding and capture are enabled for training preparation, training evaluation
  preview, optimiser setup (including auto-range), Data Output runs, and
  non-refresh explicit builds (which capture intermediate nodes as `automatic`).
  They are disabled for deploy scoring. A refresh disables seeding but still
  captures.
- A spawned worker receives the plan as `(identity, generation_id)` pairs and
  uses `lease_generation`; the parent keeps its seed leases until the worker
  exits. The worker captures directly through the store's cross-process
  publication lock and holds its own lease markers on what it publishes.
- Execution metrics report `shared_snapshot_seeds` and `shared_snapshot_captures`
  per node.

**Acceptance:**

- A second training preparation run on an unchanged graph seeds from the
  captures of the first (metrics report seeds, no source scan, no capture) and
  produces a frame equal to the first run's.
- Pipeline `source → batch Model Score → modelling`: the second training run
  seeds the captured target output and makes zero scoring calls.
- An optimiser setup run after a training run on the same upstream seeds from
  the training run's captured join instead of recomputing it.
- Disjoint demand: with a join generation holding `[a, b]`, a run needing only
  `[c]` plans with `[a, b, c]` at the join and publishes one generation holding
  `[a, b, c]`; no second execution is needed to widen it.
- Disjoint demand with an upstream narrow snapshot: additionally, a snapshot of
  the join's input holds only the columns `[c]` needs. The run drops it as a
  seed (it cannot supply the columns behind `a` and `b`), reads the sources,
  and publishes the join with `[a, b, c]`; the input snapshot, being a capture
  point in that execution, is widened in the same run.
- Paused-run diamond: run 1 leases `A1` and seeds it for both branches `B` and
  `C`. While run 1 is paused before publishing `B`, run 2 refreshes `A` to `A2`
  and publishes `B2` recorded against `A2`. Run 1 then neither reuses `B2` nor
  replaces it, continues from its own `B1` artifact, and both of its branches
  read `A1`; run 1's `D` rows all find their matches.
- Paused-run diamond without an `A` snapshot: `A` is a Data Input whose
  post-load code adds a random scalar column and is not captured. Run 1
  computes `A` once for both branches and pauses before publishing `B`; run 2
  computes its own `A`, then publishes `B2`. Run 1 neither reuses nor replaces
  `B2`, continues from its own `B1`, and every `D` row in run 1 carries run 1's
  scalar on both branches.
- No temporary checkpoint directory is created and no private namespace entry
  is written by a run with store headroom. With the quota full of pinned or
  leased generations, a join over a sampled input is executed once, the run
  continues from the retained staged artifact with exactly those rows, and the
  run reports `snapshot_capture_skipped`.
- Two concurrent training runs in separate worker processes on the same
  upstream publish each captured identity once.
- A stale snapshot is never seeded.
- Pipeline `A → B → training`: cache `A`, then cache `B` (which seeds from `A`
  and records it); both stay `current`, and repeated training preparation runs
  seed only `B`. Caching `A` only after `B` also leaves `B` `current`. Clearing
  `A` leaves `B` `current`. Refreshing `A` to a new generation makes `B`
  `stale`; the next training run seeds `A`'s new generation and misses its
  previous downstream keys.
- Pipeline `A → B → C → training`: cache `A`, `B`, and `C` in order, then
  refresh `A` with an unchanged graph. Both `B` and `C` report `stale`, and the
  next training run seeds only `A`'s new generation.
- Diamond `A → B`, `A → C`, `B + C → D → training`: cache `A`, then `B`;
  refresh `A`; cache `C`; clear `A`. `B` and `C` are both `current` but record
  different `A` generations, so the training run seeds neither and recomputes
  from sources.
- Single cached branch of the same diamond, with `A` producing an unseeded
  random sample: cache `A`, then `B`; leave `C` uncached. The training run seeds
  `B` and also seeds `A`'s recorded generation for `C`, so `D` joins rows from
  one sample. After clearing `A`, the run drops `B` as a seed (its recorded `A`
  generation cannot serve `C`) and recomputes both branches from sources; every
  `D` row finds its match.
- A paused seeded worker survives refresh and clear of its seed (the resolver's
  paused-reader contract).

**Dependencies:** the node-data build service; the current checkpoint rule,
dataframe-cache seed path, and runtime graph-input fingerprint contracts.

**Evidence:** `src/haute/_execute_lazy.py`; `src/haute/execution.py`;
`src/haute/_dataframe_execution_cache.py`; `src/haute/projection.py`;
`src/haute/routes/_training_preparation.py`;
`src/haute/routes/_training_lifecycle.py`;
`src/haute/routes/_optimiser_service.py`; `src/haute/executor.py`;
`src/haute/_model_scorer.py`; `src/haute/_json_shred/_publication.py`;
`tests/test_dataframe_execution_cache.py`.

### CACHE-S09 — Previews and traces seed from and capture into shared snapshots

**Why:** A preview shows the first rows of a node's full-data output: the row
limit applies when each node is collected, not to sources. A preview below a
join or a materialising operation such as a group-by or sort therefore pays for
that upstream work over the full data, and pays again on every backend preview
cache miss, while nothing it computes is available to later previews, runs, or
editors.

**Plan:**

- **Admission — decided against, and this package now turns on the choice that
  replaces it.** This plan was written to admit a preview lineage when the
  snapshot class mappings let `PREVIEW_EAGER` read and write `bounded`. The
  execution-profile semantics proof decided they never will: the interactive
  preview reorders a join's rows between runs, so a generation it published
  would not be the data a bounded execution produces, and a generation it read
  would carry an order it never promised. What remains is a product choice, and
  nothing below should be built until it is made:
  - **A preview class of its own.** Preview lineages seed from and capture into
    `preview` generations that only previews read. Previews stop repeating each
    other's work, which is most of the value here, but a run never reuses a
    preview's work and the acceptance below loses its training case.
  - **Make the preview reproducible.** Give the interactive path the ordering
    the sink has, then admit it to `bounded`. This buys the whole package, at
    the cost of constraining the preview engine's execution.
  - **Drop the package.** Previews keep paying for their own upstream work.
  Every bullet below assumes a lineage that has been admitted somehow, and the
  seed, capture, freshness and expiry rules hold under either of the first two
  choices; only which class the generations carry differs.
- **Seeding.** An admitted preview resolves a CACHE-S07 seed plan (upstream of
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
  Column negotiation happens before planning, as in CACHE-S07. A capture
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
- A training run after that preview seeds from the preview's `join` capture
  **only under the second choice above**; under a preview-only class it does
  not, and the run recomputes `join` itself.
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

**Dependencies:** CACHE-S05, CACHE-S07; the preview/trace lineage
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
