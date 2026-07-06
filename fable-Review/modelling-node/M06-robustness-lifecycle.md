# M06 — Robustness & lifecycle: exceptions that cross the CatBoost boundary, work-loss on MLflow failure, Windows file hazards

**Severity: HIGH (two) + MEDIUM (six) + LOW–MEDIUM (two)**
**Key probe fact (installed catboost 1.2.10, `core.py` `_TrainCallbacksWrapper` ~line 7584): an exception raised inside a Python `after_iteration` callback is caught in CatBoost's C++ layer and re-raised as `catboost.CatBoostError` — the original exception type is lost, its repr embedded as a string.** That single fact drives M06-1 and M06-3.

Genuinely well-engineered here (verified — preserve): the `owns_tmp` temp-parquet discipline
with abort nets that never mask the in-flight exception; the GPU zombie-thread annotation
(`add_note` + retained train_dir under a live writer); the job-lifecycle precedence table
(`_job_lifecycle.py:48`) that stops a late `error` from clobbering `cancelled`/`timed_out`;
MLflow artifact-cache locking against Windows rename-over-open-file; score-time contract
enforcement that raises instead of casting; deploy modelScore refusing to degrade to
passthrough. This subsystem's fail-loud discipline is mostly real — the findings below are
the places it leaks.

---

## M06-1 (HIGH): memory-limit aborts during a CPU fit are misclassified as generic errors

On the live CPU path, progress/cancel/memory checks run **inside** the CatBoost callback:
`_CatBoostProgressCallback.after_iteration` (`_algorithms.py:172-210`) → `_on_iteration`
(`_train_service.py:1068`) → `_raise_if_training_stopped` (`:624`) →
`execution_context.checkpoint` — which raises `ExecutionMemoryLimitExceededError` on breach.
That raise crosses the C++ boundary and comes back as `CatBoostError`, so in
`_train_background`'s handler chain (`:1157-1202`) the dedicated
`except ExecutionMemoryLimitExceededError` (`:1170`) never fires; the catch-all does. The job
ends `error` with a cryptic "CatBoost error: …helpers.cpp:58…" instead of `memory_limited` +
`error_code="memory_limit"` + the structured 507 payload the UI turns into "reduce rows /
row limit" guidance. The memory ceiling is the core laptop-safety mechanism; when it fires
mid-fit (the likeliest moment), its message is destroyed. GPU is unaffected (its
`on_iteration` runs on the polling thread, outside `fit()`).

**Fix:** never let exceptions cross the boundary. In `after_iteration`, wrap the
`self._on_iteration(...)` call: on `BaseException`, stash it on the callback and
`return False` (CatBoost's documented clean-stop signal — verified: fit stops at that
iteration without error). After `model.fit(...)` returns (`_algorithms.py:559`), re-raise the
stashed exception. Original types then reach the typed handlers.

**TDD (failing first):** `CatBoostAlgorithm.fit` with an `on_iteration` raising
`ExecutionMemoryLimitExceededError` at iteration N → fit re-raises that type (today:
`CatBoostError`). Integration: terminal job status `memory_limited` with
`error_code="memory_limit"`.

## M06-2 (HIGH): an MLflow logging failure after a successful train discards the result

`TrainingJob.run` (`_training_job.py:515-552`) saves the model + contract to disk, builds
`TrainResult`, **then** calls `_log_to_mlflow`. If logging raises (tracking server down,
registered-model collision, permission error), the exception propagates and the job ends
`error`: no `model_path`, no metrics, no diagnostics — even though a fully valid model sits
on disk. An expensive train is reported as failed for a logging-side fault. (The model-card
step inside `_mlflow_log.py:369` is already best-effort; the model/params/metrics logging
before it is not.)

**Fix:** post-training logging must be non-fatal to the result: catch, record a structured
entry (reuse `diagnostics_errors` / job `warning` — surfaced, not swallowed), return the
completed `TrainResult`. Cancellation raised through `check_cancelled` inside logging must
still abort.

**TDD:** patch `_log_to_mlflow` to raise → `run()` returns a result carrying
`model_path`/metrics + the surfaced logging error; job terminal status `completed`.

## M06-3 (MEDIUM): user cancellation during a CPU fit logs `training_failed` at ERROR level

Same wrapping as M06-1: `BackgroundJobStoppedError` → `CatBoostError` → catch-all logs
`training_failed` with a C++ traceback. Final job status is still correct (the lifecycle
precedence table makes the late `error` transition a no-op — this is why cancel "works"
today), but every ordinary cancel/timeout emits a crash-looking ERROR log. Fixed for free by
M06-1's capture-and-reraise; add a test pinning cancel → terminal `cancelled` with **no**
`training_failed` log.

## M06-4 (MEDIUM): unguarded memory-log writes can abort training entirely

