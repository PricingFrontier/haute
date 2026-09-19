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
delivered and specified; their packages are retired from this roadmap. The
review found no data-corruption defect and confirmed the chunked join against
the native join on a lookup side whose hot key matched ten times the chunk
size. What remains is where the delivered behaviour is narrower than the aim
or where it will not hold at scale.

| Aim | Delivered | Gap |
|---|---|---|
| One store, every consumer | Node outputs, input snapshots, and analyses share `.haute_cache`; previews, bounded runs, explicit builds, and traces run under one seed plan. | API-input tables still live in the JSON cache (`CACHE-S08`). |
| No duplicated runs | A bounded run seeds from any fresh covering generation and captures its joins, fan-outs, join feeders, batch Model Scores, and consumed producers; a preview seeds the same way and captures the joins and materialising operations it must compute in full. A chain of plain transforms is recomputed by every preview and bounded run by design, because recomputing it costs less than the cache round trip. Each capture publishes as soon as it is written, so a run that fails or is cancelled later keeps what it had already published. | Two consumers that resolve the same cold capture point at the same time, or an explicit build and an automatic capture of one node, both compute it; the publication lock decides only who publishes (`CACHE-S19`). A repeat preview served from the response cache announces captures it did not make (`CACHE-S14`). |
| Performant | Seeds stop the walk; captures are written once and read by everything below. | Captures are chosen by memory behaviour rather than by recompute cost: cheap explodes and lag operations, sliceable join feeders, fan-outs over Parquet, and cheap consumed segments are written for no saving, and every publication re-reads what it wrote to hash it (`CACHE-S11`). Sixty-four generations and 20 GiB are shared with input snapshots, so captures evict each other or fall to `quota` (`CACHE-S12`). A capturing preview must finish inside the 120-second interactive timeout (`CACHE-S13`). Every preview prepares the graph several times and signs every lineage node per resolution (`CACHE-S17`). |
| Memory safe | A frame Polars can slice at its single file or in-memory leaf is written a slice at a time; an edge join is written a driving chunk at a time against only the lookup rows those keys match; batches are one query each. | Any other plan, including an ordinary filter over a large input, is written by one native streaming sink whose peak memory is Polars' to bound (`CACHE-S20`). A heavy-row window relies on unspecified order stability (`CACHE-S15`). Full joins rescan the base per lookup chunk and cross joins collect the lookup side (`CACHE-S18`). |
| Failures are recoverable | A corrupt generation is reported, never silently repaired; a plan whose inputs moved before collection stops. | The corrupt error reaches the user as store text with no pointer to Re-cache; a mid-run input change continues instead of stopping (`CACHE-S16`). |

## Priorities

| Package | State | Priority | Outcome |
|---|---|---:|---|
| CACHE-S11 | Planned | P2 | A node is captured only when recomputing it costs more than the cache round trip. |
| CACHE-S15 | Planned | P2 | Chunked writes read heavy-row windows in a proven order and chunk one build at one size. |
| CACHE-S14 | Planned | P2 | A preview announces only the snapshots it wrote. |
| CACHE-S12 | Planned | P2 | Node-output snapshots have their own quota, large enough for capture-everything use, and a visible quota outcome. |
| CACHE-S13 | Planned | P2 | A capturing preview finishes as a job instead of dying at the interactive timeout. |
| CACHE-S19 | Planned | P2 | Two consumers that need the same cold capture compute it once. |
| CACHE-S16 | Planned | P2 | Corrupt generations and mid-run input changes surface as typed, actionable failures. |
| CACHE-S20 | Planned | P2 | Row-local single-input nodes are written a slice of their input at a time. |
| CACHE-S17 | Planned | P3 | Planning and store housekeeping cost stays flat as graphs and stores grow. |
| CACHE-S18 | Planned | P3 | Full and cross joins are written with a bounded number of scans and a bounded part product. |
| CACHE-S08 | Deferred | P3 | Fold the API-input table cache into the shared snapshot store with per-table validity. |

## Planned improvements

Delivery order is `CACHE-S11` → `CACHE-S15` → `CACHE-S14` →
`CACHE-S12` → `CACHE-S13` → `CACHE-S19` → `CACHE-S16` → `CACHE-S20` →
`CACHE-S17` → `CACHE-S18`; `CACHE-S08` is deferred. A package must not bypass
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

