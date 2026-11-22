# Staged tboost implementation plan

Implemented plan, completed 20 November 2026. The requested workflow is to build the full GBM,
inspect its pruning path, choose how far to prune, then band and smooth it. Each completed
stage supplies a prediction line controlled by a checkbox in the model's left-hand settings.
Parameters belong to the stage that uses them. Ralph's follow-up puts separate stage
configuration boxes and action buttons in a right-hand Training pane, replacing the top
Train button. Milestones A–D are complete and installed in the local demo.

The first milestone was **build GBM → inspect path → preview and apply pruning**,
including saved results and full-versus-pruned chart overlays. Banding and smoothing then
use the same stage machinery. Work is across `/home/ralph/suite/t-boost` and Haute;
no release, commit or publication is part of this work.

## Completion and verification

21 November follow-up: Ralph requested parameter controls separate from training actions.
The right column now has Features, Parameters and Training tabs. Parameters contains the
four stage configuration disclosures; Training contains run/cancel and visible Apply pruning /
Discard preview buttons. This supersedes the combined-card layout in the original design below.

All four milestones are implemented. `fit_staged` owns the full fit and OOB path;
`prune`, `band`, `smooth`, `configure_path` and `apply_recipe` reuse it. Versioned checkpoints
retain working/inference banks, prepared data, categorical bins and native bag evidence.
Ordinary native exports remain inference-only, and legacy flat configurations keep their
existing semantics.

Haute now provides persistent admitted/cancellable stage jobs, revision-checked preview/apply,
restart recovery, dependency/staleness checks, all four Training cards, stage overlays, a
stage table selector and keyboard-selectable pruning path. Applied recipes and their path
settings are frozen separately from pending controls. Failed or cancelled rebuilds preserve
the previous applied output. Model removal cleans up unleased stage artifacts.

Exploration supports development, holdout and genuine pooled OOF diagnostics for random,
grouped and temporal CV. Reserved test rows never enter interactive fits, paths or scoring.
GBM-only tuning retains the winning candidate's required checkpoints; later stages resume
those fits without boosting. Explicit Finalise replays the applied recipe on development
rows, evaluates reserved test rows, and enables ordinary native export and MLflow logging.
Final artifacts can be reopened for export after a server restart. Generated Python uses
the same canonical evaluator and recipe, including frozen pruning-path settings.

Verification completed:

- t-boost: 848 Python tests before checkpoint-review hardening; 58 affected tests and then
  14 checkpoint tests after fixes. Strict mypy, native rebuild/stubtest, clippy/fmt, xtask,
  slow Rust integration and documentation checks pass. Independent library review approved.
- Haute: 248 affected backend tests pass, including CV row provenance, winning-trial reuse,
  finalization/export, restart/CAS/cancellation, native artifacts and documentation accuracy.
  The subsequent snapshot-evidence contract regression passes through the real worker;
  a final run of the staged-runtime and documentation modules passes all 88 tests.
  Frontend: 271 affected tests pass, followed by all 38 ModellingView tests after the
  concurrent two-model restoration fix. TypeScript, lint, mypy, generated-contract checks
  and the three contract-generator tests pass. Independent final review approved.
- Real browser, using an isolated copy of the 100,000-row demo: GBM → pruning path →
  preview/apply → band → smooth → four overlays → finalization → native save passes.
  Desktop (1440×900) and compact (1024×768) layouts were inspected; compact document size
  exactly matches the viewport, with no page overflow. No browser JavaScript errors.
- After a real server restart, all four stages and final export restore. The new export
  is byte-identical, and ordinary native loading produces finite predictions for all
  100,000 rows. Selecting a different path point with the keyboard, preview/apply and
  banding reuse the same build. Cancelling a new GBM build preserves the applied revision.
  Separate behavioral tests forbid booster calls during resumed post-fit operations.
- Both frontend builds and the demo package were rebuilt/reinstalled. No commit, push,
  publication or external MLflow write was made. The user's saved demo settings were preserved.

Measured costs (local machine; complete worker timings include startup and diagnostics):

