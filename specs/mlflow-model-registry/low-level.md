# MLflow Model Registry — Low-Level Specification

## Module map

| File | Responsibility |
|---|---|
| `src/haute/_mlflow_io.py` | Model loading, disk + in-memory caching (keyed in part on artifact byte identity via `_local_artifact_fingerprint`), per-artifact I/O locks, artifact discovery, flavor-specific loaders, the `ScoringModel` carrier, predict-frame preparation per flavor, the shared eager-scoring delegate (`_score_eager`) used by both `_model_scorer.py` and deploy. |
| `src/haute/_mlflow_utils.py` | Shared MLflow bootstrap used by `_mlflow_io.py`, the optimiser IO layer, and deploy's bundler: version resolution, safe model-version search, `runtime_environment_inference` (scopes MLflow's uv-project auto-detection and uv-file logging off so a logged model records the executing interpreter), `bind_mlflow_databricks_credentials` (binds every MLflow Databricks request to the dedicated MLflow pair or the selected profile; see modelling), `ensure_experiment` (a bound client's experiment id, first creating a new Databricks experiment's missing workspace folder with the bound credentials; see modelling), `resolve_backend` (a destination key — or the empty string for the local folder — to a `ResolvedBackend` carrying the tracking/registry URIs plus a secret-free backend identity and filesystem digest; see Key types), and `resolve_mlflow_source` (import mlflow, resolve that backend, build a client pinned to it, resolve `source_type` to a concrete run ID/version, and return the backend alongside the client). |
| `src/haute/_mlflow_errors.py` | The one MLflow failure classifier (`classify_mlflow_error`: MLflow error codes, HTTP 401/403/404, and transport-only connectivity across the exception chain — any other local OS error is `unknown`; it runs inside error handlers and never raises, so when `mlflow.exceptions` or `requests` cannot be imported only the remaining rules apply), `MlflowRemoteError` (a classified write failure with haute's own message), the write-path copy `MLFLOW_LOG_FAILURE_MESSAGES`, and the shared missing-package status and detail. |
| `src/haute/_model_flavors.py` | Single source of truth for the scoring flavor domain: `ModelFlavor` (`Literal["catboost", "pyfunc", "rustystats"]`) and `_SUPPORTED_FLAVORS`, derived via `get_args` so the two can never drift apart. Dependency-free leaf module (see high-level Design rationale for why). |
| `src/haute/_model_scorer.py` | MODEL_SCORE node logic: the `ModelScorer` class, the unified `score_frame` dispatch (eager vs batched), the feature-validation cache, offset-column resolution, write-projection application, and `score_from_config` (codegen's delegation target). |
| `src/haute/_model_explainability.py` | Per-prediction SHAP (CatBoost) and native GLM contribution (RustyStats) explanations for trace enrichment, plus `explain_model_score_from_config`, the config-driven entry point trace enrichment calls. |
| `src/haute/routes/mlflow.py` | FastAPI router (`/api/mlflow/*`) exposing read-only experiment/run/model/version discovery for the MODEL_SCORE node's config UI — every discovery route accepts a `destination` query (`""` = the local folder) — plus the connection surface: the destinations inventory with optional concurrent bounded probes, `[mlflow]` settings read/write, and a bounded per-destination test-connection probe. |
| `src/haute/schemas.py` | Shared Pydantic contracts owned by [server-api](../server-api/low-level.md) and returned by the MLflow discovery routes (`MlflowExperimentSummary`, run/model/version summaries). |

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
  `clear` cascades into `_model_scorer._clear_feature_validation_cache`;
  `evict_matching(predicate)` delegates to `LRUCache.evict_where`.
  All three methods collect or remove model-cache state while holding the
  base cache lock, release it, and only then invoke the feature-validation
  cascade. No callback into the dependent cache runs under the model-cache
  lock.
- **`ResolvedBackend`** (`_mlflow_utils.py`, frozen dataclass) — the
  once-per-load resolution of a destination: `mode`
  (`"databricks"|"server"|"local"`), `tracking_uri` (the connection value,
  which may carry environment credentials and is never logged, persisted,
  or placed in a cache path or diagnostic key), `registry_uri`
  (`registry_uri_for_tracking(tracking_uri)`), `identity` (secret-free:
  `local:<canonical absolute folder>`, `server:<redacted endpoint>`, or
  `databricks:<effective workspace host>|profile=<selected profile or
  empty>`, each suffixed with `|registry=<redacted registry URI>`), and
  `digest` (the first 16 hex characters of sha256(`identity`), the
  filesystem-safe partition used in disk-cache paths).
  `resolve_backend(destination)` resolves
  `resolve_destination(node_destination_key(destination))` — `""` is the
  local folder — ([modelling](../modelling/low-level.md) owns both). A Databricks
  profile's effective host comes from the profile's own host credentials,
  so repointing a profile yields a new identity; an unloadable profile
  raises `MlflowConfigError` naming the profile (never its credentials)
  before any cache lookup. Neither an absent value nor the bare
  category key identifies a backend — only the resolved identity does.
  The Databricks host in the identity is the host MLflow's requests will
  actually target: MLflow's own per-request `get_databricks_host_creds`
  under haute's Databricks credential binding (see
  [modelling](../modelling/low-level.md): the bare form reads the dedicated
  `DATABRICKS_MLFLOW_HOST`/`DATABRICKS_MLFLOW_TOKEN` pair and a profile its
  own credentials, the SDK path is rejected, and run and logged-model
  artifacts go through MLflow's REST artifact repository, so there is exactly
  one credential path), so identity and request target
  agree, and repointing the effective workspace yields a new identity and
  cache partition that the next request on an existing client also
  follows. **One backend per operation:** a
  load, download, registry lookup, or bundling step resolves the backend
  exactly once and threads that object through
  (`resolve_mlflow_source(backend=...)`, the bundler's registered-model
  resolution and download); no helper re-resolves from a destination key
  mid-operation, so a settings save during an operation cannot split it
  across two backends.
- **`ModelFlavor`** / **`_SUPPORTED_FLAVORS`** (`_model_flavors.py`) —
  see Module map. `_model_flavors.py` is their only import surface;
  scoring and loading modules consume private local aliases.
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
  `source_names`, `source` (`"live"` → eager, anything else → batched),
  `row_limit` (set for preview and trace; scores through the row-local scan
  regardless of `source`), `required_output_columns`, `feature_contract_path`,
  `_declared_categorical_levels`, `reuse_loaded_model` (plus a private
  `_scoring_model` slot and lock, used only when pinning a model instance
  to the scorer for a reused streaming session).
- **`ModelExplanationError(RuntimeError)`** (`_model_explainability.py`)
  — every explanation failure mode raises this.
- **Cache key shapes:**
  - Model cache key: `_model_cache_key(...)` →
    `(source_type, run_id, artifact_path, task, artifact_fingerprint,
    backend_identity)`, or with an extra element inserted before
    `artifact_path` when `version` is non-empty: `(source_type, run_id,
    version, artifact_path, task, artifact_fingerprint, backend_identity)`.
    `run_id` stays fixed at slot 1 regardless of `version`'s presence so a
    targeted `clear_model_cache(run_id=...)` can match on `key[1]` without
    branching on key shape; `backend_identity` (the `ResolvedBackend`
    identity, a required keyword) is always the last element, so the same
    run ID and artifact path on two backends — or on two endpoints of the
    same category — never alias.
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
  WeakValueDictionary[tuple[str, str, str], threading.RLock]` (per
  backend-digest/run/artifact reentrant locks, entries evaporate once
  unreferenced);
  `_disk_cache_active_runs: Counter[str]` (under
  `_disk_cache_active_runs_guard`, tracks run directories currently
  "in use" so eviction skips them).
- **Module-level state in `_model_scorer.py`:** `_feature_validation_cache
  : LRUCache[...]` (same max size as the model cache);
  `_feature_validation_last_entry` (single-slot fast path, see Control
  flow); `_scenario_ctx: ContextVar[str]` (`"live"` vs `"batch"`, set by
  `Pipeline.run()`/`Pipeline.score()`); `_temp_files_to_clean` /
  `_temp_file_scope` (batch-scorer temp-parquet cleanup bookkeeping);
  `_score_output_destination: ContextVar[ScoreOutputDestination | None]`
  (set by `model_score_output_destination(path)`: the next batch score in
  that scope writes its output to `path`, records the xxh64 `digest` of the
  file it received, and marks the destination used).

## Candidate-run contract

Haute logs training runs as *candidates*; registering and promoting a model (for example after an
external process compares the candidate with the current champion and moves an alias) happens
outside haute. Contract version `1`, built by `haute.modelling._candidate_run.build_candidate_run`
for canvas and scripted runs alike, guarantees:

- **Run name** — `<node label> · <YYYY-MM-DD HH:MM> UTC` from the training completion time.
- **Tags** — `haute.contract_version` (`"1"`), `haute.job_id`, `haute.node_label`,
  `haute.trained_at` (ISO-8601 UTC), `haute.version`, `haute.algorithm`, `haute.task`,
  `haute.target`, `haute.evaluation_plan_sha256` (the persisted evaluation plan, which fixes the
  development/validation/final-test partition), and `haute.training_identity_sha256`. When known:
  `haute.node_id`, `haute.pipeline` (project-relative), `haute.git_commit`, `haute.git_dirty`
  (`"true"`/`"false"`). `haute.model_card=unavailable` marks a run whose card could not be built.
- **Metrics, namespaced by evaluation set** — `final_test_<metric>` (untouched final test, when
  reserved), `development_<metric>` (only when no final test exists: development diagnostics, never
  held-out performance), `selection_<metric>_{mean,stddev,min,max}` (validation fits used for
  selection), `tuning_{baseline,winner,improvement}_<metric>` (tuned runs), and
  `glm_{aic,bic,deviance,null_deviance}` when available. No metric is logged under a bare name.
- **Params** — `algorithm`, `task`, `target`, `weight`, `evaluation_strategy`,
  `validation_method`, `param_<name>` for the final parameters, `development_rows`,
  `final_test_rows`, `n_features`, `best_iteration` when known, and `tuning_*` summary fields.
- **Artifacts** — the logged model `model` with a `ModelSignature` from the feature contract; the
  native model file and `<name>.feature_contract.json` at the run root; `evaluation/`
  (plan, results, report); `tuning/` (plan, trials, report) for tuned runs; diagnostic JSON under
  `diagnostics/`, `importance/`, `shap/` and `glm/`; and `model_card/` unless tagged unavailable.

A validator selects candidates with tag filters (for example
`tags.haute.contract_version = '1' and tags.haute.node_id = 'freq_model'`), compares challenger and
champion on the same `final_test_<metric>` only when their `haute.evaluation_plan_sha256` match,
and registers the run's `runs:/<run_id>/model` URI. Adding tags, metrics or artifacts is
compatible within a version; renaming or removing any listed item requires a new
`haute.contract_version`.

## Control flow

### Model loading — `load_mlflow_model(...)` (`_mlflow_io.py`)

1. Validate `task` is `"regression"` or `"classification"` (CatBoost loads
   additionally check it against the model file: `_load_catboost_model` reads
   the recorded `loss_function` and raises `ConfigError` naming both tasks
   when a classification loss — `Logloss`, `CrossEntropy`, `MultiClass`,
   `MultiClassOneVsAll` — is scored as regression or any other loss as
   classification; a file without a recorded loss keeps the node's task, and
   `_load_with_bounded_retry` re-raises the `ConfigError` without treating the
   file as corrupt), then resolve
   the backend exactly once (`resolve_backend(destination)`, `""` = the local folder)
   before any cache lookup. An unresolvable destination raises
   `MlflowConfigError` here, so a previously cached artifact can never be
   served for a destination whose prerequisites are gone, and every later
   step of this load uses that same `ResolvedBackend`.
2. **Fast path.** If `source_type == "run"` and both `run_id` and
   `artifact_path` are given, derive the flavor from the artifact extension
   via `_flavor_from_artifact` before building any cache key, since the key
   now includes an artifact-fingerprint component that differs by flavor:
   - **Pyfunc** has no local artifact file to fingerprint, so the key is
     built once with `artifact_fingerprint=""` (no tracking-server round
     trip). Check the in-memory cache; on a hit, record a hit and return
     immediately.
   - **Native flavors (`catboost`/`rustystats`)** first check whether the
     disk-cached file already exists under the backend's digest partition;
     if so, compute
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
3. **Full path.** Call `resolve_mlflow_source(destination=...)` (imports
   mlflow, resolves the same backend, builds a client pinned to its
   tracking and registry URIs, resolves `source_type` to a concrete
   `run_id`/`version`, and returns the backend). If `artifact_path` was empty,
   auto-discover it via `_find_model_artifact`. Derive `flavor` from the
   resolved artifact path. For a native flavor, resolve the local artifact
   file up front (`_resolve_artifact_local`, which treats an already-present
   disk-cache file as authoritative and downloads only when that path is absent)
   and compute its fingerprint; for pyfunc, the fingerprint is `""`. Build
   the real cache key with that fingerprint.
4. Check the memory cache again under the resolved key; on a hit, return.
5. Acquire the per-`(backend digest, run_id, artifact)` lock; re-check the
   cache (single-flight); on a hit, return. Otherwise record a miss, then load:
   native flavors go through `_load_with_bounded_retry`; anything else
   loads via MLflow's pyfunc flavor (`_load_pyfunc_model` +
   `_wrap_pyfunc`). For a native flavor, the bounded retry may have deleted
   and re-downloaded the artifact, so the fingerprint is re-derived after
   loading (a no-op stat when nothing changed) and the cache key rebuilt
   from it before the result is stored, so the stored entry is always keyed
   by the bytes actually loaded. Store the result in the memory cache
   before releasing the lock. `_resolve_artifact_local`,
   `_load_with_bounded_retry`, and `_load_pyfunc_model` all receive the
   `ResolvedBackend` (never a bare tracking URI), and log only its `mode`
   and `digest`.

### Disk-cache resolution — `_resolve_artifact_local` (`_mlflow_io.py`)

1. Mark the run "active" (`_disk_cache_run_in_use`) so eviction skips it
   for the duration.
2. Resolve the cwd-relative cache root once through `_disk_cache_root()`
   (`Path.cwd() / ".cache" / "models"`), then compute the safe cache path
   (`_artifact_cache_path`: `<root>/<backend digest>/<run_id>/<sha256 of
   artifact_path>/artifact<suffix>` — the backend digest is validated as
   lowercase hex, the run id and artifact path as before, and the result
   must resolve under the root). Two backends therefore never share a
   cached file even for identical run IDs and artifact paths.
3. If the file already exists, return it (cache hit, no lock needed for
   the existence check itself).
4. Otherwise acquire the per-artifact lock, re-check existence (another
   thread may have just finished downloading), and if still missing:
   download to a fresh `tempfile.mkdtemp` directory via
   `mlflow.artifacts.download_artifacts`, verify the downloaded file
   exists (falling back to a name-based lookup if `download_artifacts`
   nested it), then `shutil.move` it into the cache path. Any exception
   during download/move deletes a partially-written cache file before
   re-raising; the temp directory is always cleaned up in a `finally`.
5. After a successful download, run `_evict_disk_cache`. It counts run
   directories across every backend-digest directory (a run cached on two
   backends is two directories) and excludes run
   directories currently marked active by *any* in-flight caller. For each
   oldest inactive directory beyond `_DISK_CACHE_MAX_DIRS` = 50, it re-checks
   activity and atomically renames the directory to a unique `.evicting-<8 hex>`
   tombstone under `_disk_cache_active_runs_guard`, then recursively deletes
   the tombstone outside the guard. The tombstone name is deliberately short —
   shorter than any real run id — because a name that grew on the run id it
   replaced pushed the cached artifact beneath it past Windows' 260-character
   path limit, where it could no longer be opened, so the deletion failed and
   the tombstone held disk for good. New users either protect the original
   directory before the rename or cleanly miss it afterwards; unrelated loads
   never wait for recursive deletion. Active directories can therefore make
   the physical total temporarily exceed 50; the next successful download
   triggers another eviction pass. Stale tombstones from an interrupted process
   are cleaned on a later eviction pass. Deletion goes through the shared
   retrying `remove_tree`, because a delete Windows refuses while an indexer
   holds a handle would otherwise leave a tombstone holding disk for good; a
   tree that still survives is logged
   (`mlflow_disk_cache_tombstone_delete_failed`) rather than ignored, and never
   raised into a caller that only asked for an artifact.

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
that run's disk directory under every backend-digest partition, and evicts only in-memory entries whose
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
The delegates receive `offset_column` on every call, including `None`; there is
no reduced-arity path for earlier delegate signatures.

- **Eager** (`_score_eager_unified`): resolves the effective offset
  column (explicit argument wins over the model's self-description),
  requires it present if set, prunes the collection to exactly what the
  write projection needs (or the full schema if none), collects once via
  `streaming_collect`, validates declared categorical value domains
  against that exact materialisation, prepares the predict frame,
  supplies the CatBoost baseline `Pool` if applicable, predicts, appends
  the proba column for classification when supported, and projects the
  result.
- **Row-local scan** (`_score_row_local_scan`, the `row_limit` path of
  `_run_score_pipeline`): scores through the execution engine's
  `row_local_python_scan`. Required input columns are the predict features plus
  any offset column, generated columns are the prediction and probability
  columns, input predicates and transform elision are permitted, and the write
  projection still applies. A limit Polars pushes to the scan bounds prediction
  to the rows the limited result reads, a projection without the prediction
  predicts nothing, and an aggregation over the prediction predicts every row.
  Each scored batch goes through `_score_collected_frame` (categorical domain
  validation, predict-frame preparation, the CatBoost baseline `Pool`,
  prediction, the proba column) and is cast strictly to output dtypes fixed
  before any row is scored by `_resolve_score_dtypes`: `_declared_score_dtypes`
  gives `Float64` for every regression and CatBoost classification's
  `classes_` dtype, and any other classifier's prediction and probability
  dtypes come from the same schema-shaped all-null probe row the empty batched
  score uses. A classifier whose probe cannot be scored fails the node with the
  probe's error; scorer exceptions keep their types at Haute's collect seams.
- **Batched** (`_score_batched_unified`): wraps the raw model in a
  short-lived `ScoringModel` carrier, sinks the (possibly projection-
  pruned) input to a temp parquet, delegates to
  `_batch_score_to_parquet` (chunked prediction, see below), unlinks the
  input temp file, registers the output temp file for process-exit
  cleanup, and returns a lazy scan of it. Inside a
  `model_score_output_destination` scope the output is written to that
  destination instead of a temporary file and is never registered for
  cleanup: the destination also records the xxh64 `digest` of the file it
  received — scored chunks through one `ParquetWriter` and an empty result
  through `pq.write_table`, both written through one `HashingWriter` — while
  a temporary scored file (no destination) records none. The planned lazy
  engine uses this to make a batch Model Score's scored file the staged
  artifact of its shared-snapshot capture, so the scored rows are written once
  (see the [execution engine](../execution-engine/low-level.md)).

### Batched chunk loop — `_batch_score_to_parquet` (`_model_scorer.py`)

Reads the input parquet via `pyarrow.parquet.ParquetFile.iter_batches`
(`_SCORE_BATCH_SIZE` = 500,000 rows/chunk). Per chunk: validates
categorical domains, prepares the predict frame (offset column riding
along for pyfunc/rustystats, or supplied as a CatBoost Pool baseline),
predicts, appends the proba column if applicable, applies the write
projection, writes to a `ParquetWriter` opened lazily from the first
chunk's Arrow schema. Regression predictions are cast to `Float64` in every
chunk, matching the eager and scan paths. If the input has zero rows, no chunk
loop runs; CatBoost derives its hard-label dtype from `raw_model.classes_` (and
uses `Float64` for probabilities), while other flavors use a schema-shaped probe. A CatBoost
classifier whose label domain is unavailable raises rather than guessing, and an empty table
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

For live/eager value-domain validation, `_run_score_pipeline` derives the
materialised validation frame from the union of model features, categorical
columns, offset requirements, and the write projection. Unrelated wide-frame
columns are not collected merely to validate categorical levels.

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

### Registry-provider representation differences

The wire contracts normalise two provider quirks at the route boundary:
the file-store registry reports model versions as **ints** where
Databricks reports strings (`/models`, `/model-versions`, and
`resolve_version()` all serialise to `str`), and file-store versions carry
`description=None` where Databricks omits or supplies a string
(coerced to `""`). A locally registered model therefore surfaces through
the discovery routes exactly like a Databricks one.

### Registered model aliases

A registered source names either a `version` (a number, or `latest`) or an
`alias` (for example `champion`), never both: `validate_node_config` rejects a
MODEL_SCORE or OPTIMISER_APPLY config carrying a non-empty `alias` beside any
`version`, and a malformed alias (not a trimmed non-empty string). An alias is
how an external promotion process points consumers at a version, so haute
resolves it on every use rather than storing what it targeted:
`resolve_version(client, model, version, alias)` calls
`client.get_model_version_by_alias`, rejects a concrete version passed
together with an alias (`latest`/`""` beside an alias is the loaders' default
and defers to it), and turns the store's missing-alias error
(`INVALID_PARAMETER_VALUE` locally, `RESOURCE_DOES_NOT_EXIST` on Unity Catalog)
into a `ValueError` naming the model and alias. `resolve_mlflow_source`,
`load_mlflow_model`, `load_mlflow_optimiser_artifact`, `ModelScorer`,
`score_from_config`, both node builders, runtime apply, the deploy scorer's
request-time optimiser load and both explanation paths thread `alias` through
unchanged. The model cache key uses the resolved version and its artifact
fingerprint, so moving the alias misses the cached entry and loads the newly
targeted version. `/api/mlflow/model-versions` adds `aliases` (sorted alias
names) to each `MlflowModelVersionSummary`, from the registered model's alias
map (`client.get_registered_model(name).aliases`), because Unity Catalog
version search results do not carry aliases.

### Routes (`routes/mlflow.py`)

`_ensure_tracking(destination)` imports mlflow (`ImportError` → `503`),
resolves the requested destination — `resolve_destination(node_destination_key(destination))`,
so `""` is the local folder — and builds a client
(`MlflowConfigError` → `502` with its actionable detail; any other
`Exception` → `502`, logged) with the registry URI pinned to that
destination (`databricks-uc` for Databricks, the tracking URI otherwise) —
ambient process-global registry state from another destination can never
answer discovery queries. Every discovery route declares
`destination: Literal["", "databricks", "server", "local"] = ""`, so an
unknown value is a `422` before any backend work.
`list_runs` is O(N) in `max_results` — MLflow has no batch artifacts API,
so each candidate run gets its own `client.list_artifacts` call to check
for a matching model/optimiser-result artifact; a run whose artifact
listing itself fails is logged and skipped rather than failing the whole
response. The route emits exactly one `mlflow_run_discovery_completed`
measurement after every attempted search (including a failed search), with
`outcome`, `max_results`, `search_calls`, `artifact_calls`, `runs_scanned`,
`runs_returned`, `artifact_failures`, `search_ms`, aggregate `artifact_ms`,
local `assembly_ms`, and `total_ms`. The record is constant-space and contains
no experiment id, run id, artifact path, parameter, metric, error text, or
other provider payload. Tracking-client setup failures remain owned by the
existing setup event because no run search was attempted.

The representative certificate uses the public route implementation with the
maximum 100 candidates. It requires exactly one search and no more than 100
artifact calls, local handler time (total less measured provider calls) below
500 ms, response-model JSON serialization below 250 ms, and a representative
payload below 1 MiB. Provider latency is deliberately not replaced with a
machine-local claim: production phase measurements provide that evidence, with
5,000 ms p95 total time at the 100-candidate cap as the operational decision
gate. The bounded N+1 path is retained, performing one search and capped artifact calls.
No speculative cache, retry fan-out, or concurrent request burst is added.

### Connection surface (`routes/mlflow.py`)

Discovery clients, registered-source resolution, and native artifact downloads are
pinned to the same destination without mutating global MLflow tracking state.
Pyfunc downloads also carry an explicit destination, but MLflow 3's nested
logged-model resolution consults global tracking state. That download therefore
uses the shared fluent-operation lock, temporarily selects the requested tracking
and registry URIs, and restores both URIs and the environment on every exit.
Loading the downloaded local model happens outside that critical section.
Experiment discovery follows every continuation token on that same client so
switching from the fluent API preserves the complete experiment list.
Databricks profile selection also applies to the Unity Catalog registry. A
destination switch during logging must neither redirect an existing run nor
leave it unterminated; existing logs finish against their captured destination.

Three endpoints own connection visibility and configuration. They consume
`list_destinations()` / `resolve_destination()` / `node_destination_key()`
/ `candidate_tracking_config()` / `load_mlflow_settings()` /
`save_mlflow_settings()` from `haute.modelling._mlflow_settings`
([modelling](../modelling/low-level.md) owns that contract — the
destination inventory, the per-key resolver, and the local-folder default) and treat
`MlflowConfigError` as reportable data, never a 5xx. `GET /api/mlflow/status`
no longer exists (a `404`); no frontend caller references it.

- **`GET /api/mlflow/destinations?probe=<bool>` →
  `MlflowDestinationsResponse`** — `mlflow_installed`, `mlflow_importable`,
  top-level `detail` (the package problem, else empty), and `destinations`: exactly three
  `MlflowDestinationEntry` rows in display order — `databricks`, `server`,
  `local` — each with `key`, `configured`, secret-free `destination`
  (`databricks://<profile>`, the workspace host, the credential-redacted
  server URI, or the absolute runs folder), `config_source`
  (`""|"toml"|"env"|"default"`), `detail` (why the entry is unconfigured,
  naming what to set; or the probe failure), `probed`, `ok`, and
  `category`. Package presence, importability, and configuration are
  independent facts: a missing or unimportable package still reports the
  inventory (resolution needs no mlflow package). With `probe=true` and an
  importable package, each *configured remote* (`databricks`, `server`) is
  probed concurrently through the bounded probe helper below, so the
  response never blocks longer than one probe budget; an unconfigured
  remote reports `configured=false` with its detail and is never probed;
  `local` is never probed (`probed=false`, it always works). A failed probe
  keeps `configured=true`, sets `probed=true, ok=false`, the classified
  `category`, and the non-secret detail: nothing redirects on a broken
  remote. There is no automatic choice to report — a node that names no
  destination uses the local folder. Every entry is reported on its own
  evidence, so one broken entry never hides the usable ones: a `[mlflow]`
  table that cannot be read (for example the retired `mode` key) marks the
  toml-backed `server` and `local` entries unconfigured with the parse
  reason while `databricks` keeps its own verdict; a Databricks
  configuration rejected for `MLFLOW_ENABLE_DB_SDK=true` marks only that
  entry, leaving a configured server and Local selectable and working, and
  only operations on a node that chose Databricks fail with that reason.
  No response field ever contains a credential.
