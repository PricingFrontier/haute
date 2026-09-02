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

- Modelling config selects an algorithm, feature/target/evaluation/metric options and
  algorithm-specific controls, including GLM factors, regularisation, dispersion
  estimation and optional CatBoost tuning. Training submission
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
- Optimiser input selection stays scoped to connected incoming edges and can
  explicitly select a `data_input`. Each option's displayed and persisted value is the edge's
  exact executable input name, so separate frames from one API Input remain separate choices and
  source node ids never leak into the user contract. `banding_source` and Optimiser Apply's
  `ratebook_input` use the same identity rule. Its columns come from the guarded
  post-provider/post-Polars schema or preview contract; the panel does not
  inspect provider config or trigger a snapshot build merely to discover them.
  Selector matching is byte-for-byte; the UI does not trim or otherwise normalise
  a stale persisted value into a valid input name. A present non-string selector is
  malformed, remains visibly invalid, and blocks solve; it is not treated as an absent
  selector eligible for single-input inference.
  Ratebook factor-level ordering resolves the configured `banding_source` through
  that exact incoming edge name too; it neither interprets the value as a node id
  nor infers a sole Banding parent when the selector is absent or stale.
  Optimiser Apply always shows an explicit input selector for a loaded ratebook
  artifact, including with one connected input; its empty option is an incomplete
  configuration, never a first-edge default.
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

- **Target** — target/weight/offset, a unified loss-function picker and variance power, metrics
  (CatBoost); family/link/dispersion/intercept/metrics (GLM). CatBoost has no separate task
  selector: choosing a loss derives and stores its regression/classification task, every supported
  loss remains visible, and metrics that are incompatible with the selected loss stay visible but
  disabled. Selecting Tweedie directly reveals its variance-power slider; when no prior value is
  stored, that selection writes the displayed 1.5 midpoint in the same update instead of showing
  an intermediate required-value warning action. It also shows the selected algorithm as read-only
  context. The gateway may set the algorithm only while it is unset; after selection, neither this
  pane nor any other supported-node editor action changes it. To configure the other algorithm,
  the user creates a separate modelling node, preserving the original node and all of its settings.
- **Features** — both algorithms get the same always-expanded feature-card browser with a
  case-insensitive name-substring search, upstream dtype labels, and the existing explicit
  not-found treatment/removal for stale exclusions. Each eligible feature has one compact,
  single-row bordered card: the name and dtype sit on the left, followed by the current-state
  inclusion button and monotonicity selector on the right. The green **Include** or red
  **Exclude** button reports its current state and toggles that state. These are compact,
  content-width pills using the same soft-tint, accent-border and
  accent-text treatment as the Data Input **Provider** selector; they do not stretch across the
  card. **Include all** and **Exclude all** use the same compact green/red treatment and set every
  eligible feature, including features hidden by the current search. Columns consumed as target, weight,
  offset, or active evaluation/metadata roles are not presented as trainable features. GLM then
  adds its factor/term editor below the common browser. Each card places a three-option
  monotonicity selector directly beside the inclusion button without a repeated visible label: a
  red downward arrow (`-1`), yellow dash (no
  stored constraint), and green upward arrow (`1`). Each choice uses the Provider-style compact
  control, with its soft tinted surface and accent border identifying the selected direction. The
  selector is enabled only for final selected numeric
  features: included CatBoost features, or included GLM `terms` (all included features when
  `all_factors` is true). Excluding a feature is immediate and reversible: it does not ask for
  confirmation or delete that feature's monotonic direction, GLM term, or interaction settings.
  The stored direction remains visibly selected in the greyed, disabled control, does not apply while the
  feature is excluded, and becomes active again when the feature is re-included. Explicit GLM term
  removal or narrowing from `all_factors` remains a confirmed atomic cleanup of dependent terms,
  interactions, and monotonic constraints; Cancel preserves every field.
