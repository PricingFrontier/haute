# MLflow Model Registry — Low-Level Specification

## Module map

| File | Responsibility |
|---|---|
| `src/haute/_mlflow_io.py` | Model loading, disk + in-memory caching (keyed in part on artifact byte identity via `_local_artifact_fingerprint`), per-artifact I/O locks, artifact discovery, flavor-specific loaders, the `ScoringModel` carrier, predict-frame preparation per flavor, the shared eager-scoring delegate (`_score_eager`) used by both `_model_scorer.py` and deploy. |
| `src/haute/_mlflow_utils.py` | Shared MLflow bootstrap used by `_mlflow_io.py`, the optimiser IO layer, and deploy's bundler: version resolution, safe model-version search, and `resolve_mlflow_source` (import mlflow, set tracking URI, build a client, resolve `source_type` to a concrete run ID/version). |
| `src/haute/_model_flavors.py` | Single source of truth for the scoring flavor domain: `ModelFlavor` (`Literal["catboost", "pyfunc", "rustystats"]`) and `_SUPPORTED_FLAVORS`, derived via `get_args` so the two can never drift apart. Dependency-free leaf module (see high-level Design rationale for why). |
| `src/haute/_model_scorer.py` | MODEL_SCORE node logic: the `ModelScorer` class, the unified `score_frame` dispatch (eager vs batched), the feature-validation cache, offset-column resolution, write-projection application, and `score_from_config` (codegen's delegation target). |
| `src/haute/_model_explainability.py` | Per-prediction SHAP (CatBoost) and native GLM contribution (RustyStats) explanations for trace enrichment, plus `explain_model_score_from_config`, the config-driven entry point trace enrichment calls. |
| `src/haute/routes/mlflow.py` | FastAPI router (`/api/mlflow/*`) exposing read-only experiment/run/model/version discovery for the MODEL_SCORE node's config UI. |

## Key types and data structures

- **`ScoringModel`** (`_mlflow_io.py`, `__slots__`-based) — carrier for a
  loaded model: `_model` (the raw flavor-specific object), `feature_names`
  (ordered `list[str]`), `cat_feature_names` (`frozenset[str]`), `flavor`
  (`ModelFlavor`), `offset_column` (`str | None`). Exposes `predict()`
  (flattened `np.ndarray`), `predict_proba()` (`np.ndarray | None`), and
  `raw_model` (property). No `__getattr__` proxying — every caller goes
  through this declared surface.
- **`_ArtifactNotFoundError(FileNotFoundError)`** — internal sentinel
  raised by the artifact-probing helpers when a probe completes and finds
  nothing. Deliberately a subclass of `FileNotFoundError` (the public
  contract callers expect) while `_find_model_artifact`'s internal
  `try/except _ArtifactNotFoundError` narrows to only the sentinel, so a
  bare `FileNotFoundError`/`MlflowException` from a real infrastructure
  problem is never swallowed.
- **`_ModelCacheWithCascade(LRUCache[tuple[str, ...], ScoringModel])`**
  — overrides `put`, `clear`, and adds `evict_matching`. `put` diffs the
  cache's live keys before/after the base `put()` to detect evictions and
  calls `haute._model_scorer._invalidate_feature_validation_cache_for`
  for each evicted model (imported lazily to avoid a module cycle).
  `clear` cascades into `_model_scorer._clear_feature_validation_cache`.
  `evict_matching(predicate)` delegates to `LRUCache.evict_where` and
  cascades *outside* the base cache's lock (the evicted values are
  returned by `evict_where` precisely so the cascade can run lock-free).
- **`ModelFlavor`** / **`_SUPPORTED_FLAVORS`** (`_model_flavors.py`) —
  see Module map. Re-exported (via `import X as X`) from
  `_model_scorer.py` so existing call sites can keep importing them from
  either module.