- **`GET /api/mlflow/settings` → `MlflowSettingsResponse`** — the stored
  `[mlflow]` table verbatim (`section_present`, `tracking_uri`, `folder`;
  empty strings when absent) plus `resolved_folder` (the absolute folder
  the `local` destination currently resolves to) and `detail` (a malformed
  stored section is reported here, never as a 5xx).
- **`PUT /api/mlflow/settings`** (`MlflowSettingsUpdateRequest`:
  `tracking_uri=""`, `folder=""`; both independent) — validates before
  writing: a non-empty `tracking_uri` must be an `http(s)://` URL with a
  host and without embedded credentials; an empty `tracking_uri` clears the
  server key; an empty `folder` persists the currently *resolved* local
  folder, so a bare save of an env-derived local folder keeps it and the
  runs already logged there discoverable. The write goes through
  `save_mlflow_settings()` (tomlkit), replacing only the `[mlflow]` table
  and preserving every other section, comment, and layout choice, and
  refuses a `haute.toml` whose resolved path escapes the project root
  (symlink containment); the response repeats the GET shape after the
  write. A validation failure → `400` naming the offending field without
  echoing the rejected value; nothing is written. Databricks has no stored
  settings: its credentials stay in `.env` or the selected profile.
- **`POST /api/mlflow/test-connection` → `MlflowTestConnectionResponse`** —
  probes one destination with one `experiments/search` request for a single
  experiment under a 5-second bound. The `MlflowTestConnectionRequest` body
  carries `destination` (a key, or `""`/absent body for the local
  folder) plus optional drafts `tracking_uri`/`folder`, each `null`
  when not supplied. A key with no draft supplied probes that destination
  exactly as the inventory currently resolves it (`resolve_destination`),
  including an environment-seeded server; a key with any draft supplied
  (even an empty string) is resolved via `candidate_tracking_config(key,
  settings)` so the user tests exactly what a save of that draft would
  produce (server: the draft URL with env-credential re-attachment, an
  empty draft being a field-naming configuration error; local: the draft
  folder, else the folder a bare save would keep; databricks: the
  environment/profile, drafts ignored). An unconfigured or unknown
  destination, or an invalid draft, reports `category="configuration"`
  with the prerequisite- or field-naming reason. Returns `ok=true`, or
  `ok=false` with `category`
  (`"authentication"|"permission"|"missing_resource"|"connectivity"|`
  `"configuration"|"unknown"`) and a non-secret `detail`. The probe never
  calls `mlflow.set_tracking_uri` — testing a candidate destination must
  not mutate the process-global tracking URI other consumers read. The
  probe runs on a **daemon** worker thread with a hard deadline; an
  abandoned worker cannot delay process exit, worker slots are bounded
  (2), a slot frees only when its underlying call returns, and with every
  slot occupied the route reports a still-running detail instead of
  spawning more workers. The underlying call is itself bounded: a REST
  destination (server or Databricks) is probed with exactly one HTTP
  attempt — retries disabled and the connect/read timeout set to the probe
  budget — using the same host credentials MLflow's own REST store would
  resolve for that URI, so an abandoned worker returns within about two
  probe budgets and frees its slot. MLflow's client defaults (120 s
  timeout, 7 retries, exponential backoff) would otherwise keep the slot
  occupied for minutes after the route had answered, and two failed tests
  would lock the connection test out for that long. The local file store
  has no transport and is probed through the client. Categories map from MLflow
  `RestException.error_code` values (`UNAUTHENTICATED` and
  invalid-credential codes → authentication; `PERMISSION_DENIED` →
  permission; `RESOURCE_DOES_NOT_EXIST` → missing_resource), transport
  connection/timeout errors → connectivity, and `MlflowConfigError` →
  configuration. Classification walks the full
  `__cause__`/`__context__` exception chain — MLflow wraps transport
  failures in `MlflowException`, so the outer type alone is never
  trusted — and never keys on exception class alone. Expected probe
  failures never surface as 5xx. The same `(ok, category, detail)`
  outcome helper serves the destinations endpoint's per-entry probes, and
  it is the only logger of probe failures: the log record carries the
  category and the exception type name, never the exception text, which
  can echo tokens or credential-bearing URLs.

