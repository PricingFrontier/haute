# Frontend Trace UI — Low-Level Specification

## Module map

| File | Responsibility |
| --- | --- |
| `frontend/src/panels/TracePanel.tsx` | Ready/loading/error panel shell, trace header, focused/full story state, omissions, correlation diagnostics, export controls, and card list. |
| `frontend/src/panels/trace/traceGrouping.ts`, `frontend/src/panels/trace/traceStoryView.ts` | Target selection, pass-through collapsing and dependency/default-expansion sets. |
| `frontend/src/hooks/useTracing.ts` | Semantic-context-bound trace request state (`idle/loading/ready/error`), delayed progress, cancellation/recovery, and canvas trace/hover projection. |
| `frontend/src/trace/traceExport.ts` | Lazily loaded deterministic projection of a validated `TraceResult` into Markdown and CSV, reused by download, clipboard, and print without adding export-only code to the initial application bundle. |
| `frontend/src/trace/StepCard.tsx` | Expandable trace-step presentation and primary-detail selection. |
| `frontend/src/trace/CalculationHero.tsx`, `frontend/src/trace/ExpressionChain.tsx`, `frontend/src/trace/InputSourceTree.tsx` | Calculation/result hero, expression-chain rows and input-source hierarchy. |
| `frontend/src/trace/WaterfallChart.tsx`, `frontend/src/trace/WaterfallErrorAlert.tsx` | Contribution waterfall and explicit backend waterfall-error alert. |
| `frontend/src/trace/TraceDetail.tsx` | Shared detail frames, chips, alerts, callouts, sections and table primitives. |
| `frontend/src/trace/NodeDetailBlock.tsx` | Detail-type dispatcher and generic detail fallback. |
| `frontend/src/trace/BandingDetail.tsx`, `frontend/src/trace/bandingRows.ts` | Banding detail renderer and normalised display rows/ranges. |
| `frontend/src/trace/RatingStepDetail.tsx`, `frontend/src/trace/ratingStepHelpers.ts` | Rating detail renderer and table/output normalisers. |
| `frontend/src/trace/ModelScoreDetail.tsx`, `frontend/src/trace/modelScoreHelpers.ts` | Score/contribution view and typed model-score field extraction. The contribution ladder of a model with a non-identity `link` ends with a row for what the contributions sum to (base plus contributions), labelled by the explanation's `output_space`: "Raw score" (`raw_formula_val`, CatBoost), "Log-odds" (`log_odds`), "Log scale" (`log`), else "Linear predictor"; then a "Prediction" row naming the inverse link (for example "inverse logit", "exp") with the explanation's response-scale value (`model_prediction_value`, else `prediction_value`). For a classifier (`prediction_space` "probability") that row reads "Probability" without the column name, because the column holds the class label. Any other ladder ends with a single "Prediction" row. |
| `frontend/src/trace/OptimiserApplyDetail.tsx`, `frontend/src/trace/optimiserApplyHelpers.ts` | Online/ratebook/error optimiser detail and candidate/chart/score helpers. Below the online score line, "How the score's inputs were calculated" shows a derivation tree for the objective and each constraint column (a ratio constraint's numerator and denominator), from where the apply read it. The ratebook ladder ends with a "Combined factor collar" row: the `[min, max]` bounds, the product before the collar, the deployed value after it, and a "clipped" chip when the collar applied. |
| `frontend/src/trace/ScenarioExpanderDetail.tsx`, `frontend/src/trace/scenarioExpanderHelpers.ts` | Scenario expansion view and shape guards/row helpers. |
| `frontend/src/trace/LiveSwitchDetail.tsx`, `frontend/src/trace/liveSwitchHelpers.ts` | Live-switch detail and typed extraction. |
| `frontend/src/trace/DerivationTree.tsx`, `frontend/src/trace/derivationTreeHelpers.ts`, `frontend/src/trace/traceContext.ts` | How a value was calculated, followed across steps: `buildDerivationTree` builds a tree from the steps' `derivations` (a formula, a load, a rule such as a model or an optimiser, a value generated before its node's code rewrote it, a node not in the trace, or no source found, each labelled with the step number its card shows), cut off with a note on a repeat within one path or beyond 12 levels; `DerivationTree` renders it with the expression-chain row view, formulas open and rules collapsed; `ComputedHere` lists a step's own formulas. `traceContext.ts` holds `TraceStepsContext` (every step of the trace, for details inside the panel) and `TraceNavigationContext` (`focusStep`, `hoverStep`; inert outside a panel). A row's step label is a link ("Go to fill_na, step 9"), and pointing at a row rings its node. |
| `frontend/src/trace/traceHelpers.ts`, `frontend/src/trace/traceFormatting.ts`, `frontend/src/trace/traceOrigins.ts` | Waterfall/chain/source transformations, trace value formatting and origin classification. |

