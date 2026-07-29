# Frontend Modelling & Optimiser UI — High-Level Specification

## Purpose

This component configures, submits and explains modelling training and optimiser solves. It
combines domain-specific configuration controls with result panels for diagnostics, charts,
frontiers, ratebooks, exports and previewed optimiser scenarios.

## Scope

It owns the modelling and optimiser configuration/preview panels and their local chart, summary,
factor/ratebook and helper modules. Node-editor dispatch is documented by
[frontend-node-editors](../frontend-node-editors/high-level.md); job execution and persisted
results are supplied by API and result-store layers.

## Behaviour

- Modelling config selects an algorithm, feature/target/split/metric options and algorithm-specific
  controls, including GLM factors, regularisation and dispersion estimation. Training submission
  records the returned job handle immediately, keeps an explicit Cancel control visible through
  preparation and fitting, and shows progress, estimates, failures and result actions. An
  insufficient-GPU estimate or terminal 507 asks the user to select CPU and retry (or reduce the
  workload); it never claims that CPU fallback happened automatically.
- The modelling editor exposes monotonicity as the sole additional cross-algorithm
  capability lever. It lists only selected numeric input features and writes `-1` or
  `1` per feature, removing the key when the user selects zero. String/categorical,
  Boolean, date/datetime, target, weight, and excluded columns are never offered.
- Modelling preview exposes only result-backed tabs (summary, coefficients/relativities, loss,
  lift, residuals, feature importance, AVE and PDP) and resets selection when the result changes.
- Optimiser config selects input/objective/mode, banding/ratebook factors, constraints, solver
  options and frontier ranges; it can auto-range constraints and submit solves. Starting another
  auto-range request or unmounting best-effort cancels that auto-range job; this panel has no
  solve-cancel control.
- Ratebook source and inferred factor columns change in one atomic config update. Constraint
  renames/removals migrate or remove their matching canonical `frontier_ranges` entry in the
  same update; range fields never inherit the removed global frontier bounds.
- Optimiser preview renders summary, convergence, detail, frontier, ratebook/rates and export
  flows. Selecting another frontier point clears stale materialised detail before enabling Save
  or MLflow actions, and structured API details are preferred on failures. The data preview
  groups and charts bounded scenario samples and can calculate statistics.
- Modelling and optimiser action areas render actionable memory-pressure and
  rejected-strategy diagnostics with profile, blocking node/operator, cost,
  reason, and remediation when the guarded payload supplies them. Other,
  missing, or unsupported planner detail never becomes an invented success or
  pre-emptive submit gate; the primary request/status error remains visible.
- Optimiser input selection stays scoped to connected upstream nodes and can
  explicitly select a `dataInput`. Its columns come from the guarded
  post-provider/post-Polars schema or preview contract; the panel does not
  inspect provider config or trigger a snapshot build merely to discover them.
  Snapshot/capability failures remain visible in estimate, solve, and
  auto-range states.
- An explicit Banding source that is no longer directly connected and
  recognised configured outputs with zero valid levels are combined into one
  accessible warning, while healthy factors remain selectable. An
  unconfigured source or ordinary empty graph does not warn, and the
  exactly-one-direct-Banding fallback is unchanged.

## Design rationale

Large config forms are split into focused subcomponents so algorithm/mode gates preserve hidden
configuration instead of destructively rewriting it. Multi-field invariants use one object
update because the production callback spreads the last committed config. Asynchronous actions
are sequence/abort aware where a newer request can supersede an older request. Result tabs defer
expensive work until opened and use structured backend diagnostics when available.

## Interactions

This component consumes graph/node-result stores, API clients, background-job state and
`frontend/src/utils/banding.ts` for ratebook factor levels. It is routed from the node editor and
shares the preview frame/tab controls with
[frontend-preview-explore](../frontend-preview-explore/high-level.md).

## Failure model