`list_model_versions` fetches each version's backing-run params
via `_model_version_run_params`, which swallows (and logs with a full
traceback) a failed `client.get_run` — a deleted/inaccessible backing run
degrades that one version's `params` to `{}` rather than failing the
endpoint.

## Edge cases and invariants

- **Disk-cache directory names are sha256 digests of the artifact path**, not
  the artifact's own basename — two artifacts with the same basename in
  different directories (or across different runs sharing a digest
  namespace) never collide on disk, and the identity is validated
  (`_validate_disk_cache_run_id`, `_validate_artifact_path`) before any
  path is constructed, rejecting path separators, `.`/`..` segments, and
  null bytes. The run directory itself sits under the resolved backend's
  digest partition, so the same run on two destinations (or on two
  endpoints of one category) is two directories, and warm-cache loads
  follow a changed server URL, local folder, Databricks host, repointed
  profile, or a node's changed destination to the newly selected
  backend's artifact.
- **Every disk-cache caller resolves its root through `_disk_cache_root()`**.
  Artifact resolution, blanket/targeted clear, and the native-model fast path
  therefore cannot drift to different cwd-derived locations.
- **`_artifact_cache_path` asserts the computed path stays under the
  resolved cache root** as defence-in-depth beyond the string-level
  validation above.
