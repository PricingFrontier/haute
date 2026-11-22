# Handover: the Modelling UI build (21 November 2026)

> **New session, 22 November 2026:** start with [session-handover.md](session-handover.md). It summarizes the latest installed build, verification, workflow and environment. The notes below retain the chronological implementation history.

For the next agent picking up this work. Read this first, then `AGENTS.md` at the repo root (the
rules), then `modelling-brainstorm/plan.md` (the build order) and the top of
`modelling-brainstorm/overview.md` (what Ralph has decided).

## Latest fix: durable banding/smoothing result size

Job `0a7a7ffb1067` failed in `StageStore._write`: the combined current + automatic
smoothing preview diagnostics exceeded the separate 32 MiB manifest limit. The frequency
banded model was already saved (revision `2ef0c91c353743ff9758874ca1bcd701`); the failed
job was the next smoothing preview. The earlier worker IPC fix was working.

Private manifest v2 now references immutable `<revision>.result.json` sidecars with
SHA-256/size; 32 MiB manifest cap unchanged, each result capped at 256 MiB. Public stage
state stays version 1. Existing inline v1 manifests read unchanged and migrate atomically
on the next write. Checkpoints share revision results. Metadata operations verify file
identity without decoding diagnostic arrays; cancellation uses a bounded nullable job ID
in the revision envelope (empty IDs from path-point application remain valid). Result
hydration precedes commit, so read/parse failure cannot roll back a published bundle.
Cleanup respects revision leases for sidecars, orphan files and exact atomic-write temp
names. Failure removes only newly created sidecars and preserves the prior manifest.

Real validation copied the user's existing store to `/tmp/haute-stage-results-real`, ran
the smoothing path from the banded bundle without a GBM refit, published and reopened it.
It represents 54,093,887 bytes of inline results in a 1,211-byte final manifest, with both
banded current and smoothed preview intact. Evidence: `/tmp/haute-stage-results-real.log`,
`/tmp/haute-stage-results-real/final-summary.json`; no user stage files were migrated or
changed by verification.

130 affected stage-store, training-stage and docs cases verified across runs (127 passed
in the final module run; its two empty-ID failures fixed and rerun successfully alongside
two cancellation identity cases). Ruff/format and mypy (406 modules) pass. Review
**APPROVED**, thread `01a20009-4f2f-7fd3-accb-3e7f17c6c292`, logs
`/tmp/haute-stage-results-review{,-resume}.ndjson`. Before files:
`/tmp/haute-stage-results-before`; all check logs `/tmp/haute-stage-results-*.log`.
Updated Haute installed in the demo; restart Haute and retry the failed next stage.
The saved banded model does not need a GBM rebuild. The user's 507 estimate errors are
separate and were not changed by these storage fixes.

## Latest fix: large frequency training results

The frequency job `935b29dfff1b` fitted successfully but failed at completion with
`WorkerProtocolError: serialized metadata exceeds byte limit`. This was the 4 MiB
worker IPC metadata guard, not the earlier unrelated `/modelling/estimate` 507s.
Training workers now write the full response to owned `output/training-response.json`
and send its size/SHA-256 descriptor. Parent publication verifies its canonical path,
lifetime, size (256 MiB cap, also respecting configured artifact limits), digest and JSON
object before existing schema, finite-value, identity and artifact-link checks. There is
no diagnostic truncation or inline fallback. The transport file is excluded from export
handles; old saved artifact handles and job-directory ownership/cleanup remain unchanged.

The regression first reproduced the original failure, then published all 120,000 diagnostic
bins successfully. Missing/duplicate/wrong-path/lifetime/size/digest/oversized/invalid-JSON/
non-object/symlink cases fail. All 274 affected worker protocol, train service, staged runtime
and docs tests pass; touched Ruff/format and mypy (406 modules) pass. Before-state files:
`/tmp/haute-training-response-before`; logs: `/tmp/haute-training-response-*.log`.
Read-only review **APPROVED**, thread `01a1fff4-d101-73a3-a84a-c7c395d58bbc`,
log `/tmp/haute-training-response-review.ndjson`. Updated Haute installed in the demo
with `uv sync --reinstall-package haute`; installed transport source matches the repository.
The user-owned server on port 8001 was left running and needs a restart to load the fix.

Real isolated spawned-worker check passed with all 678,012 frequency rows, Poisson loss,
Exposure offset, 4,000-tree/eight-bag settings and staged full GBM (automatic early stop at
1,446). It completed in 87.68 seconds. The complete response would have occupied 6,344,640
bytes inline (over 4 MiB); JSON artifact is 11,460,098 bytes and remaining IPC metadata is
3,818 bytes. Schema, consistency and the full model/evaluation/stage artifact set validated.
Scratch script/evidence: `/tmp/haute-frequency-response-check.py`,
`/tmp/haute-frequency-response-check/summary.json`. The script mirrors the editor's removal
of legacy postfit params and target/offset from features; the first scratch attempt omitted
the latter filtering and failed the feature contract, without changing any user files.
User must retry their failed UI job after restarting Haute; the scratch fit is not published
into their project.

## Current status: compact candidate summaries implemented

Paths now includes compact read-only candidate summaries, while full Factor/Tables remain
separate. Banding shows a paired histogram of materialised interaction-table cells with
shared power-of-four bins, before/selected totals and percentage reduction. Main effects
are excluded; factored effects without a cell grid are counted separately. It describes the
reference fit. Smoothing shows saved average predictions by fitted factor, largest changes
first, six per page. Before/selected share scales; categorical and Missing bins are points.
These are labelled grouped averages, not PDPs. Close values retain adaptive precision.
Loading/error hides old summaries, and selecting another point resets pagination. No new
fitting, scoring or extra fetch is added by these summaries.

