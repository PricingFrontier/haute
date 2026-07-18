# P07 — Redundant full passes over the biggest frames in setup, stats, and previews

**Severity:** MEDIUM (dominant setup latency) · **Effort:** M

The scenario-expanded frame (rows × scenarios) is the largest artifact in the optimiser
lifecycle; each finding below removes a full pass over it or an equivalent repeated scan.

## FO-14 — [MEDIUM] Solve setup executes the expanded frame twice: validation pass + sink pass
`routes/_optimiser_service.py:4506-4511` (`_validate_input_value_contracts` streaming collect)
and `:4731-4735` (`_build_grid` `bounded_sink`)

### Evidence
```python
# pass 1 — validation aggregates over the full expanded frame
validation_counts = streaming_collect(source_lf.select(validation_exprs), profile=...)
# ... later, pass 2 — the same lazy plan is executed again to sink for the grid builder
bounded_sink(scored_lf, tmp_path, streaming_chunk_size=...)
# pass 3 — Rust build_grid_from_parquet_chunked re-reads the sunk parquet (cheap, columnar, necessary)
```
When the optimiser input is not checkpoint-backed, passes 1 and 2 each re-execute the upstream
lazy graph (scenario expansion, model scoring, transforms) end-to-end.

### Fix design
Reorder: **sink first, validate from the sunk parquet.** `_build_grid` already sinks
`scored_lf` to a temp parquet; move the sink ahead of validation (or return the path), then run
the validation aggregation as `pl.scan_parquet(tmp_path).select(validation_exprs)` — a cheap
columnar scan of local parquet instead of a second upstream execution. The null/NaN/inf checks
(including P03's additions) stay in one aggregation. Note the cast asymmetry: validation
currently checks pre-cast Float64 values *at Float32 precision* via `cast_to_float32_cols`
(`:242`) — sinking the already-cast `scored_lf` preserves exactly those semantics (values that
overflow to ±inf on the cast are then caught by the inf check on the sunk data; the detail
message wording is unchanged).

## FO-15 — [MEDIUM] Auto-range fallback path double-scans the expanded frame
`routes/_optimiser_service.py:4587-4598` (validation scan) + `:1619-1647`
(`_estimate_scenario_frontier_ranges` batch scan)

### Evidence
The non-streaming fallback in `_run_frontier_auto_range_job` first runs
`_validate_and_project_auto_range` (full `streaming_collect` of validation exprs), then
`bounded_collect_batches` over the same frame for the accumulator — 2× IO over the expanded
checkpoint. (The streaming path validates the in-memory chunk it aggregates, so it is
unaffected; the estimate endpoint already folds its null check into a single scan —
`optimiser.py:292-332` — this path just never got that treatment.)

### Fix design
Fold the per-column null/NaN/inf counters into the accumulator's `add_batch` (extra aggregate
expressions per batch, summed in `finish`) and raise the loud contract error before range
assembly. Keep the message identical to the standalone validation's.

## FO-16 — [MEDIUM] Ratebook factor-level counts scan the factor artifact once per factor group
`routes/_optimiser_service.py:1898-1916` (`_ratebook_factor_level_counts`)

### Evidence
```python
for columns in factor_columns or []:
    grouped = factors_df.group_by(columns).agg(pl.len().alias("quote_count"))
    if is_lazy:
        with temporary_streaming_chunk_size(chunk_size):
            count_rows = streaming_collect(grouped, profile=...).to_dicts()
```
`factors_df` is `pl.scan_parquet` over the per-quote factor artifact (`:1941-1945`); K factor
groups ⇒ K sequential full scans — on top of `build_ratebook_factor_contexts_from_parquet_chunked`
reading the same file again (`:2449-2455`).

### Fix design
Build all K grouped lazy frames, execute one `pl.collect_all([...])` (streaming engine) under a
single `temporary_streaming_chunk_size` so Polars shares the scan; keep the existing per-group
canonicalisation and the loud level-collision check (`:1922-1927`) on the collected results.

## FO-17 — [MEDIUM] `/apply` loads the full apply-result parquet to serve a 100-row preview
`routes/_optimiser_service.py:1196-1197` (`_load_apply_result_artifact` → `pl.read_parquet`)
+ `routes/_optimiser_limits.py:65-78` (`limited_apply_preview_payload` head(100))

### Fix design
`pl.scan_parquet(path).head(APPLY_PREVIEW_ROW_LIMIT).collect()` for the preview; take
`row_count` from `read_parquet_metadata` (already used at `:1109`) instead of `len(df)`.
Callers that genuinely need the full frame (none found on the preview path — verify
`_materialise_frontier_point_apply`'s reuse) keep the eager loader.

## FO-18 — [MEDIUM] `_compute_scenario_value_stats` makes ~11 passes plus a copy over the per-quote column
`routes/_optimiser_service.py:1300-1319`

### Evidence
11 separate Series aggregations (`mean/std/min/max/5×quantile/2×comparison-sum`) — the five
`quantile` calls each re-sort — then `col.to_numpy()` copies the column again for the
histogram. Runs on every completed solve; the column is one row per quote.

### Fix design
Two options, either acceptable:
- Single `df.select([...])` computing all aggregates in one expression graph (Polars
  parallelises across expressions), keeping `quantile(..., interpolation="nearest")` — the
  current per-call default — **explicit**, so values do not silently shift; or
- Reuse the `to_numpy()` copy already taken: one `np.sort`, slice percentiles with
  `method="nearest"`, and derive min/max/mean/std from the same array.
Keep the documented `n == 1 → std = 0.0` convention (`:1292-1299`) and `n == 0 → (None, None)`.

## TDD plan (failing tests first)
1. `tests/test_optimiser_service_coverage.py::test_solve_setup_single_upstream_execution` —
   optimiser input behind a `map_batches` counter node (or a scan spy); run solve setup; assert
   the upstream plan executes once (currently twice). Structural assertion, not wall-clock.
2. `tests/test_optimiser_routes.py::test_auto_range_fallback_single_scan` — mirror the estimate
   path's single-scan test (`test_estimate_null_quote_id_rejected_within_single_scan` pattern):
   scan-count spy over the checkpoint; assert one pass, null rejection still loud (P03 tests
   compose here).
3. `tests/test_optimiser_service_coverage.py::test_factor_level_counts_single_collect_for_multiple_groups`
   — two factor groups; spy `streaming_collect`/`collect_all`; assert one execution; counts
   byte-identical to the per-group results.
4. `tests/test_optimiser_routes.py::test_apply_preview_scans_not_reads` — tall apply artifact;
   spy `pl.read_parquet` (not called on preview path) vs `pl.scan_parquet`; preview ≤ 100 rows,
   `row_count` exact from metadata.
5. `tests/test_optimiser_service_coverage.py::test_scenario_value_stats_values_unchanged` —
   real `pl.DataFrame` (not the existing mock): assert post-refactor stats byte-equal
   pre-refactor values (pin `interpolation="nearest"`), plus `n==0` and `n==1` edges the
   current mock-based test never exercises.

### Acceptance
- Solve setup: one upstream execution + one local parquet re-read (was two upstream + one).
- Auto-range fallback: one pass. Factor counts: one collect. Apply preview: no full read.
- Stats numerically identical; quantile interpolation pinned explicitly.
- Perf suite (`tests/performance/test_optimiser_memory_response_perf.py`) unchanged or better.
