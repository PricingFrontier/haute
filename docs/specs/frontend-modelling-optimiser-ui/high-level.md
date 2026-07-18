# Frontend Modelling & Optimiser UI — High-Level Specification

## Purpose

This component is the config/preview UI for the two "compute" node types in the pipeline
graph editor: **Modelling** (train a CatBoost or GLM model) and **Optimiser** (solve an
online or ratebook price optimisation). Each node type gets a top config panel (parameters
the user sets before running) and a bottom preview panel (results once the run completes,
or a pre-run data preview for the optimiser). The component turns a wide, technical
parameter surface — loss functions, GLM families, regularisation, solver tolerances,
constraint bounds — into a form a pricing analyst can operate without reading the backend
source, while refusing to let the analyst submit a run whose meaning is ambiguous.

Both node types share one recurring problem: several of the underlying libraries and
statistics have "reasonable-looking" defaults that are actually silent behaviour changes
(CatBoost training without an explicit loss, a GLM with no family, Tweedie's variance power
defaulting to 1.5, a Negative Binomial GLM's dispersion `theta` defaulting to 1.0,
elastic-net collapsing to pure Ridge, an empty GLM factor list silently becoming "one term
per every column"). This UI exists to make each of those an explicit, visible choice rather
than an invisible fallback.

## Scope

In scope:
- The Modelling node's config panel (algorithm gateway, CatBoost config, GLM config, train
  action) and its result preview (metrics, coefficients, loss curves, lift, residuals,
  feature importance, AvE, PDP).
- The Optimiser node's config panel (mode, objective/constraints, ratebook factor
  selection, frontier settings, solver tuning), its pre-solve data preview (per-quote
  scenario charts and statistics), and its post-solve result preview (frontier chart,
  summary, ratebook rates, convergence, export).
- Pure helpers that exist only to support these panels: scenario statistics
  (`optimiserScenarioStats.ts`), frontier/detail formatting, save-path derivation.

Out of scope (see linked specs):
- The actual training/solving computation and its API contract —
  [modelling](../modelling/high-level.md) and [optimiser](../optimiser/high-level.md).
- Background job submission, polling, and lifecycle once a job id is registered —
  [background-jobs](../background-jobs/high-level.md).
- Generic graph/editor plumbing this UI consumes but does not own: `OnUpdateConfig`,
  `buildGraph`, `useGraph`, the shared preview chrome (`PreviewPanelFrame`,
  `PreviewPanelTabs`), form controls (`CommittedTextField`) —
  [frontend-node-editors](../frontend-node-editors/high-level.md) /
  [frontend-shared](../frontend-shared/high-level.md).
- MLflow registration/logging mechanics beyond the button that triggers them —
  [mlflow-model-registry](../mlflow-model-registry/high-level.md).
- Banding-node rating-factor extraction consumed by ratebook mode —
  the submodels/rating banding component.

## Behaviour

### Modelling config

- Until an algorithm is chosen, the panel shows only an algorithm picker (CatBoost / GLM).
  Nothing else renders — there is no default algorithm.
- Once chosen, the panel renders algorithm-specific sections (target/task, features/
  hyperparameters for CatBoost; target/family, factors, regularisation for GLM), then two
  sections shared by both algorithms: split strategy + MLflow + monotonic constraints, and
  the train action/result section.
- The Train button is disabled whenever the training objective is incomplete — no loss
  function/family chosen, Tweedie selected without a variance power, Negative Binomial
  selected without a dispersion `theta`, GLM with no factors and "All features" unticked,
  or elastic-net selected without an L1 ratio. The disabled state shows the specific
  missing field so the user knows what to set.
- Several of those same gated fields render a "Set X (required)" button in place of a
  slider/value until the user makes an explicit choice, plus a hover tooltip explaining
  what the silent default would otherwise have been.
- For the two dispersion-shaped GLM parameters — Tweedie's variance power and Negative
  Binomial's `theta` — the gate also offers an "Estimate from data" action: it runs a
  profile-likelihood fit over the node's upstream training data on the backend and fills
  the resolved value into the same editable field the user could type into by hand. The
  estimate is always an explicit, cancellable-by-navigating-away user action; the field is
  never populated automatically, and a failed estimate shows an inline error without
  touching the field's current value.
- A RAM/VRAM estimate is fetched automatically as the config or upstream graph changes;
  the panel shows the estimate, warns when the row count will be downsampled to fit memory,
  and separately warns when GPU VRAM would be exceeded (training then falls back to CPU
  automatically, per the backend).
- Submitting a train run is asynchronous: the panel shows a "Config changed since last
  training" banner if the config was edited after the cached result, and re-training
  replaces the stale badge with a fresh run once it completes.
- Feature selection tracks columns that used to exist upstream but no longer do (renamed,
  removed) — such entries show as "stale" with a way to clear them individually or in bulk,
  distinct from ordinary include/exclude toggles.

### Modelling preview

- Shows only the tabs for which the result actually has data (a GLM run with no lift data
  never shows a Lift tab; a run with no PDP diagnostics never shows a PDP tab). Summary is
  always available and is the tab shown whenever a new result arrives.
