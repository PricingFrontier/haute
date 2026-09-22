# PR #227 materialisation evidence inventory

This is a source evidence inventory for branch `banding-rating-ui`, compared with
`a71c947000e46f3b9331e96ec1a034eaa3739c97...97f3e9909998460bae5dbdfedf821eb9189581a6`.
Classifications use only the visible call context: schema-only,
bounded sample, full dataset, or uncertain.

## Runtime execution and output

| Location | Call and visible upstream bound | Classification | Lifetime / retention owner | Changed in PR |
|---|---|---|---|---|
| `src/haute/_polars_utils.py:109-124` | `_cancellable_collect()` calls `lf.collect(engine=engine, background=True)` and fetches the query until completion; no `head`, `limit`, or `select` bound is visible in this helper. | uncertain (helper is used for both bounded and full plans) | Local `query` and returned `result`; caller owns the returned frame. | yes |
| `src/haute/_polars_utils.py:148-164` | `execution_collect()` directly calls `lf.collect(engine=engine)` when no execution context exists. | uncertain | Returned frame owned by caller. | yes |
| `src/haute/_execute_lazy.py:3378-3381` | Execution path explicitly uses `streaming_collect` rather than bare `.collect()`; comment states bounded-memory contract. | full dataset (streaming execution of the plan; no row bound shown here) | Result is placed in runtime outputs/cache by surrounding executor; parent release bookkeeping is at `1642-1651`, `2171-2214`. | yes |
| `src/haute/executor.py:2180-2183` | `execute_output` documentation states output uses `sink_parquet`/`sink_csv`; full dataset is streamed and eager collect is not a fallback. | full dataset | Sink target is the output artifact; surrounding code deletes `lf` and trims at `2344-2349`; row count is read after sink at `2351+`. | yes |
| `src/haute/_polars_utils.py:636-645` | `streaming_sink()` invokes `lf.sink_parquet(..., lazy=True, engine="streaming")` (or CSV); no upstream row bound is visible in helper. | full dataset | Destination path owns persisted output; sink plan is local. | yes |
| `src/haute/_polars_utils.py:660-665` | `write_frame()` invokes `df.write_parquet()`/`write_csv()` on an already materialised DataFrame. | full dataset (input frame already materialised) | Destination path owns persisted output; caller owns `df`. | yes |
| `src/haute/_polars_utils.py:793-797`, `801+` | `bounded_hashed_sink()` routes a lazy frame through atomic streaming parquet sink; no row bound visible. | full dataset | Atomic destination path owns artifact; temporary target is managed by atomic writer. | yes |

## Preview, deployment, and source reads

| Location | Call and visible upstream bound | Classification | Lifetime / retention owner | Changed in PR |
|---|---|---|---|---|
| `src/haute/deploy/_container.py:510-516` | `pl.scan_parquet(result.result_path).head(returned_rows).collect()`, where `returned_rows = min(row_count, _QUOTE_RESPONSE_ROW_LIMIT)` at `511-513`. | bounded sample | Local `frame` is rendered into response content and then released; parquet result artifact owns full persisted data. | pre-existing |
| `src/haute/deploy/_batch_scoring.py:131` | `pl.scan_parquet(path).select(pl.len()).collect().item()` selects only row count. | aggregate-only (no data rows retained) | Scalar count only; source parquet path owns data. | pre-existing |
| `src/haute/_ram_estimate.py:585` | `pl.scan_parquet(path).select(variable_columns).head(min(row_count, max_rows)).collect()`; explicit column and row cap. | bounded sample | Local `probe` is used for estimate and released by function; source path owns data. | yes |
| `src/haute/modelling/_glm_pyfunc.py:42-43` | A lazy scoring expression is collected, then prediction column is converted with `to_numpy`; no bound visible in this function. | uncertain | Local `scored` and returned NumPy array; caller owns returned predictions. | pre-existing |
| `src/haute/_mlflow_io.py:1531-1543` | Selected frame is converted with `to_pandas()` or `to_numpy()`; no row bound visible in these helpers. | uncertain | Local conversion result is returned to caller; caller owns result. | yes |

## Training and model scoring

