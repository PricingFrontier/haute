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

## Current behaviour and remaining gaps

The shared store, resolver, seed plans, chunked writer and leased reads are
specified in the component specifications linked above. The remaining work
addresses duplicate cold computations, planning cost, write recipes and
measured limits of the existing consumers.

| Aim | Current behaviour | Gap |
|---|---|---|
| One store, every consumer | Node outputs, input snapshots (including each emitting table of a structured API Input, prepared automatically), and analyses share `.haute_cache`; previews, bounded runs, explicit builds, and traces run under one seed plan. | — |
| No duplicated runs | A bounded run seeds from any fresh covering generation and captures a join, fan-out, join feeder, batch Model Score, or consumed producer only where recomputing it costs more than the cache round trip, and records why it skipped the others; a preview seeds the same way and captures only the joins and costly full-input work it must compute in full. A chain of plain transforms is recomputed by every preview and bounded run by design, because recomputing it costs less than the cache round trip. Each capture publishes as soon as it is written, so a run that fails or is cancelled later keeps what it had already published. A preview served from the response cache reports its generations as seeded, and the canvas raises the node-data epoch only for a capture generation it has not seen, so a repeat preview costs no refetch. | — |
| Performant | Seeds stop the walk; captures are written once and read by everything below. Each part's digest is computed while it is written, so publication reads no part in full. An explicit build runs its execution and its target write at one chunk size, and a capture records the rows-per-part bound its write applied. A preview says when a node was not cached and how to fix it. | A capturing preview must finish inside the 120-second interactive timeout (`CACHE-S13`). |
| Memory safe | A frame Polars can slice at its single file or in-memory leaf is written a slice at a time; an edge join is written a driving chunk at a time against only the lookup rows those keys match; batches are one query each. A node with one input whose code is provably row-local, or a Data Input whose post-load code is, is written a slice of its input (or source scan) at a time where a capture, an explicit build or a Data Output writes it, so its memory does not grow with the input. A pass-through node carries its parent's recipe forward, and training preparation writes its prepared parquet through the same bounded writer, slicing the frame or the recipe's input and recording which. A heavy row's windows are index ranges, so they are disjoint and complete whatever order the engine returns rows in, and the writer refuses a row whose written count is not the count it expected. | — |
| Failures are recoverable | A corrupt generation is reported, never silently repaired, and names the node whose cache to clear or rebuild; a plan whose inputs moved stops, before collection and again if they move before a capture publishes. | — |

## Priorities

| Package | State | Priority | Outcome |
|---|---|---:|---|
| CACHE-S13 | Planned | P3 | A capturing preview finishes as a job instead of dying at the interactive timeout. Unproven: no measured preview approaches the timeout (re-measured 24-Sep-2026). |

## Planned improvements

`CACHE-S13` is measurement-gated:
no measured preview approaches its timeout. What is left is measured
against cost rather than shape. A package must not bypass
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
before it returns rows, inside the interactive worker's timeout (the pipeline
settings' pipeline time limit: 30 minutes by default, and 120 seconds when the
measurements below were taken). When a capture exceeds that,
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

Measured again on 24-Sep-2026 on `haute-demo`, the largest structured source to
hand (a 4.05 GB `quotes_1m.jsonl` whose API Input emits seven tables). Building
the seven tables took 50.2 seconds (peak RSS 1.52 GB, 131 MB of Parquet); the
browser runs that build as a job before it previews, so it is outside the
preview's timeout. The first capturing preview of `Inputs/live_join` (1M rows: a
group-by of one table left-joined onto another) then took 3.18 seconds (peak RSS
4.33 GB, a 104 MB capture), and the second 0.84 seconds. A preview whose
preparation has to build the tables itself (a client that skips the pre-build)
would spend about 50 of its 120 seconds on the build. No preview approaches the
timeout, so the package stays shelved.

**Plan:** Measure before building. A performance artifact records preview
time against capture size on the largest real pipeline, so the threshold
below is chosen from data rather than guessed, and the package stays shelved
while no preview approaches the ceiling.

Raising the timeout alone is not the cheap first step it looks like: the
route dispatches the preview to a worker before any plan exists, because the
plan is opened inside the worker (`routes/pipeline.py`, `timeout_seconds=`),
so the route cannot tell a capturing preview from any other without resolving
the plan itself, a resolution of its own (about 0.05 s of signing at 80 nodes). Giving
every shared-snapshot preview the sink timeout is possible but makes a hung
preview take 300 seconds to fail, so it is a decision to take deliberately,
not a free win.

When the measurements justify it, dispatch by a typed **capture-work
estimate**:
for each capture point, the row count of each effective input as the nearest
materialised point below it records it (a seed generation's or an input
snapshot's metadata, including an API Input table's, or a direct Parquet
footer), summed over the capture's inputs; the plan's estimate is the sum
over its capture points; an input with no recorded row count makes the
estimate `unavailable`. The route resolves the plan once and
dispatches on what that resolution reports,
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
no budget that refuses a capture.

**Evidence:** `src/haute/routes/pipeline.py` (`_preview_canonical_graph`,
`_pipeline_time_limit`); `src/haute/routes/_background_jobs.py`;
`src/haute/_seed_plans.py` (`SeedPlanDecision`); `src/haute/schemas.py`;
`frontend/src/hooks/usePipelineAPI.ts`; `frontend/src/stores/useNodeDataStore.ts`.