- **`ModelSource`** (`_model_scorer.py`) — `Literal["run", "registered"]`.
- **`ScoreWriteProjection`** (`_model_scorer.py`, frozen `dataclass`,
  `slots=True`) — `passthrough_columns: frozenset[str] | None` (`None`
  means "preserve the full scored input"), `optional_passthrough_columns:
  frozenset[str]` (included only if actually present in the schema),
  `required_output_columns: frozenset[str]` (validated to actually appear
  in the final projected column set).
- **`ModelScorer`** (`_model_scorer.py`) — holds a MODEL_SCORE node's full
  configuration: `source_type`, `run_id`, `artifact_path`,
  `registered_model`, `version`, `task`, `output_col`, `code`,
  `source_names`, `source` (`"live"` vs anything else → batched),
  `row_limit`, `required_output_columns`, `feature_contract_path`,
  `_declared_categorical_levels`, `reuse_loaded_model` (plus a private
  `_scoring_model` slot and lock, used only when pinning a model instance
  to the scorer for a reused streaming session).
- **`ModelExplanationError(RuntimeError)`** (`_model_explainability.py`)
  — every explanation failure mode raises this.
- **Cache key shapes:**
  - Model cache key: `_model_cache_key(...)` →
    `(source_type, run_id, artifact_path, task, artifact_fingerprint)`, or
    with an extra element inserted before `artifact_path` when `version` is
    non-empty: `(source_type, run_id, version, artifact_path, task,
    artifact_fingerprint)`. `run_id` stays fixed at slot 1 regardless of
    `version`'s presence so a targeted `clear_model_cache(run_id=...)` can
    match on `key[1]` without branching on key shape.
    `artifact_fingerprint` (`_local_artifact_fingerprint`, a required
    keyword arg) is the byte-identity hash of the local model file for
    `catboost`/`rustystats`; pyfunc models (no local file — loaded by
    MLflow URI) key with `artifact_fingerprint=""`.
  - Feature-validation cache key:
    `((tuple(feature_names), frozenset(cat_feature_names),
    offset_column), tuple(schema.items()))` — content-addressed, never
    keyed on model object identity (see high-level Design rationale).
- **Module-level state in `_mlflow_io.py`:** `_model_cache_hits` /
  `_model_cache_misses` counters (under `_model_cache_stats_lock`,
  scraped by `get_model_cache_stats()`); `_artifact_io_locks:
  WeakValueDictionary[tuple[str, str], threading.RLock]` (per-artifact
  reentrant locks, entries evaporate once unreferenced);
  `_disk_cache_active_runs: Counter[str]` (under
  `_disk_cache_active_runs_guard`, tracks run directories currently
  "in use" so eviction skips them).
- **Module-level state in `_model_scorer.py`:** `_feature_validation_cache
  : LRUCache[...]` (same max size as the model cache);
  `_feature_validation_last_entry` (single-slot fast path, see Control
  flow); `_scenario_ctx: ContextVar[str]` (`"live"` vs `"batch"`, set by
  `Pipeline.run()`/`Pipeline.score()`); `_temp_files_to_clean` /
  `_temp_file_scope` (batch-scorer temp-parquet cleanup bookkeeping).

## Control flow

### Model loading — `load_mlflow_model(...)` (`_mlflow_io.py`)

1. Validate `task` is `"regression"` or `"classification"`.
2. **Fast path.** If `source_type == "run"` and both `run_id` and
   `artifact_path` are given, derive the flavor from the artifact extension
   via `_flavor_from_artifact` before building any cache key, since the key
   now includes an artifact-fingerprint component that differs by flavor:
   - **Pyfunc** has no local artifact file to fingerprint, so the key is
     built once with `artifact_fingerprint=""` (no tracking-server round
     trip). Check the in-memory cache; on a hit, record a hit and return
     immediately.
   - **Native flavors (`catboost`/`rustystats`)** first check whether the
     disk-cached file already exists; if so, compute
     `_local_artifact_fingerprint` from that file (a stat-gated memo, so an
     unchanged file is cheap), build the key with that fingerprint, and
     check the in-memory cache. On a hit, record a hit and return; on a
     miss, acquire the per-artifact lock, re-check the memory cache (a
     concurrent caller may have finished while this one waited), then load
     directly from the disk-cached file, populate the memory cache, and
     return.
   - In both native and pyfunc cases, if the disk-cached file vanishes
     between the existence check and acquiring the lock (a concurrent
     corrupt-retry deleted it), or the file was never disk-cached to begin
     with, fall through to the full path.
