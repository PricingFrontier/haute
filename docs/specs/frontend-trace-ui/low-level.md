# Frontend Trace UI — Low-Level Specification

## Module map

| File | Responsibility |
| --- | --- |
| `frontend/src/panels/TracePanel.tsx` | Top-level panel component. Owns `showHidden` toggle state, derives target step / focused story / hidden count via `trace/traceGrouping.ts` and `trace/traceStoryView.ts`, renders the header and the correlation-diagnostics banner, and maps story entries to `StepCard`s or the collapsed-toggle button. |
| `frontend/src/panels/trace/traceGrouping.ts` | Pure functions that decide *which* steps matter for a column: `findTargetStep`, `groupTraceSteps` (unused by `TracePanel` directly but exported for reuse), `collapsePassthroughs`, `isSourceLikeTraceStep`, `buildFlowChain`. |
| `frontend/src/panels/trace/traceStoryView.ts` | Pure functions layered on top of `traceGrouping.ts` that decide *which steps to preserve/expand by default*: `traceStoryPreserveStepIds`, `defaultExpandedStepIds`, `targetStepDependencyColumns` (per-node-detail-type dependency extraction), `directInputSourceStepIds`, plus detail-type predicates (`hasPrimaryNodeDetail`, `hasRichModelScoreDetail`, `hasRichOptimiserApplyDetail`, etc.) re-used by `StepCard`. |
| `frontend/src/trace/StepCard.tsx` | Renders one step: collapsed header + key-value chips, and (when expanded) the calculation hero, expression/opaque/source-origin text, calculation text block, node-detail block, schema-diff summary, and fallback column-values table. |
| `frontend/src/trace/CalculationHero.tsx` | Renders the "unified calculation box" for a target step: branch-parses conditional expressions, resolves waterfall data (backend-provided or frontend-derived from a 3+-factor multiplicative expression), and renders the arithmetic/banding/conditional/opaque/source-origin body variants. |
| `frontend/src/trace/traceHelpers.ts` | Pure data-shaping helpers used by `CalculationHero`: `buildWaterfallSteps`, `resolveWaterfallProp`, `buildChainEntries`, `buildInputSourceEntries`, and their supporting types (`WaterfallStep`, `ChainBoxEntry`, `InputSourceBoxEntry`, ...). Kept out of `.tsx` files so `react-refresh/only-export-components` passes. |
| `frontend/src/trace/traceFormatting.ts` | Shared value/expression formatting: `formatSmartValue`, `formatResultValue`/`formatResultValueFull`/`formatResultValue2dp`, `formatDisplayExpression` (×/÷ substitution + truncation), `tabularNums` CSS. |
| `frontend/src/trace/traceOrigins.ts` | `isTraceSourceNodeType`, `isTraceGeneratedColumnOriginType`, `isTraceOriginStep` — classifies a step as the true "origin" of a column (a source node, or a generated-column node like `scenario_expander` that added the traced column) so `StepCard`/`CalculationHero` can show "Source node X" instead of a fabricated formula. |
| `frontend/src/trace/ExpressionChain.tsx` | `ExpressionChainRowContentView` (two-line "column = formula" / "value = substituted" text, or single-line "column = value") and `ExpressionChainRow` (adds the connector-line/dot chrome). Used for both intra-node chain entries and, via `InputSourceTree`, upstream input-source entries. |
| `frontend/src/trace/InputSourceTree.tsx` | Renders one level of nested upstream input sources beneath a chain row, reusing `ExpressionChainRowContentView`. |
| `frontend/src/trace/WaterfallChart.tsx` | Animated horizontal-bar waterfall (one bar per multiplicative factor + a total row). Bars grow from 0 on mount via `requestAnimationFrame`. |
| `frontend/src/trace/WaterfallErrorAlert.tsx` | `role="alert"` box for a backend-reported waterfall-build error. |
| `frontend/src/trace/TraceDetail.tsx` | Shared detail primitives reused by every node-specific detail block: `TraceCalculationFrame` (the outer bordered/coloured card), `TraceDetailPanel`, `TraceDetailChip` (tone variants), `TraceDetailAlert`, `TraceDetailCallout`, `TraceDetailSection`, `TraceDetailTable`/`TraceDetailTableRow` (CSS-grid tabular layout driven by a caller-supplied `gridClass`). |
| `frontend/src/trace/NodeDetailBlock.tsx` | Dispatches `TraceNodeDetail.detail_type` (+ `mode`/`status` for optimiser) to the correct detail component; falls back to a generic `JSON.stringify` panel for unrecognised detail types. |
| `frontend/src/trace/BandingDetail.tsx` + `bandingRows.ts` | Banding node detail: table of banded outputs (or a single summary row when there's exactly one), band range formatting, default-used flagging. |
| `frontend/src/trace/ModelScoreDetail.tsx` + `modelScoreHelpers.ts` | Model-score node detail: prediction value, feature-value list, and (when a SHAP/GLM explanation with contributions is present) a running contribution ladder from base value to prediction, with truncation callouts. |
| `frontend/src/trace/OptimiserApplyDetail.tsx` + `optimiserApplyHelpers.ts` | Three optimiser-apply variants: `OptimiserOnlineDetail` (selected-scenario callout + SVG candidate curve + candidate table), `OptimiserRatebookDetail` (base→final running-total ladder), `OptimiserApplyErrorDetail` (backend error surfaced verbatim). |
| `frontend/src/trace/RatingStepDetail.tsx` + `ratingStepHelpers.ts` | Rating-step node detail: one block per rating table (status, selected/default value, matched factors) plus combined-output blocks. |
| `frontend/src/trace/ScenarioExpanderDetail.tsx` + `scenarioExpanderHelpers.ts` | Scenario-expander node detail: scenario column/value/index chips and optional min/max/steps grid settings. |
| `frontend/src/trace/LiveSwitchDetail.tsx` + `liveSwitchHelpers.ts` | Live-switch node detail: active branch/scenario chips, pruned vs. available branch lists. |
| `frontend/src/hooks/useTracing.ts` | Owns the trace request lifecycle for the graph canvas: sequences/aborts `traceCell` calls, derives `traceResult`/`tracedCell`, toasts and clears on failure, and remaps trace step node IDs onto visible submodel-placeholder/port nodes. Separately computes the canvas's own trace/hover/zoom node and edge visual decoration (`nodesWithStatus`/`edgesWithTrace`) consumed by React Flow — not part of `TracePanel` or anything under `frontend/src/trace/`. |
| `frontend/src/types/trace.ts` | TypeScript mirror of the backend trace contract: `TraceResult`, `TraceStep`, `TraceSchemaDiff`, `TraceInputSource`, and the `TraceNodeDetail` discriminated union (`RatingStepNodeDetail`, `BandingNodeDetail`, `ModelScoreNodeDetail`, `OptimiserApplyNodeDetail`, `ScenarioExpanderNodeDetail`, `LiveSwitchNodeDetail`, `GenericTraceNodeDetail`). |

## Key types and data structures

All types below are defined in `frontend/src/types/trace.ts` unless noted.

- **`TraceResult`** — the full response for one row (+ optional column) trace: `target_node_id`,
  `row_index`, `column`, `output_value`, `steps: TraceStep[]`, row-ID info, node counts,
  `waterfall`, and optional `correlation_diagnostics`.
- **`TraceStep`** — one pipeline node's contribution to the row: `node_id`, `node_name`,
  `node_type`, `schema_diff` (added/removed/modified/passed column names), `input_values`/
  `output_values` (full column→value maps for the row), `column_relevant` (whether this step
  matters for the traced row at all), `execution_ms`, an optional `expression` (text + type:
  `arithmetic` / `conditional` / `banding` / `opaque` / ...), an optional `calculation`
  (`substituted_text`, `result_value`, `input_values`, `taken_branch_index`,
  `expression_chain`, `input_sources`), an optional `node_detail` (see below), and
  `row_lineage_type` used as the relevance badge when no column is traced.
- **`TraceNodeDetail`** — a discriminated union on `detail_type`, always intersected with
  `Record<string, unknown>` (the backend may include extra fields the union doesn't model; the
  `as`-cast helpers like `asBandingDetail` exist because narrowing through the intersection type
  is otherwise awkward). Variants: `rating_step`, `banding`, `model_score`, `optimiser_apply`
  (further split on `mode: "online" | "ratebook"` and `status: "error"`), `scenario_expander`,
  `live_switch`, and a catch-all `GenericTraceNodeDetail`.
- **`CollapsedEntry`** (`traceGrouping.ts`) — `TraceStep | { collapsed: TraceStep[] }`. The
  story list `TracePanel` renders is `CollapsedEntry[]`; a `{ collapsed }` entry renders as the
  "N pass-through nodes hidden" button instead of a `StepCard`.
- **`WaterfallStep`** (`traceHelpers.ts`) — one bar in the waterfall chart: `name`, `factor`,
  `runningValue`, `prevValue`, `direction: "positive" | "negative" | "neutral"`. Built either
  from `buildWaterfallSteps` (frontend-parsed arithmetic expression) or `resolveWaterfallProp`
  (backend-provided `WaterfallEntry[]`).
- **`ChainBoxEntry` / `InputSourceBoxEntry`** (`traceHelpers.ts`) — normalised row shape shared
  by intra-node expression-chain entries and upstream input-source entries so
  `ExpressionChainRow` can render both without caring which one it got.

**Invariant:** `TraceStep.schema_diff` is the single source of truth for "did this step touch
column X" — every dependency/focus/badge decision in `traceGrouping.ts` and `traceStoryView.ts`
reads from `columns_added` / `columns_modified` / `columns_passed` / `columns_removed`, never
from inspecting `output_values` directly to infer a diff.

## Control flow

**Producing the trace result (`useTracing`).** Everything below in this section describes
`TracePanel`'s own rendering pipeline once it already has a `TraceResult`. That result is
produced entirely outside this component tree, by the `useTracing` hook
(`frontend/src/hooks/useTracing.ts`), which the graph canvas instantiates and feeds
`nodes`/`edges`/`selectedNode`/hover state into:
- `handleCellClick(rowIndex, column, rowValues?)` resolves the graph payload to send
  (`resolveGraphFromRefs` over `graphRef`/`parentGraphRef`/`submodelsRef`/`preambleRef`), stamps
  a monotonic `traceRequestSeq`, aborts any in-flight request via `AbortController`, and calls
  `traceCell()`. A response is only applied if `traceRequestSeq` still matches the request that
  issued it — a slower, earlier click's response arriving after a faster, later one is silently
  discarded rather than overwriting the newer trace.
- A `status !== "ok"` envelope or a rejected `traceCell()` call both toast an error (preferring
  the API error's `.detail` string, falling back to `Error.message`, then `String(err)`) and
  call `clearTrace()`. `clearTrace()` also bumps `traceRequestSeq`, so a request still in flight
  when the user dismisses the trace can no longer land.
- `resolveTraceId` remaps a trace step's `node_id` to the node actually visible on the current
  canvas: child node IDs collapsed into a submodel placeholder resolve to `submodel__<name>`,
  and external parent node IDs resolve to their `port_in__`/`port_out__` proxy node — so a trace
  that touches nodes hidden inside a submodel (or outside the drilled-down view) still
  highlights the right placeholder/port on screen.
- `nodesWithStatus`/`edgesWithTrace` are the canvas-facing outputs: per-node/per-edge visual
  projections (trace-active/dimmed, hover-connected/dimmed, low-zoom contrast boost) derived
  from `traceResult`, `hoveredNodeId`, and the zoom transform, each held in a reference-stable
  cache (`projectionCache`/`edgeProjectionCache`) keyed by node/edge id and invalidated only
  when the source object or a computed flag actually changes, so React Flow's diffing can skip
  nodes/edges whose projection is unchanged. Motion (animated markers, drop-shadow filters, CSS
  transitions) is disabled — `traceMotionLite` — when the user prefers reduced motion or the
  graph exceeds `TRACE_MOTION_GRAPH_SIZE_LIMIT` nodes.

`TracePanel` itself never calls `traceCell` or touches these caches; it only consumes the
already-resolved `traceResult`/`tracedCell` and `onClose` that `useTracing` hands its caller.

1. `TracePanel` receives a `TraceResult` and `onClose` from its caller (the `useTracing` hook,
   via `App.tsx`). It computes a `storyKey` (`target_node_id\0row_index\0column`) used purely as
   a React re-mount key so switching traces resets `StepCard` internal `expanded` state.
2. `findTargetStep(trace.steps, trace.column)` (`traceGrouping.ts`) scans all steps for the
   *last* one that added or modified the traced column. If that step's expression is missing or
   `opaque`, and the pipeline's *final* step passes the column through with a *usable*
   expression, the final step is preferred instead — this is how the backend's "enrich the
   pass-through target step with the upstream creator's expression" convention surfaces on the
   frontend.
3. `traceStoryPreserveStepIds` (`traceStoryView.ts`) builds the set of step IDs that must stay
   visible in the focused view: the target step itself, `targetDependencyColumns` steps
   (structured-detail-aware: ratebook factor names, online-optimiser objective/quote/scenario/
   constraint columns, model feature columns, banding input columns, or — for plain
   expression/calculation steps — `expression.referenced_columns` ∪ `calculation.input_values`
   keys), and the node names referenced by the target step's `calculation.input_sources` (via
   `directInputSourceStepIds`, matched by `node_name` since `input_sources` doesn't carry
   `node_id`).
4. `collapsePassthroughs(trace.steps, trace.column, preserveStepIds, { collapseUnpreserved:
   targetStep != null })` (`traceGrouping.ts`) walks the steps in order and buckets each into
   "keep" or "pending collapse", flushing a `{ collapsed }` entry whenever a run of collapsible
   steps ends. When a target step exists, *any* non-preserved step collapses (including source
   nodes); with no target step (untraced-column view), only genuine passthroughs collapse and
   source-like steps (`isSourceLikeTraceStep`) are exempted. If literally every step is a
   passthrough, the first and last are still force-preserved as visual endpoints.
5. `TracePanel` derives `hiddenStepCount` (sum of `collapsed.length` across focused entries) and
   `storyEntries`: full `trace.steps` when `showHidden` is true, `focusedStoryEntries` with
   `{ collapsed }` markers stripped out when there's a target step and hidden is off, or the raw
   focused entries (including the toggle button) when there's no target step.
6. Each non-collapsed entry renders a `StepCard` keyed by `${storyKey}-${node_id}`, receiving
   `defaultExpanded` from `defaultExpandedStepIds` (same dependency logic as step 3, but also
   requiring `shouldDefaultExpandStep` — true for the target step itself, false for a step that
   is a "bulk source origin" i.e. a source-like step that only adds columns and isn't the
   target) and, only for the target step, the trace's `waterfall` payload.
7. Inside `StepCard`, expanding a card picks exactly one primary rendering path, in priority
   order: (a) `CalculationHero` when this is the target step, no rich node detail applies, and
   an expression or calculation exists; (b) a plain expression/opaque/source-origin text block
   when not showing the hero; (c) `NodeDetailBlock` when the step has `node_detail` and either
   the hero isn't shown, or the detail is a rating-step / a banding detail with secondary bounds
   info (banding can render alongside the hero because the hero shows the transform and the
   detail block shows bounds/status); (d) the plain column-values table as the final fallback
   when none of the above produced anything to show.
8. `NodeDetailBlock` re-dispatches on `detail_type` (and, for `optimiser_apply`, on `status`/
   `mode`) to the matching detail component; each detail component pulls its rows through a
   `*Helpers.ts` module rather than reading `node_detail` fields directly in JSX.
9. `CalculationHero` internally re-derives waterfall applicability independently of the
   panel-level focus logic: it prefers `resolveWaterfallProp(props.waterfall)` (backend-computed)
   and only falls back to `buildWaterfallSteps` (frontend-parsed `a * b * c` chain) when the
   expression type is `arithmetic`, a calculation exists, and no backend waterfall/error was
   given.

There is no client-side caching, memoisation beyond `useMemo` for the per-render derived sets,
or async control flow inside this component tree — fetching the trace and holding it in state is
entirely the caller's (`useTracing`) responsibility; `TracePanel` and everything under
`frontend/src/trace/` are synchronous, pure-render consumers of an already-resolved
`TraceResult`.

## Edge cases and invariants

- **No traced column** (`trace.column` is `null`): `findTargetStep` short-circuits to `null`,
  `preserveStepIds`/`expandedStepIds` are empty sets, and `focusedStoryEntries` is just
  `trace.steps` unmodified (no collapsing happens at all) — the panel shows a bare "Result ="
  chip instead of the column-specific header line.
- **All steps are passthroughs for the traced column**: `collapsePassthroughs` still preserves
  the first and last step as visual endpoints so the story never collapses to nothing but a
  single "N hidden" button.
- **Opaque expression with a pass-through target step**: when the primary creator step's
  expression is opaque (or missing) but the pipeline's final step both passes the column through
  and carries a better (non-opaque) expression or a calculation the primary step lacks,
  `findTargetStep` retargets to the final step — this only fires when the final step actually
  differs from the primary-found step and actually mentions the column in `columns_passed`.
- **Bulk source-origin steps** (`isBulkSourceOriginStep`): a source-like step that adds columns
  and modifies/removes/passes nothing collapses to its key-chip summary by default even when it
  is on the dependency path, unless it is itself the target step — this keeps wide raw-import
  nodes from dumping dozens of columns into the default view.
- **Rounded numeric display values**: `StepCard`'s `formatTraceValue` compares the 2-decimal
  display string against the full-precision string; when they differ it attaches a `title` and
  `aria-label` carrying the full value, so no precision is silently lost even though the on-page
  text is rounded.
- **Conditional branch matching** (`CalculationHero`): branch text is parsed with a
  `when ... then ...` / `otherwise ...` regex over both the expression text and the substituted
  text. The backend's `taken_branch_index` (on either `calculation` or the step itself) is
  authoritative when present; the frontend only falls back to "does this branch's substituted
  result text contain the result value verbatim" when no backend index was given, and an
  invisible a11y sentinel span only renders when the result string is actually found inside one
  of the branch texts (a numeric result that never appears verbatim in any textual branch label
  is treated as legitimately unmatched, not an error).
- **Row-correlation diagnostics** are deduplicated for React keys via
  `${code}-${node_id}-${child_node_id}-${index}`, tolerating `null` node IDs.
- **Multi-value optimiser candidate charts**: `optimiserChartPath` degrades gracefully when a
  candidate has neither a finite `objective` nor a finite `decision_score` (excluded from the
  point set) and when `xSpan`/`ySpan` would be zero (falls back to a span of `1` to avoid
  division by zero).
- **Stale trace responses are sequence-guarded, not (only) cancelled** (`useTracing`): a new
  click aborts the previous request's `AbortController`, but the authoritative guard against a
  stale response landing is the `traceRequestSeq` comparison in the `.then`/`.catch`
  handlers — a response can still resolve after its controller was aborted (e.g. mocked
  fetches in tests never observe the abort), and it is discarded by sequence number regardless.
- **Projection caches are pruned, not just grown** (`useTracing`): both `projectionCache`
  (nodes) and `edgeProjectionCache` (edges) delete entries for ids no longer present in the
  current `nodes`/`edges` arrays on every pass, so a long editing session with many node/edge
  deletions cannot leak cache entries unboundedly.

## Error handling

This module raises no exceptions of its own; "errors" are backend-reported states rendered as
UI, not thrown JS errors:

- `role="alert"` panels for: missing calculation on a non-opaque expression, missing calculation
  on an opaque expression, and a backend-reported optimiser-apply error
  (`OptimiserApplyErrorDetail`) or live-switch/scenario-expander `error` field.
- `WaterfallErrorAlert` (also `role="alert"`) for a backend-reported waterfall-build failure;
  falls back to "No details were provided by the backend." when the error string is empty rather
  than rendering a blank alert body.
- Any value that fails `formatValue`'s numeric/finite checks renders through
  `formatJsonSpecialValue`/`formatSmartValue`'s explicit `NaN`/non-finite/`null` branches rather
  than throwing or rendering `"undefined"`.
- Unrecognised `detail_type` values fall through to `NodeDetailBlock`'s generic
  `JSON.stringify(detail, null, 2)` panel rather than rendering nothing or throwing.
- `useTracing.handleCellClick` toasts (`useToastStore`) rather than throwing on both a
  non-`"ok"` trace envelope and a rejected `traceCell()` call, and calls `clearTrace()` in both
  cases so the panel never ends up open against a stale or errored trace.

## Testing

Tests live in two locations, split by what they exercise:

- `frontend/src/panels/trace/__tests__/traceGrouping.test.ts` — unit tests for the pure grouping
  logic (`findTargetStep`, `groupTraceSteps`, `collapsePassthroughs`, `buildFlowChain`) covering:
  last-modifier-wins semantics, opaque-primary/better-final-step retargeting, non-adjacent
  same-type grouping, consecutive vs. non-consecutive passthrough collapsing, source-type
  exemption from collapsing (including `dataSource`/`apiInput` variants), the
  `collapseUnpreserved` dependency-story mode, all-passthrough endpoint preservation, and
  columns-not-mentioned-at-all treated as passthrough.
- `frontend/src/panels/__tests__/TracePanel.test.tsx` — component-level tests (React Testing
  Library) covering the header (column/value/row-id/Row-N fallback/node-count/created-by),
  correlation-diagnostics banner singular/plural wording, close button, step ordering/indexing/
  execution-time display, per-node-detail-type rendering (banding source-value display,
  upstream banding context inside a focused ratebook trace, optimiser online/ratebook/error
  rendering, bulk-source-origin default-collapse, expander/source origin text), expand/collapse
  interaction and state restoration on re-toggle, schema-diff summaries, key-entry tag chips,
  full-vs-rounded-precision tooltips on both collapsed chips and expanded rows, no-column header
  variant, and relevant/irrelevant opacity.
- `frontend/src/panels/__tests__/TracePanel.enhanced.test.tsx` — further component-level
  coverage grouped by concern: expression display (arithmetic/conditional/opaque/long-text/
  empty-referenced-columns), calculation display (null/zero/string results, multiple inputs,
  calculation+expression together), node-detail rendering for every detail type including
  default-used flags, edge-boundary banding values, RustyStats-vs-response-prediction ladder
  totals, CatBoost SHAP running-score ladders, truncated-contribution callouts, generic
  unknown-detail-type JSON fallback, row-lineage-type badges (passthrough/created/filtered/
  aggregated/joined), and a "topological order" / "single story, no tabs" smoke check that pins
  the collapsed-tabs design decision.
- `frontend/src/trace/__tests__/calculationHero.errorUI.test.tsx` — targeted regression suite
  for the "loud failure over silent fallback" contract: missing-calculation-on-arithmetic alert,
  missing-calculation-on-opaque alert (distinct wording, not a silent "computed" label),
  backend waterfall-error rendering, paired with the "legitimate empty" counter-cases (raw
  source column → "source data", opaque-with-calculation → "computed", unmatched conditional
  branch → no a11y sentinel) so a future change can't accidentally treat a legitimate empty
  state as an error or vice versa. Also directly exercises `WaterfallErrorAlert`'s contract
  (verbatim message, `role="alert"`, non-empty fallback, no truncation).
- `frontend/src/trace/__tests__/traceHelpers.gaps.test.ts` — unit tests for `buildWaterfallSteps`
  (factor-count/missing/non-numeric guards, neutral-factor detection, whitespace trimming),
  `resolveWaterfallProp` (null/error/short-array/mapping cases), `buildChainEntries`, and
  `buildInputSourceEntries` (dedup-by-already-present-column, value/text fallbacks).
- `frontend/src/trace/__tests__/bandingRows.gaps.test.ts`,
  `frontend/src/trace/__tests__/modelScoreHelpers.gaps.test.ts`,
  `frontend/src/trace/__tests__/ratingStepHelpers.gaps.test.ts` — unit tests for each detail
  type's pure helper module: field-fallback precedence (e.g. `input_column` over `column`,
  `matched_band` over `selected_band`), null/undefined/wrong-type guards, and the
  summary-row-dedup logic in `bandingRows`.