| Dataset / measurement | Result |
|---|---|
| Demo: 100,000 rows, four selected features, 40 trees, three bags | Full build 6.7s; pruning preview 5.3s; band 4.8s; smooth 4.7s; finalise 7.6s |
| Demo after server restart | Pruning preview 4.7s; band 4.7s; same build ID |
| Demo retained artifacts | Current bundle 11,727,925 bytes; separately retained final bundle 11,774,521 bytes |
| Demo observed worker peak RSS | Full 514 MB; post-fit stages 512–519 MB; finalise 711 MB |
| Larger library benchmark: 250,000 rows, 12 features, 100 trees, three bags | Fit + path 3.67s; save 0.59s; load 0.82s; prune 0.77s; band 0.61s; smooth 0.10s |
| Larger library checkpoint / total-process peak RSS | 12,496,142 bytes / 532 MiB; booster explicitly disabled during resume |

The sections below retain the design and acceptance requirements used for implementation.
General history, a queue, additional model families, interactive fold-vote selection and
post-fit automatic tuning remain outside this plan.

## Proposed user experience

1. Select a model and open **Training** in the right-hand pane, beside **Features**.
   Training replaces the current Parameters tab. It contains separate configuration boxes
   for **GBM**, **Pruning**, **Banding** and **Smoothing**, each with its own hyperparameters
   and action button. Features and Model Setup retain their existing locations.
2. The GBM box contains tree count, learning rate, depth, bagging, regularisation and related
   fit settings. Its **Train GBM** button fits once and retains the full model. It also
   computes the pruning path
   when out-of-bag evidence is available, without applying a cutoff, banding or smoothing.
3. Open **Pruning Path** beside Factor, Learning Curve and Tables. Inspect the minimum,
   recommended cutoff and full-model endpoint. Selecting a scored point selects a candidate;
   **Preview Pruning** in the right-hand Pruning box builds and scores it without boosting
   again. That box also holds the pruning parameters and Apply/Discard controls. Apply makes
   that candidate the current pruned result; discard leaves the previous result intact.
4. The **Banding** and **Smoothing** boxes each hold their own parameters and **Band** or
   **Smooth** button, operating on their chosen upstream result. The usual sequence is
   GBM → Pruned → Banded → Smoothed; a stage
   can be explicitly skipped, so smoothing can use the pruned or full bank too.
5. The existing model settings icon opens its controls, including checkboxes for **Full GBM**,
   **Pruned**, **Banded** and **Smoothed**. Unbuilt results cannot be ticked. The model checkbox
   remains the master visibility switch and preserves the individual choices when toggled.
   Opening a stage's configuration box and ticking its prediction line are independent actions.
6. Use the model colour consistently, with distinct stage line styles and explicit legend
   names. The existing dashed indication for a feature excluded from a model must move to
   a separate marker/label so it cannot be confused with a stage. Tables shows one selected
   stage at a time; Learning Curve belongs to the original GBM.
7. Remove Train from the top toolbar; there is no shared stage-dependent training button.
   Each stage's own action button becomes **Cancel** while that operation runs. Other run
   buttons are disabled while the existing single-job admission policy is occupied.
   Readiness warnings remain at the bottom of the right pane; nothing pushes the canvas down.
   Retain the current disclosure animation, schema settings icons and compact sidebar styling.

Proposed defaults: show the full and latest applied stage after the first prune, persist later
user visibility choices, and allow one candidate preview per model. Applying a result chooses
the model's current output; merely showing a line never changes the output or launches a fit.

## What the code already provides

- `t-boost/python/t_boost/sklearn.py`: `_fit_and_prune_core` fits the ensemble, derives table
  variances, selects supports, applies budgets/rebalancing and bands; graduation and monotone
  repair follow. `_ranked_path_select` already scores prefixes before choosing a cutoff, but
  path construction and selection are one method. `_finish_unpruned` still bands and smooths
  unless those stages are explicitly disabled.
- The native binding already exposes keep-set application, table conversion, OOB group
  scoring and banding. Existing operations should supply the numerical work; Python should
  orchestrate a public lifecycle rather than Haute reaching into private estimator methods.
- **Ordinary model saving is not a training checkpoint.** In
  `t-boost/crates/t-boost-core/src/engine/mod.rs`, bag spans, bag intercepts, in-bag membership
  and training-time categorical bins are deliberately omitted from model serialization.
  Saving the full model with its current serializer cannot preserve resumable OOB analysis.