- A thin progress bar overlays the top of the panel while a training job is still running.

### Optimiser config

- Two modes: **online** (per-quote lambda optimisation against constraints) and
  **ratebook** (rating-factor-level optimisation, requires a connected Banding node as the
  source of rating factors and levels).
- The objective and constraint columns are drawn from whichever connected input node is
  selected as the "data input" — not simply the union of all upstream columns — so a
  multi-input optimiser doesn't offer a ratebook's factor-table columns as objective
  candidates.
- In ratebook mode, selecting a banding source auto-selects all of its factors as the
  optimiser's factor columns on first configuration; the user can still toggle individual
  factors afterward.
- Constraints support two result shapes: a single point at a fixed bound, or an efficient
  frontier over a min/max range with a configurable step count. Frontier ranges can be
  filled by hand or fetched via "Auto range", an asynchronous job with its own progress/
  cancel lifecycle that proposes bounds for every configured constraint at once.
- The Solve button is disabled until an objective is chosen, and — in ratebook mode — until
  at least one factor column is selected.
- A "Config changed since last solve" banner and re-run action mirror the modelling panel's
  staleness indicator; a solve-cost preview (quote count, scenarios per quote, total rows)
  is fetched the same way the RAM estimate is on the modelling side.
- Non-convergence is surfaced as a distinct warning banner (not an error) once a result
  exists, separate from the pass/fail state of the solve call itself.

### Optimiser data preview (pre-solve)

- Shows the objective and constraint series for one quote at a time as a line chart against
  scenario index, with prev/next navigation and a quote-id search box; a checkbox legend
  toggles which series are plotted.
- A Statistics tab aggregates the same series across every scenario index in the preview
  (not per-quote) into count/mean/std/percentile rows; this is computed only when the tab
  is actually open.
- Chart data is capped at a fixed number of preview rows regardless of how many rows the
  upstream preview returns, to keep per-quote grouping and rendering responsive.

### Optimiser preview (post-solve)

- Tabs are conditional on what the result contains: Frontier only if frontier points exist
  (and is the default tab when they do, otherwise Summary is default), Rates only for
  ratebook mode with rate data (or while it is still being fetched), Convergence only if
  iteration history was recorded, Export always.
- Clicking a frontier point selects it; the header shows a "Point N of M" stepper once a
  point is selected, and a detail card shows that point's objective, constraint values
  (with a met/not-met indicator), and lambdas, alongside Save and Log-to-MLflow actions.
- In ratebook mode, selecting a frontier point may require fetching that point's full
  rate tables from the backend (they are not embedded in every frontier point); the UI
  shows a loading state on the Summary/Rates tabs while that happens and surfaces the error
  inline if it fails.
- The Export tab lets the user load a full result-detail preview (row-level scored output),
  save the result as a JSON artifact, and log it to MLflow — independent of which frontier
  point (if any) is selected.

## Design rationale

- **Client-side gating mirrors backend validation exactly, on purpose.** The frontend's
  `trainingObjectiveIssue()` reimplements the backend's objective-completeness check
  field-for-field so that a config that passes the UI also passes the route, and vice
  versa — the alternative (a looser client check) would let users submit runs the backend
  then rejects with the failure surfacing far from the fields that caused it.
- **Gated fields show what the silent default would have been.** Rather than just
  disabling a control, the "Set X (required)" buttons and hover tooltips (`FailoverHelp`)
  spell out the value that would otherwise have been chosen for the user, on the view that
  an explained requirement is easier to act on than an unexplained one.
- **Results live in a node-keyed store, not component state.** `useNodeResultsStore` holds
  train/solve results and in-flight jobs by node id so switching away from a node (or
  closing/reopening the panel) never discards an expensive run; a config-hash comparison
  (not a deep diff) flags staleness cheaply without needing to keep the previous config
  object around.
- **Staleness is config hash + data source + structural version, not config hash alone.**
  A cached train/solve result whose `source` or `structuralVersion` no longer matches the
  live values reads as stale even when its `configHash` still matches — otherwise switching
  the active data source, or an upstream node's structure changing, could leave a result on
  screen that was computed against different data but happens to share the same config. A
  cached result predating this contract (missing `source`/`structuralVersion`) fails the
  comparison and reads as stale, never as current, by construction.
- **Dispersion estimation is a separate, explicit action, not a smarter default.** Rather
  than picking a "less wrong" default for Tweedie's variance power or Negative Binomial's
  `theta`, the "Estimate from data" button runs the same kind of computation a silent
  default would have skipped — a real fit against the node's actual training data — but
  surfaces it as a value the user reviews and can override, on the same principle as the
  rest of this UI's gating.
- **The optimiser's column source is the selected data input, not the column union.**
  Early designs that unioned all upstream columns let a ratebook's per-level factor table
  leak into the objective/constraint dropdowns of an otherwise-unrelated online optimiser
  sharing the same graph; scoping columns to the explicitly selected input node fixed that
  at the cost of an extra selector.