- **`_artifact_io_locks` is a `WeakValueDictionary`** — per
  `(backend digest, run_id, artifact)` `RLock`s are reentrant (the load
  path can re-enter through `_resolve_artifact_local` while already
  holding the lock for the same key) and evaporate once no caller
  references them, so the lock table never grows unboundedly across a
  long-running process; concurrent loads of the same run on different
  backends never serialise on each other.
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
  protected until the *last* one finishes, not the first. The final active
  check and tombstone rename share the guard; `rmtree` deliberately does not.
- **Offset-column handling is flavor-specific by design, not
  uniform.** RustyStats and pyfunc models receive the offset column as
  part of the predict frame itself (`_OFFSET_PASSTHROUGH_FLAVORS`);
  CatBoost never receives it as a feature column — it is supplied as a
  numeric `Pool` baseline, because CatBoost only applies a baseline
  passed inside a `Pool`, never through a bare matrix `predict()`. A missing,
  null, non-numeric, or non-finite explanation offset raises
  `ModelExplanationError` before CatBoost is called.
- **Pyfunc feature discovery uses the supported MLflow signature API.**
  `_extract_pyfunc_features` reads `signature.inputs.input_names()`; Haute's
  MLflow dependency floor is 3.11 and no older list-shaped signature adapter
  is retained.
