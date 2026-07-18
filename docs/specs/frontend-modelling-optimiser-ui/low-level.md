# Frontend Modelling & Optimiser UI — Low-Level Specification

## Module map

### Modelling — top-level

| File | Responsibility |
|---|---|
| `frontend/src/panels/ModellingConfig.tsx` | Config panel entry point: algorithm gateway, wires the sub-sections below, owns train submission (`handleTrain`), the RAM estimate hook, and the objective-completeness gate. |
| `frontend/src/panels/ModellingPreview.tsx` | Result preview entry point: tab visibility filtering based on which `TrainResult` fields are populated, progress bar overlay. |

### Modelling — `panels/modelling/`

| File | Responsibility |
|---|---|
| `TargetAndTaskConfig.tsx` | CatBoost target/weight/offset/task/loss-function/metrics section; gates Tweedie variance power. |
| `FeatureAndAlgorithmConfig.tsx` | CatBoost feature include/exclude list (with stale-entry handling) and the hyperparameters JSON editor + GPU toggle. |
| `SplitAndMetricsConfig.tsx` | Split strategy (random/temporal/group), row limit, MLflow experiment/model name fields, monotonic constraints — shared by both algorithms. |
| `GLMTargetConfig.tsx` | GLM target/weight/offset, family/link buttons (incl. Negative Binomial), Tweedie variance power gate, Negative Binomial dispersion `theta` gate, per-gate "Estimate from data" action, intercept toggle, metrics. |
| `GLMFactorConfig.tsx` | GLM factor builder (per-column term type + type-specific params) or raw JSON editor, "All features" opt-in gate, interactions list. |
| `GLMRegularizationConfig.tsx` | Regularisation type toggle, alpha, elastic-net L1 ratio gate. |
| `TrainingActionsAndResults.tsx` | Staleness banner, RAM/VRAM estimate display (with row-limit-adjusted recompute), Train button, live progress, completion badge, inline error. |
| `TrainingProgress.tsx` | Progress bar + iteration/loss stats readout, used inside `TrainingActionsAndResults`. |
| `FailoverHelp.tsx` | Shared "?" tooltip affordance explaining a gated field's silent-default behaviour. |
| `OffsetFieldLabel.tsx` | Shared offset-column label + tooltip, used by both CatBoost and GLM target configs. |
| `styles.ts` | `toggleButtonStyle()` — shared selected/unselected button style used across config sections. |
| `SummaryTab.tsx` | Preview: model info grid, metrics table(s), GLM fit statistics/regularisation info, MLflow export button. |
| `MlflowExportSection.tsx` | "Log to MLflow" button + result display, used inside `SummaryTab`. |
| `GLMCoefficientsTab.tsx` | Preview: sortable coefficients table with significance colouring. |
| `GLMRelativitiesTab.tsx` | Preview: horizontal bar chart of relativities around baseline 1.0, with optional CI whiskers. |
| `LossTab.tsx` / `LossChart.tsx` | Preview / sidebar: train/eval loss curve with best-iteration marker (`LossTab` is the full-size preview version; `LossChart` a smaller variant). |
| `LiftTab.tsx` | Preview: double-lift bar chart + Lorenz curve (with Gini) toggle. |
| `ResidualsTab.tsx` | Preview: residuals histogram + actual-vs-predicted scatter (subsampled to 2,000 points). |
| `FeaturesTab.tsx` / `FeatureImportance.tsx` | Preview / sidebar: full feature-importance bar list vs. a top-10 sidebar variant, both switchable between prediction/loss/SHAP importance types. |
| `AveTab.tsx` | Preview: Actual-vs-Expected dual-axis chart (exposure bars + actual/predicted lines) per selected feature. |
| `PdpTab.tsx` | Preview: Partial Dependence Plot, numeric (line) or categorical (bar) per selected feature, including a diagnostic-error empty state. |
| `FeatureBrowser.tsx` | Shared searchable, importance-sorted feature sidebar used by `AveTab` and `PdpTab`. |
| `ChartScaffold.tsx` | Shared SVG chart primitives (`ChartSvg`, `ChartLegend`, `ChartEmptyState`) and shared colour/font constants for all modelling charts. |

### Optimiser — top-level

