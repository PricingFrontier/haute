# Frontend Modelling & Optimiser UI — Low-Level Specification

## Module map

| File | Responsibility |
| --- | --- |
| `frontend/src/panels/ModellingConfig.tsx` | Modelling form orchestration, training submission, RAM estimate and GLM estimate wiring. |
| `frontend/src/panels/ModellingPreview.tsx` | Result-backed modelling tab selection and tab reset. |
| `frontend/src/panels/OptimiserConfig.tsx` | Optimiser form, solve submission, source/constraint configuration and auto-range lifecycle. |
| `frontend/src/panels/OptimiserPreview.tsx` | Solve-result tab orchestration, point selection, exports and ratebook detail materialisation. |
| `frontend/src/panels/OptimiserDataPreview.tsx` | Bounded pre-solve scenario table, quote navigation, multi-series chart and statistics. |
| `frontend/src/panels/optimiserScenarioStats.ts` | Strict finite-number parsing and per-scenario statistical aggregation used by the optimiser data preview. |
| `frontend/src/hooks/useConstraintHandlers.ts`, `frontend/src/hooks/useDataInputColumns.ts` | Constraint mutation handlers and stale-aware data-input column fetching. |
| `frontend/src/utils/configField.ts`, `frontend/src/utils/trainingObjective.ts`, `frontend/src/utils/executionDiagnostics.ts` | Typed config reads/parsing, training-objective gate and structured execution-error/metric display helpers. |
| `frontend/src/panels/modelling/TargetAndTaskConfig.tsx`, `frontend/src/panels/modelling/FeatureAndAlgorithmConfig.tsx`, `frontend/src/panels/modelling/SplitAndMetricsConfig.tsx` | Target/task/loss/metric, feature/algorithm/hyperparameter and split configuration. |
| `frontend/src/panels/modelling/GLMTargetConfig.tsx`, `frontend/src/panels/modelling/GLMFactorConfig.tsx`, `frontend/src/panels/modelling/GLMRegularizationConfig.tsx` | GLM family/dispersion, terms/factors and regularisation controls. |
| `frontend/src/panels/modelling/TrainingActionsAndResults.tsx`, `frontend/src/panels/modelling/TrainingProgress.tsx`, `frontend/src/panels/modelling/MlflowExportSection.tsx` | Train action/result summary, progress and MLflow export. |
| `frontend/src/panels/modelling/SummaryTab.tsx` | Model info, diagnostics/errors, metrics/CV, warnings and MLflow export result summary. |
| `frontend/src/panels/modelling/GLMCoefficientsTab.tsx`, `frontend/src/panels/modelling/GLMRelativitiesTab.tsx` | GLM-specific coefficient and relativity result tables. |
| `frontend/src/panels/modelling/FeatureImportance.tsx`, `frontend/src/panels/modelling/FeaturesTab.tsx`, `frontend/src/panels/modelling/FeatureBrowser.tsx` | Feature-importance display, tab and feature browser. |
| `frontend/src/panels/modelling/ChartScaffold.tsx`, `frontend/src/panels/modelling/LossChart.tsx`, `frontend/src/panels/modelling/LossTab.tsx` | Shared chart primitives and loss visualisation. |
| `frontend/src/panels/modelling/LiftTab.tsx`, `frontend/src/panels/modelling/ResidualsTab.tsx`, `frontend/src/panels/modelling/AveTab.tsx`, `frontend/src/panels/modelling/PdpTab.tsx` | Lift, residual, actual-versus-estimated and partial-dependence result views. |
| `frontend/src/panels/modelling/FailoverHelp.tsx`, `frontend/src/panels/modelling/OffsetFieldLabel.tsx`, `frontend/src/panels/modelling/styles.ts` | Algorithm help, offset label and modelling visual helpers. |
| `frontend/src/panels/optimiser/SummaryTab.tsx` | Objective/constraint/lambda summary, ratebook-impact state and scenario histogram. |
| `frontend/src/panels/optimiser/ConvergenceChart.tsx`, `frontend/src/panels/optimiser/FrontierChart.tsx`, `frontend/src/panels/optimiser/DetailCard.tsx` | Iteration convergence, selectable frontier and strict frontier-point detail display. |
| `frontend/src/panels/optimiser/RatebookRatesTab.tsx`, `frontend/src/panels/optimiser/RatebookImpactBeeswarm.tsx`, `frontend/src/panels/optimiser/ratebookFactorTables.ts` | Ratebook tables, impact chart and factor-table normalisation/order. |
| `frontend/src/panels/optimiser/iterationSummary.ts`, `frontend/src/panels/optimiser/optimiserHelpers.ts` | Iteration copy and optimiser result/save/constraint helpers. |
| `frontend/src/utils/banding.ts` | Extracts banding factor-levels/order and resolves an optimiser's explicit or sole direct banding source. |

## Key types and data structures

- `TrainResult`/`TrainProgress` and optimiser result/job records are consumed from
  `frontend/src/stores/useNodeResultsStore.ts`; the panels key them by `config._nodeId` and source/
  structural/config hash state supplied to the shared estimate/job layers.
