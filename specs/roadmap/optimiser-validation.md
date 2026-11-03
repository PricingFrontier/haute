# Optimiser validation

The optimiser's result workspace (the "Optimiser validation" screen) now
meets the modelling evaluation standard. Its behaviour is specified in the
component specifications, not here:

- [Optimiser high-level](../optimiser/high-level.md) and
  [low-level](../optimiser/low-level.md): the result contract, effective
  bounds, typed frontier points, the analysis-column side table, bounded choice
  queries, adjustment reports, segment breakdowns, the Quotes explorer, the
  ratebook per-quote evaluation and collar, the price-contour contract haute
  relies on, and the cancellable frontier-point apply.
- [Modelling and optimiser UI high-level](../frontend-modelling-optimiser-ui/high-level.md)
  and [low-level](../frontend-modelling-optimiser-ui/low-level.md): the shared
  results workspace and every optimiser pane.

This roadmap keeps only the work still open.

## Scope

In scope: recording each frontier point's convergence trace in price_contour's sweep
(OPT-PC04).

Out of scope, as decisions rather than open work (see "Out of scope and not
applicable" below):

- any comparison with current or deployed pricing, including impact,
  dislocation and uplift (decided 25 September 2026);
- holdout or robustness validation (Q9);
- export buttons inside the results panes (Q10).

## Priorities

| Package | State | Priority | Outcome |
|---|---|---:|---|
| OPT-PC04 | Planned | P2 | price_contour's frontier sweep records each point's convergence trace, so the Convergence tab charts the selected frontier point's own solve, instantly. |

## Planned improvements

### OPT-PC04 — Per-point convergence traces from the frontier sweep

**Why:** The Convergence tab charts only the base solve's history, whichever frontier point is
selected. The installed price-contour 0.7.0 records a per-iteration history for `OnlineOptimiser.solve`
(`record_history=True`), but `frontier()` and the native `sweep_frontier_py` return, per point,
only its totals, λ, iteration count, `converged`, `solver_path` and `non_convergence_reason`: no
trace. Since haute solves a swept constraint at the start of its range, the base solve is often
unconstrained. On the haute-setup-testing pipeline (10M quotes × 11 steps, `conversion_prediction`
swept from its 104,055 minimum) it converged at iteration 0 with λ = 0, so every point showed the
same one-row history (27 September 2026).

The trace cannot be rebuilt in haute from the installed API. With one swept constraint the sweep
solves each point by bisection on λ (`solver_path = "bisection"`: 15 to 27 steps per point on a
synthetic 200,000-quote × 11-step grid), while `solve()` uses the subgradient method. Replaying a
point through `solve_from_grid_py` from its neighbour's λ hit the 50-iteration cap with λ far
from the bisection result at 14 of 15 points, so a replay charts a different algorithm from the
one that produced the point. Re-running a bisection in haute with `apply_from_grid` probes would
roughly double the frontier's cost and still not match the library's steps.

**Plan:** First extend price-contour and release a version containing this API; the
current 0.7.0 release does not provide it:

- `OnlineOptimiser.frontier(..., record_history: bool = False)` and
  `sweep_frontier_py(..., record_history=...)`. When set, each point records the steps the sweep
  already takes, adding no probes. A bisection point records every probe: step, phase (bracket
  expansion or bisection), the bracket's low and high λ, the probed λ, the total objective, each
  constraint total, the residual against the target, and whether every constraint is met. A
  subgradient point records the entries `solve(record_history=True)` records. The Python-orchestrated
  sweep (unswept or ratio constraints) passes `record_history=True` to each per-point `solve`.
- `RatebookOptimiser.frontier(..., record_history=...)` records each point's coordinate-descent
  trace, in the shape of `ratebook_cd_trace`.
- `FrontierResult.point_history`: one long, typed frame aligned to `points` by a `point_index`
  column. Its schema comes from `frontier_points_schema`, as the points' does. It is bounded by
  `max_iter` (plus the bracket-expansion cap) per point.

Then haute:

- Raise the price-contour minimum to the release providing this contract, with a
  compatible upper bound. `_compute_frontier` asks for the history, and the job keeps its
  points' traces job-side, keyed by `point_index` (a job's frontier is fixed). They stay out of
  the status payload, whose size is unchanged.
- `/api/optimiser/frontier/select` gains `include_history`: the selected point's typed trace
  (`OptimiserFrontierPointHistory`, in the generated contract), read from the job with no solver
  work. It follows the pattern of `include_adjustments`.
