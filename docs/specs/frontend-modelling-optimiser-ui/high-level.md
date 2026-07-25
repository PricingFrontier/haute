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

Remaining frontend modelling and optimiser improvement work is tracked in the
[frontend canvas roadmap](../../roadmap/frontend-canvas.md).
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

Remaining frontend modelling and optimiser improvement work is tracked in the
[frontend canvas roadmap](../../roadmap/frontend-canvas.md).

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

## Approved change contract — deterministic optimiser canvas journey

This contract implements the optimiser portions of ROAD-UI-02 and ROAD-UI-03 in the
[frontend canvas roadmap](../../roadmap/frontend-canvas.md).

- **Current limitation.** Existing browser coverage solves an optimiser, selects a frontier point,
  and applies it locally, but does not jointly prove constraint/range persistence, source identity,
  reload behaviour, and the MLflow selection boundary. A deleted or disconnected explicitly
  selected Banding source, or a selected source whose configured outputs contain no valid levels,
  can leave downstream choices silently incomplete.
- **Target behaviour.** One deterministic journey configures and reloads objective/constraint
  ranges, runs a fixed frontier, selects a point by backend `point_index`, applies that exact
  solution locally, and verifies the MLflow boundary through a deterministic intercepted contract
  rather than a live tracking service. The configured Banding source remains identified by node
  id. Missing explicit sources and zero-level configured outputs produce an accessible aggregated
  warning while healthy factor choices remain available.
- **Non-goals.** This change does not test optimiser quality, MLflow itself, network availability,
  or canvas pixel coordinates, and it does not broaden the existing sole-direct-Banding fallback.
- **Failure and compatibility.** No warning is shown for an unconfigured source or an ordinary
  empty graph. A non-blank explicit source id is considered missing when that node no longer
  exists as a directly connected Banding candidate. Point-index mismatches and malformed backend
  results continue to fail at their existing guarded boundaries.
- **Acceptance.** Component tests cover missing-source and mixed healthy/zero-level warnings.
  Browser evidence pins saved constraint/range fields across reload, frontier point identity,
  local apply identity, and the intercepted MLflow request/response identity without a live
  service.
