# M05 — Training-path performance: PDP explosion, unpruned partition scans, default-on fsync telemetry

**Severity: HIGH (two) + MEDIUM–HIGH (one) + MEDIUM (one) + LOW (five)**
**Basis: full data-path accounting of `_train_service._execute_and_sink` → `TrainingJob` (route path and direct-API path) on the code as written; CatBoost-side settings verified against the installed package.**

Already well-optimised (verified — preserve these properties in any refactor):
projection pushdown into every partition read (`_scan_with_columns`,
`_training_job.py:1035-1043`); the diagnostics partition is read **once** and reused for
metrics + AvE + SHAP + LossFunctionChange + residuals + Lorenz + PDP; y/w/baseline arrays are
extracted and the wide frame freed **before** pool construction (avoids the Polars+pandas+Pool
triple copy); numeric features cast to float32 and the pandas round-trip skipped entirely when
no categoricals exist (`_mlflow_io.py:1109-1116`); streaming `bounded_sink` everywhere; lz4
for throwaway temp parquet; the RAM/VRAM pre-flight estimate is real and metadata-based
(instant). Note: the README's "probes a sample of your data" wording is stale — the estimator
reads parquet footers (`_ram_estimate.py` module docstring); fix the README line.

## Data-path accounting (route entry, holdout enabled, some null targets)

| # | Operation | Site | Full passes over the dataset |
|---|-----------|------|------------------------------|
| W1 | Pipeline sink → `haute_train_*.parquet` | `_train_service.py:946-960` | 1 write |
| R0 | Null-target count (1 col) | `_training_job.py:662-666` | ~0.1 read |
| W2 | Split sink (+`_partition`, fused null filter) | `_training_job.py:806-810` | 1 read + 1 write |
| R1 | Train partition read | `_training_job.py:875-881` | 1 full scan |
| R2 | Validation partition read (eval pool) | `:887-893` | 1 full scan |
| R3 | Holdout (diagnostics) read | `:1130-1136` | 1 full scan |
| R4 | Validation re-read (metrics, holdout present) | `:1159-1165` | 1 full scan |

**≈ 2 full writes + 5 full reads.** Direct-API entry (DataFrame input + null targets) adds a
collect + temp write + a full "clean" rewrite → 3 writes before any partition read.
R1–R4 are full scans because the split file is written **unsorted**: partition labels are
randomly interleaved, so parquet row-group pruning can never skip anything.

---

## PERF-01 (HIGH): PDP does one predict per (feature × grid value), uncapped

`compute_pdp` (`src/haute/modelling/_metrics.py:838-933`) loops every feature × up to 50
grid values, rebuilding the 500-row sample frame and calling `algo.predict` **per grid
value** — each call paying `_prepare_predict_frame` marshalling + CatBoost setup. Called with
the full feature list, no cap (`_training_job.py:1230`), unlike `compute_ave_per_feature`
which has `max_features`. A 100-feature model ⇒ ~4,000–5,000 serial predict calls of 500
rows: seconds-to-minutes of pure post-fit diagnostics.

**Fix:** (a) batch per feature — tile sample × grid into one `500·n_grid`-row frame, one
predict, average per block (numerically identical, ~50× fewer calls); (b) cap PDP to top-N
(15–20) importance-sorted features, mirroring AvE's `max_features`; the UI renders one chart
per feature and nobody reads chart #87.
**Verify:** equivalence test batched-vs-loop grids ≤1e-9; predict-call count drops ~40×;
wall-clock ≥10× on a 100-feature model.

## PERF-02 (HIGH): split parquet unsorted → every partition read scans 100% of the file

`_split_data` sinks `split_lf.with_columns(mask)` unordered (`_training_job.py:805-810`);
each read filters `_partition == k` over all row groups (`:877, :889, :1066`). On 5–20M rows
that's ~3 redundant full decompress+scans to keep 16–64% of rows.