- Haute's `_tboost.py` adapter returns one final model and table report. `TrainingJob`, the
  worker protocol and `_training_artifacts.py` publish one model with evaluation artifacts.
  Frontend `modelRuns.ts` and `ModelsAveChart.tsx` likewise treat each model as one result.
- Haute's t-boost parameter allowlist does not expose all ranked-path controls. Updating
  the visible form alone would leave backend validation and generated scripts inconsistent.

## 1 Define stage contracts and numerical boundaries

Before implementation, update Haute's modelling and frontend modelling high/low-level specs,
and add a t-boost staged-training specification. Keep this proposal separate from existing
settled brainstorm decisions until the phase is accepted.

Define a public session/checkpoint API, with names settled in that specification:

- Build a full fit and lossless full rating-table snapshot.
- Compute a pruning path from the retained fit without applying a decision.
- Select a cutoff from that path by an automatic rule or an explicit complexity budget.
- Apply pruning, banding or smoothing to a specified parent, returning a new result.
- Predict, inspect tables and export any completed result; save/load a resumable session.

Snapshots are immutable. A session owns shared fit evidence and stage lineage; each result
records its parent, effective parameters, reports and inference artifact. The ordinary
`fit()` API retains its existing behaviour and defaults, and uses the same internal stage
operations. No second implementation of the algorithms is introduced in Haute.

Separate internal working banks from inference snapshots when necessary. Currently monotone
repair runs at the end, while pruning and banding can temporarily break a constraint. Every
user-visible snapshot must honour the configured constraint. Repair a copy for prediction
without accidentally feeding that extra repair into the next stage and changing the existing
one-call pipeline. Preserve reanchoring, exposure, categorical handling and exact table
decomposition; record any repair in the snapshot's report.

Choose the staged fit policy once, before any pruning decision. Proposed: use the deployed-fit
policy of today's default pruned workflow, including its stratified group early-stopping
carve. Currently the legacy unpruned branch omits those strata for grouped Poisson, Tweedie
and logistic fits, so both legacy branches cannot serve as an identical GBM baseline.
Preserve that compatibility distinction in the existing `fit()` entry point by passing its
resolved fit policy to the shared builder. The staged session records its resolved policy;
changing a downstream pruning choice never changes it.

**Acceptance:** the full stage's tables reproduce the retained ensemble's predictions within
the existing conversion tolerance, with no pruning/banding/smoothing. Compare it to the legacy
unpruned fit only when their resolved fitting policies agree. The default complete staged
sequence agrees with today's default pruned fit; both legacy `fit()` branches retain their
own regression tests. Selecting a different cutoff does not call the booster. Earlier results
are unchanged by every later operation.
Add grouped Poisson, Tweedie and logistic fixtures that expose the two legacy partition
choices and verify their preservation before extracting the common builder.

## 2 Retain and serialize the evidence needed to resume

Introduce a versioned training-checkpoint format separate from the existing inference model
format. Retain the full ensemble, bag partitions/intercepts/membership, training categorical
bins and all additional state needed by OOB scoring and banding. Persist actual fit evidence;
do not try to reconstruct bag membership by replaying a seed. Audit native and Python state
at each boundary, and restore it through validated public bindings with matching type stubs.

Haute retains one immutable materialised development-data snapshot per build, including row
order, feature encoding, targets, weights, offsets and group/split assignments as applicable.
The checkpoint manifest binds to that snapshot and the evaluation plan by content identity.
Validate row count, order, schema and feature identities on load; refuse mismatched data.
The library checkpoint may reference caller-owned data through this contract rather than
duplicating it internally. Each evaluation fit must bind to its own exact training subset.

Persist a run manifest keyed by project, pipeline/node, model, build and stage revision. It
owns the checkpoint, shared data, path reports, inference snapshots and chart diagnostics.
Reuse Haute's artifact validation, atomic publication and job infrastructure; extend their
allowed artifact sets deliberately. Startup restores manifests instead of relying on an
in-memory worker/job record. New artifacts become current only after complete validation.

