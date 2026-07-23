# Frontend Trace UI — High-Level Specification

## Purpose

The trace panel explains how one output cell was produced. It converts an already-resolved
backend trace into an ordered story of pipeline steps, calculation context, specialised node
details, dependencies, and waterfall/conditional evidence.

## Scope

This component owns trace request lifecycle, canvas projection, synchronous presentation and
normalisation helpers under `frontend/src/trace/`, the panel wrapper, and deterministic export of
the validated response snapshot. The backend owns trace computation and response provenance.

## Behaviour

- The panel identifies the relevant producing step, collapses non-essential pass-through work,
  and lets the user reveal hidden steps or expand individual cards.
- A card presents schema changes, inputs/outputs, expressions and calculations, with a specialised
  banding, rating, model-score, optimiser-apply, scenario-expander or live-switch detail when
  the trace supplies one. Unknown detail types remain inspectable as generic JSON.
- Arithmetic and backend-provided waterfall information render running contributions; conditional
  calculation context identifies the selected branch when the trace has enough information.
- All controls and alerts use labels/roles appropriate to their state. Numeric/null/non-finite
  values are formatted explicitly rather than being confused with missing text.
- A trace request is bound to graph `structuralVersion`, source, row limit, target, row, column,
  and clicked values. Any semantic-context change aborts and clears it; position-only canvas
  movement does not. Fast requests transition directly to the result, while only requests still
  pending beyond a short measured threshold show compact progress and cancellation.
- Request failures remain visible and actionable. A 409 identity failure refreshes/invalidates the
  preview and requires a fresh row selection; it never automatically retries the same unprovable
  row. Backend omissions and waterfall failures remain visible regardless of specialised detail.
- Markdown, CSV, clipboard and print output are generated from the exact validated `TraceResult`
  displayed in the panel, never from a second graph execution.

## Design rationale

Trace output can be very wide and repetitive. The story view preserves the target and its direct
dependencies while compressing irrelevant pass-through steps, retaining enough provenance to
explain the result without concealing the full trace behind an opaque summary.

## Interactions

It receives `TraceResult` data and close/cell state from the tracing hook and is triggered from
[frontend-preview-explore](../frontend-preview-explore/high-level.md). Node-specific configuration
concepts originate in [frontend-node-editors](../frontend-node-editors/high-level.md).

## Failure model

Backend-reported request/node/waterfall/calculation problems and typed omissions render persistent
visible alerts or omission cards. Missing data required to explain a non-opaque calculation also
renders an alert. Unknown detail shapes use the generic fallback; helper type guards avoid silently
treating malformed optional values as valid numbers.
