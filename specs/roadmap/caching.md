# Caching roadmap

## Scope

Full data at any pipeline point is computed once, stored once, and reused by
every consumer: one snapshot store, one data-point resolver, one build service,
one analysis-result surface, and one execution rule under which every
full-data materialisation both reads and writes that store. Current behaviour is
specified in [caching](../caching/high-level.md), the
[IO layer](../io-layer/high-level.md), and the
[execution engine](../execution-engine/high-level.md). Explore and rating
consumers of these packages are owned by the [Explore / EDA roadmap](explore-eda.md)
and the [rating roadmap](rating.md).

## Priorities

| Package | State | Priority | Outcome |
|---|---|---:|---|
| CACHE-S02 | Planned | P1 | Resolve any consumer and column demand to a leased, current frame for its data point. |
| CACHE-S03 | Planned | P2 | Build, join, refresh, and clear pinned full-width snapshots through one job service. |
| CACHE-S04 | Planned | P2 | Persist analysis results by data version and run the data profile as an isolated job. |
| CACHE-S05 | Planned | P2 | Give every consumer one frontend data-cache hook and button. |
| CACHE-S06 | Planned | P1 | Prove which execution profiles and projections produce identical node outputs. |
| CACHE-S07 | Planned | P1 | Make every bounded execution seed from and capture into shared snapshots, replacing private caches and temporary checkpoints. |
| CACHE-S09 | Planned | P2 | Make previews and traces seed from and capture into shared snapshots. |
| CACHE-S08 | Deferred | P3 | Fold the API-input table cache into the shared snapshot store with per-table validity. |

## Planned improvements