- `frontend/src/trace/__tests__/traceFormatting.test.ts` — a single focused test confirming
  Haute's non-finite JSON sentinels render distinctly from `null`.

- `frontend/src/hooks/__tests__/useTracing.test.ts` — hook tests (`renderHook`) for the trace
  request lifecycle and canvas decoration, independent of `TracePanel`: initial null state,
  `clearTrace` resetting both fields, no-op when `selectedNode` is unset, success/non-ok-
  envelope/network/API-detail toasting, stale-response discarding when a newer click resolves
  first, node status/trace-dimming/hover-dimming flag derivation (asserting no `style.opacity`
  double-application), edge highlighting priority (trace over hover over zoomed-out contrast),
  reduced-motion and large-graph `traceMotionLite` degradation (including a graph crossing the
  size threshold mid-session), edge/node object-reference stability across hover-to-hover and
  trace-to-trace transitions, and a `buildEdgeAdjacency` suite confirming edge endpoints are
  read once and cached rather than re-read on every hover change.

**Known coverage gaps:** no dedicated test file exercises `ExpressionChain.tsx`,
`InputSourceTree.tsx`, `WaterfallChart.tsx`, `NodeDetailBlock.tsx`'s dispatch table, or
`ScenarioExpanderDetail.tsx`/`LiveSwitchDetail.tsx` directly — those components are covered only
indirectly through `TracePanel`/`TracePanel.enhanced` integration tests that render a full trace
and assert on the resulting DOM, not through isolated unit/component tests of the files
themselves.
