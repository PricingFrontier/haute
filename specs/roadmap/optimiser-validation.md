# Optimiser validation

The optimiser's result workspace (the "Optimiser validation" screen) now
meets the modelling evaluation standard. Its behaviour is specified in the
component specifications, not here:

- [Optimiser high-level](../optimiser/high-level.md) and
  [low-level](../optimiser/low-level.md): the result contract, effective
  bounds, typed frontier points, the analysis-column side table, bounded choice
  queries, adjustment reports, segment breakdowns, the Quotes explorer, the
  ratebook per-quote evaluation and collar, and the price-contour contract
  haute relies on.
- [Modelling and optimiser UI high-level](../frontend-modelling-optimiser-ui/high-level.md)
  and [low-level](../frontend-modelling-optimiser-ui/low-level.md): the shared
  results workspace and every optimiser pane.

This roadmap keeps only the work still open.

## Scope

In scope: forecasting a solve's memory in the Solve panel from a calibrated forecast (OPT-W02),
and making a frontier point's per-quote apply interruptible, so rapid stepping through frontier
points stops wasting solver work.

Out of scope, as decisions rather than open work (see "Out of scope and not
applicable" below):

- any comparison with current or deployed pricing, including impact,
  dislocation and uplift (decided 25 September 2026);
- holdout or robustness validation (Q9);
- export buttons inside the results panes (Q10).

## Priorities

| Package | State | Priority | Outcome |
|---|---|---:|---|
| OPT-W02 | Planned | P2 | The Solve panel forecasts the solve's memory against what the machine can give, from a forecast calibrated against measured session peaks. |
| OPT-PC02 | Deferred | P3 | price_contour's point apply can be cancelled or chunked, so rapid frontier stepping stops wasted work. |

## Planned improvements

### OPT-W02 — A calibrated solve-memory forecast in the Solve panel

**Why:** Since OPT-W01 a process-mode solve is never refused on an estimate: its solver session
runs under a native cap and only a solve that exceeds it stops. The user still gets no warning
before a solve that will not fit. The resident-grid estimate the thread mode gates on
(`estimate_optimiser_grid_peak_bytes`) is conservative — on 26 September 2026 it estimated about
13 GiB for a 10M-quote × 11-step grid whose build the "Measured setup memory" figures put near
4–5 GiB — and it covers grid construction only, not the solve or the frontier.

**Plan:** Measure the session's whole-process peak (its backend charge) for grid build, solve and
inline frontier, online and ratebook, at 1M, 5M and 10M quotes × 11 steps, and record it in the
optimiser low-level specification. Refit the estimate into a solve forecast within 1.0–1.5× of
the measured peaks. Widen the input estimate's single scan to sample the solver-input widths in
the same aggregation (its `expanded_row_count` already includes the scenarios), and add
`solve_memory_forecast_bytes` and `solve_memory_cap_bytes` to `OptimiserEstimateResponse`. The
panel shows "Estimated solve memory X of Y", warning when X exceeds Y, with Solve still enabled.
Thread mode's gate uses the refit.

**Acceptance:** The forecast stays within 1.0–1.5× of the measured peak at 1M and 5M quotes
(benchmark), the estimate performs no second scan, the generated contract carries the fields,
and the panel renders the forecast and the warning without disabling Solve.

**Dependencies:** None; the forecast is recorded by the solver session described in the optimiser low-level specification.

**Evidence:** `src/haute/_ram_estimate.py::estimate_optimiser_grid_peak_bytes`;
`src/haute/routes/_optimiser_input.py::estimate_input_metrics`;
`scripts/benchmarks/opt-v09a-setup-memory.py`.

### OPT-PC02 — Cancellable or chunked point apply in price_contour

**Why:** `apply_from_grid` is one Rust call with no cancellation argument and
no slicing API (price-contour `python/price_contour/apply.py`). Nothing in the
library is cancellable. `apply_lambdas_to_parquet_chunked` streams parquet to
parquet in chunks and is a possible building block, but it is still one
uninterruptible call, with no passthrough columns and no ratio constraints. A
frontier-point materialisation, once started, cannot be stopped. Haute bounds
the cost (one materialisation per job, a latest-wins waiting slot, admission
before the call; see the optimiser low-level spec's "Bounded choice queries and
point materialisation"), but it cannot abort the apply already running.

**Plan:** Add a cancel token (checked between quote chunks in Rust), or a
chunked apply API that haute can drive and stop between chunks. Haute's
scheduler then cancels the running apply when a newer point replaces it.

**Acceptance:** Cancelling mid-apply returns promptly, with no partial
artifact. A chunked apply's concatenated output equals the one-shot output
exactly. Haute's rapid-stepping test shows at most one apply running, and the
replaced one stopped.

**Dependencies:** Deferred until real books show that stepping cost matters.
Haute works correctly without it.

**Evidence:** `src/haute/routes/_optimiser_frontier.py`,
`src/haute/routes/_optimiser_artifacts.py`.

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
| **Holdout / robustness validation** | A decision about scope, not a claim that the solve carries no uncertainty. The figures are expected values from scoring models on one fixed book, so model error, mix drift and sampling variation are all real risks. A random holdout alone may not measure them well, and scaling absolute bounds to a sample needs a separate definition. The provenance strip says the figures are model-expected, not observed. Tracked as Q9. |
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
- **Q11, inputs vs outputs:** the pre-solve `OptimiserDataPreview` becomes
  unreachable once a result exists. Add an "Inputs" tab to the result
  workspace?