| Location | Call and visible upstream bound | Classification | Lifetime / retention owner | Changed in PR |
|---|---|---|---|---|
| `src/haute/routes/_training_preparation.py:872-918` | Prepared `target_lf` is written through `write_file()` under `training_sink_write`; the surrounding path is a training input sink and has no visible row limit. | full dataset | Temporary parquet path owns the staged training data; `lazy_outputs` and `target_lf` are deleted at `915`, followed by GC. | yes |
| `src/haute/modelling/_training_job.py:1600-1612` | Materialised `df.write_parquet(data_path)` writes training input; no bound visible. `df` is deleted after write. | full dataset | `data_path` owns temporary parquet; local `df` lifetime ends at `1609`. | pre-existing |
| `src/haute/modelling/_training_job.py:1948-2000` | `train_df`/`eval_df` target, weight, offset, and feature columns are converted with `to_numpy()` for CatBoost pools; no row bound visible. | full dataset (partition frames) | Arrays and frames are deleted after pool construction (`1964-1967`, `1978-1981`, `1999-2015`); CatBoost pool owns retained training data. | pre-existing |
| `src/haute/modelling/_training_job.py:2206-2240` | Diagnostics and optional validation frames convert target/weight columns with `to_numpy()`; no row bound visible in this routine. | full dataset (diagnostics partition) | Local diagnostic arrays/frames; `diag_df` is deleted at `2329`, then temporary split cleanup follows. | pre-existing |
| `src/haute/modelling/_algorithms.py:349-441` | `_build_pool()` selects feature columns and converts labels/weights/baseline with `to_numpy()`; no row bound visible. | full dataset (the supplied training frame) | CatBoost `Pool` owns the constructed data; intermediate arrays and selected frame are deleted at `390`, `436-439`. | pre-existing |
| `src/haute/modelling/_algorithms.py:192-200` | Offset column is cast and converted to NumPy for prediction baseline; no row bound visible. | uncertain | Returned NumPy array is consumed by caller; supplied frame owns source column. | pre-existing |
| `src/haute/_model_scorer.py:579-580`, `626-627` | Offset columns are cast and converted to NumPy during scoring; no upstream bound visible in these helpers. | uncertain | Returned array is consumed by scoring path; frame lifetime belongs to caller. | yes |
| `src/haute/_model_scorer.py:1966-1967` | Scoring chunk is converted with `chunk.to_arrow()` before parquet writing; chunk size is owned by caller/iterator, but no limit is visible at this call. | uncertain (chunk-bounded by caller, exact bound not visible here) | Arrow table is passed to parquet writer; chunk local lifetime is caller-controlled. | yes |
| `src/haute/_model_scorer.py:2017` | Empty DataFrame is converted with `to_arrow()` to write an empty parquet result. | schema-only / empty artifact | Output target owns empty parquet artifact. | yes |

## Optimiser and data output writes

| Location | Call and visible upstream bound | Classification | Lifetime / retention owner | Changed in PR |
|---|---|---|---|---|
| `src/haute/routes/_optimiser_service.py:1450-1454` | `df.write_parquet(artifact_path)` writes the apply result; `row_count = len(df)` immediately after; no upstream bound visible. | full dataset | Artifact directory/path owns persisted result; local `df` is caller-owned. | yes |
| `src/haute/routes/_optimiser_service.py:1490-1494` | `factors_df.write_parquet(artifact_path)`, then metadata is read; no upstream bound visible. | full dataset | Artifact path owns persisted factors; local frame remains caller-owned. | yes |
| `src/haute/routes/_optimiser_service.py:1897-1902` | Each `bucket_df` is written to `bucket_{bucket}/part_{batch_index}.parquet`; batch/bucket bounds are established by the surrounding batch loop but exact source selection is outside these lines. | full dataset (partitioned into bounded batches) | `bucket_files` retains part paths; each `bucket_df` is local to the batch. | yes |
| `src/haute/chunking.py:2220` | `frame.write_parquet(tmp, compression="lz4")`; no upstream bound visible at write. | full dataset (chunk frame) | Temporary parquet path owns persisted chunk; caller owns `frame`. | pre-existing |
| `src/haute/_chunked_writes.py:380` | Chunked writer calls `frame.write_parquet(...)`; chunk construction is outside the call site. | uncertain (chunk-bounded, exact bound not visible here) | Writer-managed part path owns persisted chunk. | yes |
| `src/haute/_chunked_writes.py:619`, `633` | Schema is converted with `pl.DataFrame(schema=schema).to_arrow().schema`; data frames use `conformed.to_arrow()` for Arrow writer tables. | schema-only at `619`; full current chunk at `633` | Arrow schema/table and writer are local; destination writer owns persisted output. | yes |
| `src/haute/_json_shred_writer.py:169`, `214` | `frame.to_arrow().schema` derives schema; `frame.to_arrow()` writes a table. | schema-only at `169`; uncertain/full current frame at `214` (no upstream bound visible) | Writer owns output; frame/table locals are caller-managed. | pre-existing |

