# Frontend Modelling & Optimiser UI — Low-Level Specification

## Module map

| File | Responsibility |
| --- | --- |
| `frontend/src/panels/ModellingConfig.tsx` | Modelling form orchestration, early training-job registration/cancellation, RAM estimate and GLM estimate wiring. |
| `frontend/src/panels/ModellingPreview.tsx` | Result-backed modelling tab selection and tab reset. |
| `frontend/src/panels/NodePanel.tsx`, `frontend/src/panels/PreviewPanelTabs.tsx` | Five-pane hosting owned by [frontend-node-editors](../frontend-node-editors/low-level.md) and the accessible tab strip owned by [frontend-preview-explore](../frontend-preview-explore/low-level.md), both consumed by modelling. |
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
| `frontend/src/panels/modelling/TargetAndTaskConfig.tsx`, `frontend/src/panels/modelling/CommonFeatureConfig.tsx`, `frontend/src/panels/modelling/SplitAndMetricsConfig.tsx` | CatBoost target/loss/metric controls with loss-derived task compatibility, the common feature/monotonicity browser, and the canonical evaluation editor with exact-plan preview. |
| `frontend/src/panels/modelling/HyperparametersConfig.tsx`, `frontend/src/panels/modelling/hyperparameters.ts`, `frontend/src/panels/modelling/featureSelection.ts` | Algorithm-neutral fixed-parameter JSON editing, optional bounded CatBoost tuning/search-space editing, and pure parameter/feature transitions. |
| `frontend/src/panels/modelling/GLMTargetConfig.tsx`, `frontend/src/panels/modelling/GLMFactorConfig.tsx`, `frontend/src/panels/modelling/GLMRegularizationConfig.tsx` | GLM family/dispersion, terms/factors and regularisation controls. |
| `frontend/src/panels/modelling/TrainingActionsAndResults.tsx`, `frontend/src/panels/modelling/TrainingProgress.tsx`, `frontend/src/panels/modelling/MlflowExportSection.tsx` | Train action/result summary, progress and MLflow export. |
| `frontend/src/panels/modelling/SummaryTab.tsx` | Model info, diagnostics/errors, development/selection/final-test metrics, tuning baseline/winner evidence, warnings and MLflow export summary. |
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
| `frontend/src/utils/polarsDtypes.ts` | Shared canonical Polars numeric-dtype predicate used by modelling and banding controls. |

## Key types and data structures

- `TrainResult`/`TrainProgress` and optimiser result/job records are consumed from
  `frontend/src/stores/useNodeResultsStore.ts`; the panels key them by `config._nodeId` and source/
  structural/config hash state supplied to the shared estimate/job layers.
- Modelling configuration is a `Record<string, unknown>` split into target, feature,
  algorithm, fixed-parameter, required version-1 `evaluation`, and optional version-1
  `tuning` contracts. `trainingConfigurationIssues` derives incomplete target,
  objective, evaluation and tuning requirements for the Train guard; `ModellingConfig`
  adds any invalid JSON draft for the selected CatBoost parameter strategy. Public
  `split`/`cross_validation` fields have no editor or runtime result path.
- Optimiser uses input node/banding-node descriptions, `FrontierRangeConfig`, constraints and
  ratebook `FactorTables`. `frontend/src/utils/banding.ts` returns ordered factor levels, including
  a banding default only for the ordering APIs that request it. Per-constraint
  `frontier_ranges` is the sole editable range representation.

## Control flow

### Modelling

1. `frontend/src/panels/ModellingConfig.tsx` reads graph/source/job state, routes the active
   Target/Features/Params/Split/Train pane, and passes the shared `onUpdate` contract. It
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
   including `config.var_power`. `CommonFeatureConfig` uses the shared Polars numeric-dtype
   classifier and final algorithm selection, so only selected numeric feature cards enable their
   inline downward/dash/upward selector and can write
   `monotone_constraints[name] = -1|1`; choosing the dash removes the key. Exclusion changes only
   `exclude`: a stored direction remains selected in the disabled control and becomes active again
   after re-inclusion. New algorithms receive a canonical random/single-validation evaluation.
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
   can be used for the new point.
5. `frontend/src/panels/OptimiserDataPreview.tsx` caps rows at 5,000 before grouping by quote,
   orders scenario rows, and calculates full-preview statistics only when its Statistics tab is open.

`ExecutionDiagnosticsSummary` consumes the guarded versioned metrics contract
and renders only actionable memory pressure or rejected strategy, with
technical collections behind disclosure. `useDataInputColumns` consumes
guarded schema/preview results keyed by node/config/source generation and never
derives columns from path, provider internals, or a snapshot-build side effect.
The Banding classifier supplies both ordered healthy levels and named
zero-level issues; `OptimiserConfig` compares any explicit source id with the
current direct-Banding candidates and renders one aggregate accessible alert
without broadening the exactly-one-direct fallback.

## Edge cases and invariants

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
- `frontend/src/panels/modelling/featureSelection.ts` owns role exclusion, final algorithm
  selection and explicit-factor dependency cleanup. The configured target, weight, offset, fold,
  identifiers, and active evaluation group/date key are never offered as features. Cleanup returns
  only affected `terms`, `interactions`, and `monotone_constraints` fields for the caller's one
  config update.
- `TargetAndTaskConfig.tsx` and `GLMTargetConfig.tsx` show read-only algorithm context. The common
  `CommonFeatureConfig.tsx` browser supplies case-insensitive search, dtype labels, stale
  exclusion repair, compact single-row per-feature cards with the name/dtype, a
  Data-Input-Provider-style green/red current-state include/exclude button, and the adjacent
  final-selection-aware red-down/yellow-neutral/green-up monotonicity selector. Its accessible
  group label replaces a repeated visible monotonicity label. Matching bulk actions remain
  search-independent. Feature exclusion writes only `exclude`, without confirmation, so dormant
  monotonic and GLM settings survive re-inclusion. GLM composes `GLMFactorConfig.tsx` beneath it;
  explicit-term removal and `all_factors` narrowing use the confirmed dependency transition.
  `GLMRegularizationConfig.tsx` is the GLM Params body.
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
  The Train pane owns GPU, row limit, MLflow fields,
  actions, progress and results. Those editable controls retain the standard modelling input
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
  loss-derived metric compatibility, both algorithms' common feature browser,
  role/final-selection filtering, reversible confirmation-free exclusion with dormant
  settings, unset-only immutable algorithm selection, confirmed explicit-factor dependency
  cleanup, arbitrary params JSON draft/object validation, click-time aggregate
  training-validation presentation, canonical evaluation transitions/preview, tuning
  enablement/search-space drafts, evaluation/result/progress fit counts, result labels, and live
  progress presentation.
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