## Key types and data structures

- `TraceResult`, `TraceStep`, `TraceOmission`, `TraceRequestState` and the discriminated `TraceNodeDetail` union are defined in the
  consumed `frontend/src/types/trace.ts`. The renderer accepts backend extension fields but narrows
  known variants through each helper module.
- `CollapsedEntry` in `frontend/src/panels/trace/traceGrouping.ts` is either a `TraceStep` or a
  contiguous collapsed run. `WaterfallStep` and chain/input-source entries in
  `frontend/src/trace/traceHelpers.ts` are render-ready normalisations of optional trace data.
- `BandingTraceRow` and the detail helper outputs preserve the original row/order and selected
  values so display logic does not independently reinterpret backend calculation data.

## Control flow

1. `frontend/src/hooks/useTracing.ts` captures graph `structuralVersion`, active
   source, row limit, target, the explained preview's
   `seed_plan` (node, identity digest, and generation of each entry), the
   node-data epoch, row, column, and clicked values with each request, and
   sends that `seed_plan` so the trace reads the generations the preview did.
   A snapshot published, refreshed, or cleared therefore hides a displayed
   trace and aborts an in-flight one like any other semantic change. A 409
   refreshes the preview and asks for the row again. For
   `preview_seed_plan_expired` it says the cached data the preview read has
   changed. That notice belongs to the context without the seed plan and the
   epoch, so it outlives the refresh it started — which replaces the seed plan
   and may raise the epoch — and is hidden only by a change of node, graph,
   source, or row limit. `useTracing` also takes `traceFocusNodeId` from `useUIStore`:
   while a trace shows, that node is projected with `_traceFocused` and drawn with an
   accent ring. `TracePanel` sets it while a card or derivation row is pointed at (and
   clears it on unmount), except for a second after a link's click, while the scroll to the
   card moves content under a still pointer; a derivation row's step link calls `focusStep`, which shows the
   full trace when the focused one hides the step, opens and scrolls to its card and
   flashes it (`data-trace-focused`, also for a card that mounts with the request), rings
   the node and asks `useUIStore.requestTraceCentre` to centre it. `TraceViewFit`, inside
   the editor's `<ReactFlow>`, fits the canvas to the lineage steps (`column_relevant`)
   once per trace result and centres each requested node at the current zoom.
   On the canvas a step on the traced value's lineage
   (`column_relevant`) is active and shows the traced column's value, or in a column
   trace whose step does not hold it, the value of its first `contributed_columns`
   entry — a step that only carries the value's inputs (a join) shows none; a kept
   step off the lineage is neither active nor dimmed. A node the trace could not
   follow above a
   shared snapshot (an omission whose diagnostic names `seed_node_ids`) stays on the
   trace path — not dimmed, its connecting edges highlighted — without the
   active styling or value of a traced step; other omissions are dimmed like
   unrelated nodes. Requests require document execution capability
   (`capabilities?.can_execute === true`) and graph synchronisation
   (`graphSynchronized`); if either is missing, the trace does not start. In-flight
   requests verify document currency (`sourceFile`, `sourceRevision`, `loadStatus`,
   `graphSynchronized`, `can_execute`); a change in document identity mid-flight
   silently clears the trace state back to `idle`. A semantic change aborts and
   clears the state; requests resolving within the 500 ms progress delay show no
   loading chrome, while a request still pending after that delay enables compact
   progress/cancel UI.