- **Feature order is checked only relatively.** Extra columns elsewhere
  in the input schema are fine; what's enforced is that the model's own
  features appear in the schema in the same relative order they were
  declared in training (`_validate_features_uncached`'s
  `actual_order_by_position` check).
- **The batch scorer's zero-row output dtype is derived from model metadata
  where available, never assumed from the task alone.** CatBoost classification
  uses `classes_` for hard labels and raises if that metadata is absent; other
  flavors use a one-row synthetic probe through the real scoring path. This
  keeps an empty score's parquet schema
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
- **`_wrap_catboost` refuses a model whose metadata cannot be read.** It
  reads `get_metadata()` once, and any failure raises `ConfigError` naming
  the `source` it was given: the local file path, or the MLflow run and
  artifact. Neither offset reader catches exceptions, so no caller can
  turn a failed read into "trained without an offset". Being a
  `ConfigError`, the refusal passes through `_load_with_bounded_retry`
  immediately rather than triggering re-downloads.

## Error handling

| Situation | Exception | Where it surfaces |
|---|---|---|
| `mlflow` not installed | `ImportError` | `resolve_mlflow_source`; routes' `_ensure_tracking` converts to `HTTPException(503)` with `MLFLOW_NOT_INSTALLED_DETAIL`, the same status and detail the modelling and optimiser log routes return. |
| Missing `run_id`/`registered_model`, invalid `source_type`, no versions found | `ValueError` | `resolve_mlflow_source` / `resolve_version`. |
| No matching artifact in a run | `_ArtifactNotFoundError` (⊂ `FileNotFoundError`) | `_find_model_artifact` and its per-extension helpers; a genuine `MlflowException`/bare `FileNotFoundError` from `list_artifacts` is not caught here. |
| Unsupported local file extension | `NotImplementedError` | `load_local_model`. |
| CatBoost model metadata cannot be read | `ConfigError` naming the model source (chained from the read failure) | `_wrap_catboost`; re-raised immediately by `_load_with_bounded_retry`. |
| Corrupt/unloadable artifact after bounded retry | `RuntimeError` (chained `from last_err`) | `_load_with_bounded_retry`. |
| Bug in load dispatch (bad attribute/type/key) | `AttributeError` / `TypeError` / `KeyError` | Re-raised immediately from `_load_with_bounded_retry`, never retried. |
| Invalid disk-cache run_id/artifact_path | `ValueError` | `_validate_disk_cache_run_id` / `_validate_artifact_path`, called from `_artifact_cache_path` before any I/O. |
| Feature/order/categorical/offset mismatch | `FeatureMismatchError` | `_validate_features_uncached` / `_require_offset_column`; propagates through `ModelScorer.score()` uncaught (no rewrap of other exception types — see `_run_score_pipeline` docstring). |
| Unsupported scoring flavor | `ConfigError` | `score_frame()`, at the top of dispatch. |
| Unreachable/unknown flavor in predict-frame prep | `ValueError` | `_prepare_predict_frame`; enumerates `_SUPPORTED_FLAVORS`. |
| Multiclass / malformed `predict_proba` shape | `ValueError` | `_positive_class_proba_vector`, shared by eager and batch. |
| Write projection references un-produced/un-preserved columns | `ValueError` | `_score_output_projection_columns`. |
| Explanation reconstruction/shape/finiteness failures | `ModelExplanationError` | `_model_explainability.py`, both `explain_catboost_prediction` and `explain_rustystats_glm_prediction`. |
| MLflow search call failure in a discovery route | Logged `Exception` → `HTTPException(502)` with a category-mapped, non-secret detail | `routes/mlflow.py::_discovery_http_error` — the shared `classify_mlflow_error` (also used by the probe and the log routes; connectivity means a transport failure, never a local `OSError`) maps authentication (".env credentials"), permission, missing-resource, and connectivity failures to actionable messages; anything unclassified keeps `_INTERNAL_ERROR_DETAIL`. The real error text is never sent to the client. |
| Tracking misconfiguration in `_ensure_tracking` (an unconfigured explicit `destination`, or a local folder the environment breaks) | `MlflowConfigError` → `HTTPException(502, str(exc))` | Configuration messages are haute's own, actionable and secret-free, so they surface verbatim; other setup failures keep the generic detail. An unknown `destination` value is a `422` from query validation. |
| Unresolvable destination at model/artifact load time | `MlflowConfigError` | `resolve_backend`, before any memory or disk cache lookup — a cached artifact is never served for a destination whose prerequisites are missing or whose Databricks profile cannot be loaded. |
| Registered model version's backing run inaccessible | Swallowed (`Exception`), logged with `logger.exception` | `_model_version_run_params` returns `{}` for that version only; the endpoint still returns `200`. |
| Unconfigured destination reaching the connection surface | `MlflowConfigError` caught | `/destinations` reports that entry `configured=false` + reason (the toml-backed entries unconfigured when the `[mlflow]` table itself is unreadable); `/test-connection` reports `category="configuration"`; discovery routes still propagate it via `_ensure_tracking` → `502`. |
| Invalid settings update (non-`http(s)` server URI, credential-bearing URI) | `HTTPException(400)` naming the field | `PUT /api/mlflow/settings`, before any write. |
| Probe failure in test-connection | Classified result, never a 5xx | `POST /api/mlflow/test-connection` — `RestException.error_code` + transport errors → `category`/`detail`. |