Training, solve, MLflow, export, auto-range and materialisation failures are displayed in their
local action/result area. Training cancellation posts the active job ID, records the returned
terminal state immediately, and leaves a terminal race winner intact. Structured training status
fields (`error_code`, `http_status_code`, `error_detail`) survive runtime parsing so an asynchronous
GPU-VRAM 507 retains its actionable server message. A locally aborted/superseded auto-range request
suppresses an error, while a terminal cancelled/superseded status returned by the server is shown
in the auto-range error area. Deliberately strict helper parsers throw for malformed numerical
result contracts rather than silently charting incorrect values.

## Modelling config panes

With a supported algorithm (`catboost` or `glm`) selected, the modelling node
panel presents five panes — **Target**, **Features**, **Params**, **Split**, **Train** — through the
same shared equal-width pane-tab strip the Explore editor uses, hosted by the node panel
([frontend-node-editors](../frontend-node-editors/low-level.md#modelling-config-panes))
and extended with the accessible active-training indicator by
[frontend-preview-explore](../frontend-preview-explore/low-level.md#modelling-config-panes).
The active pane is remembered per node in the UI store. Without an algorithm, the existing gateway
renders alone. A non-empty unsupported algorithm renders an explicit inline diagnostic and no pane
strip; it never falls through to CatBoost. Pane ownership:

- **Target** — target/weight/offset, task, loss function and variance power, metrics (CatBoost);
  family/link/dispersion/intercept/metrics (GLM). It also shows the selected algorithm as read-only
  context. The gateway may set the algorithm only while it is unset; after selection, neither this
  pane nor any other supported-node editor action changes it. To configure the other algorithm,
  the user creates a separate modelling node, preserving the original node and all of its settings.
- **Features** — both algorithms get the same always-expanded include/exclude browser with a
  case-insensitive name-substring filter, upstream dtype labels, and the existing explicit
  not-found treatment/removal for stale exclusions. Columns consumed as target, weight, offset,
  or active split/metadata roles are not presented as trainable features. GLM then adds its
  factor/term editor below the common browser. Monotonic constraints move here as a collapsible
  sub-section for both algorithms and offer only final selected numeric features: included
  CatBoost features, or included GLM `terms` (all included features when `all_factors` is true).
  Any action that removes final selected features (single/bulk exclusion, GLM term removal, or
  narrowing from `all_factors`) is one confirmed atomic change: it also removes affected monotone
  constraints, explicit terms, and interactions; Cancel preserves every field.
- **Params** — CatBoost gets one algorithm-neutral JSON-object editor and no parameter-specific
  fields. This keeps the complete CatBoost parameter surface available without making the
  frontend duplicate or lag the algorithm library's evolving catalogue. An empty stored
  non-GPU projection displays the familiar CatBoost UI defaults as the editable draft; otherwise
  every stored key is shown. The editor validates only JSON syntax and top-level object shape,
  so arbitrary current or future CatBoost parameter keys and nested JSON values round-trip
  unchanged. A valid Apply replaces the complete non-GPU projection, while the Train-pane GPU
  `task_type` is merged from the latest stored object; Revert restores the stored projection.
  An invalid or dirty draft never updates config and survives navigation away from and back to
  Params for that node. The editor component accepts algorithm label/default/reserved-key inputs
  so another algorithm with a `params` object can reuse it without bespoke controls. GLM Params
  retains the regularisation controls because GLM's canonical editable fields live at the node
  top level rather than in `config.params`.
- **Split** — the split strategy and its per-strategy fields and allocation bar, and nothing else.
- **Train** — the GPU toggle (CatBoost only, still stored as the GPU task-type parameter), row
  limit beside the RAM/VRAM estimate it modulates, MLflow experiment/model-name logging fields,
  staleness banner, Train/Cancel actions, click-time validation banner, live progress, completion
  badge and error card. Its checkbox and text/number controls use the same visible themed borders,
  backgrounds, typography and spacing as the rest of the modelling editor; labels never collapse
  into input placeholders. The setup panes and their tabs never expose missing-field warnings:
  Target is labelled only `Target`, and Features/Params are equally free of attention badges.
  Train remains enabled while idle. An invalid Train or Re-train press sends no request and
  reveals one banner directly beneath the main Train button listing all currently missing
  target/objective items. The banner is absent before that press and disappears once the
  configuration is complete; a second press then starts training. Live progress renders the
  backend's existing bounded `train_loss_history` snapshot and
  `train_loss_history_truncated` state rather than reconstructing iterations from polls; a
  truncated chart is labelled as the latest retained window. A browser-derived estimated time
  remaining uses successive distinct increasing `(iteration, elapsed_seconds)` samples for the
  current job. It is hidden until two valid samples exist, on a duplicate/stalled or
  non-monotonic update, when iteration/total/rate is non-finite or non-positive, and after
  completion; a new job clears the estimator. No new backend response field is introduced.

Cross-pane signalling is intentionally limited to active work. `NodePanel` derives only the
active-job descriptor, and the Train tab shows that accessible indicator while a job is running
so progress is visible from any pane. Configuration completeness never changes a tab's visible
or assistive label.

**Non-goals.** No hyperparameter-tuning engine or tuning-mode UI; no cross-validation
configuration pane (the versioned backend contract is configured outside this pane set, while its
completed report is shown in the result summary); no run history/comparison (MLflow is the system
of record); no EDA readouts in the editor (the explore
node owns those); no collapsed-header summaries; no importance-threshold exclusion actions; no
algorithm switching or in-place algorithm migration; no backend route, training config schema, or
codegen change — pane state is browser-only.

**Failure and compatibility semantics.** The frontend aggregates its backend-mirroring
target/objective checks only when Train is requested. An invalid press is stopped locally and
shows the banner beneath the button; the backend remains authoritative and still rejects an
incomplete request if reached. Cancellation, terminal-race, GPU-VRAM 507 and estimate-failure
behaviour are unchanged. A malformed present live-history entry fails at the runtime response
boundary exactly like malformed completed-result history; absent history produces no chart and is
never synthesized from latest-loss polls. Under the
[prerelease canonical-only format contract](../README.md#approved-change-contract--prerelease-canonical-only-formats),
the `modelling.features`/`modelling.mlflow` section keys cease to be read or written; the generic
section store and any inert in-memory entries need no migration. The `modelling.monotonic` key
remains for the Features-pane sub-section.

**Regression evidence.** Suites in
`frontend/src/panels/__tests__/ModellingConfig.test.tsx` and under
`frontend/src/panels/modelling/__tests__/` prove: five panes with the ownership above for both
algorithms and the unsupported-algorithm diagnostic; the common filtered/dtype-labelled
include/exclude browser for CatBoost and GLM;
role/final-selection-aware monotonic candidates and confirmed dependent cleanup; exact atomic
dependent-cleanup/cancel semantics; unset-only algorithm selection, read-only selected-algorithm
context, and the absence of an in-place change action; arbitrary params JSON round trips,
default-draft presentation, invalid/non-object draft navigation/revert, and GPU task-type merge;
plain setup-tab labels, click-time-only aggregate validation beneath Train, authoritative bounded
live-history rendering including truncation; and time-remaining
show/hide/reset behaviour for valid, insufficient, duplicate/stalled, non-monotonic, terminal, and
new-job samples. `frontend/src/panels/__tests__/NodePanel.test.tsx`,
`frontend/src/stores/__tests__/useUIStore.test.ts`, and
`frontend/src/panels/__tests__/PreviewPanelTabs.test.tsx` prove strip gating, per-node memory,
active-indicator accessibility, and unchanged roving-keyboard behaviour. Runtime/store suites prove
strict live-history parsing, latest-status retention, and per-job estimator reset.
