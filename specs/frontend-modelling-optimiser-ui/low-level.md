# Frontend Modelling & Optimiser UI — Low-Level Specification

## Saving during background work

A successful save of the current canvas advances its persisted source revision
without replacing its execution identity. Active and starting training, optimiser,
Explore and pivot jobs keep their handles, progress polling and result delivery.
Their original config/source/structural-version stamps remain intact so edits made
since launch still mark results stale. Loading another document or accepting an
external revision replaces the execution identity; capability loss, unsynchronised
graphs and system failures still prevent obsolete responses from publishing.
Only a current, accepted save response may acknowledge this revision transition.

## Module map

| File | Responsibility |
| --- | --- |
| `frontend/src/panels/ModellingConfig.tsx` | Modelling form orchestration, early training-job registration/cancellation, RAM estimate and GLM estimate wiring. |
| `frontend/src/panels/ModellingPreview.tsx` | Result-backed modelling tab selection and tab reset. Loaded on demand by the app when the active node has model results, through the same Suspense boundary pattern as optimiser results. |
| `frontend/src/panels/NodePanel.tsx`, `frontend/src/panels/PreviewPanelTabs.tsx` | Six-pane hosting owned by [frontend-node-editors](../frontend-node-editors/low-level.md) and the accessible tab strip owned by [frontend-preview-explore](../frontend-preview-explore/low-level.md), both consumed by modelling. |
| `frontend/src/panels/OptimiserConfig.tsx` | Optimiser form, solve submission, and source/constraint configuration. It delegates auto-range request identity and terminal presentation to `useOptimiserAutoRange`. |
| `frontend/src/panels/optimiser/OptimiserConstraintSettings.tsx` | Constraint-bound, efficient-frontier, range, and step controls. It composes `useOptimiserAutoRange` beside the fields whose current constraint scope it owns, keeping request state out of the parent form. |
| `frontend/src/panels/optimiser/OptimiserSolveStatus.tsx` | Pure solve estimate, stale-result, progress, terminal diagnostics, action, and convergence-result presentation. It receives the parent-owned solve transition and owns no request lifecycle state. |
| `frontend/src/panels/optimiser/useOptimiserAutoRange.ts` | The single state authority for auto-range lifecycle: reducer-owned pending/error/terminal diagnostics, monotonic restart generation, document/config fence, abort/cancel ownership, polling, response validation, and completed-range publication. |
| `frontend/src/panels/OptimiserPreview.tsx` | Solve-result tab orchestration, point selection, exports and ratebook detail materialisation. |
| `frontend/src/panels/OptimiserDataPreview.tsx` | Bounded pre-solve scenario table, quote navigation, multi-series chart and statistics. |
| `frontend/src/components/ExecutionDiagnosticsSummary.tsx` | Actionable execution-memory and rejected-strategy banner shared with modelling progress, optimiser actions, and Explore. |
| `frontend/src/panels/optimiserScenarioStats.ts` | Strict finite-number parsing and per-scenario statistical aggregation used by the optimiser data preview. |
| `frontend/src/hooks/useConstraintHandlers.ts`, `frontend/src/hooks/useDataInputColumns.ts` | Constraint mutation handlers and stale-aware data-input column fetching. |
| `frontend/src/api/types.ts`, `frontend/src/types/trainGuards.ts` | [frontend-shared](../frontend-shared/low-level.md)-owned API types and dynamically loaded strict JSON response parsing consumed by modelling progress/results. |
| `frontend/src/stores/useNodeResultsStore.ts`, `frontend/src/stores/useUIStore.ts` | [frontend-shared](../frontend-shared/low-level.md)-owned result/job state and per-node modelling-pane memory consumed by the modelling workflow. |
| `frontend/src/utils/configField.ts`, `frontend/src/utils/trainingObjective.ts`, `frontend/src/utils/executionDiagnostics.ts` | Typed config reads/parsing, training-configuration issue derivation with click-time presentation, and structured execution-error/metric display helpers. |
| `frontend/src/panels/modelling/algorithmCapabilities.ts`, `frontend/src/panels/modelling/algorithmCapabilities.json` | The backend-generated family capability table (`ALGORITHM_CAPABILITIES`, `algorithmCapability`, `supportedLosses`) that the gateway, target pane and tuning readiness read. |
| `frontend/src/panels/modelling/TargetAndTaskConfig.tsx`, `frontend/src/panels/modelling/CommonFeatureConfig.tsx`, `frontend/src/panels/modelling/SplitAndMetricsConfig.tsx` | Tree-family target/loss/metric controls with loss-derived task compatibility and the positive-class field, the common feature/monotonicity browser, and the canonical evaluation editor with exact-plan preview. |
| `frontend/src/panels/modelling/HyperparametersConfig.tsx`, `frontend/src/panels/modelling/hyperparameters.ts`, `frontend/src/panels/modelling/featureSelection.ts` | Algorithm-neutral fixed-parameter JSON editing, optional bounded tuning/search-space editing from each family's starter space, and pure parameter/feature transitions. |
| `frontend/src/panels/modelling/EBMInteractionsConfig.tsx`, `frontend/src/panels/modelling/EBMTermsTab.tsx` | The EBM Features-pane pairwise-interaction control (a count EBM chooses from, or explicit feature pairs written to `params.interactions`), and the EBM Terms result tab: importance-ranked terms, main-effect shapes with the missing bin, and interaction score tables, labelled as additive link-scale term scores. |
| `frontend/src/panels/modelling/GLMTargetConfig.tsx`, `frontend/src/panels/modelling/GLMTermsConfig.tsx`, `frontend/src/panels/modelling/GLMInteractionsConfig.tsx`, `frontend/src/panels/modelling/TermCard.tsx`, `frontend/src/panels/modelling/glmTerms.ts`, `frontend/src/panels/modelling/glmFamilies.ts`, `frontend/src/panels/modelling/GLMRegularizationConfig.tsx` | GLM family/link/dispersion, feature rows with indented inline term cards, labelled interaction/slot controls, pure editor transitions mirroring the backend term contract, the family/link and solver constants shared with the backend, and regularisation, cross-validation, and solver controls. |
| `frontend/src/panels/modelling/TrainingActionsAndResults.tsx`, `frontend/src/panels/modelling/TrainingProgress.tsx` | Train action/result summary and progress. |
| `frontend/src/panels/modelling/TrainingRunSummary.tsx`, `frontend/src/panels/modelling/trainingFitBudget.ts` | The Train pane's read-only run summary (model, target, feature count, evaluation method and allocation, fit budget, compute) and the pure fit count behind it and the tuning note: selection fits (validation fits × tuning trials) plus the final development refit unless `refit_on_development` is `false`. |
| `frontend/src/panels/modelling/EvaluationAllocation.tsx`, `frontend/src/panels/modelling/evaluationPreview.ts` | The Split pane's training/validation/test allocation bar and per-fit row ranges, showing exact rows only from an evaluation preview whose strategy and validation method match the current editor (`compatibleEvaluationPreview`), and target fractions otherwise. |
| `frontend/src/panels/modelling/trainingEstimate.ts` | `estimateAfterSupersededPreviews`: the modelling estimate request retried with bounded, abortable backoff only while a 507 names nothing but other evaluation previews as the in-flight holders (`refusedByEvaluationPreviews`); every other failure, including a running training job, is returned at once. |
| `frontend/src/panels/modelling/ColumnSelector.tsx` | Searchable column-only combobox for target, weight, offset and similar role fields; a saved column that is no longer upstream stays visible but is never offered as a new choice. |
| `frontend/src/panels/modelling/chartGeometry.ts`, `frontend/src/panels/modelling/useDiagnosticFeature.ts`, `frontend/src/panels/modelling/validation.css` | Validation-workspace chart helpers (compact axis numbers, padded finite domains, ticks, label thinning, axis-label truncation), the feature selection a diagnostic pane owns standalone or shares with the result workspace, and the workspace's container-query layout styles, imported by `frontend/src/panels/ModellingPreview.tsx`. |
| `frontend/src/panels/modelling/ExportPane.tsx`, `frontend/src/panels/modelling/MlflowExportSection.tsx`, `frontend/src/panels/modelling/ModelFileExportSection.tsx` | The Export pane: the MLflow logging fields, the manual MLflow log action (names the node's destination, disabled with the reason when that destination is unconfigured or no trained model is exportable, always sends `destination`), and the save-model-to-file action. |
| `frontend/src/panels/modelling/exportReceipts.ts`, `frontend/src/panels/modelling/useTrainedJobRestore.ts` | `useExportReceipts(jobId)` (reads a completed job's export receipts from the status endpoint on mount and on `refresh`), `newOperationId()`, and `useTrainedJobRestore` (after a reload, reads a remembered job's status once to restore its result — current only when the editor's current payload has the same `trainingLineage` — or report it expired). |
| `frontend/src/utils/trainedJobHandles.ts`, `frontend/src/utils/modellingExportConfig.ts` | Per-document browser handles to a node's last completed training job (`read`/`write`/`clearTrainedJobHandle`, each holding job ID, config hash, source and lineage) and `trainingLineage` (a digest of the graph payload a training request submitted, submodel graphs included); the modelling export-field keys (`MODELLING_EXPORT_CONFIG_KEYS`) and `trainingIdentityConfig`, which omits them. |
| `frontend/src/panels/modelling/modelExport.ts`, `frontend/src/panels/modelling/FieldHelpIcon.tsx` | The model file extension per algorithm and the shared hover-only field help icon. |
| `frontend/src/panels/modelling/SummaryTab.tsx` | Model info, diagnostics/errors, development/selection/final-test metrics, tuning baseline/winner evidence and warnings. |
| `frontend/src/panels/modelling/GLMCoefficientsTab.tsx`, `frontend/src/panels/modelling/GLMRelativitiesTab.tsx` | GLM-specific coefficient and relativity result tables, including invalid-inference reasons and robust standard errors. |
| `frontend/src/panels/modelling/NumberField.tsx` | Numeric input that keeps a draft until a valid, in-range value commits on blur or Enter. |
| `frontend/src/panels/modelling/modellingPanes.ts` | `modellingPanesFor(algorithm)` and `resolveModellingPane`, the one pane list behind the modelling tabs and pane bodies. |
| `frontend/src/panels/modelling/FeatureImportance.tsx`, `frontend/src/panels/modelling/FeaturesTab.tsx`, `frontend/src/panels/modelling/FeatureBrowser.tsx` | Feature-importance display, tab and feature browser. |
| `frontend/src/panels/modelling/ChartScaffold.tsx`, `frontend/src/panels/modelling/LossChart.tsx`, `frontend/src/panels/modelling/LossTab.tsx` | Shared chart primitives and loss visualisation. |
| `frontend/src/panels/modelling/LiftTab.tsx`, `frontend/src/panels/modelling/ResidualsTab.tsx`, `frontend/src/panels/modelling/AveTab.tsx`, `frontend/src/panels/modelling/PdpTab.tsx` | Lift, residual, actual-versus-estimated and partial-dependence result views. |
| `frontend/src/panels/modelling/FailoverHelp.tsx`, `frontend/src/panels/modelling/OffsetFieldLabel.tsx`, `frontend/src/panels/modelling/styles.ts` | Algorithm help, offset label and modelling visual helpers, including the shared modelling input surface. |
| `frontend/src/panels/optimiser/SummaryTab.tsx` | Objective/constraint/lambda summary, ratebook-impact state and scenario histogram. |
| `frontend/src/panels/optimiser/ConvergenceChart.tsx`, `frontend/src/panels/optimiser/FrontierChart.tsx`, `frontend/src/panels/optimiser/DetailCard.tsx` | Iteration convergence, selectable frontier and strict frontier-point detail display. |
| `frontend/src/panels/optimiser/RatebookRatesTab.tsx`, `frontend/src/panels/optimiser/RatebookImpactBeeswarm.tsx`, `frontend/src/panels/optimiser/ratebookFactorTables.ts` | Ratebook tables, impact chart and factor-table normalisation/order. |
| `frontend/src/panels/optimiser/iterationSummary.ts`, `frontend/src/panels/optimiser/optimiserHelpers.ts` | Iteration copy and optimiser result/save/constraint helpers. |
| `frontend/src/utils/banding.ts` | Extracts banding factor-levels/order and resolves an optimiser's explicit or sole direct banding source. |
| `frontend/src/utils/polarsDtypes.ts` | Shared canonical Polars numeric-dtype predicate used by modelling and banding controls. |

## Key types and data structures

- `TrainResult`/`TrainProgress` and optimiser result/job records are consumed from
  `frontend/src/stores/useNodeResultsStore.ts`; the panels key them by `config._nodeId` and source/
  structural/config hash state supplied to the shared estimate/job layers.
- Modelling configuration is a `Record<string, unknown>` split into target, feature,
  algorithm, fixed-parameter, required version-1 `evaluation`, and optional version-1
  `tuning` contracts. `trainingConfigurationIssues` derives incomplete target,
  objective, evaluation and tuning requirements for the Train guard; `ModellingConfig`
  adds any invalid JSON draft for the selected CatBoost parameter strategy. Top-level
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
   `resolveModellingPane` selects from `modellingPanesFor(algorithm)` (Target/Features/Split/
   Train/Export, plus Params for CatBoost), the same list `NodePanel` renders as tabs, and
   passes the shared `onUpdate` contract. GLM Target composes `GLMTargetConfig` and the always-visible
   `GLMRegularizationConfig`; there is no GLM Params route. It
   hashes the training identity from the config without the export fields
   (`MODELLING_EXPORT_CONFIG_KEYS`: `mlflow_destination`, `mlflow_experiment`,
   `model_export_path`), and the graph's structural fingerprint omits the same keys for modelling
   nodes, so export edits made on the canvas neither mark the cached result stale nor re-request
   the RAM estimate. It
   continuously derives every applicable configuration issue, including the selected CatBoost
   strategy's JSON-draft issue, and passes the current messages to the Train pane. An idle
   Train/Re-train press with a non-empty list suppresses the request and reveals those messages
   only in the banner beneath the main Train button. CatBoost's Target pane has no independent task
   selector. It always presents every supported loss; selecting one atomically stores the
   loss-derived `config.task` and objective-matched default metrics. Every metric remains visible,
   while metrics outside that loss's regression or classification family are disabled and cannot
   mutate the configuration. Tweedie selection also writes variance power `1.5` when the field is
   absent, then shows the slider immediately; a previously stored power is preserved and there is
   no intermediate warning-button gate. CatBoost hyperparameters use `config.params` and
   `config.variance_power`; GLM controls write their algorithm fields directly on `config`,
   including `config.var_power`. CatBoost's `CommonFeatureConfig` uses the shared Polars
   numeric-dtype classifier and final selection, so only selected numeric feature cards enable
   their inline downward/dash/upward selector and can write
   `monotone_constraints[name] = -1|1`; choosing the dash removes the key. Exclusion changes only
   `exclude`: a stored direction remains selected in the disabled control and becomes active again
   after re-inclusion. GLM's Features pane is `GLMTermsConfig`. `glmDtypeClass` classifies columns as continuous,
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
   the saved fit, and removal; an unresolved expression stays editable, and a malformed entry
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
   writes `exclude`, `feature_columns`, `monotone_constraints`, or `feature_weights`.
   New algorithms receive a canonical
   random/single-validation evaluation.
   Later strategy changes replace incompatible keys atomically instead of retaining stale
   group/date/fraction fields.
2. `useStaleConfigEstimate` receives the RAM request endpoint with graph/source/structural version;
   it owns abort/loading/error and associates an estimate with the current config. Every cached
   solve/train result carries the complete canonical identity
   `{ configHash, source, structuralVersion }`; the hook has no partial-result shape. Its guarded
   optional `evaluation_preview` is shown only when the backend can build the exact plan and
   includes development/final-test rows, validation-fit bounds and strategy-specific group/date
   summaries.
3. Training records the `POST /api/modelling/train` job handle as soon as it is returned, after
   which background polling owns preparation/fit progress. `TrainingActionsAndResults` keeps a
   distinct Cancel control visible while that job is active; `ModellingConfig` posts its job ID to
   `/train/cancel`, then immediately stores a returned terminal failure/cancellation or completed
   race winner. Structured execution details and additive `error_code`/`http_status_code`/
   `error_detail` fields are retained in progress/error state. Both the estimate warning and the
   terminal `gpu_vram_limit` message require an explicit CPU selection and retry rather than
   describing an automatic fallback. The optional GLM dispersion action calls the dispersion API
   and writes a successful theta/variance-power estimate through the ordinary editable update
   callback.
4. `frontend/src/panels/ModellingPreview.tsx` computes which tabs have result data, renders only
   those, and resets the active tab when a new result arrives. `SummaryTab` separates selection
   estimates from final-test metrics, renders ordered validation fits and tuning
   baseline/winner/improvement evidence, and exposes diagnostics rather than suppressing a
   partially successful training result.
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

1. `frontend/src/panels/OptimiserConfig.tsx` finds direct inputs and candidate banding sources.
   It uses cached node columns, provided upstream columns, then `useDataInputColumns` as needed.
   Ratebook mode may set an unconfigured source and inferred factor columns from banding levels
   in one atomic update, while an explicitly configured empty factor list is preserved.
2. Solve submission builds the current graph and records job/result state. Config estimates use
   the same stale-config pattern as modelling. API execution diagnostics are preserved in action
   errors/progress.
3. `useOptimiserAutoRange` is the only authority for auto-range state and
   request identity. Starting or restarting increments a monotonic generation,
   captures the current document/config fence, aborts and best-effort cancels
   the prior owned job, and polls status every second with an abort-aware delay.
   Only the current generation may publish terminal state or apply completed
   ranges, and completed output is accepted only when every configured
   constraint has a returned finite range. A document/config replacement or
   unmount retires and cancels the active generation. The action remains
   available as **Restart auto range** while a request is active. A locally
   aborted or superseded request is silent; a cancelled or other terminal
   status returned by the server is shown in the reducer-owned auto-range
   error area.
4. `frontend/src/panels/OptimiserPreview.tsx` picks the available result tabs and selected frontier
   point. In ratebook mode a selected point without tables is materialised only on Rates/Summary;
   request sequence bookkeeping drops stale replies and persists accepted tables in the result
   store. A point change aborts and clears materialised export detail before Save/MLflow actions
   can be used for the new point. The MLflow log request carries the node's current
   `mlflow_destination` (found through `allNodes` by `nodeId`; `""` for Auto) and the Export tab
   and detail card derive availability from that destination alone.
5. `frontend/src/panels/OptimiserDataPreview.tsx` caps rows at 5,000 before grouping by quote,
   orders scenario rows, and calculates full-preview statistics only when its Statistics tab is open.

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
execution's effective `rss_limit_bytes`) while running it; `in_flight_memory_budget_exceeded` — other running work holds the memory it needs,
so try again when that finishes; `native_memory_cap_unavailable` — Haute cannot enforce
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
- Each non-summary view has a heading and explanatory sentence above its existing
  diagnostic component. The scrollable body remounts on view changes so a new view
  starts at the top. View availability continues to follow actual result data.
- `modelling/SummaryTab` groups results into bordered, elevated cards using the
  existing theme tokens and model accent. Performance cards precede model metadata;
  final-test metrics and diagnostic metrics retain separate names and descriptions.
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
messages. A rejected frontier point-index contract clears the in-flight request marker and
surfaces the error instead of leaving Rates loading. AbortError and recognised supersession do
not flash a failure. Render-time numeric contract violations in strict frontier/statistics
helpers are intentionally not coerced.

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

Focused diagnostics tests cover rejected-strategy and memory-pressure detail,
structured request failures, contract-error retention, and the absence of
invented planner state. Data-input tests cover multiple roots/direct parents,
explicit selection, snapshot-backed post-Polars columns, missing-snapshot
diagnostics, and no implicit build. Optimiser warning tests cover missing
explicit sources and mixed healthy/zero-level outputs. The deterministic
`frontend/e2e/canvas-assurance.spec.ts` journey persists constraint/range
fields, selects and applies the backend `point_index`, and intercepts the
MLflow API to assert request/result identity without contacting a live service.
Hook/component tests also inspect atomic constraint-range rename/removal and
prove no global frontier bounds are read or written.

## Modelling config panes

The behavioural contract is defined in
[the high-level specification](high-level.md#modelling-config-panes).

- `frontend/src/panels/ModellingConfig.tsx` is the pane router. It retains job submission,
  cancellation, RAM-estimate and dispersion wiring, receives the active pane from `NodePanel`,
  and renders one pane at a time. CatBoost JSON drafts live above the pane branch, keyed by node,
  so invalid or incomplete text survives Params unmount without crossing node identity.
  The gateway handles only an unset algorithm; unsupported values render an explicit diagnostic,
  and supported nodes expose no algorithm mutation action.
- `frontend/src/panels/modelling/featureSelection.ts` owns role exclusion and final algorithm
  selection. The configured target, weight, offset, fold, identifiers, and active evaluation
  group/date key are never offered as features. Final selection applies CatBoost's `exclude` filter;
  GLM membership is `modelMembership` in `glmTerms.ts`, and `roleColumnReasons` names each role
  for the GLM panes' unresolved reasons. The module performs no dependency cleanup.
- `TargetAndTaskConfig.tsx` and `GLMTargetConfig.tsx` show read-only algorithm context.
  `CommonFeatureConfig.tsx` is CatBoost-only: case-insensitive search, dtype labels, stale
  exclusion repair, compact single-row per-feature cards, the green/red include/exclude button,
  and the monotonicity selector. `glmTerms.ts` owns every GLM editor transition as a pure
  config-to-config function (add term, native type switch, field edit, expression rename/edit
  with grammar and column checks, remove, fit all, remove all, membership, interaction slot rules
  and writes); `GLMTermsConfig.tsx` and `TermCard.tsx` only render and call it.
  `GLMTargetConfig.tsx` labels the selected algorithm **Algorithm Rustystats**, keeping `glm`
  as its stored ID. `GLMRegularizationConfig.tsx` is a Target-pane section with a static heading.
  Choosing a type writes `cv_folds: 5`, `cv_selection: "min"`, and `cv_seed: 42` when absent.
  The selected type is shown by its button only, and the seed field is hidden while its stored
  value is preserved. Older configurations without a seed use 42 when training. Penalty mode is
  Cross-validated (alpha absent or 0, with folds and selection rule) or
  Fixed (a positive alpha). Choosing Elastic Net also writes `l1_ratio: 0.5` when absent and
  always shows the L1-ratio slider, with no endpoint shortcuts, tooltip, setup button, or
  penalty-selection explanation. A saved Elastic Net configuration with no ratio remains
  invalid until the slider is changed; its concise issue appears in the Train preflight rather
  than the Parameters pane. Inline alerts explain
  the smooth-spline and robust-standard-error conflicts, and a Solver disclosure holds maximum
  iterations, tolerance, and robust standard errors. `glmFamilies.ts` holds `GLM_FAMILY_LINKS`,
  pinned to the backend table by a contract test: `GLMTargetConfig` lists Quasi-Binomial, offers
  each family's links, flags a saved unsupported family or link, and bounds the variance power
  to 1 to 2. `trainingObjective.ts` mirrors the backend's GLM gates, including missing
  cross-validation settings and the smooth-spline and robust-standard-error conflicts.
- `HyperparametersConfig.tsx` owns the algorithm-neutral JSON-object editor, while
  `hyperparameters.ts` owns its formatting, object parsing, and reserved-key merge transitions.
  The editor receives display defaults and reserved keys from its caller, accepts arbitrary
  non-reserved object contents without duplicating algorithm-specific validation, and renders no
  dedicated parameter fields. For CatBoost, a Target-style **Fixed parameters** /
  **Tune parameters** radio group is the first control below the heading. Fixed mode renders only
  Parameters JSON; Tune mode renders only trial count, seed, configured selection metric and
  Search space JSON.
  Each JSON draft autosaves when its frontend parser accepts the top-level object, without
  Apply/Revert controls; invalid syntax, a non-object top level, or a reserved fixed key stays in
  the corresponding per-node draft and contributes a click-time issue to the Train banner only
  while that strategy is selected.
  The Train-pane GPU toggle merges only the latest stored `task_type`. The search-space formatter
  keeps scalar candidate arrays on one line and recursively indents nested conditional objects;
  the pane renders neither a derived fit-count sentence nor search-space explanatory copy.
  Selecting Tune parameters seeds fresh editable candidate lists for `depth`, `learning_rate`,
  and `l2_leaf_reg`. In the same atomic update, it adds `evaluation.test={size: 0.2}` for a
  random/group evaluation or `evaluation.test={start: ""}` for a temporal evaluation only when
  the key is absent and validation is enabled; it never overwrites an existing evaluation choice.
  Selecting Fixed parameters writes `tuning=null`. The frontend Train guard checks that the search
  space is an object with 1–32 entries; per-entry choice and conditional semantics remain owned by
  the shared backend contract.
- `SplitAndMetricsConfig.tsx` edits the single version-1 `evaluation` object. Strategy changes
  canonicalise random/group/temporal keys; validation changes canonicalise none/single/CV shapes;
  final-test controls use source-relative fractions for random/group and explicit starts for
  temporal. The neutral exact-plan card renders guarded backend counts/ranges only when present.
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
  collapsible MLflow section receives the same selector and the same explanatory experiment
  tooltip, worded for an optimisation result. The Export pane's `MlflowExportSection` omits
  the successful destination line under its button; the optimiser `ExportMlflowSection`
  retains its destination line. Both use `mlflowLogAvailability` and render the button
  disabled with the reason and a "Configure MLflow" link (opens the settings modal) only when the
  node's *own* destination is unconfigured or the package is unavailable — never because some
  other remote is — (the modelling button is additionally disabled, without that link, while the
  pane has no exportable job id) and always include `destination` in the log request: the node's
  remote key or `""` for the local folder, read from the node's current config at click time, so
  choosing Local folder after a job completes sends `""` and the response names the local backend.
  A remote the node chose that cannot be reached or rejects its credentials fails the log with the
  classified error and its "Test connection in MLflow settings" action; nothing falls back to
  local. The optimiser
  `DetailCard` log button gets the same disabled-with-reason treatment plus a "Configure" link.
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
  creates a new run." with "Log as a new run" and Cancel. Every attempt sends a fresh
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
  interface, file and internal graph by the same rules), submits that same payload, and passes the
  lineage to `startTrainJob`, so an edit made while the request is pending never relabels the job.
  A fence-current `completeTrainJob` writes the handle — job ID, config hash, source and that
  lineage — under the job's document (`documentFence.sourceFile`); a job started without a lineage
  is never remembered; a fence-current error result, failure (`failTrainJob`) or direct completion
  without a job forgets the node's handle, and a job whose document changed touches no handle.
  `ModellingConfig`, through `useTrainedJobRestore`, reads the handle when the node has neither a
  result nor a running job: a completed result is put back with `restoreTrainResult`, keeping the
  stored config hash and source, with the current structural version when the `trainingLineage`
  of the payload the editor would submit now equals the stored one and `-1` (always stale)
  otherwise, so an upstream or submodel edit saved before the reload still shows the stale
  warning; a missing job (`404`) or any other status forgets the handle and the
  Export pane reports "The last training result for this node is no longer available (the server
  restarted or it expired). Train this model again to export it." Storage failures are tolerated.
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
  path it was resolved for. **Save model to file** is disabled without an exportable job id, with
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
  publication phases, bounded fit counts and best objective, plus the final model's bounded
  `train_loss_history`; it labels a truncated retained window and shows browser-derived ETA only
  for a valid advancing sample pair.
- `trainingObjective.ts` exposes a stable issue-code union and derives the currently applicable
  frontend target, objective, evaluation and bounded-tuning Train-guard issues. `ModellingConfig`
  continuously appends at most the selected CatBoost strategy's current draft issue
  (`catboost-params` for fixed parameters or `tuning-config` for the tuning search space) and
  passes the complete current list to `TrainPane`. `TrainPane` withholds the list from
  `TrainingActionsAndResults` until an
  invalid Train/Re-train press sets its local reveal latch and suppresses the request. Once
  revealed, the banner reflects the current non-empty list. The parent keys `TrainPane` by node
  and complete/incomplete state, so resolving the final issue resets the latch and later
  invalidity is hidden until another press; leaving and returning to the pane also remounts it.
  `TrainingActionsAndResults` renders the single alert directly beneath the main Train button.
  `NodePanel.tsx` derives no configuration warning descriptors; it supplies only the
  active-job indicator
  ([frontend-node-editors](../frontend-node-editors/low-level.md#modelling-config-panes)).
  `PreviewPanelTabs.tsx` owns that visible and assistive indicator semantics without changing
  roving focus or layout
  ([frontend-preview-explore](../frontend-preview-explore/low-level.md#modelling-config-panes)).
- `api/types.ts`, `types/trainGuards.ts`, and the train-progress store type share the backend status
  contract. `parseTrainStatusResponse` strictly retains present history/truncation and leaves
  absent history absent. `parseTrainResponse` rejects retired result fields and strictly
  recomputes evaluation/tuning counts, weighted aggregates, digest links, winner and improvement
  invariants. `useUIStore.ts` remembers the pane per node.
  `useNodeResultsStore.ts` retains the latest authoritative history snapshot and only the last two
  valid increasing iteration/elapsed samples; it never reconstructs loss history. A new or
  terminal job resets the ETA state
  ([frontend-shared](../frontend-shared/low-level.md#modelling-config-panes)).

Verification is deliberately assigned to the owning seams:

- `frontend/src/panels/__tests__/ModellingConfig.test.tsx` and suites under
  `frontend/src/panels/modelling/__tests__/` cover pane content, CatBoost's unified loss picker and
  loss-derived metric compatibility, CatBoost's common feature browser with reversible
  confirmation-free exclusion and dormant settings, role/final-selection filtering, unset-only
  immutable algorithm selection, the GLM terms pane (exact editor-transition payloads and both
  shared fixtures in `glmTerms.test.ts`, unresolved and malformed terms, row tags, the
  `constructor` column, draft-preserving numeric fields, reference level and levels, bulk
  actions, JSON round-trip) and the interaction cards (slot menus per resolution rule, stable
  keys across removal, error cards, unavailable saved factors, conflict alerts, override writes,
  duplicate and incomplete flags), arbitrary params JSON draft/object validation,
  click-time aggregate training-validation presentation, canonical evaluation
  transitions/preview, tuning enablement/search-space drafts, evaluation/result/progress fit
  counts, result labels, and live progress presentation.
- `frontend/src/panels/__tests__/NodePanel.test.tsx`,
  `frontend/src/stores/__tests__/useUIStore.test.ts`, and
  `frontend/src/panels/__tests__/PreviewPanelTabs.test.tsx` cover strip gating, per-node memory,
  plain setup-tab labels, active-job indicator semantics and roving keyboard behaviour.
- `frontend/src/types/__tests__/guards.contract.test.ts`,
  `frontend/src/api/__tests__/client.contract.test.ts`, and
  `frontend/src/__tests__/stores/useNodeResultsStore.test.ts` cover strict status-history parsing,
  canonical evaluation/tuning response and preview validation, weighted-evidence tampering,
  truncation retention, latest-snapshot semantics, valid/invalid estimate samples, and new-job
  reset.

Shared `NodePanel`, tab-control, API/parser, and store interactions are recorded in
[ownership.toml](../ownership.toml).