| File | Responsibility |
|---|---|
| `frontend/src/panels/OptimiserConfig.tsx` | Config panel: mode toggle, data input/objective/constraints, ratebook banding wiring, frontier range + auto-range, solver tuning, solve submission, inline results/error. |
| `frontend/src/panels/OptimiserDataPreview.tsx` | Pre-solve preview: per-quote chart with navigation, scenario-level statistics tab. |
| `frontend/src/panels/OptimiserPreview.tsx` | Post-solve preview: tab shell (Frontier/Summary/Rates/Convergence/Export), frontier point selection and rates materialisation, save/log/apply actions. |
| `frontend/src/panels/optimiserScenarioStats.ts` | Pure `computeScenarioStatsBySeries()` — per-scenario-index count/mean/std/percentiles across preview rows. |

### Optimiser — `panels/optimiser/`

| File | Responsibility |
|---|---|
| `FrontierChart.tsx` | SVG scatter of frontier points (click-to-select, overlap-bucketing) plus the current-solve marker. |
| `DetailCard.tsx` | Selected frontier point's objective/constraints/lambdas + Save/Log actions; tolerant parsing of flat vs. nested point shapes (`total_x` vs `constraints.x` vs `lambda_x`). |
| `SummaryTab.tsx` | Objective/constraints/lambdas summary, scenario-value histogram+stats, hosts `RatebookImpactBeeswarm` in ratebook mode. |
| `RatebookImpactBeeswarm.tsx` | Beeswarm chart of each rating factor's log-rate effect per level, coloured by the level's underlying feature value. |
| `RatebookRatesTab.tsx` | Per-factor rate table with a factor selector, using the shared factor-table helpers for level ordering. |
| `ratebookFactorTables.ts` | Pure helpers shared by the rates tab and beeswarm: factor-table field access, level ordering by a supplied `FactorLevelOrder`. |
| `ConvergenceChart.tsx` | Objective/lambda-change line chart + per-iteration table from `result.history`. |
| `optimiserHelpers.ts` | `optimiserResultSavePath()` (node-id-qualified save path) and `isConstraintMet()` (min/max threshold check), shared by `SummaryTab` and `DetailCard`. |
| `iterationSummary.ts` | `formatOptimiserIterationSummary()` — "N iters" vs "N CD iters" label depending on mode. |

### Directly-consumed shared dependencies (not owned by this component)

`frontend/src/hooks/useStaleConfigEstimate.ts` (RAM/solve-cost estimate + staleness),
`frontend/src/hooks/useConstraintHandlers.ts` (constraint CRUD),
`frontend/src/hooks/useDataInputColumns.ts` (data-input column fetch/cache),
`frontend/src/utils/trainingObjective.ts` (`trainingObjectiveIssue`),
`frontend/src/utils/executionDiagnostics.ts` (error/diagnostic formatting),
`frontend/src/utils/configField.ts` (`configField`, `safeParseFloat`, `safeParseInt`),
`frontend/src/stores/useNodeResultsStore.ts` (result/job cache, `hashConfig`),
`frontend/src/api/dispersion.ts` (`runDispersionEstimate` — GLM Tweedie/Negative-Binomial
profile-likelihood dispersion estimation, kept out of `api/client.ts` and the initial bundle
since `ModellingConfig` is its only consumer).

## Key types and data structures

- **`TrainResult`** (`stores/useNodeResultsStore.ts`) — the full training-run payload:
  `status`, `metrics`, `feature_importance`, plus large optional arrays for every preview
  tab (`loss_history`, `double_lift`, `ave_per_feature`, `residuals_histogram`,
  `actual_vs_predicted`, `lorenz_curve`(+`_perfect`), `pdp_data`, `shap_summary`,
  `feature_importance_loss`) and GLM-only fields (`glm_coefficients`, `glm_relativities`,
  `glm_fit_statistics`, `glm_regularization_path`). `ModellingPreview` derives tab
  visibility purely from whether the corresponding optional field is present and non-empty.
- **`TrainProgress`** — in-flight status (`status: JobStatus`, `progress`, `iteration`/
  `total_iterations`, `train_loss`), `execution_metrics`/`terminal_reason` for diagnostics.
- **`SolveResult`** (alias of `OptimiserSolveResult` from `api/types.ts`, re-exported by
  `OptimiserPreview.tsx`) — `converged`, `total_objective`, `baseline_objective`,
  `constraints`, `lambdas`, `mode`, `factor_tables` (ratebook), `history`, `n_quotes`,
  `n_steps`, `cd_iterations`/`iterations`, `frontier_error`.