- Modelling configuration is a `Record<string, unknown>` split into target, feature, algorithm,
  parameter and split contracts. `trainingObjectiveIssue` gates an incomplete backend objective.
- Optimiser uses input node/banding-node descriptions, `FrontierRangeConfig`, constraints and
  ratebook `FactorTables`. `frontend/src/utils/banding.ts` returns ordered factor levels, including
  a banding default only for the ordering APIs that request it.

## Control flow

### Modelling

1. `frontend/src/panels/ModellingConfig.tsx` reads graph/source/job state, passes its sections the
   shared `onUpdate` contract, and gates training with `trainingObjectiveIssue`.
2. `useStaleConfigEstimate` receives the RAM request endpoint with graph/source/structural version;
   it owns abort/loading/error and associates an estimate with the current config rather than an
   obsolete result.
3. Training creates/updates result-store job state. Structured execution details are converted to
   progress/error state. The optional GLM dispersion action calls the dispersion API and writes a
   successful theta/variance-power estimate through the ordinary editable update callback.
4. `frontend/src/panels/ModellingPreview.tsx` computes which tabs have result data, renders only
   those, and resets the active tab when a new result arrives. `SummaryTab` exposes diagnostics
   rather than suppressing a partially successful training result.

### Optimiser

1. `frontend/src/panels/OptimiserConfig.tsx` finds direct inputs and candidate banding sources.
   It uses cached node columns, provided upstream columns, then `useDataInputColumns` as needed.
   Ratebook mode may set an unconfigured source/factor columns from banding levels, while an
   explicitly configured empty factor list is preserved.
2. Solve submission builds the current graph and records job/result state. Config estimates use
   the same stale-config pattern as modelling. API execution diagnostics are preserved in action
   errors/progress.
3. Starting auto-range aborts/cancels the prior job, polls status every second with an abort-aware
   delay, and applies completed ranges only when every configured constraint has a returned range.
   A locally aborted request is silent; a cancelled/superseded or other terminal status returned
   by the server is shown in the local auto-range error area.
4. `frontend/src/panels/OptimiserPreview.tsx` picks the available result tabs and selected frontier
   point. In ratebook mode a selected point without tables is materialised only on Rates/Summary;
   request sequence bookkeeping drops stale replies and persists accepted tables in the result store.
5. `frontend/src/panels/OptimiserDataPreview.tsx` caps rows at 5,000 before grouping by quote,
   orders scenario rows, and calculates full-preview statistics only when its Statistics tab is open.

## Edge cases and invariants

- With no modelling algorithm, configuration sections that require it are not rendered. Hiding a
  GLM/regularisation/config subsection preserves its stored values for later re-selection.
- Dispersion estimation is an explicit action. Missing/failed estimates remain visible as a field
  gate/error rather than being silently defaulted.
- `frontend/src/utils/banding.ts` returns `{}` for a missing/invalid/non-banding source; when no
  explicit optimiser source exists it falls back only if exactly one direct banding input exists.
- Auto-range failure is atomic for constraints: a completed response missing any configured range
  fails instead of partially updating ranges. Unmount cancels active work best-effort.
- Strict frontier/detail and scenario-statistics helpers handle empty data and zero visual spans,
  but throw on present malformed required numeric fields. The pre-solve chart separately coerces
  missing series/scenario values with `Number(... ?? 0)`. Ratebook tables preserve factor level
  order where supplied.
- Optimiser data preview distinguishes no objective, no preview rows and no quote data; its quote
  and series selection are local presentation state, not changes to the solve config.

## Error handling

Action errors prefer structured API/execution details. Training/solve errors are retained in the
result store and shown locally; estimate/MLflow/export/materialisation actions show their own
messages. AbortError and recognised supersession do not flash a failure. Render-time numeric
contract violations in strict frontier/statistics helpers are intentionally not coerced.

## Testing

Top-level coverage lives in `frontend/src/panels/__tests__/ModellingConfig.test.tsx`,
`frontend/src/panels/__tests__/ModellingPreview.test.tsx`,
`frontend/src/panels/__tests__/OptimiserConfig.test.tsx`,
`frontend/src/panels/__tests__/OptimiserPreview.test.tsx`,
`frontend/src/panels/__tests__/OptimiserPreview.storeIntegration.test.tsx`, and
`frontend/src/panels/__tests__/OptimiserDataPreview.test.tsx`, and
`frontend/src/panels/__tests__/optimiserScenarioStats.test.ts`. Modelling subcomponents have suites
under `frontend/src/panels/modelling/__tests__/`; optimiser helper/frontier coverage is under
`frontend/src/panels/optimiser/__tests__/`. `frontend/src/__tests__/utils/banding.test.ts` covers
the factor-level and optimiser-source utility. Smaller visual summaries/charts are also exercised
through their parent preview tests; not every helper has a dedicated test file.

Performance regression coverage for background progress rendering is in
`frontend/e2e/job-progress-render.benchmark.spec.ts`.
