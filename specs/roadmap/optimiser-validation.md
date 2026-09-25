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

**Picking this up.** The next session has `price_contour` installed locally
for editing. The haute-only packages (`OPT-V01` onwards) need no product
decision and can start at once. The price_contour packages (`OPT-PC01` to
`OPT-PC03`) and the ratebook half of wave 4 (`OPT-V09C`) depend on the
choice described in "Price-contour context" below. Everything ships as
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
- **Pending:** ratebook per-quote results (Q8), taken up in the
  price_contour session. See "Price-contour context".

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
| OPT-PC01 | Decision | P3 | price_contour evaluates ratebook solutions per quote (or exposes its factor-product-to-step rule) so haute can show ratebook adjustments per quote. |
| OPT-PC02 | Deferred | P3 | price_contour's point apply can be cancelled or chunked, so rapid frontier stepping stops wasted work. |
| OPT-PC03 | Planned | P3 | `clamp_rate` has a documented, verified definition that haute's help copy can quote. |

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
  1. **Keep all constraints in point summaries.** Split `constraint_names`, meaning **every** configured constraint, from `swept_axes`, meaning `ranges.keys()`. Pass both through `limited_frontier_payload` (`_optimiser_frontier.py:1227`) and `_frontier_point_summary.py:115`, so a selected point carries `threshold_*`, `total_*` and λ for unswept constraints too.
  2. **`min_pct`/`max_pct` are supported, so there is no deletion branch.** The effective absolute bound is `pct × baseline_total` of that constraint. The backend returns it, as `effective_bounds: {name: {kind, bound}}` on the solve result and on each point summary, so the frontend never re-derives it.
- **Frontend changes.**
  1. Replace `isConstraintMet(type, _ratio, abs, thr)` with a pure `constraintAttainment({kind, bound, achieved})`. It returns `{kind, bound, achieved, slack, slackPct, status: 'met'|'breached'}`.
     - `met` is a strict comparison on the Float64 values the backend returns. The signed `slackPct` is always shown, so "Breached by 0.01%" is readable rather than just red.
     - There is **no "binding" column.** λ is displayed separately as "λ (multiplier)", with help text saying it is the solver's Lagrange multiplier on this constraint's term. A positive λ can occur together with positive slack in this discrete solve.
     - A missing bound is a thrown contract error, not a coloured state.
  2. Add a store selector, `effectiveConstraintBounds`. It returns the displayed result's backend `effective_bounds`, and both panes read it. This removes the two divergent derivations and the `pointThreshold ?? spec ?? 0` fallback.
  3. `ConstraintAttainmentTable` shows Constraint | Kind | Bound | Achieved | Slack | Status | λ. Status is a text chip plus an icon, with colour as a secondary cue. Use it in both panes.
  4. Show λ for ratebook results on Summary as well, dropping the `mode !== 'ratebook'` gate so it matches DetailCard.
  5. Rewrite `LAMBDA_HELP` in `lambdaCopy.ts`: no "0 means not binding" and no "gained per unit relaxed" wording. See OPT-V06 for the signed trade-off.
- **Risks.** Contract change on point summaries: regenerate the goldens here, or fold this change into OPT-V04. There are no silent fallbacks.

**Acceptance:**

- `optimiser/__tests__/constraintAttainment.test.ts` *(new)*: min and max at, above and below the bound; slack sign; non-finite input throws.
- `SummaryTab.test.tsx` *(new)* and `DetailCard.test.tsx` *(new)*: a row with positive λ and positive slack reads "Met" and is not labelled binding.
- pytest: a two-constraint solve sweeping only one. The selected point's summary contains the unswept constraint's bound, total and λ. Mutate `constraint_names` back to `ranges.keys()` and confirm the test fails.
- pytest: a `min_pct` constraint's `effective_bounds` equals pct × baseline total, and a real prebuilt-grid solve produces it.
- G03 regression in `panels/__tests__/OptimiserPreview.test.tsx`: select a point whose swept threshold differs from the solved one; both panes print the same bound and status. Mutate the selector back to `cached.constraints` and confirm the test fails.
- Update `optimiserHelpers.test.ts:48-49`, which pins the "red" behaviour.

**Dependencies:** Size M. **Depends on:** none.

**Evidence:** Current code this package changes or relies on: `frontend/src/panels/optimiser/optimiserHelpers.ts`, `frontend/src/panels/optimiser/SummaryTab.tsx`, `frontend/src/panels/optimiser/DetailCard.tsx`, `frontend/src/panels/optimiser/lambdaCopy.ts`, `frontend/src/stores/useNodeResultsStore.ts`, `src/haute/routes/_optimiser_frontier.py`, `src/haute/routes/_frontier_point_summary.py`, `src/haute/routes/optimiser.py`, `frontend/src/panels/modelling/__tests__/SummaryTab.test.tsx`, `frontend/src/panels/__tests__/OptimiserPreview.test.tsx`, `frontend/src/panels/optimiser/__tests__/optimiserHelpers.test.ts`.