- **`FrontierData`** — `points: Record<string, unknown>[]` (each point carries
  `total_objective`, `total_<constraint>` per constraint, and either flat `lambda_<name>`
  keys or a nested `lambdas` object — `DetailCard.tsx`'s `frontierPointNumber`/
  `frontierLambdaEntries` handle both shapes), `points_returned`, `n_points`,
  `points_truncated`, `points_limit`.
- **`ScenarioStats`** (`optimiserScenarioStats.ts`) — `scenarioIndex`, `scenarioValue`,
  `count`, `mean`, `std`, `min`/`p25`/`median`/`p75`/`max`. One instance per (series,
  scenario index) pair.
- **`QuoteRow`** (`OptimiserDataPreview.tsx`, local) — `scenarioIndex`, `scenarioValue`,
  `values: Record<string, number>` for one row of one quote's chart data.
- **`FactorTables`** (`ratebookFactorTables.ts`) —
  `Record<factorName, FactorTableRow[]>`; each row carries `optimal_scenario_value`
  (`RATE_COLUMN`), an optional `quote_count`, and a level-identifying field resolved by
  `formatFactorLevel` (prefers a `__factor_group__` column, falls back to the first
  non-rate field, falls back to `"Level N"`).
- **`TermSpec`** / **`InteractionSpec`** (`GLMFactorConfig.tsx`, local) — a GLM factor's
  `type` (`linear`/`categorical`/`bs`/`ns`/`target_encoding`/`expression`) plus
  type-relevant params (`df`, `k`, `degree`, `monotonicity`, `prior_weight`, `expr`,
  `levels`); `TYPE_RELEVANT_PROPS` + `cleanTermForType()` enforce that switching a term's
  type strips params irrelevant to the new type.
- **Store slices** (`useNodeResultsStore.ts`): `trainJobs`/`trainResults` and
  `solveJobs`/`solveResults`, each keyed by `nodeId`. A cached result carries the
  `configHash`, `source`, and `structuralVersion` it was produced from — all three, not just
  `configHash`, are the staleness key (see `useStaleConfigEstimate` below);
  `ActiveTrainJob`/`ActiveSolveJob` carry the same three as a snapshot taken at submission
  time. `CachedSolveResult` additionally carries `frontier` and `selectedPointIndex` so
  frontier selection survives panel remount. A result completed with no active job (a
  synchronous completion) has no submission-time snapshot to draw from — `source` falls back
  to `""` and `structuralVersion` to `-1`, sentinels chosen so they can never equal a real
  live value and the result always reads as stale rather than accidentally current.

## Control flow

### Modelling train submission (`ModellingConfig.handleTrain`)

