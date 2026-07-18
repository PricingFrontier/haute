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
- Fast, non-solving previews of the solver's input volume (`/estimate`) and of viable
  efficient-frontier threshold ranges (`/frontier/auto-range`, foreground and background
  variants).
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
  [`with_explainer_columns` contract](low-level.md#with_explainer_columns-contract) in the
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
`contract_error`, `memory_limited`, `cancelled`, `superseded`, or `timed_out`). Only one
blocking solve (or frontier auto-range, or the setup phase that precedes either) may be active
for a given pipeline graph and optimiser node at a time; a second submission against the same
graph/node is rejected outright while one is in flight, and a *different* graph/node may run
concurrently.

Once a solve completes, its lambdas, objective/constraint totals, convergence status, and (for
ratebook) factor tables are available as a job summary. From there a user can:

- Compute an efficient frontier — a grid of alternative constraint-threshold trade-off points —
  over explicit or auto-estimated absolute threshold ranges, and select one point as the active
  result without re-running the full solve.
- Preview the online result as a capped table of per-quote selected scenarios (ratebook has no
  such per-quote view — see Failure model).
- Save the result to a JSON artifact on disk, or log it to MLflow together with a frontier CSV
  and the same artifact.

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

- A completed solve is never persisted as an artifact if it contains a NaN or Infinity value
  anywhere in the payload, or (for ratebook) if it is missing its factor tables — an artifact
  is what production pricing reads from.
- Ratebook solves have no per-quote result dataframe; the apply-preview and per-quote trace
  affordances are only ever meaningful for online mode.
- Every capped/paginated response (apply preview, frontier points) states its true total count
  and whether it was truncated; nothing is silently dropped without saying so.
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
requires Python to iterate and hold each chunk — kept as a fallback path, not the default) and
passing a `LazyFrame` straight to Rust (not supported by the `pyo3-polars` API in use). The
Parquet round-trip's disk-I/O cost (roughly 1-2s for large files) is accepted because it is small
relative to the memory it saves for large portfolios; for small datasets the round-trip overhead
is more noticeable and memory pressure isn't the bottleneck. A third alternative, Arrow IPC
instead of Parquet, was also rejected: lower serialisation overhead, but less widely supported and
without Parquet's column-level compression, whereas Parquet is already the standard interchange
format used elsewhere. The upstream lazy plan also projects down to only solver-relevant columns
before the sink, so the temporary Parquet file stays narrow regardless of how many columns the
pipeline produces upstream.

Frontier auto-range and frontier compute share the same schema validation and column-projection
logic as the main solve, and the auto-range estimate can itself run either as a classic
single-pass estimate or — when the upstream pipeline chain is provably row-local — as a
streaming, chunk-by-chunk estimate that never materialises the fully expanded scenario frame.
This keeps large-scenario-count solves from requiring a full-memory pass just to suggest
frontier ranges.

Frontier ranges are expressed as absolute threshold values, not multipliers of a baseline —
multiplier semantics are ambiguous once constraints have different natural scales, and the
`price-contour` frontier API itself is threshold-based. For the same reason, a frontier point
that is missing the numeric fields needed to reconstruct a full solve summary is treated as a
hard failure rather than being patched over with a fallback value — a partial, guessed summary
would misrepresent the actual solve.

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
unboundedly with problem size; the budgets are enforced at the request boundary — the frontier
compute cap in particular is checked *before* the solver runs, using the same growth formula
`price-contour` itself uses, so an oversized request is rejected with an actionable message
rather than dying inside the solver as an opaque failure.

Three further alternatives were considered and rejected during the frontier design. Treating
frontier ranges as multipliers of a baseline was rejected for the reason above — a multiplier is
ambiguous once constraints have different natural scales, and both the route's own API and the
underlying `price-contour` integration operate on absolute threshold values. Always returning a
pollable job id for the (short-lived) auto-range estimate was rejected because there is no polling
API for it, and keeping such a job in the shared store would leave unobservable job-store growth
with nothing to ever poll it down. Adding a frontend fallback for an incomplete selected frontier
point was rejected because a frontier point missing its numeric summary fields cannot produce a
faithful solve summary — a guessed or partial one would misrepresent the actual solve.

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
  runtime rating-key canonicalisation is reused so ratebook factor levels saved by this
  component match the join semantics `OPTIMISER_APPLY` uses at apply time.
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
any numeric solver column, an unresolvable or disconnected `data_input`, or (ratebook) an empty
or missing banding source. A second solve/auto-range request against the same graph and
optimiser node while one is already active is rejected as a conflict rather than queued or
silently superseded.

A frontier request whose projected solver grid exceeds the compute budget is rejected before
the solver ever runs, naming both the projected size and the cap. A pipeline that cannot run in
bounded/streaming memory mode is rejected with a specific message rather than being forced
through and failing deep inside the executor. A memory-admission failure (the executor's own
budget controller refusing to reserve the memory a stage needs) surfaces as a 507.

Ratebook mode has no per-quote result dataframe, so the apply-preview and apply-trace
affordances return an explicit 422 contract error naming the correct alternative (the factor
tables on the result, or re-applying a saved artifact through an `OPTIMISER_APPLY` node) rather
than either crashing on a missing attribute or silently returning a misleading result computed
some other way.

An optimiser artifact is never written with a non-finite value or (for ratebook) a missing
factor-table section; the save/log request is rejected before the write, listing every
offending path in the payload. Loading a previously saved artifact that is missing or corrupt
raises a 500 with a "re-run the solve to regenerate it" message rather than leaking the
underlying filesystem or parquet exception.

Trace explainability is the one deliberate exception to fail-loud-by-default: every failure
inside the trace-enrichment path — a missing artifact source, an import error for the
`price-contour` library, a reconciliation mismatch between the reconstructed trace and the real
output value, a quote id or row that cannot be located — is caught, logged with full detail
server-side, and returned to the caller as a structured `status: "error"` payload naming the
mode and the error, rather than propagating an exception. This is scoped narrowly: the
underlying reconciliation checks it performs are themselves strict (an unreconciled trace is
always treated as an error, never patched over), but a broken trace is not allowed to break the
click that triggered it.

> NOTE: `_load_apply_result_artifact`/`_load_ratebook_factors_artifact` always report a missing
> or corrupt artifact as a 500 ("Re-run the solve..."), even though a missing artifact caused by
> user action (e.g. a stale handle after the job's TTL evicted it) is arguably a 400/404-shaped
> problem rather than a server error. See [low-level.md](low-level.md#error-handling).