Real browser validation exposed an existing t-boost path-complexity bug: native tensors
serialize as `{shape, data}`, where `data` is a Dense/Sparse variant. Counting dict fields
reported 8 cells for a 2,020-cell grid. `_paths._complexity` now uses the product of tensor
dimensions; the regression independently compares every candidate against public table
exports. This also corrects future banding simplicity rankings. Existing immutable path
artifacts are untouched: rebuild older paths to refresh their stored suggestions. Removed
the redundant old point-level cell label; the compact histogram owns displayed counts.

Checks: 52 affected UI tests across runs (latest summary module 13 passed), 24 library
postfit tests, 26 runtime tests plus 3 affected path cases rerun after the final count fix,
and all 74 docs checks including the new module-map entry. TypeScript, touched ESLint, library
_paths typing, both production builds and bundle/dependency checks pass. Total gzip
1,852.7 KiB, initial 299.9 KiB; existing budgets unchanged. Real-browser checks at1440/1024
covered saved smoothing averages, actual banding histograms, preserved applied revisions,
separate Tables, no JS errors and zero selection-triggered training requests. Scratch-only
banding preparation was explicitly run to inspect the histogram, then discarded.

Read-only review **APPROVED**, thread `01a1ffdc-ff6f-7b11-87cf-52a10177b4a4`;
logs `/tmp/haute-path-summaries-review{,-resume,-count}.ndjson`. Review caught rounding
ambiguity and strengthened plotted-value/switching assertions; all findings resolved.
Evidence/screenshots/check logs: `/tmp/haute-path-summaries-*`. Before files:
`/tmp/haute-path-summaries-before` (Haute paths, with nested `t-boost` originals).
Demo package rebuilt/reinstalled. Owned server8011 stopped and scratch config restored;
user8002 untouched. Restart the demo server and refresh the browser. No commit/push.

## Previous refinement: paths chart and performance


Banding now plots **budget (% of configured maximum)** on x, so candidates with the same
number of tables/cells no longer form a vertical line. All path charts show percentage
objective-loss change relative to the unchanged parent, with zero and the exact practical
selection ceiling visible. Near-zero loss uses labelled absolute change. The UI explains
that this is a chosen trade-off, not statistical significance. Actual table/cell counts are
supporting details. Factor and Tables are separate canvas panes again; selected candidates
still appear there, and returning to Paths preserves selection.

Path evaluation now reuses each retained fit's partition parquet within the owned bundle
(up to 256 MiB retained disk per worker), preserves class labels, and removes all shared
files on completion/failure/cancel. Candidate JSON encoding avoids recursive diagnostic
copies. Categorical AvE uses compact Arrow dictionary codes and NumPy reductions with
variable-length labels; ordinary and staged evaluation still use the same implementation.
No change to native t-boost in this refinement.

A three-run warm benchmark of the scratch demo's saved 40-tree, three-bag GBM prepared the
same seven banding candidates in median **10.83s before → 5.09s after (~53% faster)**.
Candidate scores are identical. A separate artifact comparison verified identical metrics,
used/unused feature AvE, tables, SHAP summaries and PDP across all seven points. This is one
local case, not a general performance guarantee. The principal cost was repeated partitions
and chart aggregation, rather than the native banding path itself.

Verification: affected backend/docs run passed 143 tests initially, with one cache-disabled
write-count assertion corrected to account for existing identical-outcome deduplication.
The affected rerun passed 30 tests; a new memory regression passed after first reproducing
32.98 MB allocation with one long category, versus <0.3 MB with the fix. All 46 affected UI
tests passed (7 path tests rerun after locale lint fix). Python typing (406 modules), TS,
touched lint/format and bundle/dependency checks pass. Both production entries built;
aggregate 1,850.5 KiB gzip, initial 299.9 KiB, existing caps unchanged. The demo package is
reinstalled. Real-browser checks at 1440/1024 verified distinct budget positions, labelled
loss/tolerance, separate panes, candidate selection preserved, no page overflow or JS errors.

Read-only review **APPROVED**, thread `01a1ffc5-4d6d-7440-9479-b596e609cd93`.
It caught the long-label allocation issue and independently verified the fix/regression.
Evidence: `/tmp/haute-paths-refinement-*` (bench-{before,final}.json, review-resume.ndjson,
browser-results.json, screenshots, check logs). Task-start snapshots are
`/tmp/haute-paths-refinement-before/{haute,t-boost}`. Only the owned scratch server8011 was
used and stopped; scratch configuration restored. User server8002 was left running and
needs a restart for Python changes, followed by browser refresh. No commit/push/publication.

## Previous implementation: immutable modelling paths


The full model-wide Pruning/Banding/Smoothing path workflow is implemented across t-boost
and Haute. The canvas now has a shared **Paths** view with linked Factor/Tables inspection.
Clicking a point is read-only; Apply publishes its exact evaluated snapshots and adopts only
that phase's settings. The previous stage prepares the next enabled path inside its existing
Running/Cancel lifecycle. Parameters and Training remain separate. Identity disables the
phase and keeps its predecessor; rejected/no-op points are explicit.

Banding shares native setup across bounded noise/cost budgets. Smoothing shares Auto GCV
proposals and spectral systems, preserves zero proposals, and records proposed/requested/
applied values even after rejection. Manual alpha stays separate. Selection uses matched
validation/OOF loss, never final-test rows. Pruning's native OOB chart remains separately
labelled. Identical native outcomes across **all** retained fits share one evaluation while
retaining each logical policy for Apply/refit. Compact deltas retain parent evidence once.
Finalisation replays policies on development data rather than transferring table IDs.