### OPT-V02 — Selected-point integrity, state handling and shared fixtures

**Why:** Selecting the publish target never removes what the reviewer is looking at, a new solve resets the tab, errors are recoverable, and the distribution UI gets tested for the first time. Gaps closed: G06 (frontend half), G17, G21, part of G22.

**Plan:**

- **Files.** `frontend/src/panels/OptimiserPreview.tsx`, `frontend/src/panels/optimiser/SummaryTab.tsx`, `frontend/src/panels/optimiser/ConvergenceChart.tsx`, `frontend/src/panels/optimiser/QuotesTab.tsx`, `frontend/src/stores/useNodeResultsStore.ts`, `frontend/src/panels/optimiser/__tests__/fixtures.ts` *(new)*, `frontend/src/panels/__tests__/OptimiserPreview.test.tsx`, `frontend/src/panels/__tests__/OptimiserPreview.storeIntegration.test.tsx`.
- **Backend changes.** Add `frontier_generation` to `OptimiserFrontierResponse` (`schemas.py:3122`) and the select response, regenerate the contracts and goldens, and add a pytest that it increments on recompute. OPT-V04 later types the points, but the generation lands here with its first caller.
- **Frontend changes.**
  1. Move the stats grid out of the histogram guard, so a point's `scenario_value_stats` (from its `sv_*` columns) render when its histogram is null. Label the stats "Frontier point N" or "As solved".
  2. Add `solvedResult` (the cached original result) to `OptimiserPreviewData`.
     - Convergence availability is decided from `solvedResult`, so the tab no longer vanishes on point select.
     - It shows the as-solved history under "History is recorded for the solved result; frontier point N: converged/not, K iterations".
  3. Reset the tab to its default on a `jobId` change, **not** on `[nodeId, result]`. `applyFrontierPointSummary` creates a new result on every stepper press (`useNodeResultsStore.ts:447-452`).
  4. Show `result.warning` (the non-convergence reason) as an amber strip in the preview.
  5. Delete the unreachable "No frontier data available" branch (`OptimiserPreview.tsx:467-473`).
  6. Add Retry buttons to the Quotes and Rates error states.
  7. Cache `/apply` responses in the store under a **full request identity**: `(jobId, frontierGeneration, target: 'solved'|pointIndex, canonical query)`, where the query is sort, filters, search, offset and limit (OPT-V12 extends it). Keep at most 16 entries (LRU) and clear them on a new job or a frontier recompute. Every response is checked against the identity current when it arrives; a late response from an earlier job, generation or query is dropped. `frontierGeneration` comes from the frontier response (added in this package).
  9. `solvedResult` also anchors the as-solved frontier marker (fixing `OptimiserPreview.tsx:485-486`), so the marker no longer moves when a point is selected.
  8. Add shared fixtures:
     - online with non-null stats and histogram, frontier points carrying `threshold_*`, `converged` and `iterations`, and history with `lambdas` and `total_constraints`;
     - ratebook with factor tables and `quote_count`.
     Replace the null fixtures at `OptimiserPreview.test.tsx:162-163` and `storeIntegration.test.tsx:65-66`.
- **Risks.** The Convergence wording must not imply that the history belongs to the point.

**Acceptance:**

- The stats persist after point select.
- Pressing the stepper while on Convergence keeps Convergence.
- Pressing the stepper does not reset the tab; a new `jobId` does.
- Retry refetches.
- Reopening Quotes makes no second call.
- After a frontier recompute, the same point index refetches.
- A late response from an earlier job or generation is discarded.
- The as-solved marker keeps its position after a point is selected.
- The warning is visible.
- Rewrite the fallback tests (709-790) that assumed Convergence disappears.

**Dependencies:** Size M. **Depends on:** none.

**Evidence:** Current code this package changes or relies on: `frontend/src/panels/OptimiserPreview.tsx`, `frontend/src/panels/optimiser/SummaryTab.tsx`, `frontend/src/panels/optimiser/ConvergenceChart.tsx`, `frontend/src/panels/optimiser/QuotesTab.tsx`, `frontend/src/stores/useNodeResultsStore.ts`, `frontend/src/panels/__tests__/OptimiserPreview.test.tsx`, `frontend/src/panels/__tests__/OptimiserPreview.storeIntegration.test.tsx`, `src/haute/schemas.py`.

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
  3. Move ModellingPreview onto the shell with no behaviour change. Share the diagnostics-set label helper, which removes the duplication with `modelling/SummaryTab.tsx:227-229` and guards the `evaluation!` non-null assertion.
  4. OptimiserPreview on the shell:
     - ariaLabel "Optimiser validation", `idPrefix` "optimiser-preview", accent `var(--warning-strong)`;
     - add `optimiserPreviewHeight` to `useUIStore`;
     - keep `HeaderPointStepper` as a header action.
  5. `OPTIMISER_VIEW_INTRODUCTIONS` for Frontier, Summary, Rates, Quotes and Convergence. **Clamp-rate copy waits for Q7.**
  6. Provenance strip: "Online|Ratebook · N quotes × M scenario steps · As solved|Frontier point i of N · Expected values from the scoring models on the solve quotes; not observed outcomes."
  7. FrontierTab stacks the chart above the detail card below the 640 px container breakpoint. Replace the 9–11 px uppercase labels with the modelling type scale.
