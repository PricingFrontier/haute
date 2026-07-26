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
   shared `onUpdate` contract, and gates training with `trainingObjectiveIssue`. CatBoost
   hyperparameters use `config.params` and `config.variance_power`; GLM controls write their
   algorithm fields directly on `config`, including `config.var_power`.
2. `useStaleConfigEstimate` receives the RAM request endpoint with graph/source/structural version;
   it owns abort/loading/error and associates an estimate with the current config. Every cached
   solve/train result carries the complete canonical identity
   `{ configHash, source, structuralVersion }`; the hook has no partial-result shape.
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

## Polars backend contracts (0.6.0)

Remaining frontend modelling and optimiser improvement work is tracked in the
[frontend canvas roadmap](../../roadmap/frontend-canvas.md).
`ModellingConfig.tsx`, `OptimiserConfig.tsx`, their action/result areas, and shared diagnostics
consume only the guarded version-1 strategy contract. They render `projected`, `boundary`,
`admitted_eager`, `rejected`, and `not_planned`, plus diagnostic unavailable, using the shared
authoritative strategy-to-status mapping. Components must not define local response readers or
reinterpret `strategy`.

Boundary/rejection detail includes available blocking node/operator/profile, cost, stable reason,
and remediation; bounded metric/provenance detail is disclosed on demand and preserves
`detail_state=available|unavailable|truncated`. Missing/malformed required fields, unknown
version-1 enums, and unsupported higher versions render diagnostic unavailable. Unknown additive
fields are ignored only within version 1.

`rejected` disables the relevant submit action while leaving configuration editable;
`not_planned` and diagnostic unavailable remain explicit non-success diagnostics without
inventing an execution decision. A group-by enables execution only when the strategy is the
RAM-admitted `materialisation-boundary`; `GroupByExecutionUnsupportedError` surfaces as the
typed HTTP 422 contract error with stable code/named fields. The UI never labels group-by
ordinary checked execution or `unprojected-streaming-boundary`.

Focused tests cover all five status semantics, diagnostic unavailable, strict version/enum
handling, additive version-1 fields, accessible truncated/raw detail, group-by boundary versus
rejection, stable 422 fields, and submit gating.

## Approved change contract — 0.7.0 unified data-input UI consumption

Remaining frontend modelling and optimiser improvement work is tracked in the
[frontend canvas roadmap](../../roadmap/frontend-canvas.md).

- `OptimiserConfig.tsx` continues to derive candidate ids from direct graph inputs and preserves
  explicit selection when valid. `useDataInputColumns.ts` consumes the retained node's guarded
  schema/preview contract and keys results by node/config/source generation, not by path, table,
  or removed node type.
- `utils/banding.ts` keeps its exactly-one-direct-banding-source fallback. Its node filtering and
  tests remove legacy I/O constants without broadening the fallback to an arbitrary `dataInput`.
- Estimate/solve/auto-range request state carries backend capability and snapshot diagnostics
  unchanged. Column loading never calls input-cache Build/Refresh.
- Update component/hook/helper fixtures for grouped Data Input configs, optional code, multiple
  roots, cached generation changes, and removed-node absence. Guard tests reject legacy node
  values rather than rewriting them.

## Approved change contract — deterministic optimiser canvas journey

This contract implements ROAD-UI-02 and ROAD-UI-03 in the
[frontend canvas roadmap](../../roadmap/frontend-canvas.md).

- `utils/banding.ts` supplies the same typed factor classification consumed by Rating. For an
  optimiser it accepts the current direct Banding candidate ids and the explicit configured
  source id. A non-blank explicit id absent from that candidate set is returned as a confirmed
  missing source; absence of an explicit id retains the existing exactly-one-direct-source
  fallback and is not itself an error.
- `OptimiserConfig.tsx` renders one accessible warning that aggregates the missing selected source
  and named zero-level outputs from the effective selected Banding node. Healthy levels still
  render as factor controls. Changing to a healthy source clears only issues that no longer apply.
- `e2e/canvas-assurance.spec.ts` extends the deterministic E2E project with a solved optimiser and
  apply node. It saves non-default objective/constraint ranges, reloads and reopens the editor,
  then asserts the fields before solving. Frontier selection is asserted by stable
  `point_index`/display identity, and local apply is checked against the selected artefact.
- The MLflow leg is a browser-network contract: Playwright intercepts the repository-owned API
  boundary with a fixed run response, records the requested job/selected-point identity, and
  asserts the editor displays the returned experiment/run identity. Optimiser logging currently
  creates a run and artifacts rather than a registered-model identity; the journey neither starts
  nor contacts an external MLflow server.
- `src/panels/__tests__/OptimiserConfig.test.tsx` owns missing explicit source, healthy source,
  zero-level source, and mixed-output alert boundaries. Existing result-store tests continue to
  own rejection of a backend response whose echoed point index differs from the requested one.

## Approved change contract — prerelease canonical frontier-range editor

The target is defined in
[the frontend optimiser high-level contract](high-level.md#approved-change-contract--prerelease-canonical-frontier-range-editor).
`frontend/src/panels/OptimiserConfig.tsx` removes the scalar range reads and single-constraint
mirror writes. `rangeForConstraint` resolves only the named object in `frontier_ranges`, and
focused component tests inspect the complete update payload.