3. **Full path.** Call `resolve_mlflow_source` (imports mlflow, resolves
   the tracking URI/backend, builds a client, resolves `source_type` to a
   concrete `run_id`/`version`). If `artifact_path` was empty,
   auto-discover it via `_find_model_artifact`. Derive `flavor` from the
   resolved artifact path. For a native flavor, resolve the local artifact
   file up front (`_resolve_artifact_local`, itself a stat-gated memo for an
   already-cached artifact — only a genuinely new artifact downloads here)
   and compute its fingerprint; for pyfunc, the fingerprint is `""`. Build
   the real cache key with that fingerprint.
4. Check the memory cache again under the resolved key; on a hit, return.
5. Acquire the per-`(run_id, artifact)` lock; re-check the cache
   (single-flight); on a hit, return. Otherwise record a miss, then load:
   native flavors go through `_load_with_bounded_retry`; anything else
   loads via MLflow's pyfunc flavor (`_load_pyfunc_model` +
   `_wrap_pyfunc`). For a native flavor, the bounded retry may have deleted
   and re-downloaded the artifact, so the fingerprint is re-derived after
   loading (a no-op stat when nothing changed) and the cache key rebuilt
   from it before the result is stored, so the stored entry is always keyed
   by the bytes actually loaded. Store the result in the memory cache
   before releasing the lock.

### Disk-cache resolution — `_resolve_artifact_local` (`_mlflow_io.py`)

1. Mark the run "active" (`_disk_cache_run_in_use`) so eviction skips it
   for the duration.
2. Compute the safe cache path (`_artifact_cache_path`: sha256-digest of
   `artifact_path` as the directory name, extension-preserving file
   name, validated to resolve under the cache root).
3. If the file already exists, return it (cache hit, no lock needed for
   the existence check itself).
4. Otherwise acquire the per-artifact lock, re-check existence (another
   thread may have just finished downloading), and if still missing:
   download to a fresh `tempfile.mkstemp`-rooted temp directory via
   `mlflow.artifacts.download_artifacts`, verify the downloaded file
   exists (falling back to a name-based lookup if `download_artifacts`
   nested it), then `shutil.move` it into the cache path. Any exception
   during download/move deletes a partially-written cache file before
   re-raising; the temp directory is always cleaned up in a `finally`.
5. After a successful download, run `_evict_disk_cache` (oldest-mtime
   run directories beyond `_DISK_CACHE_MAX_DIRS` = 50 are removed,
   skipping any run currently marked active by *any* in-flight caller,
   not just this one).

### Bounded retry — `_load_with_bounded_retry` (`_mlflow_io.py`)

Up to `_LOAD_MAX_ATTEMPTS` = 2 attempts. Each attempt resolves the local
artifact path (re-downloading if a previous attempt deleted it) and tries
to load it. `AttributeError`/`TypeError`/`KeyError` re-raise immediately
(programmer/library-contract error, not corruption). Any other exception:
log a warning, delete the cached file (so the next attempt re-downloads),
and — if attempts remain — sleep `_LOAD_BACKOFF_BASE_S * 2**(attempt-1) +
random.uniform(0, _LOAD_BACKOFF_JITTER_S)` before retrying. After the
final attempt fails, raise `RuntimeError` naming the run/artifact/flavor
and wrapping the last error (`raise ... from last_err`).

### Artifact discovery — `_find_model_artifact` (`_mlflow_io.py`)

Tries `_find_cbm_artifact` (`.cbm`, top-level then one level of
subdirectories), then `_find_rsglm_artifact` (`.rsglm`, same search
shape), catching only `_ArtifactNotFoundError` between them. If neither
matches, lists the run's artifacts directly looking for a `model`
directory, then one level deeper for anything ending `/MLmodel` or named
`MLmodel`. Raises `_ArtifactNotFoundError` if nothing matches; any
`MlflowException` or bare `FileNotFoundError` from `client.list_artifacts`
itself is not caught and propagates.

