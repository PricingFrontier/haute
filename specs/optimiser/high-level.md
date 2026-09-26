# Optimiser — High-Level Specification

## Purpose

The optimiser component lets a user turn a scored dataframe — quotes with candidate
scenario values and an objective/constraint set — into a priced decision. It supports two
problem shapes: **online** optimisation, which picks one winning scenario per quote from a
scored candidate grid, and **ratebook** optimisation, which solves banded rating factors
(e.g. age-band, region multipliers) against portfolio-level constraints instead of per-quote
choices. Both shapes are solved by the external `price-contour` library using a Lagrangian
(lambda-based) constrained-optimisation approach; this component is the orchestration layer
around that library — running it as a background job, exposing efficient-frontier exploration,
letting a user apply/preview/save/log the result, and later re-loading a saved result so a
pipeline can price new data with it.

It also owns post-hoc explainability: when a user clicks a priced row that came from an
`OPTIMISER_APPLY` pipeline node, this component reconstructs *why* that row got its value —
the full scenario ladder and its selection score for online results, or the factor-by-factor
multiplication ladder for ratebook results — without re-deriving optimiser mathematics that
belongs to `price-contour`.

## Scope

In scope:

- Starting, polling, and cancelling a background optimiser solve (online or ratebook mode).
- Fast, non-solving previews of the solver's input volume (`/estimate`) and background
  estimation of viable efficient-frontier threshold ranges (`/frontier/auto-range/start`).
- Computing an efficient frontier for a completed solve and letting a user select — or
  materialise, for ratebook — one of its points as the active result.
- Producing a bounded, size-limited per-quote apply preview for online results.
- Persisting a solved result as a JSON artifact on disk, or logging it (plus a frontier CSV
  and MLflow metrics/params) to MLflow.
- Loading a previously saved optimiser artifact — from a local file or from MLflow — for the
  `OPTIMISER_APPLY` pipeline node to price data with at runtime.
- Reconstructing an explainable trace for one clicked `OPTIMISER_APPLY` output row, in both
  online and ratebook modes.
- Enforcing response-size and solver-compute budgets so a single request cannot return an
  unbounded payload or pin a worker on an oversized frontier sweep.

Out of scope:

- Executing the pipeline graph that produces the optimiser's scored input — see
  [execution-engine](../execution-engine/high-level.md).
