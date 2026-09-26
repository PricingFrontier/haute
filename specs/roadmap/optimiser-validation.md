# Optimiser validation

Bring the optimiser's result screen (`OptimiserPreview`, which plays the
"validation" role for the optimiser) up to the standard of the modelling
evaluation screen (`ModellingPreview`, labelled "Model validation"). The
packages below come from a planning session on 25 September 2026. A
10-agent planning workflow mapped both screens, wrote a gap analysis,
drafted three plans from different angles, had a critic check them, and
combined them into one. Codex (`gpt-6-astra` at `xhigh`) then reviewed that
plan over seven rounds, returning **APPROVED** twice: at round 5 for the
full plan, and at round 7 for the lean wave 4 re-cut after the product
decisions below. Codex ran small in-memory probes against the installed
`price_contour` and Polars; the facts it established are recorded under
Scope and in the price_contour context.

**Picking this up.** `price_contour` is installed into haute's `.venv` as an
editable install of the checkout at `../price-contour` (`uv pip install -e
../price-contour`). Python edits to the library are live immediately; Rust
edits need a rebuild (`uv pip install -e ../price-contour` again, or
`maturin develop --release` from the checkout). A plain `uv sync` replaces
the editable install with the locked wheel, so re-run the install after
syncing. The haute-only packages (`OPT-V01` onwards) need no product
decision and can start at once. The ratebook half of wave 4 (`OPT-V09C`)
builds on the price-contour 0.5.0 contract described below (Q8 and Q17 are
decided; see Decisions); the one remaining library package is `OPT-PC02`.
Everything ships as
**one PR** on its own branch, with one Codex review of the whole branch
diff before the PR and one Playwright e2e run at the end. The agent never
merges.

## Scope

In scope: the optimiser result workspace and the backend contract it reads.
That covers constraint attainment, the frontier, convergence, rates, a
description of the adjustments the optimiser chose (overall, by segment and
per quote), the shared results workspace frame, contract hardening, and
test coverage.

Out of scope (see "Out of scope and not applicable" below):
- any comparison with current or deployed pricing, including impact,
  dislocation and uplift (decided 25 September 2026);
- holdout or robustness validation;
- export buttons inside the results panes.

## Decisions (25 September 2026)

- **No current-vs-optimised anywhere (Q1 declined).** The optimiser applies
  scenario adjustments on top of a base price, where scenario value 1.0 is
  the unadjusted base. It reapplies scenarios to that base and never sees
  the live, currently deployed pricing. A "Current → Optimised → Change"
  view would compare against something the optimiser does not know.
  Analysts set up impact analysis elsewhere.
  - The tests that pin the absence of Baseline and Uplift (commit
    `e5da5f555`) stay.
  - The planned "current vs optimised totals" package was dropped, along
    with every change, dislocation or price-basis figure in wave 4.
  - The solver's internal `baseline_objective` and `baseline_constraints`
    stay valid. They are the library's reference for `min_pct`/`max_pct`
    constraints and do not represent deployed pricing.
- **Wave 4 is the lean, descriptive version (Q16).** It describes only what
  the optimiser chose: the distribution of chosen scenario values, where 1.0
  is the base price; those adjustments by segment; and a quotes explorer of
  the chosen scenarios (`OPT-V09A` to `OPT-V12`).
- **Analysis columns (Q3).** The user selects which input frame the
  analysis columns come from (any frame connected to the optimiser node)
  and which of its columns to use (`OPT-V09A`).
- **Delivery (Q13).** One PR for all waves.
- **Moot after Q1:** the nominated price column for dislocation (Q2), the
  dislocation weighting default, and the arbitrary price passthrough (Q14).