- **Risks.** Keep the extraction mechanical. The selected-row colour in modelling depends on the token default. The canvas-assurance snapshots will change; they are regenerated in OPT-V13.

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
  2. Return `input_summary` (`data_source`, `source_file`, `graph_fingerprint`, solver settings) on `OptimiserSolveResult`. Today it is built only for the artifact (`optimiser.py:526-541`).
  3. Add `diagnostics_errors: list[{diagnostic, error_type, message}]`. Populate it where stats currently return `None` silently (`_optimiser_solver.py:159-168`) and where the frontier fails, keeping `frontier_error` in the list.
  4. Remove the `.get(..., 0.0)` and `{}` baseline fallbacks (`optimiser.py:209-210`, `_optimiser_frontier.py:324-325, 410-411`). A missing baseline is an error, not a zero.
  5. Run the finite-JSON walk on the optimiser status payload, mirroring the modelling status route's `_result_finite_validated`.
  6. Regenerate the contracts, the goldens and the OpenAPI fingerprint.
- **Frontend changes.**
  1. Delete DetailCard's ad-hoc `optionalPointNumber` parsers (13-58) and the ad-hoc factor parsing, in favour of generated types and one guard. The throw paths go with them, so no error boundary is needed.
  2. `FAILED_SOLVE_RESULT` (`useNodeResultsStore.ts:431-435`) stops fabricating a zero baseline.
  3. The provenance strip adds the source.
  4. A "Diagnostics issues" `role=alert` in Summary, reusing the modelling pattern (`modelling/SummaryTab.tsx:256-309`).
- **Risks.**
  - The price_contour point columns are dynamic. Enumerate them from a real solve before typing them as maps.
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
  1. Map `RatebookResult.per_factor_results` into a typed `ratebook_cd_trace: [{cd_iteration, factor, total_objective, lambdas}]`, replacing the hard-coded `history=None` for ratebook (`_optimiser_solver.py` ~1224, `_optimiser_frontier.py` ~474).
  2. The trace carries objective and λ only. It has no constraint totals, and it is empty after load, so label it "live solves only".
  3. Cap its length the way `loss_history` is capped.
  4. Default for `record_history`: see Q5.
- **Frontend changes.**
  1. Extract `LossTabChart` into a generic `IterationLinesChart`. It takes a series list, an optional vertical marker, optional horizontal reference lines, and a linear or log y-axis. It is built on ResponsiveChart, ChartValueGrid and ChartLegend. LossTab becomes an adapter.
  2. Convergence as small multiples, each on its own real axis:
     - the objective;
     - the maximum λ change (log scale, with 0 clamped and a note);
     - constraint totals, with dashed bound lines and a first-feasible marker from `all_constraints_satisfied`;
     - λ per constraint.
  3. For ratebook: the objective by CD pass, coloured by factor.
  4. A `ChartValuesTable` replaces the ad-hoc iterations table.
  5. Empty state: "History was not recorded; enable Record history in the Solve pane" (`OptimiserConfig.tsx:111, 696`).
- **Risks.** `per_factor_results` is internal to price_contour, so pin its shape with a test against the installed version.

**Acceptance:**