- The Convergence tab requests the trace only while it is open with a point selected, caching it
  in the review as Adjustments does. A bisection point charts, by step, the probed λ within its
  bracket, each constraint total against its target, and the objective. A subgradient point uses
  the existing small multiples. A ratebook point uses the existing CD-trace view. With no point
  selected the tab keeps the base solve's history.

**Acceptance:**

- price-contour: for every returned point the trace's last step equals the point's reported λ and
  totals exactly; tracing adds no probes (step counts equal `iterations`, and sweep time is within
  noise of an untraced sweep).
- haute: the status payload is unchanged in size; `select` returns the trace for the requested
  point with no solver call; the Convergence tab
  charts the selected point's own trace; the canvas-assurance e2e reads point 2's trace.

**Dependencies:** a price-contour release with per-point frontier history. Until it lands,
the Convergence tab keeps showing
the base solve's history.

**Evidence:** `src/haute/routes/_optimiser_solver.py::_compute_frontier` and `_solve_online`;
`src/haute/routes/_optimiser_frontier.py`;
`frontend/src/panels/optimiser/ConvergenceChart.tsx`; price-contour 0.7.0's
`OnlineOptimiser.frontier` signature and `sweep_frontier_py` stub (no `record_history`), and its
`SolverPath` enum (`bisection`, `subgradient`).

## Out of scope and not applicable

| Modelling feature | Why it does not carry over |
|---|---|
| Double lift, Lorenz, Gini | They measure how a predictor ranks observed outcomes. The optimiser's objective and constraints are model-expected values with no observed outcome. The nearest descriptive analogue is the Adjustments tab's distribution of chosen adjustments; impact analysis is out of scope. |
| Residual histogram, actual-vs-predicted scatter | There are no actuals. |
| AvE semantics (A/E ratio) | There are no actuals. The **layout** (feature browser, per-level chart, exposure strip) is reused by the Segments tab. |
| PDP | There is no fitted model to vary. In ratebook mode the factor tables already are the per-level effect (the Rates tab). |
| Feature importance / SHAP | There are no learned attributions. The beeswarm's quote-weighted \|log rate\| is the existing analogue. |
| GLM inference (SE, Wald intervals, significance) | λ is a Lagrange multiplier from a dual solve, not a fitted coefficient, so Wald-style inference does not apply. This says nothing about how the solution varies with the book or the models (see Q9). |
| EBM terms | Not applicable. |
| Tuning details / "Use best as fixed parameters" | There is no hyper-parameter search. Picking a frontier point as the publish target is the analogous action, and it lives in the Export pane. |
| Train vs eval loss with a best-iteration line | No eval set. The Convergence tab is the analogue. |
| k-fold CV selection spread | Re-solving per fold has no standard interpretation. |
| **Holdout / robustness validation** | A decision about scope, not a claim that the solve carries no uncertainty. The figures are expected values from scoring models on one fixed book, so model error, mix drift and sampling variation are all real risks. A random holdout alone may not measure them well, and scaling absolute bounds to a sample needs a separate definition. The optimiser node documentation says the figures are model-expected, not observed. Tracked as Q9. |
| Modelling's "Training diagnostics are in-sample" wording | The wrong disclaimer for an optimiser. The accurate one is: "Expected values from the scoring models on the solve quotes; not observed outcomes." |
| **Current vs optimised, dislocation and impact analysis (decided 25 September 2026)** | The optimiser is an adjustment on top of a base price: it reapplies scenarios to that base and never sees the live, currently deployed pricing. A "Current → Optimised → Change" view would compare against something the optimiser does not know. Analysts set up impact analysis elsewhere. The tests pinning the absence of Baseline and Uplift stay. |
| Export buttons in the results workspace | Modelling excludes them deliberately, and so does the optimiser workspace. Parity means a values-table disclosure under every chart. Any CSV belongs in the Export pane (`OptimiserPublishSection.tsx`), which is Q10. |

## Open questions

- **Q9, robustness checks:** out of scope as a scope choice, not a claim that
  the solve carries no uncertainty. Is any robustness view wanted later: an
  out-of-time or group split, or a stressed re-score?
- **Q10, CSV downloads:** should frontier points and per-quote choices be
  downloadable from the Export pane? The results panes keep the no-export
  rule either way.
