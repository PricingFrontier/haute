# MLflow Model Registry — High-Level Specification

## Purpose

Haute pipelines score data through models that were trained and logged
outside the pipeline itself — as an MLflow run artifact or as a version of
an MLflow registered model. This component is the read path for those
models: given a source description (a run + artifact path, or a
registered model + version), it resolves that description against the
configured MLflow tracking server, downloads and caches the underlying
artifact, loads it with the right flavor-specific loader (CatBoost native,
RustyStats native, or generic MLflow pyfunc), and wraps it behind one
uniform interface the rest of the codebase scores against without caring
which flavor it is.

It also owns everything downstream of "model is loaded": validating that
an input DataFrame actually satisfies the model's feature contract before
prediction is attempted, running the prediction itself (in-memory for
interactive/preview use, chunked-to-disk for large batch runs), and
producing a per-prediction explanation (SHAP for CatBoost, native
contribution decomposition for RustyStats) for trace enrichment. A small
set of read-only discovery HTTP endpoints lets the pipeline-builder GUI
browse experiments, runs, registered models, and versions to configure a
MODEL_SCORE node without the user needing to know MLflow identifiers by
heart.

## Scope

In scope:
- Resolving an MLflow model source — a specific run's artifact, or a
  registered model version (including `"latest"`) — to a concrete run ID
  and artifact path via the tracking/registry API.
- Auto-discovering the model artifact within a run when no artifact path
  is given (CatBoost `.cbm`, then RustyStats `.rsglm`, then a pyfunc
  model directory).
- Downloading and disk-caching native-flavor artifacts, and
  thread-safe in-memory LRU caching of already-loaded models.
- Flavor detection and flavor-specific loading; the `ModelFlavor` domain
  is the single source of truth every dispatch site reads from.
- A uniform `ScoringModel` carrier (`predict`, `predict_proba`,
  `raw_model`) so downstream code never branches on flavor except inside
  this component's own dispatch helpers.
- Preparing an input DataFrame into the shape each flavor's `predict`
  expects (numpy/pandas dtype and categorical handling).
- Feature-contract validation at score time: presence, relative order,
  and categorical dtype, plus enforcing a model's trained-with
  offset/exposure column.
- Eager (in-memory) and batched (disk, chunked-parquet) scoring, and an
  output/input column write-projection that can prune both to exactly
  what a caller needs.
- Per-prediction SHAP (CatBoost) and native GLM contribution (RustyStats)
  explanations used to enrich a traced prediction.
- Read-only MLflow discovery endpoints (`/api/mlflow/experiments`,
  `/runs`, `/models`, `/model-versions`) that populate the MODEL_SCORE
  node's configuration UI.

Out of scope (owned elsewhere):
- Logging *new* MLflow runs — training diagnostics, SHAP summaries, model
  cards, and optimiser artifacts — is a write path owned by
  [modelling](../modelling/high-level.md) (`_mlflow_log.py`) and, for the
  optimiser's own run logging, the [optimiser](../optimiser/high-level.md)
  component. This component only ever reads.
- Deriving the train-time feature contract itself (declared features,
  offset column, categorical value domains) — owned by
  [modelling](../modelling/high-level.md) (`_feature_contract.py`); this
  component only loads and enforces a contract that already exists.
- Bundling a scored model into a standalone deployable artifact or a
  Databricks Model Serving target — see
  [deploy](../deploy/high-level.md) (`deploy/_bundler.py`,
  `deploy/_mlflow.py`), which reuses this component's loader primitives
  but owns the packaging/serving concern.
- The MODEL_SCORE node's place in the pipeline graph, its codegen
  template, and executor wiring — `score_from_config` in
  `_model_scorer.py` is codegen's *delegation target*, not the generator;
  see the execution-engine and codegen components.
- General HTTP conventions (auth, response timeout wrapping) beyond the
  discovery routes themselves — see
  [server-api](../server-api/high-level.md).