Keep one full checkpoint per build, not one copy per stage. Materialise only selected
candidates, not a full model at every path point. Measure checkpoint size, peak memory and
resume latency on the demo and a representative larger dataset. Account for these artifacts
in admission/size limits and cleanup. A reader or running child job holds its parent artifacts;
supersession and model removal cannot delete them until released. Restore completed results
after a crash and mark interrupted operations as interrupted, never successful.

**Acceptance:** restart the server, choose another pruning point and band it without fitting;
the resulting predictions and reports agree with uninterrupted execution. Cancellation,
missing/corrupt checkpoints and storage-limit failures preserve the previous completed model.

## 3 Separate path construction from the pruning decision

Path construction owns ranking, heredity, whether main effects are eligible, path resolution
and OOB scoring requirements. Selection owns fraction/tolerance or a manual table budget;
application owns any variance floor, budgets, rebalance and repair. Changing selection does
not rescore the path. Changing its structural settings may recompute it but does not boost.
Defaults make the path available after the GBM stage without asking for a pruning cutoff.

Return a typed path report with a version, parent checkpoint identity, evidence partition,
OOB row count, scored prefixes, supports added, actual candidate table counts and deviances.
Return the minimum, recommendation and applied selection as distinct fields. Do not copy
the existing `sizes` blindly onto a "total tables" axis: sticky main effects are outside the
current ranked sequence. The intercept is not a table; endpoint and count semantics need
explicit tests. An unscored point is not given an interpolated score.

The chart is OOB selection evidence from the training partition. It is not validation/test
performance, nor the score of the bank after rebalance, budgets, banding or monotone repair.
Show the previewed model's evaluation separately, including the actual deployed table count.
Never substitute in-sample predictions for unavailable OOB evidence.

With insufficient OOB rows or a single bag, keep the full fit usable and mark the path
unavailable with a concrete reason in the right pane. Do not silently launch fold-vote fits
as part of interactive path generation. Existing one-call `fit()` fallback behaviour stays
compatible. Interactive fold-vote selection is outside the first milestone.

Manual selection records an exact prefix identity for this checkpoint and an explicit
replayable rule, proposed as `max_tables=K`. On a new fit, select the largest heredity-valid
ranked prefix within K, counting sticky main effects; use all candidates if fewer exist and
refuse a budget below the mandatory-main count. Preserve full ranking so this can be evaluated
even if K was not a sampled path point. Automatic selection stores fraction/tolerance.
Never reuse a path index or feature-support set blindly in a new fold or refit.

**Acceptance:** monotone, main-effect-prunable, interaction-only, no-improvement, no-interaction
and insufficient-OOB cases give correctly labelled results. A chosen point reproduces its
recorded keep-set before explicitly reported application adjustments.

## 4 Integrate stages into Haute jobs and configuration

Add versioned per-model stage configuration: GBM parameters, path settings, pruning rule,
banding settings, smoothing settings and the applied output stage. Keep visibility/active
tabs in view state, separate from fitting configuration. Define the old flat-parameter
migration explicitly; loading an old model must not silently reinterpret its settings.
Legacy final artifacts remain viewable/exportable but do not pretend to contain a resumable
full fit. Their first staged build is a real new fit.

Extend the algorithm capability/parameter descriptors rather than giving every model family
t-boost-specific methods. Route stage operations through the existing admitted, isolated,
cancellable worker lifecycle. Requests carry model/build/parent revision and operation;
responses carry stage identity, diagnostics and artifact handles. Reject stale parent
requests. A late result may remain an old-run artifact but cannot overwrite a newer model.
Keep the existing one-active-job admission policy; a global queue is not required here.

Use dependency-aware validity:

| Change | Results that become out of date |
|---|---|
| Data, split, target, features, directions, encoding or GBM fit settings | Entire build |
| Path structure | Path and derived pruning descendants; full GBM remains valid |
| Pruning choice | Pruned result and its descendants |
| Banding settings | Banded result and descendants that use it |
| Smoothing settings | Smoothed result |
| Name, colour, line visibility or open pane | None |