- **Optimiser save paths always embed the node id.** A label-only save path let two nodes
  with case-variant labels ("Foo" vs "FOO") silently overwrite each other's saved result,
  since the backend save route has no overwrite guard; embedding the node id makes the path
  unique per node while still overwriting on a genuine re-save of the same node.
- **The GLM factor JSON editor exists as an Atelier paste-in target, not just a power-user
  escape hatch.** Atelier is a separate, standalone interactive GLM workbench where a user
  iteratively curve-fits terms/interactions; `GLMFactorConfig`'s JSON textarea is designed so
  that config can be pasted in directly from Atelier as well as hand-edited. The visual
  builder and the JSON textarea are both views over the same `terms` dict (the single source
  of truth) — a change from either side updates that one dict, so pasting a full Atelier
  export and switching back to the visual builder always shows it correctly reflected.
- **Scenario statistics are computed lazily and fail loudly.** The Statistics tab's
  aggregation is deferred until the tab is opened (avoiding an O(n·log n) sort per series on
  every render), and it throws rather than substituting a zero or blank cell when a required
  column is missing, non-numeric, or internally inconsistent (e.g. the same scenario index
  reporting two different scenario values across quotes) — consistent with this codebase's
  preference for loud failure over a quietly wrong chart.

## Interactions

- **Depends on** [modelling](../modelling/high-level.md) for the training pipeline and the
  `TrainResult` contract this UI renders, its GLM dispersion-estimation endpoints
  (`/api/modelling/dispersion/estimate|status|cancel`, wired through `api/dispersion.ts`'s
  `runDispersionEstimate` and consumed here as `ModellingConfig.handleEstimateDispersion`),
  and [optimiser](../optimiser/high-level.md) for the solve pipeline and
  `OptimiserSolveResult`/`FrontierData` contracts.
- **Depends on** [background-jobs](../background-jobs/high-level.md): once a train/solve
  call returns `status: "started"`, this UI only registers the job id in the results store
  (`startTrainJob` / `startSolveJob`) — polling, progress updates, and completion are driven
  by the background-jobs hook, not by these panels.
- **Depends on** [frontend-node-editors](../frontend-node-editors/high-level.md) /
  [frontend-shared](../frontend-shared/high-level.md) for `OnUpdateConfig`, `buildGraph`,
  `useGraph`, the shared preview chrome (`PreviewPanelFrame`, `PreviewPanelTabs`), and form
  controls (`CommittedTextField`, `Tooltip`).
- **Depends on** [mlflow-model-registry](../mlflow-model-registry/high-level.md) for the
  actual logging behind the "Log to MLflow" buttons in both the modelling summary tab and
  the optimiser export/detail-card actions.
- **Depends on** the submodels/rating banding component for `extractBandingLevelsForNode`
  and `bandingLevelOrderForOptimiser`, which supply ratebook mode's factor names, levels,
  and display ordering.
- **Depended on by** nothing further downstream — this is leaf UI wired into the graph
  editor's node-type → component registry (owned by
  [frontend-node-editors](../frontend-node-editors/high-level.md)), not consumed by other
  panels.

## Failure model

- **Submission failures surface in the config panel, not the preview.** A failed train or
  solve call (thrown by the API client, or an immediate `status: "error"` response) is
  converted into an error result and shown inline where the action was triggered, including
  an `ExecutionDiagnosticsSummary` for memory-pressure detail when the backend reported it.
  There is no result to show in the preview panel in this case.
- **The objective-completeness gate is advisory, not a substitute for backend validation.**
  It exists to give the user an immediate, in-place reason before submitting; the backend
  still independently rejects an incomplete objective.
- **Scenario statistics throw on bad input.** `computeScenarioStatsBySeries` raises on a
  non-finite/missing value in a configured series or index column, and on a scenario index
  that maps to two different scenario values across quotes. Nothing in
  `OptimiserDataPreview` catches this — it is allowed to surface as a render error rather
  than silently rendering a wrong or blank statistics table.
- **Frontier auto-range failures are bucketed and shown inline**, distinguishing a
  memory-limited failure (shown with the execution diagnostics detail) from a generic one;
  cancellation/component-unmount best-effort-cancels the backend job and only logs a console
  warning if that cancel call itself fails, since there is no UI surface left to show it on.
- **Column-fetch failures show a toast and fall back to an empty column list** rather than
  blocking the panel — the user can still see and edit every other field, just without
  populated dropdowns until the fetch is retried (e.g. by changing the data input).
- **RAM/solve-cost estimate failures are non-blocking.** They surface via toast and leave
  the estimate panel absent; they never prevent the user from training or solving.
- **A dispersion-estimate failure is local to its field, not the whole panel.** It shows an
  inline "Estimation failed: …" message under the gated Tweedie/Negative-Binomial field
  (preferring the backend's structured error detail over a generic HTTP message) and leaves
  the field exactly as it was — still gated/empty if the user hadn't set anything, unchanged
  if they had. It never disables the rest of the config or the Train button beyond what the
  gate itself already does.