## Behaviour

- A model is located one of two ways: `source_type="run"` with a
  `run_id` (and optional `artifact_path`, auto-discovered if omitted), or
  `source_type="registered"` with a `registered_model` name and a
  `version` (a literal version number, or `"latest"`, which resolves to
  the highest numeric version currently registered).
- Loaded models are cached in two tiers. An in-memory LRU (16 entries)
  holds fully-loaded `ScoringModel` objects keyed by the resolved source
  identity plus `task`. A disk cache under `.cache/models/<run_id>/...`
  holds the downloaded bytes for CatBoost and RustyStats artifacts (not
  pyfunc, which relies on MLflow's own local artifact cache), bounded to
  50 run directories with oldest-first eviction. Both caches persist
  across calls within a process; the disk cache also survives process
  restarts.
- When the caller already knows the exact run + artifact (the common
  case once a pipeline is configured), loading can skip the tracking
  server round-trip entirely and go straight to the in-memory or
  disk cache.
- Concurrent requests for the *same* artifact never each download or load
  it independently — one caller does the work, the rest wait and reuse
  the result. Concurrent requests for *different* artifacts proceed in
  parallel.
- A model artifact that fails to load (corrupt or truncated cache file)
  is deleted and re-downloaded once, with a short randomized backoff,
  before the failure is allowed to propagate — a persistently broken
  artifact fails loudly rather than looping silently.
- Every loaded model exposes the same interface — `predict()`,
  `predict_proba()` (or `None` if unsupported), and `raw_model` — so
  scoring code, explanation code, and deploy code share one calling
  convention regardless of whether the underlying object is a
  `CatBoostRegressor`, a RustyStats `GLMModel`, or an MLflow
  `PyFuncModel`.
- Before a prediction is attempted, the input schema is checked against
  the model's declared features: every feature must be present, in the
  same relative order the model was trained with (categorical feature
  indices are positional for CatBoost), with categorical columns
  presented as non-numeric — and, if the model declares a trained-with
  offset/exposure column, that column must also be present. Any of these
  failing raises before prediction runs; validation results are memoised
  so repeated score calls against the same model/schema pair are cheap.
- Scoring runs either eagerly (the input is collected once, in memory,
  and predictions are appended to that same materialisation) or in
  batches (the input is written to a temp parquet file and predicted
  chunk-by-chunk, streaming a lazy scan of the result back) — the caller
  chooses per call; there is no automatic switch based on estimated size.
- A caller can supply a write projection naming exactly which passthrough
  columns the scored output must contain; both the scoring input read and
  the output write are pruned to that set (plus the model's own features
  and any declared offset column), avoiding materialising columns nobody
  asked for.
- For classification tasks, a `<output_col>_proba` column carries the
  binary positive-class probability when the model supports
  `predict_proba`; a model whose `predict_proba` returns more than two
  classes' worth of probabilities is rejected rather than silently
  reporting one arbitrary class's probability as "the" positive-class
  value.
- A per-prediction explanation reconstructs the traced prediction from
  its own decomposition (SHAP values for CatBoost, contribution terms for
  RustyStats) and verifies the reconstruction matches the model's actual
  output within a small numeric tolerance before returning it — an
  explanation that doesn't add up is never shown to a user.
- The discovery endpoints only ever read from the configured MLflow
  tracking server; they never touch the model cache and have no
  side effects on it.

## Design rationale

- **Two-tier caching (disk + memory).** Downloading a model artifact from
  a Databricks-hosted tracking server can take 30 seconds or more; the
  disk cache means that cost is paid once per artifact per machine, not
  once per process restart. The memory cache exists on top of that so a
  hot model inside a long-running process/preview session avoids even the
  disk read and the flavor-specific deserialisation cost.
- **Per-artifact locking, not one global lock.** A single lock around
  "load a model" would serialize unrelated artifacts unnecessarily; a
  lock keyed on `(run_id, artifact_path)` lets distinct artifacts load
  concurrently while guaranteeing the classic thundering-herd case
  (many callers all requesting the same cold artifact at once) resolves
  to exactly one download.
- **Bounded retry, not infinite or none.** One clean retry after deleting
  a suspect cache file absorbs genuinely transient issues (an
  interrupted download, a disk hiccup); a hard ceiling after that means
  persistent corruption becomes a loud, diagnosable failure instead of an
  invisible retry loop burning time and bandwidth on-call would never see.
- **Cache eviction cascades to feature-validation state.** The
  feature-validation cache in `_model_scorer.py` is keyed by *content*
  (feature names, categorical set, offset column) rather than by model
  object identity, specifically so that evicting a `ScoringModel` from
  the model cache can purge exactly the validation results tied to that
  contract — a later reload of the same contract, even as a distinct
  object, still gets a cache hit, and a genuinely evicted contract
  doesn't leave stale validation results pinned forever.
- **Fail loud on structural mismatches.** CatBoost's categorical feature
  indices and offset baseline are positional/structural, not named —
  scoring with reordered features or a missing offset would silently
  produce a wrong-but-plausible prediction rather than an error. The
  codebase's stated preference for loud failure over silent fallback is
  applied deliberately hard here: feature order, categorical dtype, and
  offset presence are all checked before any `predict()` call, and a
  multiclass `predict_proba` output is rejected rather than arbitrarily
  picking one class's column.
- **The flavor domain lives in its own leaf module.** `_model_flavors.py`
  has no dependency on the rest of `haute`, specifically so that
  `_model_scorer.py` and `_mlflow_io.py` — which already have a
  load-order dependency on each other — can both import the *same*
  `ModelFlavor` object instead of each hand-maintaining a parallel
  spelling of `"catboost"` / `"pyfunc"` / `"rustystats"` that could drift.
- **Explanation additivity is enforced, not assumed.** A SHAP or
  contribution breakdown that looks reasonable but doesn't actually sum
  to the model's real prediction is strictly worse than no explanation at
  all — it would be trusted. Both explanation paths recompute the model's
  own prediction independently and compare it to the decomposition's sum
  before returning anything.
- **RustyStats owns its own contribution decomposition; Haute never
  reconstructs it.** The retired RUSTYSTATS_TRACE_EXPLAINABILITY doc (git
  history) records the original design decision: RustyStats' GLM structure (spline bases,
  categorical/target encodings, interactions, offsets, complement logic)
  is implementation detail internal to the model library, so Haute calls
  RustyStats' own `predict_contributions(...)` API
  (`explain_rustystats_glm_prediction` in `_model_explainability.py`) rather
  than re-deriving term contributions from the fitted coefficients itself.
  The consistency target between CatBoost and RustyStats explanations is
  the shared `output_space`/`prediction_space`/`base_value`/`contributions`
  *contract shape*, not the underlying mathematical method — CatBoost uses
  SHAP values, RustyStats uses exact GLM term contributions grouped back to
  source terms, and both report where their contributions live (raw/linear-
  predictor space) separately from where the final prediction lives
  (response space) so a non-identity-link model's ladder can label the
  conversion explicitly.