Pending settings do not destroy the last applied result. It remains inspectable with its
original provenance; stale results are not passed off as matching current settings. Applying
a new pruning result detaches its old banded/smoothed descendants from the current chain.
Preview results are transient, only Apply changes the current output, and discard/cancel
retains the prior output. Keep one current result per stage and one candidate initially;
general history and arbitrary branching remain separate roadmap work.

Touchpoints include `_node_config_models.py`, `_train_config.py`, `_descriptors.py`,
`_tboost.py`, `_training_job.py`, the training preparation/worker/lifecycle/artifact modules,
`schemas.py`, generated contracts, `interactiveModels.ts`, result/view stores and model UI.
Extend canonical execution and generated Python export together: staged recipes must run
without the GUI, using the same evaluator and library stage operations.
Milestone B includes standalone GBM/pruning recipe execution and selected-snapshot native
export. Milestone C extends both to banding/smoothing. Until milestone D supports staged
tuning, CV and finalisation/logging, reject those combinations at the backend as well as
disabling their controls; an old endpoint must not run them through the legacy final-fit path.

## 5 Keep evaluation and tuning consistent across stages

All lines in a comparison use the same row identities, partition, weights, offset semantics
and shared bins. Extend series identity to model + build + stage revision. Do not rely on
matching bin labels/exposure alone to prove the same data. Calculate diagnostics for each
materialised stage once, including features omitted from its fit; toggling a checkbox is
only a display change. Avoid sending row-level predictions through status polling.

The first demo slice supports a fixed holdout or explicitly labelled development diagnostics
when no validation split is selected. Disable the staged operation for unsupported evaluation
modes with the existing bottom-of-pane warning, rather than silently changing the split.
Implement an explicit exploration purpose in the canonical evaluator: the current
`TrainingJob._run_evaluation` can automatically refit on development rows and produce
final-test diagnostics. Interactive builds must instead retain the training-partition fit
and score its validation partition, or label training-only diagnostics as development.
Do not execute the final refit/test path and hide its output afterwards. Test partitions
remain reserved; explicit finalisation/logging owns their eventual evaluation. This is
required in milestone B, before any interactive chart is exposed.
Completing the feature then includes cross-validation: retain each required fold's full-fit
checkpoint, apply the same pruning recipe within each fold using only that fold's training
evidence, and aggregate matching held-out predictions through Haute's existing evaluation
plan. Never apply the full-data fit's keep-set to every fold or score its predictions as OOF.
Parameter exploration is model selection; do not describe repeatedly inspected validation
scores as untouched final-test evidence. No interactive stage consumes test rows.

Scope automatic tuning to the GBM stage initially. Its search space and trial objective must
refer to the full GBM, with post-fit processing disabled, rather than today's final pipeline.
Retain checkpoints for the selected candidate's required evaluation fits and final build,
not every trial. Post-fit settings remain explicit user choices. Search and config validation
must reject stage-misplaced parameters with a concrete explanation. Existing non-staged
tuning keeps its behaviour. Stage-by-stage automatic tuning is a separate future extension.

Logging/refitting records both the selected stage and its recipe. The refit replays that
recipe on its new fit; ordinary inference/export gets the chosen table snapshot, not the
training checkpoint. Keep feature contracts and native t-boost loading compatible.

## 6 Build the UI on the persisted stage contract

Extend `ModelEntries.tsx`, `ModellingToolbar.tsx`, `ModellingView.tsx`, `modelRuns.ts`,
`ModelsAveChart.tsx` and the parameter controls. Add the pruning-path view using the existing
chart scaffold and tab component. Keep the tabs content-sized and aligned left. Reuse
keyboard/focus and reduced-motion behaviour from existing disclosures.

Replace the right-hand Parameters tab with Training. Within its independently scrolling
content, give GBM, Pruning, Banding and Smoothing distinct, consistent configuration boxes
with a heading, stage-specific controls and an action area. Use compact collapsible boxes
with the existing slide animation; collapsing a box preserves its settings. Keep its running
status and Cancel reachable in the header/action area even when collapsed. No stage borrows
another stage's parameter form or uses a top-toolbar Train button. The model's left-hand
checkboxes control chart visibility only.