### CACHE-S11 — Capture on recompute cost, not on memory

**Why:** The rule, decided on 19 September 2026, is that a node is cached
only when recomputing it costs more than writing and reading the cache.
Joins, group-bys, sorts, and batch model scoring meet it and are captured;
plain transforms do not and are not; the preview rule captures only what a
preview must compute in full anyway, and a chain of plain transforms is
recomputed by every preview and bounded run by design. Four places
approximate cost by something else. The capture set for code nodes is the
registry's materialisation-boundary list in `src/haute/_polars_operations.py`,
which classifies by streaming memory: `explode`, `shift`, `diff`, and
`pct_change` are cheap to redo and, for an explode, multiply rows, yet they
are captured; only `over` in that group is costly. A join feeder is captured
even when the chunked join could slice it in place, so a sliceable feeder is
written twice. A fan-out node is captured even when its whole upstream is a
Parquet scan or a generation plus row-local steps, where each branch would
just re-read Parquet. A consumed producer is captured whatever its segment
costs, although training and Data Output write the frame to their own file
anyway. And publication hashes every part with SHA-256, a full read of what
was just written, which alone outweighs a cheap transform.

**Plan:** Keep the preview rule. The cost decision stays in
`capture_points`, which runs on graph and store facts before any builder
runs, so every fact it uses is a planning-time fact and the capture set is
settled before `CACHE-S13` dispatches and `CACHE-S19` claims; nothing about a
built frame is consulted. Two classifications feed it, both separate from
`CACHE-S20`'s chunk-local safety proof, which answers a different question
(whether slicing the input preserves the result) and rejects operations that
are cheap to recompute. **Recompute cost** is a per-operation policy in the
registry, `costly_to_recompute`, set for `join`, `join_asof`, `group_by`,
`sort`, `unique`, `top_k`, `bottom_k`, and `over`, and clear for `explode`,
`reverse`, `shift`, `diff`, and `pct_change`; a code node is cheap when the
receiver-aware walk that already finds materialising calls resolves every
frame and expression call to a registered operation whose policy is
row-local or streaming, or a boundary without the flag, and costly when any
call is flagged, opaque, or unresolvable. Every builder node type declares
its own cost: Banding, the column step, renames, a blank-code Explore, and
the scenario expander are cheap; Rating Step, Model Score, and edge joins are
costly. **Slice transparency** is the planning-time counterpart of the
chunked writer's sliceability proof: a segment is slice-transparent when its
source is a Parquet scan, a snapshot generation, or a JSON table cache and
every step is a select, a rename, an unnest, or a row-local projection with
no filter, so the chunked join is known to read it in place. A **segment** is
the nodes from the nearest seed, capture point, or source below a node up to
and including it; a flat-file API Input source makes it costly. Precedence:
a segment with a costly step is captured under the existing kinds; a cheap,
slice-transparent segment is never captured as a feeder, fan-out, or
consumed producer; a cheap segment that is not slice-transparent (a filter
over Parquet) is captured only as a join feeder, because the chunked join
would stage it anyway and the capture is that write made shareable, and is
skipped as a fan-out or consumed producer. Batch Model Score and join
captures are unchanged. Compute each part's digest while it is written, in
`_Parts.sink`, `collect_and_write`, and `ensure_one` through a hashing
wrapper around the sink, and in the batch scorer's own Parquet writer for a
prewritten scored file, so publication reads footers and schemas but never
re-reads a part to hash it. Record each skipped capture point and its reason
(`cheap_segment`, `slice_transparent_feeder`) in the execution metrics.