- **Params** — immediately below the Hyperparameters heading, CatBoost shows a
  Target-style **Parameter strategy** radio group with **Fixed parameters** and
  **Tune parameters** choices. Exactly one strategy body is visible. Fixed parameters
  shows only the algorithm-neutral **Parameters JSON** object editor; Tune parameters
  instead shows Trial count, Seed, Selection metric and **Search space JSON**. Neither
  JSON editor has Apply/Revert controls or an inline explanatory block. A syntactically
  valid top-level object updates its corresponding config automatically, except that a
  fixed-parameter object containing the Train-owned `task_type` key is rejected. Invalid,
  non-object, or reserved-key drafts remain local for that node and contribute only to the
  ordinary click-time Train validation banner while their strategy is selected. This preserves
  the complete CatBoost parameter surface without duplicating the algorithm library's evolving
  catalogue. An empty stored non-GPU projection displays the familiar CatBoost UI defaults as
  the editable fixed draft; arbitrary current or future keys and nested JSON values round-trip
  unchanged, and the Train-pane GPU `task_type` remains merged from the latest stored object.
  Search-space candidate arrays render on one line per parameter while nested conditional objects
  remain indented. Selecting Tune parameters seeds editable candidate lists for `depth`,
  `learning_rate`, and `l2_leaf_reg`. When `evaluation.test` is absent and validation is enabled,
  the same update adds a 20% random/group test or an empty temporal test start for the user to
  complete; it never rewrites an existing evaluation choice. Selecting Fixed parameters removes
  tuning without changing the last valid fixed Parameters JSON.
  The editor component accepts algorithm label/default/reserved-key inputs
  so another algorithm with a `params` object can reuse it without bespoke controls. GLM Params
  retains the regularisation controls because GLM's canonical editable fields live at the node
  top level rather than in `config.params`.
- **Split** — one canonical version-1 evaluation workflow. It asks how data is
  structured (Random rows, Keep entities together, Respect time order), how candidates
  are validated (Single validation, Cross-validation, No validation), and whether an
  untouched final test is reserved. Random/group forms use source-relative validation
  and test sizes plus a deterministic seed; group additionally selects its entity
  column. Temporal forms select a date column and explicit validation/test starts;
  temporal CV is expanding-window only. Once relevant fields are valid, a neutral
  estimate preview shows development/final-test counts, validation fit count and row
  bounds, plus group counts or date ranges. The pane uses only **development data**,
  **validation**, and **final test** terminology.
- **Train** — the GPU toggle (CatBoost only, still stored as the GPU task-type parameter), row
  limit beside the RAM/VRAM estimate it modulates, MLflow experiment/model-name logging fields,
  staleness banner, Train/Cancel actions, click-time validation banner, live progress, completion
  badge and error card. Its checkbox and text/number controls use the same visible themed borders,
  backgrounds, typography and spacing as the rest of the modelling editor; labels never collapse
  into input placeholders. The setup panes and their tabs never expose missing-field warnings:
  Target is labelled only `Target`, and Features/Params are equally free of attention badges.
  Train remains enabled while idle. With tuning enabled the action reads **Tune &
  Train**. Before the first invalid Train or Re-train attempt, the validation banner is absent.
  An invalid press sends no request, latches validation presentation on, and reveals one banner
  directly beneath the main Train button listing every current frontend Train-guard issue,
  including an invalid JSON draft for the selected parameter strategy. Once revealed, the banner
  tracks the current non-empty issue list. Reaching an empty list hides the banner and resets the
  reveal state, so a later invalid configuration again waits for a Train press; a valid press
  starts training. Live progress renders the
  backend's existing bounded `train_loss_history` snapshot and
  `train_loss_history_truncated` state rather than reconstructing iterations from polls; a
  truncated chart is labelled as the latest retained window. A browser-derived estimated time
  remaining uses successive distinct increasing `(iteration, elapsed_seconds)` samples for the
  current job. It is hidden until two valid samples exist, on a duplicate/stalled or
  non-monotonic update, when iteration/total/rate is non-finite or non-positive, and after
  completion; a new job clears the estimator. Tuning progress instead displays phase,
  trial/fold indices, completed/total fits and best objective, and derives ETA only
  from enough completed increasing fit durations.