- The LossTab and LossChart tests stay unchanged. Mutate the adapter to prove they exercise it.
- Convergence: real tick values; bound lines; a zero λ change on the log axis; the ratebook view; the empty state.
- pytest: a real small ratebook solve gives a finite trace whose last objective equals `total_objective`.

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
     - **Feasible means converged and every effective bound met.** "Every" includes unswept constraints, judged with OPT-V01's `constraintAttainment`, because `converged=True` can coexist with a breached bound. Codex saw a converged ratebook point with volume 4.968 against a minimum of 5.5, and haute forwards `converged` separately from the totals (`_optimiser_frontier.py:459`).
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
  1. Extract the diverging-around-1.0 bar list from GLMRelativitiesTab into `RelativityBars`, with chart tokens replacing `var(--accent)`.
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
     - **UI:** in OptimiserConfig, an "Analysis input" select listing the connected frames (defaulting to the data input), then a multi-select of that frame's columns from its schema, with help text saying they are used only for result breakdowns.
  2. **Two paths, depending on the chosen frame.**
     - *The frame is the data input*: carry the columns through input preparation, as below.
     - *The frame is a different connected input*: resolve it as a side input (as `extract_ratebook_factors` does for `banding_source`, `_optimiser_input.py:826`), project it to `quote_id` + the analysis columns, reduce it to one row per quote, and stream it into the same `quote_analysis.parquet`.
       - **Coverage rule:** a solve quote with no row in the analysis frame falls into a "Missing" level, and the count is shown in the Segments tab. Analysis-frame quotes that are not in the solve are ignored.
     - **Carry-through (data-input path).** Today both stages drop non-solver columns: the upstream column demand (`_optimiser_input.py:461`) and the validation projection (`_optimiser_input.py:757`), before the worker writes the projected frame (`_optimiser_service.py:1517`). Extend both stages to retain the configured `analysis_columns`, and only those, through to the worker's parquet. The solver's own inputs (the grid build and the library call) still see solver columns only.
  2b. **Extraction.** In `_build_grid_from_parquet`, before the solver input is deleted, stream `quote_analysis.parquet` (`quote_id` + the analysis columns, one row per quote). The scan is projected and grouped with `group_by(quote_id).first()`, sunk with `sink_parquet` and never collected. The constant-within-quote check is a streamed `n_unique` per quote, reduced to a boolean before any collection.
  3. **Cardinality metadata.** Also record each column's `approx_n_unique` and maximum string byte length, for OPT-V11's gate.
  4. **Ownership contract**, as approved:
     - setup ownership, and explicit adoption into the completion `artifact_handles` map (`_optimiser_solver.py:956`);
     - `JobStore.lease` defers deletion while a reader holds the file, and collection happens inside the lease;
     - cleanup on failure or cancel, and startup reaping;
     - the file lives for the **24-hour job lifetime**, not the 15-minute heavy-state lifetime, and is untouched by `_clear_result_data_after_user_action` and by frontier recompute.
  5. **Durable scenario grid.** Always, with or without analysis columns, record the immutable `scenario_grid: list[{optimal_step, scenario_value}]`. It is the complete sorted grid, taken from the solver input at setup, and it is returned on `OptimiserSolveResult` and kept in the job for its 24-hour lifetime. It is the **only** source for the bar set, "grid contains 1.0" and the range edges; nothing is inferred from the chosen rows. (Two grids, `[0.8,1.0,1.2,1.4]` and `[0.8,0.95,1.2,1.4]`, can produce identical apply frames.)
  6. With no analysis columns configured, no side table is written. The segment views show their empty state only when there are **neither** analysis keys **nor** factor keys (see OPT-V09C).

**Acceptance:**

- pytest: an **integration test through the real input preparation**, from a pipeline whose source has a non-solver `region` column configured as an analysis column: it reaches `quote_analysis.parquet`, and the solver inputs are unchanged (mutation: remove the demand extension and watch the test fail). One row per quote; a column that varies within a quote is rejected; adoption survives completion and `/apply`; a lease defers deletion across expiry; cancel leaves no file; reaping; recompute keeps the file.
- **Measured scaling:** the setup process's peak RSS with and without extraction, at 1M and 5M quotes × 10 steps, with thresholds in the spec. The largest size runs once, in the background.
- Mutation-check the lease deferral.
- pytest (side-input path): a separate connected frame supplies `region`, is joined by `quote_id`, and produces the same side table; a solve quote missing from it lands in "Missing" with the right count; a frame without `quote_id` is refused with an actionable message; changing `analysis_input` makes the result stale.
- vitest: the frame select lists only the connected inputs, the column multi-select follows the chosen frame's schema, and switching frames clears columns that no longer exist.

**Dependencies:** Size M. **Depends on:** OPT-V04.

**Evidence:** Current code this package changes or relies on: `src/haute/_types.py`, `src/haute/_cache.py`, `src/haute/routes/_optimiser_input.py`, `src/haute/routes/_optimiser_service.py`, `src/haute/routes/_optimiser_artifacts.py`, `src/haute/routes/_optimiser_solver.py`, `src/haute/routes/_job_store.py`, `frontend/src/panels/OptimiserConfig.tsx`, `docs/building-models/nodes/optimiser.md`, `tests/test_job_store.py`.

### OPT-V09B — Bounded queries over the chosen scenarios

**Why:** One backend primitive answers "which scenario did each quote get, with what objective and constraint values", for the as-solved result or a frontier point. **Online mode only** until OPT-V09C.

**Plan:**