**Acceptance:** `tests/test_seed_plans.py` proves, without executing any
builder (a builder registry that raises on call), that an explode-only code
node, a shift-only code node, a slice-transparent join feeder, a fan-out over
a generation plus a select, and a consumed producer over a cheap segment are
not captured; that a filter over Parquet feeding a join is captured as a
feeder and the same filter feeding a fan-out or consumed by training is not;
that a group-by, a window `over`, a fan-out over a flat-file API Input, and a
consumed producer whose segment holds a Rating Step or unresolvable code are
captured; and that the capture set is identical before and after the run's
claims are taken. The registry test proves the two policies are independent
and every boundary operation declares both, and, example by example, that
explode, reverse, shift, diff, and pct_change are cheap to recompute yet
rejected by the chunk-local allowlist, that group_by, sort, unique, top_k,
bottom_k, join, and over are costly, and that head, slice, and
with_row_index stay rejected by the allowlist whatever their cost; the
allowlist's own rejection tests in `CACHE-S20` are preserved. A builder test
proves every registered node type declares its recompute cost. `tests/test_node_snapshot_retention.py` proves a published
generation's digests match a fresh hash for a sunk, an ordered, an empty,
and a prewritten scored generation, and, through a counting file source,
that publication reads no part in full after writing it while footer and
schema reads are permitted. `tests/test_snapshot_seeding.py` proves a bounded
run over a cheap consumed segment reads the segment directly, writes its own
output bounded (`CACHE-S20`), and the next run recomputes the segment rather
than seeding.