### Cache clearing — `clear_model_cache(run_id=None)` (`_mlflow_io.py`)

`run_id=None` (blanket clear): removes the entire `.cache/models` tree,
clears the in-memory model cache (cascading to the feature-validation
cache via `_ModelCacheWithCascade.clear`), and resets the hit/miss
counters. `run_id="<id>"` (targeted clear): validates the ID, removes
only that run's disk directory, and evicts only in-memory entries whose
cache-key `run_id` slot (index 1) matches — via `evict_matching`, which
cascades per-evicted-model — but leaves the hit/miss counters untouched
(a targeted clear is not a measurement-window boundary).

### Scoring dispatch — `score_frame(...)` (`_model_scorer.py`)

The unified entry point both `ModelScorer.score()` and deploy's scorer
call through (directly or via the `_score_eager`/`_score_batched_standalone`
thin delegates). Validates `flavor` against `_SUPPORTED_FLAVORS` (raises
`ConfigError` otherwise), normalises `required_output_columns` into a
`ScoreWriteProjection` if given (mutually exclusive with passing
`write_projection` directly), normalises categorical level declarations
to the feature set, then dispatches to `_score_batched_unified` (`batch=
True`) or `_score_eager_unified` (`batch=False`).

- **Eager** (`_score_eager_unified`): resolves the effective offset
  column (explicit argument wins over the model's self-description),
  requires it present if set, prunes the collection to exactly what the
  write projection needs (or the full schema if none), collects once via
  `streaming_collect`, validates declared categorical value domains
  against that exact materialisation, prepares the predict frame,
  supplies the CatBoost baseline `Pool` if applicable, predicts, appends
  the proba column for classification when supported, and projects the
  result.
- **Batched** (`_score_batched_unified`): wraps the raw model in a
  short-lived `ScoringModel` carrier, sinks the (possibly projection-
  pruned) input to a temp parquet, delegates to
  `_batch_score_to_parquet` (chunked prediction, see below), unlinks the
  input temp file, registers the output temp file for process-exit
  cleanup, and returns a lazy scan of it.

### Batched chunk loop — `_batch_score_to_parquet` (`_model_scorer.py`)

Reads the input parquet via `pyarrow.parquet.ParquetFile.iter_batches`
(`_SCORE_BATCH_SIZE` = 500,000 rows/chunk). Per chunk: validates
categorical domains, prepares the predict frame (offset column riding
along for pyfunc/rustystats, or supplied as a CatBoost Pool baseline),
predicts, appends the proba column if applicable, applies the write
projection, writes to a `ParquetWriter` opened lazily from the first
chunk's Arrow schema. If the input has zero rows, no chunk loop runs; a
one-row synthetic probe (built from the input's stored parquet schema) is
scored instead purely to derive the correct output dtypes (including a
non-Float64 CatBoost classifier hard-label dtype), and an empty table
with those dtypes is written. Any failure before the writer closes
cleans up the (incomplete) output file in a `finally`.

### Feature validation — `_validate_features` (`_model_scorer.py`)

Two-level memoisation ahead of the uncached worker
(`_validate_features_uncached`): a single-slot "last entry" check
(`_feature_validation_last_entry`) covers the dominant case of repeated
calls against the same model/schema without taking the LRU's lock or
promoting an `OrderedDict` entry; on a miss there, the bounded LRU
(`_feature_validation_cache`) is checked, and only on a full miss does
the uncached validator run. **Errors are never cached** — a
`FeatureMismatchError` leaves both the last-entry slot and the LRU
untouched so a later call against the same (fixed) schema re-validates
rather than replaying a stale exception.

### Explanation — `explain_catboost_prediction` /
`explain_rustystats_glm_prediction` (`_model_explainability.py`)

Both build a one-row input (Pool for CatBoost, Polars DataFrame for
RustyStats) from the traced `input_row`, call the model's native
contribution API (`get_feature_importance(type="ShapValues")` /
`predict_contributions(...)`), and independently recompute the model's
own prediction to check against the decomposition's sum. CatBoost SHAP
values are always in raw-formula space; for Poisson/Tweedie losses
(detected via `get_all_params()["loss_function"]`) `predict()` applies a
final exponential transform, so the function reports both an
`output_space` (where the returned contributions live) and a
`prediction_space` (where `prediction_value` and the traced-output check
live), and re-predicts in both `RawFormulaVal` and default spaces to
reconcile them. RustyStats returns the same shape of information
natively via `output_space`/`prediction_space` fields in its own
response. Both raise `ModelExplanationError` if the reconstructed sum
disagrees with the independently-computed prediction beyond
`_prediction_tolerance` (`max(1e-6, abs(value) * 1e-6)`), or if the
traced `prediction_value` disagrees with the model's own response beyond
the same tolerance.

### Routes (`routes/mlflow.py`)

`_ensure_tracking()` imports mlflow (`ImportError` → `503`), resolves the
tracking backend and builds a client (`Exception` → `502`, logged).
`list_runs` is O(N) in `max_results` — MLflow has no batch artifacts API,
so each candidate run gets its own `client.list_artifacts` call to check
for a matching model/optimiser-result artifact; a run whose artifact
listing itself fails is logged and skipped rather than failing the whole
response. `list_model_versions` fetches each version's backing-run params
via `_model_version_run_params`, which swallows (and logs with a full
traceback) a failed `client.get_run` — a deleted/inaccessible backing run
degrades that one version's `params` to `{}` rather than failing the
endpoint.

## Edge cases and invariants

- **Disk-cache filenames are sha256 digests of the artifact path**, not
  the artifact's own basename — two artifacts with the same basename in
  different directories (or across different runs sharing a digest
  namespace) never collide on disk, and the identity is validated
  (`_validate_disk_cache_run_id`, `_validate_artifact_path`) before any
  path is constructed, rejecting path separators, `.`/`..` segments, and
  null bytes.
- **`_artifact_cache_path` asserts the computed path stays under the
  resolved cache root** as defence-in-depth beyond the string-level
  validation above.
- **`_artifact_io_locks` is a `WeakValueDictionary`** — per-artifact
  `RLock`s are reentrant (the load path can re-enter through
  `_resolve_artifact_local` while already holding the lock for the same
  key) and evaporate once no caller references them, so the lock table
  never grows unboundedly across a long-running process.
- **Double-checked locking appears at three sites**: the fast-path memory
  cache check, the fast-path disk-cache-file check, and the full-path
  memory cache check — each re-checks the cache immediately after
  acquiring the relevant lock, because a concurrent caller may have
  completed the same work while this caller was waiting to acquire.
- **The in-memory cache key's artifact-fingerprint component is a required
  keyword argument to `_model_cache_key`**, not one with a default of
  `""` — so a new call site cannot silently omit byte-identity from the
  key by forgetting the parameter. `""` is only ever passed explicitly, and
  only for pyfunc (the one flavor with no local artifact file to
  fingerprint).
- **Disk-cache eviction excludes any run currently "in use"** —
  `_disk_cache_active_runs` is a reference count, not a boolean, so
  nested/concurrent callers touching the same run correctly keep it
  protected until the *last* one finishes, not the first.
- **Offset-column handling is flavor-specific by design, not
  uniform.** RustyStats and pyfunc models receive the offset column as
  part of the predict frame itself (`_OFFSET_PASSTHROUGH_FLAVORS`);
  CatBoost never receives it as a feature column — it is supplied as a
  numeric `Pool` baseline, because CatBoost only applies a baseline
  passed inside a `Pool`, never through a bare matrix `predict()`.
- **Feature order is checked only relatively.** Extra columns elsewhere
  in the input schema are fine; what's enforced is that the model's own
  features appear in the schema in the same relative order they were
  declared in training (`_validate_features_uncached`'s
  `actual_order_by_position` check).
- **The batch scorer's zero-row output dtype is derived, never
  hardcoded**, from a one-row synthetic probe scored through the same
  code path as real rows — including re-deriving the classification proba
  dtype — so an empty score of a model produces a parquet schema
  byte-for-byte type-compatible with a non-empty score of the same model.
  A probe `predict()` failure here is allowed to propagate rather than
  falling back to a guessed dtype.
- **`_positive_class_proba_vector` is the single shared shape dispatch**
  for both the eager and batch proba paths (`_predict_positive_proba` in
  `_model_scorer.py` calls the same function `_append_classification_proba`
  in `_mlflow_io.py` uses) — 1-D used as-is, `(n, 1)` takes column 0,
  `(n, 2)` takes column 1, anything else raises. The two call sites are
  guaranteed to raise the identically-worded error for the same bad shape.
- **`_prepare_predict_frame` rejects any flavor outside
  `{"catboost", "pyfunc"}` minus the `"rustystats"` branch handled
  above** — i.e. a flavor newly added to `ModelFlavor` but not yet taught
  a prep path here fails loudly (`ValueError` enumerating
  `_SUPPORTED_FLAVORS`) rather than silently falling through the
  CatBoost-shaped branch.
- **`test_mlflow_io.py::TestFlavorSsot`** pins the cross-module SSOT
  contract directly: `_model_scorer` and `_mlflow_io` must import the
  *same* `ModelFlavor`/`_SUPPORTED_FLAVORS` object, and
  `_prepare_predict_frame` must recognise exactly the SSOT's members.
- **`_catboost_offset_column` gates on `isinstance(value, str) and
  value`**, not truthiness alone — metadata proxies and mocked models in
  tests can return non-string truthy objects for an absent key, and only
  a real non-empty string counts as a declared offset.

## Error handling

| Situation | Exception | Where it surfaces |
|---|---|---|
| `mlflow` not installed | `ImportError` | `resolve_mlflow_source`; routes' `_ensure_tracking` converts to `HTTPException(503)`. |
| Missing `run_id`/`registered_model`, invalid `source_type`, no versions found | `ValueError` | `resolve_mlflow_source` / `resolve_version`. |
| No matching artifact in a run | `_ArtifactNotFoundError` (⊂ `FileNotFoundError`) | `_find_model_artifact` and its per-extension helpers; a genuine `MlflowException`/bare `FileNotFoundError` from `list_artifacts` is not caught here. |
| Unsupported local file extension | `NotImplementedError` | `load_local_model`. |
| Corrupt/unloadable artifact after bounded retry | `RuntimeError` (chained `from last_err`) | `_load_with_bounded_retry`. |
| Bug in load dispatch (bad attribute/type/key) | `AttributeError` / `TypeError` / `KeyError` | Re-raised immediately from `_load_with_bounded_retry`, never retried. |
| Invalid disk-cache run_id/artifact_path | `ValueError` | `_validate_disk_cache_run_id` / `_validate_artifact_path`, called from `_artifact_cache_path` before any I/O. |
| Feature/order/categorical/offset mismatch | `FeatureMismatchError` | `_validate_features_uncached` / `_require_offset_column`; propagates through `ModelScorer.score()` uncaught (no rewrap of other exception types — see `_run_score_pipeline` docstring). |
| Unsupported scoring flavor | `ConfigError` | `score_frame()`, at the top of dispatch. |
| Unreachable/unknown flavor in predict-frame prep | `ValueError` | `_prepare_predict_frame`; enumerates `_SUPPORTED_FLAVORS`. |
| Multiclass / malformed `predict_proba` shape | `ValueError` | `_positive_class_proba_vector`, shared by eager and batch. |
| Write projection references un-produced/un-preserved columns | `ValueError` | `_score_output_projection_columns`. |
| Explanation reconstruction/shape/finiteness failures | `ModelExplanationError` | `_model_explainability.py`, both `explain_catboost_prediction` and `explain_rustystats_glm_prediction`. |
| MLflow search call failure in a discovery route | Logged `Exception` → `HTTPException(502, _INTERNAL_ERROR_DETAIL)` | `routes/mlflow.py`; the real error is never sent to the client. |
| Registered model version's backing run inaccessible | Swallowed (`Exception`), logged with `logger.exception` | `_model_version_run_params` returns `{}` for that version only; the endpoint still returns `200`. |

`FeatureMismatchError`, `ConfigError`, and `ModelExplanationError`'s
sibling errors in this component all carry structured context — see
`haute.errors.HauteError.__init__`. `ModelExplanationError` itself is a
plain `RuntimeError`, not a `HauteError` subclass.

## Testing

Tests live across eleven files. Strategy is unit-level with `mlflow`,
`catboost`, and `rustystats` either mocked or exercised against small
real artifacts fixture-built in `tmp_path`; there is no test that talks
to a live MLflow tracking server.

- **`tests/test_mlflow_io.py`** — the bulk of `_mlflow_io.py` coverage:
  run/registered loading (happy path, missing args, invalid source
  type), pyfunc auto-detect and auto-discovery fallback, in-memory cache
  hit/LRU-eviction, CatBoost loader dispatch by task, model wrapping
  (CatBoost cat-feature extraction, pyfunc signature extraction including
  the older-MLflow ColSpec-list fallback), predict-frame preparation for
  every flavor/dtype/null/categorical combination, artifact-by-extension
  discovery (top-level, subdirectory, "prefers top-level" ordering,
  missing → labeled error), classification proba shape handling
  (1-D/`(n,1)`/`(n,2)`/multiclass/zero-width/3-D, eager-vs-batch
  agreement), RustyStats loading (`required_columns` contract, including
  against a real RustyStats model), local-model dispatch by extension,
  `_find_model_artifact`'s full priority order and its propagation of a
  bare `FileNotFoundError`/`MlflowException` past the internal sentinel,
  `_resolve_artifact_local`'s cache-hit/miss/download-failure/partial-
  write-cleanup/nested-path paths, `clear_model_cache` (blanket, targeted,
  nonexistent, invalid run_id), the fast-cache-check and post-resolve
  cache-check paths of `load_mlflow_model` (now exercised with the
  artifact-fingerprint component of the cache key present), the bounded
  retry for both native flavors, `_score_eager`'s dispatch for every
  task/flavor combination, and `TestFlavorSsot` (the cross-module SSOT pin
  described above).
- **`tests/test_mlflow_io_concurrency.py`** — single-flight download and
  load correctness under real threads: a second caller waits rather than
  re-downloading, distinct artifacts proceed concurrently, a failed
  download releases the lock for the next caller, concurrent same-model
  loads produce exactly one shared instance, same-basename artifacts
  under different runs/versions store distinct bytes and models,
  disk-cache-eviction races with an in-flight load of the run being
  considered for eviction (several scenarios: eviction skips an
  active run, eviction re-checks activity before deleting, eviction
  blocks new users of a run mid-delete, the fast disk-cache path marks a
  run active before probing), and waiters on both the fast-path and
  full-resolve-path reusing a model that finished loading while they
  waited.
- **`tests/test_mlflow_model_cache_key_contract.py`** (new) — pins cache-key
  *completeness* specifically for the artifact-fingerprint component:
  `TestFastPathArtifactPerturbation` and `TestFullPathArtifactPerturbation`
  each rewrite the local artifact's bytes in place (disk-cache file for the
  fast path, resolved local path for the full path) under an unchanged run
  reference and assert the next `load_mlflow_model` call returns a model
  built from the new bytes rather than the stale in-memory entry;
  `TestKeyContract` covers the key-shape invariants directly (`run_id`
  fixed at slot 1 regardless of `version`'s presence, pyfunc keyed with an
  empty-string fingerprint). This is a regression suite for a real bug: a
  re-logged run or a `version="latest"` retrain-in-place used to keep
  serving the previously loaded model on a long-lived server until
  `clear_model_cache` was called by hand.
- **`tests/test_mlflow_io_real_pyfunc.py`** — pyfunc wrapping and predict-
  frame dtype fidelity against a real (non-mocked) MLflow pyfunc model,
  including the named-column signature contract and declared-dtype
  precision preservation.
- **`tests/test_mlflow_utils.py`** — `search_versions` (name quoting) and
  `resolve_version` (`"latest"` resolution, explicit version passthrough,
  no-versions-found error).
- **`tests/test_mlflow_routes.py`** — full FastAPI coverage of every
  route: experiments/runs/models/model-versions happy paths, the
  artifact-filter behaviour of `/runs` (model vs optimiser), a failing
  per-run `list_artifacts` call being skipped rather than failing the
  whole listing, `_ensure_tracking`'s both failure branches (missing
  mlflow → `503`, backend resolution failure → `502`), and additional
  edge-case classes for runs/models/model-versions (pagination, missing
  optional fields, the inaccessible-backing-run params-degrades-to-empty
  behaviour).
