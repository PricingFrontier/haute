# New-session handover — 22 November 2026

## Start here

The latest requested implementation is complete and installed in the demo. There is no
unfinished feature request. The user now asked for this handover for a fresh session.

Read `/home/ralph/suite/haute/AGENTS.md` before changing code. This document is the current
entry point; [handover.md](handover.md) contains the longer history, with the most recent
implementation notes at its end. Its older sections describe intermediate states.
`plan.md` and `overview.md` contain the original brainstorm, not a new task list.

## Repositories and installed state

- Demo and user project: `/home/ralph/suite/haute-modelling-demo`.
- Haute source: `/home/ralph/suite/haute`.
- Editable t-boost source: `/home/ralph/suite/t-boost`.
- Latest wheel: `/tmp/haute-bag-wheels/haute-0.7.1-py3-none-any.whl`.

The wheel was installed into the demo `.venv` with `uv pip install --no-deps --reinstall`.
Installed backend files and frontend index were checked against the source build by hash.
Other dependencies, including editable t-boost, were preserved. The user was told to
**restart their server and refresh the browser**; whether they have done so is unknown.

User-owned servers were left alone. The last verification server on port 8012 was stopped.
Recheck processes rather than relying on historical PIDs or port numbers.

The Haute working tree has extensive pre-existing modified and untracked files covering
this whole modelling project. No commits or pushes were made for this latest change.
Do not reset, clean, or treat the existing diff as disposable. The demo previously had
unrelated `uv.lock` and `.gitattributes` changes; preserve them too.

## User intent and established UI preferences

The modelling workflow supports Full GBM → Pruning → Banding → Smoothing, with phases
individually enabled or disabled. A disabled phase is skipped when resolving the parent.
Each completed/applied phase automatically prepares the next enabled phase's path.

Keep these preferences:

- Automatic preparation feels like part of the preceding run. Do not introduce a separate
  preview/apply-to-prepare workflow or label the train buttons “building preview”. Applying
  a chosen pruning/banding/smoothing candidate still commits that stage.
- Parameters and training actions live in separate right-side panes. Features are also on
  the right; models are on the left. Model/feature settings use compact disclosures.
- No warning banners that push the canvas/panes down. Readiness reasons belong at the
  bottom of the settings pane, with appropriate disabled training controls.
- Keep Factor, Learning curve, Tables and Paths separate. Do not put full factor/table
  interfaces back inside Paths.
- Preserve cancellation, phase skipping, saved-stage recovery, comparison provenance,
  and checks against stale configuration. User wants a polished, uncluttered interface.

## Latest change: continuous progress and all bag curves

The user noticed progress restarting after boosting and step changes near the end of the
learning curve. Investigation found that the UI reused raw progress from the automatic
next-path job, and the completed curve averaged only bags still boosting at each round.

Implemented:

1. One approximate, monotonic progress bar per model action. The requested fit uses the
   first half; automatic next-path preparation uses the second. Queued handoff keeps the
   bar visible. A new explicit run resets it. The bar clears when the run settles.
2. Independent job-status and stage-state requests can arrive out of order. A completed
   staged job stays active until stage state confirms its result/continuation. This prevents
   a brief “complete” state followed by a restarted bar. Failed/interrupted automatic
   preparation is a failure, not successful completion.
3. Separate Training and Validation views of every bag, stable colours keyed by bag number,
   and accessible visibility checkboxes. Individual curves use their own iteration counts
   and end at their last observed round.
4. Completed means retain stopped bags' last observed losses. Live curves omit the mean
   because concurrently reporting bags are at different iterations. The mean is an average
   of bag losses, **not the loss of the ensemble's averaged predictions**.
5. “Validation” in these bag curves means each bag's own held-back rows. It is distinct
   from the overall evaluation partition; the chart explains this.
6. Independently bounded live histories per bag, packed into rows with explicit per-bag
   iteration coordinates. Faster bags cannot erase slower bags' points. Compact stage
   reads also sample nested completed histories; full artifacts retain original histories.
7. Correct thinning labels for the selected stage, instead of inheriting a parent's flag.

**Compatibility:** old saved results without bag history columns retain their aggregate
chart. New GBM runs populate individual curves; no migration of old results was performed.
The bar percentage is approximate work allocation, not a calibrated time estimate.

### Key code

| File under Haute | Responsibility |
| --- | --- |
| `src/haute/modelling/_tboost.py` | `_RoundProgress` records bag-specific rounds/losses; `_loss_history` aligns final histories and holds stopped bags in the mean. |
| `src/haute/modelling/_loss_history.py` | Shared completed-history sampling and configured row budget. |
| `src/haute/routes/_training_worker.py` | `_append_live_loss_row` and `_append_live_bag_history` retain and independently thin live curves. |
| `src/haute/modelling/_stage_views.py` | Bounded nested histories in compact responses. |
| `frontend/src/modelling/modelRuns.ts` | Model run state and confirmation of the asynchronous handoff. |
| `frontend/src/modelling/runProgress.ts` | Pure progress transition logic. |
| `frontend/src/modelling/ModelEntries.tsx` | Model-name progress bar, including reduced-motion-aware width transition. |
| `frontend/src/modelling/ModellingView.tsx` | Keeps the learning curve on the Full GBM and distinguishes live history. |
| `frontend/src/panels/modelling/BagLossChart.tsx` | Bag curves, split controls, legend/visibility and interpretation text. |
| `frontend/src/panels/modelling/LossTab.tsx` | Chooses bag-aware or legacy aggregate presentation. |
| `frontend/src/panels/IterationLinesChart.tsx` | Optional numeric x axis and independent series coordinates. |
| `frontend/src/modelling/stageResults.ts` | Stage projection and correct history-thinning flag. |