`FeatureMismatchError`, `ConfigError`, and `ModelExplanationError`'s
sibling errors in this component all carry structured context — see
`haute.errors.HauteError.__init__`. `ModelExplanationError` itself is a
plain `RuntimeError`, not a `HauteError` subclass.

## Testing

- `tests/test_offset_scoring.py` verifies offset-aware GLM/CatBoost/pyfunc/canvas/deploy scoring, feature-name handling, metrics, and signature contracts.
- `tests/test_scoring_path_unified.py` verifies explicit flavor dispatch, unified scoring regression guards, structural invariants, wrapper dispatch, and eager/batch equivalence.
- `tests/test_scoring_prep_perf.py` verifies prediction-frame preparation correctness, pyfunc named-frame dispatch, downstream passthrough, edge cases, and benchmark behavior.

Tests live across primary files under `tests/`. Strategy is unit-level with `mlflow`,
`catboost`, and `rustystats` either mocked or exercised against small
real artifacts fixture-built in `tmp_path`; there is no test that talks
to a live MLflow tracking server.

- **`tests/test_mlflow_io.py`** — the bulk of `_mlflow_io.py` coverage:
  run/registered loading (happy path, missing args, invalid source
  type), pyfunc auto-detect and auto-discovery fallback, in-memory cache
  hit/LRU-eviction, CatBoost loader dispatch by task, model wrapping
  (CatBoost cat-feature extraction, pyfunc signature extraction),
  predict-frame preparation for
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
  atomically tombstones before lock-free deletion, the fast disk-cache path
  marks a run active before probing), and waiters on both the fast-path and
  full-resolve-path reusing a model that finished loading while they
  waited.