Targeted checks: the earlier affected runs passed 89 backend tests, 68 library staged/
checkpoint tests and 287 UI tests across runs. After review fixes, the affected runtime/store/
service modules passed 60 tests, library post-fit/checkpoint modules passed 37, UI path/hook
regressions passed 17, and all 74 docs-accuracy tests passed. Python typing, TypeScript,
touched lint/format, generated contracts, five native banding tests and native clippy pass.
The read-only Codex review is **APPROVED** (thread
`01a1ff47-415b-79f3-b1a8-3a71a7da8fc5`; logs `/tmp/haute-paths-review*.ndjson`). It caught
and verified fixes for cross-fold pruning floors, concurrent cleanup during Apply, path-tab
selection after automatic continuation, Auto multiplier adoption, malformed keep sets and
pruning no-op reporting.

The measured production bundle is 1,850.3 KiB gzip; the aggregate allowance is 1,853 KiB.
Initial load remains 299.9 KiB under the existing 300 KiB cap; vendor/single-chunk caps stay
unchanged. Both browser entry points were built and the demo package reinstalled. No commit,
push or publication. Preserve pre-existing dirty changes. Task-start snapshots for review
are `/tmp/haute-paths-before/{haute,t-boost}`.

A 10k-row, 80-tree, three-bag local probe prepared seven banding points in ~0.53s and six
smoothing points in ~0.056s, plus ~0.015s/~0.009s serialization. Candidate payloads totalled
~285 KB/~148 KB versus a ~610 KB parent checkpoint; process peak RSS was ~200 MiB. This is
one diagnostic case, not a performance guarantee. Every candidate matched independent
operation predictions exactly (`/tmp/tboost-paths-benchmark.json`).

The final post-review browser chain passed GBM → pruning → banding → smoothing, exact
non-default Apply, automatic tab continuation without another click, linked Factor/Tables,
history after reload, and 1440/1024 layout without page overflow or browser exceptions.
Additional browser checks passed identity Apply (phase disabled, exact parent preserved),
stale inspection returning 409, restoring the completed model on its applied Smoothing path,
and cancellation through the completed predecessor stopping
the real automatic child while preserving the new full model. Evidence/scripts/screenshots
are `/tmp/haute-paths-*` and `/tmp/haute-seamless-cancel-*`. The dedicated scratch server on
8011 is stopped and its config restored after testing; **the user-owned server on 8002 was
left alone**.
The real demo needs a server restart and browser reload to load the installed update.

Standalone main-effect banding and independent feature-by-feature optimisation remain
separate future work, as agreed. The paths here are model-wide policies.

## Earlier status: staged t-boost complete

Ralph explicitly asked to continue until the entire staged plan was complete. Milestones
A–D in `staged-tboost.md` are now implemented, reviewed and verified in the rebuilt local
demo. The Training pane has separate GBM, Pruning, Banding and Smoothing controls, inline
Cancel, and explicit finalization/export. Persistent checkpoints support restart, preview/
apply, all four prediction overlays, CV/OOF, GBM-only tuning and recipe replay for refit,
script export and logging. The bottom of `staged-tboost.md` retains the original scope;
its completion section records current tests and measured costs.

Browser acceptance used `/tmp/haute-staged-browser-project`, an independent demo copy:
all stages, native export, server restart, a new pruning point without a new build, and
cancelled rebuild preserving the applied model passed. No browser errors or compact page
overflow. Screenshots and scripts are `/tmp/haute-staged-*`; the 250k-row benchmark is
`/tmp/tboost-staged-large-benchmark.json`. No commits, pushes or publication. Existing dirty
changes are preserved. Restart the original demo server and reload to load the new package.

The chronological notes below describe earlier increments; statements that staged work is
pending are historical and superseded by this status and `staged-tboost.md`.

## How Ralph works

- **One phase at a time.** Agree a phase's scope in a few lines, build it, Ralph tries it in the
  demo and gives feedback, then plan the next one. He reorders phases when something he expects
  is missing; offer a scoped first cut rather than defending the plan's order.
- **Fast iteration.** For each request: a small change, a targeted test, the touched modules'
  tests plus tsc and lint, a spec and doc update of a line or two, a rebuild into the demo, and a
  short reply saying what to try. Don't run the full suites (CI does that).
- **Nothing is committed** unless Ralph says so. Everything is uncommitted on the haute branch
  `modelling-library-moves`, on top of a large earlier change that is also uncommitted. Don't
  commit, push or open a PR unprompted.
- **Specs before code** (AGENTS.md): every behaviour change goes into
  `specs/frontend-modelling-optimiser-ui/{high,low}-level.md` and, for the backend,
  `specs/modelling/{high,low}-level.md`. User-facing instructions go in
  `docs/building-models/modelling.md`, never as on-screen helper text (`CLAUDE.md`, "UI text").
- **The brainstorm's rules.** `overview.md` has Settled (Ralph's decisions in his words; never
  reword or contradict them), Open (Q1 to Q6), Concerns (lettered A to H) and Proposed (numbered
  1 to 61; strike, never renumber). When Ralph decides something, add it to Settled and update
  every file that leaned on the old line. Don't decide a concern or question yourself.
- Keep replies short and plain: what changed, what to try, and the caveats that matter.

## Repos and the build loop