1. Sets `submitting`, builds the graph via `buildGraph(allNodes, edges, submodels, preamble)`, and snapshots `trainSource`/`trainStructuralVersion` from `useSettingsStore`/`useGraphStore` at call time (not read later inside the `.then`, so a source/graph change mid-request can't relabel a job that was actually submitted against the old values).
2. Calls `trainModel({ graph, node_id, source, streamingChunkSize })`.
3. Three outcomes: `status: "started"` with a `job_id` → `startTrainJob(nodeId, jobId, nodeLabel, currentConfigHash, trainSource, trainStructuralVersion)`, handing off to the background-jobs poller; `status: "error"` → `completeTrainJob(nodeId, result)` immediately, no job registered; any other status (synchronous completion) → also `completeTrainJob` directly.
4. A thrown error (network/validation) is caught, converted via `trainErrorMessage`/`trainFailureStatus` (which extract `execution_metrics`/`terminal_reason` from the error's `detail` payload when present), and stored as an error `TrainResult` via `completeTrainJob`.
5. `submitting` is cleared in `finally` regardless of outcome.

### Optimiser solve submission (`OptimiserConfig.handleSolve`)

Same three-outcome shape as training (including the submission-time `solveSource`/
`solveStructuralVersion` snapshot), via `solveOptimiser` / `startSolveJob` / `failSolveJob`.
Note `startSolveJob` is called even on immediate error/throw (before `failSolveJob`), so the
job entry always exists to attach the `constraints` snapshot and `configHash`/`source`/
`structuralVersion` the error is associated with.

### RAM / solve-cost estimate (`useStaleConfigEstimate`)

Both `ModellingConfig` and `OptimiserConfig` wrap their estimate endpoint in a
`useCallback` and pass it to
`useStaleConfigEstimate(nodeId, config, cachedResult, endpoint, context, options)`, where
`context = { source: activeSource, structuralVersion }` is a required positional argument
(not folded into `options` as an `estimateKey` string). The hook: computes
`configHash = hashConfig(config)` on every render; derives `isStale` as true whenever the
cached result's `configHash`, `source`, *or* `structuralVersion` disagrees with the live
values (a cached result predating this three-field contract is missing `source`/
`structuralVersion` and so always fails the comparison — read as stale, never as
accidentally current); and re-fires the estimate fetch in a `useEffect` keyed on
`[nodeId, configHash, context.source, context.structuralVersion, estimateKey, enabled]` —
the estimate `endpoint` function itself is read through a ref so a new callback identity
each render does not retrigger the fetch. (`estimateKey` remains a supported `options`
field on the hook itself — an additional, caller-supplied invalidation key beyond `context`
— but neither `ModellingConfig` nor `OptimiserConfig` passes one any more now that
`source`/`structuralVersion` are checked directly via the required `context` argument.)
Each fetch gets its own `AbortController`; the previous fetch is aborted (not merely
ignored) on re-trigger or unmount.

### GLM dispersion estimate (`ModellingConfig.handleEstimateDispersion` → `GLMTargetConfig`)

1. `ModellingConfig` passes `handleEstimateDispersion` to `GLMTargetConfig` as
   `onEstimateDispersion`; the prop is optional — `GLMTargetConfig` renders no "Estimate
   from data" button anywhere when it's absent (e.g. in isolated tests that don't wire it).
2. `handleEstimateDispersion(param: DispersionParam)` (`param` is `"theta"` or `"var_power"`)
   calls `runDispersionEstimate({ graph: buildGraphCb(), node_id: nodeId, param, source: useSettingsStore.getState().activeSource })` and returns the resolved `Promise<number>`
   straight through — it does not itself touch component state or the config.
3. `runDispersionEstimate` (`api/dispersion.ts`) posts to
   `/api/modelling/dispersion/estimate`, gets back a `job_id`, then polls
   `/api/modelling/dispersion/status/:job_id` on a fixed interval (default 500ms) until a
   terminal status: `"completed"` resolves with `status.value` (throwing `ApiError` if the
   backend completed without a value — never resolving with `null`/`undefined`);
   `"error"`/`"cancelled"`/`"superseded"`/`"timed_out"`/`"memory_limited"`/
   `"contract_error"` all reject with an `ApiError` built from `status.error` or
   `status.message`. An aborted caller signal cancels the job server-side
   (best-effort, failure swallowed) and rejects with a `DOMException("AbortError")`.
4. `GLMTargetConfig.handleEstimate` is the actual click handler: guards against a second
   click while one param is already `estimating`, clears any previous `estimateError`, and
   on success calls `onUpdate(param, value)` — the same `onUpdate` the manual slider/input
   for that field uses, so the estimate lands in the ordinary editable config field rather
   than a separate read-only display. On failure it prefers `ApiError.detail` (the backend's
   actionable message, e.g. "GLM config has no factors…") over the generic error message.
5. Each of the Tweedie and Negative Binomial sections tracks and renders its own
   `estimateError` independently — both use the same `estimating`/`estimateError` state
   pair in `GLMTargetConfig`, but only the section matching the current `family` is mounted,
   so there is never a case where an error from one dispersion field is shown against the
   other.

### Optimiser data-input column resolution

`OptimiserConfig` builds `dataInputColumns` from, in priority order: (1) the selected
`dataInput` node's own cached `_columns` if present (`columnsForNode`), (2) the panel's
`upstreamColumns` prop as a fallback, (3) `useDataInputColumns(dataInput, ...)` which fetches
via `previewNode` with `rowLimit: 1` when neither of the above is populated. The hook itself
caches by `"${dataInput}:${activeSource}"` in `useNodeResultsStore.columnCache` and only
re-fetches when the cache is stale for the current `structuralVersion`.

### Ratebook auto-selection (`OptimiserConfig`)

Two effects run on mode/banding-source change: (1) if `mode === "ratebook"` and
`bandingSource` is unset but a banding node is available, auto-persist
`banding_source` to the first connected banding node; (2) if factor columns are still
empty and the resolved banding source has levels, auto-persist `factor_columns` to one
group per factor (`singleFactorColumnsFromLevels`). Both effects check
`hasConfiguredFactorColumns` (an explicit `Object.prototype.hasOwnProperty` check, not just
"array is empty") so a user who deliberately cleared every factor is not immediately
re-populated.

### Frontier auto-range (`OptimiserConfig.handleAutoRange`)

1. Aborts/cancels any previous auto-range job (`autoRangeAbortRef`/`autoRangeJobRef`),
   including firing a best-effort `cancelOptimiserFrontierAutoRange` for the superseded job.
2. Starts a new job via `startOptimiserFrontierAutoRange`, then polls
   `getOptimiserFrontierAutoRangeStatus` on a fixed 1s interval (`abortableDelay`, itself
   abort-aware) while `status.status === "running"`.
3. On `"cancelled"`/`"superseded"`: records terminal diagnostics but treats it as a
   non-error unless the signal was already aborted by this same component (which suppresses
   the message — an intentional supersede shouldn't flash an error).
4. On any other non-`"completed"` terminal status: records diagnostics and an error message
   built from `autoRangeFailureMessage` (routes memory-limited statuses through
   `buildExecutionFailureMessage` for the fuller diagnostic).
5. On `"completed"`: maps the response's `ranges` onto every currently configured
   constraint; if any configured constraint has no returned range, throws rather than
   silently leaving it unset — the whole auto-range call fails visibly instead of
   partially applying.
6. The unmount cleanup effect (separate from the above) also cancels any still-running
   auto-range job, swallowing cancel-call failures to a `console.warn` since no toast/error
   surface remains once the panel is gone.

### Optimiser frontier point selection → ratebook rate materialisation (`OptimiserPreview`)

Selecting a frontier point in ratebook mode does not automatically carry full rate tables
(`result.factor_tables`) — those are only embedded for the base solve result, not per
frontier point. `shouldMaterialiseSelectedRates` becomes true when the mode is ratebook, a
point is selected, and `factor_tables` lacks data, and only while the Rates or Summary tab
is active. An effect keyed on `[shouldMaterialiseSelectedRates, selectedIdx, jobId, nodeId]`
then calls `selectFrontierPointApi({ job_id, point_index, include_ratebook_tables: true })`,
guarding against races with a per-request sequence number stored in
`requestedRatesRef`/`ratesRequestSeqRef` (a stale response whose sequence number no longer
matches the map entry is discarded). On success it calls
`storeUpdateAfterSelect(nodeId, selectedIdx, res)` to persist the fetched tables back into
the cached solve result, so re-selecting the same point later does not re-fetch.

### Optimiser data preview chart/statistics build

`OptimiserDataPreview` derives `allSeries` (objective first, then constraints, de-duplicated),
truncates `data.preview` to `OPTIMISER_DATA_PREVIEW_ROW_LIMIT` (5,000) rows before any
per-quote grouping, groups the truncated rows by `quote_id` into `quoteRowsByQuote`
(insertion-ordered `quoteIds` array), and for the currently selected quote builds `QuoteRow[]`
sorted by `scenarioIndex`. The Statistics tab's `scenarioStatsBySeries` is a separate
`useMemo` explicitly gated on `tab === "statistics"` — it returns an empty `Map` otherwise,
deferring the `computeScenarioStatsBySeries` call (which iterates every preview row, not
just the current quote) until the tab is actually opened.

## Edge cases and invariants

- **No algorithm selected** (`ModellingConfig`): renders only the picker; no config section,
  RAM estimate, or train action mounts, since `nodeId`-scoped hooks below still run but have
  nothing to gate on until `algorithm` is set.
- **Stale exclude entries** (`FeatureAndAlgorithmConfig`): an excluded column that no longer
  appears in `upstreamColumns` is not silently dropped from `exclude` — it renders in a
  visually distinct "stale" row with its own removal control, and only a config edit removes
  it from the persisted list.
- **GPU-toggle with invalid JSON draft** (`FeatureAndAlgorithmConfig.handleGpuToggle`): if the
  hyperparameters textarea currently holds unparseable JSON, the GPU toggle falls back to the
  last-known-good `displayParams` rather than failing the toggle outright (the one exception
  in this component to "fail loudly" — chosen because the user is mid-edit of unrelated text
  and losing the GPU checkbox click would be more surprising than reverting stray edits).
- **`task_type: "GPU"` is stripped from the displayed/edited JSON** and merged back in on
  commit, so the GPU checkbox and the JSON editor never fight over the same key.
- **Elastic-net / Tweedie / Negative-Binomial collapse-and-restore**: unticking a
  regularisation type or switching the GLM family away from Tweedie or Negative Binomial
  does not clear `l1_ratio`/`var_power`/`theta` from the config — the gated UI
  (`l1RatioSet`/`config.var_power === undefined`/the `family === "negbinomial"` mount check)
  just stops showing the control, so switching back restores the previously chosen or
  estimated value instead of re-prompting.
- **Negative Binomial's `theta` gate starts empty, not with a "Set X" button.** Unlike
  Tweedie's variance power (which shows a "Set variance power" call-to-action button until
  clicked), the theta field is always a visible, editable number input — empty and
  warning-styled (`--warning-soft-subtle`/`--warning-border`) when unset, normally styled
  once a number is typed or estimated. Clearing the input back to blank calls
  `onUpdate("theta", null)`, explicitly re-arming the training gate rather than leaving a
  stale numeric string the parser would silently coerce.
- **"All features" gate greys out but preserves the individual builder** (`GLMFactorConfig`):
  ticking `all_factors` visually disables (`opacity/pointerEvents`) the term builder and JSON
  editor without clearing `terms`, so unticking restores exactly what was configured before.
- **Constraint frontier range fallback** (`OptimiserConfig.rangeForConstraint`): a
  per-constraint range only overrides the shared `frontier_min`/`frontier_max` for fields it
  explicitly sets; an unset `min` or `max` on a per-constraint entry falls back to the shared
  value, not to `undefined`.
- **Single-constraint frontier sync**: when exactly one constraint is configured,
  `handleFrontierRangeChange` also writes the shared `frontier_min`/`frontier_max` fields
  alongside the per-constraint entry, so single-constraint configs stay consistent whichever
  field a future reader looks at.
- **Overlapping frontier points** (`FrontierChart`): points that land on the exact same
  pixel are bucketed by rounded `(cx, cy)`; the visible marker for a bucket prefers index 1
  (skipping the baseline point at index 0), then the selected point if it's in the bucket,
  then any non-zero index, then the first — and the rendered circle's `aria-label` and
  `data-overlap-count` report how many points are stacked there.
- **Duplicate-shape frontier point fields** (`DetailCard`): objective/constraint/lambda
  values are read tolerantly across three possible shapes (`total_<name>` flat key, nested
  `constraints.<name>`/`lambdas.<name>`, or bare `<name>`) via `frontierPointNumber`; a
  present-but-non-numeric value throws rather than being coerced or hidden.
- **Save path uniqueness** (`optimiserResultSavePath`): always `output/optimiser_<sanitized
  label>_<sanitized nodeId>.json` — the node id suffix is load-bearing, not decorative,
  because two nodes with case-variant labels sanitize to the same string and the backend
  save route has no overwrite guard.
- **Empty preview / no scenario data** (`OptimiserDataPreview`): distinct guard states for
  "no objective configured yet" vs. "objective configured but zero rows/quotes in the
  preview", each with different copy pointing at the likely cause.
- **Chart axis crowding** (`OptimiserDataPreview.buildScales`): the space allotted to each
  additional right-side Y axis shrinks (`Math.min(RIGHT_AXIS_GAP, maxRightAxisSpace /
  rightAxisCount)`) rather than growing the chart or overflowing, so toggling on many series
  degrades axis label spacing gracefully instead of clipping the plot area.

## Error handling

- **Train/solve API errors**: `trainErrorMessage`/`requestErrorDetail` prefer a structured
  `detail` payload on the thrown error (via `executionErrorDetailMessage`) over
  `error.message`/`String(error)`, so a backend validation message reaches the user verbatim
  when present.
- **`optimiserScenarioStats.readFiniteNumber`** throws `Error` (uncaught by any surrounding
  try/catch in this component) for a missing, blank-string, or non-finite value in a
  configured series or index column; **`computeScenarioStatsBySeries`** additionally throws
  if the same scenario index is seen with two different scenario values across quotes, and
  if a discovered scenario index is non-integer. These propagate as render-time exceptions
  in `OptimiserDataPreview`'s Statistics tab — there is no local error boundary here, so a
  malformed upstream preview crashes that tab's render rather than showing a wrong table.
- **`DetailCard`'s frontier-point parsers** (`optionalPointNumber`, `requiredPointNumber`,
  `frontierLambdaEntries`) throw `Error` on a present-but-wrong-typed field (e.g.
  `lambdas` present but not an object, or a numeric field present but a string) — malformed
  frontier data crashes the detail card render rather than silently showing blanks.
  Genuinely absent optional fields (`undefined`/`null`) are treated as "not present," not
  an error.
- **Auto-range and column-fetch failures** are caught and converted to user-visible state
  (`autoRangeError`, a toast for column fetch) rather than thrown further; `AbortError`
  from a superseded/unmounted request is explicitly filtered out in every one of these paths
  so a cancelled request never surfaces as a user-visible error.
- **MLflow/save/apply actions** (`handleSave`, `handleLogMlflow`, `handleLoadResultDetail` in
  `OptimiserPreview`; `MlflowExportSection.handleLogExperiment`) catch and stringify any
  failure into an inline message local to that action; they do not affect the rest of the
  panel's state.

## Testing

Tests live under `frontend/src/panels/__tests__/` (top-level `ModellingConfig.test.tsx`,
`ModellingPreview.test.tsx`, `OptimiserConfig.test.tsx`, `OptimiserDataPreview.test.tsx`,
`OptimiserPreview.test.tsx`, `OptimiserPreview.storeIntegration.test.tsx`,
`optimiserScenarioStats.test.ts`, `GLMComponents.test.tsx`, `configPanelsDry.test.ts`),
`frontend/src/panels/modelling/__tests__/` (one file per modelling sub-component), and
`frontend/src/panels/optimiser/__tests__/` (`FrontierChart.test.tsx`,
`optimiserHelpers.test.ts`). All use React Testing Library + Vitest for component tests and
plain Vitest for pure-function tests. One E2E benchmark exists outside this tree:
`frontend/e2e/job-progress-render.benchmark.spec.ts` (tagged `@benchmark`, run via
`npm run test:e2e:benchmark`) polls an optimiser job's status against a real browser session
targeting node id `browser_optimiser` and asserts that progress-polling churn causes zero
React re-renders in shell components (toolbar/canvas) and at most one DOM mutation each —
a regression guard for progress polling leaking work outside the job-progress panel itself.

Coverage by area:
- **`ModellingConfig.test.tsx`** is the largest file, organised by `describe` block: config
  rendering per algorithm, the hyperparameter JSON editor (including GPU toggle + invalid-
  JSON fallback), split/eval section, training actions (submit/started/error/sync paths),
  the staleness indicator (now asserting against fixture jobs/results that carry `source`/
  `structuralVersion` alongside `configHash`), training results display, RAM estimate
  (including downsample/GPU VRAM-exceeded warnings), collapsible sections, edge cases, the
  algorithm gateway, task-switching metrics defaults, loss-function selection (including the
  Tweedie gate), the GLM Negative-Binomial `theta` gate (disabled Train button without
  `theta`, enabled once set), row limit input, feature exclude/include updates, and split
  strategy selection.
- **`ModellingPreview.test.tsx`** covers tab visibility gating per populated `TrainResult`
  field and the tab-reset-on-new-result behaviour.
- **`OptimiserConfig.test.tsx`** mirrors the modelling breadth: mode toggle, input/objective
  selection, ratebook mode (banding auto-select), column mappings, constraints CRUD, solver
  tuning, advanced section, solve action (started/error/sync + extended variants), constraint
  interactions, staleness (including an "extended" pass, with fixture jobs/results now
  carrying `source`/`structuralVersion`), results display, progress, and result-type/
  efficient-frontier switching.
- **`OptimiserDataPreview.test.tsx`** covers `computeScenarioStatsBySeries` directly (moved
  under the component's test file rather than a separate stats-only suite — see also the
  dedicated `optimiserScenarioStats.test.ts`), header/metadata, quote navigation (incl.
  search), series checkboxes, chart rendering, edge cases (no objective/no data), the quote
  summary sidebar, collapse/expand, and the statistics tab (including a config-with-no-
  constraints case).
- **`OptimiserPreview.test.tsx`** covers the Summary-tab-as-default-when-no-frontier case,
  tab switching, the frontier tab with real chart data, convergence, export, constraint
  indicators, lambda display, tab-default resolution, collapse/expand, save/log failure
  message formatting, and a dedicated ratebook-mode block (rates materialisation, error
  states). **`OptimiserPreview.storeIntegration.test.tsx`** separately exercises the
  interaction with `useNodeResultsStore` (frontier selection persistence, `getOptimiserPreview`)
  rather than mocking the store away.
- **`optimiserScenarioStats.test.ts`** unit-tests `computeScenarioStatsBySeries` directly:
  percentile/mean/std correctness, the conflicting-scenario-value throw, the non-integer-
  index throw, and empty-bucket handling.
- **`GLMComponents.test.tsx`** (`frontend/src/panels/__tests__/`, not
  `panels/modelling/__tests__/`) is the dedicated suite for the GLM-specific components:
  `GLMTargetConfig` (family/link buttons including Negative Binomial, Tweedie gate, the
  Negative-Binomial `theta` gate — empty by default, typing/clearing it, the "Estimate from
  data" flow for both `theta` and `var_power` filling the field via `onUpdate` on success and
  showing an inline error without calling `onUpdate` on failure, and the button being absent
  when no `onEstimateDispersion` handler is passed — offset, intercept, metrics),
  `GLMFactorConfig` (add/remove factors, term-type switching, interactions, Builder/JSON
  mode sync), `GLMRegularizationConfig` (type toggle, alpha, elastic-net L1-ratio gate),
  `GLMCoefficientsTab` (sorting, significance stars, empty state), `GLMRelativitiesTab`
  (sort modes, CI whiskers, empty state), the GLM fit-statistics/regularisation extensions
  to `SummaryTab`, and `ModellingConfig`'s GLM routing. Both this file and
  `ModellingConfig.test.tsx` mock `api/dispersion.ts`'s `runDispersionEstimate` and export a
  real `ApiError` class from the `api/client` mock (`GLMTargetConfig` narrows caught errors
  with `instanceof ApiError`, which throws against a plain mock object).
- **`configPanelsDry.test.ts`** is a structural regression guard, not a behavioural test: it
  reads `ModellingConfig.tsx`/`OptimiserConfig.tsx` from disk and fails if either re-inlines
  the RAM/estimate state, the config-hash `useMemo`, or a bespoke polling loop that the
  `useStaleConfigEstimate`/`useBackgroundJobs` extraction was meant to centralise.
- Per-sub-component modelling tests (`FeatureAndAlgorithmConfig`, `TargetAndTaskConfig`,
  `SplitAndMetricsConfig`, `TrainingActionsAndResults`, `TrainingProgress`,
  `MlflowExportSection`, `FeatureImportance`, `FeaturesTab`, `LossChart`, `LossTab`,
  `PdpTab`, `ChartScaffold`, `FeatureBrowser.gaps`, `styles`) test each in isolation with
  hand-built `TrainResult`/config fixtures rather than through the full `ModellingConfig`/
  `ModellingPreview` tree, keeping the top-level suites focused on wiring and cross-section
  behaviour. `optimiserHelpers.test.ts` and `FrontierChart.test.tsx` do the same for the
  optimiser sub-components.

Known gaps: `AveTab.tsx`, `ResidualsTab.tsx`, `FailoverHelp.tsx`, and `OffsetFieldLabel.tsx`
have no dedicated test file — their behaviour is only exercised indirectly through
`ModellingConfig.test.tsx`/`ModellingPreview.test.tsx` where those sections are reachable
via the GLM/CatBoost algorithm paths. (`GLMFactorConfig.tsx`, `GLMRegularizationConfig.tsx`,
`GLMTargetConfig.tsx`, `GLMCoefficientsTab.tsx`, and `GLMRelativitiesTab.tsx` do have
dedicated coverage — see `GLMComponents.test.tsx` above, which lives alongside the
top-level panel tests rather than under `panels/modelling/__tests__/`.) Similarly,
`ConvergenceChart.tsx`, `DetailCard.tsx`, `SummaryTab.tsx` (optimiser),
`RatebookImpactBeeswarm.tsx`, `RatebookRatesTab.tsx`, and `ratebookFactorTables.ts` have no
standalone test file and are covered only through `OptimiserPreview.test.tsx`'s
ratebook/convergence/export blocks.
