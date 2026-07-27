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
- Modelling and optimiser action areas render actionable memory-pressure and
  rejected-strategy diagnostics with profile, blocking node/operator, cost,
  reason, and remediation when the guarded payload supplies them. Other,
  missing, or unsupported planner detail never becomes an invented success or
  pre-emptive submit gate; the primary request/status error remains visible.
- Optimiser input selection stays scoped to connected upstream nodes and can
  explicitly select a `dataInput`. Its columns come from the guarded
  post-provider/post-Polars schema or preview contract; the panel does not
  inspect provider config or trigger a snapshot build merely to discover them.
  Snapshot/capability failures remain visible in estimate, solve, and
  auto-range states.
- An explicit Banding source that is no longer directly connected and
  recognised configured outputs with zero valid levels are combined into one
  accessible warning, while healthy factors remain selectable. An
  unconfigured source or ordinary empty graph does not warn, and the
  exactly-one-direct-Banding fallback is unchanged.

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
