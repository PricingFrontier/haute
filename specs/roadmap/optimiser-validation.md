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

In scope: forecasting a solve's memory in the Solve panel from a calibrated forecast (OPT-W02),
and recording each frontier point's convergence trace in price_contour's sweep (OPT-PC04).
`OPT-R01` to `OPT-R05` come from the
[25 October 2026 codebase review](codebase-review-2026-10-25.md): one gate for every
persisted optimiser artifact, setup files under a reaped root, failures classified once, the
frontier recompute sweep as a decision, and the test-only paths.

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
| OPT-PC04 | Planned | P2 | price_contour's frontier sweep records each point's convergence trace, so the Convergence tab charts the selected frontier point's own solve, instantly. |
| OPT-R01 | Planned | P2 | Every optimiser artifact that is persisted, logged or served passes the same finiteness and shape gate, and a corrupt or non-finite ratebook table is refused with a message naming it. |
| OPT-R02 | Planned | P2 | The solver input, the setup worker's scratch and the auto-range parts live under a marked root the startup reaper sweeps. |
| OPT-R03 | Planned | P3 | Optimiser failures are classified once, a library error is never reported as the user's input's fault, and both MLflow summaries use one key scheme. |
| OPT-R04 | Decision | P3 | The frontier recompute sweep either gets a browser action or is retired with its generation fencing. |
| OPT-R05 | Planned | P3 | The optimiser's test-only production paths are gone, the Optimiser Apply builder refuses a missing input name, and one test pins a binding constraint against a hand-computed answer. |

## Planned improvements

### OPT-W02 — A calibrated solve-memory forecast in the Solve panel

**Why:** A process-mode solve is never refused on an estimate: its solver session
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
  compatible upper bound. `_compute_frontier` asks for the history, the frontier recompute
  path does too, and each generation keeps its points' traces job-side, keyed by
  `(frontier_generation, point_index)`. They stay out of the status payload, whose size is
  unchanged.
- `/api/optimiser/frontier/select` gains `include_history`: the selected point's typed trace
  (`OptimiserFrontierPointHistory`, in the generated contract), read from the job with no solver
  work. It follows the pattern of `include_adjustments` and its generation check.
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
  generation and point with no solver call and refuses a stale generation; the Convergence tab
  charts the selected point's own trace; the canvas-assurance e2e reads point 2's trace.

**Dependencies:** a price-contour release with per-point frontier history. Until it lands,
the Convergence tab keeps showing
the base solve's history.

**Evidence:** `src/haute/routes/_optimiser_solver.py::_compute_frontier` and `_solve_online`;
`src/haute/routes/_optimiser_frontier.py`;
`frontend/src/panels/optimiser/ConvergenceChart.tsx`; price-contour 0.7.0's
`OnlineOptimiser.frontier` signature and `sweep_frontier_py` stub (no `record_history`), and its
`SolverPath` enum (`bisection`, `subgradient`).

### OPT-R01 — One gate for every persisted optimiser artifact

**Why:** `_validate_artifact_payload` has one caller, the save route, whose write also passes
`allow_nan=False`; the MLflow log builds the same complete `optimiser_result.json` (what
Optimiser Apply prices from) and writes it with `json.dumps(..., default=str)` and no gate, so
a non-finite value or a malformed ratebook section is logged unchecked while the save refuses
it with a 400 naming the paths; both specifications promise the gate on save and log. The
browser's status poll corrects a non-finite `result` or `frontier_data` to `error` before a
user can reach Log, so this is a missing defence layer rather than a demonstrated wrong
artifact. A retained ratebook point whose tables hold a non-finite rate is refused by
`_serialise_ratebook_factor_table_rows` with a bare `ValueError` that `/frontier/select`,
`/save` and `/mlflow/log` reach outside any handler, so the user gets the sanitized 500.
`_scan_ratebook_factors_artifact` wraps only the lazy `scan_parquet` in its corrupt-artifact
handler, but a corrupt parquet fails only when its schema is read (probed), so the documented
stable 500 becomes an unclassified `ComputeError` later; the apply scan forces
`collect_schema()` for exactly this reason.

**Plan:** Call `_validate_artifact_payload(complete_payload)` in `mlflow_log` before
`create_run` and pass `allow_nan=False` to both `json.dumps` calls; raise a typed 400 from the
ratebook serialiser (or catch it in the three callers) naming the table and level; call
`scan.collect_schema()` inside the ratebook scan's `try`.

**Acceptance:** `/mlflow/log` answers 400 for a NaN lambda; a frontier point with a non-finite
rate is answered 400 naming the table; a corrupt ratebook factor parquet answers the
documented stable 500.

**Dependencies:** None.

**Evidence:** `src/haute/routes/optimiser.py::_validate_artifact_payload`;
`src/haute/routes/optimiser.py::_log_optimiser_run`; `src/haute/routes/optimiser.py::mlflow_log`;
`src/haute/routes/_optimiser_solver.py::_serialise_ratebook_factor_table_rows`;
`src/haute/routes/_optimiser_artifacts.py::_scan_ratebook_factors_artifact`.

### OPT-R02 — Setup files under a reaped root

