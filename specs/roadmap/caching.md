# Caching roadmap

Review update, 22 September 2026: the [PR #227 assessment](pr-227-review.md)
supersedes the earlier review conclusions below for head `97f3e99`. The
[focused implementation plan](pipeline-cache-memory-design.md) records the
current scope for the existing Polars/Parquet pipeline and filesystem store,
with no replacement-engine or catalog work. It records input-lease,
publication, replacement and consumer-identity defects, and finds that
ordinary chunked joins also rescan their lookup; the existing package
descriptions below have not yet been rewritten or implemented to that plan.

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
rows-per-part bound, reporting a preview served from the response cache
as seeded while raising the node-data epoch only for an unseen capture
generation, and a cache-usage surface opened from the toolbar were delivered
on the same branch and are specified in
[caching](../caching/low-level.md#seed-plans), the
[IO layer](../io-layer/low-level.md#node-output-snapshots), the
[execution engine](../execution-engine/low-level.md), the
[server API](../server-api/low-level.md#node-data-builds) and its
[cache usage](../server-api/low-level.md#cache-usage) section, and
[frontend shared](../frontend-shared/low-level.md#the-shared-data-cache).
The byte and generation budgets that surface first reported have since been
removed: stored data has no cache-specific limit, and the Pipeline settings
pane lists it as a cached-data inventory with per-entry clearing. The
review found no data-corruption defect and confirmed the chunked join against
the native join on a lookup side whose hot key matched ten times the chunk
size. What remains is where the delivered behaviour is narrower than the aim
or where it will not hold at scale.

| Aim | Delivered | Gap |
|---|---|---|
| One store, every consumer | Node outputs, input snapshots (including each emitting table of a structured API Input, prepared automatically), and analyses share `.haute_cache`; previews, bounded runs, explicit builds, and traces run under one seed plan. | — |
| No duplicated runs | A bounded run seeds from any fresh covering generation and captures a join, fan-out, join feeder, batch Model Score, or consumed producer only where recomputing it costs more than the cache round trip, and records why it skipped the others; a preview seeds the same way and captures only the joins and costly full-input work it must compute in full. A chain of plain transforms is recomputed by every preview and bounded run by design, because recomputing it costs less than the cache round trip. Each capture publishes as soon as it is written, so a run that fails or is cancelled later keeps what it had already published. A preview served from the response cache reports its generations as seeded, and the canvas raises the node-data epoch only for a capture generation it has not seen, so a repeat preview costs no refetch. | Two consumers that resolve the same cold capture point at the same time, or an explicit build and an automatic capture of one node, both compute it; the publication lock decides only who publishes (`CACHE-S19`). |
| Performant | Seeds stop the walk; captures are written once and read by everything below. Each part's digest is computed while it is written, so publication reads no part in full. An explicit build runs its execution and its target write at one chunk size, and a capture records the rows-per-part bound its write applied. A preview says when a node was not cached and how to fix it. | A capturing preview must finish inside the 120-second interactive timeout (`CACHE-S13`). Every preview prepares the graph several times and signs every lineage node per resolution (`CACHE-S17`). |
| Memory safe | A frame Polars can slice at its single file or in-memory leaf is written a slice at a time; an edge join is written a driving chunk at a time against only the lookup rows those keys match; batches are one query each. A node with one input whose code is provably row-local is written a slice of its input at a time where a capture or an explicit build writes it, so its memory does not grow with the input. A pass-through node carries its parent's recipe forward, and training preparation writes its prepared parquet through the same bounded writer, slicing the frame or the recipe's input and recording which. A heavy row's windows are index ranges, so they are disjoint and complete whatever order the engine returns rows in, and the writer refuses a row whose written count is not the count it expected. | Full joins rescan the base per lookup chunk and cross joins collect the lookup side (`CACHE-S18`). |
| Failures are recoverable | A corrupt generation is reported, never silently repaired, and names the node whose cache to clear or rebuild; a plan whose inputs moved stops, before collection and again if they move before a capture publishes. | — |

## Priorities

| Package | State | Priority | Outcome |
|---|---|---:|---|
| CACHE-S13 | Planned | P3 | A capturing preview finishes as a job instead of dying at the interactive timeout. Unproven: no measured preview approaches the timeout. |
| CACHE-S19 | Deferred | P3 | Two consumers that need the same cold capture compute it once. |
| CACHE-S22 | Planned | P3 | The shapes that cannot carry a write recipe at all can. |
| CACHE-S17 | Planned | P3 | Planning cost stays flat as graphs grow, a lease validates each part once per process, and the store's bookkeeping files are swept. |
| CACHE-S18 | Planned | P3 | Full and cross joins are written with a bounded number of scans and a bounded part product. |
| CACHE-S24 | Planned | P3 | The assistant's two hand-rolled LRUs move onto the shared cache primitive. |
| CACHE-S25 | Planned | P3 | Cache identity hashes the whole canonical node config instead of classifying every field. |
| CACHE-S26 | Decision | P3 | Stored snapshots and node outputs have a retention policy, or the absence of one is a stated product choice. |
| CACHE-S27 | Planned | P2 | The server, not the browser, chooses how an input snapshot is built. |

## Planned improvements

Delivery order is `CACHE-S17` → `CACHE-S22` → `CACHE-S18`, each gated on a
measurement named in its own entry rather than started on the strength of its
shape; `CACHE-S19` is deferred with the others. `CACHE-S12` (a refused
capture shown at the node) was retired on 23-Sep-2026: the cache budgets
whose refusals it would have shown are removed, and a failed build already
reports its error through the shared job poller. `CACHE-S13` moved down on 20-Sep-2026 because measurement showed
no preview near its timeout. The packages from the
[23 September 2026 codebase review](codebase-review-2026-09-23.md)
(`CACHE-S24` to `CACHE-S27`) sit outside that order: `CACHE-S24` goes first; `CACHE-S25` follows the
pipeline-config package `PCFG-R08`. Every full-frame write is now bounded, so what
is left is measured against cost rather than shape. A package must not bypass
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

**Dependencies:** None. Capture capacity no longer limits it: stored data has
no cache-specific budget.

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

**Deferred 20-Sep-2026.** The server never has concurrent users, so the only
way two consumers reach one cold identity at once is one person's own
overlapping work — a preview while their training job runs, which is the case
this entry names. That can happen, but it is rarer than two people, and the
measurement below says it does not happen at all in practice. Against that,
this is the most dangerous change left in the component: it alters behaviour
under the store's lease lock, where a mistake is a cross-process data hazard
rather than a slow write. The value is small, the risk is the largest here,
and the lock discipline it would change is currently correct.

**Activation trigger:** a real project's logs show
`node_snapshot_capture_superseded` outside a deliberately constructed race —
that is, one person's overlapping operations actually colliding on a cold
identity often enough to be worth the risk. If it is built, it gets its own
review rather than a share of a batch.

**The measurement.** The duplicate computation
this removes already announces itself: a losing publication logs
`node_snapshot_capture_superseded` and records `outcome: "superseded"`. Tallied
over the seeding, node-data, Data Output, training and cross-process suites:
157 published against 6 superseded, and every one of those six comes from a
test that constructs the race on purpose. So in ordinary operation the case is
rare, while this is the most correctness-sensitive package left — it changes
what happens under the store's lease lock, where a mistake is a cross-process
data hazard rather than a slow write.

That is not a retirement: a test suite does not run a preview and a training
job against one cold lineage the way two people sharing a project do, and this
repository's runs cannot measure that. It is a gate. The event is already
logged, so counting it in a real project's logs answers the question at no
cost, and the work should wait for that count rather than be built on the
strength of the shape alone. If it is built, it gets its own review: claims
under the lease lock are not a package to fold into a batch.

Its stated dependency on `CACHE-S13` is soft and should not block it. This
package's own plan has a route that must answer promptly open with waiting
disabled, so without `CACHE-S13` the policy is simply that a preview never
waits — it computes as it does today and the claim holder publishes — which
loses nothing against today and keeps the value for the consumers that do run
long: training, a Data Output, and an explicit build.

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

### CACHE-S22 — The shapes that cannot carry a write recipe at all

**Why:** The chunked writer slices a node's input when the engine can hand it a write recipe.
Two shapes never can, so they take the native path however cheap their work is.

A **Data Input with editor code** has no input frame the engine hands out, because its scan lives
inside its builder; the builder must expose that scan before a recipe can name it.

A node whose operations ARE row-local but whose **code the allowlist refuses** is refused for the
code, not for the work. An earlier draft proposed letting a node type declare its builder's step
chunk-local; that is the wrong granularity, since stepped nodes already render their operations
into `code` that the classifier reads (`src/haute/_types.py:902-914`), and a node carrying a code
box is not decidable by its type at all (`src/haute/_builders.py:648-660`). The question is the
classifier's reach over the code it is given, and it should be answered by measuring which
refusals occur in real graphs before widening anything.

**Measured 20-Sep-2026, and it retires the allowlist half.** Every recipe refusal is now logged
(`write_recipe_refused`, with the node, reason, blocking operator and position), and tallied over
the seeding, node-data and Data Output suites: 74 admitted against 41 refused, whose blocking
operators were `sort` 32, `head` 4, `collect` 3, `pl.int_range` 1 and one unresolved name. Every
one of those is global by definition — a slice cannot be sorted, or headed, or collected, and
give the same answer as the whole — so each is refused correctly and there is nothing the
evidence asks to admit. Widening the allowlist is not work; confirming it against a real project's
graphs, rather than this repository's, is the only thing left to say about it.

**Plan:** What remains is the other shape: a Data Input with editor code has no input frame the
engine hands out, because its scan lives inside its builder. Expose that scan so a recipe can name
it.

**Acceptance:** Named once the measurement says which refusals are worth removing.

**Owning specifications:** [execution engine](../execution-engine/low-level.md)
(chunk-local classification, write recipes).

**Dependencies:** None.

**Evidence:** `src/haute/chunking.py`; `src/haute/_builders.py` (a Data Input's scan).

### CACHE-S17 — Flat planning and housekeeping cost

**Why:** One preview prepares the graph in admission, in every resolution
round, in the post-capture key, and in execution, and every resolution signs
each node-output node in the lineage with a fresh upstream fingerprint. Every
lease reads each part's Parquet footer, Arrow schema, and Polars schema: the
verified-generation memo skips only the digest check, not these reads. Each
process that leases creates a token file under `.processes`, and each
identity ever published a lock file under `.locks`; nothing sweeps either.

**Measured 20-Sep-2026, and the quadratic claim did not hold.** Seed-plan
resolution over a chain of row-local nodes, three runs each, best of three:
10 nodes 0.142s, 20 nodes 0.257s, 40 nodes 0.738s, 80 nodes 1.235s. Eight times
the nodes costs 8.7 times the time and the per-node cost is flat at 13–18ms, so
growth is linear, not quadratic. What the measurement does support is the
absolute cost: 15ms per node, paid again in admission, in each resolution
round, in the post-capture key and in execution, is over a second of planning
for an eighty-node graph before anything is read. The memoisation half of this
package is justified by that.

The once-per-process retired-directory sweep is delivered: it globbed the whole
store on every store construction, and a preview builds several. The store-walk
half this package once planned is obsolete. It kept byte and generation totals
in a store-level summary, with intent records for crash recovery, so that
publication could admit against the cache budgets without walking every
generation. The budgets are removed, and publication no longer walks the
store to admit against totals or look for eviction candidates. Two walks
remain: the on-demand cache inventory, and the stale-staging cleanup, which
globs every identity's staging directories and walks each one it finds on
every store construction.

**Plan:** Keep one prepared graph and its structural facts (order, effective
edges, pass-through edges, materialising operators, projection inputs) across
lease attempts and preparation rounds, while every node signature and
identity is recomputed after any preparation, because a signature signs the
input generations preparation may have moved; derive per-node lineage
fingerprints from one canonical-graph pass memoised by node id within a
single resolution. Have the verified-generation memo cover the footer and
schema checks, so a lease validates each part once per process. Count the
stale-staging cleanup in the per-preview store operations, and if it shows,
run it at most once per interval per process, as the retired-directory sweep
already runs once per process. At the once-per-process cleanup, sweep token
files whose process is dead and
publication lock files whose identity no longer exists. Remove a lock file
only under a protocol that cannot leave two processes holding different files
for one identity, and state that protocol in the IO-layer specification.

**Acceptance:** The artifact records the before and after measurements and
the store's operation count per preview; `tests/performance/` gains the
planning benchmark; a test proves a second lease of a verified generation
reads no part footer or schema, and that a generation whose files changed is
validated again; `tests/test_node_snapshot_cross_process.py` proves the
cleanup removes a dead process's token and an orphaned publication lock,
keeps a live process's token and a held lock, and that a publisher racing the
sweep still excludes a second publisher; the existing regression that a
preview's capture moves to the prepared signature after an input build passes
unchanged, as do the seed-plan, retention, and cross-process tests, because
none of this changes what is read or written.

**Owning specifications:** [caching](../caching/low-level.md#seed-plans);
[IO layer](../io-layer/low-level.md#node-output-snapshots).

**Dependencies:** None.

**Evidence:** `src/haute/_seed_plans.py` (`_Resolver`,
`open_resolved_seed_plan`, `_open_preview_seed_plan`);
`src/haute/_node_snapshots.py` (`__init__`, `_cleanup_retired`,
`_publication_lock`); `src/haute/_source_cache.py` (`_own_token`,
`_metadata_from_path`, `_cleanup_stale_staging`); `tests/test_seed_plans.py` (prepared-signature
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

**Checked for usage 20-Sep-2026, not yet for cost.** Full and cross joins do
occur — across this repository's graphs, 7 full and 10 cross against 105 left
and 35 inner — so this does not retire on nobody using it. What is still
unmeasured is whether any real graph pays the cost at a size where it matters:
the rescanning is real in the code, but a full join over two small sides costs
nothing worth days of partitioning work. Measure a full join and a cross join
at the sizes a real store actually holds before building this.

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

### CACHE-S24 — The assistant's bounded caches on the shared primitive
**Why:** One freshness proof and one bounded-cache primitive landed on
24-Sep-2026: every source kind asks `_json_shred/_source_proof.py` whether a
file changed (a native revision, or a settled stat where the platform has
none), one shared content signature serves Data Input and API Input snapshot
freshness, runtime identity and utility hashes, and `StatGatedCache`, the
signature memo and the parked Python-scan failures are built on `LRUCache`. Two
hand-rolled `OrderedDict` LRUs remain, both in the assistant, which another
lane owns: the plan records of `PlanStore` and the sessions of `SessionStore`.

**Plan:** Replace both with `LRUCache`: `get` already moves an entry to most
recently used, and `max_size` is the bound.

**Acceptance:** No `OrderedDict`-based LRU remains outside the shared primitive.

**Dependencies:** None.

**Owning specifications:** [caching](../caching/high-level.md);
[assistant](../assistant/high-level.md).

**Evidence:** `src/haute/assistant/_ops.py::PlanStore`;
`src/haute/assistant/_session.py::SessionStore`; `src/haute/_lru_cache.py::LRUCache`.

### CACHE-S25 — Hash the whole canonical node config
**Why:** The cache-identity framework declares a versioned field set for nine
consumers and classifies every node-config field as included or excluded,
failing on any unclassified field. Most of that classification exists to keep
editor-only state (column lists, schema warnings, step errors) out of cache
keys, because that state is stored inside the node config.

**Plan:** Once `PCFG-R08` moves editor state out of the config, key execution
caches on the complete canonical config and delete the per-field
classification. Keep the consumer contracts only where a consumer genuinely
needs a narrower identity, and say why in the specification.

**Acceptance:** Any change to a persisted config field changes the execution
identity without a classification table; editor-state changes do not; the
cache-identity tests are reduced to the remaining consumer contracts.

**Dependencies:** `PCFG-R08` (pipeline config).

**Owning specifications:** [caching](../caching/low-level.md).

**Evidence:** `src/haute/_cache.py::CacheConsumerContract`;
`src/haute/_cache.py::_classify_config_fields`;
`src/haute/_cache.py::validate_cache_config_field_classifications`;
`tests/test_cache_identity_contract.py`.

### CACHE-S26 — A retention policy for stored datasets
**Why:** Input snapshots and node outputs have no byte or count limit and no
automatic eviction; the former budgets were removed. Every admitted preview
captures its joins and materialising operations at full data, so disk use
grows with each wide pipeline a user previews, and only a manual clear in the
cache inventory reclaims it.

**Plan:** Decide whether unbounded retention is the product choice. If it is,
say so in the IO-layer specification and show the store's size where users
work, not only in the inventory pane. If it is not, specify a retention rule
(for example, least-recently-leased automatic generations beyond a
configurable size, never pinned or leased ones) and implement it.

**Acceptance:** The IO-layer specification states the retention rule or the
explicit absence of one, and the user can see the store's disk use without
opening the cache inventory.

**Dependencies:** None.

**Owning specifications:** [IO layer](../io-layer/high-level.md);
[caching](../caching/high-level.md).

**Evidence:** `src/haute/_source_cache.py`; `src/haute/_node_snapshots.py`;
`src/haute/routes/cache.py`.

### CACHE-S27 — The server chooses the snapshot build profile
**Why:** Before a preview, the browser checks each snapshot-backed input,
starts a build with the `lazy_sink` profile, and if the server answers 400
with a detail string starting `snapshot_build_unsupported`, retries with
`preview_eager`. The client chooses an execution profile by matching error
text. Bounded executions already prepare their inputs automatically on the
server, so the orchestration exists twice.

**Plan:** Let the build endpoint choose the build profile itself, and return a
typed outcome rather than an error to be string-matched. Decide whether the
browser pre-build is still needed once the server prepares inputs for
previews; if it is, keep it as a single call that starts or joins the server's
choice of build.

**Acceptance:** No frontend code inspects error-detail prefixes to choose a
profile; the build endpoint's choice is covered by a backend test for each
input format; preview preparation behaves as before.

**Dependencies:** None.

**Owning specifications:** [caching](../caching/high-level.md);
[frontend shared](../frontend-shared/low-level.md).

**Evidence:** `frontend/src/hooks/ensureInputSnapshots.ts::startBuild`;
`frontend/src/hooks/ensureInputSnapshots.ts::ensureInputSnapshots`;
`src/haute/_input_preparation.py::prepare_input_snapshots`;
`src/haute/routes/input_cache.py`.