## Analysis and related helpers

| Location | Call and visible upstream bound | Classification | Lifetime / retention owner | Changed in PR |
|---|---|---|---|---|
| `src/haute/routes/_optimiser_service.py:1741-1743` | Histogram values use `col.to_numpy()`; no filter/limit visible in helper. | uncertain | NumPy values and histogram arrays are local to analysis result. | yes |
| `src/haute/modelling/_metrics.py:492`, `914` | Metric/feature analysis converts a column or null-dropped sample column to NumPy; `914` visibly uses `sample_df[feat].drop_nulls()`, but no row cap is shown there. | uncertain | Local arrays are consumed by metric/analysis calculation. | pre-existing |
| `src/haute/modelling/_rustystats.py:53-58` | Series are converted to NumPy for categorical level matching and null mask; no upstream bound visible. | uncertain | Local arrays/masks are consumed by RustyStats calculation. | pre-existing |

## Related tests and benchmark scripts

The repository contains direct coverage for these boundaries in the following
modules (file inventory only):

- Execution and sinks: `tests/test_execute_lazy.py`, `tests/test_execute_lazy_contracts.py`, `tests/test_execute_lazy_dataframe_cache.py`, `tests/test_executor.py`, `tests/test_bounded_sink_contract.py`, `tests/test_polars_utils.py`, `tests/test_sink.py`, `tests/test_chunked_writes.py`.
- Preview/snapshot/source reads: `tests/test_preview_snapshot_seeding.py`, `tests/test_snapshot_seeding.py`, `tests/test_trace_snapshot_seeding.py`, `tests/test_ram_estimate.py`, `tests/test_deploy_batch_scoring.py`, `tests/test_model_scorer_eager_single_execution.py`.
- Training/model conversion: `tests/test_training_preparation_worker.py`, `tests/test_training_memory_safety.py`, `tests/test_training_split_streaming.py`, `tests/test_training_null_target_fused_split.py`, `tests/test_train_param_routing.py`, `tests/test_algorithms_coverage.py`, `tests/test_model_scorer.py`.
- Optimiser/analysis/data output: `tests/test_optimiser_frontier_materialisation.py`, `tests/test_optimiser_routes.py`, `tests/test_optimiser_apply.py`, `tests/test_data_output_seeding.py`, `tests/test_analysis_results.py`, `tests/test_banding_stats.py`.
- Performance scripts: `tests/performance/test_write_strategy_memory.py`, `tests/performance/_write_strategy_memory_probe.py`, `tests/performance/test_execution_engine_certification.py`, `tests/performance/test_preview_trace_perf.py`, `tests/performance/test_optimiser_memory_response_perf.py`, `tests/performance/test_training_scoring_wide_perf.py`.

The source paths in this inventory are marked from exact
`git diff a71c947000e46f3b9331e96ec1a034eaa3739c97...97f3e9909998460bae5dbdfedf821eb9189581a6`
status checks; this document itself is newly added on the branch.

The companion join benchmark recorded these repeated write timings and external
child peaks:

| Rows | Strategy | Write seconds (rep 1 / rep 2) | Child peak RSS MiB (rep 1 / rep 2) |
|---:|---|---:|---:|
| 100,000 | native | 0.039 / 0.028 | 140.5 / 140.7 |
| 100,000 | JoinRecipe | 0.105 / 0.082 | 141.5 / 139.0 |
| 400,000 | native | 0.061 / 0.076 | 228.0 / 227.9 |
| 400,000 | JoinRecipe | 0.364 / 0.330 | 221.1 / 207.9 |

The external peak includes post-write verification. These are two small,
in-memory-scale cases and do not certify out-of-core behaviour. Verification
checks row count plus key and payload sums, rather than full per-row equality.
Both strategy modules were imported before the timer; the benchmark used the
base interpreter path to avoid the Windows process launcher.

## Targeted verification

Command run exactly:

```text
uv run pytest tests/test_chunked_writes.py tests/test_bounded_sink_contract.py tests/test_polars_utils.py tests/test_data_output_seeding.py tests/test_training_memory_safety.py tests/test_training_split_streaming.py tests/test_optimiser_frontier_materialisation.py -q
```

Result: PASS — 275 passed, 58 warnings, exit code 0, elapsed 84.99s
(0:01:24). The output also reported 8 out-of-sandbox writes in the test
harness census; no production or test files were modified by this task.