**Why:** The solver-input parquet (`tempfile.mkstemp`, often gigabytes), the setup worker's
scratch directory (`mkdtemp`) and the auto-range parts directory (`TemporaryDirectory`) are
unmarked files in the system temp directory outside the three roots
`reap_stale_optimiser_artifacts` walks, so a server crash mid-setup leaves them forever; the
specification's "a killed worker leaves nothing behind" covers worker exits, which the parent
cleans, not a parent crash.

**Plan:** Create the solver-input file and both scratch directories under a fourth marked root
through `create_owned_artifact_directory` and add that root to the reaper.

**Acceptance:** A setup interrupted by killing the server leaves directories the next start
reaps; the optimiser modules call none of `mkstemp`, `mkdtemp` or `TemporaryDirectory`.

**Dependencies:** None.

**Evidence:** `src/haute/routes/_optimiser_artifacts.py::reap_stale_optimiser_artifacts`;
`src/haute/routes/_optimiser_worker.py`;
`src/haute/_artifact_housekeeping.py::create_owned_artifact_directory`.

### OPT-R03 — Failures classified once and named right

**Why:** The failure classification (worker failure record, stopped, the 507-memory /
400-422-contract / other split, admission and memory-limit errors, public contract errors,
generic) is hand-written five times across the service, the frontier sweep and the setup
worker with small differences: one site records a 409 as `error`, another as
`contract_error`, and `_failure_before_job_mapping` maps every 4xx to `contract_error`. The
auto-range job maps any bare `ValueError` to a 400 carrying `str(exc)`, so a library or Polars
`ValueError` is reported as the user's input's fault, against the solve path's own policy. A
memory-admission failure in the setup worker is worded as an auto-range failure because the
noun is hard-coded. A frontier point's MLflow summary uses different metric keys from the
anchor's, so two runs from one solve cannot be compared by name.

**Plan:** One `classify_optimiser_failure(exc, execution_context)` used by the five sites;
typed refusals from the auto-range reducer and no bare `except ValueError`; pass the caller's
`operation_noun`; build the point summary with the library's key scheme.

**Acceptance:** One classifier; a bare `ValueError` in auto-range reaches the generic branch;
the setup worker's memory message names optimiser setup; both summaries share their metric
keys (a test compares the key sets).

**Dependencies:** None. The services raise domain errors, which the classifier maps.

**Evidence:** `src/haute/routes/_optimiser_service.py`;
`src/haute/routes/_optimiser_frontier.py::_frontier_point_mlflow_summary`;
`src/haute/routes/_optimiser_worker.py::_failure_before_job_mapping`.

### OPT-R04 — The frontier recompute sweep

**Why:** `POST /frontier`, its status and cancel routes, `start_sweep`, the session `sweep`
command, the per-parent sweep registry and the `frontier_generation` increment it alone
produces have no caller in the browser: a user only ever gets the inline generation-0
frontier. About 600 lines in five modules and the generation fencing (the 409 "frontier
changed" paths) serve an API-only path the specification describes as a user path.

**Plan:** Decide: give the results workspace a "Recompute frontier" action that uses it, or
retire the recompute routes and the sweep service and keep generation 0 only, which removes
every generation check.

**Acceptance:** Either the action exists and the canvas-assurance journey covers it, or the
routes, service and fencing are gone and the specification says the frontier is fixed per
job.

**Dependencies:** None.

**Evidence:** `src/haute/routes/_optimiser_frontier.py::start_sweep`;
`src/haute/routes/optimiser.py`; `frontend/src/api/client.ts`.

### OPT-R05 — Test-only paths, a strict apply binding and a binding-constraint test

**Why:** `_load_ratebook_factors_artifact` (an eager read of the whole factor table),
`_finalize_solve_result`'s `factors_df` parameter with its eager persist branch, the
`"factors_df"` heavy key, `_launch_background`'s self-admitting branch and `select_point`'s
early return on a job without frontier points are reachable only from tests, and two
`# noqa: F401` re-exports exist for one test import. `_build_optimiser_apply` silently drops a
declared source missing from the by-name frames and then binds the ratebook input by
position, while the Optimiser builder raises for the same condition (reachability from the
engine unconfirmed). No test pins a solve whose constraint binds (λ > 0, total at the bound)
against a hand-computed answer; the real-solver pins are unconstrained optima and
self-consistency sums, so a regression in constraint units, bound anchoring or the Float32
projection would pass.

**Plan:** Delete the test-only paths with their tests and make `_launch_background` require a
context; raise in the apply builder as the Optimiser builder does; add a three-quote fixture
where a `min` constraint forces a different scenario than the unconstrained optimum, asserting
the chosen steps, λ > 0 and the constraint total at the bound, online and ratebook.

**Acceptance:** The named symbols are gone; a missing by-name input raises; the
binding-constraint test exists and fails when the bound is mis-anchored.

**Dependencies:** None.

**Evidence:** `src/haute/routes/_optimiser_artifacts.py::_load_ratebook_factors_artifact`;
`src/haute/routes/_optimiser_solver.py::_finalize_solve_result`;
`src/haute/routes/_optimiser_service.py::_launch_background`;
`src/haute/_builders.py::_build_optimiser_apply`; `src/haute/_builders.py::_optimiser_select`;
`tests/test_optimiser_routes_real_library.py`.

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