## Interactions

- Depends on [modelling](../modelling/high-level.md) for tracking-backend
  resolution (`_mlflow_log.py::resolve_tracking_backend`, shared by both
  the loader and the discovery routes), the CatBoost offset-metadata key
  contract (`_algorithms.py::CATBOOST_OFFSET_METADATA_KEY`), and the
  train-time feature contract (`_feature_contract.py`) that
  `_model_scorer.py` loads and enforces at score time.
- Is consumed by the pipeline
  [execution-engine](../execution-engine/high-level.md): the MODEL_SCORE
  node calls `ModelScorer.score()` / `score_frame()` directly, and
  codegen-generated pipeline scripts call `score_from_config` as their
  delegation target.
- Is consumed by [deploy](../deploy/high-level.md), which loads and
  bundles models via the same `load_mlflow_model` /
  `resolve_mlflow_source` primitives for its own scorer, and by
  [optimiser](../optimiser/high-level.md), which loads MLflow-backed
  models to evaluate during optimisation.
- Feeds the [tracing](../tracing/high-level.md) component: per-prediction
  explanations produced here are attached to trace output for a scored
  node.
- `routes/mlflow.py` is mounted under `/api/mlflow/*` as part of
  [server-api](../server-api/high-level.md) and follows that layer's
  shared error-response conventions (a non-leaking generic detail message
  on unexpected failures).
