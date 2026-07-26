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
- Ratebook source and inferred factor columns change in one atomic config update. Constraint
  renames/removals migrate or remove their matching canonical `frontier_ranges` entry in the
  same update; range fields never inherit the removed global frontier bounds.
- Optimiser preview renders summary, convergence, detail, frontier, ratebook/rates and export
  flows. Selecting another frontier point clears stale materialised detail before enabling Save
  or MLflow actions, and structured API details are preferred on failures. The data preview
  groups and charts bounded scenario samples and can calculate statistics.

## Design rationale

Large config forms are split into focused subcomponents so algorithm/mode gates preserve hidden
configuration instead of destructively rewriting it. Multi-field invariants use one object
update because the production callback spreads the last committed config. Asynchronous actions
are sequence/abort aware where a newer request can supersede an older request. Result tabs defer
expensive work until opened and use structured backend diagnostics when available.

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

## Execution diagnostics

Modelling and optimiser action/result areas consume the shared guarded execution metrics.
Actionable memory pressure and rejected-strategy payloads name available profile, blocking
node/operator, cost, reason, and remediation, with technical details disclosed secondarily.
Other planner states do not create a success claim or pre-emptively disable submit. A missing or
unsupported diagnostic payload currently produces no secondary diagnostic; the primary
request/status error remains visible.

## Data-input consumption

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

Tests cover multiple Data Input roots/direct parents, explicit selection, direct/cached
column discovery including post-input code, missing-snapshot diagnostics, no implicit build, and
absence of legacy candidates.

## Optimiser canvas assurance

- One deterministic journey configures and reloads objective/constraint
  ranges, runs a fixed frontier, selects a point by backend `point_index`, applies that exact
  solution locally, and verifies the MLflow boundary through a deterministic intercepted contract
  rather than a live tracking service. The configured Banding source remains identified by node
  id. Missing explicit sources and zero-level configured outputs produce an accessible aggregated
  warning while healthy factor choices remain available.
- The journey does not test optimiser quality, MLflow itself, network availability,
  or canvas pixel coordinates, and it does not broaden the existing sole-direct-Banding fallback.
- No warning is shown for an unconfigured source or an ordinary
  empty graph. A non-blank explicit source id is considered missing when that node no longer
  exists as a directly connected Banding candidate. Point-index mismatches and malformed backend
  results continue to fail at their existing guarded boundaries.
- Component tests cover missing-source and mixed healthy/zero-level warnings.
  Browser evidence pins saved constraint/range fields across reload, frontier point identity,
  local apply identity, and the intercepted MLflow request/response identity without a live
  service.

## Frontier-range editor

The panel reads and writes only per-constraint `frontier_ranges`. It does not use global
`frontier_min`/`frontier_max` as display defaults and does not mirror a single constraint back to
those fields. Renaming or removing a constraint atomically migrates or removes its range entry.
Existing auto-range and validation behaviour remains on the canonical map.

Component tests assert that range edits preserve unrelated constraint ranges and persist only the
canonical map.
