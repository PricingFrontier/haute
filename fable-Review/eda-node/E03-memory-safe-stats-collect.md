# E03 — Memory-safe, budget-enforced, cancellable stats collect

**Severity:** HIGH (unbounded memory + bypassed budget + dead cancel) · **Effort:** M–L · **Review:** dev/reviewer pair
Files: `src/haute/routes/_explore_service.py` (primary), touches nothing in `_polars_utils.py`
unless adopting `bounded_collect_batches`. Tests: `tests/test_explore_routes.py` (+ one
`pytest.mark.perf` structural test).

Land AFTER E01/E02 — this package restructures the same `_build_frame_stats` they correct.

## Benchmark evidence (Polars 1.39.2, streaming engine, 3M rows × 40 int64 + 6 low-card text + 1 unique-per-row text; peak = isolated-subprocess working-set delta, 10 ms sampling)

| Aggregation shape (single collect) | time | peak Δ |
|---|---|---|
| light: null/min/max/mean/std/zero/neg (40 numeric) | 0.17 s | 1150 MB |
| only exact `n_unique` (47 cols incl. 1 hi-card) | 0.47 s | **2203 MB** |
| only `approx_n_unique` (same 47) | 0.23 s | 1511 MB |
| only exact `quantile` p25/median/p75 (40 numeric) | **2.37 s** | 1148 MB |
| only `value_counts().head(50).implode()` (7 text) | 1.02 s | 728 MB |
| **everything, exact — current shape** | 3.56 s | **2230 MB** |
| everything, `approx_n_unique` | 3.24 s | 1716 MB |

Two critical observations: (a) none of the heavy variants *errored* — the streaming engine raises
no stream-compat error for `n_unique`/`quantile`/`value_counts`, it just accumulates unbounded
state, so `streaming_collect`'s `BoundedMemoryUnsupportedError` path never fires; (b) the relative
costs are robust even though absolute numbers won't extrapolate linearly (the 3M fixture is few
row-groups; see Uncertainties).

## EF-07 [HIGH] — one giant select accumulates every column's unbounded aggregators at once

**Evidence:** `_build_frame_stats` builds one flat expression list — per column `n_unique` (`:451`),
per numeric `quantile(0.25)/median/quantile(0.75)` (`:459-465`), per text
`value_counts(sort=True).head(50).implode()` (`:469-474`; `head(50)` bounds the *output*, not the
intermediate distinct→count map) — and collects once (`:476-480`).

**Impact (500 cols × 50M rows, mixed dtypes):** in one streaming pass every accumulator is live
simultaneously: hash sets sized by cardinality (n_unique), per-column value buffers (exact
quantiles), full distinct maps (value_counts). Peak ≈ Σ(accumulators) and blows the 4 GB
`EXPLORE_ANALYSIS` budget (`_execution_admission.py:39`); the job dies or the box swaps.

## EF-08 [HIGH] — no RSS sample runs during the dominant collect, so the typed `memory_limited` state is bypassed

**Evidence:** the whole stats step is one `execution_context.stage("explore_frame_stats")`
(`:821-826`) around one opaque collect. `stage` samples RSS at entry/exit only
(`_execution_context.py:722-780`); `checkpoint()` — the only cooperative
`_check_memory_budget` — first runs *after* the collect (`explore_before_store`, `:719`).
Admission reserves budget up front (`_execution_admission.py:427-435`) but nothing enforces it
mid-flight. For the EF-07 workload the OS kills the worker before the stage-exit sample ever runs.

## EF-09 [MEDIUM] — exact `n_unique` is unnecessary for every consumer

`distinct_count` feeds: the constant-column test (`:265-276` — needs "is it ≤1", not exactness),
`expandable`/`values_truncated` flags (`:394-402` — ">50", tolerant), and cardinality display
(tolerant). `approx_n_unique` (HLL, bounded memory) measured −23% peak / ~2× faster on that
component. One guard: HLL must return exactly 1 for a constant column at scale (linear counting in
the small regime should, but verify), else keep the constant *gate* on an exact cheap signal
(`min == max` — both already computed).

## EF-10 [MEDIUM] — cancel() cannot interrupt the collect

**Evidence:** `cancel()` sets the cooperative token (`_background_jobs.py:89-96`), but the work is
one blocking `collect(engine="streaming")` (`_polars_utils.py:75`) that never observes it. The
daemon thread runs to completion (CPU/time unbounded; memory bounded only by admission), and
cancellation merely discards the result. "Cancel" is currently "abandon result", not "stop work".
Existing test `test_explore_cancel_stops_in_flight_job` (`tests/test_explore_routes.py:414`) does
not pin prompt worker termination.