2. `frontend/src/panels/TracePanel.tsx` derives the story key used for card
   identity, finds the last applicable producer for the traced column (the last step
   whose `contributed_columns` name it, so a joined-in table that also adds a shared key
   is never named; without one, the last step adding or modifying it),
   calculates dependency-preservation/default-expansion sets, and chooses a
   focused or full sequence. The focused story keeps the producer and every step
   whose `contributed_columns` is non-empty — every step computing a column the
   traced value depends on, however far upstream — and hides steps that only carry
   those columns; default expansion covers the producer and the non-bulk steps
   creating its direct inputs. The correlation-warning banner lists the diagnostics
   no omission card shows whose `severity` is not `info`: an informational diagnostic
   (a join that found no row, an aggregate, identical rows) appears only as its
   omission note or step label, and not at all for a node off the traced value's
   lineage. It interleaves typed omissions by topological
   rank: an omission that is a fact about the data is a neutral `role="note"` card —
   `join_no_match` (the join found no row) labelled "no match", and `aggregated_rows`
   (the row aggregates several input rows) labelled "aggregated" with its diagnostic's
   group keys and row count, and the snapshot reasons `seed_inputs_changed`,
   `seed_recompute_refused`, `seed_row_not_reproduced` and `seed_row_ambiguous` (a node
   not traced above the snapshot its diagnostic's message names) labelled "snapshot";
   every other omission, `seed_recompute_failed` and `ancestor_row_conflict` included,
   is a warning "trace gap" alert. With no traced column, it leaves the steps uncollapsed.
3. `collapsePassthroughs` groups hidden runs. If a focused target exists the UI removes the
   collapsed markers until the user asks for the full trace; otherwise the marker is a button that
   reveals the full trace.
4. `frontend/src/trace/StepCard.tsx` renders schema/value context — collapsed, the traced
   column's value, else the first two `contributed_columns`, else the first added and
   modified columns — dims a step whose `column_relevant` is false, labels a step whose
   `identical_row_count` is set "One of N identical rows" (the panel does not repeat its
   `identical_row_match` diagnostic as a correlation warning), and routes its expanded body to
   a calculation hero, expression/source view, `NodeDetailBlock`, or value table according to the
   data present. The expanded body also lists, under "Computed here", the step's formulas for
   the other columns the traced value depends on (the traced column's own formula is shown
   above it when the step has one), leaving out a substituted line that only restates the
   value (a formula copying a column). Its value table lists what the step did, not every
   column of its row: the traced column and the columns it added or modified, except one
   already under "Computed here"; a source, which adds every column it loads, lists in a
   column trace only the columns the traced value uses. The added/modified/passed-through
   counts still say what is not listed, and exports keep the full rows.
5. `frontend/src/trace/NodeDetailBlock.tsx` dispatches on `detail_type` (and optimiser status/mode).
   Detail components call their matching helpers before rendering; an unknown type is shown as
   formatted generic detail instead of disappearing.
6. `frontend/src/trace/CalculationHero.tsx` prefers backend waterfall entries. A calculation,
   chain entry or input source carrying `not_computable_reason` shows a note: "From the traced
   run: …" when `result_source` is `trace_execution`, otherwise "Not computed from this row: …",
   with the reason described by `describeNotComputable`. A value that was not computed is never
   replaced by the row's input value.
   `StepCard` owns the single backend-waterfall-error alert and never passes an
   error payload into the hero. The hero builds a local arithmetic waterfall
   only when backend entries are absent. It renders client-parsed conditional
   branch text without attaching the backend index to those rows; the backend
   `taken_branch`/`taken_branch_index` values are shown separately as selection
   metadata.
7. An export action dynamically imports `frontend/src/trace/traceExport.ts`;
   downloads use the shared browser helper, which removes the synthetic anchor
   immediately but revokes its object URL on the next task so the browser can
   start the download. Clipboard, download, and print failures remain visible
   in the mounted trace panel.

## Edge cases and invariants

- The traced column's `schema_diff` is the evidence used to select/retain steps; a trace with all
  pass-through steps preserves endpoints so the story does not collapse entirely.
- A card's relation badge says what the step did with the traced value: "creates",
  "modifies", or "value unchanged" for a step that only carries it (which may still expand or
  aggregate rows, so no badge speaks for rows in a column trace). A step off the value's
  lineage has none.
- The online optimiser's score line reads as arithmetic: each lambda term's sign is its
  operator, followed by the term's magnitude.
- An opaque/missing primary creator may be replaced by a later usable pass-through expression.
  Source-like/bulk origins are treated specially so a broad import does not dominate the default
  story.
- Source-like classification delegates to the canonical source-only node set:
  `dataInput`, `apiInput`, and `constant`. Production trace code contains no
  synthetic `source` or legacy `dataSource` node literal.
- The header handles a null traced column and supports row-id or row-index identity. Correlation
  warning keys include code/node/child/index to remain unique even with null IDs.
- Detail helpers distinguish absent optional fields from present wrong-typed/non-finite values;
  formatting has explicit null and non-finite representations. Optimiser candidate charts guard
  zero spans and omit candidates without usable coordinates.
- `showHidden` is panel state. It resets only when the owning `TracePanel`
  unmounts/remounts; card keys derived from `traceStoryKey` reset individual
  card expansion but do not reset panel state. Callers that replace a trace in
  place must therefore remount the panel if they require hidden-state reset.

## Error handling

Missing calculation data for an expression that should explain a value, backend waterfall errors,
typed omissions other than the notes (`join_no_match`, `aggregated_rows`, and the snapshot reasons), request failures, and
banding/model-score/optimiser/scenario/live-switch errors render persistent `role="alert"` UI. When the document lacks execution capability or the graph is
unsynchronised, trace requests do not start; a mid-flight document identity change silently resets
trace state to `idle` without raising an alert. A banding or model-score root `error`
suppresses all normal summary/result rows so placeholder nulls cannot look like
valid evidence. A 409 invalidates the preview and requires a new row selection
rather than retrying the same identity. Unknown node detail falls back to
generic JSON. Nothing in `frontend/src/trace/` or `frontend/src/panels/trace/` throws on a
malformed detail: `asBandingDetail` and `asModelScoreDetail` are plain casts, and the
extractors return null or absent markers instead (`bandingRowFromDetail` and
`formatBandingRange` return `null`, `modelScorePrediction` reports
`hasPrediction: false`, `buildWaterfallSteps` returns `null` for fewer than three or
non-numeric factors), so the renderers show an explicit alert or omission.

## Testing

`frontend/src/panels/__tests__/TracePanel.test.tsx` and
`frontend/src/panels/__tests__/TracePanel.enhanced.test.tsx` cover story rendering, target/focus
behaviour, alerts and detail variants. `frontend/src/panels/trace/__tests__/traceGrouping.test.ts`
tests grouping and preservation rules. Focused helper/error suites are under
`frontend/src/trace/__tests__/` for calculations, formatting, banding, model score and rating.
`frontend/src/hooks/__tests__/useTracing.test.ts` covers semantic request binding,
abort/clear races, progress, and recovery, including a node not traced above a snapshot kept on the
canvas trace path while other omissions dim, a completed trace hidden and an in-flight
one aborted with its late response discarded when the node-data epoch changes, a trace hidden
when the explained preview reads other generations, and `preview_seed_plan_expired` refreshing
the preview with a notice that survives that refresh and its captures until the node changes. `frontend/src/trace/__tests__/traceExport.test.ts`
covers the deterministic Markdown/CSV projection (each step's `derivations` included) and
filesystem-safe names used by clipboard, download, and print.
`frontend/src/trace/__tests__/derivationTreeHelpers.test.ts` covers the derivation tree: an
optimiser objective followed to the loaded values, a model's prediction as a rule, a node not
in the trace, a read with no source, several possible sources, unfollowed reads, a value
generated before its node's code, and the depth limit;
`frontend/src/panels/__tests__/TracePanel.test.tsx` renders it in the
online optimiser card and the "Computed here" list, follows a row's step link to its card
and the canvas focus, shows the full trace for a link to a card the focused one hides, and
rings a hovered card's node. `frontend/src/components/__tests__/TraceViewFit.test.tsx` pins
one lineage fit per trace and centring at the current zoom;
`frontend/src/hooks/__tests__/useTracing.test.ts` and
`frontend/src/nodes/__tests__/PipelineNode.test.tsx` pin the `_traceFocused` projection and
ring.
Some presentational primitives (`ExpressionChain`, `InputSourceTree`, `WaterfallChart`, and the
detail dispatcher) are principally covered through integration rendering rather than one test file
per module.

The browser-level preview-to-trace path is covered by
`frontend/e2e/core-flows.spec.ts`. `frontend/e2e/trace-render.benchmark.spec.ts` measures
trace-request-to-render latency for representative linear and multi-frame traces. The
trace-specific component and helper suites do not provide browser coverage for every specialised
detail renderer.