- **Files.** `src/haute/routes/_optimiser_outcomes.py`, `src/haute/routes/_optimiser_artifacts.py`, `src/haute/routes/_optimiser_frontier.py`, `src/haute/routes/optimiser.py`, `src/haute/routes/_optimiser_limits.py`, `src/haute/_execution_admission.py`, the optimiser specs, `tests/test_optimiser_outcomes.py`, `tests/test_optimiser_apply.py`.
- **Backend changes.**
  1. `choice_query(job, target, reducer)` works over the target's apply result: the as-solved apply, or the point's apply artifact.
     - It uses the chosen `scenario_value`, the `optimal_step` index, and the objective and constraints at the chosen scenario. When analysis columns exist, it joins the OPT-V09A side table 1:1 on `quote_id`, with the row count asserted.
     - Callers pass a **reducer** (histogram, group-by or top-k) that runs in the lazy plan and returns a small result. No API returns the whole frame.
  2. Replace `load_apply_artifact`'s eager read (`_optimiser_artifacts.py:364`) with `scan_parquet` inside a lease. In the same package, adapt the `/apply` callers (`optimiser.py:424`, `_optimiser_limits.py:70`) to a lazy count plus a bounded `head(limit)`, collected inside the lease.
  3. **Admission** for each query, with its own estimate; over budget returns an actionable refusal. Single-flight is keyed by `(job, generation, target, query)`.
  4. **Point materialisation**, as approved:
     - `apply_from_grid` cannot be interrupted;
     - one materialisation per job at a time, with a latest-wins queue of depth 1 (a replaced waiter gets 409);
     - admission before `apply_from_grid` is called;
     - a shared result per point, where a disconnecting caller detaches only itself.
  5. **Availability.** The as-solved target is available for 24 hours. A point is available if its artifact exists, or while the grid is alive. Otherwise the response is a named 410.
  6. **Precision.** Totals are Float64 sums of the Float32 values the solver ingested. Summed objective and constraints at the chosen scenarios equal the solved totals.

**Acceptance:**

- pytest: the reconciliation above; the join-count failure; admission refusal; single-flight; A/B/C rapid stepping; shared-subscriber disconnect; admission before `apply_from_grid` (the mock is not invoked); availability for a retained point, an unmaterialised point after eviction, and a point evicted as the ninth artifact; eviction during a read.
- **Measured scaling:** peak RSS for the histogram, group-by, index and top-k reducers at 1M and 5M quotes.

**Dependencies:** Size L. **Depends on:** OPT-V09A.

**Evidence:** Current code this package changes or relies on: `src/haute/routes/_optimiser_artifacts.py`, `src/haute/routes/_optimiser_frontier.py`, `src/haute/routes/optimiser.py`, `src/haute/routes/_optimiser_limits.py`, `src/haute/_execution_admission.py`, `tests/test_optimiser_apply.py`.

### OPT-V09C — Ratebook per-quote choices (decision: Q8)

**Why:** Ratebook solves return no per-quote frame, so the wave 4 tabs cannot describe them without a canonical per-quote evaluation from price_contour.

**Plan:**

- A ratebook solve has no per-quote frame, and haute refuses to substitute the online apply (`_optimiser_frontier.py:89`, `_builders.py:1914`).
- The options are: **(a)** a canonical per-quote evaluation primitive in price_contour (OPT-PC01), persisted while the grid is alive; **(a′)** the library exposes only its factor-product → step rule and haute reads the values from the in-memory grid during the solve; or **(b)** the Adjustments, Segments and Quotes tabs stay **online-only**, with an explicit "not available for ratebook solves" state.
- Ratebook reviewers already have the Rates tab (OPT-V07) and the beeswarm.
- Under (a), the tests are per-quote and aggregate agreement for the objective and every constraint.
- **Factor segments under (a).** A quote's factor memberships come from the separate banding source and are already persisted separately (`_optimiser_input.py:869, 893`). `choice_query` gains a second, leased 1:1 join on `quote_id` to those per-quote factor rows. A composite factor is grouped by **all** its constituent columns, and its level label matches the Rates tab.
  - Factor keys make the Segments view non-empty even with no analysis columns.
  - Test: a composite factor with **no analysis columns configured** produces factor segments whose counts sum to `n_quotes`.
- Under (b), none of this applies.

**Acceptance:**

See the plan: agreement tests per quote and in aggregate for the objective and every constraint under (a) or (a′); under (b), the explicit unavailable state renders for ratebook results on all three tabs.

**Dependencies:** Q8 decides between canonical evaluation and online-only; consumes OPT-PC01 under (a) or (a′). Depends on OPT-V09B.

**Evidence:** Current code this package changes or relies on: `src/haute/routes/_optimiser_frontier.py`, `src/haute/_builders.py`.

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
  - Convergence axes, with `record_history` on in the fixture;
  - the focus view closing on Escape;
  - a ratebook solve's Rates chart.

  Optionally, add Lift and AvE assertions to `core-flows.spec.ts`, closing the modelling e2e gap. Regenerate the snapshots through `e2e-snapshots.yml`, never by hand.
- **Risks.** CI solve time, so keep the fixture book small.

**Acceptance:**

The named specs locally; CI runs the full suite.

**Dependencies:** Size S. **Depends on:** OPT-V01–OPT-V12, excluding OPT-V09C if Q8 chooses (b).

**Evidence:** Current code this package changes or relies on: `frontend/e2e/canvas-assurance.spec.ts`, `.github/workflows/e2e-snapshots.yml`, `frontend/e2e/core-flows.spec.ts`.

### OPT-PC01 — Per-quote evaluation of ratebook solutions in price_contour

**Why:** In ratebook mode the optimiser does not choose a scenario per quote. It solves for factor-table rates, and each quote's adjustment follows from the product of its factor rates. `RatebookResult` returns the factor tables and the portfolio totals, but no per-quote frame saying which scenario each quote landed on, or what its objective and constraint values are there. So the Adjustments, Segments and Quotes tabs (`OPT-V10` to `OPT-V12`) cannot describe a ratebook solve.

