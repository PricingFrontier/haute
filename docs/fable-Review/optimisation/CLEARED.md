# CLEARED — behaviours adversarially checked and found correct

**Do not "fix" anything on this list.** Each item was explicitly investigated (five reviewers +
coordinator re-verification at HEAD `2caa4134`) and found to be correct, deliberate, and in
several cases exemplary. Line refs locate by symbol if drifted.

## Solve lifecycle & concurrency
- Solver never runs under a lock: `_solve_background` does its `atomic_update` then calls the
  solver outside any lock (`_optimiser_service.py:4879-4924`); `_start_lock` covers only job
  registration (`:2566-2599`).
- Setup checkpoint dir is `tempfile.TemporaryDirectory` context-managed — removed on every exit
  path including crash (`:2690`); the grid temp parquet is unlinked in `finally` with a logged
  fallback (`:4796-4806`). No staging leaks in-process.
- Completion race handling is thorough: when the completed-transition is skipped or
  `artifact_handles` was replaced, both apply and factor artifacts are cleaned
  (`:2285-2317`); `_extract_factors` cleans its own handle on failure (`:4677-4701`); setup
  `finally` cleans when launch never started (`:2888-2906`). Double-release of
  registries/single-flight is idempotent by design.
- `# M6` direct `store.jobs` reads from the worker are safe: `atomic_update` swaps the whole
  dict under `_write_lock`, so unlocked readers see old-or-new, never partial; bypassing
  `_evict_stale` from a background thread is the documented intent (`_job_store.py:382-423`).
- `gc.collect()` calls (`:2742`, `:3516`) are one-shot per setup after dropping the lazy output
  map — defensible, not a per-row hammer.
- Terminal-reason precedence prevents a later timeout overwriting a completed result
  (`_job_lifecycle.py:48-55`); `expected_status` guards on every completion/progress update.

## Job store & artifacts
- `JobStore` mutations linearise on `_write_lock` with whole-dict atomic swap; guarded updates
  return `None` so callers must handle the race (`_job_store.py:382-456`).
- Heavy-object retention is race-safe: `touch_heavy_objects` extends `expires_at` and
  reschedules; a stale timer bails when the expiry moved (`:299-336`, `:164-181`).
- Server-owned parquet handles are traversal-validated — absolute, NUL-free, resolved, under
  the artifact root, exact dir-prefix and filename — before any read or delete
  (`_optimiser_service.py:979-1021`).
- `get_job_store` prefix allow-list is closed, bounding the `functools.cache`
  (`_job_store.py:561-607`).
- `_optimiser_io.py` artifact cache is content-hash keyed (mtime-immune, same-second-overwrite
  safe) and returns deep copies; MLflow variant resolves "latest" per call so version bumps
  miss the cache correctly (`_optimiser_io.py:32-74`, `:84-157`).

## Limits & HTTP contract
- Frontier truncation is not silent: payload carries `n_points` / `points_returned` /
  `points_limit` / `points_truncated` (`_optimiser_limits.py:98-106`); requesting a dropped
  point yields an actionable 400 naming retained-vs-total (`optimiser.py:418-427`).
- Apply preview truncation is surfaced (`row_count`/`preview_truncated`,
  `_optimiser_limits.py:72-78`).
- `enforce_frontier_compute_budget` overflow-proofs the `n^d` projection with repeated
  multiplication and pins its cap to the library's `max_total_points` via a real-library test
  (`_optimiser_limits.py:30-62`).
- Ratebook `/apply` detail is rejected with an explicit 422 before any solver/artifact work
  (`optimiser.py:119-129`), with defense-in-depth inside materialisation.

## Auto-range numerics & design
- Disk-bucket partitioning is **justified** (initial "why not single-pass min/max?" hypothesis
  refuted): the envelope is Σ over quotes of per-quote scenario extrema, which requires a
  per-quote group-by; bucketing bounds group-by memory to ~1/partition_count of distinct quotes
  (`_optimiser_service.py:1467-1527`).
- Cross-batch quote recombination is exact: `hash(quote_id) % partitions` routes every
  occurrence of a quote to one bucket; `finish` re-groups per quote within the bucket
  (`:1471`, `:1523-1527`; verified with a split-quote reproduction).
- Degenerate inputs fail loudly: empty frame, all-null quote_id, non-1-row bucket totals all
  raise (`:1504-1511`, `:1541-1542`); NaN/inf constraint values are rejected pre-aggregation at
  Float32 precision (`:4443-4463`, `:4587-4598`). (Nulls are the gap — P03.)
- `ChunkPlanUnsupportedError` surfaces loudly as a 422 (`:935-942`, `:3415-3417`) rather than
  silently widening to the high-memory path; `ProjectionImpossibleError`'s fallback to
  non-streaming (`:3408-3414`) is deliberate and distinct.
- Memory-limit handling is uniform: admission/limit errors map to a 507 `memory_limit` payload
  and a `memory_limited` transition in both run paths and the sync estimate.
- `_optimiser_input_metrics` folds quote counts, per-quote scenario stats, and the null-qid
  check into one grouped streaming scan (`optimiser.py:297-332`) — the pattern P07 extends to
  the fallback path.

## Apply / explainability agreement
- Online apply and online trace share `_prepare_online_apply_frame` — identical casts and
  null-qid filtering, so apply/explain agree by construction (`_builders.py:1478`,
  `_optimiser_apply_explainability.py:168`).
- Ratebook key canonicalisation is shared (`normalise_rating_key` / `_rating_key_expr`) across
  engine join, trace, and solve-side level counts, pinned by `test_rating_key_agreement.py`;
  duplicate levels resolve last-wins on both sides (`_rating.py:632` vs reversed-entries walk).
- Null factor keys never match on either side (engine `join_nulls=False` + explicit None-guard
  in `_match_ratebook_entry`), then `fill_null(1.0)` counted by the rating miss guard —
  loud-neutral, not silent.
- Artifact `mode` is authoritative over node config (`_builders.py:1413`, `:1455`).
- The ratebook trace input-row match pushes equality predicates into Polars and only
  materialises the matching row, with a bounded-batch fallback for cross-dtype cases
  (`_optimiser_apply_explainability.py:474-545`).
- `_compute_scenario_value_stats` `n==1 → std = 0.0` is a documented, correct population
  convention required by the response schema (`_optimiser_service.py:1292-1299`).

## Engine (`price_contour`) — see also UPSTREAM-price-contour.md
- Fail-loud discipline holds: no swallowed exceptions in the Python layer (sole `except` is the
  documented version-metadata fallback in `__init__.py`).
- DataFrame-path validation rejects nulls and NaNs column-named before the Rust hot path
  (`solver.py:1639-1674`); ratio specs are validated thoroughly (`:658-747`); ratio-label vs
  column collisions rejected up front (`:1014-1022`).
- Grid/factor-context alignment enforced by 64-bit fingerprint + n_quotes match
  (`ratebook.py:1227-1261`) — quote-axis misalignment cannot pass silently.
- `ApplyOptimiser.save/load` has a version gate and unknown-key allowlist (`apply.py:420-471`).
- Ratio wrappers bounce dunders in `__getattr__` (no pickle/copy recursion traps)
  (`solver.py:1255-1257`); `_stitch_optimal_ratio_columns` inner-join + height assertion fails
  loudly on a missing chosen step (`_ratio_results.py:96-107`).
- Ratebook warm start is predictor-corrector: a bad predictor costs extra iterations, never a
  wrong answer (`ratebook.py:881-890`).