Delivery order is `CACHE-S02` → `CACHE-S03` → `CACHE-S04` →
`CACHE-S05`, then the consumer packages `EDA-C01` and `RAT-B01` → `RAT-B02` →
`RAT-B03`, then `CACHE-S06` → `CACHE-S07` → `CACHE-S09`. A later package must
not bypass the resolver, lease, signature, or capture contracts of an earlier
one. Every package builds on the node-output snapshot store (signature, slot
index, column widening, retention, cross-process leases, and the publication
rule) specified in the [IO layer](../io-layer/low-level.md#node-output-snapshots).

### CACHE-S02 — Data-point resolver and leased reads

**Why:** A consumer must be able to ask for the data at a pipeline point
without knowing whether that point is a derived node output, a snapshot-backed
Data Input, an API-input table, or a direct Parquet file, and must never read a
stale, unpinned, or column-incomplete file.

**Plan:**

- A data point is `(producer_node_id, port_label | None)`. A consumer registry
  maps consumer nodes to points and column demands:
  - Banding: exactly one incoming edge; the point is `(edge.source,
    edge.sourceHandle)` when the producer is an `apiInput`, else `(edge.source,
    None)`; the demand is the banded columns. Rating Step: the same point rule;
    the demand is the raw factor columns. Zero or several incoming edges fail
    with `node_data_point_invalid` (HTTP 400).
  - Explore with blank `code`: its single incoming edge's point, because
    `_explore_fn` returns its input unchanged. Explore with non-blank `code`:
    `(explore_node_id, None)`. The demand is `all`.
  - Any other node: its own output (used by CACHE-S07 and CACHE-S09) with the
    execution's projected demand.
- Each point has one kind, resolved under the requested `source` with the
  executor's source selection:

  | Kind | Producer | State | Data version | Build |
  |---|---|---|---|---|
  | `data_input` | Data Input with blank post-load code | direct Parquet: always `current`; snapshot-backed: IO-layer status, where `ready`+`fresh` is `current`, `ready`+`stale` is `stale`, otherwise `missing`, `building`, or `corrupt` | source version (direct: resolved path, size, `mtime_ns`; snapshot: generation id) plus the Data Input node's lineage fingerprint | none (direct) or existing input-cache job |
  | `api_input_table` | `apiInput` port | JSON table cache validity for the full schema, `working/` then `committed/` | serving layer's metadata digest, port label, and the node's lineage fingerprint | existing JSON-cache build |
  | `node_output` | any other producer, including a Data Input with non-blank post-load code | node-output slot state for the current signature; a fresh generation that does not cover the demand is `partial` | generation id | CACHE-S03 job |

- A source kind is re-executed for each read, so it is limited to outputs that
  are a deterministic function of their versioned source: column selection and
  renames, and an `apiInput` port (whose builder runs no post-load code).
  Arbitrary post-load code can sample or depend on time, so a Data Input with
  post-load code is a `node_output` point and every consumer reads one
  generation.
- An `apiInput` point's build is the existing JSON-cache build, which caches
  every emitting table of the node in one pass (the shred walks the source
  once whatever the number of tables), and its validity covers the node's full
  schema: editing one table's schema makes every table of that node `stale`.
  Independent per-table builds and validity are evaluated in CACHE-S08.
- Source kinds never return the raw file. Their frame is the lazy execution of
  that single source node, so Data Input selected columns and renames, and an
  `apiInput` port's frame selection, apply exactly as in a run.
  The execution uses `prepare_inputs=False`, so it never builds or refreshes an
  input snapshot, and the API-input loader runs in a cache-only mode that raises
  `cache_required` instead of shredding JSON when no cache layer can serve the
  schema. Source data that is already Parquet or already cached is therefore
  never copied into a node-output snapshot.
- `lease_point_frame(graph, point, source, columns)` is a context manager
  yielding `kind`, `data_version`, `columns`, and a `scan` projected to the
  demand. It raises the typed `cache_required` failure, carrying the state,
  unless the point is `current` for that demand. `node_output` and
  snapshot-backed `data_input` points lease their generation; `api_input_table`
  points use the JSON cache's existing private file-backed snapshot of each
  opened Parquet file.
- Lease lifetime is caller-owned and covers the whole operation including the
  final collect or sink. A spawned child never leases a current pointer: the
  parent leases, passes `(identity, generation_id)`, holds the lease until the
  child terminates, and the child calls `lease_generation`.

**Acceptance:**

- Explore with blank code and Banding on the same parent resolve equal points;
  Explore with code resolves to itself; a Banding node with two incoming edges
  returns `node_data_point_invalid`.
- A generation captured by a training run holding the banded column makes the
  Banding editor's point `current` with no explicit build; Explore on the same
  parent reports `partial` until a full-width build.
- Two consumers on different ports of one `apiInput` resolve different points
  and each scan returns only its own table.
- A direct-Parquet `data_input` point is `current` with no build, and its data
  version changes when the file is rewritten.
- A Data Input with blank post-load code whose config selects and renames
  columns resolves to a `data_input` point yielding exactly the frame a run
  produces for that node.
- A Data Input whose post-load code filters rows, derives a column, and draws
  an unseeded random sample resolves to a `node_output` point; after one build,
  two different consumers read identical rows (same filter, derived column, and
  sample) until the point is refreshed; editing the code makes it `stale`.
- A `missing`, `stale`, `partial`, `building`, or `corrupt` point raises
  `cache_required` with that state; reading a missing snapshot-backed
  `data_input` point starts no snapshot build; an `api_input_table` point whose
  cache cannot serve the schema raises `cache_required` and performs no JSON
  shredding.
- Paused-reader test: a spawned child reading a parent-leased generation keeps
  reading while the parent refreshes and then clears the slot; the generation
  directory survives until the child exits and the parent releases its lease.

**Dependencies:** The IO-layer node-output snapshot store, canonical Data Input, and snapshot
lease contracts; the JSON-shredding cache contract.

**Evidence:** `src/haute/_input_providers.py`; `src/haute/_source_cache.py`;
`src/haute/_json_shred/_cache.py`; `src/haute/_execute_lazy.py`;
`src/haute/_builders.py`; `tests/test_source_cache.py`.

### CACHE-S03 — Explicit snapshot jobs and API

**Why:** Explore's job lifecycle (isolated worker, memory budget, latest-wins
replacement, cancellation, typed failure envelope) is the right lifecycle for
an explicit full-width build, but it is bound to Explore requests and Explore
identity.

