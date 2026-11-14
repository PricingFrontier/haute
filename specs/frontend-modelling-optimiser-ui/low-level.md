# Frontend Modelling & Optimiser UI — Low-Level Specification

## Saving during background work

A successful save of the current canvas advances its persisted source revision
without replacing its execution identity. Active and starting training, optimiser,
Explore and pivot jobs and a running optimiser auto range keep their handles, progress
polling and result delivery.
Their original config/source/structural-version stamps remain intact so edits made
since launch still mark results stale. Loading another document or accepting an
external revision replaces the execution identity; capability loss, unsynchronised
graphs and system failures still prevent obsolete responses from publishing.
Only a current, accepted save response may acknowledge this revision transition.

## Module map

| File | Responsibility |
| --- | --- |
| `frontend/src/panels/ModellingConfig.tsx` | Modelling form orchestration, early training-job registration/cancellation, RAM estimate and GLM estimate wiring, the per-pane issue alerts, and the report of the panes holding an issue, which the node panel marks on their tabs. |
| `frontend/src/panels/ResultsWorkspace.tsx` | The results workspace shell modelling and optimiser results share: the `ModalShell` Focus view toggle, `PreviewPanelFrame` with a remembered docked height, an optional progress bar, `PreviewPanelTabs appearance="results"` with an `idPrefix`, an optional notices slot, a provenance slot, a per-tab intro (`{title}`) and the `role="tabpanel"` body keyed by tab (`aria-labelledby` its tab, class `validation-workspace`). It owns only the Focus state; the tab, height and content belong to the caller. It imports `modelling/validation.css` and sets the body's `--results-accent`/`--results-accent-soft` from its `accent` prop, which defaults to the model tokens. |
| `frontend/src/panels/ModellingPreview.tsx` | Result-backed modelling tab selection and tab reset on the `ResultsWorkspace` shell (ariaLabel "Model validation", `idPrefix` "modelling-preview", model accent, `modellingPreviewHeight`). Loaded on demand by the app when the active node has model results, through the same Suspense boundary pattern as optimiser results. Its collapsed bar summarises the first two of the metrics the result leads with (`headlineMetrics`). |
| `frontend/src/panels/modelling/diagnosticsSet.ts` | The diagnostics partition's label (Test, Validation, Training) and row count, shared by the workspace's diagnostics strip and the Summary tab; `validationMetricsLead` (a validation fit ran and the diagnostics are in-sample) and `headlineMetrics` (test metrics, else the validation selection means when they lead, else the diagnostics), shared by the Summary's first card and the collapsed bar. Validation diagnostics without an evaluation selection fit throw instead of reporting a count. |
| `frontend/src/panels/NodePanel.tsx`, `frontend/src/panels/PreviewPanelTabs.tsx` | Six-pane hosting owned by [frontend-node-editors](../frontend-node-editors/low-level.md) and the accessible tab strip owned by [frontend-preview-explore](../frontend-preview-explore/low-level.md), both consumed by modelling. |
| `frontend/src/panels/OptimiserConfig.tsx` | Optimiser pane router, solve submission, source/factor derivation and the solve-blocking issue list. It receives the active pane from `NodePanel` and renders one pane at a time; auto-range request identity and terminal presentation stay in `useOptimiserAutoRange`. |
| `frontend/src/panels/optimiser/OptimiserFactorsTable.tsx` | The Factors pane's factor table: the Validation input select over the connected inputs (the Objectives & Constraints input first, stored as no `analysis_input`), a search, one row per rating factor or validation-frame column with Ratebook (`factorColumns`, ratebook mode and source factors only) and Validation (`analysis_columns`, capped, frame columns only) checkboxes, the switch-time removal of validation factors the new frame lacks, and the missing-input and missing-column flags. |
| `frontend/src/panels/optimiser/optimiserPanes.ts` | The optimiser pane list (the same in both modes) and resolution of a remembered pane, Data when none. |
| `frontend/src/panels/optimiser/OptimiserConstraintSettings.tsx` | The Constraints pane body: the Result line, then one card per constraint holding its column and remove action, then its bound type (the four kinds of `constraintBounds.ts`), Fixed | Sweep switch and either its fixed value or its from/to range (in percent for a percentage bound), then, when any constraint is swept, the Frontier section with points per swept constraint. Each swept card has its own Auto range button and error; a percentage card's is disabled. It composes one `useOptimiserAutoRange` for the pane, running it for one constraint at a time and merging the filled range over the latest `frontier_ranges` (read through a ref when the run completes), keeping request state out of the parent form. |
| `frontend/src/panels/optimiser/constraintBounds.ts` | A constraint's bound: the four kinds (`min`, `max`, `min_pct`, `max_pct`) with their labels, `constraintBoundKind` (the spec's one kind, `null` for none or several), `constraintBoundValue`, the percent display and entry conversions (0.95 ↔ 95) and `startingBoundValue` (1 for a percentage, 0 for an absolute bound). |
| `frontend/src/panels/optimiser/OptimiserSolveStatus.tsx` | Pure solve estimate, stale-result, progress, terminal diagnostics, action, and convergence-result presentation. It receives the parent-owned solve transition and owns no request lifecycle state. Its convergence result is the as-solved result (`originalResult`), never the frontier point the preview shows. |
| `frontend/src/panels/optimiser/useOptimiserAutoRange.ts` | The single state authority for auto-range lifecycle: reducer-owned pending/error/terminal diagnostics, monotonic restart generation, the document execution fence, structural version and node id (never the source revision a save advances), abort/cancel ownership, polling, response validation, and completed-range publication. `run(targets)` names the constraints a run fills and the state reports them (`autoRangeTargets`), so a pane can show the run on the card that asked; the caller's `writeRanges` merges the result. |
| `frontend/src/panels/OptimiserPreview.tsx` | Solve-result tab orchestration on the `ResultsWorkspace` shell (ariaLabel "Optimiser validation", `idPrefix` "optimiser-preview", the `--optimiser-accent`/`--optimiser-accent-soft` pair, `optimiserPreviewHeight`, `HeaderPointStepper` as a header action), no tab intros, point selection, the stale-result strip with Re-run, ratebook detail materialisation and the Frontier tab layout; it has no publish actions. |
| `frontend/src/panels/optimiser/resultViews.ts` | The result views in tab order with their labels; no view has an intro. A failed solve with no earlier result has no result, so no preview. |
| `frontend/src/panels/DiagnosticsIssues.tsx` | The "Diagnostics Issues" `role="alert"` (accessible name "Diagnostic issues") both result workspaces' Summary tabs show for diagnostics that could not be produced: per issue its label (the caller's `formatLabel`), raw diagnostic id, error type and message. Modelling passes its training result's diagnostics errors (their error text as the message); the optimiser passes its solve result's. |
| `frontend/src/panels/optimiser/solveActions.ts` | The one solve entry point (`startOptimiserSolve`) used by the Solve pane, Ctrl+Enter and the preview's Re-run; `stopOptimiserSolve`; and the solve-identity hash and staleness check (`solveConfigHash`, `isSolveResultStale`) that exclude the export settings. |
| `frontend/src/panels/optimiser/solveReadiness.ts` | The one set of solve-readiness rules: `resolveOptimiserInputs` (selectors against the connected inputs) and `optimiserSolveReadiness` (blocking issues, `canSolve`, `canAutoRange`), used by the Solve pane, Ctrl+Enter and the preview's Re-run. |
| `frontend/src/panels/optimiser/useOptimiserReadiness.ts` | Wraps those rules with the one data-input column path (known single-input columns, else the source-aware column cache and preview fetch) and the analysis frame's columns (the data-input columns when the analysis input is the data input, else a preview of the selected analysis input's source node), so the editor and Re-run judge the same columns; the preview fetches only while its stale strip is shown. |
| `frontend/src/panels/optimiser/OptimiserPublishSection.tsx` | The Export pane's Publish section: target choice bound to the result store's selected point, `result_export_path`, version label, Save/Log with the overwrite confirmation, receipts, Use in Apply node, the ratebook factor-table CSV with its on-request load of a frontier point's tables (the shared point-request lifecycle) and the combined-factor collar statement (read from the solve's `originalResult.combined_factor_bounds`, printed through `frontend/src/panels/optimiser/adjustments.ts::formatScenarioValue`). |
| `frontend/src/stores/useOptimiserPublishStore.ts` | Per-node publish state keyed by solve job (busy flags, receipts, errors, overwrite prompt) owned by the Export pane. It belongs to the document: a change of the document's execution generation (another document, a change on disk) forgets every node's state, and a save or log reply is applied only while the document fence it started under is current. |
| `frontend/src/panels/optimiser/QuotesTab.tsx`, `frontend/src/panels/optimiser/lambdaCopy.ts` | The Quotes explorer (both modes) for the publish target: a server-sorted, searched and filtered page of the chosen scenarios in `SortableValuesTable`, with the presets, the pager and the analysis-value filters, read through the result store's identity-keyed `/apply` cache (the full canonical query in the identity), a missing page requested through the shared point-request lifecycle; and the shared λ label and explanation. |
| `frontend/src/panels/optimiser/useLatestRequest.ts`, `frontend/src/panels/optimiser/RequestFailureNotice.tsx` | The one frontier-point request lifecycle (see Control flow) that Rates, Adjustments, Segments, Quotes and the Export pane's factor-table load share: the hook sends the request a view names by its key while it is wanted and has not failed, aborts it on a new key, none or unmount and discards the aborted reply, reissues a 409 `frontier_point_apply_replaced`, and keeps a failure for its key (`RequestFailure`, gone for a 410) until Retry; `RequestFailureNotice` states the failure with Retry unless it is gone. |
| `frontend/src/panels/ViewSwitch.tsx` | `ViewSwitch`, the one switch between views of a result in both results workspaces: a labelled group of `aria-pressed` buttons whose pressed one takes the workspace's `--results-accent` (`.view-switch-option` in `modelling/validation.css`). The Features tab's importance measures, Lift's Double lift / Lorenz curve, the GLM coefficients' Table / Intervals and relativities' sort, the term tables' Table / Chart and scale, the optimiser Adjustments and Segments Weight by, and the ratebook beeswarm's Top 8 / All use it. |
| `frontend/src/panels/SortableValuesTable.tsx`, `frontend/src/panels/valuesSort.ts` | The shared sortable values table (extracted from the GLM coefficients): column headers as sort buttons with `aria-sort` and a ▲/▼ indicator on the sorted column (a disabled button for a column that cannot be sorted now, plain text for one that never can), the caller's cells and an empty-state `role="status"` message; `ValuesTableSearch`, its labelled search input; and `nextSort` (`valuesSort.ts`, with `SortState`), the one sort cycle (a new column sorts ascending, the sorted column reverses). Sorting itself is the caller's: GLM sorts in the browser, Quotes asks the server. |
| `frontend/src/panels/OptimiserDataPreview.tsx`, `frontend/src/panels/optimiser/useOptimiserScenarioData.ts`, `frontend/src/panels/optimiser/OptimiserScenarioPanes.tsx` | The pre-solve scenario view: the hook groups the bounded preview rows by quote and owns series visibility, quote navigation (reset during render when the series list or the quote ids, in order, change, so another quote-id column or another quote set of the same size starts at the first quote) and on-demand statistics; each series' y scale is the shared `chartDomain` with `chartTickSpan` ticks, so a constant series sits mid-plot and rounding noise reads as flat; the panes (quote navigation, Curves, Statistics, the no-objective/no-data notice) render in the data preview's frame and as the result workspace's Curves and Statistics tabs. The Statistics tables print their numbers through `frontend/src/utils/formatValue.ts::formatNumber`. |
| `frontend/src/components/ExecutionDiagnosticsSummary.tsx` | Actionable execution-memory and rejected-strategy banner shared with modelling progress, optimiser actions, and Explore. |
| `frontend/src/panels/optimiserScenarioStats.ts` | Strict finite-number parsing and per-scenario statistical aggregation used by the optimiser data preview. |
| `frontend/src/hooks/useConstraintHandlers.ts`, `frontend/src/hooks/useDataInputColumns.ts` | Constraint mutation handlers (add, on the first free column or else the first unused `constraint_<n>` name, remove, rename, a bound's value, and its kind: between at least and at most the value stays, between an absolute and a percentage bound it restarts at `startingBoundValue`, with a swept constraint's range in the same update) and stale-aware data-input column fetching, which returns only the current input's columns: after a switch, that input's cached (else fallback) columns until its fetch answers, never the previous input's. |
| `frontend/src/api/types.ts`, `frontend/src/types/trainGuards.ts` | [frontend-shared](../frontend-shared/low-level.md)-owned API types and dynamically loaded strict JSON response parsing consumed by modelling progress/results. |
| `frontend/src/stores/useNodeResultsStore.ts`, `frontend/src/stores/useUIStore.ts` | [frontend-shared](../frontend-shared/low-level.md)-owned result/job state and per-node modelling-pane memory consumed by the modelling workflow. |
| `frontend/src/utils/configField.ts`, `frontend/src/utils/trainingObjective.ts`, `frontend/src/utils/executionDiagnostics.ts` | Typed config reads/parsing, training-configuration issue derivation (a stable issue-code union; `glmConflictIssues`, the GLM settings RustyStats refuses together, which the Parameters pane shows beside them), and structured execution-error/metric display helpers. |
| `frontend/src/panels/modelling/algorithmCapabilities.ts`, `frontend/src/panels/modelling/algorithmCapabilities.json` | The backend-generated family capability table (`ALGORITHM_CAPABILITIES`, `algorithmCapability`, `supportedLosses`) that the gateway, target pane and tuning readiness read, and `tuningFamilyCapability`, the tuned family the Summary tab's fit line names. |
| `frontend/src/panels/modelling/modelColumns.ts`, `frontend/src/panels/modelling/WeightOffsetFields.tsx` | What the GLM and tree-family target configurations share: the columns each role may take (the target is never the weight or offset; weight and offset are numeric) and the optional weight and offset pickers. |
| `frontend/src/panels/modelling/TargetAndTaskConfig.tsx`, `frontend/src/panels/modelling/CommonFeatureConfig.tsx`, `frontend/src/panels/modelling/SplitAndMetricsConfig.tsx`, `frontend/src/panels/modelling/metricOptions.ts` | Tree-family target/loss/metric controls with loss-derived task compatibility and the positive-class field, the common feature/monotonicity browser, the canonical evaluation editor with exact-plan preview, and the one metric table both families' pickers and the Train guard read. |
| `frontend/src/panels/modelling/HyperparametersConfig.tsx`, `frontend/src/panels/modelling/hyperparameters.ts`, `frontend/src/panels/modelling/featureSelection.ts` | Algorithm-neutral fixed-parameter JSON editing, optional bounded tuning/search-space editing from each family's starter space under the family's label, the tuning note with the run summary's fit budget (`frontend/src/panels/modelling/trainingFitBudget.ts::fitBudgetLabel`), and pure parameter/feature transitions. |
| `frontend/src/panels/modelling/EBMInteractionsConfig.tsx`, `frontend/src/panels/modelling/EBMTermsTab.tsx` | The EBM Features-pane pairwise-interaction control (a count EBM chooses from, or explicit feature pairs written to `params.interactions`), and the EBM Terms result tab: importance-ranked terms shown as tables with a chart on request (main-effect score tables or shapes with the missing bin, and interaction score tables or lines) of additive link-scale term scores (the docs, not the pane, explain the scale). |
| `frontend/src/panels/modelling/TBoostTablesTab.tsx`, `frontend/src/panels/modelling/TermTableView.tsx`, `frontend/src/panels/modelling/termCharts.tsx` | The t-boost Tables result tab (importance-ranked tables shown as tables with a chart on request, relativity or link-scale view, training mass per cell, axis selectors for tables of two or more factors; `TBoostTables.test.tsx` renders a main effect's level table, a two-way table whose categorical axis had gapped native cells, its line chart and a three-way table, and proves the shown values follow each axis and slice selection), and the term view both it and the EBM Terms tab draw with: the Table/Chart options, the axis selectors, and the level table, shape, level, surface and interaction-line charts. |
| `frontend/src/panels/modelling/GpuTrainingToggle.tsx` | `GpuTrainingCheckbox`, the Train pane's GPU training checkbox (CatBoost's directly), and `XGBoostGpuToggle`, that checkbox for a GPU-capable family (XGBoost): fetches `GET /api/modelling/gpu` once per mount, enables the box only when the server can train on a CUDA GPU (otherwise shows the server's reason), and always allows switching an existing GPU node back to CPU. |
| `frontend/src/panels/modelling/GLMTargetConfig.tsx`, `frontend/src/panels/modelling/GLMTermsConfig.tsx`, `frontend/src/panels/modelling/GLMInteractionsConfig.tsx`, `frontend/src/panels/modelling/TermCard.tsx`, `frontend/src/panels/modelling/glmTerms.ts`, `frontend/src/panels/modelling/glmFamilies.ts`, `frontend/src/panels/modelling/GLMRegularizationConfig.tsx` | GLM family/link/dispersion, feature rows with indented inline term cards, labelled interaction/slot controls, pure editor transitions mirroring the backend term contract, the family/link and solver constants shared with the backend, and regularisation, cross-validation, and solver controls. |
| `frontend/src/panels/modelling/TrainingActionsAndResults.tsx`, `frontend/src/panels/modelling/TrainingProgress.tsx` | Train action/result summary and progress. |
| `frontend/src/panels/modelling/TrainingRunSummary.tsx`, `frontend/src/panels/modelling/trainingFitBudget.ts` | The Train pane's read-only run summary (model, target, feature count, evaluation method and allocation, fit budget, compute, and for a CatBoost model with String or Categorical features the categorical encoding: one-hot up to `one_hot_max_size` levels with target statistics above, tuned when the search space holds it, or CatBoost's default) and the pure fit count behind it: selection fits (validation fits × tuning trials) plus the final development refit unless `refit_on_development` is `false`, or for a family that publishes its validation fit (t-boost) a study's reproduced winning fit; `fitBudgetLabel` words that budget once for the run summary and the Parameters pane's tuning note. |
| `frontend/src/panels/modelling/EvaluationAllocation.tsx`, `frontend/src/panels/modelling/evaluationPreview.ts` | The Split pane's training/validation/test allocation bar and per-fit row ranges, showing exact rows only from an evaluation preview whose strategy and validation method match the current editor (`compatibleEvaluationPreview`), and target fractions otherwise; `EVALUATION_PREVIEW_FAILED`, the `error_code` of an estimate refused because its evaluation preview failed. |
| `frontend/src/panels/modelling/trainingEstimate.ts` | `estimateAfterSupersededPreviews`: the modelling estimate request retried with bounded, abortable backoff only while a 507 names nothing but transient in-flight holders (`transientEstimateHolders`): other evaluation previews (`training_prep:training_evaluation_preview`) and input-snapshot builds (any `<profile>:input_snapshot_build`). A refusal naming only evaluation previews keeps the short `SUPERSEDED_PREVIEW_RETRY_DELAYS_MS` schedule (about 3.5 s); one naming a snapshot build is retried every `INPUT_SNAPSHOT_RETRY_DELAY_MS` (2 s) for up to `INPUT_SNAPSHOT_WAIT_LIMIT_MS` (60 s) of waiting, and reports each wait through the optional `onWaiting` callback so the caller can say what it is waiting for. The two budgets belong to one call and never reset: each preview-only refusal takes the next preview delay and each refusal naming a snapshot build takes 2 s of the 60 s, whatever came before, and a refusal whose own budget is spent is returned. So alternating refusals retry at most 4 + 30 times. Every other failure, including a running training job, is returned at once. |
| `frontend/src/panels/modelling/ColumnSelector.tsx` | Searchable column-only combobox for target, weight, offset and similar role fields; a saved column that is no longer upstream stays visible but is never offered as a new choice, and is flagged unavailable only once the columns are known. |
| `frontend/src/panels/modelling/useDiagnosticFeature.ts`, `frontend/src/panels/modelling/FeatureDiagnosticTab.tsx`, `frontend/src/panels/modelling/validation.css` | The feature selection a diagnostic pane owns standalone or shares with the result workspace; the per-feature tab layout AvE, PDP and the optimiser Rates tab share (`FeatureDiagnosticLayout`: a ranked browser beside the selected item's chart, with empty states for no rows and for a selection without a row; `FeatureDiagnosticTab` ranks model features by importance through it); and the results workspace's container-query layout styles, imported by `frontend/src/panels/ResultsWorkspace.tsx` so the optimiser is styled even when no model result has been opened. Accent-coloured rules read `--results-accent`/`--results-accent-soft`, which the shell sets per workspace; the optimiser Frontier tab's stacking rules live here too. The axis and scale helpers live in [frontend-shared](../frontend-shared/low-level.md)'s `utils/chartHelpers.ts`. |
| `frontend/src/panels/modelling/ExportPane.tsx`, `frontend/src/panels/modelling/MlflowExportSection.tsx`, `frontend/src/panels/modelling/ModelFileExportSection.tsx` | The Export pane: the MLflow logging fields, the manual MLflow log action (names the node's destination, disabled with the reason when that destination is unconfigured or no trained model is exportable, always sends `destination`), and the save-model-to-file action. |
| `frontend/src/panels/modelling/exportReceipts.ts`, `frontend/src/panels/modelling/useTrainedJobRestore.ts` | `useExportReceipts(jobId)` (reads a completed job's export receipts from the status endpoint on mount and on `refresh`), `newOperationId()`, and `useTrainedJobRestore` (after a reload, reads a remembered job's status once to restore its result — current only when the editor's current payload has the same `trainingLineage` — or record it as expired in the results store with `markTrainResultExpired`). |
| `frontend/src/utils/trainedJobHandles.ts`, `frontend/src/utils/modellingExportConfig.ts` | Per-document browser handles to a node's last completed training job (`read`/`write`/`clearTrainedJobHandle`, each holding job ID, config hash, source and lineage) and `trainingLineage` (a digest of the graph payload a training request submitted, submodel graphs and global constants included); the modelling and optimiser export-field keys (`MODELLING_EXPORT_CONFIG_KEYS`, `OPTIMISER_EXPORT_CONFIG_KEYS`, `exportConfigKeysFor`) and `trainingIdentityConfig` / `solveIdentityConfig`, which omit them. |
| `frontend/src/panels/modelling/modelExport.ts`, `frontend/src/panels/modelling/FieldHelpIcon.tsx` | The model file extension per algorithm (read from the model family registry, `frontend/src/utils/modelFamilies.ts`) and the shared hover-only field help icon. |
| `frontend/src/utils/modelFamilies.ts`, `frontend/src/utils/modelFamilies.json` | The backend-generated model family table (`MODEL_FAMILIES`, `MODEL_FILE_SUFFIXES`, `modelFileSuffixForAlgorithm`): each family's label, the training algorithm whose models load as it, and its file suffixes. `tests/test_model_families.py` holds the JSON equal to `haute._model_flavors.model_family_fixture()`. |
| `frontend/src/panels/modelling/SummaryTab.tsx` | Model info, diagnostics/errors, development/selection/final-test metrics (the validation fit's selection metrics lead when the diagnostics are in-sample after a validation fit and no test set was reserved), tuning baseline/winner evidence and warnings. |
| `frontend/src/panels/modelling/GLMCoefficientsTab.tsx`, `frontend/src/panels/modelling/GLMRelativitiesTab.tsx` | GLM-specific coefficient and relativity result tables, including invalid-inference reasons and robust standard errors. |
| `frontend/src/panels/RelativityBars.tsx` | The diverging bar list GLM relativities, the optimiser Rates tab and the SHAP curves share, around a `baseline` of 1.0 unless the caller names another (SHAP curves use 0 outside a log link): one row per item in the caller's order, `--chart-above`/`--chart-below` bars scaled to the largest deviation (confidence whiskers included), theme-token baseline and whiskers, optional focusable rows with an active row, an optional aligned side strip, and, from a caller threshold, compact rows whose labels are thinned with `chartLabelIndices`. A `null` value is unavailable: the row draws no bar, shows "—" and does not set the scale (the Segments tab's zero-weight level); a non-finite value throws. |
| `frontend/src/panels/modelling/NumberField.tsx` | Numeric input, the kit's field in the size its `field` prop names (regular unless told), that keeps a draft until a valid, in-range value commits on blur or Enter. |
| `frontend/src/panels/modelling/modellingPanes.ts` | `modellingPanesFor(algorithm)` and `resolveModellingPane`, the one pane list behind the modelling tabs and pane bodies. |
| `frontend/src/panels/modelling/ShapBeeswarm.tsx`, `frontend/src/panels/modelling/shapBeeswarmLayout.ts`, `frontend/src/panels/modelling/beeswarm.ts` | The Features tab's SHAP beeswarm. `layoutShapBeeswarm` places every dot once per width (`beeswarmOffsets`: buckets along the SHAP axis, lanes alternating above and below the row line, scaled so the densest bucket fits the row) and indexes them on a pixel grid for `nearestDot`. The component paints the dots on a canvas (device-pixel scaled, grouped by colour, colours resolved from the `--chart-value-low`/`--chart-value-high`/`--chart-value-none` tokens on the document root, throwing when a token is not a hex colour or no 2D context exists) between the SVG that carries the axes, focusable feature rows and colour bar, and an SVG overlay ring for the pointed-at dot; one pointer handler on the wrapper finds the nearest dot, so pointing repaints no dots: it re-renders the overlay ring, the detail line and the feature rows, while each feature's row count, SHAP range and median (the row labels, focus detail and values table) are computed once per result. The axes, row lines and labels use the modelling chart tokens (`MODELLING_CHART_GRID_COLOR`, `MODELLING_CHART_AXIS_TEXT_COLOR`, `MODELLING_CHART_AXIS_FONT_SIZE`, `--text-secondary`/`--text-primary` labels) on the panel surface. `beeswarm.ts` holds the SHAP value colours (`SHAP_VALUE_COLORS`, `SHAP_VALUE_TOKENS`, `valuePositionRgb`), the ratebook beeswarm's white-plot colours (`valuePositionColor` over the `--chart-impact-*` tokens) and the stacking; `ValueColorBar` in `ChartScaffold.tsx` draws either beeswarm's colour bar from the caller's low and high colours, caption colour and font size. |
| `frontend/src/panels/modelling/ShapCurvesTab.tsx` | The SHAP curves tab on `FeatureDiagnosticTab`: a numeric feature's band line with its 10th to 90th percentile area, zero or 1.0 baseline and a separate missing point; a categorical feature's `RelativityBars` with percentile whiskers and a row-count strip, baseline 1.0 under a log link and 0 otherwise; a detail line for the focused point or bar and a values table. Display values are exp of the SHAP statistics when `shap_link` is `log`. |
| `frontend/src/panels/modelling/FeaturesTab.tsx`, `frontend/src/panels/modelling/FeatureBrowser.tsx` | The feature-importance tab and the feature browser. The browser names what it ranks by ("Ranked by importance", "Ranked by rate spread") and what it lists (features or factors), so a non-importance ranking is never presented as importance. An item whose measure is `null` is unranked and draws no bar (the Segments keys while their index loads). |
| `frontend/src/panels/IterationLinesChart.tsx` | The shared by-iteration line chart: aligned series (a `null` value is a gap the line bridges; a non-finite value throws), a linear or log value axis (a log axis throws on a value at or below 0, so its caller decides how to show zeros, and passes its scale to `ChartValueGrid`), optional horizontal reference lines (dashed, inside the value domain) and an optional dashed vertical marker at an index, optional point markers, the x-axis labelled with each index's iteration value, and a legend built from the series, reference lines and marker. It draws on `ChartSvg`, `ChartValueGrid` and `ChartLegend` at the width it is given; the modelling Loss tab and the optimiser Convergence tab are its adapters. |
| `frontend/src/panels/modelling/ChartScaffold.tsx`, `frontend/src/panels/modelling/LossChart.tsx`, `frontend/src/panels/modelling/LossTab.tsx`, `frontend/src/panels/modelling/lossHistory.ts` | Shared chart primitives (responsive width, SVG surface, legend (line, dashed and bar swatches, and dot, ring, hollow and cross point markers), empty state, `ChartValueGrid`, the value axis of gridlines that every validation chart draws, its linear ticks labelled together by `formatChartTicks` so neighbouring ticks never share a label and a log axis's decades (`scale="log"`) labelled one by one by `formatChartNumber`, `ChartYAxisTitle`, the upright value-axis title the Lift, Lorenz and residual scatter charts share, `ChartValuesTable`, a chart's raw values behind a native disclosure in the shared `validation-value-table` (the first column is each row's header), which the Lift, AvE, PDP and Convergence tabs use, and `TwoChartLayout`, the two-chart result layout: side by side with a 24 px gap from a caller breakpoint when both charts exist, each chart at least a caller minimum wide, otherwise full width one under the other, with an optional header above) and loss visualisation (`LossTab` adapts the training loss history to `IterationLinesChart`; `LossChart` is the compact live curve, drawn when `lossHistory.ts`'s `lossCurveKeys` finds a `train_` key in a history of at least two rows, or one row when the fit's round budget is known; its axes are fixed from the first row, rounds from 0 to the budget and loss spanning zero and the fit's extremes so far, padded by a tenth of that range on each side a loss reaches, so a positive loss runs from 0 to 1.1 times its peak (the starting loss unless a later value outgrows it) and a negative one, such as CatBoost's Poisson, stays inside the axis; the extremes come from the current rows alone, which the live history keeps through thinning, so the axis never shrinks during a fit and starts afresh with the next fit's history, and the loss axis's two ends and the budget are labelled). `lossHistory.ts`'s `shownFit` picks the fit `LossTab` draws, and the workspace offers the Loss tab when that fit's history has at least two rows, so a refit that kept one tree still shows its validation fit. `LossTab` draws the validation fit when one ran: after a holdout validation fit and a refit it plots `validation_loss_history`'s train and eval curves with the selection fit's best iteration, and when the refit was skipped `loss_history` is that fit; otherwise it plots `loss_history` with `best_iteration`. The best-iteration marker sits on the row whose `iteration` is `best_iteration + 1` and is labelled with `best_iteration` as the Summary shows it. For a validation fit the intro reads "Validation fit: N training, M validation rows" from the selection fit, followed after a refit by "The final model was refit on all N development rows" (with "for K iterations" when `final_tree_count` is known). A thinned history adds "Thinned to R of S iterations", S being the last row's iteration. Charts are hand-drawn SVG on these primitives; ECharts stays confined to the Explore combo chart. |
| `frontend/src/panels/modelling/LiftTab.tsx`, `frontend/src/panels/modelling/ResidualsTab.tsx`, `frontend/src/panels/modelling/AveTab.tsx`, `frontend/src/panels/modelling/PdpTab.tsx` | Lift, residual, actual-versus-estimated and partial-dependence result views. Lift and Residuals lay their two charts out with `TwoChartLayout`: Lift side by side from 900 px (charts at least 260 px, a Double lift / Lorenz curve switch in the header when narrower), Residuals from 760 px (at least 280 px, stacked when narrower). |
| `frontend/src/panels/modelling/FailoverHelp.tsx`, `frontend/src/panels/modelling/OffsetFieldLabel.tsx`, `frontend/src/panels/modelling/styles.ts` | Algorithm help, offset label and modelling visual helpers, including the shared modelling input surface. |
| `frontend/src/panels/optimiser/SummaryTab.tsx` | Objective, the constraint-attainment table (with λ, for both modes), ratebook-impact state and a compact adjustments summary (up / down / unadjusted / at the range edge) linking to the Adjustments tab. |
| `frontend/src/panels/HistogramChart.tsx` | The shared histogram (extracted from Residuals): a titled `ChartSvg` with value gridlines, x ticks, axis labels, a dashed reference line and a legend. Bars are placed on a numeric axis (Residuals' bins, by centre) or as evenly spaced categories (the Adjustments grid values), and may be focusable (`role="button"`, a described `aria-label`, activated by hover, focus, click, Enter or Space) with the active bar highlighted. A non-finite bar value or an empty bar list throws. |
| `frontend/src/panels/optimiser/AdjustmentsTab.tsx`, `frontend/src/panels/optimiser/adjustments.ts` | The online Adjustments view (see Control flow): the adjustment report's bars against the 1.0 base price, the Weight by switch, the quantile row, the shares, a detail line for the active bar, a values table, and the lazy per-point load; and the copy and formatting Summary shares with it (the point-report key, the no-1.0 note, grid values and shares, and `rangeEdgeShare`, the union of the minimum and maximum edge shares, counted once on a one-step grid). |
| `frontend/src/panels/optimiser/SegmentsTab.tsx` | The Segments view (see Control flow): the result's segment keys in the per-feature diagnostic layout, ranked by the index's adjustment spread (unranked while it loads), the selected key's per-level mean scenario value as `RelativityBars` around 1.0 with an aligned quote strip, a `ChartFocusDetail` line, a values table, the Weight by switch, and the review-owned caches of loaded breakdowns and indexes. |
| `frontend/src/panels/optimiser/constraintAttainment.ts`, `frontend/src/panels/optimiser/ConstraintAttainmentTable.tsx` | The one pure attainment judgement (`constraintAttainment({kind, bound, achieved})` → bound, achieved, signed slack and slack %, `met`/`breached`; non-finite input throws) and the Constraint / Kind / Bound / Achieved / Slack / Status / λ table Summary and the detail card share. |
| `frontend/src/panels/optimiser/ConvergenceChart.tsx`, `frontend/src/panels/optimiser/FrontierChart.tsx` | Iteration convergence (the solved result's online history or ratebook `ratebook_cd_trace`, or a selected frontier point's bisection, subgradient or coordinate-descent trace, as `IterationLinesChart` small multiples with a `ChartValuesTable`; an online solve without history throws), the selectable frontier slice chart and the Frontier tab's points table, which the result preview draws beside it. `FrontierChart` draws one slice on `ResponsiveChart`, `ChartSvg`, `ChartValueGrid` and `ChartLegend` at the container width with 12 px axes named by the objective column and the x constraint; it keeps the overlap bucketing (one focusable marker per coordinate, showing the selected point when it is in the bucket, else the first in bound order) and keyboard selection, joins only feasible points in bound order (an infeasible point breaks the line), draws a non-converged point hollow and a converged-but-breached point as a cross, and, while no point is selected, reports the hovered or focused point through `ChartFocusDetail`; its ticks span `chartTickSpan`, so a flat series spreads them over its padded axis. Both charts scale through the shared `chartDomain`/`chartTicks`; their axes are labelled by `formatChartTicks` (through `ChartValueGrid`, and directly for the frontier's x axis) and single values by `formatChartNumber`. |
| `frontend/src/panels/optimiser/frontierSlices.ts` | The frontier's pure slice and feasibility model: `frontierConstraintKinds` (each constraint's min/max from the solve's bounds via `effectiveConstraintBounds`; a missing one throws), `assessFrontierPoint` (feasible = `converged` and every constraint's `totals` meets its absolute `bounds` by `constraintAttainment`, swept or not; a missing bound or total throws), `sliceFrontier` (groups the points by the other constraints' `thresholds` with exact equality, since they come from linspace; each slice lists **global** indices in ascending x bound, ties by index), `sliceNeighbours`, `displayedSlicePosition` and `asSolvedOnSlice`. |
| `frontend/src/panels/ChartFocusDetail.tsx` | The polite `role="status"` line under a chart that states the hovered or focused item's exact values, or an optional placeholder (the optimiser's frontier points pass one; the modelling detail lines stay empty until an item is active); AvE bins and frontier points use it. |
| `frontend/src/panels/optimiser/RatebookRatesTab.tsx`, `frontend/src/panels/optimiser/RatebookImpactBeeswarm.tsx`, `frontend/src/panels/optimiser/ratebookFactorTables.ts` | Ratebook Rates tab, impact beeswarm and factor-table helpers over the generated `OptimiserFactorTableRow` (`__factor_group__`, `optimal_scenario_value`, `quote_count`), read directly with no per-row parsing or fallbacks: banding order, `factorRateSpread` (the quote-weighted mean \|ln rate\|, which ranks factors in both views), `levelQuoteShares` (per-level quote shares rounded by largest remainder to sum to exactly 100.0%), `formatVsNeutral`, and `factorTablesCsv(factorTables, collar)`, which appends `combined_factor_min`/`combined_factor_max` to every row. A non-positive rate or a factor with no quotes throws. |
| `frontend/src/panels/optimiser/iterationSummary.ts`, `frontend/src/panels/optimiser/optimiserHelpers.ts` | Iteration copy and optimiser result/save helpers. |
| `frontend/src/utils/banding.ts` | Extracts banding factor-levels/order and resolves an optimiser's explicit or sole direct banding source. |
| `frontend/src/utils/polarsDtypes.ts` | Shared canonical Polars numeric-dtype predicate used by modelling and banding controls, `isStringOrCategoricalDtype` (the String and Categorical dtypes CatBoost trains as categorical features, as the backend detects them; Enum is not one), and the Date/Datetime predicate (`isTemporalDtype`, matching the Date and Datetime dtype strings Polars renders) banding uses to offer Numeric on date columns; `castFamily` names a column type's family in the step renderer's cast table (`cast_family`) and `decimalScale` reads a Decimal type's scale, for the step editor. |

## Key types and data structures

- `TrainResult`/`TrainProgress` and optimiser result/job records are consumed from
  `frontend/src/stores/useNodeResultsStore.ts`; the panels key them by their `nodeId` prop and source/
  structural/config hash state supplied to the shared estimate/job layers.
- Modelling configuration is a `Record<string, unknown>` split into target, feature,
  algorithm, fixed-parameter, required version-1 `evaluation`, and optional version-1
  `tuning` contracts. Displayed numbers are the server's, shown as it computed them: a
  metric value through `formatMetricValue` (four decimals) wherever it appears, so the Lorenz
  label is the Summary's diagnostics Gini; a chart's detail line and values table through
  `formatChartNumber`, as its axes; and a stored fraction through `fractionToPercent`, so
  0.29 reads 29. `trainingConfigurationIssues(config, { targetDtype })` derives incomplete
  target, objective, evaluation and tuning requirements for the Train guard, each refusal the
  server would make: a text-target classification without `positive_class` (`positive-class`),
  a Negative Binomial theta that is not a finite positive number, a reported metric the
  objective cannot report (`training-metrics`, by `metricCompatible`), and one `tuning-config`
  issue per cause (an unsupported family, malformed settings, the trial count, the selection
  metric, the search-space size, no validation rows, which names the Split pane's holdout or
  cross-validation, and more than 200 trial-validation fits). A validation and test total of
  100% or more is its own `evaluation-fraction-sum` issue, and every other incomplete or
  invalid evaluation is one `evaluation-config` issue. `ModellingConfig` passes the
  target's upstream dtype and adds any invalid JSON draft for the selected parameter
  strategy. Top-level
  node config keys `config.split` and `config.cross_validation` have no editor or runtime
  result path (validation settings are edited under `evaluation.validation`, where
  `cross_validation` is an editable method option).
- Optimiser uses input node/banding-node descriptions, `FrontierRangeConfig`, constraints and
  ratebook `FactorTables`. `frontend/src/utils/banding.ts` returns ordered factor levels, including
  a banding default only for the ordering APIs that request it. Per-constraint
  `frontier_ranges` is the sole editable range representation.

## Control flow

### Modelling

1. `frontend/src/panels/ModellingConfig.tsx` reads graph/source/job state, routes the pane
   `resolveModellingPane` selects from `modellingPanesFor(algorithm)` (Target/Features/
   Parameters/Split/Train/Export for every family), the same list `NodePanel` renders as tabs,
   and passes the shared `onUpdate` contract. A tree family (`usesSharedPanes`) composes
   `TargetAndTaskConfig`, `CommonFeatureConfig` (with `EBMInteractionsConfig` for an EBM) and
   `HyperparametersConfig`; the GLM composes `GLMTargetConfig`, `GLMTermsConfig` with
   `GLMInteractionsConfig`, and `GLMRegularizationConfig` on Parameters. It
   hashes the training identity from the config without the export fields
   (`MODELLING_EXPORT_CONFIG_KEYS`: `mlflow_destination`, `mlflow_experiment`,
   `model_export_path`), and the graph's structural fingerprint omits the same keys for modelling
   nodes, so export edits made on the canvas neither mark the cached result stale nor re-request
   the RAM estimate. It
   continuously derives every applicable configuration issue, including the selected
   strategy's JSON-draft issue; reports the panes those issues belong to (`trainingIssuePane`)
   through `onPaneIssuesChange`, keyed by node, for the node panel's tab indicators; lists each
   pane's issues above its body except the codes the body shows itself; and passes the current
   messages and their panes to the Train pane, which lists them beneath the main Train button
   whenever no job is starting or running. A Train/Re-train press with a non-empty list
   suppresses the request. A tree family's Target pane has no independent task
   selector. It always presents every supported loss; selecting one atomically stores the
   loss-derived `config.task` and objective-matched default metrics. Every metric remains visible,
   while metrics outside that loss's regression or classification family are disabled and cannot
   mutate the configuration. Tweedie selection also writes variance power `1.5` when the field is
   absent, then shows the slider immediately; a previously stored power is preserved and there is
   no intermediate warning-button gate (the GLM's Tweedie family instead asks for its power or an
   estimate). Tree-family hyperparameters use `config.params` and
   `config.variance_power`; GLM controls write their algorithm fields directly on `config`,
   including `config.var_power`. `CommonFeatureConfig` uses the shared Polars
   numeric-dtype classifier and final selection, so only included numeric feature rows enable
   their inline downward/dash/upward selector and can write
   `monotone_constraints[name] = -1|1`; choosing the dash removes the key. Inclusion changes only
   `feature_columns`, which starts absent so every feature starts excluded: a stored direction
   remains selected in the disabled control and becomes active again after re-inclusion. GLM's Features pane is `GLMTermsConfig`. `glmDtypeClass` classifies columns as continuous,
   integer, boolean, categorical, or unsupported, pinned to the backend by
   `__tests__/fixtures/glmDtypeClasses.json`; role columns and unsupported dtypes are not offered,
   and the pane states how many unsupported columns it hides. A feature is in the model exactly
   when it has a term, is read by an expression, or is a filled interaction factor. Each row offers
   Add term: the dtype-class default fit first, then a uniquely named `** 2` expression for a
   numeric column whose name is an expression identifier, and each unused encoding for integer,
   boolean, and categorical columns; when nothing is left the button is disabled and says why.
   A `TermCard` per term sits directly under its row with persistent labels, compact wrapping
   controls, a remove action, an inline Advanced disclosure that keeps drafts while collapsed,
   per-field refusal alerts, and the backend's parameter refusals (`termSpecIssues`). Fit menus
   follow the dtype class; a saved fit outside the menu stays visible as unavailable. Spline df
   mode changes through one transition (Auto removes df and knots; Fixed writes df as the larger
   of 5 and degree + 1 and removes k and knots). `NumberField` keeps a draft until a valid
   in-range value commits on blur or Enter, so an edit is one undo step and clearing a required
   field never flips the mode or deletes a sibling setting. Categorical Advanced offers Reference
   level and Levels, which replace each other. Terms on role, missing, or unsupported columns,
   expressions outside the grammar (`__tests__/fixtures/glmExpressionGrammar.json`) or over
   non-numeric columns, and malformed entries are listed under Unresolved terms with the reason,
   the saved fit, and removal. Before the upstream columns are known (`ColumnContext.known` is
   false), the same entries are listed under a neutral Saved terms group with the reason
   "Waiting for the upstream columns", and GLM interaction slots, EBM pairs and `ColumnSelector`
   show saved values without an unavailable flag. An unresolved expression stays editable, and a malformed entry
   keyed by an eligible column offers a type select that writes a valid spec. Lookups use
   own-property checks, so a `constructor` column is ordinary. Rows are tagged "Main effect from
   Interaction N (fit)" when Include main effects materialises one, "Target encoding from
   Interaction N" when a product target encoding registers one, "Interaction only", or "In an
   expression". Fit all with defaults / Remove all terms, In model only, search, and JSON mode
   (which refuses entries without a string `type`) remain.
   `GLMInteractionsConfig` renders interaction cards beneath the feature list with card and slot
   keys that stay with each item for its lifetime in the editor, so removing one never moves
   drafts or disclosure state onto a neighbour. A malformed stored entry renders an error card
   with removal. Each slot pairs a column select (eligible columns whose fits suit the partners; a
   saved factor that is no longer eligible stays selected as unavailable with a warning) with a
   `TermCard` slot. Slot fits follow `resolveSlot` and `slotFitOptions`: an override, the
   column's single inheritable main effect (As main), or its dtype-class default. Monotone
   splines, monotone linear or B-spline mains, level-restricted categoricals, frequency
   encodings, and several main effects require an explicit fit; categorical only sits over a
   categorical main, while linear, splines, and target encoding never do; product target encoding
   keeps one encoded factor with Linear partners. `simulateInteractionDesign` mirrors the
   backend's order-independent resolution to tag materialised main effects and to show
   conflicting materialisations and main-effect conflicts on every card involved. Target encoding
   always includes its main effect; a named encoding's settings are shared, and a differing
   override can be reset. Include main effects shows its help beside the checkbox. Product /
   Target encoding / Frequency encoding modes are offered by dtype class (joint encodings need
   integer, boolean, or categorical factors) and factor-set usage; a mode change keeps only the
   factors and Include main effects, and duplicates are checked per mode. The GLM pane never
   writes `feature_columns`, `monotone_constraints`, or `feature_weights`.
   New algorithms receive a canonical
   random/single-validation evaluation.
   Later strategy changes replace incompatible keys atomically instead of retaining stale
   group/date/fraction fields.
2. `useStaleConfigEstimate` receives the RAM request endpoint with graph/source/structural version;
   it owns abort/loading/error and associates an estimate with the current config. A refused
   estimate keeps the server's message and its `error_code`: `ModellingConfig` passes the
   message to the Split pane only when the code is `evaluation_preview_failed`
   (`EVALUATION_PREVIEW_FAILED`), and `TrainingActionsAndResults` shows that message alone
   and any other under "Memory estimate failed". Every cached
   solve/train result carries the complete canonical identity
   `{ configHash, source, structuralVersion }`; the hook has no partial-result shape. Its guarded
   optional `evaluation_preview` is shown only when the backend can build the exact plan and
   includes development/final-test rows, validation-fit bounds and strategy-specific group/date
   summaries.
3. Training records the `POST /api/modelling/train` job handle as soon as it is returned, after
   which background polling owns preparation/fit progress, polling about once a second while that
   progress moves ([frontend-shared](../frontend-shared/low-level.md)'s progress-aware backoff).
   `TrainingActionsAndResults` keeps a
   distinct Cancel control visible while that job is active; `ModellingConfig` posts its job ID to
   `/train/cancel`, then immediately stores a returned terminal failure/cancellation or completed
   race winner. Structured execution details and additive `error_code`/`http_status_code`/
   `error_detail`/`worker_remote_traceback` fields are retained in progress/error state. A
   terminal status with a `worker_remote_traceback` shows it under "Training failed" in a
   collapsed `<details>` headed **Error details**, as preformatted, horizontally scrolling text, so
   the message's "recorded in the job's error details" has somewhere to point. While the
   estimate waits out an input-snapshot build, `ModellingConfig` holds a waiting flag (set by
   `onWaiting`, cleared when that estimate settles or is aborted) and
   `TrainingActionsAndResults` shows "Waiting for the input snapshot to finish" in the
   estimate's place, as a neutral `role="status"` notice rather than a warning, and disables
   Train and Re-train until it clears. Both the estimate warning and the
   terminal `gpu_vram_limit` message require an explicit CPU selection and retry rather than
   describing an automatic fallback. The optional GLM dispersion action calls the dispersion API
   and writes a successful theta/variance-power estimate through the ordinary editable update
   callback. `ModellingConfig` passes `GLMTargetConfig` a `dispersionEstimate`: its `run` (the
   current graph, node id and active source) and its `scope`, one key of the document's
   `documentExecutionKey`, the node id, the active source, the structural version and the hash
   of the node's training identity (`trainingIdentityConfig`, which includes the estimated
   `theta` or `var_power`). An estimate keeps the scope it started under; when the scope
   changes, `GLMTargetConfig` aborts it (which cancels the job), the buttons are free again and
   a neutral `role="status"` note reads "Estimate stopped: the training settings changed while
   it ran."; a reply is filled in only while the scope is still the one it started under, so a
   late value for target A never lands on target B's manual theta. `GLMTargetConfig` aborts an
   estimate still running when it unmounts, which cancels the job and applies no value; a
   failed cancellation (on unmount or a scope change) is reported as an error toast.
4. `frontend/src/panels/ModellingPreview.tsx` computes which tabs have result data, renders only
   those, and resets the active tab when a new result arrives. `SummaryTab` separates selection
   estimates from final-test metrics, renders ordered validation fits and tuning
   baseline/winner/improvement evidence, and exposes diagnostics rather than suppressing a
   partially successful training result. When `diagnostics_set` is `"development"` and
   `evaluation.validation_method` is not `"none"`, its first metric card is the validation
   fit's `evaluation.selection_metrics` (each metric's `mean`), titled "Validation, N rows"
   for holdout or "Validation (K-fold mean), N rows" for cross-validation, N being the
   summaries' `validation_rows` and K `validation_fit_count`; the in-sample diagnostics
   card follows. Summary cards carry only their titles; what each one measures is in the
   docs. A `"final_test"` result keeps its test metrics first, and a run without
   validation is unchanged. A node with no result shows its data preview, whether or not the
   results store records its remembered result as expired.
5. `parseTrainStatusResponse` preserves explicit `null` values in categorical PDP grids;
   missing value fields, non-scalar values and null numeric grid values remain invalid.
   `PdpTab` displays the null level as `(missing)` without changing its prediction or
   conflating the payload with a literal string. `getTrainStatus`, `getOptimiserStatus`,
   `getExploreStatus` and `getExplorePivotStatus` wrap parser failures
   in `ApiResponseValidationError`, retaining the original cause and field detail.
   Every job poller treats that error as terminal for result delivery, removes the active
   job and displays the error. Request failures and lazy-module loading failures retain
   normal retry behavior. A running-to-completed nullable-category response must publish
   the completed result, including its missing-level chart, and clear running progress.

### Optimiser

1. `frontend/src/panels/OptimiserConfig.tsx` finds direct inputs and candidate banding sources
   whatever pane is active, so ratebook defaults persist and the Solve issue list is current.
   It uses cached node columns, provided upstream columns, then `useDataInputColumns` as needed.
   Ratebook mode may set an unconfigured source and inferred factor columns from banding levels
   in one atomic update, while an explicitly configured empty factor list is preserved.
2. Solve submission builds the current graph and records job/result state. Config estimates use
   the same stale-config pattern as modelling. API execution diagnostics are preserved in action
   errors/progress.
3. `useOptimiserAutoRange` is the only authority for auto-range state and
   request identity. Starting or restarting increments a monotonic generation,
   captures the current document execution fence and structural version, aborts and best-effort cancels
   the prior owned job, and waits for the status through the shared
   `waitForJob`, polling every second with the generation's abort signal.
   Only the current generation may publish terminal state or apply completed
   ranges, and completed output is accepted only when every configured
   constraint has a returned finite range. The run's scope is the node id, the
   structural version and the execution fence (source file, execution generation,
   load status, execute capability, graph synchronisation), as the solve's: a
   document/config replacement or unmount retires and cancels the active
   generation, while a save, which advances only the source revision, leaves it
   polling and publishing its ranges. The action remains
   available as **Restart auto range** while a request is active. A locally
   aborted or superseded request is silent; a cancelled or other terminal
   status returned by the server is shown in the reducer-owned auto-range
   error area.
4. `frontend/src/panels/OptimiserPreview.tsx` picks the available result tabs and the selected
   frontier point, which is also the publish target (`selectedPointIndex`, `null` = the solved
   result). Every frontier-point request in the workspace — the Rates materialisation, the
   Adjustments point report, the Segments index and breakdowns, the Quotes pages and the Export
   pane's factor-table load — goes through `useLatestRequest` with one policy. The view names the
   request it wants by an identity key, or none; a request is sent while it is wanted and has not
   failed. A new identity, none, or unmount aborts the request in flight, and a browser abort
   discards its reply or error silently (the server cancels a point apply no request waits for
   any more, OPT-PC02). A 409 whose `error_code` is `frontier_point_apply_replaced` (another
   point's apply took the job's queue) is not an error: the request is reissued while the view
   still wants that identity. A 410 shows the server's message with no Retry (only a new solve
   helps). Any other failure, including a reply the view cannot use, shows the message with
   **Retry**, which reissues the request; a failure is remembered for its identity until Retry.
   In ratebook mode a selected point without tables is materialised only on Rates, Summary and
   the Frontier pane that carries Summary; the request's identity is `(jobId, point)`, accepted
   tables persist in the result store, and a reply without tables is a failure ("No rate tables
   were returned for this selected point."). Save and MLflow requests always send the target explicitly (`point_index` omitted for the
   solved result) and never consume the server-side result, so they and the Quotes view may run
   in any order. `OptimiserPreviewData` carries `solvedResult` (the store's as-solved
   `originalResult`) beside the displayed `result`: Convergence (offered for every result) draws
   the history or CD trace of `solvedResult` while no point is selected, and the Frontier tab's
   as-solved marker reads it, so selecting or stepping a point never moves the marker. With a
   point selected, Convergence loads that point's trace through `POST /frontier/select` with
   `include_history: true`, only while the tab is open, under the shared point-request policy
   with identity `(job, point)`; a reply without a trace is a failure with **Retry**, and a
   loaded trace is kept for the review (`pointHistories`), so stepping back to a point already
   loaded makes no request. It dispatches on the trace's `kind`: `bisection` charts by step the
   probed λ with `bracket_lo` and `bracket_hi`, each constraint total against the point's bound
   (the displayed result's `effective_bounds`) with a marker at the last accepted probe, and the
   objective, over a closed values table; `subgradient` uses the online small multiples and
   `ratebook` the CD-trace view, both against the point's bounds. Above it: "Frontier point N:
   converged|not converged, K iterations"
   (K from `formatOptimiserIterationSummary` of the displayed result; omitted when the point
   reports none). The tab is owned by the `(nodeId, jobId)` pair and resets to its default
   (Frontier when the frontier has points, else Summary) when either changes, adjusted during
   render so a stale tab never paints; it does not reset when only `result` changes, because
   every stepper press builds a new displayed result. The X-axis constraint and slice choices
   reset with it. `result.warning` renders as an amber `role="status"` strip in the workspace notices slot.
   Summary shows the solved result's adjustments compactly ("Adjusted up", "Adjusted
   down", "Unadjusted" only when the grid has 1.0, and "At the range edge", by quote count; the
   edge share is the union of the minimum and maximum steps, so on a one-step grid, whose only
   step is both, it is that step's share, never their sum) with
   a **View adjustments** button that opens the Adjustments tab. With a frontier point selected
   (whose summary carries no report) Summary shows no adjustments block and no button; the
   point's report loads in the Adjustments tab.
   **Adjustments** (every result, online and ratebook; a ratebook report describes the step
   price-contour evaluated for each quote) shows the as-solved report
   (`solvedResult.adjustments`) when no point is selected. With a point selected it loads that
   point's report through `POST /frontier/select` with `include_adjustments: true`, only while
   the tab is open, under the shared point-request policy with identity `(job, point)`: a new
   point or closing the tab aborts it, a replaced 409 is reissued, a 410 has no Retry, and a
   reply without a report is a failure with **Retry**. A loaded point
   report is kept for the tab's review (node, job, point), so stepping
   back to a point already loaded makes no request. An as-solved result without a report
   shows the result's `"adjustments"` diagnostic message; one with neither throws. A ratebook
   report (`deployed_factor_differs` not null) adds, under the shares, "Deployed factor differs
   from evaluated step: N quotes (x%)" with the note that inside the scenario range the Optimiser
   Apply node deploys the unsnapped product of the rates while the solve evaluated the nearest
   step, and that a product past a grid edge deploys at the edge; Summary's compact summary adds
   the same count as "Deployed ≠ evaluated step". An online report (`null`) shows neither. The chart is
   a categorical `HistogramChart`: one bar per grid value (empty bars included), labelled by the
   grid value, x axis "Scenario value (1.0 = base price)", y axis the chosen weighting ("Quotes"
   or its label), and a dashed "1.0 = base price (no adjustment)" line at the 1.0 bar or
   interpolated between its neighbours (none when 1.0 is outside the grid, which a note states).
   **Weight by** (`aria-pressed` buttons) offers Quotes and each weighting the report computed;
   a refused weighting is named with its reason. Under the chart: a quantile row (P5, P25,
   median, P75, P95, mean), the shares (at range minimum and at range maximum, each labelled with
   its grid value, or on a one-step grid a single "At the range edge (v)" row, since its only step
   is both; a one-step report whose two edge shares differ throws), a `ChartFocusDetail` line for the active bar, the note
   "The scenario grid has no 1.0 step, so no quote is unadjusted." when `has_unadjusted` is
   false, and a closed values table (Step | Scenario value | Quotes | Share of quotes, plus the
   weighting and its share when one is chosen).
   **Segments** (every result) breaks the chosen scenarios down by the result's
   `segment_keys` (analysis columns, and a ratebook result's rating factors). With no key it
   shows "Tick validation factors in the optimiser's Factors pane". Otherwise it uses
   `FeatureDiagnosticLayout`: the keys in a searchable browser ranked by the index statistic,
   named in the browser's header ("Adjustment spread", the quote-weighted standard deviation of
   the levels' mean scenario values), from `GET /segments/index` for the target; until the index
   arrives the keys are listed unranked, in catalogue order with no bars, and an index failure
   leaves them unranked with the message and **Retry**. The selected key and the search are the
   Rates tab's (`OptimiserPreview`'s review state), so choosing a factor in one selects it in the
   other; a selection that is not a key here shows the first ranked key. An unavailable key shows
   its reason and makes no request. An available key loads `POST /segments` for the target (the
   selected point, else the solved result) and the chosen weighting. The index and each
   breakdown follow the shared point-request policy: switching key, weighting or point aborts the
   request in flight, a replaced 409 is reissued, a 410 has no Retry and anything else offers
   **Retry**. Loaded breakdowns and indexes are kept for the review, by
   `(job, target, key, weight)` and `(job, target)`, so returning to one
   makes no request. The chart is `RelativityBars` of each level's mean chosen scenario value
   under the weighting (the unweighted mean for Quotes) around the 1.0 base line, in the
   response's level order, with an aligned quote strip (0 to the largest level's quotes); a level
   whose weighted mean is unavailable draws no bar and shows "—". Hovering or focusing a level
   fills the `ChartFocusDetail` line (level, quotes and share, the mean against 1.0, the shares
   adjusted up and down and at the range edge, and for a ratebook result the deployed-factor
   count). **Weight by** offers Quotes, the objective and each constraint at the chosen scenario;
   the response's diagnostics (a refused weighting, a zero-weight level) are listed under it. A
   closed values table lists Level | Quotes | Mean (unweighted) | Mean (the weighting) | Adjusted
   up | Adjusted down | At range edge (and Deployed ≠ evaluated step for a ratebook result), with
   "—" for an unavailable weighted figure.
   **Quotes** (every result, online and ratebook) explores the target's chosen scenarios one
   server page at a time (`POST /apply` with the query; the selected point, else the solved
   result). The table (`SortableValuesTable`, labelled "Per-quote detail") shows the response's
   `columns` in order: Quote ID, "Scenario value" with a text glyph against 1.0 ("▲" above,
   "▼" below, "=" at 1.0, and an accessible "adjusted up/down/unadjusted" label, so the
   direction never rests on colour), "Objective", each constraint by name, for ratebook "Factor
   product" and "Deployed ≠ evaluated" ("Yes"/"No"), and each analysis column; numbers through
   `formatValue` (grouped, at most four decimals), a missing analysis value as "—". A sortable header (`sortable`) is a sort button
   with `aria-sort`: the first click sorts ascending, the next descending, alternating; sorting
   resets the offset. **Presets** (`aria-pressed` buttons): "Highest adjustment" (scenario value
   descending), "Lowest adjustment" (ascending), "At range edge" (the `at_range_edge` filter, no
   sort) and, for a ratebook result, "Deployed ≠ evaluated" (the `deployed_factor_differs`
   filter); a preset replaces the sort and filters and resets the offset, and it is pressed
   while the query is exactly its own. A filterable analysis cell is a button that adds
   `analysis_equals` for that value ("Show only quotes with region = North"); each active
   filter is listed as a removable chip. **Search** ("Search quote IDs", a `ValuesTableSearch`)
   sends `quote_id_prefix` 300 ms after typing stops (an empty box sends none) and resets the
   offset. The **pager** states "Showing a–b of M matching (of N)" (or "No quotes match (of
   N)"), with Previous and Next moving by the limit; Next is disabled at the last matching row
   and at the depth limit (10,000 rows), where a note asks to narrow the filter or search.
   Quotes reads `/apply` through the result store's apply cache (see Edge cases); a page the cache
   lacks is requested under the shared point-request policy, identified by its cache key: a page
   the query or target moved past is aborted, a replaced 409 is reissued, a refusal (400, 422,
   507, or any other 409) shows the server's message with **Retry**, and a 410 shows it without. The MLflow log request carries the node's current `mlflow_destination` (found
   through `allNodes` by `nodeId`; `""` for Auto) and the Export pane derives availability from
   that destination alone. It publishes through `useOptimiserPublishStore`, whose state belongs to
   one solve job. For a ratebook frontier point without tables, Export offers **Load factor
   tables for CSV**; the load follows the shared point-request policy with identity
   `(jobId, point)`, so changing the target or the job aborts it, and while its failure shows
   (with Retry, or none for a 410) the button is not offered. Its reply is stored through
   `recordFrontierPointSummary`, which drops a reply for a job the node does not hold and never
   changes the selection; a reply without factor tables is a failure. The combined-factor collar
   sentence prints the solve's `combined_factor_bounds` through `formatScenarioValue`, so the
   Float32-widened grid ends read as the grid's values (`0.8`, not `0.800000011920929`); the CSV
   repeats the exact bounds.
5. `useOptimiserScenarioData` caps rows at 5,000 before grouping by quote, orders scenario rows,
   and calculates full-preview statistics only while a Statistics pane is open (the data preview's
   tab, or the result workspace's). `App` passes `OptimiserPreview` the active node's `ok`
   preview as `scenarioData`; the result workspace offers Curves and Statistics only when those
   rows hold an objective and scenario rows.

Memory notices use the headline “{Profile} reached {threshold}% of its memory
allowance.” Their details separately identify memory used, the process limit, and
memory remaining at the recorded event. Negative remaining memory is labelled
“Memory over limit”; remaining memory is never presented as the denominator of
memory used. GiB-scale values use readable GB units. Common stages use plain-language
labels (including “Caching the dataframe”), and adaptive limits are described as
automatically set from available RAM; explicit configuration keys remain available.
These wording changes do not change thresholds, severity, or failure precedence.

A structured `error_code: "memory_limit"` error detail carrying no authored `message` is
never shown as its raw JSON or reason code: `executionErrorDetailMessage` renders it in
plain language chosen by its closed `reason`, with GB-scale byte values:
`worker_may_have_exceeded_memory_limit` — the process running it stopped abruptly, most
likely because it ran out of memory; `worker_memory_exhausted`, `worker_memory_limit`,
or an unknown reason — it ran out of memory before it finished;
`worker_rss_limit_exceeded` — it used `rss_bytes`, over its `rss_limit_bytes` limit;
`rss_exceeds_memory_limit` — it needed more than its `memory_limit_bytes` allowance;
`process_rss_limit_exceeded` — at admission (the detail carries `rss_at_admission_bytes`)
not enough memory is free to start because Haute already uses that much of its
`process_rss_limit_bytes` limit, otherwise Haute reached its process limit (the running
execution's effective `rss_limit_bytes`) while running it; `in_flight_memory_budget_exceeded` — another job is running,
named from the detail's `in_flight_operations` (`<profile>:<operation>`) by a fixed map of user-facing job names
(Auto range, Optimisation, Solve estimate, Model training, Training preview, Explore, Data
caching, Data output; an operation outside the map is left out, never shown by its internal name), so try again
when it finishes — a solve estimate refused while auto-range runs reads "Solve estimate failed: Another job is
running (Auto range). Try again when it finishes."; `native_memory_cap_unavailable` — Haute cannot enforce
its memory limit on this machine; `memory_sampler_unavailable` — Haute stopped it because
it could not measure its memory use. A byte value missing from the detail is omitted
from the sentence rather than invented. Each ran-out-of-memory outcome (the first four,
and a process limit reached while running) adds the action: filter rows or drop columns
earlier in the pipeline to reduce the memory it needs.

`ExecutionDiagnosticsSummary` consumes the guarded versioned metrics contract
and renders only actionable memory pressure, a rejected strategy, or a `warned`
conservative strategy, with technical collections behind disclosure. Memory
pressure takes precedence over a warned strategy, and the strategy-specific
disclosure label is used only when the rendered diagnostic is the strategy
itself. `useDataInputColumns` consumes
guarded schema/preview results keyed by node/config/source generation and never
derives columns from path, provider internals, or a snapshot-build side effect.
The Banding classifier supplies both ordered healthy levels and named
zero-level issues; `OptimiserConfig` compares any explicit source id with the
current direct-Banding candidates and renders one aggregate accessible alert
without broadening the exactly-one-direct fallback.

## Edge cases and invariants

- `ModellingPreview` uses `PreviewPanelTabs` with `equalWidth` and an ID prefix
  connecting each tab to its `tabpanel`. The strip has a 112px minimum per view
  inside a horizontal scroll container. Summary is selected initially and when
  node/result identity changes; collapse/expand retains the selected view.
- `ModellingPreview` and `OptimiserPreview` render through `ResultsWorkspace`. Each
  tab's `aria-controls` names its pane and the pane's `aria-labelledby` names its tab
  (`<idPrefix>-<tab>-tab` / `<idPrefix>-<tab>-pane`). Focus view keeps the active tab,
  closes on Escape and restores focus to its toggle; each workspace remembers its
  own docked height in `useUIStore` (`modellingPreviewHeight`,
  `optimiserPreviewHeight`, both 420px initially) for the session.
- The optimiser accent is its own token pair (`--optimiser-accent`,
  `--optimiser-accent-soft`), never a warning colour: the stale strip and breached
  statuses use the warning palette, and a warning accent would make the whole pane
  read as a warning.
- No optimiser tab has an intro (`intro={null}`); the clamp-rate definition (the mean,
  over every grouped solve, of the fraction of (quote, candidate) targets strictly
  outside the scenario range; quotes at a grid edge not counted) is documented.
- A frontier result offers no Summary tab: the Frontier pane renders `SummaryTab` below
  its chart and points table, and materialises a selected ratebook point's rates there
  as the Summary and Rates tabs do. `SummaryTab` shows no adjustments block for a
  selected point (its report loads in the Adjustments tab). The points table scrolls
  itself, not the pane, to bring the selected row into view.
- The Rates tab uses the per-feature diagnostic layout: a browser of factors ranked by
  rate spread (quote-weighted mean |ln rate|, how far the factor's rates move from 1.0),
  with search, beside the selected factor's `RelativityBars` in banding order and an
  aligned quote-count strip (muted bars scaled 0 to the factor's largest level count,
  the active level emphasised, as AvE's exposure strip). Every level row is focusable;
  hover, focus or click fills the detail line (level, rate, % vs neutral 1.0, quotes,
  share of the factor's quotes). From 25 levels the rows compact and level labels thin
  with `chartLabelIndices`, always keeping the first and last. A closed "View rate
  values" table lists Level | Rate | vs neutral 1.0 (%) | Quotes | Share in banding
  order; "vs neutral 1.0" is the rate against the unadjusted base price, and the shares
  sum to 100.0%. The selected factor and the search (shared with the Segments tab) live in `OptimiserPreview`'s review
  state, so they survive tab switches and frontier-point steps and reset with the tab on
  a new job or node.
- The Summary beeswarm ("Mechanical Price Effect") draws on `ResponsiveChart` at the
  pane's pixel width, with no viewBox scaling and no minimum width; label and colour-bar
  margins shrink with the width. It shows the eight factors with the largest rate spread
  and says "Showing top 8 of N factors" with a Top 8 / All toggle whenever there are
  more. Dots are coloured by the level's numeric value where the label has one; levels
  without one (categorical factors, or labels such as "missing") use the neutral colour,
  which a legend note names, and the focus detail says "no value order (categorical
  level)" in words. Every dot is focusable and fills the detail line; a closed values
  table lists Factor | Level | Rate | vs neutral 1.0 (%) | Quotes for the factors shown.
- The optimiser result workspace shows no provenance strip (the header and point stepper
  carry the mode's convergence, the quote count and the point shown). On the Curves
  and Statistics tabs the header subtitle is the sampled input's metadata instead
  (quotes, scenarios, and the preview rows used when the sample is truncated).
- The optimiser Summary shows the result's `diagnostics_errors` in the shared
  `DiagnosticsIssues` alert above its columns ("Scenario-value statistics",
  "Efficient frontier"), so a degraded result says what is missing and why. A
  selected frontier point's summary carries none.
- The Frontier tab plots each typed point's `totals[x constraint]` against its
  `total_objective`, one **slice** at a time. The x constraint is one of the
  frontier's `swept_axes` (the "X axis" picker appears when there are two or
  more); a slice is the points whose other constraints' `thresholds` are equal,
  drawn as a line in ascending x bound. With another swept axis a "Holding
  <other> at" select picks the slice (one select naming every other swept axis;
  its options are the slices, so a truncated grid never offers a missing
  combination; a held threshold of a percentage constraint, by the solve's own
  `constraints`, reads as its percentage of baseline, "95% of baseline"). The footnote states the slice size and, when the payload is
  capped at `FRONTIER_POINT_LIMIT`, the truncation. **Global identity:** every
  point keeps its global index through slicing, overlap grouping, the stepper,
  the values table and `/frontier/select`; nothing uses a slice-local index. The
  displayed slice is the one the user chose, until the selection changes; then
  it is the selected point's slice (so a point selected from Summary or the
  stepper brings its slice into view); with no selection and no choice it is
  point 1's slice. The choice belongs to the review (reset with the tab) and to
  the x constraint it was made under. The header stepper
  steps to the selected point's bound-order neighbours within its slice and is
  disabled at the slice's ends; its label keeps the global "Point i of N".
  **Feasibility** is haute's own judgement in both modes, since online
  `converged` includes a tolerance check and ratebook `converged` only says the
  factor values stopped moving: a point is feasible when `converged` and every
  constraint's total meets its absolute bound (`bounds`, never the pct
  `thresholds` fraction), judged by `constraintAttainment` with the kind from the
  solve's `effective_bounds`. The as-solved anchor comes from `solvedResult` and
  always renders; it is on the slice when every other swept axis's solve bound
  equals the slice's bound exactly, and otherwise is drawn hollow with the legend
  entry "As solved (different slice)". The y axis is named by the node's
  objective column while the result is not stale, else "Objective". A
  table ("Frontier slice points", always open beside the chart) lists the slice's points: Point,
  <x> bound, <x> achieved, Objective, Converged, Iterations, Status; the selected point's row is
  `aria-current`, highlighted in the optimiser accent and scrolled into view, and each row's
  "Point N" button (or the row) selects that point.
- `/apply` responses are cached in `useNodeResultsStore` under their full request identity
  `(jobId, target, query)`: `target` is `"solved"` or the frontier point
  index, and `query` is
  the canonical (key-sorted) request query beyond the target: `sort_by`, `descending`,
  `quote_id_prefix`, `filters` and `offset`/`limit`, always fully specified (`null` and `false`
  for what is unset, an empty `analysis_equals`), so one query has one key, and the request body
  is exactly the query plus the job and point. At most 16 entries are kept,
  least recently used evicted first. Installing a solve result for a node (a new job) drops that
  node's entries for any other `jobId`; a failed solve or clearing or evicting the node's result
  drops all of the node's entries. `recordOptimiserApply` accepts a response only when its
  identity's job and target are the node's current ones, so a late response from an
  earlier job or target is discarded and never displayed; QuotesTab additionally
  drops a response whose query is no longer its current query. A cache hit makes no request and
  marks the entry most recently used.
- The Frontier tab lays the chart beside its points table at equal widths and stacks the
  chart above it when the workspace container is at most 640px wide. Its labels and footnote use
  the workspace type scale (12-13px, sentence case), not 9-11px uppercase labels.
- Each non-summary modelling view has a heading and explanatory sentence above its existing
  diagnostic component. The scrollable body remounts on view changes so a new view
  starts at the top. View availability continues to follow actual result data.
- `modelling/SummaryTab` groups results into bordered, elevated cards using the
  existing theme tokens and model accent. Performance cards precede model metadata;
  final-test metrics and diagnostic metrics retain separate names and descriptions, and
  the validation card that leads in-sample diagnostics carries its own row-count title.
  Responsive grids follow the panel's available width. Values use tabular numerals,
  long labels/paths wrap, and detailed tables scroll within their cards. Evaluation,
  tuning ranking, parameter application and MLflow payloads are unchanged.
- Focused preview tests cover labelled keyboard view switching for both algorithm
  result shapes and reset to Summary on replacement. Summary tests cover semantic
  metric groups, explicit no-test messaging, full paths, the GLM regularisation block and
  smooth terms, alongside existing selection/tuning/warning evidence. The Coefficients view shows
  the inference reason, dashes, no significance legend, and design order when inference is not
  valid, and names robust standard errors; Relativities draw whiskers only when bounds exist.

- With no modelling algorithm, configuration sections that require it are not rendered. Hiding a
  GLM/regularisation/config subsection preserves its stored values for later re-selection.
- The start request itself has no Cancel control because no job handle exists yet; once the handle
  is registered, cancellation remains available in both preparation and fit progress states.
- Dispersion estimation is an explicit action. Missing/failed estimates remain visible as a field
  gate/error rather than being silently defaulted.
- `frontend/src/utils/banding.ts` returns `{}` for a missing/invalid/non-banding source; when no
  explicit optimiser source exists it falls back only if exactly one direct banding input exists.
- Auto-range failure is atomic for constraints: a completed response missing any configured range
  fails instead of partially updating ranges. Unmount cancels active work best-effort.
- Constraint renames and removals update `constraints` and `frontier_ranges` atomically, preserving
  the renamed range and deleting an orphan on removal. A range field reads only its named
  `frontier_ranges` entry and never falls back to global `frontier_min`/`frontier_max`.
- Strict frontier/detail and scenario-statistics helpers handle empty data and zero visual spans,
  but throw on present malformed required numeric fields. The pre-solve chart separately coerces
  missing series/scenario values with `Number(... ?? 0)`. Ratebook tables preserve factor level
  order where supplied.
- Optimiser data preview distinguishes no objective, no preview rows and no quote data; its quote
  and series selection are local presentation state, not changes to the solve config.

## Error handling

Action errors prefer structured API/execution details. Training/solve errors are retained in the
result store and shown locally; estimate/MLflow/export/materialisation actions show their own
messages. A rejected frontier point-index contract fails the Rates request and surfaces the
error instead of leaving Rates loading. An aborted point request and a replaced point apply
(409 `frontier_point_apply_replaced`) do not flash a failure. Render-time numeric contract violations in strict frontier/statistics
helpers are intentionally not coerced.

## Testing

Top-level coverage lives in `frontend/src/panels/__tests__/ModellingConfig.test.tsx`,
`frontend/src/panels/__tests__/ModellingPreview.test.tsx`,
`frontend/src/panels/__tests__/ValidationWorkspace.test.tsx` (the modelling workspace: Focus
view, remembered height, diagnostics strip; unchanged by the shell extraction, so it guards it),
`frontend/src/panels/__tests__/OptimiserWorkspace.test.tsx` (the optimiser workspace on the same
shell: tab/pane ARIA wiring, Focus view Escape keeping the tab, remembered height, no
provenance strip or tab intros, and the Frontier tab's narrow-width stacking rule),
`frontend/src/panels/__tests__/OptimiserConfig.test.tsx`,
`frontend/src/panels/__tests__/OptimiserPreview.test.tsx`,
`frontend/src/panels/__tests__/OptimiserPreview.storeIntegration.test.tsx`, and
`frontend/src/panels/__tests__/OptimiserDataPreview.test.tsx`, and
`frontend/src/panels/__tests__/optimiserScenarioStats.test.ts`. The optimiser preview suites
build results from the shared fixtures in `frontend/src/panels/optimiser/__tests__/fixtures.ts`
(an online solve with its as-solved adjustment report and history carrying λ and constraint
totals; typed online frontier points with `thresholds`, `bounds`, `totals` and `lambdas` maps,
`converged`, `iterations`, `solver_path` and the `sv_*` statistics, and matching point summaries; a ratebook solve with factor tables carrying
`quote_count`), so selected-point views are exercised with real-shaped data rather than nulls.
`frontend/src/panels/optimiser/__tests__/QuotesTab.test.tsx` covers the Quotes explorer: the
formatted columns and the up/down glyph with its label, `aria-sort` cycling ascending and
descending with the matching request, each preset's request and pressed state (ratebook's
only for ratebook), the analysis-value filter and its chip, the debounced search, the pager text
and Previous/Next offsets, the depth note, a response for an earlier query dropped, a replaced
409 reissued without an error, any other 409 shown with Retry, Retry, the 410 state without it,
and a ratebook page's factor product and flag. `frontend/src/panels/__tests__/GLMComponents.test.tsx` stays green over the
extracted `SortableValuesTable`.
Store-integration tests cover the apply cache (no second request on reopening Quotes, late
responses from an earlier job discarded, the 16-entry bound), the tab kept across stepper presses and reset on a new job or node, Convergence
kept after point select, the fixed as-solved marker, the warning strip,
Retry, and on Rates a replaced 409 reissued without an error and a 410 without Retry.
`frontend/src/panels/__tests__/OptimiserConfig.test.tsx`'s Publish section covers the Export
pane's factor-table load (the request, a load aborted when the target moves on with the target
kept, a 410 with no Retry and no Load button, Retry after another failure, a replaced 409
reissued) and the collar sentence printing Float32-widened bounds as `0.8` and `1.2`;
`frontend/src/panels/__tests__/OptimiserDataPreview.test.tsx` covers the Statistics tables'
shared number formatter carrying 999,999 into `1.00M`.
`frontend/src/panels/__tests__/IterationLinesChart.test.tsx` covers the shared chart's
real tick values, distinct labels on a narrow value range, log axis, reference lines, marker,
gaps and loud failures, and
`frontend/src/panels/optimiser/__tests__/AdjustmentsTab.test.tsx` covers the Adjustments view:
the axis labels, one bar per grid value with empty bars, the base-price line, the Weight by
switch, the quantile row and shares, the no-1.0 note, focusable bars with the detail line, the
lazy point load only while the tab is open, a fast stepper whose earlier replies are discarded,
a replaced 409 reissued without an error, Retry, and the 410 state;
`frontend/src/panels/optimiser/__tests__/SegmentsTab.test.tsx` covers the Segments view: the
empty state, the keys unranked while the index loads and then ranked with the statistic named,
search and select, the shared selection, an unavailable key's reason with no request, the chart
and detail line, the values table with "—" for a zero-weight level, the Weight by switch, abort
on key switch, a point target, Retry and the 410 state without it;
`frontend/src/panels/modelling/__tests__/ValidationDistributionTabs.test.tsx` keeps Residuals on
the extracted `HistogramChart` unchanged, and
`frontend/src/panels/optimiser/__tests__/ConvergenceChart.test.tsx` the online small multiples
(bound lines, first-feasible marker, a zero λ change on the log axis), the ratebook trace view,
its truncation note, the values table and the "live solves only" state, and a selected point's
bisection, subgradient and ratebook traces against that point's bounds, one request per point,
the loading state and a failed load's **Retry**.
`frontend/src/panels/optimiser/__tests__/frontierSlices.test.ts` covers slicing a 2×3 grid,
the single-constraint identity, feasibility (non-converged, converged-but-breached including
an unswept breach);
`frontend/src/panels/optimiser/__tests__/FrontierChart.test.tsx` the axes, legend, markers, the
overlap marker showing (and a click keeping) the selected point, the
feasible-only line and the as-solved anchor;
and `frontend/src/panels/__tests__/OptimiserPreview.test.tsx` the slice switching, the points
table's highlighted and clickable rows,
global-index selection, the in-slice stepper, and the Curves and Statistics panes after a solve
(quote navigation replacing the point stepper, no panes without preview rows or an objective). Modelling subcomponents have suites
under `frontend/src/panels/modelling/__tests__/`:
`frontend/src/panels/modelling/__tests__/SummaryTab.test.tsx` proves the validation card leads a holdout-validated refit's in-sample diagnostics with its row count
while test metrics still lead a test-set run, and
`frontend/src/panels/modelling/__tests__/TrainingRunSummary.test.tsx` the CatBoost
categorical encoding. `frontend/src/panels/modelling/__tests__/ShapBeeswarm.test.tsx` proves
the beeswarm lays out one row per feature in the given order with one dot per row value,
paints every dot on the canvas in its rank colour or the neutral colour, shows the legend note
for unordered dots, keeps dots inside their row at any density, finds the nearest dot to the
pointer and names its feature, value and SHAP value, gives each feature row one tab stop whose
focus states its rows and SHAP range, and lists per-feature rows and minimum, median and maximum
SHAP in its values table; `frontend/src/panels/modelling/__tests__/ShapCurvesTab.test.tsx` proves a
log-link result shows relativities around 1.0 and an identity-link one mean SHAP around 0, a
numeric curve draws its bands in order with the percentile area and a separate missing point,
a categorical curve draws a bar per level with whiskers and the omitted-level note, focus names
the point's rows, mean SHAP, relativity and range, and the values table lists every point;
`frontend/src/panels/__tests__/RelativityBars.test.tsx` also covers a non-1.0 baseline;
`frontend/src/panels/modelling/__tests__/FeaturesTab.test.tsx` shows the SHAP beeswarm button
only for a result with beeswarm rows, and that choosing it replaces the search and Features
shown controls with the top-20 line. `frontend/src/__tests__/App.integration.test.tsx` proves the results
panel of a Model Training node shows its data preview when its remembered result has expired
or no training was remembered, and the result panes when a result is present; optimiser helper/frontier coverage is under
`frontend/src/panels/optimiser/__tests__/`. `frontend/src/__tests__/utils/banding.test.ts` covers
the factor-level and optimiser-source utility. Smaller visual summaries/charts are also exercised
through their parent preview tests; not every helper has a dedicated test file.
`frontend/src/panels/__tests__/RelativityBars.test.tsx` covers the shared bar list (caller order,
token colours, whiskers kept inside the track, loud non-finite values, focusable rows, the aligned
side strip, compact label thinning), `frontend/src/panels/__tests__/GLMComponents.test.tsx` the GLM
relativities built on it, `frontend/src/panels/optimiser/__tests__/RatebookRatesTab.test.tsx` the
Rates tab (rate-spread ranking and search, banding order, the values table and shares summing to
100, the quote strip, keyboard detail, high-cardinality thinning, the lifted selection), and
`frontend/src/panels/optimiser/__tests__/RatebookImpactBeeswarm.test.tsx` the beeswarm (dot
labels and value colours, weighted ordering, pixel-width geometry with no viewBox or minimum
width, the top-8 notice and toggle, the categorical note and focus detail, the values table).

Performance regression coverage for background progress rendering is in
`frontend/e2e/job-progress-render.benchmark.spec.ts`.

Focused diagnostics tests cover rejected-strategy and memory-pressure detail,
structured request failures, contract-error retention, and the absence of
invented planner state. Data-input tests cover multiple roots/direct parents,
explicit selection, snapshot-backed post-Polars columns, missing-snapshot
diagnostics, and no implicit build. Optimiser warning tests cover missing
explicit sources and mixed healthy/zero-level outputs. The deterministic
`frontend/e2e/canvas-assurance.spec.ts` journey persists constraint/range
fields, selects and applies the backend `point_index`, and intercepts the
MLflow API to assert request/result identity without contacting a live service.
After its solve it walks every online pane: the Solve pane's status against the
as-solved result, the tablist, the Summary attainment row
(Bound, Slack, Status consistent with each other), frontier point 2 (selected by its own chart
marker, which no other point of the fixture's frontier shares) highlighted in the points table
and its attainment on Summary, the Adjustments bars and base-price
line, a Segments level of the fixture's `region` analysis column, the Quotes
"Highest adjustment" preset's descending order, the Convergence axes, and the
Focus view closing on Escape with its tab kept. A second journey solves the
fixture's small ratebook (`browser_ratebook`: one banded three-level factor, no
frontier) and reads its Rates chart. `frontend/e2e/core-flows.spec.ts` reads a
trained model's Lift and AvE panes. The screenshot baselines are regenerated only
through `.github/workflows/e2e-snapshots.yml`.
Hook/component tests also inspect atomic constraint-range rename/removal and
prove no global frontier bounds are read or written;
`frontend/src/__tests__/hooks/useConstraintHandlers.test.ts` covers the bound-kind switch
(value kept between at least and at most, restarted with the swept range between absolute and
percentage), `frontend/src/panels/__tests__/OptimiserConfig.test.tsx` a percentage constraint shown and edited in percent
under its pct key, its range in percent with Auto range withheld, and the Solve issues for a
constraint without one bound, and
`frontend/src/panels/optimiser/__tests__/useOptimiserAutoRange.test.ts` a save mid-run leaving
the job polling and its ranges published while a document reset retires it.

## Modelling config panes

The behavioural contract is defined in
[the high-level specification](high-level.md#modelling-config-panes).

- `frontend/src/panels/ModellingConfig.tsx` is the pane router. It retains job submission,
  cancellation, RAM-estimate and dispersion wiring, receives the active pane from `NodePanel`,
  and renders one pane at a time. Tree-family JSON drafts live above the pane branch, keyed by
  node, so invalid or incomplete text survives a Parameters unmount without crossing node
  identity.
  The gateway handles only an unset algorithm; unsupported values render an explicit diagnostic,
  and supported nodes expose no algorithm mutation action.
- `frontend/src/panels/modelling/featureSelection.ts` owns role exclusion and final algorithm
  selection. The configured target, weight, offset, fold, identifiers, and active evaluation
  group/date key are never offered as features. A tree family's final selection is its
  `feature_columns` restricted to eligible columns, mirroring the backend's `selected_feature_columns`;
  GLM membership is `modelMembership` in `glmTerms.ts`, and `roleColumnReasons` names each role
  for the GLM panes' unresolved reasons. The module performs no dependency cleanup.
- `TargetAndTaskConfig.tsx` and `GLMTargetConfig.tsx` show read-only algorithm context.
  `CommonFeatureConfig.tsx` serves every tree family: case-insensitive search, the
  All/Included/Excluded filters, Include all / Exclude all, dtype labels, stale feature repair,
  and one row per eligible feature with its inclusion checkbox and monotonicity selector.
  `glmTerms.ts` owns every GLM editor transition as a pure
  config-to-config function (add term, native type switch, field edit, expression rename/edit
  with grammar and column checks, remove, fit all, remove all, membership, interaction slot rules
  and writes); `GLMTermsConfig.tsx` and `TermCard.tsx` only render and call it.
  `GLMTargetConfig.tsx` labels the selected algorithm **Algorithm Rustystats**, keeping `glm`
  as its stored ID. `GLMRegularizationConfig.tsx` is the GLM's Parameters pane, under a static
  heading.
  Choosing a type writes `cv_folds: 5`, `cv_selection: "min"`, and `cv_seed: 42` when absent.
  The selected type is shown by its button only, and the seed field is hidden while its stored
  value is preserved. Older configurations without a seed use 42 when training. Penalty mode is
  Cross-validated (alpha absent or 0, with folds and selection rule) or
  Fixed (a positive alpha). Choosing Elastic Net also writes `l1_ratio: 0.5` when absent and
  always shows the L1-ratio slider, with no endpoint shortcuts, tooltip, setup button, or
  penalty-selection explanation. A saved Elastic Net configuration with no ratio remains
  invalid until the slider is changed; its concise issue appears in the Train list rather
  than above the Parameters pane. Inline alerts show the smooth-spline and
  robust-standard-error conflicts, picked by code (`glm-smooth-regularization`,
  `glm-robust-standard-errors`) from `glmConflictIssues` in `trainingObjective.ts`, the one
  derivation and wording the Train guard uses too, and a Solver disclosure holds maximum
  iterations, tolerance, and robust standard errors. `glmFamilies.ts` holds `GLM_FAMILY_LINKS`,
  pinned to the backend table by a contract test: `GLMTargetConfig` lists Quasi-Binomial, offers
  each family's links, flags a saved unsupported family or link, and bounds the variance power
  to 1 to 2. `trainingObjective.ts` mirrors the backend's GLM gates, including missing
  cross-validation settings and those conflicts.
- `HyperparametersConfig.tsx` owns the algorithm-neutral JSON-object editor, while
  `hyperparameters.ts` owns its formatting, object parsing, and reserved-key merge transitions.
  The editor receives the family's capability label, starter search space and reserved keys
  from its caller, accepts arbitrary non-reserved object contents without duplicating
  algorithm-specific validation, and renders no dedicated parameter fields. A Target-style
  **Fixed parameters** / **Tune parameters** radio group is the first control. Fixed mode
  renders only Parameters JSON; Tune mode renders the tuning note, trial count, seed,
  configured selection metric and the search space JSON, whose accessible name is
  "<family label> search space JSON". The note states whether `evaluation.test` holds a test
  set, offers **Review split →** (the caller's `onReviewSplit`, which selects Split), and shows
  `fitBudgetLabel` (`trainingFitBudget.ts`) for its evaluation, tuning,
  `publishesValidationFit` and `refitOnDevelopment` props, the run summary's own words. The
  gateway's CatBoost starter parameters are `iterations: 1000`, `learning_rate: 0.05`,
  `depth: 6`, `l2_leaf_reg: 3`, `early_stopping_rounds: 50` and `one_hot_max_size: 10`; the
  backend applies no default of its own, so a node without the key trains with CatBoost's.
  Each JSON draft autosaves when its frontend parser accepts the top-level object, without
  Apply/Revert controls; invalid syntax, a non-object top level, or a reserved fixed key stays in
  the corresponding per-node draft, is named in an inline alert beneath the editor, and
  contributes an issue to the Train list and the Parameters tab only while that strategy is
  selected.
  CatBoost's Train-pane GPU checkbox (`GpuTrainingCheckbox`) merges only the latest stored
  `task_type`. For a family whose
  capability has `gpu_device`, `XGBoostGpuToggle` (`frontend/src/panels/modelling/GpuTrainingToggle.tsx`) fetches
  `fetchModellingGpuStatus` once per mount and writes `device` (`"gpu"` or `undefined`);
  `trainsOnGpu` in `algorithmCapabilities.ts` drives the run summary's Compute line. The search-space formatter
  keeps scalar candidate arrays on one line and recursively indents nested conditional objects;
  the pane renders no search-space explanatory copy.
  Selecting Tune parameters writes, in one atomic update, a version-1 `tuning` with 20 trials,
  seed 42, the first reported metric and the family's starter search space (CatBoost's fresh
  editable candidate lists for `depth`, `learning_rate`, and `l2_leaf_reg`), and
  `refit_on_development: true` unless the family publishes its validation fit; it never
  changes `evaluation`, and it resets the hidden fixed draft to the stored parameters.
  Selecting Fixed parameters writes `tuning=null`. The frontend Train guard checks that the search
  space is an object with 1–32 entries; per-entry choice and conditional semantics remain owned by
  the shared backend contract.
- `SplitAndMetricsConfig.tsx` edits the single version-1 `evaluation` object. Strategy changes
  canonicalise random/group/temporal keys; validation changes canonicalise none/single/CV shapes;
  final-test controls use source-relative fractions for random/group and explicit starts for
  temporal. The neutral exact-plan card renders guarded backend counts/ranges only when present.
  The pane renders `evaluationConfigurationIssues` itself, picking `evaluation-fraction-sum` by
  its code to show beneath the validation settings, and shows `previewError`, the message of an
  estimate refused with `evaluation_preview_failed`, as the server worded it.
  Split starts with the row limit; Train owns GPU, actions, progress and results. `ExportPane.tsx` owns the
  "MLflow logging" and "Model file" sections. It derives one export block from the node's train
  state — an active train job ("Training is running — export is available when it completes."),
  else no cached result or an error result (no instructional note) — and, when not
  blocked, a stale warning from the parent's training-identity staleness ("Training settings
  changed since this model was trained. Exports use the last trained model."). A blocked pane
  passes no job id to either section, which renders its action disabled; each section is keyed
  by the exportable job id so a previous job's success or error never survives a new result.
  The MLflow logging section is headed by an Info icon whose
  tooltip carries the manual-only note ("used only when you press Log run to MLflow after
  training completes; nothing is logged automatically"), mounts the shared
  `MlflowDestinationSelector` bound to `mlflow_destination` (absent = the local folder; choosing
  Local folder removes the key), with `showDestinationDetails={false}` to omit the successful
  resolved-location line while retaining unavailable-state feedback, and gives the
  Experiment path label an Info icon in the offset-field pattern whose tooltip explains the
  field. The experiment tooltip says an MLflow experiment is the named group
  a logged run is filed under so related runs can be compared, that on Databricks it is a
  workspace folder path and elsewhere a plain name, and names the computed default used when
  blank, which follows the node's effective destination (`/Shared/haute/<label>` for databricks,
  else `<label>`); the field's placeholder is that default. The section has no model name or
  registry field and the log request carries none. The section carries no always-visible instruction prose and no "Logging destination"
  line. The experiment datalist loads through `useMlflowBrowser({destination})` from the node's
  destination on focus, only when that destination can accept a log. The optimiser config's
  Export pane receives the same selector and the same explanatory experiment
  tooltip, worded for an optimisation result. The Export pane's `MlflowExportSection` omits
  the successful destination line under its button, as does the optimiser Publish section. Both use `mlflowLogAvailability` and render the button
  disabled with the reason and a "Configure MLflow" link (opens the settings modal) only when the
  node's *own* destination is unconfigured or the package is unavailable — never because some
  other remote is — (the modelling button is additionally disabled, without that link, while the
  pane has no exportable job id) and always include `destination` in the log request: the node's
  remote key or `""` for the local folder, read from the node's current config at click time, so
  choosing Local folder after a job completes sends `""` and the response names the local backend.
  A remote the node chose that cannot be reached or rejects its credentials fails the log with the
  classified error and its "Test connection in MLflow settings" action; nothing falls back to
  local.
  Both log requests also carry `experiment_name`, the node's current `mlflow_experiment` or
  `null` when blank. A failed modelling log renders `apiErrorMessage` in an alert (never
  `ApiError: HTTP <status>`), and when `apiErrorCode` is `mlflow_connectivity` or
  `mlflow_authentication` the alert adds a "Test connection in MLflow settings" button that opens
  the settings modal. The optimiser preview reports failures as "MLflow log failed: <message>"
  with the same `apiErrorMessage` text.
  A successful modelling log shows only "Run ID: <run id>", followed by an "Open in Databricks"
  (databricks) or "Open run" (server) link when the response carries a `run_url`; there is no
  heading, local `mlflow ui` command or other description.
  The Export pane reads the exportable job's receipts through `useExportReceipts` and passes the
  newest of each kind down. With an MLflow receipt the section shows "Last logged to <destination
  label> · <experiment> · Open run" (or the run ID when there is no link) and its button reads "Log
  again"; pressing it first asks "This result is already logged to <experiment>. Logging again
  creates a new run." with "Log as a new run" and Cancel; the question belongs to the job,
  destination, experiment and availability it was asked under, so changing any withdraws it,
  and "Log as a new run" is disabled whenever the log could not run. Every attempt sends a fresh
  `operation_id` (`newOperationId`) and re-reads the receipts afterwards (`onLogAttempted`). A
  failure without an HTTP response keeps that operation ID and offers Retry, which resends it — a
  log whose response was lost returns its recorded run instead of creating another; a failure the
  server answered offers no Retry. The Model file section shows "Last saved to <path>" while no
  save outcome of its own is visible and re-reads receipts after a save (`onSaved`).
  The results store remembers a completed training job at its completion boundary, so a job that
  finishes while the node's editor is closed is remembered too. `ModellingConfig.onTrain` builds
  the training payload once, takes its `trainingLineage` before awaiting the request (every node's
  type, label, description, code, function name and config with sorted keys, runtime keys and
  modelling export settings omitted; every edge; the preamble; every submodel definition's
  interface, file and internal graph by the same rules; the pipeline's global constants and their
  load error), submits that same payload, and passes the
  lineage to `startTrainJob`, so an edit made while the request is pending never relabels the job.
  A fence-current `completeTrainJob` writes the handle — job ID, config hash, source and that
  lineage — under the job's document (`documentFence.sourceFile`); a job started without a lineage
  is never remembered; a fence-current error result of a job or a job failure (`failTrainJob`)
  forgets the node's handle, and a job whose document changed touches no handle. A request
  refused before any job existed (a rejected request, or an answer that starts no job) is
  recorded by `failTrainStart` as the node's failure, `result: null` with its `error`, under the
  identity it was asked for, and keeps the handle: the last completed job is still the server's.
  No failure is recorded as a fabricated training result. The job's config
  hash is the hash of the training identity `ModellingConfig` renders with
  (`useStaleConfigEstimate`'s `configHash` of `trainingIdentityConfig(config)`, computed in that
  render, never debounced), taken with the lineage before awaiting.
  `ModellingConfig`, through `useTrainedJobRestore`, reads the handle when the node has neither a
  result nor a running job: a completed result is put back with `restoreTrainResult`, keeping the
  stored config hash and source, with the current structural version when the `trainingLineage`
  of the payload the editor would submit now equals the stored one and `-1` (always stale)
  otherwise, so an upstream or submodel edit saved before the reload still shows the stale
  warning; a missing job (`404`) or any other status forgets the handle and records the node's
  result as expired in the results store (`markTrainResultExpired`, keyed by node, holding the
  job ID), unless the node has meanwhile gained a result or a running job. The Export pane
  reads that record, which lasts for the session until the node starts training or gets a
  result, and reports "The last training result for this node is no longer available (the
  server restarted or it expired). Train this model again to export it." The results panel
  does not read it: without a result it shows the node's data preview. A transient status
  failure keeps the handle and records nothing. Storage failures are tolerated.
  `ModelFileExportSection` is headed "Model file" with an Info icon tooltip describing the
  action. It mounts the shared `PathPickerField` labelled "Filename or path *", bound to
  `model_export_path`, with manual entry and a browser filtered to the model extension.
  There is no path-instruction paragraph. The picker displays the selected path once.
  While the stored path is non-empty it resolves `resolveModelSaveDestination({output_path,
  algorithm})` (aborting the previous request on change). The resolved "Destination: {path}"
  line appears only when the resolved path differs from the selected path (after normalizing
  backslashes to forward slashes). It shows a
  "The destination extension does not match the model format ({ext})." alert for
  `suffix_mismatch`, or "Could not resolve destination: {detail}"; a result is shown only for the
  path and model format it was resolved for, so a format change shows none until its own
  answer arrives. **Save model to file** is disabled without an exportable job id, with
  an empty path, while saving, or while the resolved destination reports a suffix mismatch; it
  calls `saveTrainedModel({job_id, output_path, overwrite: false})` and reads "Saving..." while
  pending. A failure whose `apiErrorCode` is `model_file_exists` (dispatched on the code, never the
  HTTP status) moves to a confirm state showing its message and a **Replace existing file**
  button, which is the only way to call `saveTrainedModel` with `overwrite: true`. Success shows
  "Saved model to {path}" and "Feature contract: {feature_contract_path}"; any other failure shows
  `apiErrorMessage` in the danger treatment. Save outcomes are
  tied to the path they were made for: editing the path hides them, and a new attempt replaces
  them.
  Those editable controls retain the standard modelling input
  background, border, text, spacing and monospace-value treatment instead of relying on unstyled
  browser defaults. `TrainingProgress.tsx` renders authoritative planning/trial/fold/final-fit/
  publication phases, bounded fit counts and best objective, plus the current fit's bounded
  `train_loss_history` through `LossChart` on an x-axis running to `total_iterations` (an
  `eval_` curve too when the rows carry one), and shows browser-derived ETA only for a valid
  advancing sample pair.
- `trainingObjective.ts` exposes a stable issue-code union, derives the currently applicable
  frontend target, objective, evaluation and bounded-tuning Train-guard issues, and names the
  pane that fixes each (`trainingIssuePane`). `ModellingConfig` continuously appends at most the
  selected strategy's current draft issue (`catboost-params` for any tree family's fixed
  parameters, or `tuning-config` for the tuning search space); reports the issues' panes,
  sorted and deduplicated, through `onPaneIssuesChange(nodeId, panes)`; lists each setup pane's
  issues in alerts above its body, skipping the draft issues and GLM conflicts the body shows
  beside their settings and `glm-elastic-net-l1-ratio`, which only Train lists; and passes the
  complete current list to `TrainPane`. `TrainingActionsAndResults` renders it as the single
  **Complete before training** alert directly beneath the main Train button whenever it is
  non-empty and no job is starting or running, each message with a **Go to** button that
  selects its pane, and `TrainPane` drops a Train/Re-train press while the list is non-empty.
  `NodePanel.tsx` keeps the latest report with the node it came from and marks each reported
  pane's tab with a compact warning indicator labelled "<Pane> needs attention", ignoring a
  report from another node; the Train tab's active-job indicator takes its place while a job
  runs ([frontend-node-editors](../frontend-node-editors/low-level.md#modelling-config-panes)).
  `PreviewPanelTabs.tsx` owns that visible and assistive indicator semantics without changing
  roving focus or layout
  ([frontend-preview-explore](../frontend-preview-explore/low-level.md#modelling-config-panes)).
- `api/types.ts`, `types/trainGuards.ts`, and the train-progress store type share the backend status
  contract. `parseTrainStatusResponse` requires the history and truncation flag the server
  always sends, and keeps an optional string-or-null `worker_remote_traceback`. `parseTrainResponse` validates the generated structure, which rejects retired
  result fields; the evaluation/tuning counts, weighted aggregates, digest links, winner and
  improvement are the server's to check, where the artifacts are produced. `useUIStore.ts` remembers the pane per node.
  `useNodeResultsStore.ts` retains the latest authoritative history snapshot and only the last two
  valid increasing iteration/elapsed samples; it never reconstructs loss history. A new or
  terminal job resets the ETA state
  ([frontend-shared](../frontend-shared/low-level.md#modelling-config-panes)).

Verification is deliberately assigned to the owning seams:

- `frontend/src/panels/__tests__/ModellingConfig.test.tsx` and suites under
  `frontend/src/panels/modelling/__tests__/` cover pane content, the tree families' unified loss
  picker and loss-derived metric compatibility, the common feature browser with reversible
  confirmation-free exclusion and dormant settings, role/final-selection filtering, unset-only
  immutable algorithm selection, the GLM terms pane (exact editor-transition payloads and both
  shared fixtures in `glmTerms.test.ts`, unresolved and malformed terms, row tags, the
  `constructor` column, draft-preserving numeric fields, reference level and levels, bulk
  actions, JSON round-trip) and the interaction cards (slot menus per resolution rule, stable
  keys across removal, error cards, unavailable saved factors, conflict alerts, override writes,
  duplicate and incomplete flags), arbitrary params JSON draft/object validation, the panes
  reported for the tab indicators and each pane's own alerts (the GLM conflicts once, beside
  their settings), the Train list with its Go to links and no request from an invalid press,
  canonical evaluation transitions/preview with the fraction-sum issue placed by its code, a
  coded evaluation-preview failure shown in Split and an uncoded estimate failure kept out of
  it, tuning enablement/search-space drafts with each family's starter space, the tuning
  note's family-labelled search space and run-summary fit budget (t-boost's winning validation
  fit), evaluation/result/progress fit counts, result labels, and live progress presentation.
  `frontend/src/utils/__tests__/trainingObjective.test.ts` pins the issue codes, including
  `evaluation-fraction-sum`, the GLM conflicts' wording and each issue's pane, and
  `frontend/src/hooks/__tests__/useStaleConfigEstimate.test.ts` a refused estimate's message
  with its `error_code`.
  `trainingEstimate.test.ts` covers which refusals are waited out (evaluation previews and
  input-snapshot builds of any profile, alone or together) and which fail at once (a running
  training job, a mix with any other holder, a non-507), the two schedules and their limits,
  alternating refusals drawing on budgets that never reset, `onWaiting` firing only for a
  snapshot build, and abort during a wait.
  `TrainingActionsAndResults.test.tsx` covers the collapsed **Error details** traceback under a failure
  (absent without a traceback), the waiting notice with Train and Re-train disabled, and an
  estimate failure labelled by its `error_code`, never by its words.
  `ModellingConfig.test.tsx`'s dispersion-estimate cases render the real editor with held
  replies: the request carries the current graph, node and source and an unchanged draft's
  field is filled; target A's late theta never overwrites target B's manual theta 7; and a
  source or structural change stops the estimate. `GLMComponents.test.tsx` covers the stopped
  note, unmount abort and failed-cancellation toast.
- `frontend/src/panels/__tests__/NodePanel.test.tsx`,
  `frontend/src/stores/__tests__/useUIStore.test.ts`, and
  `frontend/src/panels/__tests__/PreviewPanelTabs.test.tsx` cover strip gating, per-node memory,
  the needs-attention indicators for the reporting node only, active-job indicator semantics and
  roving keyboard behaviour.
- `frontend/src/types/__tests__/guards.contract.test.ts`,
  `frontend/src/api/__tests__/client.contract.test.ts`, and
  `frontend/src/__tests__/stores/useNodeResultsStore.test.ts` cover strict status-history parsing,
  canonical evaluation/tuning response and preview validation, weighted-evidence tampering,
  `worker_remote_traceback` retention, truncation retention, latest-snapshot semantics, valid/invalid estimate samples, and new-job
  reset.

Shared `NodePanel`, tab-control, API/parser, and store interactions are recorded in
[ownership.toml](../ownership.toml).

## Optimiser config panes

The behavioural contract is defined in
[the high-level specification](high-level.md#optimiser-config-panes).

- `frontend/src/panels/optimiser/optimiserPanes.ts` exports `OPTIMISER_PANES` (Data,
  Factors, Constraints, Solve, Export, in both modes) and `resolveOptimiserPane(remembered)`,
  which returns the remembered pane, or Data when there is none. `NodePanel` and `OptimiserConfig` both resolve through it, so the selected
  tab and the rendered body never disagree.
- `OptimiserConfig.tsx` keeps every derivation and effect above the pane branch: input and
  banding-source resolution, the atomic ratebook-default writes, the solve estimate and solve
  submission. Switching panes therefore never skips a default write or drops a pending solve.
  `solveIssues` is the single source of `canSolve`; each issue names the pane that resolves it,
  and the Solve pane's alert lists every issue with a **Go to** link that calls
  `setOptimiserPane`. The alert is hidden while a solve runs. Ctrl+Enter defers the solve to a
  zero-delay timer that calls the latest solve-if-ready, so a field committing on the same
  keystroke is solved with its committed value and re-judged for readiness. `stopOptimiserSolve`
  applies the cancel reply only while the cancelled job is still the node's active job and the
  document fence is current. The size estimate keys on its input fields (with the constraint
  count, which the memory forecast prices, and the confirmed `chunk_rows` and `kept_free_gb`
  pipeline settings through `selectConfirmedPipelineSetting`, which the forecast's chunk size and
  its cap read) and a structure key built from every other node, the
  edges and submodels, so the node's own edits (which move the global structural version) never
  re-request it.
- `OptimiserSolveStatus.tsx` renders the issue alert directly beneath the Optimise button. Its
  size block ends with `SolveMemoryForecast`, a `role="status"` line "Estimated solve memory X of
  Y" (`formatBytes` of the estimate's `solve_memory_forecast_bytes` and
  `solve_memory_cap_bytes`), in the warning colour with an `AlertTriangle` labelled "More than
  this machine can give" when X > Y. It never feeds `canSolve`.
- `resolveOptimiserInputs` also resolves `analysis_input` (`analysisInput`,
  `selectedAnalysisInput`, `missingExplicitAnalysisInput`, `malformedAnalysisInput`, and
  `analysisUsesDataInput` when it is unset or names the data input), and
  `optimiserSolveReadiness` adds the Factors-pane issues for a disconnected or malformed analysis
  input, more than `MAX_ANALYSIS_COLUMNS` (12) columns, and a column the analysis frame's known
  columns lack, and the Data and Constraints issues for an objective or constraint column the
  data input's known columns lack. `OptimiserFactorsTable` prunes columns only in response to a
  user switch of the analysis input (a pending prune applied, and disarmed, once the chosen
  frame's columns are known, which the column hook guarantees are that frame's own, so an
  identically structured frame settles it at once), never on load, so a temporarily unavailable
  schema never rewrites the configuration.
- `useUIStore.ts` remembers `optimiserPanes` per node. `NodePanel.tsx` selects only the Boolean
  presence of `solveJobs[node.id]` for the Solve tab's active indicator and derives no
  configuration warning descriptors.

Verification: `frontend/src/panels/__tests__/OptimiserConfig.test.tsx` covers each pane's
content, the Factors table in both modes (ratebook choices greyed out online), the constraint cards for both result types, the solve-blocking
alert and its navigation, and the factor table (only connected inputs listed, rating factors first with both choices, the
validation choices following the chosen frame's schema, search, switch-time pruning and a switch
to an identical frame leaving nothing armed, the cap), the Solve issues for an objective or
constraint column the input lacks and for a disconnected or malformed Rating Factor Source, and
the Replace confirmation withdrawn by a changed version label;
`frontend/src/hooks/__tests__/useDataInputColumns.test.ts` covers a switch never showing the
previous input's columns; `frontend/src/stores/__tests__/useOptimiserPublishStore.test.ts` the
publish state forgotten on a document load with a late reply dropped;
`frontend/src/panels/__tests__/OptimiserDataPreview.test.tsx` the navigation reset for a
same-size quote set and a constant or rounding-noise series' scale;
`frontend/src/__tests__/hooks/useConstraintHandlers.test.ts` the non-colliding placeholder name;
`frontend/src/panels/__tests__/ModellingConfig.test.tsx` a pre-job failure or `error` response
keeping the remembered handle and Train right after an edit recording the edited hash;
`frontend/src/panels/__tests__/ModellingPreview.test.tsx` Use best refused in a read-only
document and writing nothing once the document went read-only;
`frontend/src/panels/modelling/__tests__/MlflowExportSection.test.tsx` the Log again
confirmation withdrawn by an experiment or availability change; and
`frontend/src/panels/modelling/__tests__/ModelFileExportSection.test.tsx` the destination check
keyed by path and model format; `frontend/src/panels/optimiser/__tests__/optimiserPanes.test.ts` covers
the pane list and resolution; `NodePanel.test.tsx` and `useUIStore.test.ts` cover strip gating,
per-node memory and the active-solve indicator.