- **haute:** `/home/ralph/suite/haute`, the code.
- **The demo:** `/home/ralph/suite/haute-modelling-demo`. It installs haute from `../haute`
  *non-editable*, so a change reaches the demo only after this loop:
  1. Frontend: `cd frontend`, then run both builds with the Linux node:
     `NODE=~/.vscode-server/bin/2242ebbb54efeeb0129e08e919e7e8d43033cd83/node`,
     `$NODE node_modules/vite/bin/vite.js build && $NODE node_modules/vite/bin/vite.js build --config vite.app.config.ts`.
     The `npm`/`npx` on PATH are Windows binaries and fail on this WSL path; run tools directly
     with `$NODE node_modules/<tool>/...`.
  2. Vite empties `src/haute/static/`, including `haute-build-inputs.json`, so rewrite it from
     the haute root:
     `uv run --with hatchling python -c "import sys; sys.path.insert(0,'.'); from pathlib import Path; from hatch_build import FrontendBuildHook; FrontendBuildHook._write_input_manifest(Path('frontend').resolve(), Path('src/haute/static/index.html').resolve())"`.
     If frontend source changes after the build, the wheel build refuses ("Build failures
     usually indicate..."). Rebuild, then reinstall.
  3. In the demo: `uv sync --reinstall-package haute`. Ralph then restarts `haute serve` and
     reloads the browser.
- **Checks:**
  - Frontend tests: `$NODE node_modules/vitest/vitest.mjs run <files>`.
  - Types: `$NODE node_modules/typescript/bin/tsc -b`.
  - Lint: `$NODE node_modules/eslint/bin/eslint.js <files>`.
  - Backend: `uv run pytest <files> -q -n 4`, `uv run ruff check`, `uv run ruff format --check`
    and `uv run mypy src/haute/`.
- **Contracts:** after changing `schemas.py` or `_node_config_models.py`, run
  `uv run python scripts/generate_api_contracts.py`, then `$NODE scripts/generate-api-contracts.mjs`
  in `frontend`. After changing config keys, run `uv run python scripts/generate_node_config_reference.py`.
  The shared JSON fixtures in `tests/fixtures/ui_contracts/` need a new required response field
  added by hand.
- **Real checks against the demo data:** start your own server on another port, never Ralph's:
  `HAUTE_LOCAL_SESSION_TOKEN=<token> .venv/bin/haute serve --port 8011 --no-browser`. Send the
  cookie `haute_session=<token>`, and build the graph with
  `haute.parser.parse_pipeline_file('rating/main.py').model_dump(mode='json')`.
  - Earlier scripts are in the Claude scratchpad, which may have been cleared:
    `/tmp/claude-1000/-home-ralph-suite-haute-modelling-demo/6ac210b2-d18c-41b5-afde-3f1097c65fdd/scratchpad/routecheck/`.
  - Stop your server by its pid from `ss -ltnp | grep :8011`. **Don't use `pkill -f` with the
    command line**: it matches and kills its own shell.
  - Ralph's own server moves port; it was on 8002 at handover. Leave it alone.

## What is built

The phases in `plan.md` marked built: Frame; Every factor; Several models, first cut; Layout and
comparison. In short:

- **Config shape:** an interactive build's node config (`interactive_build: true`) holds the
  shared settings at the top level and its models under `models`. Each model has `id`, `name`
  and `algorithm`, plus `feature_columns`, `params`, `tuning`, `monotone_constraints` and
  `feature_weights`. The per-model keys are `INTERACTIVE_MODEL_KEYS` in
  `src/haute/modelling/_train_config.py`, mirrored in `frontend/src/modelling/interactiveModels.ts`;
  a parity test in `tests/test_train_config_builder.py` keeps them equal.
  - `interactive_model_config(config, model_id)` gives the flat config one model trains from.
    It deliberately does not carry the model's name: the name was becoming the trained model's
    file name (`output_dir / f"{name}{ext}"`).
  - `TrainRequest` and `TrainEstimateRequest` take `model`. `_resolve_interactive_model` in
    `routes/_training_preparation.py` swaps the node's config for the flat one at the
    `train_model` and `estimate_training` entry points.
  - Load and save check each model's flat config (`_check_modelling` and `_modelling_gaps` in
    `_node_config_models.py`). A flat interactive build with no `models` passes validation, but
    training refuses it and the view shows "has no models".
  - Job records keep `model_id`, and the older run's artifacts are released only for the same
    node, pipeline and model.
- **Left-out factor charts:** for an interactive build, `chart_left_out` keeps every column in
  the training parquet, and `TrainingJob._ave_left_out` bins each column the model left out
  (one that holds no role) into `ave_left_out`. A column of a type it can't bin is recorded in
  `diagnostics_errors` as `ave_left_out`, the message led by the column's name.
- **The view (`frontend/src/modelling/`):**
  - **`ModellingView.tsx`:** the factor list, the model entries (`ModelEntries.tsx`: a colour
    dot, the name, a tick box for the charts, a bar while training, and a Duplicate / Rename /
    Remove menu, plus Add), a notice line, and the panes Factor, Learning curve and Tables.
  - **`ModelsAveChart.tsx`:** draws every ticked model over the actual. Bins must match on label
    and exposure.
  - **Feature list:** one condensed list in the right-hand Features pane; a name charts the factor and expands
    its Include and direction controls for the chosen model.
  - **Right-hand column:** the chosen model's Features and Parameters tabs, scrolling beneath their heading. Model setup (the Target
    pane) and Training split (the Split pane) show there from the toolbar. It is hidden, not
    unmounted, behind the Git, Files and Assistant panels, so the toolbar's Train keeps working.
- **`ModellingToolbar.tsx`:** Model Setup over Training Split, and one Train button that
  becomes Cancel while the chosen model trains, in `ToolbarColumn`s like the pipeline toolbar's. Train is `aria-disabled` with the reason as its
  title and as a toast on click; it waits while any training runs, because the server trains one
  job at a time and a refused start overwrites that model's cached run. Undo, Redo, the source
  and Pipeline are gone (undo stays on the keyboard).