- **The deployed ratebook factor is collared to the scored grid range
  (Q17, decided 25 September 2026).** The solver priced each quote at the
  grid step nearest its factor product, clamped to the grid ends; the
  Optimiser Apply node multiplied the rates with no clamp, so a quote whose
  product lay past a grid end deployed at a rate the solve never scored.
  The solve now records the grid's `[sv_min, sv_max]` (from
  `QuoteGrid.scenario_values`, Float32 widened, exactly what was scored) as
  `combined_factor_bounds` on the job result, the frontier-select response,
  the saved and MLflow-logged artifact, and every frontier point (they share
  the solve's grid). `_apply_ratebook`, the one apply path behind preview,
  generated code and the deploy scorer, clips `optimised_factor` to it after
  the neutral fill and the product; per-factor columns stay unclamped. The
  trace adds a collar step and reconciles against the clamped value, and the
  factor-table CSV and Publish section state the collar for an external
  rating engine. A ratebook artifact without the bounds is invalid (no legacy
  reader). Inside the range the deployed factor is still the unsnapped
  product: snapping to the nearest step was not adopted.
- **History is always recorded (Q5, decided 26 September 2026).** The
  `record_history` flag is removed from the config, the solver settings and
  the Solve pane: every online solve records its per-iteration history, which
  `max_iter` bounds, and every live ratebook solve its coordinate-descent
  trace (`OPT-V05`).
- **Adjustments are weighted by quote count by default (Q4, decided 26 September
  2026).** The Adjustments tab (`OPT-V10`) weighs each quote by 1 unless the user picks,
  in its Weight by switch, a non-negative objective or constraint column evaluated at the
  chosen scenario. A negative value or a zero total refuses that weighting by name in the
  report's `diagnostics_errors`; it is never computed.
- **A grid without 1.0 only gets a note (Q12, decided 26 September 2026).** Such a grid has
  no unadjusted scenario, so the Adjustments tab omits the unadjusted share and says "The
  scenario grid has no 1.0 step, so no quote is unadjusted." The Scenario Expander is not
  changed to force 1.0 into the grid.
- **Ratebook per-quote results take option (a) (Q8, decided 25 September
  2026).** price-contour 0.5.0 surfaces the per-quote frame the solver
  computes (`RatebookResult.quote_results`) and a public
  `RatebookOptimiser.evaluate()`; see "Price-contour contract (0.5.0)" below.
  The wave 4 tabs describe the solver-evaluated step; after the collar, the
  deployed factor differs from it only by the within-range rounding to the
  nearest step, which `quote_results.factor_product` makes visible.

## Price-contour contract (0.5.0)

The library changes this plan needed were built in the sibling checkout
(`../price-contour`, branch `feat/haute-link-0.5`, version 0.5.0). Its
`docs/DESIGN_DECISIONS.md` §13 is the contract, and haute's side is
specified in [the optimiser low-level spec](../optimiser/low-level.md).
Haute pins `price-contour>=0.5.0,<0.6`, and its runtime guard
(`src/haute/_price_contour.py`) requires the new surface, so **the haute PR
merges only after 0.5.0 is released to PyPI and `uv.lock` is regenerated**
(`uv lock --upgrade-package price-contour`). Until then haute runs against the
editable checkout.

What the remaining packages can rely on:

- **Canonical ratebook evaluation.** Every reported ratebook number comes
  from one Rust kernel that prices each quote at the grid step nearest the
  f32 product of the final factor tables (products outside the grid clamp to
  the end steps; an exact midpoint goes to the lower step).
  `RatebookResult.quote_results` holds `quote_id`, `optimal_step`,
  `optimal_scenario_value`, `optimal_objective`, `optimal_<c>`,
  `factor_product`, `clamped_low` and `clamped_high`; the result also carries
  `n_quotes_clamped_low/high`, `scenario_values`, `baseline_scenario_value`
  and `constraint_bounds`. `RatebookOptimiser.evaluate(grid, factors,
  factor_tables)` runs the same kernel and reproduces a result exactly.
- **Ratebook frontier points.** The frontier keeps each point's factor tables
  (`factor_tables`, aligned with `points`), and each row's totals are that
  point's canonical evaluation. Haute stores them as `frontier_factor_tables`
  and materialises a selected point from them, exactly and without a solver
  or grid; `evaluate(grid, factors, tables)` gives the point's per-quote rows.
- **Absolute bounds.** Every frontier emits `bound_<c>` for every constraint,
  swept or not, next to `threshold_<c>` (which stays in the user's units, a
  fraction for pct constraints); solve results expose `constraint_bounds`.
- **One baseline rule.** Sum, pct and ratio baselines all use the scenario
  value nearest 1.0 (f32, lowest on a tie). Every total, including a reported
  ratio's numerator and denominator, accumulates f32 values in f64.
- **Fail loud.** No reported value is a silent default. Missing frontier
  totals or λ, a zero-baseline pct constraint, an unknown warm-start λ name,
  a non-positive candidate range, scenario values that are not strictly
  increasing, a reserved constraint name (`objective`, `step`,
  `scenario_value`) and an ignored `parallel=True` all raise; unpersisted
  fields raise `ResultUnavailableError`.
- **Explicit contracts.** Dict outputs follow constraint order;
  `per_factor_results` records carry `cd_iteration`, `factor`,
  `factor_index`, totals, λ, `clamp_rate`, `inner_iterations` and
  `inner_converged`; `quote_results_schema` and `frontier_points_schema` give
  exact schemas, and the Python-orchestrated online frontier matches the Rust
  one; the package ships `py.typed`.
- **`clamp_rate` is a search-space diagnostic:** the mean, over every grouped
  solve, of the fraction of (quote, candidate) targets that fell strictly
  outside the scenario range. It does not count quotes at an edge;
  `n_quotes_clamped_low/high` do. Help copy (`OPT-V03`, `OPT-V07`) says so.

## Priorities

| Package | State | Priority | Outcome |
|---|---|---:|---|
| OPT-V01 | Planned | P1 | Every constraint, swept or not, shows bound, achieved, slack, status and λ from one backend-owned bound; Summary and point details can no longer disagree. |
| OPT-V02 | Planned | P2 | Selecting a frontier point never removes a view; state resets on a new job; request identity and late-response guards; stable as-solved anchor. |
| OPT-V03 | Planned | P2 | Optimiser and modelling share one results workspace: focus view, remembered height, ARIA tabs, per-tab intros, provenance strip. |
| OPT-V04 | Planned | P2 | Typed frontier and factor rows with complete constraint keys, input provenance, `diagnostics_errors`, finite JSON, no silent zero baselines. |
| OPT-V05 | Planned | P3 | Convergence on real axes with bound lines and λ per constraint; a ratebook coordinate-descent trace. |
| OPT-V06 | Planned | P2 | A responsive frontier drawn only through feasible points, with slices, global point identity and a signed discrete trade-off. |
| OPT-V07 | Planned | P3 | A Rates tab relativity chart with quote strip and values table; an honest, responsive beeswarm. |
| OPT-V09A | Planned | P2 | Analysis columns from a user-chosen input frame, and the complete scenario grid, kept durably under an ownership and lease contract. |
| OPT-V09B | Planned | P2 | Bounded, admitted, per-job serialised queries over the chosen scenarios, for the as-solved result and any frontier point. |
| OPT-V09C | Decision | P3 | Ratebook per-quote choices through a canonical price_contour evaluation, or an explicit online-only state. |
| OPT-V10 | Planned | P2 | An Adjustments tab: exact per-grid-value distribution of chosen scenario values against the 1.0 base price. |
| OPT-V11 | Planned | P3 | A Segments tab: chosen adjustments by analysis column (and rating factor with `OPT-V09C`). |
| OPT-V12 | Planned | P3 | A Quotes explorer sorted, filtered and searched server-side over the chosen scenarios. |
| OPT-V13 | Planned | P2 | One e2e walk of every optimiser pane and one snapshot refresh. |
| OPT-PC02 | Deferred | P3 | price_contour's point apply can be cancelled or chunked, so rapid frontier stepping stops wasted work. |

## Planned improvements

These rules apply to every package:
- Update the owning component specification first.
- Write the failing tests first.
- Run only the affected vitest and pytest files, plus `tsc -b --noEmit` for frontend changes.
- Playwright and the snapshot regeneration happen once, in `OPT-V13`.
- Codex reviews the whole branch diff once, before the PR.
- Haute has no users, so contract changes need no migration; tests that pin old behaviour are rewritten.

The gap IDs (`OPT-G01`…`OPT-G22`) refer to the gap table under "Screens today and gaps" below.

### OPT-V01 — Constraint attainment with one bound source, stated in text

**Why:** Every constraint on Summary and DetailCard, **swept or not**, reads like "min 1,000,000 · achieved 1,012,400 · slack +12,400 (+1.24%) · Met · λ 0.0031". It is computed by one pure helper, judged against the bound the displayed result was actually solved at, and the status is text, not colour. λ is shown as its own quantity and is never turned into a tightness claim. Gaps closed: G02, G03, G18, part of G14.

**Plan:**

- **Files.** `frontend/src/panels/optimiser/optimiserHelpers.ts`, `frontend/src/panels/optimiser/constraintAttainment.ts` *(new)*, `frontend/src/panels/optimiser/ConstraintAttainmentTable.tsx` *(new)*, `frontend/src/panels/optimiser/SummaryTab.tsx`, `frontend/src/panels/optimiser/DetailCard.tsx`, `frontend/src/panels/optimiser/lambdaCopy.ts`, `frontend/src/stores/useNodeResultsStore.ts`, `src/haute/routes/_optimiser_frontier.py`, `src/haute/routes/_frontier_point_summary.py`, `src/haute/routes/optimiser.py`, and the tests listed below.
- **Backend changes.**
  1. **Keep all constraints in point summaries.** Split `constraint_names`, meaning **every** configured constraint, from `swept_axes`, meaning `ranges.keys()`. There are **three** swept-only call sites, and all three change: `limited_frontier_payload` for the recompute route (`_optimiser_frontier.py:1227-1230`), `limited_frontier_payload` for the frontier built during the solve (`_optimiser_solver.py:917-919`), and frontier select, which re-derives summaries from the stored `frontier_data["constraint_names"]` (`_optimiser_frontier.py:311-318`). `_frontier_point_summary.py:115` then carries `threshold_*`, `total_*` and λ for unswept constraints too.
     - Today the raw `points` rows already carry every library column (`_optimiser_limits.py:93`), and point-summary λ already covers every constraint (`frontier_point_lambdas`). Only `point_summaries[].constraints` is filtered, and summaries have no threshold field. The fix is still backend-owned, so the frontend reads one typed source rather than re-parsing raw rows.
  2. **`min_pct`/`max_pct` are supported by the library, so there is no deletion branch.** The effective absolute bound is `pct × baseline_total` of that constraint, where `baseline_total` is taken at the grid's nearest-1.0 step (see "Price-contour contract (0.5.0)"). The backend returns it, as `effective_bounds: {name: {kind, bound}}` on the solve result and on each point summary, so the frontend never re-derives it.
     - **Frontier `threshold_*` is a fraction for pct constraints**, not an absolute bound. Since price-contour 0.5.0 every frontier row also carries `bound_<c>` (the absolute bound, for every constraint) and solve results carry `constraint_bounds`: `effective_bounds` reads those, so haute never multiplies a fraction by a baseline itself. `_frontier_point_constraints_override` (`_optimiser_frontier.py`) keeps building the constraint specs published as `effective_constraints`.
     - The constraint editor offers only min and max (`OptimiserConstraintSettings.tsx:34, 152`), so pct constraints reach haute only through hand-written config. Adding them to the UI is out of scope; the backend contract still handles them correctly.
  3. **Goldens.** Regenerate the contracts and goldens in this package, so the contract change lands with its first caller. OPT-V04 then types the rows on top.
- **Frontend changes.**
  1. Replace `isConstraintMet(type, _ratio, abs, thr)` with a pure `constraintAttainment({kind, bound, achieved})`. It returns `{kind, bound, achieved, slack, slackPct, status: 'met'|'breached'}`.
     - `met` is a strict comparison on the Float64 values the backend returns. The signed `slackPct` is always shown, so "Breached by 0.01%" is readable rather than just red.
     - There is **no "binding" column.** λ is displayed separately as "λ (multiplier)", with help text saying it is the solver's Lagrange multiplier on this constraint's term. A positive λ can occur together with positive slack in this discrete solve.
     - A missing bound is a thrown contract error, not a coloured state.
  2. Add a store selector, `effectiveConstraintBounds`. It returns the displayed result's backend `effective_bounds`, and both panes read it. This removes the two divergent derivations and the `pointThreshold ?? spec ?? 0` fallback.
  3. `ConstraintAttainmentTable` shows Constraint | Kind | Bound | Achieved | Slack | Status | λ. Status is a text chip plus an icon, with colour as a secondary cue. Use it in both panes.
  4. Show λ for ratebook results on Summary as well, dropping the `mode !== 'ratebook'` gate so it matches DetailCard.
  5. Rewrite `lambdaCopy.ts`: `LAMBDA_LABEL` (today "λ (shadow price)") becomes "λ (multiplier)", and `LAMBDA_HELP` and the file's doc comment lose the "0 means not binding" and "gained per unit relaxed" wording. See OPT-V06 for the signed trade-off.
- **Risks.** Contract change on point summaries (goldens regenerated here). There are no silent fallbacks.

**Acceptance:**

- `optimiser/__tests__/constraintAttainment.test.ts` *(new)*: min and max at, above and below the bound; slack sign; non-finite input throws.
- `panels/optimiser/__tests__/SummaryTab.test.tsx` *(new; the modelling file of the same name is separate)* and `panels/optimiser/__tests__/DetailCard.test.tsx` *(new)*: a row with positive λ and positive slack reads "Met" and is not labelled binding.
- pytest: a two-constraint solve sweeping only one. The selected point's summary contains the unswept constraint's bound, total and λ, through all three call sites (solve-time frontier, recompute, select). Mutate each `constraint_names` back to `ranges.keys()` and confirm the test fails.
- pytest: a `min_pct` constraint's `effective_bounds` equals pct × baseline total on the solve result **and on a swept frontier point** (where the library reports the threshold as a fraction), from a real prebuilt-grid solve.
- G03 regression in `panels/__tests__/OptimiserPreview.test.tsx`: select a point whose swept threshold differs from the solved one; both panes print the same bound and status. Mutate the selector back to `cached.constraints` and confirm the test fails.
- Rewrite the tests that pin today's behaviour: `optimiserHelpers.test.ts:48-49` (the "red" state); in `OptimiserPreview.test.tsx`, 1217 ("hides Lambdas section in ratebook mode"), 1130 and 1144 (green/red dot), 891 (met/unmet indicators), and 320, 929 and 1169 (the "λ (shadow price)" label).

**Dependencies:** Size M. **Depends on:** none.

**Evidence:** Current code this package changes or relies on: `frontend/src/panels/optimiser/optimiserHelpers.ts`, `frontend/src/panels/optimiser/SummaryTab.tsx`, `frontend/src/panels/optimiser/DetailCard.tsx`, `frontend/src/panels/optimiser/lambdaCopy.ts`, `frontend/src/panels/optimiser/OptimiserConstraintSettings.tsx`, `frontend/src/stores/useNodeResultsStore.ts`, `src/haute/routes/_optimiser_frontier.py`, `src/haute/routes/_optimiser_solver.py`, `src/haute/routes/_optimiser_limits.py`, `src/haute/routes/_frontier_point_summary.py`, `src/haute/routes/optimiser.py`, `frontend/src/panels/modelling/__tests__/SummaryTab.test.tsx`, `frontend/src/panels/__tests__/OptimiserPreview.test.tsx`, `frontend/src/panels/optimiser/__tests__/optimiserHelpers.test.ts`.

### OPT-V02 — Selected-point integrity, state handling and shared fixtures

**Why:** Selecting the publish target never removes what the reviewer is looking at, a new solve resets the tab, errors are recoverable, and the distribution UI gets tested for the first time. Gaps closed: G06 (frontend half), G17, G21, part of G22.

**Plan:**

- **Files.** `frontend/src/panels/OptimiserPreview.tsx`, `frontend/src/panels/optimiser/SummaryTab.tsx`, `frontend/src/panels/optimiser/ConvergenceChart.tsx`, `frontend/src/panels/optimiser/QuotesTab.tsx`, `frontend/src/stores/useNodeResultsStore.ts`, `frontend/src/panels/optimiser/__tests__/fixtures.ts` *(new)*, `frontend/src/panels/__tests__/OptimiserPreview.test.tsx`, `frontend/src/panels/__tests__/OptimiserPreview.storeIntegration.test.tsx`.
- **Backend changes.** `frontier_generation` already exists on the job internally (it is bumped on recompute). Expose it on `OptimiserFrontierResponse` (`schemas.py:3122`), the select response and the solve result, regenerate the contracts and goldens, and add a pytest that the exposed value increments on recompute. OPT-V04 later types the points, but the generation lands here with its first caller.
- **Frontend changes.**
  1. Move the stats grid out of the histogram guard, so a point's `scenario_value_stats` (from its `sv_*` columns) render when its histogram is null. Label the stats "Frontier point N" or "As solved".
  2. Add `solvedResult` (the cached original result) to `OptimiserPreviewData`.
     - Convergence availability is decided from `solvedResult`, so the tab no longer vanishes on point select.
     - It shows the as-solved history under "History is recorded for the solved result; frontier point N: converged/not, K iterations".
  3. Reset the tab to its default when the `jobId` or the `nodeId` changes. Today the tab is plain `useState` (`OptimiserPreview.tsx:132-134`) with no reset at all, and App.tsx mounts the preview with no `key`, so the tab survives a new solve and a switch between optimiser nodes. Do **not** copy ModellingPreview's `[nodeId, result]` reset (`ModellingPreview.tsx:126-132`): `applyFrontierPointSummary` creates a new result on every stepper press (`useNodeResultsStore.ts:455-459`), so keying on `result` would reset the tab on every step.
  4. Show `result.warning` (the non-convergence reason) as an amber strip in the preview.
  5. Delete the unreachable "No frontier data available" branch (`OptimiserPreview.tsx:467-473`).
  6. Add Retry buttons to the Quotes and Rates error states.
  7. Cache `/apply` responses in the store under a **full request identity**: `(jobId, frontierGeneration, target: 'solved'|pointIndex, canonical query)`, where the query is sort, filters, search, offset and limit (OPT-V12 extends it). Keep at most 16 entries (LRU) and clear them on a new job or a frontier recompute. Every response is checked against the identity current when it arrives; a late response from an earlier job, generation or query is dropped. `frontierGeneration` comes from the frontier response (exposed in this package).
  8. `solvedResult` also anchors the as-solved frontier marker (fixing `OptimiserPreview.tsx:485-486`), so the marker no longer moves when a point is selected.
  9. Add shared fixtures:
     - online with non-null stats and histogram, frontier points carrying `threshold_*`, `converged` and `iterations`, and history with `lambdas` and `total_constraints`;
     - ratebook with factor tables and `quote_count`.
     Replace the null fixtures in both factories: `makePointSummary` (`OptimiserPreview.test.tsx:162-163`) and the shared `makeSolveResultFactory`, plus `storeIntegration.test.tsx:65-66`.
- **Risks.** The Convergence wording must not imply that the history belongs to the point.

**Acceptance:**

- The stats persist after point select.
- Pressing the stepper while on Convergence keeps Convergence.
- Pressing the stepper does not reset the tab; a new `jobId` does, and so does switching to another optimiser node.
- Retry refetches.
- Reopening Quotes makes no second call.
- After a frontier recompute, the same point index refetches.
- A late response from an earlier job or generation is discarded.
- The as-solved marker keeps its position after a point is selected.
- The warning is visible.
- Convergence staying visible after point select is untested today, so it gets **new** tests. The "tab switching" block (709-790) stays; its "hides Convergence tab when no history data" case (769) remains valid for a solve with no recorded history.

**Dependencies:** Size M. **Depends on:** none.

**Evidence:** Current code this package changes or relies on: `frontend/src/App.tsx`, `frontend/src/panels/OptimiserPreview.tsx`, `frontend/src/panels/ModellingPreview.tsx`, `frontend/src/panels/optimiser/SummaryTab.tsx`, `frontend/src/panels/optimiser/ConvergenceChart.tsx`, `frontend/src/panels/optimiser/QuotesTab.tsx`, `frontend/src/stores/useNodeResultsStore.ts`, `frontend/src/panels/__tests__/OptimiserPreview.test.tsx`, `frontend/src/panels/__tests__/OptimiserPreview.storeIntegration.test.tsx`, `src/haute/schemas.py`, `src/haute/routes/_optimiser_frontier.py`.

### OPT-V03 — Shared ResultsWorkspace shell, optimiser on it, provenance strip

**Why:** The optimiser screen gets the modelling frame: a Focus view, a remembered height, results-style tabs with full ARIA wiring, per-tab intros, container-query responsiveness, and a strip that names what the figures are. Gaps closed: G12, G13, G14, G15, G04 (disclosure half).

**Plan:**

- **Files.**
  - `frontend/src/panels/ResultsWorkspace.tsx` *(new)*
  - `frontend/src/panels/ModellingPreview.tsx`
  - `frontend/src/panels/OptimiserPreview.tsx`
  - `frontend/src/panels/PreviewPanelTabs.tsx`
  - `frontend/src/panels/PreviewPanelFrame.tsx`
  - `frontend/src/components/ModalShell.tsx`
  - `frontend/src/stores/useUIStore.ts`
  - `frontend/src/panels/modelling/validation.css`
  - `frontend/src/panels/optimiser/iterationSummary.ts`
  - `frontend/src/panels/__tests__/ValidationWorkspace.test.tsx`
  - `frontend/src/panels/__tests__/ModellingPreview.test.tsx`
  - `frontend/src/panels/__tests__/OptimiserWorkspace.test.tsx` *(new)*
- **Backend changes.** Populate `n_quotes` and `n_steps` for **ratebook** results, which currently omit them (`_optimiser_solver.py:1219`), with a pytest on a real small ratebook solve. Online results already carry both. Source provenance comes in OPT-V04.
- **Frontend changes.**
  1. Extract `ResultsWorkspace` from `ModellingPreview.tsx:229-343`. It owns:
     - the ModalShell focus toggle;
     - PreviewPanelFrame with a height getter and setter;
     - an optional progress bar;
     - `PreviewPanelTabs appearance="results"` with an `idPrefix`;
     - a provenance slot and an intro `{title, description}`;
     - the `role=tabpanel` body keyed by tab, with `aria-labelledby` and the `validation-workspace` class.
  2. The `validation.css` import moves into `ResultsWorkspace`, so the optimiser is styled even when no model has been opened in the session. Replace the hard-coded `--model-accent` with `--results-accent` and `--results-accent-soft`, defaulting to the model tokens. `ChartScaffold.tsx` **stays where it is**: it already has consumers outside modelling (e.g. `editors/banding/BandingHistogram.tsx`).
  3. Move ModellingPreview onto the shell with no behaviour change. Share the diagnostics-set label helper, which removes the duplication between `ModellingPreview.tsx:290` and `modelling/SummaryTab.tsx:227-229`, and replace the `evaluation!` non-null assertion in the diagnostics strip (`ModellingPreview.tsx:294`) with an explicit guard.
  4. OptimiserPreview on the shell:
     - ariaLabel "Optimiser validation", `idPrefix` "optimiser-preview", accent from a new `--optimiser-accent` / `--optimiser-accent-soft` token pair. It must **not** reuse a warning colour: the amber strip (OPT-V02) and breached statuses use the warning palette, and a warning-coloured accent would make the whole pane read as a warning;
     - add `optimiserPreviewHeight` to `useUIStore`;
     - keep `HeaderPointStepper` as a header action.
  5. `OPTIMISER_VIEW_INTRODUCTIONS` for Frontier, Summary, Rates, Quotes and Convergence. Clamp-rate copy uses the definition in "Price-contour contract (0.5.0)".
  6. Provenance strip: "Online|Ratebook · N quotes × M scenario steps · As solved|Frontier point i of N · Expected values from the scoring models on the solve quotes; not observed outcomes."
  7. FrontierTab stacks the chart above the detail card below the 640 px container breakpoint. Replace the 9–11 px uppercase labels with the modelling type scale.
- **Risks.** Keep the extraction mechanical. It is: `PreviewPanelFrame` already takes `initialHeight`, `onHeightChange` and `focused`, `PreviewPanelTabs` already takes `appearance` and `idPrefix`, and `validation.css` already has the container query at 640 px. The selected-row colour in modelling depends on the token default. The canvas-assurance snapshots will change; they are regenerated in OPT-V13.

**Acceptance:**

- Every existing modelling test must pass **unchanged**: this is the characterisation guard for the extraction.
- New `OptimiserWorkspace.test.tsx`, mirroring `ValidationWorkspace.test.tsx`:
  - aria-controls and aria-labelledby wiring;
  - the focus view closes on Escape and keeps the tab;
  - the height is remembered;
  - the strip shows on every tab;
  - an intro shows per tab;
  - the layout stacks at a narrow width.

**Dependencies:** Size M. **Depends on:** OPT-V02, because both edit `OptimiserPreview.tsx`.

**Evidence:** Current code this package changes or relies on: `frontend/src/panels/ModellingPreview.tsx`, `frontend/src/panels/OptimiserPreview.tsx`, `frontend/src/panels/PreviewPanelTabs.tsx`, `frontend/src/panels/PreviewPanelFrame.tsx`, `frontend/src/components/ModalShell.tsx`, `frontend/src/stores/useUIStore.ts`, `frontend/src/panels/modelling/validation.css`, `frontend/src/panels/optimiser/iterationSummary.ts`, `frontend/src/panels/__tests__/ValidationWorkspace.test.tsx`, `frontend/src/panels/__tests__/ModellingPreview.test.tsx`, `src/haute/routes/_optimiser_solver.py`, `frontend/src/panels/modelling/ChartScaffold.tsx`, `frontend/src/panels/editors/banding/BandingHistogram.tsx`, `frontend/src/panels/modelling/SummaryTab.tsx`.

### OPT-V04 — Contract hardening: typed rows, provenance, `diagnostics_errors`, no silent zeros

**Why:** Bring the optimiser payload to modelling's contract discipline **before** any chart is rebuilt on it: typed frontier and factor rows, the input provenance returned, degraded diagnostics reported, finite JSON enforced, and no silent 0.0 baselines. Gaps closed: G20, G04 (provenance half).

**Plan:**

- **Files.**
  - Backend: `src/haute/schemas.py`, `src/haute/routes/optimiser.py`, `src/haute/routes/_optimiser_solver.py`, `src/haute/routes/_optimiser_frontier.py`, `src/haute/routes/_frontier_point_summary.py`, `src/haute/routes/_optimiser_service.py`, `scripts/generate_api_contracts.py`.
  - Frontend: `frontend/src/generated/api-contracts.generated.ts`, `frontend/src/types/guards.ts`, `frontend/src/api/client.ts`, `frontend/src/panels/optimiser/DetailCard.tsx`, `frontend/src/panels/optimiser/ratebookFactorTables.ts`, `frontend/src/stores/useNodeResultsStore.ts`.
  - Tests: `tests/test_optimiser_contracts.py`, `tests/test_frontier_point_summary.py`, `tests/test_ui_contract_golden.py`, `tests/fixtures/ui_contracts/optimiser_status_response.json`, `tests/fixtures/ui_contracts/optimiser_frontier_response.json`, `tests/fixtures/ui_contracts/optimiser_frontier_select_response.json`.
- **Backend changes.**
  1. Replace `OptimiserFrontierResponse.points: list[dict]` with a strict `OptimiserFrontierPoint`: `total_objective`, `thresholds`, `totals`, `lambdas` maps, `converged`, `iterations`, and `sv_*` stats. Replace the factor-table rows with strict models: level, rate, quote_count, plus declared factor keys.
     - A model validator enforces **map-key completeness**. `thresholds`, `totals` and `lambdas` each hold exactly the configured constraint names, and `effective_bounds` (from OPT-V01) matches them.
  2. Return `input_summary` (`data_source`, `source_file`, `graph_fingerprint`, solver settings) on `OptimiserSolveResult`. Today `_input_summary` (`optimiser.py:526-541`) builds it only for the artifact, from the job's `input_provenance` (`_optimiser_service.py:1209`); reuse both rather than building a second summary.
  3. Add `diagnostics_errors: list[{diagnostic, error_type, message}]`. Populate it where stats currently return `None` silently (`_optimiser_solver.py:159-168`) and where the frontier fails, keeping `frontier_error` in the list.
  4. Remove the `.get(..., 0.0)` and `{}` baseline fallbacks (`optimiser.py:209-210`, `_optimiser_frontier.py:324-325, 410-411`). A missing baseline is an error, not a zero.
     - The library side already raises on a missing frontier total or λ (price-contour 0.5.0), so the strict rows here never receive a fabricated zero from it.
  5. Run the finite-JSON walk on the optimiser status payload (`optimiser.py:312-352`). The walk already exists as `_non_finite_paths` (`optimiser.py:626`), used today only by the artifact payload validation (`optimiser.py:729`); mirror the modelling status route's `_result_finite_validated` in how it is applied.
  6. Regenerate the contracts, the goldens and the OpenAPI fingerprint.
- **Frontend changes.**
  1. Delete DetailCard's ad-hoc `optionalPointNumber` parsers (13-58) and the ad-hoc factor parsing, in favour of generated types and one guard. The throw paths go with them, so no error boundary is needed.
  2. `FAILED_SOLVE_RESULT` (`useNodeResultsStore.ts:431-435`) stops fabricating a zero baseline.
  3. The provenance strip adds the source.
  4. A "Diagnostics issues" `role=alert` in Summary, reusing the modelling pattern (`modelling/SummaryTab.tsx:256-309`).
- **Risks.**
  - The price_contour point columns are dynamic, and differ by mode (see "Price-contour contract (0.5.0)"; `frontier_points_schema` gives the exact columns per mode): ratebook points have `clamp_rate` and **no** `sv_*` columns, so the `sv_*` stats are optional on a ratebook point and required on an online one. Pct thresholds arrive as fractions. Pin both shapes with a test against a real solve before typing them as maps.
  - The MLflow frontier CSV (`optimiser.py` ~960-970) must move to the new shape.
  - Contract changes trip the OpenAPI fingerprint and the mypy Literal gates.
  - **Legacy stats transition.** `scenario_value_stats` and `scenario_value_histogram` stay typed as they are in OPT-V04. OPT-V10 owns their removal, and in the same change updates the point summaries, OPT-V02's fixtures and the Summary pane.

**Acceptance:**

- pytest: strict rows reject extra, missing or non-finite fields, and a missing constraint key; a missing baseline raises; a failing stats computation lands in `diagnostics_errors`; `input_summary` is present; the goldens are regenerated.
- vitest: the guard rejects a malformed point without a render crash; the alert renders; the strip shows the source.

**Dependencies:** Size M. **Depends on:** OPT-V01.

**Evidence:** Current code this package changes or relies on: `src/haute/schemas.py`, `src/haute/routes/optimiser.py`, `src/haute/routes/_optimiser_solver.py`, `src/haute/routes/_optimiser_frontier.py`, `src/haute/routes/_frontier_point_summary.py`, `src/haute/routes/_optimiser_service.py`, `scripts/generate_api_contracts.py`, `frontend/src/generated/api-contracts.generated.ts`, `frontend/src/types/guards.ts`, `frontend/src/api/client.ts`, `frontend/src/panels/optimiser/DetailCard.tsx`, `frontend/src/panels/optimiser/ratebookFactorTables.ts`, `frontend/src/stores/useNodeResultsStore.ts`, `tests/test_optimiser_contracts.py`, `tests/test_frontier_point_summary.py`, `tests/test_ui_contract_golden.py`, `tests/fixtures/ui_contracts/optimiser_status_response.json`, `tests/fixtures/ui_contracts/optimiser_frontier_response.json`, `tests/fixtures/ui_contracts/optimiser_frontier_select_response.json`, `frontend/src/panels/modelling/SummaryTab.tsx`.

### OPT-V05 — Convergence on real axes (LossTab parity) plus a ratebook CD trace

**Why:** A reviewer can diagnose non-convergence: the objective, λ per constraint, and each constraint total against its bound, on real scales. Ratebook solves get a coordinate-descent view. Gaps closed: G10, part of G16.

**Plan:**

- **Files.** `frontend/src/panels/modelling/LossTab.tsx`, `frontend/src/panels/modelling/LossChart.tsx`, `frontend/src/panels/IterationLinesChart.tsx` *(new, extracted)*, `frontend/src/panels/optimiser/ConvergenceChart.tsx`, `frontend/src/panels/OptimiserConfig.tsx`, `src/haute/routes/_optimiser_solver.py`, `src/haute/routes/_optimiser_frontier.py`, `src/haute/schemas.py`, `frontend/src/panels/optimiser/__tests__/ConvergenceChart.test.tsx`, `frontend/src/panels/modelling/__tests__/LossTab.test.tsx`, `frontend/src/panels/modelling/__tests__/LossChart.test.tsx`, `tests/test_optimiser_routes_real_library.py`.
- **Backend changes.**
  1. Map `RatebookResult.per_factor_results` into a typed `ratebook_cd_trace: {records: [{cd_iteration, factor, factor_index, total_objective, total_constraints, lambdas}], truncated}` on the ratebook solve result and the frontier-select response (`null` for online results and for frontier points, like `history`).
  2. The trace carries the objective, the constraint totals and λ per pass and factor (price-contour 0.5.0 records), so the ratebook view can draw constraint totals against their bounds like the online one. Every record holds exactly the configured constraint names. A ratebook result without a trace (a frontier point's, or one loaded from a save) labels the view "live solves only".
  3. Cap its length the way `loss_history` is capped: keep the last `HAUTE_OPTIMISER_CD_TRACE_LIMIT` records (default 1,000) and set `truncated`.
  4. `record_history` is removed (Q5, decided): every online solve records its history, which `max_iter` bounds. The config key, the solver setting, the Solve pane toggle and the docs go; a config that still carries the key is refused as an undeclared key.
- **Frontend changes.**
  1. Extract `LossTabChart` into a generic `IterationLinesChart`. It takes a series list, an optional vertical marker, optional horizontal reference lines, and a linear or log y-axis. It is built on ResponsiveChart, ChartValueGrid and ChartLegend. LossTab becomes an adapter.
  2. Convergence as small multiples, each on its own real axis:
     - the objective;
     - the maximum λ change (log scale, with 0 clamped and a note);
     - constraint totals, with dashed bound lines and a first-feasible marker from `all_constraints_satisfied`;
     - λ per constraint.
  3. For ratebook: the objective and each constraint total (with its dashed bound line) by CD pass, one line per factor; λ per record is in the values table.
  4. A `ChartValuesTable` replaces the ad-hoc iterations table.
  5. Convergence is offered for every result. Empty state, for a ratebook result without a trace: "The coordinate-descent trace is recorded by live solves only; this result has none." An online solve without history is a contract error and fails loudly.
- **Risks.** `per_factor_results` records name their factor and pass explicitly (`cd_iteration`, `factor`, `factor_index`, price-contour 0.5.0), so the trace never infers the factor from position; pin the record fields with a test against the installed version.
- **Files (Q5).** Removing `record_history` also touches `src/haute/_types.py`, `src/haute/_cache.py`, `docs/building-models/nodes/optimiser.md`, the online example's `config/optimiser.json`, `scripts/run_frontend_e2e_server.py` and every test that set the key.

**Acceptance:**

- The LossTab and LossChart tests stay unchanged. Mutate the adapter to prove they exercise it.
- Convergence: real tick values; bound lines; a zero λ change on the log axis; the ratebook view; the empty state.
- pytest: a real small ratebook solve gives a finite trace whose last objective agrees with `total_objective` to a relative 1e-6. It is not exact by design: the trace reports each inner solve on the search's working multiplier, and `total_objective` is the canonical evaluation of the final tables.

**Dependencies:** Size M. **Depends on:** OPT-V03.

**Evidence:** Current code this package changes or relies on: `frontend/src/panels/modelling/LossTab.tsx`, `frontend/src/panels/modelling/LossChart.tsx`, `frontend/src/panels/optimiser/ConvergenceChart.tsx`, `frontend/src/panels/OptimiserConfig.tsx`, `src/haute/routes/_optimiser_solver.py`, `src/haute/routes/_optimiser_frontier.py`, `src/haute/schemas.py`, `frontend/src/panels/optimiser/__tests__/ConvergenceChart.test.tsx`, `frontend/src/panels/modelling/__tests__/LossTab.test.tsx`, `frontend/src/panels/modelling/__tests__/LossChart.test.tsx`, `tests/test_optimiser_routes_real_library.py`.

### OPT-V06 — Frontier chart and point details fit for choosing the publish target

**Why:** The frontier reads like a modelling chart and supports the choice: responsive, 12 px named axes, a legend, a true 1-D slice for multi-constraint sweeps, non-converged points marked, the trade-off stated, and a values table. Gaps closed: G09, part of G16.

**Plan:**

- **Files.** `frontend/src/panels/optimiser/FrontierChart.tsx`, `frontend/src/panels/optimiser/DetailCard.tsx`, `frontend/src/panels/optimiser/frontierSlices.ts` *(new)*, `frontend/src/panels/ChartFocusDetail.tsx` *(new, extracted from `modelling/AveTab.tsx`)*, `frontend/src/panels/modelling/AveTab.tsx`, `frontend/src/panels/OptimiserPreview.tsx`, `frontend/src/utils/chartHelpers.ts`, `frontend/src/panels/optimiser/__tests__/FrontierChart.test.tsx`, `frontend/src/panels/optimiser/__tests__/frontierSlices.test.ts` *(new)*, `frontend/src/panels/modelling/__tests__/AveTab.test.tsx`.
- **Backend changes.** None. The typed points come from OPT-V04.
- **Frontend changes.**
  1. Rebuild FrontierChart on ResponsiveChart, ChartSvg, ChartValueGrid and ChartLegend.
     - The y axis is labelled with the objective column name; the x axis with the constraint name.
     - Keep the overlap bucketing and keyboard focus.
     - Legend entries: frontier points, the as-solved ring, the selected point, and non-converged points (hollow).
  2. `frontierSlices.ts` groups the n^k grid by the other constraints' `threshold_*` values. Exact equality is safe because those values come from linspace. "Holding <other> at" selects pick a slice, and the slice is drawn as a line in bound order, so the chart shows a frontier rather than a projected cloud. The footnote states the slice size and any truncation at `FRONTIER_POINT_LIMIT`.
     - **Slice identity contract.** Every point keeps its **global** index through filtering, overlap grouping, stepping and publishing. Selection, the stepper and `/frontier/select` always use the global index, never a slice-local one.
     - When a point is selected from outside the displayed slice (for example from Summary), the slice switches to the one containing it. The as-solved anchor comes from `solvedResult` (OPT-V02) and always renders; when it lies outside the current slice it is drawn hollow, with the legend note "as solved (different slice)".
     - **Feasible means converged and every effective bound met.** "Every" includes unswept constraints, judged with OPT-V01's `constraintAttainment` against the **absolute** effective bound (pct thresholds are fractions on the raw point; see OPT-V01). The two modes mean different things by `converged`:
       - **online**: λ converged **and** every constraint met within the library's own tolerance (`|t| · tol · 10`, `online.rs:154`), so a breach here is at most that tolerance;
       - **ratebook**: only that the factor values stopped moving (max |Δfactor| over a CD pass < `cd_tolerance`, `grouped_py.rs:582-583`). There is no feasibility check at all. Codex saw a converged ratebook point with volume 4.968 against a minimum of 5.5.
       Haute forwards `converged` separately from the totals (`_optimiser_frontier.py:465`), so the check is haute's in both modes, and DetailCard's reason text names which kind of convergence failed or which bound is breached.
     - The line joins **feasible** points only. Non-converged points are drawn hollow; converged-but-breached points get a distinct cross marker labelled "breached". Both stay selectable and inspectable, with the reason in DetailCard, and neither is ever joined.
  3. Extract AveTab's focus/hover aria-live detail into `ChartFocusDetail`, used by both AveTab and the frontier.
  4. DetailCard:
     - the swept bound, slack and status, via OPT-V01's table;
     - `converged` and `iterations`;
     - **λ as the solver's multiplier, with the sign stated.** DetailCard shows λ exactly as the solver reports it, next to a **discrete trade-off** row labelled "Objective change per unit of <constraint> bound relaxed, to the next point in this slice".
       - The trade-off is Δobjective / Δrelaxation, where Δrelaxation is the change in the swept **bound** in the relaxing direction: `bound_next − bound` for a max constraint and `bound − bound_next` for a min constraint.
       - It uses the bound, not the achieved total, so the denominator is what the reviewer controls.
       - It is shown only between two **feasible** neighbours (converged, with every effective bound met) in the same slice whose bounds differ, and "—" otherwise.
       - Its copy says it is a discrete step across the frontier, in which other achieved totals may also move. It is **not** presented as a check of λ.
  5. A `ChartValuesTable` of the slice's points.
- **Risks.** The canvas-assurance e2e clicks point 2, so check that the default slice keeps it visible.

**Acceptance:**

- slices on a 2×3 grid;
- the single-constraint identity;
- axis labels and legend entries;
- the hollow non-converged marker;
- the trade-off sign for a min constraint and a max constraint (hand-calculated);
- "—" for identical bounds, non-converged neighbours and **converged-but-breached** neighbours: a fixture with converged=True and a breached bound, including one where only an **unswept** constraint is breached;
- the breached marker and its DetailCard reason;
- a sliced selection publishes the **global** point index (mutation-check by passing the slice-local index);
- the as-solved anchor is fixed and hollow off-slice;
- the line breaks at a non-converged point;
- the stepper stays within the slice;
- the values table;
- AveTab unchanged on `ChartFocusDetail`;
- the existing point-selection tests (`OptimiserPreview.test.tsx` 797-990) stay green.

**Dependencies:** Size L. **Depends on:** OPT-V01, OPT-V03, OPT-V04.

**Evidence:** Current code this package changes or relies on: `frontend/src/panels/optimiser/FrontierChart.tsx`, `frontend/src/panels/optimiser/DetailCard.tsx`, `frontend/src/panels/modelling/AveTab.tsx`, `frontend/src/panels/OptimiserPreview.tsx`, `frontend/src/utils/chartHelpers.ts`, `frontend/src/panels/optimiser/__tests__/FrontierChart.test.tsx`, `frontend/src/panels/modelling/__tests__/AveTab.test.tsx`, `src/haute/routes/_optimiser_frontier.py`, `frontend/src/panels/__tests__/OptimiserPreview.test.tsx`.

### OPT-V07 — Rates tab relativity chart and beeswarm made honest and responsive

**Why:** A ratebook reviewer can judge curve shape, the size of each change and the business at each level. The beeswarm states what it hides. Gaps closed: G08, G19, part of G16.

**Plan:**

- **Files.** `frontend/src/panels/modelling/GLMRelativitiesTab.tsx`, `frontend/src/panels/RelativityBars.tsx` *(new, extracted)*, `frontend/src/panels/modelling/FeatureBrowser.tsx`, `frontend/src/panels/modelling/FeatureDiagnosticTab.tsx`, `frontend/src/panels/optimiser/RatebookRatesTab.tsx`, `frontend/src/panels/optimiser/RatebookImpactBeeswarm.tsx`, `frontend/src/panels/optimiser/ratebookFactorTables.ts`, `frontend/src/panels/__tests__/GLMComponents.test.tsx`, `frontend/src/panels/optimiser/__tests__/RatebookRatesTab.test.tsx` *(new)*, `frontend/src/panels/optimiser/__tests__/RatebookImpactBeeswarm.test.tsx` *(new)*.
- **Backend changes.** None. `quote_count` per level already ships.
- **Frontend changes.**
  1. Extract the diverging-around-1.0 bar list from GLMRelativitiesTab into `RelativityBars`. The bars already use `--chart-above`/`--chart-below` (`GLMRelativitiesTab.tsx:18-19, 104`); what needs tokenising is the hard-coded `rgba(255,255,255,.15/.3)` at 95 and 118. `var(--accent)` stays on the sort toggle (62-63), which is not part of the extraction.
  2. Rates tab in the FeatureDiagnosticTab layout:
     - a FeatureBrowser of factors ranked by quote-weighted \|log rate\|, labelled "Rate spread" (how far the factor's rates move from 1.0), with search;
     - `RelativityBars` in banding order;
     - an aligned quote-count strip, following the AvE exposure-strip pattern;
     - focusable levels with a detail line;
     - a `ChartValuesTable` of Level | Rate | vs neutral 1.0 (%) | Quotes | Share.
     The "vs neutral 1.0" column is the rate relative to the base price (no rate change). The selection survives tab switches.
  3. Beeswarm:
     - ResponsiveChart replaces the 720 viewBox, and `min-w-[520px]` goes;
     - a "Showing top 8 of N factors" notice with a Top 8 / All toggle;
     - categorical factors get a legend note plus text in the focus detail, not grey alone;
     - a values table.
- **Risks.** High-cardinality factors need label thinning with `chartLabelIndices`. The FeatureBrowser adapter must not fake importance semantics.

**Acceptance:**

- `GLMComponents.test.tsx` stays green.
- New files: banding order kept; % change and share sum to 100; search; the truncation notice and toggle; no fixed minimum width; keyboard detail.
- Move the beeswarm assertions out of `OptimiserPreview.test.tsx` (520-707).

**Dependencies:** Size M. **Depends on:** OPT-V03.

**Evidence:** Current code this package changes or relies on: `frontend/src/panels/modelling/GLMRelativitiesTab.tsx`, `frontend/src/panels/modelling/FeatureBrowser.tsx`, `frontend/src/panels/modelling/FeatureDiagnosticTab.tsx`, `frontend/src/panels/optimiser/RatebookRatesTab.tsx`, `frontend/src/panels/optimiser/RatebookImpactBeeswarm.tsx`, `frontend/src/panels/optimiser/ratebookFactorTables.ts`, `frontend/src/panels/__tests__/GLMComponents.test.tsx`, `frontend/src/panels/__tests__/OptimiserPreview.test.tsx`.

**Wave 4 framing (lean re-cut).**

**Framing.** The optimiser chooses a scenario per quote: an **adjustment on top of a base price**, where scenario value 1.0 is the unadjusted base. It never sees the live, deployed pricing. So wave 4 only **describes what the optimiser chose**: which adjustments, where, and for which quotes. There is **no "current" reference, no change / dislocation / impact figure and no price column**; impact analysis is set up by analysts outside haute.

**Retained from the approved review rounds:** the file ownership and lease contract, the admission and measured-RSS gates, per-job latest-wins materialisation, the availability rules, the cardinality gate, and the Float32-ingest / Float64-aggregation precision rule.

**Specs first.** Before any wave-4 code, write the lifecycle, precision and statistical contracts below into the optimiser component specifications under `specs/`, with `tests/test_docs_accuracy.py` passing.

### OPT-V09A — Analysis-column side table and its ownership contract

**Why:** Keep the per-quote analysis columns, which the library drops from solve and apply output, in a durable side table, so the adjustments can be broken down by segment.

**Plan:**

- **Files.** `src/haute/_types.py`, `src/haute/_cache.py`, `src/haute/routes/_optimiser_input.py`, `src/haute/routes/_optimiser_service.py`, `src/haute/routes/_optimiser_artifacts.py`, `src/haute/routes/_optimiser_solver.py`, `src/haute/routes/_job_store.py`, `src/haute/routes/_optimiser_outcomes.py` *(new)*, `frontend/src/panels/OptimiserConfig.tsx`, the optimiser specs, `docs/building-models/nodes/optimiser.md`, `tests/test_optimiser_outcomes.py` *(new)*, `tests/test_job_store.py`.
- **Backend changes.**
  1. **Config keys (Q3, Ralph 25-Sep: the user chooses the frame and the columns).**
     - `analysis_input: str | None` names **any frame connected to the optimiser node**, resolved with the same machinery as `data_input` and `banding_source` (`_optimiser_side_input_ids`, `_resolve_optimiser_input_edge`, `_optimiser_input.py:378, 496`).
     - `analysis_columns: list[str]` picks columns from that frame. It is optional and capped at 12.
     - The chosen frame must contain the configured `quote_id` column, with the same dtype rules (`_invalid_quote_id_dtype_detail`).
     - Each column must be constant within a quote, validated loudly: a quote with two different values is a named error.
     - All of this is added to `OptimiserConfig`, the config key lists and the staleness key in `_cache.py`, so changing the frame or the columns makes the result stale.
     - **UI:** in the optimiser **Data** pane (the config is split into Data, Factors, Constraints, Solve and Export panes, `optimiserPanes.ts`), an "Analysis input" select listing the connected frames (defaulting to the data input), then a multi-select of that frame's columns from its schema, with help text saying they are used only for result breakdowns.
  2. **Two paths, depending on the chosen frame.**
     - *The frame is the data input*: carry the columns through input preparation, as below.
     - *The frame is a different connected input*: project it to `quote_id` + the analysis columns, reduce it to one row per quote, and stream it into the same `quote_analysis.parquet`.
       - **This is more than reusing the `banding_source` resolver.** Online setup executes only up to the data-input node (`_setup_execution_target_node_id`, `_optimiser_input.py:412-428`). A side input counts as consumed only if it is in that node's lineage (`_optimiser_service.py:3198-3201`), and `_optimiser_side_input_ids` preserves `banding_source` only in ratebook mode. As things stand, a separate analysis frame would be neither executed nor preserved in online mode. The package therefore also changes: the setup execution target (so the analysis frame's branch runs in both modes), the projection seeds (`_optimiser_solve_required_columns_by_node`, so only `quote_id` + the analysis columns are demanded from it), the seed/capture plan, and `_optimiser_side_input_ids` (so `analysis_input` is preserved in both modes). `extract_ratebook_factors` (`_optimiser_input.py:826`) is the model for the streamed extraction once the frame is available.
       - **Coverage rule:** a solve quote with no row in the analysis frame falls into a "Missing" level, and the count is shown in the Segments tab. Analysis-frame quotes that are not in the solve are ignored.
     - **Carry-through (data-input path).** Today both stages drop non-solver columns: the upstream column demand (`_optimiser_input.py:461`) and the validation projection (`_optimiser_input.py:757`), before the worker writes the projected frame (`_optimiser_service.py:~1527`). Extend both stages to retain the configured `analysis_columns`, and only those, through to the worker's parquet. The solver's own inputs (the grid build and the library call) still see solver columns only.
  2b. **Extraction.** In `_build_grid_from_parquet`, before the solver input is deleted, stream `quote_analysis.parquet` (`quote_id` + the analysis columns, one row per quote). The scan is projected and grouped with `group_by(quote_id).first()`, sunk with `sink_parquet` and never collected. The constant-within-quote check is a streamed `n_unique` per quote, reduced to a boolean before any collection.
  3. **Cardinality metadata.** Also record each column's `approx_n_unique` and maximum string byte length, for OPT-V11's gate.
  4. **Ownership contract**, as approved:
     - setup ownership, and explicit adoption into the completion `artifact_handles` map (`_optimiser_solver.py:956`);
     - `JobStore.lease` *(new; JobStore has no lease concept today)* defers deletion while a reader holds the file, and collection happens inside the lease;
     - cleanup on failure or cancel, and startup reaping, built on the existing `register_artifact_cleaner` / `detach_artifact_handle` (`_job_store.py`) and `reap_stale_optimiser_artifacts` (`_optimiser_artifacts.py:85`);
     - the file lives for the **24-hour job lifetime**, not the heavy-state lifetime (15 minutes idle, extended by `touch_heavy_objects` up to the job lifetime, `_job_store.py:506-535`), and is untouched by `_clear_result_data_after_user_action` and by frontier recompute.
  5. **Durable scenario grid.** Always, with or without analysis columns, record the immutable `scenario_grid: list[{optimal_step, scenario_value}]`. It is the complete sorted grid, taken from the solver input at setup, and it is returned on `OptimiserSolveResult` and kept in the job for its 24-hour lifetime. It is the **only** source for the bar set, "grid contains 1.0" and the range edges; nothing is inferred from the chosen rows. (Two grids, `[0.8,1.0,1.2,1.4]` and `[0.8,0.95,1.2,1.4]`, can produce identical apply frames.)
  6. With no analysis columns configured, no side table is written. The segment views show their empty state only when there are **neither** analysis keys **nor** factor keys (see OPT-V09C).

**Acceptance:**

- pytest: an **integration test through the real input preparation**, from a pipeline whose source has a non-solver `region` column configured as an analysis column: it reaches `quote_analysis.parquet`, and the solver inputs are unchanged (mutation: remove the demand extension and watch the test fail). One row per quote; a column that varies within a quote is rejected; adoption survives completion and `/apply`; a lease defers deletion across expiry; cancel leaves no file; reaping; recompute keeps the file.
- **Measured scaling:** the setup process's peak RSS with and without extraction, at 1M and 5M quotes × 10 steps, with thresholds in the spec. The largest size runs once, in the background.
- Mutation-check the lease deferral.
- pytest (side-input path): a separate connected frame supplies `region`, is joined by `quote_id`, and produces the same side table; a solve quote missing from it lands in "Missing" with the right count; a frame without `quote_id` is refused with an actionable message; changing `analysis_input` makes the result stale.
- pytest (side-input path, **online mode**): the separate frame is executed and preserved even though it is outside the data input's lineage; only `quote_id` + the analysis columns are demanded from it (mutation: drop the execution-target change and watch the test fail).
- vitest: the frame select lists only the connected inputs, the column multi-select follows the chosen frame's schema, and switching frames clears columns that no longer exist.

**Dependencies:** Size L (M for the data-input path alone; the side-input path's execution changes add the rest). **Depends on:** OPT-V04.

**Evidence:** Current code this package changes or relies on: `src/haute/_types.py`, `src/haute/_cache.py`, `src/haute/routes/_optimiser_input.py`, `src/haute/routes/_optimiser_service.py`, `src/haute/routes/_optimiser_artifacts.py`, `src/haute/routes/_optimiser_solver.py`, `src/haute/routes/_job_store.py`, `frontend/src/panels/OptimiserConfig.tsx`, `frontend/src/panels/optimiser/optimiserPanes.ts`, `docs/building-models/nodes/optimiser.md`, `tests/test_job_store.py`.

### OPT-V09B — Bounded queries over the chosen scenarios

**Why:** One backend primitive answers "which scenario did each quote get, with what objective and constraint values", for the as-solved result or a frontier point. **Online mode only** until OPT-V09C.

**Plan:**

- **Files.** `src/haute/routes/_optimiser_outcomes.py`, `src/haute/routes/_optimiser_artifacts.py`, `src/haute/routes/_optimiser_frontier.py`, `src/haute/routes/optimiser.py`, `src/haute/routes/_optimiser_limits.py`, `src/haute/_execution_admission.py`, the optimiser specs, `tests/test_optimiser_outcomes.py`, `tests/test_optimiser_apply.py`.
- **Backend changes.**
  1. `choice_query(job, target, reducer)` works over the target's apply result: the as-solved apply, or the point's apply artifact.
     - It uses the chosen `scenario_value`, the `optimal_step` index, and the objective and constraints at the chosen scenario. When analysis columns exist, it joins the OPT-V09A side table 1:1 on `quote_id`, with the row count asserted.
     - Callers pass a **reducer** (histogram, group-by or top-k) that runs in the lazy plan and returns a small result. No API returns the whole frame.
  2. Replace `_load_apply_result_artifact`'s eager `pl.read_parquet` (`_optimiser_artifacts.py:339, 364`) with `scan_parquet` inside a lease. In the same package, adapt **all three** callers: the `/apply` route (`optimiser.py:424`), point materialisation (`materialise_point_apply`, `_optimiser_frontier.py:1019`), and the preview builder that consumes the frame (`limited_apply_preview_payload`, `_optimiser_limits.py:70`), which becomes a lazy count plus a bounded `head(limit)`, collected inside the lease.
  3. **Admission** for each query, with its own estimate; over budget returns an actionable refusal. Single-flight is keyed by `(job, generation, target, query)`.
  4. **Point materialisation**, as approved:
     - `apply_from_grid` cannot be interrupted;
     - one materialisation per job at a time, with a latest-wins queue of depth 1 (a replaced waiter gets 409);
     - admission before `apply_from_grid` is called;
     - a shared result per point, where a disconnecting caller detaches only itself.
  5. **Availability.** The as-solved target is available for 24 hours. A point is available if its artifact exists, or while the grid is alive. Otherwise the response is a named 410.
  6. **Precision.** Totals are Float64 sums of the Float32 values the solver ingested. Summed objective and constraints at the chosen scenarios equal the solved totals.
  7. **Reuse check.** `ApplyOptimiser.with_explainer_columns` (`_optimiser_apply_explainability.py:194-202`) already emits per-quote `selected`, `is_baseline` and `linearised_<name>` columns. Before writing new reducers, check whether any Quotes-explorer column (OPT-V12) is already produced there; do not build a second derivation of the same value.

**Acceptance:**

- pytest: the reconciliation above; the join-count failure; admission refusal; single-flight; A/B/C rapid stepping; shared-subscriber disconnect; admission before `apply_from_grid` (the mock is not invoked); availability for a retained point, an unmaterialised point after eviction, and a point evicted as the ninth artifact; eviction during a read.
- **Measured scaling:** peak RSS for the histogram, group-by, index and top-k reducers at 1M and 5M quotes.

**Dependencies:** Size L. **Depends on:** OPT-V09A.

**Evidence:** Current code this package changes or relies on: `src/haute/routes/_optimiser_artifacts.py`, `src/haute/routes/_optimiser_frontier.py`, `src/haute/routes/optimiser.py`, `src/haute/routes/_optimiser_limits.py`, `src/haute/_execution_admission.py`, `tests/test_optimiser_apply.py`.

**Implemented (26 September 2026).** As specified in the optimiser low-level specification ("Bounded choice queries and point materialisation", "Measured choice-query memory"), with these choices the plan left open or that measurement changed:

- The 1:1 correspondence with the side table is asserted by a streamed key fingerprint (row count and hash sums, the OPT-V09A precedent) rather than a whole-table join. Only the group-by joins every quote; top-k and the row index attach analysis values to their own rows. A whole-table join held about 1 GiB in the server process at 5M quotes for reducers that never read the analysis values.
- "Index" is `RowIndex(offset, limit)`, a page of rows in the apply frame's quote order, which the Quotes explorer (OPT-V12) pages with.
- Admission takes each operation's own estimate (`WorkEstimate`): an estimate over the allowance is refused with its remedy, and the estimate, not the profile's whole budget, is reserved in flight, so several bounded queries run side by side.
- The point queue and query single-flight are `LatestWinsQueue` and `SharedFlights` (`src/haute/routes/_shared_flights.py`); `/apply` is asynchronous so a client that leaves detaches only itself.
- No route exposes `choice_query` yet; OPT-V10 to OPT-V12 add theirs over it.

### OPT-V09C — Ratebook per-quote choices (decisions: Q8, Q17)

**Why:** The wave 4 tabs describe ratebook solves from the library's canonical per-quote evaluation (price-contour 0.5.0), never from a haute reconstruction.

**Plan:**

- **Persist the canonical frames.** At solve completion, persist `RatebookResult.quote_results` as the job's apply artifact (the same handle, reaper and cleanup as online). For a frontier point, `RatebookOptimiser.evaluate(grid, contexts, frontier_factor_tables[i]).quote_results` is the point's apply artifact, materialised through `OPT-V09B`. Then remove the ratebook `/apply` gate (`_RATEBOOK_APPLY_DETAIL_UNSUPPORTED`, `_optimiser_frontier.py`), which exists only because no per-quote frame was available.
- **Two different "ratebook adjustments" exist (Q17).**
  - **Solver-evaluated:** the solver prices each quote at the grid step nearest its factor product, clamped to the grid ends (see "Price-contour contract (0.5.0)"). The objective and constraint totals, and the frontier, are computed on this.
  - **Deployed:** the Optimiser Apply node's ratebook path (`_apply_ratebook` in `_builders.py`) multiplies the looked-up factor rates into `optimised_factor` with **no** snapping to the grid, then clips it to the solve's `combined_factor_bounds` (Q17, decided: see Decisions).
  - They agree exactly when the product lands on a grid value inside the range, and at both edges: a quote whose product is past a grid end now deploys at that end, the step the solver evaluated. Inside the range they can differ by the rounding to the nearest step. The wave 4 tabs describe **the solver-evaluated step**, because the objective and constraint values at that step are the only ones the solve vouches for, together with a per-quote "deployed factor differs from evaluated step" flag and a portfolio count for the within-range rounding, so the gap is visible rather than hidden.
- The "deployed factor differs from evaluated step" flag compares the frame's `factor_product` (the f32 product the kernel used) with its `optimal_scenario_value`; it is never recomputed in haute.
- Ratebook reviewers already have the Rates tab (OPT-V07) and the beeswarm.
- The tests are per-quote and aggregate agreement for the objective and every constraint.
- **Factor segments.** A quote's factor memberships come from the separate banding source and are already persisted separately (`_optimiser_input.py:869, 893`). `choice_query` gains a second, leased 1:1 join on `quote_id` to those per-quote factor rows. A composite factor is grouped by **all** its constituent columns, and its level label matches the Rates tab.
  - Factor keys make the Segments view non-empty even with no analysis columns.
  - Test: a composite factor with **no analysis columns configured** produces factor segments whose counts sum to `n_quotes`.

**Acceptance:**

Agreement tests per quote and in aggregate for the objective and every constraint, for the as-solved result and a frontier point. Also: a quote whose deployed factor differs from its evaluated step (within-range rounding only, since the collar closed the past-the-edge gap) is flagged as "deployed factor differs from evaluated step", and the flagged count matches a hand count on a small fixture; a quote whose product lies past a grid edge is not flagged, because it deploys at the edge step.

**Dependencies:** Q8 and Q17 are decided (canonical evaluation; the collar, with the tabs describing the evaluated step). Needs price-contour 0.5.0. Depends on OPT-V09B.

**Evidence:** Current code this package changes or relies on: `src/haute/routes/_optimiser_frontier.py`, `src/haute/_builders.py` (`_apply_ratebook`).

**Implemented (26 September 2026).** As specified in the optimiser low-level specification ("Ratebook per-quote choices (OPT-V09C)", "Bounded choice queries and point materialisation", "Adjustment reports (OPT-V10)") and the frontend specification, with these choices the plan left open:

- The persisted apply frame must have exactly its mode's schema (`apply_frame_schema`; the ratebook one is price-contour's `quote_results_schema`); any other schema is a `ChoiceJoinError` naming the differences. The per-quote flag is the choice frame's `deployed_factor_differs` column (`factor_product != optimal_scenario_value` on a quote with neither clamp flag); the histogram, the analysis group-by and the factor segments count it per group, and the adjustment report states the portfolio count (`deployed_factor_differs`, `null` for online).
- A ratebook point's frame is materialised only while the solver, the quote grid and the factor contexts are held (they are slimmed together), and its evaluation must reproduce the point's frontier row exactly, else a `RuntimeError`: a mismatch would describe a different point.
- `/apply` for a ratebook point records the materialised point (its own factor tables) as the selection, as frontier select with `include_ratebook_tables` does, so the job's result never pairs a point's totals with the solve's tables.
- The factor breakdown is its own reducer, `FactorSegments(factor, limit)`, whose rows carry `level` (the Rates tab's `__factor_group__` label) rather than the constituent columns; the Segments tab (OPT-V11) builds on it.
- The frontend's `adjustmentsOffered` hook was removed rather than filled: every result now has a report, so the Adjustments tab and Summary's compact summary are always offered. The Quotes tab stays online-only in the frontend until OPT-V12, although `/apply` now serves ratebook detail.
- price-contour caches `quote_results` on the result, so a ratebook solve's frame stays resident with the heavy `solve_result` until heavy-state slimming (an online solve's is dropped once persisted).

### OPT-V10 — Adjustments tab: the distribution of chosen scenario values, including for the selected point

**Why:** Show, on real axes, how the optimiser adjusted the book relative to the base price: how many quotes (or how much of a weight) got each adjustment, for the as-solved result **and** for the selected frontier point. This is a description of the solution, not an impact analysis. Gaps closed: G05 (re-scoped: the adjustment distribution replaces dislocation), G06 (backend half).

**Plan:**

- **Files.** `src/haute/routes/_optimiser_adjustments.py` *(new)*, `src/haute/routes/_optimiser_solver.py`, `src/haute/routes/_frontier_point_summary.py`, `src/haute/routes/optimiser.py`, `src/haute/schemas.py`, `frontend/src/generated/api-contracts.generated.ts`, `frontend/src/api/client.ts`, `frontend/src/panels/HistogramChart.tsx` *(new, extracted from `modelling/ResidualsTab.tsx`)*, `frontend/src/panels/modelling/ResidualsTab.tsx`, `frontend/src/panels/optimiser/AdjustmentsTab.tsx` *(new)*, `frontend/src/panels/optimiser/SummaryTab.tsx`, `frontend/src/panels/OptimiserPreview.tsx`, `frontend/src/panels/modelling/__tests__/ValidationDistributionTabs.test.tsx`, `frontend/src/panels/optimiser/__tests__/AdjustmentsTab.test.tsx` *(new)*, `tests/test_optimiser_adjustments.py` *(new)*, `tests/test_frontier_point_summary.py`.
- **Statistical contract (written into the spec first).**
  - **Quantity.** The chosen `scenario_value` per quote. The scenario grid is discrete, and OPT-V09A's durable `scenario_grid` supplies it, so the distribution is **one bar per grid value, including values nobody chose**: an exact count, with no binning, working unchanged for Float32 linspace grids and non-uniform grids such as `[0.8, 1.0, 1.3]`. Bars are keyed by `optimal_step` and labelled by the grid value.
  - **Reference.** A vertical "1.0 = base price (no adjustment)" line.
    - "Adjusted up" means sv > 1.0, "adjusted down" means sv < 1.0, and "unadjusted" means sv == 1.0, which exists only when the grid contains 1.0.
    - When the grid has no 1.0, "unadjusted" is omitted, and a note says the grid has no unadjusted scenario.
  - **Population.** Every solve quote; nothing is excluded.
  - **Weights.** Quote count by default. Optionally, any **non-negative** objective or constraint column, evaluated **at the chosen scenario** and labelled that way. A negative value, or a zero total weight, is a `diagnostics_errors` entry, and that weighting is not computed.
  - **Summary figures.**
    - The weighted and unweighted mean of sv.
    - Quantiles p5/p25/p50/p75/p95 by the inverted CDF (the lower quantile, which is always a grid value).
    - The share adjusted up, down and unadjusted.
    - The share at the grid's minimum and maximum (**"at the edge of the scenario range"**), which flags solutions pinned against the grid bounds.
  - Replaces `scenario_value_stats`, `scenario_value_histogram` and `_compute_scenario_value_stats`. OPT-V10 owns that contract transition: the point summaries, OPT-V02's fixtures and Summary change in the same package.
- **Backend changes.**
  1. A pure `_optimiser_adjustments.py`, a OPT-V09B histogram reducer, produces a strict `OptimiserAdjustmentReport` (`extra=forbid`).
  2. As-solved: computed at finalize.
  3. Frontier point: `POST /frontier/select` with `include_adjustments`, through OPT-V09B's materialisation. The report is cached per `(generation, point)` for the 24-hour job lifetime, served without the grid, invalidated on recompute, and bounded at 64 entries. Availability follows OPT-V09B.
- **Frontend changes.**
  1. Extract `HistogramChart` from ResidualsTab's `ResidualsHistogram`, with ResidualsTab visually unchanged. It supports categorical, discrete bars.
  2. The Adjustments tab has:
     - the bar chart with the base-price line;
     - a "Weight by" switch (Quotes | non-negative objective/constraint at the chosen scenario);
     - the quantile row;
     - the up / down / unadjusted and at-the-edge shares;
     - focusable bars with a detail line;
     - a values table.
  3. A selected point loads lazily, only while the tab is open. It reuses the AbortController and request-sequence flow. A browser abort only discards the response; a 409 "replaced" is not shown as an error. There are loading, error-with-Retry and 410 states.
  4. Summary swaps the 320×100 histogram for a compact up / down / at-edge summary linking to the tab.

**Acceptance:**

- pytest:
  - bar counts on a linspace Float32 grid and on `[0.8, 1.0, 1.3]`, compared to hand counts, including **zero-count grid values** rendered as empty bars;
  - an **available but unselected 1.0**: "unadjusted" is 0%, not omitted; grid `[0.8, 0.95, 1.2, 1.4]` omits it. Both have identical apply frames, which distinguishes grid-sourced from row-inferred logic;
  - **unselected endpoints**: the at-edge shares are 0 and the bars still span the full grid;
  - the report and the Quotes "at range edge" filter both work **after grid eviction**, from `scenario_grid`;
  - a grid without 1.0 omits "unadjusted";
  - weighted quantiles by the inverted CDF, compared to hand values;
  - the at-edge shares;
  - a negative weight and a zero total weight produce `diagnostics_errors`;
  - a point report equals the report computed from that point's apply;
  - a cached report is served after eviction, and a recompute invalidates it.
  - Mutation-check the up/down comparison and the negative-weight guard.
- vitest: axis labels ("Scenario value (1.0 = base price)"); the weight switch; lazy load; a fast stepper; Retry; the no-1.0 note. `ValidationDistributionTabs` stays green.

**Dependencies:** Size M. **Depends on:** OPT-V09B, OPT-V03.

**Evidence:** Current code this package changes or relies on: `src/haute/routes/_optimiser_solver.py`, `src/haute/routes/_frontier_point_summary.py`, `src/haute/routes/optimiser.py`, `src/haute/schemas.py`, `frontend/src/generated/api-contracts.generated.ts`, `frontend/src/api/client.ts`, `frontend/src/panels/modelling/ResidualsTab.tsx`, `frontend/src/panels/optimiser/SummaryTab.tsx`, `frontend/src/panels/OptimiserPreview.tsx`, `frontend/src/panels/modelling/__tests__/ValidationDistributionTabs.test.tsx`, `tests/test_frontier_point_summary.py`.

**Implemented (26 September 2026).** As specified in the optimiser low-level specification ("Adjustment reports (OPT-V10)") and the frontend specification, with these choices the plan left open:

- The negative-weight guard is per quote, not per step: `ScenarioHistogram` also counts each step's quotes whose objective or constraint value is below zero (`negative_<column>`), since a step's sum can be positive while one of its quotes is negative.
- "Served without the grid" means a cached point report needs neither the quote grid nor the point's apply artifact; each bar still carries its grid value.
- `POST /frontier/select` runs off the event loop (`run_until_disconnected`), because with `include_adjustments` it can wait for a point to materialise.
- Ratebook results had no report yet (filled by OPT-V09C): the as-solved report was `null` with no diagnostic, and the Adjustments tab was offered for online results only, through the hooks `_ratebook_adjustments` (`_optimiser_solver.py`) and `adjustmentsOffered` (`resultViews.ts`).
- Summary's compact adjustments summary appears wherever the workspace offers the Adjustments tab. The Quotes "at range edge" filter in the acceptance list belongs to OPT-V12, which reads the same `scenario_grid`.

### OPT-V11 — Segments tab: where the optimiser adjusted (AvE-style layout)

**Why:** For each analysis column (and each rating factor, once OPT-V09C lands), show per level: the quotes, the mean chosen scenario value (weighted or unweighted), the share adjusted up and down, and the share at the range edge. The layout follows FeatureDiagnosticTab. Gaps closed: G07 (re-scoped: adjustments by segment, with no before–after).

**Plan:**

- **Files.** `src/haute/routes/_optimiser_segments.py` *(new)*, `src/haute/routes/optimiser.py`, `src/haute/schemas.py`, `frontend/src/api/client.ts`, `frontend/src/panels/optimiser/SegmentsTab.tsx` *(new)*, `frontend/src/panels/modelling/FeatureBrowser.tsx`, `frontend/src/panels/modelling/FeatureDiagnosticTab.tsx`, `frontend/src/panels/OptimiserPreview.tsx`, `tests/test_optimiser_segments.py` *(new)*, `tests/test_api_contracts.py`, `frontend/src/panels/optimiser/__tests__/SegmentsTab.test.tsx` *(new)*.
- **Backend changes.**
  1. `POST /api/optimiser/segments {job_id, point_index?, key}` is a OPT-V09B group-by reducer.
     - Numeric keys get quantile bins (≤ 20) plus Missing; categorical keys get the top 15 by quotes plus Other.
     - It returns typed rows plus `diagnostics_errors`.
     - It needs an OpenAPI fingerprint entry and a Literal route constant.
  2. `GET /api/optimiser/segments/index` ranks the keys by the quote-weighted standard deviation of the per-level mean sv. It runs one key per lazy pass and is cached per `(job, generation, target)`.
  3. **Cardinality gate**, as approved:
     - admission refuses keys with `approx_n_unique > 1,800` (a heuristic margin) or > 256 bytes;
     - an **exact** post-group-by check refuses more than 2,000 levels, before truncation;
     - the memory guarantee is the measured-RSS gate;
     - rating factors (only with OPT-V09C) use their exact table row counts and the full composite key width, capped at 30 keys and listed as unavailable when over.
- **Frontend changes.**
  1. The browser is ranked by the index statistic, which is named in its header; it shows the keys unranked while the index loads.
  2. A per-level chart of mean sv against the 1.0 base line, with an aligned quote strip, `ChartFocusDetail` and a values table.
  3. The selection is shared with Rates by key. Results are cached per `(job, generation, target, key, weight)`.
  4. Empty state: "Add analysis columns in the optimiser config".

**Acceptance:**

- **Zero weight within a level.** When a level's total weight is 0 (for example, all chosen conversions in that segment are zero), its weighted mean and weighted shares are **unavailable** ("—", with a `diagnostics_errors` entry naming the level). Its quote count and unweighted figures are still shown.
- pytest:
  - a hand-calculated fixture with positive-weight and zero-weight levels: the weighted figures match hand values for the positive levels and are unavailable for the zero level, whose quote count is kept;
  - per-level quote counts sum to `n_quotes`;
  - per-level weighted means reconcile to the portfolio mean;
  - Missing and Other; tied quantile bin edges collapse into one bin; sparse levels;
  - an unknown key gives a 422; a point target; availability;
  - the index statistic matches a hand calculation.
  - Admission: `k0`…`k2000` (HLL estimate 1,980) is refused by the margin.
  - Separate exact-enforcement test: an admitted estimate is injected, 2,001 actual levels are refused before truncation, and 2,000 are accepted. Mutation: delete the guard.
- vitest: search and select; the detail line; the values table; abort on key switch; the empty state.

**Dependencies:** Size M–L. **Depends on:** OPT-V09B, OPT-V07, OPT-V10 (the schema, client and workspace edits land in order: OPT-V10, then OPT-V11, then OPT-V12).

**Evidence:** Current code this package changes or relies on: `src/haute/routes/optimiser.py`, `src/haute/schemas.py`, `frontend/src/api/client.ts`, `frontend/src/panels/modelling/FeatureBrowser.tsx`, `frontend/src/panels/modelling/FeatureDiagnosticTab.tsx`, `frontend/src/panels/OptimiserPreview.tsx`, `tests/test_api_contracts.py`.

### OPT-V12 — Quotes explorer over the chosen scenarios

**Why:** A reviewer can find a specific quote, or the quotes the optimiser pushed hardest, with each quote's chosen scenario and its objective and constraint values at that scenario. Sorting and search run **server-side over the full result**, not over the first 100 rows. Gaps closed: G11, G22.

**Plan:**

- **Files.** `src/haute/routes/optimiser.py`, `src/haute/routes/_optimiser_limits.py`, `src/haute/routes/_optimiser_outcomes.py`, `src/haute/schemas.py`, `frontend/src/api/client.ts`, `frontend/src/panels/SortableValuesTable.tsx` *(new, extracted from `modelling/GLMCoefficientsTab.tsx`)*, `frontend/src/panels/modelling/GLMCoefficientsTab.tsx`, `frontend/src/panels/optimiser/QuotesTab.tsx`, `tests/test_optimiser_apply.py`, `tests/fixtures/ui_contracts/optimiser_apply_response.json`, `frontend/src/panels/optimiser/__tests__/QuotesTab.test.tsx` *(new)*, `frontend/src/panels/__tests__/GLMComponents.test.tsx`.
- **Backend changes.**
  1. `OptimiserApplyRequest` gains:
     - `sort_by` (scenario value, objective, any constraint, any analysis column) and `descending`;
     - a `quote_id` prefix search;
     - filters (a scenario-value range, "at range edge", analysis-column equality);
     - `offset`, with the limit capped at `APPLY_PREVIEW_ROW_LIMIT`.
  2. The response carries typed column roles (id, scenario, objective, constraint, analysis) and `matched_row_count`.
  3. It is served through a OPT-V09B top-k reducer, with ties broken by `quote_id`. Deep offsets are refused beyond `offset + limit ≤ 10,000`, with a "narrow the filter" message.
- **Frontend changes.**
  1. Extract `SortableValuesTable` (`aria-sort`, search).
  2. QuotesTab:
     - formatted columns;
     - the scenario value shown with an up/down glyph against 1.0 (not colour alone);
     - presets "Highest adjustment", "Lowest adjustment" and "At range edge";
     - quote-id search;
     - a pager with "Showing a–b of M matching (of N)".
  3. The OPT-V02 cache identity includes the full query.

**Acceptance:**

- pytest: sort with ties; each filter; search; offset past the end; the deep-offset refusal; the cap; a point target; admission refusal.
- vitest: `aria-sort` cycles; the presets send the right request; reopening the tab is served from the cache. `GLMComponents.test.tsx` stays green.

**Dependencies:** Size M. **Depends on:** OPT-V09B, OPT-V02, OPT-V11.

**Evidence:** Current code this package changes or relies on: `src/haute/routes/optimiser.py`, `src/haute/routes/_optimiser_limits.py`, `src/haute/schemas.py`, `frontend/src/api/client.ts`, `frontend/src/panels/modelling/GLMCoefficientsTab.tsx`, `frontend/src/panels/optimiser/QuotesTab.tsx`, `tests/test_optimiser_apply.py`, `tests/fixtures/ui_contracts/optimiser_apply_response.json`, `frontend/src/panels/__tests__/GLMComponents.test.tsx`.

### OPT-V13 — End-to-end walk of every pane plus a single snapshot refresh

**Why:** Match and then exceed modelling's e2e coverage, and regenerate the screenshots once. Gaps closed: G21.

**Plan:**

- **Files.** `frontend/e2e/canvas-assurance.spec.ts`, `frontend/e2e/canvas-assurance.spec.ts-snapshots`, `.github/workflows/e2e-snapshots.yml`, `frontend/e2e/core-flows.spec.ts`.
- **Backend changes.** None.
- **Frontend changes (tests only).** After the existing solve, the e2e asserts:
  - the tablist and the provenance strip;
  - Summary attainment text (Bound, Slack, Status);
  - **select point 2, then Summary and DetailCard show the same status**;
  - the Adjustments bars and the base-price line;
  - a Segments level;
  - the Quotes "Highest adjustment" preset;
  - Convergence axes (every online solve records its history);
  - the focus view closing on Escape;
  - a ratebook solve's Rates chart.

  Optionally, add Lift and AvE assertions to `core-flows.spec.ts`, closing the modelling e2e gap. Regenerate the snapshots through `e2e-snapshots.yml`, never by hand.
- **Risks.** CI solve time, so keep the fixture book small.

**Acceptance:**

The named specs locally; CI runs the full suite.

**Dependencies:** Size S. **Depends on:** OPT-V01–OPT-V12, excluding OPT-V09C if Q8 chooses (b).

**Evidence:** Current code this package changes or relies on: `frontend/e2e/canvas-assurance.spec.ts`, `.github/workflows/e2e-snapshots.yml`, `frontend/e2e/core-flows.spec.ts`.

### OPT-PC02 — Cancellable or chunked point apply in price_contour

**Why:** `apply_from_grid` is one Rust call with no cancellation argument and no slicing API (`python/price_contour/apply.py:482-534`). Nothing in the library is cancellable today. The recent "Chunking ratebook" change (86e9e8c) chunks the ratebook factor-context build for memory, not the apply. `apply_lambdas_to_parquet_chunked` streams parquet to parquet in chunks and is a possible building block, but it is still one uninterruptible call, with no passthrough columns and no ratio constraints. A frontier-point materialisation, once started, cannot be stopped, so rapid stepping through frontier points wastes work. `OPT-V09B` bounds this in haute: one materialisation per job, a latest-wins queue of depth 1, and admission before the call. But it cannot abort the apply that is already running.

**Plan:** Add a cancel token (checked between quote chunks in Rust), or a chunked apply API that haute can drive and stop between chunks. Haute's V09B scheduler then cancels the running apply when a newer point replaces it.

**Acceptance:** Cancelling mid-apply returns promptly, with no partial artifact. A chunked apply's concatenated output equals the one-shot output exactly. Haute's rapid-stepping test shows at most one apply running, and the replaced one stopped.

**Dependencies:** Deferred until real books show that stepping cost matters. `OPT-V09B` works without it.

**Evidence:** `src/haute/routes/_optimiser_frontier.py`, `src/haute/routes/_optimiser_artifacts.py`.

## Price-contour behaviour the packages rely on

Beyond the 0.5.0 contract above, these library facts shape the remaining
packages (paths are in the `../price-contour` checkout):

- **λ sign convention** (`apply.py:284-294`; Rust `solver/argmax.rs`).
  Each quote picks the step that maximises objective + Σ sₖ·λₖ·constraintₖ,
  with sₖ = +1 for a min constraint and −1 for a max constraint; λ is kept
  ≥ 0. A positive λ can occur with positive slack in this discrete solve
  (Codex probed bound 5.0, achieved 5.18, λ 0.949), so haute never calls a
  positive λ "binding" (`OPT-V01`).
- **`converged` differs by mode.** Online: λ converged and every constraint
  met within `|t| · tol · 10`. Ratebook: only that the factor values stopped
  moving over a CD pass; no feasibility check (a six-quote probe converged
  with volume 4.968 against a minimum of 5.5). Haute judges feasibility
  itself (`OPT-V06`).
- **`apply_from_grid`** (`apply.py:482-534`) takes only the grid, the
  lambdas and the constraints: no passthrough columns (haute carries
  analysis columns in its own side table, `OPT-V09A`), no cancellation and no
  slicing (`OPT-PC02`).
- **Ratebook CD trace.** `per_factor_results` is empty after
  `RatebookResult.load` of a format-1 save and not persisted per quote;
  `OPT-V05` labels the trace "live solves only".
- **Precision.** Haute casts objective and constraint inputs to Float32;
  every reconciliation in this roadmap uses Float64 sums of those Float32
  values, as the library does.

## Screens today and gaps

### What each screen is today

**"The optimiser validation screen"** is the OptimiserPreview result panel (`frontend/src/panels/OptimiserPreview.tsx`, with its tabs in `frontend/src/panels/optimiser/`). App.tsx lazy-mounts it once a solve is cached. Its modelling counterpart is `frontend/src/panels/ModellingPreview.tsx`, whose ModalShell is labelled "Model validation".

A grep of optimiser code in the frontend and the backend finds no validation, holdout or out-of-sample concept. The only hits are input range checks and `_validate_artifact_payload`. The pre-solve chart view, `frontend/src/panels/OptimiserDataPreview.tsx`, is a separate panel. It becomes unreachable once a result exists.

| Aspect | Modelling evaluation (`ModellingPreview.tsx`) | Optimiser result (`OptimiserPreview.tsx`) |
|---|---|---|
| Shell | ModalShell Focus view; PreviewPanelFrame with the height remembered in `useUIStore`; `appearance="results"` tabs with `idPrefix`; a `role=tabpanel` `validation-workspace` body | PreviewPanelFrame only. No focus view, no remembered height, no idPrefix, no tabpanel. The body is `flex-1 overflow-auto px-4 py-3` |
| Provenance | A strip on every tab: "Diagnostics: Test/Validation/Training · N rows", plus an amber in-sample warning | None. The header shows "Converged · N iters · N quotes"; `mode`, `n_steps` and `warning` are not shown |
| Per-tab help | `VIEW_INTRODUCTIONS` title and description on each tab (ModellingPreview.tsx:72-115) | Only the λ tooltip (`optimiser/lambdaCopy.ts`) |
| Tabs | Summary, Coefficients, Relativities, Terms, Loss, Lift, Residuals, Features, AvE, PDP | Frontier, Summary, Rates (ratebook), Quotes (online), Convergence |
| Charts | All on `modelling/ChartScaffold.tsx`: ResponsiveChart, TwoChartLayout, ChartValueGrid, ChartLegend, ChartValuesTable, ChartEmptyState; `validation.css` container queries; 12 px axes | Hand-rolled fixed SVGs: Frontier 380×220 at 9 px; histogram 320×100 with no axes; Convergence 400×140, normalised, no axes; beeswarm on a 720 viewBox, so its text shrinks. Only ConvergenceChart uses `ChartSvg` |
| Comparison reference | Test against diagnostics, winner against baseline in tuning, actual against expected per level | Absolute totals only. `baseline_objective` and `baseline_constraints` are returned but never shown; their absence is pinned by tests |
| Constraint status | n/a | A colour dot and nothing else. Summary and DetailCard judge it against **different** bounds (verified: `SummaryTab.tsx:61-64` uses `constraints[name]`, while `DetailCard.tsx:120-124` uses `threshold_<name>`) |
| Breakdown | AvE and PDP with a shared FeatureBrowser, an exposure strip and an aria-live bin detail | None by segment or factor. The Rates tab is a bare Level/Rate table |
| Degraded diagnostics | A `diagnostics_errors` alert in the Summary | `frontier_error` only. Stats that are missing silently become `null` |
| Contract | Strict Pydantic, finite-JSON check, consistency check before publishing, generated TS types and runtime guards | Generated TS types, but frontier points and factor rows are open `dict[str, Any]`, parsed ad hoc (DetailCard throws on a bad field) |
| Tests | One test file per tab, `ValidationWorkspace.test.tsx`, and an e2e that asserts Summary and Model Info | No dedicated test file for Summary, DetailCard, Quotes, Rates or the beeswarm. Every fixture sets the distribution stats to `null`. The e2e touches only the Frontier tab |

Code facts confirmed during planning, which change what the drafts assumed:
- **"Current" is not the `scenario_value == 1.0` row.** price_contour picks one grid-wide step with the smallest `|scenario_value − 1.0|` in f32, taking the lowest index on a tie (`data.rs:96-119` in the checkout; see "Price-contour contract (0.5.0)"). The Scenario Expander uses `np.linspace(min, max, steps, float32)` (`src/haute/_node_apply.py:272`), and with an even step count the grid has no 1.0 row.
- **The in-memory grid does not last.** The heavy objects (`solver`, `solve_result`, `quote_grid`, `factors_df`, `ratebook_factor_contexts`) expire after 15 minutes idle (`src/haute/routes/_job_store.py:35-37`); `touch_heavy_objects` slides the window, up to the 24-hour job lifetime (`_job_store.py:506-535`). `/apply` drops all of them when there is no frontier (`src/haute/routes/optimiser.py:224-231`). Per-point apply artifacts are capped at 8 (`_optimiser_frontier.py:77`).
- **The solver input is deleted in `_build_grid`'s `finally`** (`src/haute/routes/_optimiser_service.py:3566-3596`), before the solve starts. Any extraction has to happen in `_build_grid_from_parquet` (:3598).
- **Ratebook factors are already persisted** as an artifact (`_optimiser_artifacts.py:208-310`, `optimiser_ratebook_factors`).
- **`validation.css` is imported only by `ModellingPreview.tsx:30`.** Both previews are lazy chunks.
- **Silent 0.0 baselines** exist at `optimiser.py:209-210`, `_optimiser_frontier.py:324-325, 410-411` and `useNodeResultsStore.ts:433-435` (`FAILED_SOLVE_RESULT`).
- **The ratebook CD trace** (`RatebookResult.per_factor_results`) carries only `total_objective` and `lambdas` per pass and factor, and it is empty after `RatebookResult.load` (`price_contour/ratebook.py:108-134, 264`).
- **`_DEFAULT_TOLERANCE = 1e-6`** is the λ-convergence tolerance, not a feasibility tolerance (`_optimiser_solver.py:84`).

Facts added after Codex plan review round 1:
- **Frontier point summaries drop unswept constraints.** `limited_frontier_payload(..., constraint_names=list(ranges.keys()))` at `_optimiser_frontier.py:1227-1230` and `_optimiser_solver.py:917-919`, and frontier select's re-derivation from the stored `constraint_names` (`_optimiser_frontier.py:311-318`), keep only the swept axes in `point_summaries[].constraints`, even though price_contour emits `threshold_*` and `total_*` for every constraint. The raw `points` rows still carry every column, and summary λ covers every constraint, but the typed summary the panes should read does not; the backend is fixed in OPT-V01.
- **The as-solved frontier marker moves.** It is read from the displayed `result` (`OptimiserPreview.tsx:485-486`), which becomes the selected point's result (`useNodeResultsStore.ts:461`).
- **A positive λ does not mean the constraint is tight.** Codex probed a converged point with bound 5.0, achieved 5.18 and λ 0.949. The solver is discrete, so a positive multiplier and positive slack can occur together. `lambdaCopy.ts` ("0 means not binding") is loose in the other direction too.
- **λ sign convention.** The library adds `+λ` for a min constraint and `−λ` for a max constraint (`price_contour/apply.py:284`).
- **`min_pct`/`max_pct` are live in the library, not in the UI.** Haute passes constraints to the library unvalidated (`_validate_config` does not inspect them), and only `_CONSTRAINT_THRESHOLD_KEYS` (`_optimiser_frontier.py:78`) names the pct kinds. The library sets the bound as baseline × fraction (`solver_py.rs:450-499`) and reports frontier thresholds as fractions. The constraint editor offers only min and max (`OptimiserConstraintSettings.tsx:34, 152`).
- **Ratebook results omit `n_quotes` and `n_steps`** (`_optimiser_solver.py:1219`).
- **Objective and constraint inputs are cast to Float32** (`_optimiser_input.py:760`). The library's baseline equals the Float32 values promoted to Float64 and then summed; a Polars Float32 sum differs in the 7th significant figure.
- **Point apply artifacts are looked up before the grid** (`_optimiser_frontier.py:995`), so a materialised point stays usable after the grid is evicted.
- **Nothing leases job-owned files to a reader.** JobStore deletes owned files when the job expires (`_job_store.py:284`), and completion builds a fresh `artifact_handles` map (`_optimiser_solver.py:956`). `_load_apply_result_artifact` reads the whole parquet eagerly (`_optimiser_artifacts.py:339, 364`), and has three callers (`optimiser.py:424`, `_optimiser_frontier.py:1019`, and the preview builder in `_optimiser_limits.py:70`).
- **Point materialisation cannot be cancelled.** It runs synchronously outside the parent lock and only handles duplicates afterwards (`_optimiser_frontier.py:1054`), so a browser abort does not stop the backend work.
- **Premium is not guaranteed to be a column.** The required columns are the configurable objective, scenario, ID and constraint columns (`_optimiser_input.py:725`).

### Gaps

One-to-one with the gap analysis. Several are closed in re-scoped form after the 25 September decisions: G01 is out of scope; G05 and G07 describe chosen adjustments rather than change.

| ID | Dimension | Severity | Backend? |
|---|---|---|---|
| OPT-G01 | Change vs current (baseline) not shown | high | small |
| OPT-G02 | Constraint attainment is a colour dot, with no bound, slack or binding | high | no |
| OPT-G03 | Summary and DetailCard disagree on met status for a selected point | high | no |
| OPT-G04 | No provenance; nothing says what the figures are computed on (no out-of-sample concept) | high | yes (provenance) |
| OPT-G05 | No dislocation / impact distribution; the histogram is unreadable | high | yes |
| OPT-G06 | Selecting a frontier point drops the stats grid and the Convergence tab | high | yes (per-point histogram) |
| OPT-G07 | No segment / factor before–after view (AvE analogue) | high | yes |
| OPT-G08 | Rates tab is a bare table: no relativity chart, no quote_count, no % change | medium | no |
| OPT-G09 | Frontier chart unreadable; multi-constraint cloud; no legend or trade-off readout | medium | no |
| OPT-G10 | Convergence has no axes; per-constraint λ and totals unused; no ratebook trace | medium | yes (ratebook) |
| OPT-G11 | Quotes tab is a raw, unsortable, capped dump | medium | yes |
| OPT-G12 | Shared modelling scaffolding unused | medium | no |
| OPT-G13 | No per-tab intros or help copy | medium | no |
| OPT-G14 | Accessibility: no tabpanel wiring; colour-only status; unlabelled SVGs | medium | no |
| OPT-G15 | No focus view or remembered height; fixed, non-stacking layout | medium | no |
| OPT-G16 | No values tables under charts | medium | no |
| OPT-G17 | State: tab not reset on a new solve; no Retry; `warning` hidden; unreachable empty state | low | no |
| OPT-G18 | Ratebook λ hidden on Summary but shown on DetailCard; clamp rate unexplained | low | no |
| OPT-G19 | Beeswarm silently truncates to 8 factors, text scales down, categorical factors all grey | low | no |
| OPT-G20 | Contract discipline: open dict rows, silent nulls, no `diagnostics_errors` | low | yes |
| OPT-G21 | Test coverage: no per-tab files, null-only fixtures, e2e touches only Frontier | medium | no |
| OPT-G22 | Quotes tab re-POSTs `/apply` on every open; input stats unreachable after a solve | low | no |

### Out of scope and not applicable

| Modelling feature | Why it does not carry over |
|---|---|
| Double lift, Lorenz, Gini | They measure how a predictor ranks observed outcomes. The optimiser's objective and constraints are model-expected values with no observed outcome. The nearest descriptive analogue is the distribution of chosen adjustments (OPT-V10); impact analysis is out of scope. |
| Residual histogram, actual-vs-predicted scatter | There are no actuals. |
| AvE semantics (A/E ratio) | There are no actuals. The **layout** (feature browser, per-level chart, exposure strip) is reused for the chosen adjustment by segment (OPT-V11). |
| PDP | There is no fitted model to vary. In ratebook mode the factor tables already are the per-level effect (OPT-V07). |
| Feature importance / SHAP | There are no learned attributions. The beeswarm's quote-weighted \|log rate\| is the existing analogue. |
| GLM inference (SE, Wald intervals, significance) | λ is a Lagrange multiplier from a dual solve, not a fitted coefficient, so Wald-style inference does not apply. This says nothing about how the solution varies with the book or the models (see Q9). |
| EBM terms | Not applicable. |
| Tuning details / "Use best as fixed parameters" | There is no hyper-parameter search. Picking a frontier point as the publish target is the analogous action, and it already lives in the Export pane. |
| Train vs eval loss with a best-iteration line | No eval set. Convergence is the analogue (OPT-V05). |
| k-fold CV selection spread | Re-solving per fold has no standard interpretation. |
| **Holdout / robustness validation (out of scope for this release)** | A decision about scope, not a claim that the solve carries no uncertainty. The figures are expected values from scoring models on one fixed book, so model error, mix drift and sampling variation are all real risks. A random holdout alone may not measure them well, and scaling absolute bounds to a sample needs a separate definition. The provenance strip must say the figures are model-expected, not observed. Tracked as Q9. |
| Modelling's "Training diagnostics are in-sample" wording | The wrong disclaimer for an optimiser. The accurate one is: "Expected values from the scoring models on the N solve quotes; not observed outcomes." |
| **Current vs optimised, dislocation and impact analysis (Ralph, 25-Sep: Q1 declined)** | The optimiser is an adjustment on top of a base price: it reapplies scenarios to that base and never sees the live, currently deployed pricing. A "Current → Optimised → Change" view would therefore compare against something the optimiser does not know. Analysts set up impact analysis elsewhere. The absence pins from e5da5f555 stay. |
| Export buttons in the results workspace | Modelling excludes them deliberately (SummaryTab test), and so does this plan. Parity means a `ChartValuesTable` disclosure under every chart. Any CSV belongs in the Export pane (`OptimiserPublishSection.tsx`), which is Q10. |

## Delivery

| Wave | Packages | Nature | Why this order |
|---|---|---|---|
| 1 | `OPT-V01`, `OPT-V02`, then `OPT-V03` | Correctness and the shared shell | Fixes the wrong constraint status, the vanishing views and the moving anchor before any rebuild. `OPT-V03` follows `OPT-V02` because both edit `OptimiserPreview.tsx`. |
| 2 | `OPT-V04`, then `OPT-V05`, `OPT-V06`, `OPT-V07` | Contract first, then charts | Typed rows land before the frontier rebuild; the charts reuse the extracted modelling components. |
| 3a | Spec updates for wave 4 (lifecycle, precision, adjustment statistics), then `OPT-V09A`, then `OPT-V09B` | Backend foundation | Specs first. Measured-RSS results gate the analytics. |
| 3b | `OPT-V10`, then `OPT-V11`, then `OPT-V12`, serially | Descriptive analytics | All three edit `schemas.py`, `client.ts` and the workspace. `OPT-V09C` runs alongside. |
| 4 | `OPT-V13`; the affected checks (targeted tests, `tsc -b --noEmit`, `tests/test_docs_accuracy.py`, the OpenAPI fingerprint); one Codex review of the branch diff; then the PR | e2e and snapshots once | One PR (Q13). |

`OPT-PC02` is deferred.

## Open questions

- **Q4 (resolved 26 September 2026):** quote count by default, with an
  optional non-negative objective or constraint column at the chosen
  scenario. See Decisions and `OPT-V10`.
- **Q5 (resolved 26 September 2026):** `record_history` is removed and every
  online solve records its history, bounded by `max_iter`. See Decisions and
  `OPT-V05`.
- **Q6, "Met" tolerance (`OPT-V01`):** the plan uses a strict comparison,
  with the signed slack % always shown and no "binding" label. Should a
  presentation tolerance apply? The solver's `tolerance` is a
  λ-convergence setting, not a feasibility one.
- **Q9, robustness checks:** out of scope for this release, as a scope
  choice, not a claim that the solve carries no uncertainty. Is any
  robustness view wanted later: an out-of-time or group split, or a
  stressed re-score?
- **Q10, CSV downloads:** should frontier points and per-quote choices be
  downloadable from the Export pane? The results panes keep the no-export
  rule either way.
- **Q11, inputs vs outputs:** the pre-solve `OptimiserDataPreview` becomes
  unreachable once a result exists. Add an "Inputs" tab to the result
  workspace?
- **Q12 (resolved 26 September 2026):** a grid without 1.0 only gets the
  Adjustments tab's "no unadjusted scenario" note; the Scenario Expander is
  unchanged. See Decisions.
- **Q15, interruptible apply:** see `OPT-PC02`.
- **Q17 (resolved 25 September 2026):** the Apply node clamps the combined
  factor to the scored grid range (`combined_factor_bounds`) and does not
  snap inside it; the wave 4 tabs describe the solver-evaluated step with a
  flag for within-range rounding. See Decisions and `OPT-V09C`.

## Planning and review record

- **Workflow (25 September 2026).**
  - Four readers mapped the modelling UI, the modelling backend, the
    optimiser UI and the optimiser backend.
  - A gap analysis found 22 gaps.
  - Three drafts followed, prioritising user value, reuse and contract
    first respectively.
  - A critic checked the drafts, and a synthesis combined them.
- **Codex plan review**, one thread (`01a0d960-a0b4-7e32-8cc5-8f0e05b12a9f`, local session state only):
  1. **Round 1: needs rework.**
     - Ratebook reconstruction was ruled out.
     - Memory bounds and artifact ownership needed defining.
     - λ ≠ 0 does not mean binding.
     - The dislocation statistics were undefined.
     - Unswept constraints were being dropped.
     - Cache identity was incomplete, and the as-solved marker moved.
     - Segment ranking had no data source.
     - The precision contract was missing.
     - `min_pct`/`max_pct` are live.
  2. **Round 2: changes requested.** The price column needed a data path. Materialisation cannot be cancelled. Converged is not feasible. Statistical populations had to be defined across panes. Segment memory needed bounding. Contract changes had to land with their callers.
  3. **Round 3: changes requested.** The scenario step was ambiguous for Float32 and non-uniform grids. The HLL cardinality estimate can undercount. Composite factor tables are uncapped.
  4. **Round 4: changes requested.** Units were mislabelled. The exact-limit test was untested. The memory margin was overstated.
  5. **Round 5: approved.**
  6. **Round 6 (lean re-cut): changes requested.** Analysis columns were projected away before extraction. The grid needed a durable source. The ratebook factor join was missing. A zero-weight segment was undefined. Comparison wording remained.
  7. **Round 7: approved.**
- **Verification against the code (25 September 2026, after round 7).**
  Three read-only agents checked every claim against haute's frontend,
  haute's backend and the price_contour source checkout, with one small
  ratebook probe. The spec was corrected as follows:
  - The per-quote ratebook frame already existed inside the library, so the
    recommendation for Q8 moved from (a′) to (a).
  - Frontier `threshold_*` is a fraction for pct constraints (OPT-V01,
    OPT-V06).
  - `clamp_rate` was defined: a search-space diagnostic.
  - The deployed ratebook factor is not snapped to the grid; Q17 added.
  - Online and ratebook `converged` differ (OPT-V06).
  - OPT-V09A's side-input path needs execution-plan changes in online mode.
  - Two more swept-only call sites (OPT-V01) and a third apply-artifact
    reader (OPT-V09B).
  - OPT-V02's tab reset and test claims corrected; OPT-V01 now covers
    `LAMBDA_LABEL` and every test that pins old behaviour.
  - Smaller file and line corrections throughout, and haute's `.venv` moved
    from a stale price_contour 0.2.7 to an editable install of the
    checkout.
- **Existing bugs the review confirmed**, all covered by packages above:
  - Summary and DetailCard judge constraints against different bounds (`OPT-V01`).
  - Frontier point summaries drop unswept constraints (`OPT-V01`).
  - The as-solved frontier marker moves when a point is selected (`OPT-V02`, `OPT-V06`).
  - Converged-but-breached points are drawn as ordinary points (`OPT-V06`).
  - Silent 0.0 baselines (`OPT-V04`).
  - The Quotes tab re-POSTs `/apply` on every open (`OPT-V02`).