- Depends on the third-party `mlflow`, `catboost`, and `rustystats`
  packages, all imported lazily at the point of use so none of them is a
  hard dependency of the package.

## Failure model

- `mlflow` not installed: `ImportError` from `resolve_mlflow_source`
  (the discovery routes convert this to `HTTPException(503)`).
- Missing required source arguments (`run_id` for `"run"`,
  `registered_model` for `"registered"`), an invalid `source_type`, or no
  versions found for a registered model: `ValueError`.
- No matching model artifact found in a run after checking `.cbm`,
  `.rsglm`, and a pyfunc model directory (top level and one level of
  subdirectories): an internal `_ArtifactNotFoundError`
  (a `FileNotFoundError` subclass). A *different* `FileNotFoundError` or
  an MLflow `MlflowException` raised by the tracking client itself (e.g.
  a credential or network failure) is never caught here — it propagates
  as the real infrastructure error instead of being reported as "no
  model artifact found."
- Loading a local file with an unsupported extension via
  `load_local_model`: `NotImplementedError`.
- A model artifact that is still corrupt/unloadable after the one bounded
  retry: `RuntimeError` naming the run ID, artifact path, flavor, and the
  last underlying error.
- `AttributeError` / `TypeError` / `KeyError` raised while loading a
  model are treated as a programming or library-contract error, not
  corruption — they are never retried and propagate immediately with
  their real stack trace.
- A scoring input that fails the feature contract (missing features,
  wrong relative order, a categorical column with numeric dtype, or a
  missing offset column): `FeatureMismatchError` with the full expected /
  available / missing / type-mismatch detail.
- An unsupported or misspelled scoring flavor reaching `score_frame()`:
  `ConfigError` — never silently treated as pyfunc.
- A `predict_proba` output representing more than two classes, where only
  a single binary positive-class column is defined, or an output of an
  unsupported shape: `ValueError`.
- A write projection that names output columns that were neither
  produced by scoring nor preserved as passthrough: `ValueError`.
- Explanation failures (missing required features, a decomposition that
  doesn't reconstruct the model's own prediction, non-finite values, an
  unsupported multi-output model, or an unexpected result shape):
  `ModelExplanationError`.
- Discovery-route failures: `mlflow` not installed → `503`; tracking
  backend resolution failure → `502`; an MLflow search call
  (`search_experiments` / `search_runs` / `search_registered_models` /
  `search_model_versions`) failing → `502` with a non-leaking generic
  detail message (the real error is logged server-side). A registered
  model version whose backing run has been deleted or is otherwise
  inaccessible does not fail the whole `/model-versions` response — its
  run-derived params are reported as empty and the failure is logged,
  since the version record itself is still valid.
- Invalid disk-cache identity — a `run_id` containing a path separator or
  `..`, or an `artifact_path` that would resolve outside the cache root —
  raises `ValueError` before any filesystem I/O is attempted.

> NOTE: pyfunc models never populate the on-disk artifact cache under
> `.cache/models/` — only CatBoost and RustyStats artifacts do. A pyfunc
> model is instead re-resolved through MLflow's own `pyfunc.load_model`
> path (which has its own, separate local caching behaviour) on every
> cache-miss load.