- **`ModellingConfig.tsx`:** the one editor per model (keyed by model id). `modelId` keys its run
  as `${nodeResultKey}#model:${id}` and names the model in requests. It publishes a
  `TrainAction` through `onTrainAction` into `useModellingViewStore`, which the toolbar reads.
- **View state:** `stores/useModellingViewStore.ts` holds the open node, the chosen model,
  `hiddenModels`, `panel`, `pane`, `factor` and `trainAction`.
- **The demo's model:** its `rating/config/model_training/modelling.json` was converted to the
  `models` shape. It is tracked in git there and uncommitted.

## What to tell Ralph or watch for

- **Undo clears cached columns:** browser checks of the combined feature list found that
  Ctrl+Z can show "Columns not known yet". Refresh reloads them; the fitting edit is correctly
  undone. The combined list was checked at 1440×900 and 1024×768 with no page overflow.

- **Not seen in a browser:** the toolbar pairs, the multi-line chart, the entries and the rename
  dialog. Ralph is checking.
- **Bundle budget:** total JS is 1,817 KiB gzip against a 1,813 KiB budget in
  `frontend/scripts/check-bundle-size.mjs`, and the initial load is 299.7 of 300 KiB. CI fails
  until the budget is raised, which is Ralph's call. Earlier commits raised it in this situation.
- **Lost with the old Train pane:** the RAM estimate, the row-limit warning and detailed failure
  output. Nothing marks a run as out of date.
- **Tuning** now shows in Parameters, because Training Split is reachable. Its trial progress has
  no home.
- **With validation rows and no test set,** the charts use the development rows (Haute's
  refit). The "Same rows" phase is meant to fix this.
- **Runs are lost on restart** (the "Runs that last" phase). A restored run can read as stale
  after another model was edited.
- **Colours** are by a model's position, so removing a model shifts the colours after it.
- **A model trains on the source last chosen in Pricing,** which this toolbar no longer shows.
- **Ralph hasn't accepted these** (an agent added them): proposals 59 to 61 (what Add and
  Duplicate copy) and concerns G and H.

## Next

Ask Ralph what he thought of the latest round before planning. The plan's next phases are Runs
that last (a run store keyed by node, model and run; reference models), Same rows, then the rest
of Several models (a second model type, a queue, a shape per model type). Ralph's own memory
notes for Claude are in
`~/.claude/projects/-home-ralph-suite-haute-modelling-demo/memory/`; this file covers the same
ground.

Latest layout decision (20 Nov): Main Menu in the top toolbar replaces the back arrow;
models and Add are in the left palette; the complete animated feature list is in Features
on the right, beside the Parameters tab. This supersedes earlier locations in the sketches.

The left sidebar now matches Pricing's shared palette chrome, uppercase Models header and
collapse/reveal controls. It shares `useUIStore.paletteOpen` with Pricing and Workbench,
keeps the compact view switcher when collapsed, and preserves the active model and feature.

Readiness warnings no longer render above the chart panes. Train is natively disabled
with a reason tooltip; stacked editor warnings follow the controls, and training failures
show at the settings column's foot, keeping the chart tabs fixed.

The left model names now expand like features: one at a time, with the same animated
disclosure CSS (`disclosures.css`, shared with `FactorList`). Model selection and chart
visibility survive collapse. Show on charts and direct Duplicate/Rename/Remove actions
replace the inline checkbox/ellipsis menu; progress remains visible while collapsed.

The node-name subtitle on the left and model-name heading above Features/Parameters are
removed. Models, Model setup and Training split keep their headings.

The chart and settings tab strips now share the default `PreviewPanelTabs` appearance and
`equalWidth`, matching their height, spacing, uppercase 11px labels and selected styling.

Tab alignment correction: both strips keep their matching compact uppercase styling,
but omit `equalWidth`, grouping content-sized tabs at the left rather than filling the bar.

The chart checkbox is back beside each model name, always accessible without expanding or
selecting the model. Only Duplicate/Rename/Remove remain inside the disclosure.

Model actions now stack vertically. Remove uses the shared confirmation dialog with the
model name; Cancel/dismiss preserves it, and only confirmation applies removal.

+ Add now opens `AddModelPicker`, a keyboard-accessible menu offering t-boost only. Choosing
it adds the model using the existing copy-settings behaviour; dismissal adds nothing.

Model actions are now toggled by the Workbench schema’s `IconButton`/`SlidersHorizontal`,
immediately left of the chart checkbox. Names only select models; opening actions does
not switch models. The stacked actions, animation and removal confirmation remain.

Features now match model rows: name, schema sliders icon, inclusion checkbox. Names chart
the factor and close options; the icon toggles direction/missing-feature controls without
changing the chart. Inclusion stays visible and only edits the selected model.

Staged t-boost planning (20 Nov): Ralph asked for a build plan after discussing full/pruned/
banded/smoothed prediction checkboxes and separate stage parameters, with the pruning path
available before choosing a cutoff. See `staged-tboost.md`; no stage implementation has been
made. A key finding is that native model serialization omits bag membership/intercepts and
training categorical bins: resumable stages need a separate checkpoint format, not just saved
final models. The first proposed demo slice is full GBM + pruning path + pruning previews and
overlays with persistence. The existing workflow/analytics descriptions of today's one-call
fit remain descriptions of current behaviour, not constraints on the new library design.

Follow-up to that plan: Ralph wants a right-hand Training pane instead of the top Train
button, with separate hyperparameter boxes and training buttons for each stage. The plan
now proposes Features/Training tabs, replacing Parameters, and GBM/Pruning/Banding/Smoothing
boxes with their own actions and inline Cancel. This remains planning work; the live UI
has not been changed to this layout yet.