**Fix (preferred):** partitioned sink (`partition_by="_partition"` / explicit per-partition
files) so each partition read opens only its own file. Fallback if the pinned Polars lacks
it in streaming: `sort("_partition")` pre-sink so row groups are partition-homogeneous and
predicate pushdown prunes. Cleanup sites must then handle a directory
(`_cleanup_owned_temp_parquets`, unlinks at `:710, :824, :1269`).
**Verify:** per-partition `bytes_read` ≈ its row fraction (the ExecutionContext stage metrics
already record this); partition row counts unchanged.

## PERF-03 (MEDIUM–HIGH): the split write duplicates the entire dataset to add one Int8 column

W2 reads all of W1 and writes a near-copy. With PERF-02's partitioned sink this same write
becomes the pruning enabler, so do them as one change; a true single-write design (partition
during the pipeline sink) needs the row count pre-sink and is only worth it later.
**Verify:** `_split_data` + first-partition-read wall-clock before/after.

## PERF-04 (MEDIUM): validation read twice and re-predicted; eval arrays discarded

Eval pool is built then freed (`:947-969`); `_compute_metrics` re-reads validation (`:1159`)
or reads it as the diagnostics set (`:1130`). Cheap once PERF-02 lands; if pursued, retain
only the 1-D `y_true/y_pred/w` arrays from the eval step — never the wide frame.

## PERF-05 (LOW–MEDIUM, hygiene): `_mem_checkpoint` defaults to `~/training_mem.log` with per-line fsync

`_algorithms.py:24` defaults `_MEM_LOG` into the user's **home directory**; every checkpoint
re-opens, writes, flushes, and `os.fsync`s (`:94-112`) — ~50–80×/run plus every 50 fit
iterations (`:189-192`). The route path truncates the file each run (`_train_service.py:820`)
but direct `TrainingJob` use grows it forever.
**Fix:** invert the default — no-op unless `HAUTE_MEM_LOG` is set; open once per run; fsync
only under the debug flag. Never default into `~`.
**Verify:** training with the var unset creates no file; fit wall-clock unchanged.

## PERF-06 (LOW): GPU progress polling re-`readlines()` the whole `learn_error.tsv` every 2s

`_algorithms.py:386-410` — O(polls × file size); trivially fixed by tracking a byte offset
and seeking. Negligible today, quadratic by construction.

## PERF-07 (LOW): diagnostics pool builds are genuinely necessary — no change

The LossFunctionChange pool (`_training_job.py:1203-1214`) is new work when holdout exists
(holdout was never pooled during fit); SHAP subsamples ≤1000 rows. Documented here so nobody
"optimises" it into incorrectness.

## PERF-08 (LOW): Windows `_malloc_trim` compacts the CRT default heap, which Polars likely doesn't use

`_polars_utils.py:372-387` calls `HeapCompact(GetProcessHeap())`; Rust/Polars allocations
generally don't live there, so this may be a no-op heap walk on every call site. Measure RSS
around it on a real run; if Δ≈0, make it Linux-only.

## PERF-09 (LOW): multi-GB training temps always land on the `%TEMP%` volume

`_train_service.py:833`, `_training_job.py:616,680,792`. On split SSD/HDD laptops the system
temp may be the small/slow disk. Offer a configurable training scratch dir (or co-locate with
`_pipeline_dir` outputs).

## CatBoost-side notes (no defects)
- `thread_count` unset → all logical cores (right default for a laptop trainer; expose as a
  param only if oversubscription complaints appear).
- Eval-pool quantization is handled internally by CatBoost against train borders; no manual
  `Pool.quantize()` needed at current scale.
- Per-iteration job-store update (`_train_service.py:1068-1088`) is fine at CPU iteration
  counts; throttle to every N iterations only if 10k+-iteration runs become common.

## Suggested benchmark harness
One script, repo venv: synthetic 10M×100 frame (~20 categoricals, skewed target, a few %
null targets), run `TrainingJob` end-to-end with holdout, and read per-stage `elapsed_ms` /
`bytes_read` / `bytes_written` / `rss_peak_bytes` from the existing
`ExecutionContext.metrics_payload()` stages (`training_split_parquet_write`,
`training_train_partition_materialise`, `training_*_diagnostics_materialise`; add a
`training_pdp` stage with PERF-01). Before/after deltas for PERF-01/02/03/04 then need no new
plumbing.