Disable a stage's action when its required parent is missing or out of date; do not build
the missing parent implicitly. Keep the reason at the bottom of the right pane. Reopening
Training or switching back to the running model restores its live progress and Cancel.
The request targets the model and stage of the pressed button, not whichever model happens
to be selected when the request finishes. Read-only mode permits inspection and chart
visibility changes but prevents configuration edits and starting stage jobs.

The chart identifies the OOB minimum, recommended cutoff, selected preview and applied
cutoff without making a hover commit anything. Offer keyboard selection and explicit
preview/apply controls. Show stages in chart legends/tooltips and identify missing or stale
results. A settings change cannot silently turn a previously displayed stage into another
model. Displaying a candidate should add a clear preview series until applied or discarded.

## Delivery sequence and verification

| Milestone | Work delivered | What Ralph can try |
|---|---|---|
| A Library foundation | Stage boundaries, path/decision split, checkpoint format and numerical equivalence | A small Python example builds once, reloads, then prunes to two cutoffs |
| B First complete UI slice | Training tab with separate GBM/pruning configuration boxes and buttons, no toolbar Train, persistent jobs, path chart, preview/apply, two overlays, script/native export; fixed holdout/development modes | Build, compare full/pruned, cancel a preview, restart and choose another cutoff |
| C Banding and smoothing | Their settings, immutable results, reports, selectable parents and all four overlays | Change banding/smoothing while the GBM and pruning remain reusable |
| D Evaluation and output completion | CV checkpoint lifecycle, GBM-only tuning, refit recipe replay, matching script export and selected-stage logging | Compare stages under CV and verify saved/exported predictions |

Each milestone starts with its specification changes and smallest behavioural tests, then
lands as a reviewable demo increment. No promise of elapsed time before measuring checkpoint
cost and validating the native resume boundary.

Required behavioural evidence:

- No booster calls after the full-fit stage when moving a cutoff, banding or smoothing;
  counting fits is additional evidence, not a substitute for prediction correctness.
- Existing one-call numerical results, exact table decomposition, monotone constraints,
  exposure/weights, missing and unknown categories, regression objectives and binary
  classification remain correct. Multiclass is not added to Haute by this work; the library's
  staged capability must declare its supported tasks and reject unsupported ones explicitly.
- Checkpoint save/load preserves OOB path scores, categorical fit evidence and downstream
  results. Inference export still loads through ordinary t-boost without checkpoint data.
- Cancellation, failed saves, stale requests, data changes, restart and artifact cleanup
  cannot mutate or lose a valid parent or publish an incomplete child.
- CV lines are from the correct fold predictions; shared chart bins/row provenance match;
  no test rows enter training, path selection, tuning or interactive previews.
- Frontend controls preserve model visibility independently of stage visibility, disable
  unavailable stages, retain keyboard access and introduce no canvas shift/overflow.
- Training replaces Parameters and toolbar Train is absent. Each stage box exposes only
  its own hyperparameters; its button dispatches that stage for the correct model and turns
  into Cancel while running. Collapsing boxes or switching panes/models does not lose the
  running job or its cancellation control. Unready stages cannot implicitly rebuild parents.
- Real demo browser checks at desktop and compact widths; restart and native export checks.
  Measure retained bytes, peak RSS and stage latency on representative data before calling
  the no-retraining workflow complete.

Run Haute's affected tests, typing, lint and contract-generation checks only, following
its AGENTS.md. For t-boost changes, run the applicable gates in its CONTRIBUTING.md,
including Rust/serialization determinism and binding rebuild/stub checks when touched.
Rebuild Haute's frontend and reinstall the updated local packages into the demo for each
usable increment. Update user documentation and these brainstorm assumptions as it lands.

## Scope choices still proposed

The Training pane and separate stage configuration boxes/buttons reflect Ralph's follow-up.
Replacing Parameters with Training, the exact button labels and collapsible-box presentation
are implementation proposals. Fixed-count replay for a manually chosen
point, full-plus-latest default overlays, one preview at a time and the four delivery
milestones are implementation proposals, not additional settled user decisions. General run
history, a training queue, new model families, interactive fold-vote selection and automatic
tuning of post-fit stages are outside this feature. Required checkpoint persistence and
evaluation provenance are included now rather than deferred to those broader roadmap phases.