## Fix design (one structural change resolves EF-07/08/09/10)

Split `_build_frame_stats`'s expression list into **cost classes** and collect in batches:

1. **Pass A (one collect, all columns):** the streaming-safe single-value aggregations —
   `pl.len()`, null/nan/inf counts, min/max, mean, std, zero/negative counts. Cheap and bounded
   (benchmark row 1).
2. **Pass B (batched by column group):** the memory-unbounded aggregations — quantiles,
   `value_counts(...).implode()`, and (if kept exact anywhere) `n_unique`. Batch columns so one
   batch's accumulators bound the peak (start ~32 columns/batch; make it a module constant).
   Between batches call `execution_context.checkpoint(label=...)` — this is what turns a budget
   breach into a typed `ExecutionMemoryLimitExceededError` → `memory_limited` (EF-08) and gives
   cancellation a cooperative observation point with bounded latency (EF-10).
3. **Adopt `approx_n_unique`** for `distinct_count` (EF-09) with the constant-gate guard above.
   Note the EF-03 (E02) derivation `distinct − 1 if nulls` stays valid — approx affects magnitude
   tolerance, not the null adjustment.
4. Keep per-column alias/parse code shared with the single-pass path — the batching must not fork
   the alias scheme (`null::{name}` etc.) or the `_has_categorical_value_counts` gate discipline.
5. Progress (replaces the 0.85 dead zone, EF-25c): update job progress per completed batch
   (`0.85 + 0.14 × batches_done/batches_total`). Cheap, honest, and makes long stats phases legible.
6. Do NOT try to stream quantiles approximately or subsample — numbers shown to analysts stay
   exact except where explicitly labelled approximate (distinct). If a future need arises, that is
   a product decision, not a perf patch.

`bounded_collect_batches` (`_polars_utils.py:120-144`) already implements checkpoint-between-
batches for *row* batches; here we batch *expressions*, so a small local loop over
`streaming_collect` calls per column-group is the simpler shape. Either is acceptable; don't build
new infrastructure.

## TDD plan (failing tests first)

1. `test_frame_stats_batches_unbounded_aggregations` — monkeypatch `streaming_collect` to record
   each call's expression count/aliases; on a 100-column mixed frame assert >1 call and that no
   single call carries more than BATCH_SIZE quantile/value-count columns. **Fails today** (exactly
   one call).
2. `test_frame_stats_checkpoints_between_batches` — monkeypatch `ExecutionContext.checkpoint` to
   count invocations during `_build_frame_stats`; assert ≥ number of B-batches. **Fails today**
   (zero).
3. `test_explore_memory_budget_enforced_mid_stats` — with a tiny profile budget (monkeypatch the
   admission limit) and a frame sized to breach it in pass B, assert the job terminates
   `memory_limited` (typed), not `error`. **Fails today** (worker dies / generic error).
4. `test_explore_cancel_interrupts_stats` — start a job on a frame large enough that pass B has
   multiple batches; cancel after batch 1; assert the worker thread joins promptly (bounded wait)
   and status is `cancelled`. Extend `test_explore_cancel_stops_in_flight_job`.
5. `test_constant_column_detection_with_approx_distinct` — 10M-row single-valued column across
   dtypes: constant issue still fires (guards EF-09). Property-style; mark `perf` if slow.
6. `pytest.mark.perf` structural peak test — on a wide hi-card frame, assert peak RSS of
   `_build_frame_stats` stays within a generous multiple of a one-batch run (scaling-ratio bound,
   not wall-clock). Follow the repo's existing perf-mark conventions.
7. Regression: full-report equivalence test — batched output equals single-pass output field-by-
   field on a mixed fixture (protects the alias/parse sharing).

## Uncertainties (state in PR; do not oversell)

- Absolute 50M-row extrapolation is reasoned (accumulator-sum), not measured; the 3M fixture's few
  row-groups made even light aggs peak near frame size. The *relative* component costs are robust.
- HLL exactness for constant columns at scale — test 5 settles it; if it flakes, switch the
  constant gate to `min == max AND null_count < row_count AND nan_count == 0`.
- Whether `map_elements` inside the big select forces sub-plans off the streaming engine was not
  isolated (E05 removes the UDF anyway).
