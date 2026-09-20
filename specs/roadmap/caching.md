# Caching roadmap

## Scope

The aim is one cache system for Haute: full data at any pipeline point is
computed once, stored once, and reused by every consumer, in a way that is
**performant** (a consumer starts from what an earlier run left, and the cache
itself costs little), **memory safe** (every full-data read and write is
bounded so data larger than memory works), and **prevents duplicated runs** (a
preview, a training run, an optimiser set-up, a Data Output, an explicit cache
build, and a trace all read and write the same store instead of each keeping
its own, and two consumers that need the same cold data compute it once).
Current behaviour is specified in [caching](../caching/high-level.md), the
[IO layer](../io-layer/high-level.md), and the
[execution engine](../execution-engine/high-level.md). Explore consumers of
these packages are owned by the [Explore / EDA roadmap](explore-eda.md).

## Where the aim stands

The shared store, the data-point resolver, the node-data build service, the
analysis-result store, seed plans, chunked writes, and the preview and trace
seeding that a `banding-rating-ui` review examined on 19 September 2026 are
delivered and specified; their packages are retired from this roadmap.
Choosing captures by recompute cost, recording why a candidate was skipped,
computing part digests during the write, heavy-row index windows and their
cardinality check, one chunk size per explicit build with its reported
rows-per-part bound, and reporting a preview served from the response cache
as seeded while raising the node-data epoch only for an unseen capture
generation were delivered on the same branch and are specified in
[caching](../caching/low-level.md#seed-plans), the
[IO layer](../io-layer/low-level.md#node-output-snapshots), the
[execution engine](../execution-engine/low-level.md), the
[server API](../server-api/low-level.md#node-data-builds), and
[frontend shared](../frontend-shared/low-level.md#the-shared-data-cache). The
review found no data-corruption defect and confirmed the chunked join against
the native join on a lookup side whose hot key matched ten times the chunk
size. What remains is where the delivered behaviour is narrower than the aim
or where it will not hold at scale.

| Aim | Delivered | Gap |
|---|---|---|
| One store, every consumer | Node outputs, input snapshots, and analyses share `.haute_cache`; previews, bounded runs, explicit builds, and traces run under one seed plan. | API-input tables still live in the JSON cache (`CACHE-S08`). |
| No duplicated runs | A bounded run seeds from any fresh covering generation and captures a join, fan-out, join feeder, batch Model Score, or consumed producer only where recomputing it costs more than the cache round trip, and records why it skipped the others; a preview seeds the same way and captures only the joins and costly full-input work it must compute in full. A chain of plain transforms is recomputed by every preview and bounded run by design, because recomputing it costs less than the cache round trip. Each capture publishes as soon as it is written, so a run that fails or is cancelled later keeps what it had already published. A preview served from the response cache reports its generations as seeded, and the canvas raises the node-data epoch only for a capture generation it has not seen, so a repeat preview costs no refetch. | Two consumers that resolve the same cold capture point at the same time, or an explicit build and an automatic capture of one node, both compute it; the publication lock decides only who publishes (`CACHE-S19`). |
| Performant | Seeds stop the walk; captures are written once and read by everything below. Each part's digest is computed while it is written, so publication reads no part in full. An explicit build runs its execution and its target write at one chunk size, and a capture records the rows-per-part bound its write applied. Node outputs have their own budget which input snapshots neither consume nor are evicted by. A preview says when a node was not cached and how to fix it. | The store's usage is invisible, and a job's refused capture is (`CACHE-S12`). A capturing preview must finish inside the 120-second interactive timeout (`CACHE-S13`). Every preview prepares the graph several times and signs every lineage node per resolution (`CACHE-S17`). |
| Memory safe | A frame Polars can slice at its single file or in-memory leaf is written a slice at a time; an edge join is written a driving chunk at a time against only the lookup rows those keys match; batches are one query each. A heavy row's windows are index ranges, so they are disjoint and complete whatever order the engine returns rows in, and the writer refuses a row whose written count is not the count it expected. | Any other plan, including an ordinary filter over a large input, is written by one native streaming sink whose peak memory is Polars' to bound (`CACHE-S20`). Full joins rescan the base per lookup chunk and cross joins collect the lookup side (`CACHE-S18`). |
| Failures are recoverable | A corrupt generation is reported, never silently repaired; a plan whose inputs moved before collection stops. | The corrupt error reaches the user as store text with no pointer to Re-cache; a mid-run input change continues instead of stopping (`CACHE-S16`). |

## Priorities

| Package | State | Priority | Outcome |
|---|---|---:|---|
| CACHE-S12 | Planned | P2 | The store's usage is visible, and a job's refused capture is. |
| CACHE-S13 | Planned | P3 | A capturing preview finishes as a job instead of dying at the interactive timeout. Unproven: no measured preview approaches the timeout. |
| CACHE-S19 | Planned | P2 | Two consumers that need the same cold capture compute it once. |
| CACHE-S16 | Planned | P2 | Corrupt generations and mid-run input changes surface as typed, actionable failures. |
| CACHE-S20 | Planned | P2 | Row-local single-input nodes are written a slice of their input at a time. |
| CACHE-S17 | Planned | P3 | Planning and store housekeeping cost stays flat as graphs and stores grow. |
| CACHE-S18 | Planned | P3 | Full and cross joins are written with a bounded number of scans and a bounded part product. |
| CACHE-S08 | Deferred | P3 | Fold the API-input table cache into the shared snapshot store with per-table validity. |

## Planned improvements

Delivery order is `CACHE-S20` → `CACHE-S19` → `CACHE-S16` →
`CACHE-S17` → `CACHE-S13` → `CACHE-S18`; `CACHE-S08` is deferred, and
`CACHE-S12`'s remaining half waits on the toolbar work its usage surface
would land in, so it is taken whenever that settles rather than in this
order. `CACHE-S13` moved down on 20-Sep-2026 because measurement showed
no preview near its timeout; `CACHE-S20` moved up because the cost rule now
sends the commonest node shape down the one write path whose memory is
unproven. A package must not bypass
the resolver, lease, signature, seed-plan, or capture contracts already
specified. Every package builds on the node-output snapshot store (signature,
slot index, column widening, retention, cross-process leases, and the
publication rule) specified in the
[IO layer](../io-layer/low-level.md#node-output-snapshots), the data-point
resolver and leased reads specified in
[caching](../caching/low-level.md#data-points), seed plans specified in
[caching](../caching/low-level.md#seed-plans), the chunked writer specified in
the [IO layer](../io-layer/low-level.md), the node-data build service, the data
profile job, and the request-time analysis helper specified in the
[server API](../server-api/low-level.md#node-data-builds), the analysis-result
store specified in [caching](../caching/low-level.md#analysis-results), and the
shared frontend data cache specified in
[frontend shared](../frontend-shared/low-level.md#the-shared-data-cache).

Per the [working protocol](README.md#working-protocol), each package updates
the specification sections its **Owning specifications** line names before
its behaviour changes; the roadmap records the direction and the acceptance
evidence, not the contract text. Each package's **Evidence** line is also its
affected-file list.

### CACHE-S12 — A refused capture and the store's usage are visible

**Why:** Node outputs have their own budget. A refused capture is now shown in
the preview pane's execution-diagnostics indicator, naming the node and both
remedies. The store's usage against its two budgets is visible nowhere, so a
user cannot tell how close the cache is to refusing the next capture. The
node-data job status still says nothing when a build's capture was refused.

**Plan:** Two items, with the usage surface designed rather than sketched:

- **Usage surface (deferred by decision, 20-Sep-2026).** It reports, per budget
  (node outputs, and input snapshots), the generations used against the limit
  and the bytes used against the limit, and names the environment variable
  behind each limit (`HAUTE_NODE_SNAPSHOT_MAX_GENERATIONS`,
  `HAUTE_NODE_SNAPSHOT_MAX_BYTES`, `HAUTE_INPUT_CACHE_MAX_GENERATIONS`,
  `HAUTE_INPUT_CACHE_MAX_BYTES`). The numbers come from the store's existing
  per-bucket accounting (`_bucket_usage`), read through one read-only endpoint
  that returns both budgets in one response and takes no arguments. That
  accounting walks every identity, generation and staging entry, which is what
  an admission already pays, so the endpoint answers an explicit request and
  the response is a snapshot, not a subscription. A surface that wants to poll
  needs an incremental count in the store first; decide which before building
  it, and say which in the change. Where it appears is
  deferred: the toolbar is being changed on another branch, so placing it now
  would conflict, and the placement decision belongs with that work.
- **Job status.** A build whose capture was refused says so in the node-data
  job status, the same way the preview pane now does.

**Acceptance:** For the usage surface, a route test proves the endpoint
reports both budgets' generations and bytes against their limits, and a
frontend test proves whatever displays it shows both budgets and names the
variable behind each limit. For the job status, a route test proves a
refused capture appears in the job status payload, and a frontend test
proves it is shown.

**Owning specifications:** [server API](../server-api/low-level.md)
(execution warnings); [frontend shared](../frontend-shared/low-level.md).

**Dependencies:** The usage surface depends on the toolbar work being settled.

**Evidence:** `src/haute/routes/node_data.py`;
`src/haute/_source_cache.py` (`_bucket_usage`).

### CACHE-S13 — Capturing previews as jobs

**Why:** A preview whose plan captures a join writes the whole join to disk
before it returns rows, inside the interactive worker's timeout
(`HAUTE_PREVIEW_TIMEOUT`, default 120 seconds). When a capture exceeds that,
the request answers 504, the worker is killed, and its staging is discarded,
so the unfinished capture is lost and the next preview repeats it. Captures
the worker had already published survive, because each capture publishes as
soon as it is written.

Measured on `haute-setup-testing` on 20-Sep-2026, that ceiling is far away.
Previewing a 10M-row, 110-column join captured and published it in 9.9
seconds, writing 982 MB at about 160 MB/s; previewing the model-score node
below it took 6.1 seconds; previewing the cheap slice below the join, seeded
from its capture, took 0.7 seconds. No preview timeout is recorded anywhere
in that project. Reaching 120 seconds needs roughly an order of magnitude
more data in one capture, or a node where computing rather than writing
dominates: a batch Model Score or a group-by over the full data with no
upstream row limit. This package is therefore unproven rather than urgent,
and it is ordered after the packages whose problems are demonstrated.

**Plan:** Measure before building. A performance artifact records preview
time against capture size on the largest real pipeline, so the threshold
below is chosen from data rather than guessed, and the package stays shelved
while no preview approaches the ceiling.

Raising the timeout alone is not the cheap first step it looks like: the
route dispatches the preview to a worker before any plan exists, because the
plan is opened inside the worker (`routes/pipeline.py`, `timeout_seconds=`),
so the route cannot tell a capturing preview from any other without resolving
the plan itself, which is the work `CACHE-S17` is trying to remove. Giving
every shared-snapshot preview the sink timeout is possible but makes a hung
preview take 300 seconds to fail, so it is a decision to take deliberately,
not a free win.

When the measurements justify it, dispatch by a typed **capture-work
estimate**:
for each capture point, the row count of each effective input as the nearest
materialised point below it records it (a seed generation's or an input
snapshot's metadata, a direct Parquet footer, a JSON table cache's
metadata), summed over the capture's inputs; the plan's estimate is the sum
over its capture points; an input with no recorded row count makes the
estimate `unavailable`. The route resolves the plan once with waiting
disabled (`CACHE-S19`) and dispatches on what that resolution reports,
before anything blocks: a plan whose estimate is `unavailable` or at least
`HAUTE_PREVIEW_CAPTURE_JOB_ROWS` (default one million), or whose first
resolution finds a capture point another run has claimed, runs under the
isolated job supervisor with `HAUTE_PREVIEW_CAPTURE_JOB_TIMEOUT` (default
the sink timeout): the route answers `status: "caching"` with a job id and
the capture points' slot keys at once, the job opens its plan with waiting
enabled and performs any wait and re-resolution there, the frontend polls it
through the existing node-data slot job machinery so every consumer of those
points sees the build and can cancel it, and the completed job carries the
preview response. A plan with no captures and no claimed points, or an
estimate below the threshold, answers inline as today; the inline path also
opens its plan with waiting disabled, so a claim that appeared between the
route's resolution and the plan's open never blocks the request but hands it
to a job the same way. The estimate, the claimed points, the waits, and the
dispatch decision are recorded in the execution metrics.

**Acceptance:** A route test proves a preview whose estimate is at the
threshold returns `caching` and its job completes with a preview response
whose `seed_plan` lists the captures; one below it answers inline; one with an
`unavailable` estimate is dispatched; one with no captures of its own whose
capture point another run has claimed returns its job id while that claimant
is still blocked, and the wait and re-resolution happen inside the job; one
whose point is claimed between the route's resolution and the inline plan's
open is handed to a job without blocking; a job cancelled after its first
capture published leaves that capture published and current, discards the
unfinished capture's staging, and publishes nothing further. A frontend test
proves the panel shows the caching state, the cache button of each capture
point shows the same build, and the preview renders when the job completes.

**Owning specifications:** [server API](../server-api/low-level.md) (preview
route, job lifecycle, response schema); [caching](../caching/low-level.md#seed-plans)
(capture-work estimate); [frontend preview](../frontend-preview-explore/low-level.md);
[frontend shared](../frontend-shared/low-level.md#the-shared-data-cache).

**Dependencies:** None. The capacity `CACHE-S12` was needed for is
delivered: node outputs have their own budget, which input snapshots neither
consume nor evict.

**Evidence:** `src/haute/routes/pipeline.py` (`_preview_canonical_graph`,
`_preview_timeout`); `src/haute/routes/_background_jobs.py`;
`src/haute/_seed_plans.py` (`SeedPlanDecision`); `src/haute/schemas.py`;
`frontend/src/hooks/usePipelineAPI.ts`; `frontend/src/stores/useNodeDataStore.ts`.

### CACHE-S19 — One computation per cold capture point

**Why:** A seed plan decides its captures from store metadata, and the run
computes each capture before it takes the identity's publication lock
(`src/haute/_node_snapshots.py`, `publish_node_output`). Two consumers that
resolve the same cold identity at the same time, a preview and a training run
on one lineage, both compute it in full; the lock only decides which
publishes and the other keeps its artifact as `superseded`. Only explicit
node-data builds join a running build (`src/haute/routes/_node_data_service.py`,
`run`). The aim's "computed once" therefore holds after publication, not
during it.

**Plan:** Add cross-process in-flight **claims** to the store, settled when
a plan opens and never during execution, so every plan that runs is one
resolution that passed ancestry agreement whole. A claim is a per-identity
lock file plus `.inflight/<identity digest>.json` recording the columns the
claimant will write, its process token, and its deadline; a dead process
token releases a claim as it releases a lease marker. `open_seed_plan`
resolves, then under the store's lease lock compares each capture point
(automatic captures and an explicit build's own node alike) against live
claims: a claim whose columns cover the point's negotiated demand makes the
point **awaited**; when nothing is awaited the plan claims every capture point
it owns under the same lock and proceeds. Whether a plan waits is the
caller's policy: a route that must answer promptly opens with waiting
disabled and, on an awaited point, returns at once so `CACHE-S13` can hand
the request to a job; a job or a spawned worker opens with waiting enabled.
A plan that awaits anything and may wait takes no claims, releases its seed
leases, waits on the awaited claims' locks up to its deadline, and resolves
again from scratch, so the published generation is
seeded through the same walk, negotiation, and ancestry agreement as any
other seed, and a diamond whose ancestor was refreshed while the waiter
waited drops the stale seed exactly as it does today; if the claimant
published nothing, the point is cold on re-resolution and the waiter claims
and computes it. Because a plan holds claims only when it awaits nothing,
two plans cannot wait on each other. A `refresh` request never awaits and its
claim records `refresh`; a claim that does not cover the demand is not
joined, and an explicit build, which writes every column, awaits only a
claim that writes every column. Claims are released on publication,
supersession, failure, cancellation, and plan close. Waits are recorded in
the execution metrics (`shared_snapshot_waits`, with the awaited identity,
the claimant's token, and the wait time) and shown as "waiting for another
run's cache" in the preview panel and job status.

**Acceptance:** `tests/test_node_snapshot_cross_process.py` proves two
processes opening plans on one cold identity perform one build and both read
its generation; that an explicit build and an automatic capture racing on one
identity perform one build, whichever claims first; that a claimant's failure
or cancellation lets the waiter claim and build; that a narrower claim is not
joined; that a refresh does not wait; that a dead claimant's claim is
released; and, for a diamond `A → B`, `A → C`, `B, C → D`, that a waiter
whose first resolution seeded `A` at one generation re-resolves after `A` is
refreshed and `B` is published from the new generation, and neither reads
`B` beside the old `A` nor publishes `D` from mixed generations.
`tests/test_seed_plans.py` covers the awaited decision, that a plan with
awaits holds no claims, and that a plan without awaits claims every capture
point it owns; a route test proves the waiting state in a job's status.

**Owning specifications:** [IO layer](../io-layer/low-level.md#node-output-snapshots)
(in-flight claims); [caching](../caching/low-level.md#seed-plans) (claims
and waits at plan open); [server API](../server-api/low-level.md#node-data-builds)
(explicit builds joining claims, execution metrics).

**Dependencies:** `CACHE-S13`, whose dispatch sends a preview that waited to
the job path so it is not bounded by the interactive timeout.

**Evidence:** `src/haute/_node_snapshots.py`; `src/haute/_seed_plans.py`;
`src/haute/_execute_lazy.py` (`_PlannedCaptures`);
`src/haute/_execution_context.py`.

### CACHE-S16 — Typed failures

**Why:** Plan resolution propagates a corrupt generation as the store's
`SourceCacheCorruptError`, and an automatic capture surfaces corruption rather
than repairing it, by design. Every preview and run through that lineage then
fails with store text until the user presses Re-cache or Clear on the right
node, and nothing tells them which node. Separately, the pre-collection check
stops a run whose inputs moved since planning, but a capture that finds its
inputs moved mid-run keeps its own artifact and continues
(`src/haute/_execute_lazy.py`, `_PlannedCaptures.capture`), so seeds computed
from the old inputs are joined with branches computed from the new ones, the
very mix the check exists to prevent.

**Plan:** Raise a public contract error, `SnapshotCorruptError`, carrying the
producer node id and label from plan resolution and from the publication
rule's corrupt branch; map it to 422 with the node in its payload; have the
frontend toast name the node and point to its cache button, whose `corrupt`
state already offers Re-cache. Make a mid-run input change raise
`SnapshotPlanInputsChangedError`, matching the pre-run check and the error's
own documentation: the unfinished capture's staging is discarded, captures
the run published before the change stay published under the identities they
were computed for, and nothing further is published. Prove the eager
engine's per-node error capture never swallows either error.

**Acceptance:** `tests/test_seed_plans.py` and
`tests/test_node_snapshot_retention.py` prove the typed error and its node;
route tests prove the 422 payload for preview, training preparation, and Data
Output; a frontend test proves the toast; `tests/test_snapshot_seeding.py`
proves an input change after the first capture published stops the run,
leaves that capture published, and publishes nothing further.

**Owning specifications:** [caching](../caching/low-level.md#seed-plans)
(error handling); [server API](../server-api/low-level.md) (contract error
payloads); [frontend shared](../frontend-shared/low-level.md).

**Dependencies:** None.

**Evidence:** `src/haute/_seed_plans.py`; `src/haute/_node_snapshots.py`
(`_should_publish_locked`); `src/haute/_execute_lazy.py`;
`src/haute/errors.py`; `src/haute/routes/_contract_errors.py`.

### CACHE-S20 — Input-sliced writes for row-local nodes

**Why:** The chunked writer slices a frame only when Polars pushes a slice
down to its single leaf (`src/haute/_chunked_writes.py`, `sliceable`), which
Polars refuses for a filter, because slicing after a filter is not slicing
before it. Every such plan, including an ordinary filter or a row-local
transform with a filter in it over a large input, is written by one native
streaming sink (`write_parts`, `native` strategy), whose peak memory is
Polars' to bound and which the module's own rationale does not trust for one
long query. The same native sink is how training preparation writes its
parquet and how a Data Output writes Parquet, so an uncaptured cheap segment,
which the cost rule leaves to the consumer, takes that path in every bounded
run. The memory-safety aim therefore holds for sliceable frames and edge
joins and is unproven for the most common node shape.

**Plan:** Measured on 20-Sep-2026: the native sink's peak grows with its input
while the sliced strategy's stays bounded, growing about 1.3 times over a
fourfold input rather than in step with it. On 60-column real data, fresh process
per case, peak private bytes over a clean baseline:

| Rows | filter (native) | filter with derived columns (native) | unnest (sliced) | sort (native control) |
|---|---|---|---|---|
| 2.5M | 2520 MB | 2650 MB | 1465 MB | 5520 MB |
| 5M | 4446 MB | 4940 MB | 1577 MB | 10063 MB |
| 10M | 6128 MB | 8607 MB | 1604 MB | 14375 MB |

The artifact carries a passthrough control, every row kept, forced down the
same native path: it grew 1.97 to 2.00 times against the filter's 2.02 to
2.25, so the predicate adds almost nothing and the growth belongs to the
native sink itself rather than to filtering. Recorded as a permanent reproducible artifact in
`tests/performance/test_write_strategy_memory.py`, which measures a 40-column
fixture at 1.5M and 6M rows and asserts the relationship rather than any byte
count: every native case grew at least 1.97x there against the sliced
control's 1.32x.
The package proceeds on this evidence. Write a chunk-local single-input node
a slice of its input at a time: the engine
already knows a node's builder function and its one input frame, and the
union of the function applied to each input slice equals its output exactly
when the function is chunk-local. That proof is the existing closed
allowlist `classify_chunk_local_polars_code` in `src/haute/chunking.py`,
which admits a node's code only when every construct is recognised and
rejects positional and cross-row operations, so `head`, `slice`, `limit`,
`tail`, `with_row_index`, `sample`, `unique`, cumulative and window
expressions, and any operation it does not name stay native; the absence of
a materialisation boundary is not a proof and is not used as one. A node
without code is admitted only when its builder declares its step chunk-local
(selection, renames, an unnest, an explode, a filter on a row-local
predicate). The allowlist is extended, if at all, one operation at a time
with an equivalence test. Route every full-frame write through the chunked
writer, captures, explicit builds, training preparation's parquet, and a
Data Output's Parquet, so the strategy applies to an uncaptured cheap
consumed segment as much as to a capture. A consumer receives only a node's
final frame today, so the engine hands it the recipe as it already hands
join recipes: `execute_lazy_graph` fills a caller-supplied
`write_recipes` map with, for every node whose segment the proof accepts,
its sliceable input frame, the builder function to apply per slice, the
engine's own post-builder shaping (selected columns, then renames) composed
after it exactly as `JoinRecipe.finish` composes it for a join, and the
proof's reason, so the recipe reproduces the exact frame the engine hands
out and nothing else. A consumer that transforms that frame further
composes each row-local step into the recipe through the same `then`
mechanism (training's column exclusions and projections, a Data Output's
selected columns); any step that is not chunk-local, above all training's
seeded sample under a row limit, which is a global draw and cannot be taken
slice by slice, invalidates the recipe, and that write takes the native
path with the reason recorded. A recipe is only ever applied to the frame it
was bound to: passing it with any other frame is an error.
Because training preparation and a Data Output each need one regular file
at a fixed staging path, the writer gains a single-file mode beside
`write_parts`: it drives the same slices through one Parquet writer, one row
group per slice, into the destination's staging file, so the existing
sign-then-publish contract and every atomic-replace and cleanup rule of the
consumer are untouched. Record the strategy (`input_sliced`), the proof's
reason, and the input slice count in the capture record and the consumer's
execution metrics.

**Acceptance:** The artifact records the measurements and the decision.
If implemented: `tests/test_chunked_writes.py` proves equality with the
native result for a filter, a filter with derived columns, an unnest, and an
explode; proves `head`, `slice`, `with_row_index`, `unique`, a shift, a
window function, and a group-by stay native with the classifier's reason;
and proves no query in the sliced write holds more than one input slice;
`tests/test_snapshot_seeding.py` proves a captured filter node reports
`input_sliced` and a captured `head` node reports `native`;
`tests/test_training_seeding.py` proves a cheap consumed filter over a
large input is written input-sliced by training preparation into its single
parquet, across several slices, equal to the native result, without
publishing a snapshot; that a node with selected and renamed columns and a
training run with column exclusions are written equal to the native result
across several slices; that a training run with a row-limit sample takes
the native path with the reason recorded and its rows equal the unsliced
sample; that a recipe passed with a frame other than the one it was bound
to is refused; and that a cancellation mid-write leaves no partial file
where the output would be; `tests/test_data_output_seeding.py` proves the
same for a Data Output with selected columns through its staging path and
that the signed publication contract is unchanged.

**Owning specifications:** [IO layer](../io-layer/low-level.md) (chunked
writes, single-file mode); [execution engine](../execution-engine/low-level.md)
(capture writes, write recipes, chunk-local classification);
[modelling](../modelling/low-level.md) (training preparation's parquet);
[server API](../server-api/low-level.md) (Data Output writes).

**Dependencies:** None.

**Evidence:** `src/haute/_chunked_writes.py`; `src/haute/_execute_lazy.py`
(`_PlannedCaptures.capture`, `_edge_join_recipe`, the `join_recipes`
handoff); `src/haute/execution.py` (`execute_lazy_graph`);
`src/haute/chunking.py` (`classify_chunk_local_polars_code`);
`src/haute/_builders.py`; `src/haute/routes/_training_preparation.py`
(`_execute_and_sink_training_frame`); `src/haute/executor.py`
(`prepare_data_output`, `_run_lazy`); `src/haute/_polars_io_registry.py`
(Data Output Parquet writer).

### CACHE-S17 — Flat planning and housekeeping cost

**Why:** One preview prepares the graph in admission, in every resolution
round, in the post-capture key, and in execution, and every resolution signs
each node-output node in the lineage with a fresh upstream fingerprint, so
planning cost grows with the square of graph size. Every `NodeSnapshotStore`
construction globs the store for retired directories, every publication walks
every generation for bytes and eviction candidates, and every lease reads each
part's footer, Arrow schema, and Polars schema. Each process that leases
creates a token file under `.processes` and each identity ever published a
lock file under `.locks`; nothing sweeps either.

**Plan:** Measure first: a performance artifact records planning time per
preview against graph size on the largest real pipeline, and the store
operations' time against generation count. Then: keep one prepared graph and
its structural facts (order, effective edges, pass-through edges,
materialising operators, projection inputs) across lease attempts and
preparation rounds, while every node signature and identity is recomputed
after any preparation, because a signature signs the input generations
preparation may have moved; derive per-node lineage fingerprints from one
canonical-graph pass memoised by node id within a single resolution; run
retired-directory cleanup once per process per root; keep the node-output
byte and generation totals that `CACHE-S12` admits against in a store-level
summary, and keep walking the input-snapshot totals, because input-snapshot
publication, retirement, clear, and reconciliation are serialised by
`SourceCacheStore`'s process-local locks and not by the cross-process lease
lock. Every node-output mutation already runs under the lease lock
(publication, retirement, eviction, clear), so the protocol is: under that
lock, write an intent record naming the identity, the generation, and the
process token before the mutation; perform it; rewrite the summary
atomically with the next revision; remove the intent. Admission, under the
same lock, first reconciles any intent record left behind, whose process
token is dead or whose generation the summary disagrees with, by walking
that one identity's directory and rewriting the summary, so a worker killed
between a generation's rename and the summary commit never lets the next
admission trust stale totals; a summary that is absent or unparsable is
rebuilt by a full walk of node-output generations. Staging bytes are still
walked at admission, because staging is written outside the lock. A
directory's modification time is not used as a gate, because a write beneath
an existing identity does not change the inputs root. Have the
verified-generation memo cover the footer checks so a lease validates each
part once per process; sweep dead token files and unused publication locks
at that once-per-process cleanup, and have that cleanup reconcile every
leftover intent under the lease lock, exactly as admission does, before it
removes the record: an intent is never swept unreconciled, because it names
a mutation whose totals the summary may not yet hold, and store construction
runs cleanup before any admission.

**Acceptance:** The artifact records the before and after measurements and
the store's operation count per preview; `tests/performance/` gains the
planning benchmark; `tests/test_node_snapshot_retention.py` proves the
summary tracks a node-output publication under an existing identity, a
retirement, an eviction, a clear, and staging growth, and that an input
snapshot published from another process leaves the node-output totals
correct; `tests/test_node_snapshot_cross_process.py` proves two processes
publishing under one identity admit against the same totals, and that a
process killed between a generation's rename and the summary commit leaves
an intent that the next admission reconciles before it admits, and that a
fresh store constructed after that kill reconciles the intent in its cleanup
so its first admission sees correct totals and no intent survives
unreconciled; a corrupt summary is rebuilt; the existing regression that a
preview's capture moves to the
prepared signature after an input build passes unchanged, as do the
seed-plan, retention, and cross-process tests, because none of this changes
what is read or written.

**Owning specifications:** [caching](../caching/low-level.md#seed-plans);
[IO layer](../io-layer/low-level.md#node-output-snapshots).

**Dependencies:** None.

**Evidence:** `src/haute/_seed_plans.py` (`_Resolver`,
`open_resolved_seed_plan`, `_open_preview_seed_plan`);
`src/haute/_node_snapshots.py` (`__init__`, `_cleanup_retired`,
`_own_token`, `_admit_node_output_locked`); `src/haute/_source_cache.py`
(`_metadata_from_path`); `tests/test_seed_plans.py` (prepared-signature
regression).

### CACHE-S18 — Bounded scans in full and cross joins

**Why:** A full join's unmatched pass (`src/haute/_chunked_writes.py`,
`_write_chunked_join`) runs a semi-join of the whole base against every
lookup chunk, so the base is scanned once per chunk of the lookup side. A
cross join collects the whole lookup side into memory before slicing the
driving side against it. Both are correct and bounded in output, but the first
costs the product of the two sides divided by the chunk size and the second is
unbounded in memory on the lookup side. The uniqueness check filters its input
once per hash partition, so it is a rescanning algorithm too and not a model
for this package.

**Plan:** Partition physically, not by rescanning. In one pass over each
side, write the lookup side's whole rows, keys and payload, and the base
side's distinct keys into `partitions` part files by
`hash(keys) % partitions`, with `partitions` chosen from the larger row count
so an average bucket fits a chunk; null keys, which match nothing, are
written straight to the unmatched output and never partitioned. Then, per
partition, anti-join the lookup bucket against the base-key bucket and write
the unmatched lookup rows through the empty-base join. Hashing keeps every
row of one key in one bucket, so skew is handled where it lands: a lookup
bucket is a part file and is read in slices of `chunk_rows` against its
base-key bucket, so a hot key with more distinct payload rows than a chunk
is written a slice at a time; a base-key bucket holds distinct keys, so a hot
key is one row there, and a base-key bucket that still exceeds a chunk (many
distinct keys hashed together) is partitioned once more with a second seed,
after which a bucket that still exceeds a chunk makes the join fall back to
the native write with a recorded reason. Each side is read at most twice,
once to partition and once through its buckets, plus one pass over any
re-partitioned bucket. Stage a cross join's lookup side through `_staged`
and read it in slices: a part is the product of one driving slice and one
lookup slice, sized so `driving_rows × lookup_rows ≤ chunk_rows`, and an
ordered cross join writes its parts in driving-then-lookup order.

**Acceptance:** `tests/test_chunked_writes.py` proves full-join equality with
the native join across partitions, including null keys on either side,
`coalesce`, an empty side, and an unmatched hot key whose distinct payload
rows exceed `chunk_rows`, and asserts through a scan-counting source that
each side is read at most twice plus one re-partition pass; it proves the
second-seed re-partition and the native fallback each take effect on a
constructed skew; it proves cross-join equality with a lookup side larger
than one chunk, with an empty side, and with `maintain_order`, and asserts
every part holds at most `chunk_rows` rows; the capture record reports the
partition count, the re-partition count, and any fallback reason.

**Owning specifications:** [IO layer](../io-layer/low-level.md) (chunked
joins).

**Dependencies:** None.

**Evidence:** `src/haute/_chunked_writes.py`.

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

**Owning specifications:** [caching](../caching/low-level.md);
[JSON shredding](../json-shredding/low-level.md).

**Dependencies:** The caching data-point resolver.

**Evidence:** `src/haute/_json_shred/_cache.py`; `src/haute/routes/json_cache.py`.
