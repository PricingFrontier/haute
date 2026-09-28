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

In scope: making a frontier point's per-quote computation interruptible, for
both the online apply and the ratebook evaluation, so rapid stepping through
frontier points stops wasting solver work.

Out of scope, as decisions rather than open work (see "Out of scope and not
applicable" below):

- any comparison with current or deployed pricing, including impact,
  dislocation and uplift (decided 25 September 2026);
- holdout or robustness validation (Q9);
- export buttons inside the results panes (Q10).

## Priorities

| Package | State | Priority | Outcome |
|---|---|---:|---|
| OPT-PC02 | Deferred | P3 | A frontier point's apply or evaluation can be cancelled, and haute cancels the running one once no request is waiting for it, so rapid stepping stops wasted work. |

## Planned improvements

### OPT-PC02 — Cancellable frontier-point computation

**Why:** A frontier point's per-quote frame comes from one of two
price-contour calls, and neither can be stopped once started:

- online: `apply_from_grid(grid, lambdas, constraints)`
  (`apply_from_grid_py`, `crates/price-contour/src/apply_py.rs`), whose argmax
  runs inside one `py.detach` block;
- ratebook: `RatebookOptimiser.evaluate(grid, contexts, tables)`
  (`evaluate_ratebook_py`, `crates/price-contour/src/grouped_py.rs`).

Nothing in the library checks for cancellation. Both results also build their
per-quote DataFrame lazily, when `.dataframe` or `.quote_results` is first
read (`build_result_dataframe`), so that work sits outside the argmax.
Haute then writes the frame out (`_persist_apply_frame_artifact`). The cost
of one point is therefore argmax, then frame build, then persist.

Haute bounds that cost (one point computation per job, a latest-wins waiting
slot, memory admission before the call, a generation fence on publish; see
the optimiser low-level spec's "Bounded choice queries and point
materialisation"). But a running computation always finishes, even after
every request waiting for it has gone. The results tabs already abort their
request when the user steps to another point, and that abort detaches the
server-side subscriber (`ticket.wait(cancellation_token)` in
`_optimiser_outcomes.py` and `_optimiser_quotes.py`). `LatestWinsQueue` then
does nothing for a running flight, and the run's `ExecutionCancellationToken`
is ignored by `_materialise_point`.

`apply_lambdas_to_parquet_chunked` is not a building block for this. It
reads the long quote parquet from disk and builds a small grid per chunk,
while haute holds the solve's in-memory `QuoteGrid`. (Its lack of passthrough
columns and ratio constraints is shared with `apply_from_grid`, so that is
not the difference.)

**Plan:**

1. **price-contour: a cancel token.** Add `CancelToken`, a
   `#[pyclass(frozen)]` holding an `Arc<AtomicBool>`, so `cancel()` and
   `cancelled` take `&self` and are safe from any thread without an exclusive
   borrow. Add an optional keyword-only `cancel=` to `apply_from_grid` and
   `RatebookOptimiser.evaluate`. A cancelled call raises
   `price_contour.Cancelled` (a `RuntimeError` subclass); a token already
   cancelled on entry raises before any work. No token means today's
   behaviour, bit for bit.
2. **price-contour: cover every expensive phase.** The token is polled every
   N quotes (or N rows) in each phase that scales with the book:
   - online: the baseline scan in `parse_constraints`
     (`solver_py.rs`), the argmax kernel, and the baseline totals after it
     (`price-contour-core/src/solver/apply.rs`);
   - ratebook: the evaluation kernel and its baseline scan
     (`grouped_py.rs`);
   - both: the lazy per-quote frame build (`ApplyResult.dataframe`,
     `RatebookEvaluation.quote_results`), polled per block of rows, not per
     column, so one wide column cannot stall a cancel.

   Each of these runs inside `py.detach`. Today `parse_constraints` and both
   lazy getters hold the GIL, so another Python thread could not even deliver
   the cancel. A result keeps a clone of its call's token, so a lazy frame
   build on another thread honours a cancel issued after the call returned.
3. **price-contour: benchmark and release.** Add a cancellation benchmark: a
   synthetic grid of 1,000,000 quotes × 41 steps × 3 constraints, online and
   ratebook, cancelled at random points in each phase above. Threshold: p99
   latency from `cancel()` to the raise under 50 ms. Then tests for both
   calls, a minor release, and the new contract in the library's docs.
4. **haute: the guard and the pin.** Bump the `price-contour` specifier. In
   `src/haute/_price_contour.py`, verify `CancelToken`, `Cancelled` and the
   `cancel` parameter on `apply_from_grid` and `RatebookOptimiser.evaluate`
   (the guard checks parameter names), and update
   `tests/test_decoupling_contracts.py`.
5. **haute: the bridge between tokens.** `ExecutionCancellationToken` gains
   `on_cancel(callback)`: the callback runs once, outside the token's lock,
   when the token is cancelled, or at once if it already is. The point run
   creates a `CancelToken` and registers its `cancel` before starting the
   native call, so a cancel before, during or after the call reaches Rust.
6. **haute: the queue rule, "cancel when nobody is waiting".** This matches
   `SharedFlights`, which already cancels a run whose last subscriber leaves.
   - A new request for another point never cancels the running computation
     by itself. It takes the waiting slot as today, and the 409
     `frontier_point_apply_replaced` still applies only to a replaced waiter.
     Two views on different points therefore cannot cancel each other: one
     finishes and is retained, then the other runs.
   - When the running flight's last subscriber detaches,
     `LatestWinsQueue._detach` cancels the flight's token. The flight becomes
     *cancelling*: it keeps the lane (so nothing else starts) until its run
     function returns, but a new request for its key no longer joins it. That
     request takes the waiting slot like any other key, replacing a waiter as
     today. So A → B → A while A is cancelling queues a fresh A in place of B,
     and it starts once the cancelled A has cleaned up.
   - The lane moves on only when the run function has returned, after
     cleanup and admission release (`_finished` is called on return today),
     so a cancelled run's memory is never counted twice.
   - A request answered from a retained artifact never reaches the queue and
     cancels nothing.
7. **haute: one commit point.** The point's success is committed only by
   adopting its handle in `_publish_point_handle`, under the parent lock.
   `_materialise_point` passes the token into the computation, checks it
   between the frame build and the persist step, and checks it once more
   under the parent lock just before adoption. A cancel seen before adoption
   means nothing is adopted, the written artifact is removed, admission is
   released, and the run ends with `ExecutionCancelledError`. A cancel after
   adoption changes nothing: the artifact stays, retained for the next
   request. Cancellation is best-effort and deliberately does not take the
   parent lock: a cancel that lands after the final check but before adoption
   loses the race, and the finished point is adopted. That outcome is
   harmless (the artifact is complete and valid, nothing is orphaned, and
   admission is released as usual), so no shared guard between the queue,
   the token and publication is added. `price_contour.Cancelled` is mapped to `ExecutionCancelledError`
   only when haute's token is cancelled. Any other failure, and a
   `Cancelled` raised without a haute cancel, propagates with its own
   identity.
8. **Specs, updated with the code.**
   - Optimiser low-level: "Bounded choice queries and point materialisation"
     ("a running one finishes and keeps its artifact", why at most one runs),
     the testing scenario at "A/B/C rapid stepping", and the price-contour
     contract section.
   - Optimiser high-level: the point-materialisation paragraph ("because the
     library's point apply cannot be interrupted").
   - The `_shared_flights.py` module docstring ("A running flight is never
     cancelled").
   - The frontend low-level spec's 409 retry rule stays as it is, because the
     409 contract is unchanged.

**Acceptance:**

- price-contour:
  - The benchmark in step 3 meets its threshold for every phase.
  - A pre-cancelled token raises `Cancelled` without starting any work.
  - An uncancelled call with a token returns output identical to a call
    without one.
  - A result obtained on one thread, then cancelled from a second thread
    while a third thread reads its cold `dataframe` or `quote_results`,
    raises `Cancelled`.
- haute, with the native call replaced by a controllable fake at the
  `price_contour()` seam:
  - **Step away:** A running, its only subscriber detaches → A's token is
    cancelled; B (waiting) starts only after A's cleanup and admission
    release; no handle for A is adopted and no A artifact is left on disk.
  - **Two consumers:** A and B each have a live subscriber → A finishes and
    is retained, B then runs; neither gets a 409; no retry loop.
  - **Back again:** A cancelling, a new request for A → it does not join the
    cancelling flight; it takes the waiting slot (replacing B with a 409) and
    completes after the cancelled A clears.
  - **Commit race:** a cancel during persist, and a cancel just before
    adoption → nothing adopted, no orphan file; a cancel just after adoption,
    or after the final check but before adoption → the artifact is kept, a
    later request is answered from it, and no orphan file is left.
  - **Error identity:** a computation or persist failure without a cancel
    reaches every subscriber as that failure, not as a cancel or a 409.
  - **Retained point:** requesting a retained point while another point is
    running answers at once and leaves the running one untouched.

**Dependencies:** Deferred until real books show that stepping cost matters.
Haute works correctly without it. Steps 1–3 ship in price-contour first, and
steps 4–8 follow in one haute PR.

**Evidence:** `src/haute/routes/_optimiser_frontier.py`
(`request_point_apply`, `_point_frame_computation`, `_materialise_point`,
`_publish_point_handle`), `src/haute/routes/_shared_flights.py`
(`LatestWinsQueue`, `SharedFlights`), `src/haute/_execution_context.py`
(`ExecutionCancellationToken`),
`src/haute/routes/_optimiser_artifacts.py`, `src/haute/_price_contour.py`.

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
