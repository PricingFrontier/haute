# Frontend Trace UI — High-Level Specification

## Purpose

The trace panel explains how one output cell was produced. It converts an already-resolved
backend trace into an ordered story of pipeline steps, calculation context, specialised node
details, dependencies, and waterfall/conditional evidence.

## Scope

This component owns synchronous trace presentation and normalisation helpers under
`frontend/src/trace/`, plus the panel wrapper. Requesting, cancelling and projecting a trace onto
the canvas are owned by the tracing hook/canvas layer. The backend owns trace computation.

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

## Design rationale

Trace output can be very wide and repetitive. The story view preserves the target and its direct
dependencies while compressing irrelevant pass-through steps, retaining enough provenance to
explain the result without concealing the full trace behind an opaque summary.

## Interactions

It receives `TraceResult` data and close/cell state from the tracing hook and is triggered from
[frontend-preview-explore](../frontend-preview-explore/high-level.md). Node-specific configuration
concepts originate in [frontend-node-editors](../frontend-node-editors/high-level.md).

## Failure model

Backend-reported node/waterfall/calculation problems render visible alerts. Missing data required
to explain a non-opaque calculation also renders an alert. Unknown detail shapes use the generic
fallback; helper type guards avoid silently treating malformed optional values as valid numbers.