**Plan:**

- A node-data service generalises the Explore service: `point(graph, node_id,
  source)` reports the resolved point, kind, state for the consumer's demand,
  data version, row count, columns, size, and retention; `run(graph, node_id,
  source, refresh)` starts or joins a build; `status(job_id)`,
  `cancel(job_id)`, and `clear(graph, node_id, source)` complete the surface.
  Requests name the consumer node; the service resolves the point and demand.
- Routes live under `/api/node-data`. For snapshot-backed `data_input` and
  `api_input_table` points, `point` reports state and `run` returns
  `delegated` with the existing build endpoint to use; a direct-Parquet
  `data_input` point has nothing to build; only `node_output` points build
  here.
- An explicit build writes `all` columns and pins the slot. A `run` whose
  identity equals a running job's returns that job id (`joined`). A `run` for
  the same slot with a different signature cancels the running job and starts
  a new one. A non-refresh `run` on a point that is `current` for `all` pins it
  and returns `completed` with `cached=true`.
- Refresh is separate from ordinary building. A non-refresh `run` on a
  `missing`, `stale`, `partial`, or `corrupt` point builds the current
  signature's identity and never returns another identity's generation.
  `refresh=true` rebuilds the current identity even when it is `current` and
  disables seeding for the whole build lineage, so a refresh always recomputes
  from sources. A non-refresh build may seed only from points strictly upstream
  of the point being built; a `stale` point is never seeded.
- The build runs in the existing isolated worker under `NODE_SNAPSHOT`,
  calls `SourceCacheStore.build` with `defer_retirement` and the parent's
  leased generation ids as retained ids, and the parent retires unleased
  generations after publication, as input-snapshot builds already do.
- `clear` removes every identity of the slot and its pin, except generations
  still leased, which retire when released.
- Delete `/api/explore/run`, `/api/explore/cache-status`,
  `/api/explore/status/{job_id}`, and `/api/explore/cancel/{job_id}`.

**Acceptance:**

- One job serves Explore and Banding on the same parent: the second `run`
  returns `joined` with the first job id, then `completed`/`cached=true`.
- After an upstream edit, `run(refresh=false)` on the now-stale point publishes
  a generation under the new signature, and the point reports `current`.
- A different signature for the same slot cancels the running job, whose
  terminal state is the existing replacement state.
- Refresh test: a producer whose code draws a fresh random sample on every
  run is cached, then refreshed with an unchanged graph; the refresh executes
  (execution metrics report zero shared-snapshot seeds and a source scan) and
  publishes a new generation with different rows.
- A `partial` point (an automatic generation holding some columns) becomes
  `current` for `all` after `run` and is pinned.
- Memory admission failure, contract errors, and cancellation surface with the
  existing Explore worker failure envelope and terminal reasons.
- `clear` removes every identity of the slot; a later `point` reports `missing`.

**Dependencies:** CACHE-S02; the background-jobs worker isolation
and job lifecycle contracts.

**Evidence:** `src/haute/routes/_explore_service.py`;
`src/haute/routes/explore.py`; `src/haute/routes/input_cache.py`;
`src/haute/_worker_isolation.py`; `tests/test_explore_routes.py`;
`tests/test_input_cache_route.py`.

### CACHE-S04 — Analysis results and the data profile job

**Why:** Explore computes its data profile (per-column statistics and overview
summary) inside the materialisation worker and stores it inside its private
generation. Shared points need analyses that are independent of how the data
was produced, keyed by the exact data they read, and computed under the same
resource controls.