Implementation has now started in t-boost (first part of milestone A): a public
`fit_staged` session builds the full fit and pruning path, then creates independent pruned
models without boosting or rescoring the path. Full and pruned snapshots retain ordinary
inference export; manual unscored cutoffs are labelled honestly in HTML reports. The new
library spec is `../t-boost/specs/roadmap/staged-training.md`. This increment is in-memory:
checkpoint persistence and the Haute Training pane are still pending. Do not wire these
sessions through existing isolated workers as if their state survived worker exit.
Verification: full t-boost Python suite 823 passed before final review fixes; final affected
modules 148 passed (37 new staged cases), strict mypy and both documentation gates passed.
Review approved. No native code changed, no commits or releases, and no demo UI rebuild was
needed. Next is the native training-checkpoint format and its resume/ownership tests before
integrating the staged jobs and Training pane into Haute.


## Staged implementation completion (20 November 2026)

The full plan is complete; see its completion section for the architecture, test evidence
and measured costs. Final review approved both the checkpoint boundary and Haute integration.
The important late fixes preserve applied recipes during failed rebuilds, merge simultaneous
model restorations against live canonical state, freeze applied path settings, compare only
compatible evaluation identities, refresh expired final export handles, reject symlinked
store ancestors, and normalize optional fit evidence in persisted stage responses.
No commit, push or publication has been made. Keep the existing dirty changes intact.

21 November follow-up: Parameters is again a separate right-hand tab beside Features and
Training. The four parameter disclosures contain settings only; Training contains the stage
actions and visible Apply pruning / Discard preview decisions. The editor stays mounted
across these tabs to preserve numeric drafts, validity and cancellation. Prune by training
the GBM, previewing an automatic or chart-selected cutoff, then applying that preview.

21 November phase-selection follow-up: Ralph declined the common-fields/Advanced JSON
proposal; retain the JSON GBM editor. Parameters now has enable checkboxes for pruning,
banding and smoothing instead of parent dropdowns. Parent routing follows the nearest
earlier enabled phase; GBM is the required base. Settings and applied results survive toggles.
A stale parent chain must be rebuilt before a downstream action can reuse it.

## Workflow decision support complete (21 November 2026)

Ralph approved the other usability ideas, excluding set 4 (friendly parameter forms).
JSON Parameters and the enable/skip phase routing are retained. Training now shows stage
states/prerequisites, explicit first-fit development-only acknowledgement and setup/split
links. Factor plots identify Development/Validation/OOF evidence. Every post-fit phase
publishes a candidate; previews show compatible parent metrics, direction/difference,
reported tables/cells and changed factors. Compare parent/full temporarily isolates the
actual chart lines; End comparison restores previous model/stage visibility.

Named checkpoints retain up to ten applied models and their per-model settings, with
artifact leases/checksums, current-revision checks and restart persistence. Restore rejects
changed upstream identity or incompatible shared setup/split settings. It never changes
shared configuration. It resets invalid local JSON/numeric drafts to saved settings as well
as adopting the saved actual recipe. Delete is confirmed and preserves the applied model.
Final model explicitly names the applied and exported recipes and separates final-test
metrics. Old manifests without named checkpoints remain readable. Final-test-use ledgers
and advanced statistical/stability analytics remain later proposals.

Verification: 252 affected frontend tests passed before review fixes; the updated
ModellingConfig and stageResults modules passed 202 tests, including fixed/tuning JSON
restore regressions. A rendered chart regression verifies exact SVG series isolation,
restoration and comparison exit on revision change. Sixteen store tests and five stage
service/route tests passed; real band/smooth preview/apply runs forbid booster fitting.
Checkpoint API tests restore model params, reject changed shared evaluation, and verify
metadata-only checkpoint lists and deletion. Ruff, mypy (404 files), tsc, touched ESLint,
generated-contract checks and docs_accuracy (74 tests) pass. Both production builds pass;
~1,841 KiB total gzip and 299.8 KiB initial; aggregate cap 1,843, other caps unchanged.

Review APPROVED: `/tmp/haute-workflow-review-resume.ndjson`, thread
`01a1fd13-06e5-76f0-aa1f-7bd5758b06c7`. Browser workflow in the independent scratch project
passed full GBM → band preview/discard/retry/apply → smooth preview/compare/apply → finalise,
checkpoint save/restore/delete and no page overflow at 1024×768. The first script's response
listener threw after all assertions passed during shutdown; the focused final browser
script exits cleanly and verifies invalid JSON/numeric draft recovery, reload persistence
and delete preserving the current result. Evidence: `/tmp/haute-workflow-browser.log`,
`/tmp/haute-workflow-restore-browser.log`, `/tmp/haute-workflow-band-preview.png`.

Rebuilt/reinstalled Haute in `/home/ralph/suite/haute-modelling-demo`; leave its user-owned
port-8002 process alone. Restart that server and refresh before using the new backend APIs.
The agent-owned port-8011 scratch server is stopped after verification. No commits or pushes.

GBM readiness follow-up (21 November): Ralph reported grey Train GBM plus Changes pending.
Saved demo evidence was already development-only. The gate incorrectly requested the
first-fit acknowledgement again whenever that saved fit became stale. TrainingPane now
retains the development choice from saved evidence; the GBM says Rebuild needed and may
train when otherwise valid. New models and validation→development transitions show Choose
evaluation plus an explicit checkbox/split instruction. Descendants stay blocked until
rebuild. Two red/green regressions; 12 TrainingPane tests, tsc, touched ESLint,
docs_accuracy (74), both builds and bundle check pass. Independent review APPROVED in
`/tmp/haute-gbm-readiness-review.ndjson`. A browser replay of actual saved development
metadata confirms Train GBM enabled with stale config, no repeated checkbox, post-fit
buttons blocked and zero training requests. Screenshot `/tmp/haute-gbm-readiness.png`.
Rebuilt/reinstalled demo; refresh browser to use this frontend-only fix. Scratch port8011
server stopped and scratch config restored; the user's project settings were not edited.