- **`tests/test_model_explainability.py`** — CatBoost SHAP additivity for
  regression and classification, categorical feature value preservation
  in the explanation payload, missing-feature and prediction-mismatch
  failures, the raw-formula-vs-response space reconciliation for
  Poisson/Tweedie losses (parametrized across link-loss variants) both
  with and without a traced prediction value, and the equivalent RustyStats
  GLM contribution suite (shared-contract mapping, missing features,
  prediction mismatch, additivity-break detection, a real RustyStats
  model's `predict_contributions` contract, and config-driven explanation
  selection by artifact extension).
- **`tests/test_model_scorer.py`** — the largest file: `ModelScorer`
  construction and `.score()` behaviour, `_sink_to_temp`,
  `_batch_score_to_parquet` (including multi-batch accumulation, Series-
  conversion edge cases, the zero-row dtype-probe path and its own dtype-
  fidelity sub-suite), `ScoringModel` behaviour, `score_from_config`
  (scenario-context selection, a symlink-escape rejection test on the
  config path), `FeatureMismatchError` construction, `_validate_features`
  (including the last-entry-cache-cleared regression test and a
  registered-temp-cleanup-callback test), `_run_score_pipeline`, and
  `TestFeatureMismatchTypeOverflow` plus the flavor-SSOT-derivation unit
  test.
