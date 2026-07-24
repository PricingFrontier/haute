# Frontend Modelling & Optimiser UI — High-Level Specification

## Purpose

This component configures, submits and explains modelling training and optimiser solves. It
combines domain-specific configuration controls with result panels for diagnostics, charts,
frontiers, ratebooks, exports and previewed optimiser scenarios.

## Scope

It owns the modelling and optimiser configuration/preview panels and their local chart, summary,
factor/ratebook and helper modules. Node-editor dispatch is documented by
[frontend-node-editors](../frontend-node-editors/high-level.md); job execution and persisted
results are supplied by API and result-store layers.

## Behaviour

- Modelling config selects an algorithm, feature/target/split/metric options and algorithm-specific
  controls, including GLM factors, regularisation and dispersion estimation; it submits training
  and shows progress, estimates, failures and result actions.
- Modelling preview exposes only result-backed tabs (summary, coefficients/relativities, loss,
  lift, residuals, feature importance, AVE and PDP) and resets selection when the result changes.
- Optimiser config selects input/objective/mode, banding/ratebook factors, constraints, solver
  options and frontier ranges; it can auto-range constraints and submit solves. Starting another
  auto-range request or unmounting best-effort cancels that auto-range job; this panel has no
  solve-cancel control.
- Optimiser preview renders summary, convergence, detail, frontier, ratebook/rates and export
  flows. The data preview groups and charts bounded scenario samples and can calculate statistics.

## Design rationale

Large config forms are split into focused subcomponents so algorithm/mode gates preserve hidden
configuration instead of destructively rewriting it. Asynchronous actions are sequence/abort
aware where a newer request can supersede an older request. Result tabs defer expensive work until
opened and use structured backend diagnostics when available.

## Interactions

This component consumes graph/node-result stores, API clients, background-job state and
`frontend/src/utils/banding.ts` for ratebook factor levels. It is routed from the node editor and
shares the preview frame/tab controls with
[frontend-preview-explore](../frontend-preview-explore/high-level.md).

## Failure model

Training, solve, MLflow, export, auto-range and materialisation failures are displayed in their
local action/result area. A locally aborted/superseded auto-range request suppresses an error,
while a terminal cancelled/superseded status returned by the server is shown in the auto-range
error area. Deliberately strict helper parsers throw for malformed numerical result contracts
rather than silently charting incorrect values.

## Polars backend contracts (0.6.0)

See [the remediation plan](../../trip/plans/F_0.6.0_polars-backend-remediation.plan.md).
Modelling and optimiser entry points will present the shared version-1 states `projected`,
`boundary`, `admitted_eager`, `rejected`, and `not_planned`, plus a distinct diagnostic-
unavailable state. Components use the authoritative shared mapping and never reinterpret internal
strategies. Missing/malformed required fields, unknown version-1 enum values, and unsupported
higher versions become diagnostic unavailable; unknown additive fields are ignored only within
version 1.

Rejections and boundaries name available blocking node/operator/profile, cost, reason, and
remediation, with bounded metric/provenance detail available secondarily and its
`available|unavailable|truncated` state preserved. Group-by may appear only as a RAM-admitted
`materialisation-boundary` or a typed HTTP 422 rejection; it is never shown as ordinary checked or
unprojected streaming execution. `not_planned`, rejection, and diagnostic unavailable are not
successful execution. Stable contract-error codes and named fields remain available to accessible
error copy.

## Approved change contract — 0.7.0 unified data-input UI consumption

Implementation follows
[`F_0.7.0_data-io-convergence.plan.md`](../../trip/plans/F_0.7.0_data-io-convergence.plan.md).

- Optimiser source selection remains scoped to connected upstream nodes and continues to support
  an explicit `data_input` id; it never assumes one global Data Input merely because
  `dataInput` is now the only authored tabular-source type.
- Column discovery for a retained Data Input uses the common guarded schema/preview response
  after provider resolution and optional Polars code. The UI does not inspect file extensions,
  Databricks cache paths, connection fields, or provider-specific configs to guess columns.
- Snapshot-required errors and direct/chunk capability diagnostics remain visible in estimate,
  solve, and auto-range states. The UI never triggers a cache build as a side effect of opening
  optimiser configuration or requesting columns.
- Existing sole-direct-banding fallback semantics remain unchanged. Removed Data Source/Data Sink
  types disappear from candidates, fixtures, guards, and tests without a compatibility mapping.

Acceptance covers multiple Data Input roots/direct parents, explicit selection, direct/cached
column discovery including post-input code, missing-snapshot diagnostics, no implicit build, and
absence of legacy candidates.