**Plan:**

- An analysis-result store under `.haute_cache/analyses` keeps one atomic,
  schema-validated JSON document per
  `(point identity digest, data_version, analysis kind, analysis version)`.
  A document whose data version is no longer the point's current version is
  never returned and is removed on read; `clear` of a point removes its
  documents.
- The `profile` analysis is the Explore frame-statistics computation, run as an
  isolated-worker job with the existing admission, memory budget,
  cancellation, and failure envelope. The parent leases the point for the
  job's lifetime (CACHE-S02). Its result schema is the current column
  statistics plus overview summary, with no Explore node identity in it.
- Synchronous analyses (banding statistics, rating levels, pivot members) run
  under an admitted execution context with `cancellable_streaming_collect`, as
  pivot members already do: admission and memory-limit failures return HTTP 507
  with the execution error payload; an interrupted request cancels its context.
  Their in-process result LRU is keyed by `(point identity digest,
  data_version, request digest)`.

**Acceptance:**

- A refreshed or widened point never returns the previous generation's profile
  or statistics.
- Profile job admission failure, memory-limit failure, and cancellation produce
  the existing typed terminal states; the analysis store is unchanged after
  each.
- A profile for a direct-Parquet `data_input` point becomes unavailable after
  the file is rewritten or the Data Input's selection or renames change.
- A corrupt analysis document is discarded and recomputed, never returned.

**Dependencies:** CACHE-S02, CACHE-S03; the current Explore frame-statistics
contract.

**Evidence:** `src/haute/routes/_explore_service.py`;
`src/haute/routes/_pivot_service.py`; `src/haute/_execution_admission.py`;
`tests/test_explore_routes.py`; `tests/test_explore_pivot_routes.py`.

### CACHE-S05 — Shared frontend data-cache hook and button

**Why:** Explore's cache state, job polling, document execution fence, and
cache button live inside `ExplorePreview.tsx` and an Explore-only store slice,
so Banding and Rating cannot reuse them and would not see a job Explore
started on the same point.

**Plan:**

- A `useNodeDataCache(consumerNode)` hook calls `/api/node-data/point` and
  exposes `point`, `kind`, `state`, `progress`, `rowCount`, `sizeBytes`,
  `retention`, `dataVersion`, `run`, `refresh`, `cancel`, and `clear`. For
  snapshot-backed `data_input` and `api_input_table` points it delegates to the
  existing input-snapshot and JSON-cache orchestration; a direct-Parquet
  `data_input` point shows no build action.
- A store slice keyed by slot (`producerNodeId|portLabel|source`) holds what
  every consumer of the point shares: the running job and its progress, and the
  current generation's id, column set, row count, size, and retention. Each
  consumer derives its own availability (`current`, `partial`, `stale`,
  `missing`) from that shared entry and its own column demand, so a narrow
  generation can be `current` for Banding and `partial` for Explore at the same
  time. The slice keeps a node-data epoch that increments whenever a generation
  is published, widened, evicted, or cleared, as reported by `point` responses
  and job completions.
- The identity hash that decides when to re-ask the backend generalises
  `frontend/src/panels/explore/cacheIdentity.ts` to any point; the backend's
  `point` response remains authoritative.
- A shared `DataCacheButton` component carries Explore's current states,
  colours, progress, and cancel, shows `partial` as "Cached for some columns",
  and shows the snapshot size and whether it is pinned or automatic.

**Acceptance:**

- Explore and a Banding editor on the same parent render the same job progress
  from one job, whichever consumer started it.
- With one generation holding only the banded column, a Banding editor and an
  Explore preview on the same parent, mounted together, show `current` and
  `partial` respectively from the same slot entry; a full-width build then makes
  both `current`.
- A point that becomes `current` through a run's automatic capture updates the
  button without a user build.
- A stale identity aborts the in-flight `point` request; a document-fence
  change prevents late state writes.