- **`tests/test_model_scorer_contracts.py`** — the shared
  `_positive_class_proba_vector` shape contract exercised at the module
  boundary (positive/negative/edge shapes, batch-helper agreement),
  `score_frame`'s flavor rejection, and `score_from_config`'s scenario-
  context and symlink-escape behaviour (a second, narrower pass over
  territory `test_model_scorer.py` also covers).
- **`tests/test_model_scorer_eager_single_execution.py`** — regression
  coverage for the "collect exactly once" structural guarantee: eager
  scoring must not re-execute the upstream lazy plan a second time
  (order-unstable upstream ops would otherwise misalign predictions with
  rows), covering both the unified `score_frame` eager path and
  `_run_score_pipeline`'s live path, plus confirming the batched path is
  unaffected.
- **`tests/test_model_scorer_feature_order_lookup.py`** — the
  precomputed schema-position lookup used by feature-order validation:
  wide-input performance shape, duplicate-schema-name first-position
  semantics, order-mismatch error detail correctness, and cache-key
  sensitivity to a schema-order change.
- **`tests/test_model_scorer_feature_projection.py`** — write-projection
  behaviour end to end for both eager and batched scoring: feature-only
  vs. required-output-column projection, `None`-projection full-
  passthrough, missing-passthrough and required-but-unproduced column
  rejection, existing-vs-generated proba column interaction, projection
  actually preventing excluded-column computation, single-materialisation
  guarantees, zero-row batched projection schema preservation, multi-
  batch passthrough preservation, and interaction with declared transform
  contracts and stale selected-column sets in the lazy batch path (the
  latter two lean into execution-engine territory but exercise this
  component's projection code directly).

`score_from_config` is also exercised indirectly by
`tests/test_model_score_codegen.py` and
`tests/test_model_score_executor.py`, which belong primarily to the
codegen and execution-engine components respectively and are not detailed
here.

Known coverage gaps: none flagged with `xfail` or an explicit gap marker
in this component's own test files at the time of writing.