## Automatic next-stage previews (21 November 2026)

Ralph requested removing the obligatory Preview-then-Apply sequence after every parent.
Interactive Train GBM now opts into preparing the next enabled phase automatically;
Apply pruning prepares banding, and Apply banding prepares smoothing. Disabled phases
are skipped; applying the last enabled phase stops. Nothing auto-applies. A ready candidate
has Apply as its primary action. Changed candidate settings require Refresh preview;
Preview remains available for retries and previously saved builds. Parameters stays separate.

Continuation runs server-side through the existing supervised training service, after
full-fit admission/resource cleanup. An atomic manifest queue binds it to the applied
revision and is consumed by stage begin. Frozen full-fit settings, revision checks, dead
owner recovery and independent start-failure messages protect the successful parent.
Cancellation/failure of the next preview preserves that parent. Reads, discard and restore
never initiate work. Existing API callers opt out by default. Intermediate queued→running
poll responses now reach the UI so the automatic job exposes Cancel before it completes.

Verification: 104 store/stage/docs tests passed; the real service chain proves one GBM fit
and no downstream refits. Cleanup error aggregation regression passed. Affected frontend
modules passed 222 tests, with additional Apply/Refresh and queued-transition regressions
passing afterward (latest hook module: 9 tests). Ruff/format, mypy (404 files), tsc, touched
ESLint, generated contracts, both production builds and bundle gate pass. Bundle 1841.9 KiB
gzip; initial 299.8 KiB, existing caps unchanged. Read-only review APPROVED after fixing
the polling finding: `/tmp/haute-auto-preview-review-resume.ndjson`, thread
`01a1feee-1a4b-76e1-9e27-e64d45437ddf`.

Real browser verification in the independent scratch project passed full GBM → automatic
pruning preview → Apply → automatic banding preview → Apply → automatic smoothing preview
→ Apply, with exactly one browser training request, current/candidate checks at each step,
no JS errors and no outer overflow at 1440×900 or 1024×768. Evidence:
`/tmp/haute-auto-preview-browser.log`, `/tmp/haute-auto-preview-browser-results.json`,
`/tmp/haute-auto-preview-pruning.png`. Rebuilt/reinstalled the demo package; backend changes
require restarting the user's server and refreshing. Leave user-owned port 8002 alone.

Additional browser checks passed skipping pruning and banding (GBM directly prepares
smoothing) and disabling all post-fit phases (GBM completes with no preview or queue).
Evidence: `/tmp/haute-auto-preview-skipped.log`. Agent-owned port 8011 stopped and scratch
config restored. User project settings/server were left alone. No commits or pushes.

## Seamless preparation and training availability (21 November 2026)

Ralph clarified that automatic preparation should be invisible within the preceding run,
and ready previews must not grey out Train GBM or valid earlier-stage actions. Training now
keeps Running/Cancel on the applied parent through queued/running automatic work, including
when durable completion arrives before the background poller retires its job. Removed the
preparation captions and intermediate success toasts. Manual preview jobs retain their own
card. The durable job's server-owned `automatic` marker defaults false for old manifests.

Ready candidates no longer block GBM rebuilds, other stages with valid available parents,
or finalising the explicitly named applied output. Genuine configuration, stale-parent,
missing-path, disabled-phase, concurrent-work and evaluation-choice guards remain, with
reasons below all Training controls. Invalid local phase drafts affect only that phase;
invalid enabled pruning-path drafts also affect GBM, which computes the path. Parameter and
Training panes stay separate; candidates still require explicit Apply.

Cancel handles the invisible handoff on the server as well: cancelling a completed parent
atomically clears its queued followup or cancels its running automatic child, matching the
current applied response job ID and revision. Manual or replaced-parent work is untouched,
recursive cancellation of a later explicit Apply is prevented, and applied success remains.
The frontend also targets a durable job before the background store adopts it.

Verification: TrainingPane 18 tests, ModellingConfig 201, useStagedTraining 9 and shared
polling 6 passed. Backend store/stage modules 35 tests and docs_accuracy 74 passed. Added
red/green regressions for candidate-blocked training, phase-local drafts, bottom messages,
terminal handoff card identity and queued/running parent cancellation. Ruff/format, mypy
(404 files), tsc, touched ESLint, generated-contract checks, both builds and bundle gates
pass (1842.4 KiB total gzip, 299.9 KiB initial, unchanged caps).

Read-only review APPROVED in `/tmp/haute-seamless-review-resume.ndjson`, thread
`01a1fefe-7a89-7081-b579-017e5ef60e43`. Real browser verification retrained the GBM while a
pruning candidate existed, exercised the full automatic chain and checked earlier stage
buttons with a later candidate; no preparation captions, premature success toasts, JS errors
or outer overflow. `/tmp/haute-seamless-browser.log`, `/tmp/haute-seamless-pruning.png`.
A second real-worker browser check deliberately held the UI on the completed parent, clicked
its Cancel, and verified the running automatic child was cancelled while the applied parent
survived: `/tmp/haute-seamless-cancel-browser.log`, `/tmp/haute-seamless-cancel.png`.

Final build reinstalled in the demo environment. Restart the user's server and refresh for
backend/UI changes; its port-8002 process was left alone. Agent-owned scratch port 8011 is
stopped and scratch configuration restored after checks. No commits or pushes.

## Frequency workflow performance fixes (21 November 2026)

Implemented the frequency audit findings and reinstalled the verified Haute wheel in
`haute-modelling-demo` without changing its other dependencies. Its user-owned port 8001
server was left running: restart it and refresh the browser. t-boost is editable from
`../t-boost` and includes the new independent session `fork()` and binary inference clone.