`_MEM_LOG` (default `~/training_mem.log`) is written unguarded at `_algorithms.py:106`
(`open(...,"a")`) and truncated at `_train_service.py:820` (`write_text("")`) before the
pipeline runs. A locked/roaming/read-only home dir or bad `HAUTE_MEM_LOG` raises `OSError`
and the *diagnostic side-channel vetoes training*. (The fsync is guarded; the open is not.)
**Fix:** best-effort with a log-once warning — pairs with M05 PERF-05 (make the whole
facility opt-in). **TDD:** unwritable `HAUTE_MEM_LOG` → training completes + single warning.

## M06-5 (MEDIUM): success-path `os.unlink` of just-scanned parquet risks Windows `PermissionError`

The loud-but-safe `_remove_temp_parquet` helper exists (`_training_job.py:56`) but three
success-path deletes bypass it: `_split_data` (`:824`), `_compute_metrics` (`:1269-1270`),
and the null-clean unlink (`:710`). A lingering Polars scan handle on Windows (WinError 32)
turns cosmetic cleanup into a crashed training. **Fix:** route all three through
`_remove_temp_parquet`. **TDD (Windows-guarded):** open scan handle on the split file →
cleanup warns, run completes.

## M06-6 (MEDIUM): no startup sweep for orphaned multi-GB training temps

`haute_train_* / haute_split_* / haute_clean_*` land in the OS temp dir; cleanup runs only on
success hand-offs and in-process abort nets. A hard kill (power loss, taskkill, OOM) orphans
multi-GB files forever — no sweep exists (grep: only the writers reference the prefixes;
the scorer's `atexit` doesn't run on kill). **Fix:** age-gated startup sweep (>1h, not owned
by a live job), logging every reclaimed file. **TDD:** stale-mtime files reclaimed; fresh
ones spared.

## M06-7 (MEDIUM): tiny datasets silently disable validation, early stopping, and honest metrics

`n_validation = int(n_rows * validation_size)` (`_split.py:251`) can be 0 on small frames →
no eval pool → no early stopping → `diagnostics_set="train"`: headline metrics are
**in-sample** with nothing telling the user (the UI does label the set, but no warning says
"your 0.2 validation request produced 0 rows"). **Fix:** structured warning on
`TrainResult`/job when requested sizes produce empty partitions ("metrics are in-sample,
early stopping disabled"). **TDD:** 4-row frame + validation_size=0.2 → warning present,
`diagnostics_set` honest.

## M06-8 (MEDIUM): the per-model feature contract never reaches MLflow or deployed bundles

Training writes `{name}.feature_contract.json` (remediation 4b.9), but `log_experiment` /
`_log_model_with_signature` (`_mlflow_log.py:405`) never upload it, and the deploy bundler's
`_bundle_feature_contract` (`deploy/_bundler.py:145`) looks only for the **legacy bare**
`feature_contract.json` that training never writes (its own docstring admits this). So an
MLflow-sourced deployed model carries no contract; drift detection rests on the
`ModelSignature` alone, which logs as `None` when feature metadata was missing. **Fix:** log
the per-model contract as a run artifact next to the model; teach the bundler to prefer
`{model_stem}.feature_contract.json` (bare name as fallback). This is also where M01's
offset field must ride once added. **TDD:** train→log → run has the contract artifact;
bundling a per-model-contract dir picks it up.

## M06-9 (LOW–MEDIUM): model/contract saves are not atomic under a concurrent reader

`model.save_model(path)` (`_algorithms.py:674`) and `save_contract`
(`_feature_contract.py:137-155`) write in place over files the stat-gated caches
(`(mtime_ns, size)`-keyed) and deploy scorers may be reading; a retrain-while-scoring can hit
`PermissionError` or expose a torn file within one mtime window. **Fix:** write
`*.tmp` + `os.replace` (atomic on Windows and POSIX). **TDD:** interleaved save/read sees
whole-old or whole-new, never truncated.

## M06-10 (LOW–MEDIUM): thin request-time validation defers avoidable faults to opaque background errors

`_validate_config` (`_train_service.py:643-668`) checks target/algorithm/GLM-links only.
Deferred-to-late failures: unknown `params` keys (constructor TypeError after the full
pipeline sink), non-numeric weight (cast error deep in `_train_model`), Tweedie
variance_power out of range (M02-1). Genuinely **silent**: negative weights (CatBoost
accepts; fit is wrong), and `offset` + `task="classification"` (baseline applied in log-odds
space — almost never what an actuary means). **Fix:** fast 400s at request time for:
variance_power ∉ (1,2); offset+classification (reject or require explicit intent); negative
weights (validate min ≥ 0 during data prep — schema-only validation can't see values);
optionally sanity-check params keys. **TDD:** each rejected config → 400 naming the rule
(today: background job that errors minutes later or trains silently wrong).

## Test-coverage gap summary
`tests/test_modelling.py` has zero coverage for: callback-time cancellation/memory-limit,
CatBoostError wrapping, MLflow-failure result preservation, unwritable mem-log, Windows
unlink-under-scan, orphan sweeps, empty-validation warnings, contract-to-MLflow/bundle
propagation, atomic saves. The TDD items above are the missing suite.
