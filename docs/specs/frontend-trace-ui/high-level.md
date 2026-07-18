# Frontend Trace UI — High-Level Specification

## Purpose

When a user clicks a cell in the graph canvas or preview grid, the backend computes a
row-level trace: the sequence of pipeline nodes that touched that row, and (when a specific
column was clicked) which of those nodes created, modified, or merely passed through that
column. The Frontend Trace UI renders that trace as a right-hand panel so a user can answer
"why does this cell have this value?" without reading pipeline configuration or backend logs.

The panel exists because pipelines can be long and highly branched, and most of that branching
is irrelevant to any single value. Presenting every node touched by a row buries the answer in
noise; the panel's central job is to find the one causal chain that actually produced the
clicked value and to make everything else optional.

## Scope

In scope:
- Rendering a `TraceResult` (row + optional column) as an ordered list of step cards.
- Deriving, from the raw step list, which step is responsible for the clicked column (the
  "target step") and which of the other steps are on its dependency path.
- Collapsing steps that are not part of that dependency path into a single "N pass-through
  nodes hidden" affordance, with a toggle to reveal the full trace.
- Rendering node-type-specific explanations (arithmetic/conditional/banding expressions,
  waterfall charts, rating table lookups, model scoring contribution ladders, optimiser
  apply candidates/ladders, scenario expander and live switch summaries) inside each step.
- Surfacing row-correlation ambiguity warnings returned alongside the trace.

Out of scope (owned elsewhere):
- Computing the trace itself, deciding which nodes/columns are "relevant", building
  `expression_chain` / `input_sources` / node-detail payloads, and detecting row-correlation
  ambiguity — all backend work, see [tracing](../tracing/high-level.md).
- Triggering a trace and holding it in state: the `useTracing` hook
  (`frontend/src/hooks/useTracing.ts`, instantiated by the graph canvas) sequences and aborts
  `traceCell` requests so a stale response can never overwrite a newer one, toasts and clears
  the trace on failure, and remaps trace step node IDs onto submodel placeholders / port
  proxies so a trace into a collapsed submodel or an external parent node still highlights the
  right node on the visible canvas — see [low-level.md](low-level.md#control-flow) for the
  request lifecycle and [frontend-graph-canvas](../frontend-graph-canvas/high-level.md) for how
  the canvas wires cell clicks into it. The same hook also computes the canvas's own trace/
  hover node and edge decoration (`nodesWithStatus`/`edgesWithTrace`) — a canvas-rendering
  concern this panel does not consume or influence — see
  [frontend-preview-explore](../frontend-preview-explore/high-level.md) for the preview grid's
  equivalent trigger path.
- The generic resizable/slide-in panel chrome (`PanelShell`, `PanelHeader`) and shared value
  formatting utilities — see [frontend-shared](../frontend-shared/high-level.md).
- Node colour/label metadata (`nodeTypeColors`, `nodeTypeLabels`, source-type classification)
  — owned by the shared node-type registry in frontend-shared.

## Behaviour

The panel opens as a right-side slide-in panel (via the shared `PanelShell`) and never occupies
the full window. Its header shows:
- "Trace" plus the traced column name, and the column's resulting value, when a column was
  clicked; otherwise just the row's overall output value.
- The row identity: either a business row-ID column/value pair, or a bare row index.
- The count of pipeline nodes included in the trace versus the pipeline's total node count.
- The node name that created/modified the traced column, when known.
- A toggle between "focused" and "full" trace views, shown only when at least one step has
  been collapsed.

Below the header, any row-correlation ambiguity diagnostics returned by the backend are shown
as a warning banner before the step list — the panel always tells the user when the row-level
join used to build the trace was ambiguous, rather than silently presenting a trace that may not
correspond 1:1 to the displayed row.

The step list itself is one card per pipeline node touched by the row, in pipeline execution
order:
- By default (**focused view**), only the target step and its structured dependencies are shown
  as cards; everything else collapses into a single dashed "N pass-through nodes hidden"
  button. Clicking it — or the "show full trace" link in the header — switches to full view.
- In **full view**, every step in the trace is shown as its own card, still in execution order.
- Each card shows the node name, a node-type badge (coloured per node type), a relevance badge
  (`creates` / `modifies` / `passes` for the traced column, or the row's lineage type when no
  column is traced), and the step's execution time. Cards for steps not relevant to the row are
  visually dimmed.
- Collapsed (unexpanded) cards show only the traced column's value (or the first couple of
  added/modified columns) as small tag chips. The target step, and steps on its dependency
  path, are expanded by default; large source nodes that merely add many raw columns default to
  collapsed even when part of the dependency path, unless they are themselves the target.
- Expanding a card reveals the node's full explanation: for a normal calculation this is a
  compact "calculation hero" showing the formula, substituted values, and result, using the
  presentation appropriate to the expression (arithmetic waterfall, conditional branch
  highlighting, banding lookup, or opaque/computed placeholder). For nodes with richer
  structured detail (rating table lookups, banding, model scoring, optimiser apply, scenario
  expander, live switch) the card instead renders that node type's dedicated detail view. As a
  last resort, when a step has no expression, no calculation, and no recognised structured
  detail, the card falls back to a plain list of every input/output column value.