**Owning specifications:** [caching](../caching/low-level.md#seed-plans)
(capture points, cheap segments); [execution engine](../execution-engine/low-level.md)
(operation policies, builder cost declarations);
[IO layer](../io-layer/low-level.md#node-output-snapshots) (publication
digests).

**Dependencies:** None.

**Evidence:** `src/haute/_seed_plans.py` (`capture_points`);
`src/haute/_polars_operations.py`; `src/haute/_builders.py` (registrations);
`src/haute/chunking.py` (`classify_chunk_local_polars_code`);
`src/haute/_chunked_writes.py` (`sliceable`, `_Parts.sink`);
`src/haute/_source_cache.py` (`describe_parts`);
`src/haute/_node_snapshots.py` (`_staged_metadata`);
`src/haute/_model_scorer.py` (`_batch_score_to_parquet`, the scorer's
Parquet writer).

### CACHE-S15 — Chunked writes: proven order, one chunk size

**Why:** `_ChunkJoin._write_heavy_row` in `src/haute/_chunked_writes.py`
writes one driving row whose matches exceed a part by re-executing an
unordered semi-join with successive slice offsets, and assumes the lookup
rows come back in the same order each time. Polars 1.44.2 happened to keep
that order in a 3M-row probe, but it is not a documented contract, and a
change in morsel scheduling would duplicate or drop matches silently. An
explicit node-data build also writes its target at the request's chunk size
(`src/haute/routes/_node_data_service.py`, `_build_node_snapshot`) while the
captures inside the same execution use the process default
(`current_streaming_chunk_size`), so one build chunks at two sizes.

**Plan:** Make the heavy-row semi-join ordered (`maintain_order="left"`, the
lookup side's own order) for that path only, or attach a row index to the
staged lookup and window by index range; either way the windows are disjoint
and complete by construction. Run the node-data worker's execution under
`temporary_streaming_chunk_size(request.streaming_chunk_size)` so captures and
the target write share one size, and record that size in the capture record.

**Acceptance:** `tests/test_chunked_writes.py` gains a regression with a hot
key matching several parts over a lookup side of several row groups and
asserts equality with the native join, run with a chunk size small enough that
the heavy-row path is exercised; `tests/test_node_data_routes.py` proves an
explicit build's captures report the request's chunk size.

**Owning specifications:** [IO layer](../io-layer/low-level.md) (chunked
writes); [server API](../server-api/low-level.md#node-data-builds).

**Dependencies:** None.

**Evidence:** `src/haute/_chunked_writes.py`;
`src/haute/routes/_node_data_service.py`; `src/haute/_execute_lazy.py`
(`_PlannedCaptures.capture`); `src/haute/_seed_plans.py`
(`SharedSnapshotCaptureRecord`).

### CACHE-S14 — A preview announces only what it wrote

**Why:** A preview served from the response cache replays the stored
`seed_plan`, whose entries keep the `captured` kind of the execution that
stored them (`src/haute/executor.py`, cache-hit branch). The frontend treats
any `captured` entry as "this request wrote a snapshot"
(`frontend/src/hooks/usePipelineAPI.ts`, `capturedSnapshots`) and raises the
node-data epoch, so every open consumer asks the backend for its point again
and the trace's semantic context renews, on every repeat preview.

**Plan:** On a response-cache hit the backend reports every listed generation
as `seeded`: the entry names what the response was computed from and what
this request leased and verified, and `captured` means this request published
it. Keep the frontend rule, and add a guard in `announceOwnCaptures` that
raises the epoch only for generation ids the store has not observed, so a
duplicated announcement is a no-op.

**Acceptance:** `tests/test_preview_snapshot_seeding.py` proves a repeat
preview lists its generations as `seeded`; the frontend epoch test proves a
response whose captured generations are already known does not raise the
epoch, and one whose generation is new does.

**Owning specifications:** [server API](../server-api/low-level.md) (preview
`seed_plan`); [frontend shared](../frontend-shared/low-level.md#the-shared-data-cache).

**Dependencies:** None.

**Evidence:** `src/haute/executor.py` (`_execute_graph_core`);
`frontend/src/hooks/usePipelineAPI.ts`; `frontend/src/hooks/useTracing.ts`;
`frontend/src/stores/useNodeDataStore.ts`.

### CACHE-S12 — A quota for node outputs

**Why:** `SourceCacheStore` bounds the whole store at 64 generations and
20 GiB (`HAUTE_INPUT_CACHE_MAX_GENERATIONS`, `HAUTE_INPUT_CACHE_MAX_BYTES`),
limits set for input snapshots. Node-output captures count against the same
numbers: input snapshots are never evicted by a node-output publication but
occupy the count, and once previews capture every join in every lineage per
source the remaining slots fill within a session. Admission then retires
current, unpinned node outputs LRU-first, so captures evict each other, and
when pinned and leased generations alone exceed the cap every capture falls to
the `quota` outcome with only an execution warning that nothing surfaces.

**Plan:** Give node outputs their own quota
(`HAUTE_NODE_SNAPSHOT_MAX_GENERATIONS`, default 512;
`HAUTE_NODE_SNAPSHOT_MAX_BYTES`, default 40 GiB) counted over node-output
generations only, leaving the input-snapshot numbers as they are; admission
and eviction candidates read one provider's generations, and the store's
byte and generation totals are kept per provider. Surface a `quota` capture
outcome as an execution warning the preview panel and job status show, naming
the node and the two remedies: clear an unused snapshot or raise the quota.
Add a settings entry that reports the store's usage against both quotas.

**Acceptance:** `tests/test_node_snapshot_retention.py` proves input
snapshots do not consume node-output slots and that node outputs do not
consume input-snapshot slots, and that a node-output admission never evicts
an input snapshot; a capture that falls to `quota` appears as a warning in the
preview response and the node-data job status, covered by a route test and a
frontend test.

**Owning specifications:** [IO layer](../io-layer/low-level.md#node-output-snapshots)
(quota, admission, eviction); [server API](../server-api/low-level.md)
(execution warnings); [frontend shared](../frontend-shared/low-level.md).

**Dependencies:** None.

**Evidence:** `src/haute/_source_cache.py` (`__init__`, `_generation_bytes`,
`_generation_count`); `src/haute/_node_snapshots.py`
(`_admit_node_output_locked`, `_eviction_candidates_locked`);
`src/haute/_execute_lazy.py` (`_PlannedCaptures._record`);
`frontend/src/panels/DataPreview.tsx`.

### CACHE-S13 — Capturing previews as jobs

**Why:** A preview whose plan captures a join writes the whole join to disk
before it returns rows, inside the interactive worker's timeout
(`HAUTE_PREVIEW_TIMEOUT`, default 120 seconds). A join over data of any size
exceeds that, the request answers 504, the worker is killed, and its staging
is discarded, so the unfinished capture is lost and the next preview repeats
it. Captures the worker had already published survive, because each capture
publishes as soon as it is written.

**Plan:** First, when the resolved plan has captures, bound the preview
worker by the sink timeout (`HAUTE_SINK_TIMEOUT`, default 300 seconds) rather
than the interactive one. Then dispatch by a typed **capture-work estimate**:
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

**Dependencies:** `CACHE-S12` so a job's captures have room.

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
which `CACHE-S11` leaves to the consumer, takes that path in every bounded
run. The memory-safety aim therefore holds for sliceable frames and edge
joins and is unproven for the most common node shape.

**Plan:** Measure first: a performance artifact records the native sink's peak
RSS for a filter, a filter with row-local derived columns, and an unnest over
a 10M-row input at the default chunk size. If any grows with the input, write
a chunk-local single-input node a slice of its input at a time: the engine
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
