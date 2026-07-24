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
| `frontend/src/trace/ModelScoreDetail.tsx`, `frontend/src/trace/modelScoreHelpers.ts` | Score/contribution view and typed model-score field extraction. |
| `frontend/src/trace/OptimiserApplyDetail.tsx`, `frontend/src/trace/optimiserApplyHelpers.ts` | Online/ratebook/error optimiser detail and candidate/chart/score helpers. |
| `frontend/src/trace/ScenarioExpanderDetail.tsx`, `frontend/src/trace/scenarioExpanderHelpers.ts` | Scenario expansion view and shape guards/row helpers. |
| `frontend/src/trace/LiveSwitchDetail.tsx`, `frontend/src/trace/liveSwitchHelpers.ts` | Live-switch detail and typed extraction. |
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

1. `frontend/src/hooks/useTracing.ts` captures graph `structuralVersion`, active source, row limit,
   target, row, column, and clicked values with each request. A semantic change aborts and clears
   the state; requests resolving within the 500 ms progress delay show no loading
   chrome, while a request still pending after that delay enables compact
   progress/cancel UI.
2. `frontend/src/panels/TracePanel.tsx` derives a remount key, finds the last applicable producer
   for the traced column, calculates dependency-preservation/default-expansion sets, and chooses a
   focused or full sequence. It interleaves typed omissions by topological rank. With no traced
   column, it leaves the steps uncollapsed.
3. `collapsePassthroughs` groups hidden runs. If a focused target exists the UI removes the
   collapsed markers until the user asks for the full trace; otherwise the marker is a button that
   reveals the full trace.
4. `frontend/src/trace/StepCard.tsx` renders schema/value context and routes its expanded body to
   a calculation hero, expression/source view, `NodeDetailBlock`, or value table according to the
   data present.
5. `frontend/src/trace/NodeDetailBlock.tsx` dispatches on `detail_type` (and optimiser status/mode).
   Detail components call their matching helpers before rendering; an unknown type is shown as
   formatted generic detail instead of disappearing.
6. `frontend/src/trace/CalculationHero.tsx` prefers backend waterfall entries. It builds a local
   arithmetic waterfall only when appropriate backend data/error is absent, and renders branch
   information from calculation/expression context.
7. An export action dynamically imports `frontend/src/trace/traceExport.ts`; clipboard, download,
   and print failures remain visible in the mounted trace panel.

## Edge cases and invariants

- The traced column's `schema_diff` is the evidence used to select/retain steps; a trace with all
  pass-through steps preserves endpoints so the story does not collapse entirely.
- An opaque/missing primary creator may be replaced by a later usable pass-through expression.
  Source-like/bulk origins are treated specially so a broad import does not dominate the default
  story.
- The header handles a null traced column and supports row-id or row-index identity. Correlation
  warning keys include code/node/child/index to remain unique even with null IDs.
- Detail helpers distinguish absent optional fields from present wrong-typed/non-finite values;
  formatting has explicit null and non-finite representations. Optimiser candidate charts guard
  zero spans and omit candidates without usable coordinates.
- `showHidden` is panel state and resets when the trace identity changes because cards are keyed
  by `traceStoryKey`; individual card expansion is therefore not leaked across traces.

## Error handling

Missing calculation data for an expression that should explain a value, backend waterfall errors,
typed omissions, request failures, and optimiser/scenario/live-switch errors render persistent
`role="alert"` UI. A 409 invalidates the preview and requires a new row selection rather than
retrying the same identity. Unknown node detail falls back to generic JSON. Pure parsers
intentionally throw on invalid required numerical contracts instead of silently showing a
plausible-but-wrong calculation.

## Testing

`frontend/src/panels/__tests__/TracePanel.test.tsx` and
`frontend/src/panels/__tests__/TracePanel.enhanced.test.tsx` cover story rendering, target/focus
behaviour, alerts and detail variants. `frontend/src/panels/trace/__tests__/traceGrouping.test.ts`
tests grouping and preservation rules. Focused helper/error suites are under
`frontend/src/trace/__tests__/` for calculations, formatting, banding, model score and rating.
Some presentational primitives (`ExpressionChain`, `InputSourceTree`, `WaterfallChart`, and the
detail dispatcher) are principally covered through integration rendering rather than one test file
per module.

The browser-level preview-to-trace path is covered by
`frontend/e2e/core-flows.spec.ts`. `frontend/e2e/trace-render.benchmark.spec.ts` measures
trace-request-to-render latency for representative linear and multi-frame traces. The
trace-specific component and helper suites do not provide browser coverage for every specialised
detail renderer.

## Approved change contract — 0.7.0 data-input trace presentation

Remaining trace-UI improvement work is tracked in the
[tracing and explainability roadmap](../../roadmap/tracing-explainability.md).

- Change `traceGrouping.isSourceLikeTraceStep` to accept `dataInput`/`apiInput` (and only
  backend-defined non-node source markers where still valid) and remove `dataSource`.
- Extend guarded trace provenance types/rendering/export with safe provider, format, cache mode,
  generation, and `fresh | stale | unknown` fields. Unknown additive fields remain inspectable,
  but malformed known fields fail the guard rather than being coerced.
- `useTracing` includes the backend source/generation identity in semantic request state so a
  refresh aborts/clears stale trace presentation. No frontend cache-status lookup is used to
  reinterpret a completed trace.
- Update grouping, panel, export, hook-race, redaction, and browser tests for retained inputs and
  assert no legacy node literal remains in production trace code.