- Numeric values that were rounded for display carry a tooltip/aria-label with the full-precision
  value.

## Design rationale

**Focused-by-default, full-trace-on-demand.** Pipelines commonly branch for reasons unrelated
to a given column (alternate scenarios, unrelated optimiser branches, bulk source imports).
Showing every node by default buries the causal chain. The panel instead asks each node detail
type to expose its own real dependency columns (ratebook factors, online-optimiser objective/
constraint columns, model feature columns, banding input columns) rather than hard-coding
per-node-type hiding rules, so new node types automatically integrate with focusing as long as
they declare their dependencies. See the retired TRACE_PANEL_EXPERIENCE_DESIGN doc (git
history) for the fuller design narrative and rejected alternatives (separate Calculation/Nodes
tabs; showing every connected node by default; hard-coded per-node hiding).

**One story, not two tabs.** Earlier iterations of the panel split "Calculation" and "Nodes"
into separate tabs; that was rejected because it forced the user to reconcile value, source, and
pipeline context across two navigation surfaces. All node types now share one linear list and
the same detail primitives (`TraceCalculationFrame`, `TraceDetailPanel`, `TraceDetailChip`, etc.)
so the panel reads as a single scrollable explanation regardless of node type.

**Loud failure over silent fallback.** Where the backend claims a value was computed (a
non-opaque expression, or an opaque expression) but did not attach the calculation data needed
to explain it, the panel renders a visible `role="alert"` message rather than silently falling
back to a generic view — a missing calculation is a backend contract violation, and hiding it
would make debugging traces harder, not easier (see Failure model).

**Node-detail rendering never re-derives a match.** Rating-step, banding, and every other
structured `node_detail` block (see the retired RATING_STEP_TRACE_DETAIL_DESIGN doc, git
history, for the rating-step-specific design discussion) is a pure renderer of fields the backend already
computed — `RatingStepDetail`/`ratingStepHelpers.ts` reads a table's `status`/`matched`/
`default_used` fields straight off the payload rather than re-running the lookup against
`input_values`/`output_values` itself. The backend has the runtime config and the row
snapshot needed to match execution semantics exactly (see
[tracing](../tracing/high-level.md) and [rating](../rating/high-level.md) for why the
matched/default verdict can never disagree with what the engine's own join produced); the
frontend rendering that same verdict a second, independently-derived way would risk the two
falling out of sync.

## Interactions

- **Depends on** [tracing](../tracing/high-level.md) for the `TraceResult` / `TraceStep` /
  `TraceNodeDetail` payload shape (mirrored in `frontend/src/types/trace.ts`), including which
  columns each step added/modified/passed, per-node structured detail, waterfall data, and
  correlation diagnostics.
- **Depends on** the `useTracing` hook (`frontend/src/hooks/useTracing.ts`) and its callers
  (graph canvas, preview grid) to trigger a trace and supply the `TraceResult` / `onClose`
  props: the hook owns the request sequencing/abort/toast lifecycle and the trace-step node-ID
  remapping described in [low-level.md](low-level.md#control-flow); this panel only receives
  its already-resolved output — see
  [frontend-graph-canvas](../frontend-graph-canvas/high-level.md) and
  [frontend-preview-explore](../frontend-preview-explore/high-level.md).
- **Depends on** [frontend-shared](../frontend-shared/high-level.md) for `PanelShell`/
  `PanelHeader` chrome, `formatValue`/`formatTrace` value formatting, node-type colour/label
  metadata, and the shared chart colour palette.
- Structured node details echo domain concepts owned by
  [rating](../rating/high-level.md) (rating table lookups) and the optimiser/model-scoring
  domains; the panel only renders the data those domains already computed, it does not
  reinterpret it.
- **Depended on by** nothing else in the frontend — the trace panel is a leaf UI surface reached
  only from a cell click.

## Failure model

The panel never lets an internal `undefined`/`null` value pass through as if it were a
legitimate "nothing to show" state without visual distinction from an actual backend error:

- If a node's expression is non-opaque (the backend claims to expose a formula) but no
  `calculation` payload was returned, the step renders a `role="alert"` box: "Calculation data
  not available for this step."
- If a node's expression is opaque (the backend claims a value was computed without exposing
  the formula) but no `calculation` payload was returned either, the step renders a distinct
  alert: "Calculation is not available for this opaque expression." This is treated as a
  backend misconfiguration, not a normal opaque node, precisely because opaque nodes are
  expected to still carry a calculation result.
- If the backend reports a structured waterfall-build error (row had multiple passes, or another
  reason a waterfall isn't well-defined), the step renders `WaterfallErrorAlert` with the
  backend's error message, never a silently empty chart.
- If row-correlation between trace steps was ambiguous, the diagnostics banner at the top of the
  panel is never suppressed — even a single diagnostic is shown, pluralised correctly.
- Genuinely benign "nothing to show" cases are distinguished from errors: a raw source column
  with no expression and no calculation renders italic "source data" (not an alert); an opaque
  node that *does* have a calculation renders italic "computed" (not an alert); a step with no
  richer detail falls back to the plain column-values table rather than an empty pane.