Specs were updated in `specs/modelling/low-level.md` and
`specs/frontend-modelling-optimiser-ui/{high-level,low-level}.md`.
User documentation is in `docs/building-models/modelling.md`.

## Verification and evidence

Affected backend/frontend tests, mypy (408 source modules), TypeScript, touched ESLint/Ruff,
production builder/app builds and bundle-size gates passed. Docs accuracy: 74 passed.
Test-library deprecation warnings and existing float32 warnings are not browser failures.
Do not rerun entire suites; AGENTS.md requires focused verification.

Relevant regression modules:

- `tests/test_tboost_family.py`
- `tests/test_modelling_train_routes.py`
- `tests/test_stage_store.py`
- `frontend/src/modelling/__tests__/{runProgress.test.ts,ModellingView.test.tsx,stageResults.test.tsx}`
- `frontend/src/panels/modelling/__tests__/{LossTab.test.tsx,LossTab.adapter.test.tsx}`
- `frontend/src/panels/__tests__/IterationLinesChart.test.tsx`

Read-only review approved after the delayed-confirmation and preparation-failure fixes.
Thread: `01a20081-9f74-7ee2-9a97-d679c5d66b9b`.
Logs: `/tmp/haute-bag-review{,-followup}.ndjson`.

Two real frequency Full GBM + automatic pruning-path browser runs passed. Final run:

- 158.549 seconds; 1,500 visible progress samples, no retreats or handoff gaps.
- All eight bag curves, Training/Validation controls and visibility verified.
- Terminal UI waited for; no browser errors or warnings.
- Full-stage compact view: 465,241 bytes, 200 sampled rows.
- Bag endpoints: 1103, 908, 758, 1138, 1109, 991, 924, 1151 rounds.
- The last reported mean exactly matched the average of all eight final bag losses.

Evidence: `/tmp/haute-bag-evidence-final/workflow-interactions.json` and screenshots in
that directory. Disposable project: `/tmp/haute-bag-workflow-final`.
Server log: `/tmp/haute-bag-server-final.log`.
Other verification logs use `/tmp/haute-bag-*.log`.
Temporary files may not survive environment cleanup; the reusable code is in the repository.

## Reusable browser workflow

See `scripts/modelling_audit/README.md`. The harness prepares an isolated project, runs
real training and records timings, request sizes, errors/warnings and screenshots.
Never replay against the user's project when a disposable copy suffices.

`workflow-interactions.mjs --full-only` now checks the GBM + automatic pruning path,
learning curves and continuous progress. Without that option it exercises the full
pruning/banding/smoothing workflow. `--resume` is explicit; normal runs reject existing
applied models. The development-row acknowledgement is checked only when shown.

The most recent frequency configuration has 678,012 rows, target ClaimCount, exposure
Exposure, eight bags, 4,000 configured trees, and 20% random validation. This differs from
some earlier performance runs, so their timing/metric figures are not directly comparable.
Do not assume the earlier harness's 44-table candidate exists under every configuration;
inspect the path or pass the desired available point explicitly for a full-chain replay.

### Local command details

Use Haute's `.venv/bin/python`, `.venv/bin/ruff` and `.venv/bin/mypy`.
The normal npm/npx commands resolved to Windows executables in this session. Working Node:

```sh
NODE=/home/ralph/.vscode-server/bin/2242ebbb54efeeb0129e08e919e7e8d43033cd83/node
# From Haute root:
"$NODE" frontend/node_modules/vitest/vitest.mjs run --root frontend <test-file>
# From Haute/frontend:
"$NODE" node_modules/typescript/bin/tsc -b --noEmit
"$NODE" node_modules/vite/bin/vite.js build
"$NODE" node_modules/vite/bin/vite.js build --config vite.app.config.ts
```

Browser executable used:
`/home/ralph/.cache/ms-playwright/chromium_headless_shell-1243/chrome-headless-shell-linux64/chrome-headless-shell`.
Required `LD_LIBRARY_PATH=/tmp/haute-browser-libs/extracted/usr/lib/x86_64-linux-gnu`.

Before packaging, refresh the frontend input manifest using the existing
`FrontendBuildHook._write_input_manifest` helper in `hatch_build.py`, then `uv build --wheel`.
Install the wheel into the demo with `uv pip install --python <demo>/.venv/bin/python
--no-deps --reinstall <wheel>`; avoid a dependency sync that might replace editable t-boost.
Do not rebuild root static assets during an active browser test or concurrently with
source-cleanliness tests. The initial JS budget is very close to its 300 KiB limit;
keep new modelling functionality lazy and do not casually raise that budget.

## Prior performance/storage work to preserve

The preceding session fixed oversized stage manifests with immutable result sidecars,
added compact view sidecars and lazy dense-table reads, reused validated staged sessions
with independent forks, and avoided unnecessary parent restoration/recompression on Apply.
It also fixed estimate reservation, repeated estimates on post-fit changes, legacy stage
configuration normalization and applied-selection state. Detailed timings and evidence
are in the final sections of `handover.md`; do not undo those optimizations while changing
learning curves or stage lifecycle behaviour.

## Suggested start for the next session

Read the user's next request before choosing new work. Confirm whether they restarted
and whether they are viewing a new fit if individual curves seem absent. Inspect current
files/processes rather than relying on scratch artifacts. No additional feature or
optimization is implicitly pending from this handover.