Haute cannot safely rebuild those values itself. The solver maps the factor product onto the scenario grid, clamping or remapping at the grid edges, inside price_contour's Rust code, and that rule is not documented clearly (see `OPT-PC03`: Codex saw a `clamp_rate` of 0.42 with every final factor inside the range). A haute copy could quietly disagree with what the solver actually evaluated, which is the worst failure for sign-off. Haute already refuses to substitute the online apply for ratebook results for this reason.

**What a ratebook reviewer gains**, for the as-solved result and any frontier point:
1. **Adjustments.** How much of the book goes up, down or stays unadjusted, and how much is pinned at the edge of the scenario range. Rates multiply together, so quotes can be pushed past the grid ends; this shows the effect that `clamp_rate` only hints at.
2. **Segments.** The mean final adjustment by analysis column and by rating factor. The factor view differs from the Rates tab: a level's rate may be 1.05, but its quotes' final adjustments also depend on every other factor.
3. **Quotes.** Look up a quote, or the most-adjusted quotes, with its final adjustment and its objective and constraint values at that adjustment.

**Plan:**
1. In the price_contour checkout, read the ratebook evaluation path (the Rust code behind `RatebookResult` and the per-factor coordinate descent) to find where each quote's factor product becomes a grid step.
2. Choose one of two shapes:
   - **(a) Full evaluation.** `evaluate_ratebook(grid, factor_tables, quote_factors) -> frame[quote_id, optimal_step, scenario_value, objective, <constraints>…]`, running the same code path the solver uses.
   - **(a′) Rule only.** Expose the factor-product → step mapping, for example `ratebook_steps(grid, factor_tables, quote_factors) -> frame[quote_id, optimal_step]`. Haute then reads the objective and constraints at that step from the grid while it is still in memory. This is a smaller library change with the same guarantee, provided the read happens during the solve and frontier materialisation, before the 15-minute heavy-state eviction.
3. Release price_contour and raise haute's pin (`price-contour>=0.4.1,<0.5` in `pyproject.toml`) to the new version.
4. Haute's side is `OPT-V09C`.

**Acceptance:** For the objective and every constraint, per quote and in aggregate, the output agrees with `RatebookResult`'s totals, using the precision rule (Float32 inputs promoted to Float64 before summing). This holds:
- on a small real ratebook solve;
- on selected frontier points;
- at both clamp edges, with a composite factor.