- The optimisation mathematics themselves (lambda solving, scenario scoring, ratio-constraint
  linearisation, ratebook coordinate descent) — these live in the external `price-contour`
  library, which this component treats as a black box with a documented contract (for the
  explainability surface specifically, see the
  [`with_explainer_columns` contract](low-level.md#withexplainercolumns-contract) in the
  low-level spec).
- Background job storage, lifecycle transitions, cancellation registries, and artifact TTL
  eviction — see [background-jobs](../background-jobs/high-level.md).
- MLflow tracking-URI resolution, experiment naming, and run-URL construction — see
  [mlflow-model-registry](../mlflow-model-registry/high-level.md).
- Producing the ratebook `banding_source` input (banding rules, factor columns) — see
  [rating](../rating/high-level.md).
- Request/response schema definitions and the FastAPI application shell — see
  [server-api](../server-api/high-level.md).
- Correlating a runtime output row back to its owning trace/node and rendering the resulting
  chart — see [tracing](../tracing/high-level.md) and
  [frontend-trace-ui](../frontend-trace-ui/high-level.md).
- The optimiser configuration/preview panels in the pipeline editor — see
  [frontend-modelling-optimiser-ui](../frontend-modelling-optimiser-ui/high-level.md).

## Behaviour

A solve is submitted asynchronously: the request returns a job id immediately, and the caller
polls a status endpoint until the job reaches a terminal state (`completed`, `error`,
`contract_error`, `memory_limited`, `cancelled`, `superseded`, or `timed_out`). There is one
process-wide blocking-solve slot, so a second solve is rejected while any solve is running,
even for a different graph/node. Estimates, auto-range jobs, and frontier recomputes are
non-blocking job types and do not reserve that global slot. Separately, a graph/node
single-flight key prevents a solve setup and a background auto-range setup from overlapping
for the same graph/node. A repeated background auto-range start with the same node id and graph
fingerprint returns the active job id (request-only chunk-size differences are not part of that
identity), while a conflicting operation receives HTTP 409.

Estimate, solve setup, and auto-range pipeline materialisation all consume the execution
facade's typed projection/strategy result for their request context. The same bounded
strategy diagnostics and deterministic feature provenance feed execution and admission;
the optimiser does not select a second planning policy behind the execution engine.

Solve setup and auto-range execute the pipeline in a killable spawn worker under a native memory
cap, as training preparation does: the worker writes the projected, validated solver input to a
setup-owned Parquet file (or returns the auto-range totals), and the server process builds the
quote grid from that file. A setup whose pipeline exceeds its memory budget therefore ends as a
typed `memory_limited` job instead of growing the server, and cancelling setup terminates the
worker. The solver itself still runs on a server thread against the grid. The explicit `thread`
compatibility mode runs the same steps on the job's thread. The input estimate runs on the warm
interactive worker pool that preview and trace use, under the estimate's admitted memory caps,
and a memory-limited estimate answers the typed 507. Auto-range's byte-budgeted chunk sizing,
which samples rows, also runs in the auto-range worker; the server process decides only from the
graph's structure whether a job can be chunked. No optimiser path reads pipeline rows in the
server process in process mode.

Once a solve completes, its lambdas, objective/constraint totals, convergence status, and (for
ratebook) factor tables are available as a job summary. From there a user can:

- Compute an efficient frontier — a grid of alternative constraint-threshold trade-off points —
  over explicit or auto-estimated absolute threshold ranges, and select one point as the active
  result without re-running the full solve. The server derives each returned point's summary
  once, when it builds the frontier, and sends it with the points; selecting a point shows
  exactly that summary, and the browser never derives one from a frontier row. Like the solve
  itself, the sweep runs as a background
  job: the request validates synchronously (runtime availability, range resolution, the
  compute-budget cap) and returns a pollable frontier job id, and the caller polls a separate
  frontier-status endpoint to a terminal state. Only one frontier sweep may be in flight per solve
  job at a time; a second `/frontier` request against the same solve job while one is running is
  rejected as a conflict. Admission is one locked check-and-create operation. A running sweep can
  be stopped through `POST /frontier/cancel/{job_id}`; timeout polling requests the same
  cooperative stop before publishing `timed_out`. A stopped sweep may never publish frontier data
  to the parent solve job, even if the underlying solver call returns later.
- Preview the online result as a capped table of per-quote selected scenarios (ratebook has no
  such per-quote view — see Failure model).
- Save the result to a JSON artifact on disk, or log it to MLflow together with a frontier CSV
  and the same artifact. Publishing names its target explicitly: no point index means the job's
  own solve (the anchor), a point index means that frontier point, and the server's currently
  selected frontier point is never an implicit target. Publishing never needs, holds or releases
  the solve's heavy in-memory state: the anchor publishes from its lightweight result summary and
  an MLflow summary computed once at solve completion, so Save, Log and Save again all succeed,
  in any order, including after the heavy-state retention window has expired. (A ratebook
  frontier point that was never materialised still needs the retained runtime to derive its
  factor tables.) A relative save path resolves against the project root, an existing file is
  only replaced when the request says so (otherwise a 409 conflict and nothing is written), and
  the response gives the project-relative POSIX path an `OPTIMISER_APPLY` node's
  `artifact_path` accepts. Every artifact records an audit trail: the solver settings the solve
  used, the constraints in force for the published target (a frontier point's own thresholds),
  cheap input provenance, and whether the node configuration had changed since the solve
  (`stale_at_publish`, reported by the caller).

A solve can also keep **analysis columns**: up to twelve per-quote columns, such as a region
or a channel, that exist only to break the result down by segment. The user picks the connected
frame they come from (`analysis_input`: any frame connected to the optimiser node, the data
input when unset) and the columns (`analysis_columns`). The solver never sees them. Setup
reduces them to one row per solved quote and keeps that side table, `quote_analysis.parquet`,
for the job's whole 24-hour lifetime; a quote the chosen frame has no row for is marked as
missing rather than dropped. Every solve, with or without analysis columns, also records its
complete **scenario grid** — each step index with the scenario value the solver scored there —
from the solver input at setup, and returns it on the solve result. The grid is the only source
for which adjustments were possible, whether the grid contains 1.0 and where its edges are;
nothing is inferred from the scenarios the quotes happened to choose.

A saved artifact is later loaded by an `OPTIMISER_APPLY` pipeline node to price new data:
either a local file (content-hash cached so an on-disk edit is always picked up, even a
same-second overwrite) or an MLflow run/registered-model artifact (cached by resolved
run/version).

When a user inspects a traced `OPTIMISER_APPLY` output row, the component reconstructs the
full decision: for online mode, every candidate scenario for that quote with its objective,
linearised constraint values, per-constraint lambda contributions, decision score, and which
candidate was selected versus which was the baseline; for ratebook mode, the ordered chain of
factor lookups (one per rating factor) with each factor's matched or "unseen" status and the
running product that produces the final value. In both cases the reconstruction is checked
against the actual output value — if the numbers don't reconcile, the trace request fails
rather than silently rendering a plausible-looking but wrong explanation.

Invariants:

- `data_input`, `banding_source`, and Optimiser Apply's `ratebook_input` name one connected
  incoming edge by its exact executable input name. Runtime resolution matches that name to the
  edge and then selects the edge's source frame; it never treats the value as a graph node id or
  falls back through the retired representation. The removed `scored_input` and `factors_input`
  fields are rejected at parse, save, execution, and code-generation boundaries rather than
  being migrated, ignored, or dropped from a sidecar. Distinct frames from one API Input therefore
  remain independently selectable. If a ratebook Optimiser selects its data and banding frames
  from two physical edges of the same multi-frame source, projection preserves those edges at
  full width rather than conflating their different column demands by source node id. A ratebook
  Optimiser Apply requires `ratebook_input` even
  when only one edge is connected; an empty selector never means "first edge". Online apply
  remains a single-primary-input operation and does not interpret `ratebook_input`.
- A completed solve is never persisted as an artifact if it contains a NaN or Infinity value
  anywhere in the payload, or (for ratebook) if it is missing its factor tables, the ordered
  `factor_dtypes` descriptor for any table, or the solve's `combined_factor_bounds` — an
  artifact is what production pricing reads from.
- A deployed ratebook never prices outside the range the solve scored. The solver priced each
  quote at the scenario-grid step nearest its factor product, clamped to the grid ends, so the
  artifact carries that grid's `[min, max]` as `combined_factor_bounds` and every apply path
  (preview, generated code, deploy scorer, trace) clamps the combined `optimised_factor` to it.
  The per-factor columns stay unclamped, and the factor-table CSV download states the collar
  for a rating engine to apply.
- Ratebook apply verifies each saved factor name and dtype descriptor against the apply-frame
  schema before constructing a lookup. A legacy artifact without dtype metadata or any mismatch
  fails as a typed 422/background contract error; it never becomes a neutral rating miss.
- Ratebook solves have no per-quote result dataframe; the apply-preview and per-quote trace
  affordances are only ever meaningful for online mode.
- Every capped/paginated response (apply preview, frontier points) states its true total count
  and whether it was truncated; nothing is silently dropped without saying so.
- A completed solve retains at most eight per-frontier-point apply artifacts. Materialising a
  ninth point evicts and deletes the oldest point artifact; returning to that point recomputes it.
  Concurrent point materialisations merge handles under the frontier-state lock, so one handle
  cannot overwrite and orphan another.
- Crash-surviving apply-result, ratebook-factor and quote-analysis directories carry distinct
  versioned Haute ownership markers. Startup cleanup can remove only stale marked direct
  children of those three dedicated roots; unmarked or foreign temporary data is never swept.
- Analysis columns never reach the solver. The quote grid is built from the solver columns
  only, so a solve with analysis columns has exactly the grid, totals and choices of the same
  solve without them.
- Each analysis column holds one value per quote. A quote with two different values of an
  analysis column (in the data input across its scenario rows, or across its rows in a separate
  analysis frame) fails setup with a named error; the first value is never picked silently.
- The quote-analysis side table is owned by solve setup until the completed job adopts it, and
  then by the job. It is deleted when setup or the solve fails, is cancelled, superseded or
  times out, when the job expires (24 hours after creation), or by startup reaping. Heavy-state
  slimming, the slimming after a user action, and frontier recompute never touch it. A reader
  holding a lease on it defers its deletion until the lease is released.
- The scenario grid recorded at setup is immutable for the job's lifetime and is never
  re-derived from chosen rows. Two grids such as `[0.8, 1.0, 1.2, 1.4]` and
  `[0.8, 0.95, 1.2, 1.4]` can produce identical per-quote choices, so the chosen rows cannot
  recover it.
- Precision: the solver ingests objective, constraint and scenario values as Float32; every
  total, reconciliation and breakdown haute reports accumulates those Float32 values in
  Float64, as price-contour does, so per-quote values summed over a breakdown equal the solved
  totals.
- Trace reconciliation either matches the real output exactly (within floating-point tolerance)
  or the trace request fails with a specific error — it never returns an approximate or
  partially-reconstructed explanation.

## Design rationale

Solves run on background threads polled through a shared job store rather than blocking the
request, matching the pattern used by [modelling](../modelling/high-level.md) training jobs —
solves and ratebook coordinate descent can run long enough that holding an HTTP connection open
is impractical, and polling lets the UI show live progress.

Heavy runtime objects a solve produces (the solver instance, the built `QuoteGrid`, the raw
solve result with its dataframe) are kept in the job store only for a short retention window,
then the job is slimmed to its API-facing summary. Anything a later request might need past
that window — the per-quote apply dataframe, the ratebook factor source — is persisted to a
parquet file under a dedicated temp-directory root instead of being kept in memory, and the
in-memory dataframe reference is explicitly cleared once that happens. This bounds worker
memory for long solve sessions without forcing every result to be re-solved on each interaction.

Grid construction itself avoids materialising the scored dataframe twice in Python. `_build_grid`
(`_optimiser_service.py`) sinks the scored lazy frame to a temporary Parquet file via
`bounded_sink` rather than collecting an eager `DataFrame` and handing it to the Rust grid
builder; `price_contour.build_grid_from_parquet_chunked` reads that file directly and constructs
the `QuoteGrid` without any Python-side `DataFrame` intermediate, so peak Python memory for a
solve is close to just the `QuoteGrid` handle rather than the scored dataframe plus a Rust copy
plus intermediate casts. The temp file is always removed in a `finally` block, on both success and
failure. Two alternatives were rejected: a chunked builder via `QuoteGridBuilder.append()` (still
requires Python to iterate and hold each chunk, and is not present as a runtime fallback) and
passing a `LazyFrame` straight to Rust (not supported by the `pyo3-polars` API in use). The
Parquet round-trip's disk-I/O cost (roughly 1-2s for large files) is accepted because it is small
relative to the memory it saves for large portfolios; for small datasets the round-trip overhead
is more noticeable and memory pressure isn't the bottleneck. A third alternative, Arrow IPC
instead of Parquet, was also rejected: lower serialisation overhead, but less widely supported and
without Parquet's column-level compression, whereas Parquet is already the standard interchange
format used elsewhere. The upstream lazy plan also projects down to only solver-relevant columns
before the sink, so the temporary Parquet file stays narrow regardless of how many columns the
pipeline produces upstream.

Analysis columns live in a haute-owned side table rather than travelling with the solve,
because price-contour's solve and `apply_from_grid` outputs carry no passthrough columns: the
solver's inputs stay exactly the solver columns, and a breakdown joins the side table to the
per-quote result on `quote_id`. The side table is written by streaming — a projected scan
reduced per quote and sunk to Parquet, never collected — in the same hard-capped setup worker
that writes the solver input, so the reduction's memory is bounded, typed as `memory_limited`
when it does not fit, and never added to the server process that holds the grid. Its
lifetime is the job's (24 hours), not the heavy state's (15 minutes idle), because a breakdown must still be possible after the solver
objects have been released. Readers take a lease instead of copying the file, so a job expiring
under a reader cannot delete the file mid-read.

Frontier auto-range and frontier compute share the same schema validation and column-projection
logic as the main solve. One auto-range job produces the estimate. When the upstream pipeline
chain is provably row-local it runs chunk by chunk: the pipeline executes up to the node below the
scenario expander, and each base chunk is expanded, scored and reduced before the next, so the
fully expanded scenario frame is never materialised. Its peak memory follows the chunk size
rather than the expanded frame: at a fixed chunk size, four times the scenarios raises the job's
peak memory by at most half, plus 64 MiB. A chain that cannot be proven row-local runs the same
job over the whole frame in bounded batches and records why chunking was lost. This keeps
large-scenario-count solves from requiring a full-memory pass just to suggest frontier ranges.
One streaming `group_by(quote).agg(min, max)` over the whole frame was measured as the
alternative and rejected: scenario expansion (an `explode`) and batch model scoring materialise
the expanded frame ahead of it, so its peak grew about threefold with four times the scenarios
and exceeded the default 2 GiB auto-range budget where the chunked job stayed near 1 GiB.

Frontier ranges are expressed as absolute threshold values, not multipliers of a baseline —
multiplier semantics are ambiguous once constraints have different natural scales, and the
`price-contour` frontier API itself is threshold-based. For the same reason, a frontier point
that is missing the numeric fields needed to reconstruct a full solve summary is treated as a
hard failure rather than being patched over with a fallback value — a partial, guessed summary
would misrepresent the actual solve. The failure surfaces once, where the server builds the
summary: the frontier is reported unavailable instead of being published.

Trace explainability deliberately does not reimplement `price-contour`'s scoring or
ratio-constraint linearisation math in Haute. Instead `price-contour` exposes deterministic
"explainer" columns (`decision_score`, `selected`, `is_baseline`, and per-constraint
`linearised_*`/`lambda_term_*` columns) on the same candidate frame the solver already scored,
and this component's job is purely to locate the right rows, assemble them into a UI-friendly
payload, and verify the result reconciles with the real output. Duplicating the linearisation
logic in two places would let the two silently drift apart; delegating it keeps exactly one
implementation of "how a scenario is scored."

Response-size and compute budgets (frontier point count, apply preview row count, frontier
solver-grid size) exist because both the frontier grid and the per-quote apply table can grow
unboundedly with problem size. The frontier compute cap is checked before either frontier entry
point invokes `price-contour`: both an explicit `/frontier` request and the optional frontier
computed as part of a solve use the same growth formula and cap.

Two further alternatives shape the frontier design. Treating frontier ranges as multipliers of
a baseline was rejected for the reason above — a multiplier is ambiguous once constraints have
different natural scales, and both the route's own API and the underlying `price-contour`
integration operate on absolute threshold values. Adding a frontend fallback for an incomplete
selected frontier point was rejected because a point missing its numeric summary fields cannot
produce a faithful solve summary — a guessed or partial one would misrepresent the actual solve.

A frontier sweep is hundreds of sequential re-solves at 2-3 constraints — the same class of
sustained CPU work as a full solve — so it was moved off the request thread onto a background job
for the same reason solves are: holding an HTTP connection open for minutes is impractical, and a
FastAPI worker thread blocked on it cannot serve other requests. This closes a regression class
where a future change to the heavy solver entrypoints (`_compute_frontier`, `_solve_online`,
`_solve_ratebook`) could quietly reintroduce inline request-thread execution: each is wrapped with
`require_solver_worker_context`, a decorator that raises immediately unless the call is running
inside `solver_worker_context()` — entered only by the background job runners (the solve worker
thread, the frontier sweep worker thread). The guard turns "someone called a heavy solver function
from a request handler" into an immediate, loud `RuntimeError` instead of a silent worker-pool
stall discovered later under load.

## Interactions

- [execution-engine](../execution-engine/high-level.md) — runs the pipeline graph up to the
  optimiser node (or, for streaming auto-range, up to an intermediate node) to produce the
  scored input dataframe and, for ratebook mode, the banding-source factor columns.
- [background-jobs](../background-jobs/high-level.md) — provides the job store, lifecycle state
  machine, cancellation registries, and artifact-cleaner/TTL eviction that every solve, apply,
  and frontier operation is built on.
- [mlflow-model-registry](../mlflow-model-registry/high-level.md) — resolves tracking URIs and
  experiment names for `mlflow/log`, and is the download path `OPTIMISER_APPLY` uses to fetch an
  artifact logged this way.
- [rating](../rating/high-level.md) — supplies the `banding_source` node and banding-rule
  configuration that ratebook mode reads its factor columns and level display order from; its
  dtype-faithful rating-key canonicalisation and dtype descriptor are reused so ratebook factor
  levels saved by this component match the join semantics `OPTIMISER_APPLY` uses at apply time.
- [tracing](../tracing/high-level.md) — calls into this component's explainability entry point
  when a user inspects an `OPTIMISER_APPLY` output row, and owns correlating that row back to
  its producing node in the first place.
- [server-api](../server-api/high-level.md) — defines the request/response schemas this
  component's routes use and hosts the shared FastAPI app.
- [frontend-modelling-optimiser-ui](../frontend-modelling-optimiser-ui/high-level.md) and
  [frontend-trace-ui](../frontend-trace-ui/high-level.md) — the consumers of, respectively, the
  solve/frontier/apply/save endpoints and the trace-explainability payload.

## Failure model

Configuration and input problems surface as 4xx errors with a specific, actionable message:
missing objective/mode/ratebook `factor_columns`, missing required columns in the scored data,
a non-string/categorical quote-id column, null quote ids, non-finite (NaN/Infinity) values in
any numeric solver column, a null value in any objective/constraint/scenario column (any dtype,
not just numeric — the solver's external aggregation has undefined behaviour on a null input), an
unresolvable or disconnected `data_input`, or (ratebook) an empty or missing banding source.
Analysis-column problems are 400s of the same kind: more than twelve analysis columns, a
duplicate or blank name, the quote-id column or a reserved `__haute_` name used as an analysis
column, an `analysis_input` that is not one exact connected input name, an analysis frame
without the configured quote-id column (or with one of an unsupported dtype), an analysis
column the chosen frame does not have, and an analysis column that varies within a quote
(`AnalysisColumnNotConstantError`, naming each column, how many quotes vary and one example
quote). A second solve is rejected whenever the process-wide solve slot is occupied. For one graph/node,
solve setup conflicts with a running background auto-range setup; a repeated background
auto-range start for the same graph fingerprint/node returns the existing job id instead of
creating, queuing, or superseding work.

A frontier request whose projected solver grid exceeds the compute budget is rejected before
the solver ever runs, naming both the projected size and the cap. A pipeline that cannot run in
bounded/streaming memory mode is rejected with a specific message rather than being forced
through and failing deep inside the executor. A memory-admission failure (the executor's own
budget controller refusing to reserve the memory a stage needs) surfaces as HTTP 507 on a
synchronous request path; when it occurs inside asynchronous solve/frontier/auto-range work, the
job instead transitions to `memory_limited` and exposes the failure through status polling. Once
a frontier sweep has passed its synchronous validation and started as a background job, any
failure inside the sweep itself (a lost race against a concurrent job-state change, an invalid
persisted apply-artifact handle, an unclassified exception) is reported through the frontier
job's own terminal status rather than as a synchronous HTTP error from the original `/frontier`
request — the caller only learns of it by polling the frontier-status endpoint to a terminal
state. Cancellation and timeout are terminal-state guards as well as user-visible labels: after
either transition, the worker checks the stop signal before any parent-job mutation and discards
its late result.

Unknown pipeline and grid-construction failures follow the accepted
[OPT-D01 error-detail policy](error-detail-policy.md): both are internal 500
outcomes with fixed client details and full server-side diagnostics. Explicit
configuration/schema/domain validation remains an actionable 4xx. Solver
failures are classified by their boundary: a typed input-adaptation failure is
`contract_error`, an exception translated at the external solver boundary is
an algorithm `error`, and an untyped orchestration/post-processing exception is
an unexpected `error`; a bare `ValueError` is never treated as user data solely
because of its Python type.

Ratebook mode has no per-quote result dataframe, so the apply-preview and apply-trace
affordances return an explicit 422 contract error naming the correct alternative (the factor
tables on the result, or re-applying a saved artifact through an `OPTIMISER_APPLY` node) rather
than either crashing on a missing attribute or silently returning a misleading result computed
some other way.

An optimiser artifact is never written with a non-finite value or (for ratebook) a missing
factor-table/dtype-contract section or combined-factor collar; the save/log request is rejected before the write, listing
every offending path in the payload. A valid server-owned handle whose artifact has been
removed or expired returns 410 with a stable re-run message. An invalid server-owned handle or
a present-but-corrupt artifact returns a sanitized 500; filesystem and parquet details remain
server-side. Applying a structurally valid legacy ratebook
artifact without `factor_dtypes`, or applying one to a changed factor dtype, raises
`RatingFactorDtypeContractError` before lookup construction.

Trace explainability is the one deliberate exception to fail-loud-by-default: every failure
inside the trace-enrichment path — a missing artifact source, an import error for the
`price-contour` library, a reconciliation mismatch between the reconstructed trace and the real
output value, a quote id or row that cannot be located — is caught, logged with full detail
server-side, and returned to the caller as a structured `status: "error"` payload naming the
mode and the error, rather than propagating an exception. This is scoped narrowly: the
underlying reconciliation checks it performs are themselves strict (an unreconciled trace is
always treated as an error, never patched over), but a broken trace is not allowed to break the
click that triggered it.

An empty ratebook factor ladder is not a successful explanation: because there is no factor
product to reconcile against the clicked output, trace enrichment returns its normal structured
`status: "error"` payload. Online trace correlation always reads the quote id from the
artifact-configured quote-id column, including when the output row also contains a different
literal `quote_id` field.

`HAUTE_SOLVER_TIMEOUT` is optional, but when present it must be a positive integer. A malformed,
zero, or negative value fails loudly as a server configuration error; it never disables timeouts.

The classification and disclosure rationale is recorded in the accepted
[OPT-D01 error-detail policy](error-detail-policy.md).

## Canonical frontier ranges

The optimiser follows the
[canonical-only format policy](../README.md#canonical-only-format-policy).
Every configured constraint range is represented only by
`frontier_ranges[constraint] = {"min": number, "max": number}`. There is no global-range reader,
fallback, mirroring, or migration in the service or optimiser UI.

Tests cover exact per-constraint validation and assert that frontend persistence
contains `frontier_ranges` only.