Cross-pane signalling is intentionally limited to active work. `NodePanel` derives only the
active-job descriptor, and the Train tab shows that accessible indicator while a job is running
so progress is visible from any pane. Configuration completeness never changes a tab's visible
or assistive label.

**Completed results.** Summary shows final-test metrics first when present, then the
selection score and variability, baseline improvement, winning/final parameters,
fit count/elapsed time and a bounded top-trial table. **Use best as fixed
parameters** asks for confirmation, atomically writes the reported fixed-parameter
projection and disables tuning; an asynchronous result never mutates node config
automatically.

**Non-goals.** No GLM structural-model tuning; no multi-objective, parallel,
distributed or nested-CV tuning; no parameter-specific form builder; no run
history/comparison (MLflow is the system
of record); no EDA readouts in the editor (the explore
node owns those); no collapsed-header summaries; no importance-threshold exclusion actions; and no
algorithm switching or in-place algorithm migration.

**Failure and compatibility semantics.** The frontend continuously derives its training-guard
target, objective, evaluation and bounded-tuning issues, plus any invalid draft for the selected
CatBoost parameter strategy. The Train pane keeps those messages hidden until an invalid Train or
Re-train press, suppresses that request, and then reflects the current non-empty issue list beneath
the button. Resolving the complete list resets that reveal state. The backend remains authoritative
for the full configuration and candidate/conditional search-space contract and still rejects an
invalid request if reached.
Cancellation, terminal-race, GPU-VRAM 507 and estimate-failure behaviour are unchanged. A
malformed present live-history entry fails at the runtime response boundary exactly like malformed
completed-result history; absent history produces no chart and is never synthesized from
latest-loss polls. Under the
[canonical-only format policy](../README.md#canonical-only-format-policy),
the `modelling.features`/`modelling.mlflow`/`modelling.monotonic` section keys cease to be read or
written; the generic section store and any inert in-memory entries need no migration.

**Regression evidence.** Suites in
`frontend/src/panels/__tests__/ModellingConfig.test.tsx` and under
`frontend/src/panels/modelling/__tests__/` prove: five panes with the ownership above for both
algorithms and the unsupported-algorithm diagnostic; CatBoost's unified all-loss picker,
loss-derived task/default metrics, and visible disabled incompatible metrics; the common searched/dtype-labelled
feature-card browser for CatBoost and GLM, including current-state per-card toggles and
search-independent bulk actions; confirmation-free reversible exclusion with dormant monotonic
and GLM settings; inline arrow-based, role/final-selection-aware monotonic controls and confirmed
explicit-factor cleanup; exact atomic dependent-cleanup/cancel semantics; unset-only algorithm selection, read-only selected-algorithm
context, and the absence of an in-place change action; mutually exclusive fixed/tuned Params
bodies, arbitrary params JSON round trips, valid fixed/search-space autosave, compact default
draft presentation, invalid/non-object/reserved-key draft persistence without Apply/Revert or
inline warnings, selected-strategy click-time validation, and GPU task-type merge; plain setup-tab
labels, click-time-only aggregate validation beneath Train, authoritative bounded live-history
rendering including truncation; and time-remaining
show/hide/reset behaviour for valid, insufficient, duplicate/stalled, non-monotonic, terminal, and
new-job samples. `frontend/src/panels/__tests__/NodePanel.test.tsx`,
`frontend/src/stores/__tests__/useUIStore.test.ts`, and
`frontend/src/panels/__tests__/PreviewPanelTabs.test.tsx` prove strip gating, per-node memory,
active-indicator accessibility, and unchanged roving-keyboard behaviour. Runtime/store suites prove
strict live-history parsing, latest-status retention, and per-job estimator reset.