A mutation of the step rule (for example, rounding instead of the library's rule) fails the agreement test.

**Dependencies:** Q8 must choose (a) or (a′) over (b), which keeps the tabs online-only. `OPT-V09C` consumes this. Size L for (a), M for (a′).

**Evidence:** `src/haute/routes/_optimiser_solver.py`, `src/haute/routes/_optimiser_frontier.py`, `src/haute/_builders.py`, `src/haute/routes/_optimiser_input.py`, `pyproject.toml`.

### OPT-PC02 — Cancellable or chunked point apply in price_contour

**Why:** `apply_from_grid` is one Rust call with no cancellation argument and no slicing API (`price_contour/apply.py`, lines 482 and 534, in the installed 0.4.1). A frontier-point materialisation, once started, cannot be stopped, so rapid stepping through frontier points wastes work. `OPT-V09B` bounds this in haute: one materialisation per job, a latest-wins queue of depth 1, and admission before the call. But it cannot abort the apply that is already running.

**Plan:** Add a cancel token (checked between quote chunks in Rust), or a chunked apply API that haute can drive and stop between chunks. Haute's V09B scheduler then cancels the running apply when a newer point replaces it.

**Acceptance:** Cancelling mid-apply returns promptly, with no partial artifact. A chunked apply's concatenated output equals the one-shot output exactly. Haute's rapid-stepping test shows at most one apply running, and the replaced one stopped.

**Dependencies:** Deferred until real books show that stepping cost matters. `OPT-V09B` works without it.

**Evidence:** `src/haute/routes/_optimiser_frontier.py`, `src/haute/routes/_optimiser_artifacts.py`.

### OPT-PC03 — A verified definition of clamp_rate

**Why:** `price_contour/ratebook.py` (around line 569 in 0.4.1) copies `clamp_rate` from Rust. The installed package docs (`price_contour-0.4.1.dist-info/METADATA`, around line 606) describe boundary-hit remappings. Neither of the two readings the plan assumed matches: Codex observed a `clamp_rate` of 0.42 with every final factor value inside the scenario range. Haute's Rates and Summary help copy (`OPT-V03`, `OPT-V07`) must say what the number means.

**Plan:**
1. Read the Rust aggregation in the price_contour checkout and find its numerator and denominator: per quote or per level, per coordinate-descent pass or final.
2. Document the definition in price_contour's docstring and README.
3. Add a price_contour test that pins it.
4. Write haute's intro and tooltip copy from that definition.

**Acceptance:** A small hand-constructed ratebook case whose expected `clamp_rate` is computed by hand matches the library's value, and haute's copy states the same definition.

**Dependencies:** None. It informs the copy in `OPT-V03` and `OPT-V07`. Size S.

**Evidence:** `src/haute/routes/_optimiser_solver.py`, `frontend/src/panels/optimiser/SummaryTab.tsx`.

## Price-contour context

This section is for the session with price_contour checked out locally. It
collects every library fact the planning and review rounds relied on, with
line numbers from the installed 0.4.1 wheel (`.venv/Lib/site-packages/price_contour/`).
Re-check them against the source checkout.

**What the library does today**

- **Baseline ("nearest 1.0") rule** (`apply.py` 190-192, 337-344): per quote,
  the row with the smallest |scenario_value − 1.0|, taking the lowest
  `scenario_index` on a tie. Haute's Scenario Expander builds grids with
  `np.linspace(min, max, steps, dtype=float32)`, so an even step count has
  no exact 1.0 row. Under the Q1 decision, haute uses this only for
  `min_pct`/`max_pct` bounds and the "grid contains 1.0" flag.
- **λ sign convention** (`apply.py` 284): the objective gains `+λ` for a min
  constraint and `−λ` for a max constraint. A positive λ can occur together
  with positive slack in this discrete solve; Codex probed a converged
  point with bound 5.0, achieved 5.18 and λ 0.949. Haute must not call a
  positive λ "binding" (`OPT-V01`).
- **Converged is not feasible.** A six-quote ratebook probe returned
  `converged=True` with volume 4.968 against a minimum of 5.5
  (`ratebook.py` 554 gives the coordinate-descent flag). Haute treats a
  point as feasible only when it has converged **and** meets every
  effective bound (`OPT-V06`).
- **`min_pct`/`max_pct`** are supported as a fraction of the baseline total
  (`solver.py` 750). Haute returns the effective absolute bound (`OPT-V01`).
- **`apply_from_grid`** (`apply.py` 482, 534) takes only the grid, the
  lambdas and the constraints. It has no passthrough columns (an extra
  `premium` column disappears from both solve and apply output), no
  cancellation and no slicing. Haute carries analysis columns in its own
  side table (`OPT-V09A`); cancellation is `OPT-PC02`.
- **Ratebook coordinate-descent trace** (`ratebook.py` 108-134, 264):
  `RatebookResult.per_factor_results` carries only `total_objective` and
  `lambdas` per pass and factor, and it is **empty after
  `RatebookResult.load`**. `OPT-V05` pins this shape with a test against
  the installed version.
- **Precision.** Haute casts objective and constraint inputs to Float32.
  The library's baseline equals those Float32 values promoted to Float64
  and summed; a Polars Float32 sum differs in the seventh significant
  figure. Every reconciliation in this roadmap uses Float64-of-Float32
  sums.
- **Frontier points** emit `threshold_*` and `total_*` for **every**
  constraint, swept or not. Haute's point summaries currently keep only
  the swept ones (`OPT-V01` fixes this).

**Options for Q8 (ratebook per-quote results)**

| Option | Library change | Haute work | Ratebook reviewers get |
|---|---|---|---|
| (a) Full per-quote evaluation | New function returning per-quote step, scenario value, objective and constraints (`OPT-PC01`) | `OPT-V09C` persists it while the grid is alive | Adjustments, Segments (including by rating factor) and Quotes, all matching the solve exactly |
| (a′) Step rule only | Expose the factor-product → step mapping (`OPT-PC01`, smaller) | `OPT-V09C` computes steps and reads the values from the in-memory grid during the solve | The same, with the same guarantee, provided the grid read happens before eviction |
| (b) Online-only | None | `OPT-V09C` shows an explicit "not available for ratebook solves" state | The Rates tab and beeswarm (`OPT-V07`) only |

Option (b) can be followed later by (a) or (a′) without redoing any haute
work. The recommendation: if ratebook is how rates are actually set in
production, take (a′), falling back to (a) if the rule cannot be exposed
cleanly.

**Not needed after Q1:** a passthrough-metric column for an arbitrary
price, which was once Q14. Dislocation is out of scope, so no price column
is required.

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
- **"Current" is not the `scenario_value == 1.0` row.** price_contour picks, per quote, the row with the smallest `|scenario_value − 1.0|`, taking the lowest `scenario_index` on a tie (`.venv/Lib/site-packages/price_contour/apply.py:190-192, 337-344`). The Scenario Expander uses `np.linspace(min, max, steps, float32)` (`src/haute/_node_apply.py:272`), and with an even step count the grid has no 1.0 row.
- **The in-memory grid does not last.** The heavy objects (`solver`, `solve_result`, `quote_grid`, `factors_df`) expire after 15 minutes (`src/haute/routes/_job_store.py:35-37`). `/apply` drops all of them when there is no frontier (`src/haute/routes/optimiser.py:224-231`). Per-point apply artifacts are capped at 8 (`_optimiser_frontier.py:77`).
- **The solver input is deleted in `_build_grid`'s `finally`** (`src/haute/routes/_optimiser_service.py:3566-3596`), before the solve starts. Any extraction has to happen in `_build_grid_from_parquet` (:3598).
- **Ratebook factors are already persisted** as an artifact (`_optimiser_artifacts.py:208-310`, `optimiser_ratebook_factors`).
- **`validation.css` is imported only by `ModellingPreview.tsx:30`.** Both previews are lazy chunks.
- **Silent 0.0 baselines** exist at `optimiser.py:209-210`, `_optimiser_frontier.py:324-325, 410-411` and `useNodeResultsStore.ts:433-435` (`FAILED_SOLVE_RESULT`).
- **The ratebook CD trace** (`RatebookResult.per_factor_results`) carries only `total_objective` and `lambdas` per pass and factor, and it is empty after `RatebookResult.load` (`price_contour/ratebook.py:108-134, 264`).
- **`_DEFAULT_TOLERANCE = 1e-6`** is the λ-convergence tolerance, not a feasibility tolerance (`_optimiser_solver.py:84`).

Facts added after Codex plan review round 1:
- **Frontier point summaries drop unswept constraints.** `limited_frontier_payload(..., constraint_names=list(ranges.keys()))` (`_optimiser_frontier.py:1227-1230`) and `_frontier_point_summary.py:115` keep only the swept axes, even though price_contour emits `threshold_*` and `total_*` for every constraint. A frontend selector cannot recover the missing rows; the backend has to be fixed (OPT-V01).
- **The as-solved frontier marker moves.** It is read from the displayed `result` (`OptimiserPreview.tsx:485-486`), which becomes the selected point's result (`useNodeResultsStore.ts:461`).
- **A positive λ does not mean the constraint is tight.** Codex probed a converged point with bound 5.0, achieved 5.18 and λ 0.949. The solver is discrete, so a positive multiplier and positive slack can occur together. `lambdaCopy.ts` ("0 means not binding") is loose in the other direction too.
- **λ sign convention.** The library adds `+λ` for a min constraint and `−λ` for a max constraint (`price_contour/apply.py:284`).
- **`min_pct`/`max_pct` are live.** Config validation accepts them and the library supports thresholds as a fraction of the baseline (`_optimiser_service.py:3130`, `_optimiser_solver.py:1049`, `price_contour/solver.py:750`).
- **Ratebook results omit `n_quotes` and `n_steps`** (`_optimiser_solver.py:1219`).
- **Objective and constraint inputs are cast to Float32** (`_optimiser_input.py:760`). The library's baseline equals the Float32 values promoted to Float64 and then summed; a Polars Float32 sum differs in the 7th significant figure.
- **Point apply artifacts are looked up before the grid** (`_optimiser_frontier.py:995`), so a materialised point stays usable after the grid is evicted.
- **Nothing leases job-owned files to a reader.** JobStore deletes owned files when the job expires (`_job_store.py:284`), and completion builds a fresh `artifact_handles` map (`_optimiser_solver.py:956`). `load_apply_artifact` reads the whole parquet eagerly (`_optimiser_artifacts.py:364`).
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
| 3b | `OPT-V10`, then `OPT-V11`, then `OPT-V12`, serially | Descriptive analytics | All three edit `schemas.py`, `client.ts` and the workspace. `OPT-V09C` and `OPT-PC01` run alongside if Q8 chooses (a) or (a′). |
| 4 | `OPT-V13`; the affected checks (targeted tests, `tsc -b --noEmit`, `tests/test_docs_accuracy.py`, the OpenAPI fingerprint); one Codex review of the branch diff; then the PR | e2e and snapshots once | One PR (Q13). |

`OPT-PC02` is deferred, and `OPT-PC03` can be taken whenever the
price_contour checkout is open, before `OPT-V03`'s help copy is written.

## Open questions

- **Q8, ratebook per-quote results:** (a), (a′) or (b). See "Price-contour
  context".
- **Q4, Adjustments weighting default:** the plan uses quote count, with an
  optional non-negative objective or constraint column at the chosen
  scenario.
- **Q5, `record_history` (`OPT-V05`):** default it to true, or remove the
  flag and always record? It is bounded by `max_iter`.
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
- **Q12, grids without 1.0:** such a grid has no unadjusted scenario. Warn
  only in the config (the plan), or make the Scenario Expander always
  include 1.0?
- **Q15, interruptible apply:** see `OPT-PC02`.

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
- **Existing bugs the review confirmed**, all covered by packages above:
  - Summary and DetailCard judge constraints against different bounds (`OPT-V01`).
  - Frontier point summaries drop unswept constraints (`OPT-V01`).
  - The as-solved frontier marker moves when a point is selected (`OPT-V02`, `OPT-V06`).
  - Converged-but-breached points are drawn as ordinary points (`OPT-V06`).
  - Silent 0.0 baselines (`OPT-V04`).
  - The Quotes tab re-POSTs `/apply` on every open (`OPT-V02`).