- **`tests/test_mlflow_model_cache_key_contract.py`** — pins cache-key
  *completeness* specifically for the artifact-fingerprint component:
  `TestFastPathArtifactPerturbation` and `TestFullPathArtifactPerturbation`
  each rewrite the local artifact's bytes in place (disk-cache file for the
  fast path, resolved local path for the full path) under an unchanged run
  reference and assert the next `load_mlflow_model` call returns a model
  built from the new bytes rather than the stale in-memory entry;
  `TestKeyContract` covers the key-shape invariants directly (`run_id`
  fixed at slot 1 regardless of `version`'s presence, the backend identity
  fixed as the last element, pyfunc keyed with an
  empty-string fingerprint). These tests simulate re-log/retrain replacement
  by rewriting the already-resolved local artifact path; they pin local-byte
  invalidation of the in-memory cache, not detection of an unseen remote
  overwrite behind an unchanged disk-cache file.
- **`tests/test_mlflow_io_real_pyfunc.py`** — pyfunc wrapping and predict-
  frame dtype fidelity against a real (non-mocked) MLflow pyfunc model,
  including the named-column signature contract and declared-dtype
  precision preservation.
- **`tests/test_mlflow_registered_alias.py`** — against a real local
  registry: an alias resolving to the version it targets and following a move,
  a missing alias naming the model and alias, a concrete version beside an alias
  rejected, a scoring load following the alias to the newly targeted model,
  alias/version exclusivity and malformed aliases in node config validation,
  `/model-versions` listing each version's aliases, the optimiser loader
  forwarding the alias, and the deploy bundle recording the alias's resolved
  version and run.
- **`tests/test_mlflow_utils.py`** — `search_versions` (name quoting),
  `resolve_version` (`"latest"` resolution, explicit version passthrough,
  no-versions-found error), and `resolve_backend`: the identity rule for
  each category (canonical local folder, credential-redacted server
  endpoint, Databricks effective host plus selected profile, registry
  suffix), distinct digests for two folders and for a repointed profile, an
  unloadable profile failing secret-free, no destination staying on the
  local folder when Databricks is configured, unknown keys rejected, and
  `resolve_mlflow_source` pinning its client to the returned backend for both
  the empty and explicit destinations.