- A direct-Parquet `data_input` point renders "Reads Parquet directly" with no
  button.

**Dependencies:** CACHE-S03.

**Evidence:** `frontend/src/panels/ExplorePreview.tsx`;
`frontend/src/panels/explore/cacheIdentity.ts`;
`frontend/src/stores/useNodeResultsStore.ts`;
`frontend/src/hooks/ensureInputSnapshots.ts`;
`frontend/src/panels/__tests__/ExplorePreview.test.tsx`.

### CACHE-S06 — Execution-profile and projection data semantics proof

**Why:** Execution profiles change source reads: bounded profiles reject plain
JSON, require declared CSV dtypes, and project source scans, while preview does
not. Reusing a snapshot across profiles, or serving a narrow reader from a wide
generation, is safe only where the outputs are identical.

**Plan:** Add a differential test that materialises the same point under every
bounded profile (`TRAINING_PREP`, `OPTIMISER_SETUP`, `EXPLORE_ANALYSIS`,
`AUTO_RANGE`, `LAZY_SINK`, `CHUNKED_MAP_REDUCE`, `NODE_SNAPSHOT`) through the
current source paths: a canonical Data Input over direct Parquet
(`read_polars_input`), a published input snapshot, an `apiInput` port served
from its table cache, a CSV Data Input with declared dtypes, a transform, a
join, an aggregation, and a Model Score node. Frame fixtures assert identical
schema and values. Each frame fixture is also materialised with a projected
column demand and compared with the full output restricted to those columns.
Rejection fixtures (a bounded CSV read without declared dtypes, plain JSON)
assert the same typed error under every bounded profile instead of frame
equality. Any profile that differs gets its own semantics class before
CACHE-S07 starts. The frame fixtures are also run under `PREVIEW_EAGER` with no
row limit and with row limits 1, 3, and larger than the frame. Without a limit
the preview output must equal the bounded output; with limit `N` each collected
node must equal the first `N` rows of the bounded output. The Model Score
fixture runs with a limit so the row-local scoring path that a non-zero limit
selects is compared. That result decides whether preview may read and write
`bounded` snapshots (CACHE-S09).

**Acceptance:** The differential test passes, and the snapshot write and read
mappings match its findings, including the `PREVIEW_EAGER` decision.

**Dependencies:** The IO-layer snapshot write and read class mappings.

**Evidence:** `src/haute/_polars_utils.py`; `src/haute/_io.py`;
`src/haute/_input_providers.py`; `src/haute/_builders.py`.

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
  node-output publication rule as an `automatic` generation, then continues
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
- A paused seeded worker survives refresh and clear of its seed (CACHE-S02
  paused-reader contract).

**Dependencies:** CACHE-S02, CACHE-S03, CACHE-S06; the current checkpoint rule,
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

- **Admission.** A preview lineage is admitted to shared snapshots when the
  snapshot class mappings let `PREVIEW_EAGER` read and write `bounded` and a
  schema-only bounded preparation of the lineage's sources succeeds (every CSV
  it reads has declared dtypes and it reads no plain JSON). A lineage that is not
  admitted neither seeds nor captures and behaves as today.
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
  execution runs in an isolated worker, the CACHE-S02 parent-lease handoff
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
- When CACHE-S06 finds preview and bounded outputs differ, previews and traces
  never seed or capture.
- Frontend tests: a preview fetched before a snapshot publishes is refetched
  after the epoch increments; a preview's own capture does not refetch it; the
  panel lists the seeded node labels; a completed trace is hidden, and an
  in-flight trace is aborted and its late response discarded, when the snapshot
  it depends on is refreshed or cleared.

**Dependencies:** CACHE-S05, CACHE-S06, CACHE-S07; the preview/trace lineage
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

**Dependencies:** CACHE-S02.

**Evidence:** `src/haute/_json_shred/_cache.py`; `src/haute/routes/json_cache.py`.