- Worker-owned session cache reuses validated parents, with independent candidate recipes,
  replacement invalidation, conservative 512 MiB retention budget and cleanup.
- Apply retains the parent checkpoint plus exact candidate delta, avoiding restoration and
  recompression. Subsequent phases and restart replay the same selected model.
- Path candidates retain canonical metrics/AvE/tables while deferring optional SHAP/PDP to
  final evaluation. Immutable compact stage views retain shape/count summaries; dense
  table reports load on demand with document/revision fences.
- Post-fit changes no longer repeat the evaluation estimate. Preview admission uses bounded
  target/key projection work rather than reserving the full training budget. Preview
  progress returns 204 for settled/unknown requests. Applied selections say Applied;
  Keep unchanged remains actionable until its skip is committed.
- Reloaded legacy flat settings use the same migrated request draft as stage training,
  without mutating the source merely on read. Actual GBM edits still invalidate builds.
  Legacy full/compact comparisons use the same immutable snapshot summary or explicitly
  report unavailable shapes; projected ancestors identify their own stage.

Reusable runner and benchmarks: `scripts/modelling_audit/README.md`. It copies a disposable
project, runs a real browser workflow, records timing/response sizes/console findings,
checks factor and dense-table views, and supports optional worker/request cProfile capture.
No hard-coded project/store/job identities remain. For this dataset use 44 retained tables,
0.75 banding tolerance and smoothing strength 4. The original saved recipe's 41-table point
is not on this pruning path; the harness fails rather than silently choosing another point.

Same-data profiled worker timings (seconds; profiling adds overhead):

| Work | Before | After |
| --- | ---: | ---: |
| Full fit | 93.2 | 95.8 |
| Pruning preparation | 190.0 | 125.1 |
| Banding preparation | 118.1 | 79.5 |
| Smoothing preparation | 85.7 | 49.4 |

Browser Apply fell from about 17 seconds to 3.8–4.7 seconds. Routine stage reads now carry
0.39–1.28 MB, rather than up to 29.6 MB. Selected path responses are 0.24–0.73 MB; a full
13.6 MB table report is requested only when Tables opens. Isolated compact stage reads
measured 20–31 ms versus the earlier seconds-long reads. Full-fit cost is essentially
unchanged; native post-fit calculation and canonical evaluation remain real work.

A separate fresh unprofiled replay completed in 380.765 seconds, including browser startup,
all stages, selection, Apply, factor view and Tables. It had no browser errors or warnings;
server logs had no failed requests. Gini 0.37732274051680625 and Poisson deviance
0.29657381442459047 exactly match the prior replay. Ten browser long tasks totalled
891 ms (maximum 116 ms), compared with the audit's 36 / 3.7 seconds / 171 ms; these runs
include different idle gaps and are diagnostic evidence, not a controlled browser benchmark.

Evidence is in `/tmp/haute-frequency-optimized-audit/audit-summary.json`, with fresh-run
results in `/tmp/haute-frequency-final-audit/workflow-interactions.json`. Both scratch
servers were stopped. Original audit data remains in `/tmp/haute-frequency-audit`.
Affected backend/frontend and 76 t-boost tests pass; tsc, mypy (413 modules including the
retained Python scripts), Ruff, contracts, production builds and bundle gates pass.
Existing unrelated frontend lint warnings and test-library deprecation/float32 warnings
remain. The compact contracts add about 4.4 KiB to the total JS; its budget is 1,858 KiB,
with initial 300 KiB and vendor caps unchanged. Review approved in
`/tmp/haute-frequency-audit/review-final.txt`; all review findings have regressions.

## Continuous progress and bag learning curves — 2026-11-21

Implemented one monotonic approximate progress bar across the requested fit and
automatic next-path preparation. The hook preserves the bar while job completion
awaits stage confirmation; queued preparation and restored automatic jobs occupy
the second half. New explicit runs reset it; preparation failures show failure.

New t-boost fits retain individual bag round/train/eval loss columns. The learning
curve has Training and Validation views, stable bag colours, visibility checkboxes
and a completed mean. Individual curves end at each bag's last round; the mean
keeps stopped bags' final observed losses. Validation here is each bag's own
held-back sample, and the mean is a diagnostic average, not ensemble loss. Live
curves use each bag's own x coordinates and omit the unaligned live aggregate.
Older saved results without bag columns continue to show aggregate curves.

Live histories are independently sampled per bag and packed into bounded rows;
compact nested stage histories use the shared completed-history sampler. This
retains early/late points without a row-budget failure for many bags. The full
artifacts retain original histories. The displayed thinning label now derives
from the selected stage.

Validation: affected backend/frontend tests, mypy, tsc, touched ESLint/Ruff,
docs accuracy (74 tests), production builder/app builds and bundle budgets pass.
Read-only review approved after fixing delayed stage confirmation and preparation
failure status. Two real frequency GBM + automatic pruning-path browser runs
passed. Final run: 158.55 seconds, 1,500 visible progress samples with no backward
movement or handoff gaps, zero browser errors/warnings, all eight curves and
visibility controls checked. Full-stage compact view is 465,241 bytes, 200 sampled
rows; individual endpoints and the fixed-population mean were verified.

Reusable audit: scripts/modelling_audit/workflow-interactions.mjs --full-only
now checks this flow, captures progress and live/completed Training/Validation
screenshots, and waits for terminal UI completion. Evidence:
/tmp/haute-bag-evidence-final; scratch project: /tmp/haute-bag-workflow-final.
Wheel installed into haute-modelling-demo with --no-deps; restart the user's
server and refresh the browser. New GBM fits populate the individual bag curves.