- **`tests/test_mlflow_databricks_credentials.py`** — the Databricks
  credential binding at request level, with the general and the MLflow pair
  set to different hosts and tokens (patched `requests.Session.request`):
  tracking and Unity Catalog registry requests, and every Databricks API
  request made for run and logged-model artifact listing, upload and download
  (including the scoped-credential requests), reach only the MLflow host with
  the MLflow bearer token and construct no Databricks SDK client; the byte
  transfers go only to the signed cloud-storage URLs those credential
  responses return and carry neither workspace token; a REST artifact failure
  propagates to the caller; Unity Catalog model-artifact listing, download
  and upload through MLflow's SDK repository (selected by the workspace), with
  a data-access service principal and `DATABRICKS_AUTH_TYPE=oauth-m2m` in the
  environment, send only the MLflow bearer token to the MLflow host and make no
  OAuth token request; identity and the next request on the same
  client follow a repointed `DATABRICKS_MLFLOW_HOST`; the bound provider with
  the pair unset raises naming both variables without consulting the
  `DEFAULT` profile; a profile URI still binds that profile; binding is
  idempotent, a no-op when mlflow is absent, restorable, and happens in a cold
  interpreter before the first client is built (subprocess); and an upgrade
  guard asserts the three MLflow symbols the binder replaces still exist and
  are still resolved at call time. An autouse fixture restores the patched
  MLflow globals and the binder state after every test.
- **`tests/test_mlflow_destination_cache.py`** — identical run IDs and artifact paths with different contents on
  two local folders and on two server endpoints of the same category never
  alias (distinct digest partitions on disk, distinct memory entries and
  locks); warm-cache loads follow a toml folder change, a node choosing
  Databricks (while configuring Databricks alone never retargets a node that did
  not choose it), and a changed endpoint to the newly selected backend's artifact; removing an
  explicit destination's prerequisites after warming raises
  `MlflowConfigError` instead of serving the cached artifact; targeted
  `clear_model_cache(run_id)` clears every backend partition; eviction
  counts run directories across partitions; concurrent loads on different
  backends stay isolated.
- **`tests/test_mlflow_destinations_e2e.py`** — against real local file stores while Databricks is configured but not chosen: a training job logged
  to Local scores through a MODEL_SCORE node that names no destination; an optimiser
  artifact logged to Local applies through OPTIMISER_APPLY, the deploy
  scorer's request-time optimiser path, and explanation loading, all
  without touching the remote; an unavailable explicit destination fails
  without consulting another backend.
- **`tests/test_mlflow_connection_routes.py`** — the connection surface plus
  registry parity: a model registered in a real local file store surfaces
  through `/models` and `/model-versions` with string versions and the
  backing run id (pinning the int-version and null-description provider
  normalisations); the connection surface itself: the destinations
  inventory reporting all three entries truthfully with no `auto` field, probing
  only configured remotes, running two probes concurrently inside one
  probe budget, reporting a failed probe on its entry with a secret-free reason,
  reporting an unreadable `[mlflow]` table on the toml-backed entries,
  never probing when the package is missing, and redacting a credentialed
  env URI; the removed `/status` route returning `404`; settings GET/PUT
  round trip against a real `haute.toml` (layout preservation, empty
  `tracking_uri` clearing the key, `400` validation for non-`http(s)` and
  credential-bearing URIs without echoing them, bare-save resolved-folder
  persistence, a malformed section reported as data); an end-to-end
  regression proving a run logged in an env-selected folder remains
  discoverable through `/api/mlflow/experiments` after an unchanged local
  save; test-connection probing the local folder without a body, an explicit key, a
  draft server URL instead of the saved one, a draft local folder, an
  unconfigured or unknown destination as `category="configuration"`, and a
  broken selected profile staying configured with a secret-free failure;
  classification with one named case per category plus a wrapped
  `MlflowException` transport chain and a real dead-port transport
  boundary run under MLflow's default retry policy, proving both probe
  slots are free again once the route has answered; and probe mechanics
  (deadline on hanging work, daemon workers, bounded slots with a
  still-running busy report, and the per-destination request shape — one
  retry-free, timeout-bounded request with the REST store's host
  credentials for server and Databricks, the file-store client for local,
  never a global tracking-URI mutation).
- **`tests/test_mlflow_routes.py`** — full FastAPI coverage of every
  route: experiments/runs/models/model-versions happy paths, the
  artifact-filter behaviour of `/runs` (model vs optimiser), a failing
  per-run `list_artifacts` call being skipped rather than failing the
  whole listing, `_ensure_tracking`'s both failure branches (missing
  mlflow → `503`, backend resolution failure → `502`), the `destination`
  query forwarded to `_ensure_tracking` (an explicit key resolving its own
  backend, an unconfigured key → `502` naming the prerequisite, an unknown
  value → `422`), and additional
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
  test. `TestScoreDtypeResolution` learns an undeclared classifier's dtypes from one
  all-null row and raises for a CatBoost classifier without `classes_`. `TestRowLocalScanScoring` scores a real CatBoost model through the scan:
  a limit predicts only the limited rows, an aggregation predicts every row, a
  projection without the prediction predicts nothing, predictions and dtypes
  match eager scoring for regression and classification, and an undeclared
  categorical level raises through `streaming_collect`.
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
- **`tests/test_feature_validation_cache.py`** — feature-validation LRU and
  last-entry semantics, targeted/full invalidation, model-cache cascade
  behavior, and callback lock boundaries.
- **`tests/test_model_cache_observability.py`** — structured model-cache
  hit/miss events, counter concurrency, and blanket-clear reset semantics.
- **`tests/performance/test_external_scaling_perf.py::test_mlflow_run_discovery_maximum_cardinality_budget`** —
  enforces the representative performance certificate for run discovery: exactly one search call,
  at most 100 artifact calls, and latency/serialization budgets under the maximum candidate cap.

`score_from_config` is also exercised indirectly by
`tests/test_model_score_codegen.py` and
`tests/test_model_score_executor.py`, which belong primarily to the
codegen and execution-engine components respectively and are not detailed
here.

Known coverage gaps: none. Empty-batch schema behavior, minimal live
categorical-validation projection, and the absence of an unbenchmarked
contiguity conversion all have direct regression coverage.
