# WS-07 — Modelling & MLflow model registry

Part of the Opus 5 review split (`opus-5-workstreams.md`). Evidence and fix guidance:
`opus-5-review.md`. Owner: WS-07 · Status: delivered in PR #140.

**Branch:** `opus5/ws-07-modelling-registry`

## Mission

Model training (the train service, GLM/CatBoost algorithms, MLflow logging) and the
MLflow-backed model cache/registry that scoring and deploy read from. Carries a Wave-1
data-loss bug (cancelled training destroying the durable model) and a registry cache-key /
`clear_model_cache(".")` cluster that can wipe the disk cache or bypass the tracking-server
fast path.

## Scope

| Component | C | H | M | L |
|---|---:|---:|---:|---:|
| modelling | 0 | 2 | 1 | 7 |
| mlflow-model-registry | 0 | 1 | 3 | 7 |
| **Total** | **0** | **3** | **4** | **14** |

## Priorities

**P1 — data loss (review Wave 1):**

- `modelling-1` (H): cancelled/timed-out training still `os.replace`s the staged model and
  feature contract over the durable files, then reports `cancelled` with `result: None`. Make
  publication itself the CAS point (claim `running→committing` under the store lock, or run
  `_persist_terminal_outcome` first). A lifecycle-API change, if needed, is coordinated with
  WS-03 (which owns `_job_lifecycle.py`); the train-service edit lives here.

**P1 — registry correctness:**

- `mlflow-model-registry-4` (M): `clear_model_cache(run_id=".")` deletes the entire disk
  cache — the `".." in run_id` substring check lets `"."` and null bytes through.
- `mlflow-model-registry-1` (M): fast-path and full-path cache keys disagree on `version`, so
  the documented tracking-server bypass never fires.
- `mlflow-model-registry-3` (M): CatBoost explanation builds its Pool without the offset
  baseline — every offset-trained model's trace explanation fails.
- `modelling-2` (H): `mlflow.register_model` uses a non-model artifact URI and is not
  best-effort — throws away a successfully trained model for every Databricks run with
  `model_name`. Register `runs:/…/model` and decide best-effort explicitly.
- `mlflow-model-registry-2` (H): the spec asserts zero-row batch dtypes are "derived, never
  hardcoded" while CatBoost hardcodes `Int64`/`Float64` — a classifier trained on string or
  boolean labels writes an incompatible empty-batch schema. Derive from `classes_` or raise,
  and correct the spec's two paragraphs.

**P2 — bugs:** `modelling-8` (cancel during synchronous materialisation doesn't stop work),
`mlflow-model-registry-6` (eviction holds the active-runs guard across `rmtree`),
`mlflow-model-registry-7` (cascade under the cache lock), `mlflow-model-registry-9` (failed
re-download loses the original error), `modelling-6` (an explicit `feature_columns` entry
that also appears in `exclude` is projected away, then fails deep in the child),
`modelling-11` (GLM link/family read asymmetry).

**P3 — spec truth:** test-only surface and undercounted tests
(`mlflow-model-registry-10`, `-11`, `testing-credibility-11` modelling half — pipeline-config
half is WS-06), `Polars backend contracts (0.6.0)` and cwd-recompute notes
(`mlflow-model-registry-5`, `-8`), fold shipped 0.8.0 isolated-fit contract
(`modelling-4`, `contracts-d-8`), docstring/spec drift (`modelling-10`, `modelling-3`,
`modelling-9`).

## Finding inventory

High (3): `modelling-1`, `modelling-2`, `mlflow-model-registry-2`.
Medium (4): `modelling-8`, `mlflow-model-registry-1`, `mlflow-model-registry-3`,
`mlflow-model-registry-4`.
Low (14): `contracts-d-8`, `modelling-10`, `modelling-11`, `modelling-3`, `modelling-4`,
`modelling-6`, `modelling-9`, `mlflow-model-registry-5`, `mlflow-model-registry-6`,
`mlflow-model-registry-7`, `mlflow-model-registry-8`, `mlflow-model-registry-9`,
`mlflow-model-registry-10`, `mlflow-model-registry-11`.

## File ownership (exclusive)

- `src/haute/routes/_train_service.py`, `routes/modelling.py`, `routes/_train_service` helpers
- `src/haute/modelling/**` (`_algorithms.py`, `_train_config.py`, `_mlflow_log.py`,
  `_training_job.py`, `_feature_contract.py`)
- `src/haute/_mlflow_io.py`, `_mlflow_utils.py`, `_model_scorer.py`,
  `_model_explainability.py`
- `docs/specs/modelling/**`, `docs/specs/mlflow-model-registry/**`
- Their tests (`tests/test_train*.py`, `test_modelling_routes.py`, `test_mlflow*.py`,
  `test_model_scorer.py`, `test_model_cache_observability.py`,
  `test_feature_validation_cache.py`, `test_model_explainability.py`)

## Cross-stream touchpoints

- `_job_lifecycle.py` / `_background_jobs.py` publication-CAS (WS-03): WS-07 implements the
  claim in the train service; if the lifecycle needs a new transition, WS-03 makes it.
- `_feature_contract.py` and `_model_scorer.py` are read by deploy (`deploy/_scorer.py`,
  WS-14) — keep the feature-contract format stable; deploy-side scoring stays in WS-14.
- `caching-7` names `_feature_contract.py`/`deploy/_scorer.py` as StatGatedCache sites — that
  spec text is WS-02's; no code overlap.

## Definition of done

- Cancelled/timed-out training can never overwrite the durable model — regression test drives
  a cancel into the publication window and asserts the deployed pair survives.
- `clear_model_cache` rejects `.`/`..`/null bytes; fast-path/full-path keys agree; offset
  CatBoost explanations succeed; `register_model` uses the model URI.
- modelling + registry contracts folded; test counts corrected.
- Baseline entries deleted; findings fixed or deferred with reasons.

## Verification

- `uv run pytest tests/test_modelling_routes.py tests/test_mlflow_io.py tests/test_model_scorer.py -q`
- Targeted cancel/timeout regression for `modelling-1`.
- `uv run pytest tests/test_docs_accuracy.py -q`; quick preflight near completion.
