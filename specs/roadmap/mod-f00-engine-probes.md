# MOD-F00 engine probes — 23 September 2026

Evidence for the pre-implementation gates of the
[model-family expansion plan](modelling.md#model-family-expansion-proposed-22-september-2026-revised-23-september-2026).
This is a dated evidence record, not a specification; the behaviour it supports
is binding only through the approved change contracts in the owning component
specifications.

The probe script is [`mod-f00-engine-probes.py`](mod-f00-engine-probes.py); the raw
output of every run is [`mod-f00-engine-probes.json`](mod-f00-engine-probes.json).
Each run fits tiny real models on a 2,000-row fixture (a four-level categorical
and a numeric feature, exposure, weights, a Poisson count, a Gamma severity and
a binary flag; every fifth row is validation).

## Environments

Each environment is Haute's own resolved dependency set (`pyproject.toml`) plus
these engine pins, resolved with `uv pip compile pyproject.toml engines.in
--universal -p <python>` and installed into an isolated virtualenv outside the
repository:

```text
xgboost-cpu==3.2.0 ; sys_platform != "darwin"
xgboost==3.2.0 ; sys_platform == "darwin"
lightgbm==4.7.0
interpret-core==0.7.8
```

Resolution succeeds for Python 3.11, 3.12 and 3.13 (NumPy 2.3.5, pandas 2.3.3,
scikit-learn 1.9.1, joblib 1.6.0; LightGBM adds `narwhals`). The probe then ran
with `PYTHONPATH=src python specs/roadmap/mod-f00-engine-probes.py OUT.json` on:

| Run | Platform | Python | Result |
|---|---|---|---|
| `windows-py3.11` | Windows 11 x86_64 | 3.11.14 | all probes completed |
| `windows-py3.13` | Windows 11 x86_64 | 3.13.11 | all probes completed |
| `linux-py3.11` | Ubuntu 24.04 (WSL2) x86_64 | 3.11.14 | all probes completed |
| `linux-py3.13` | Ubuntu 24.04 (WSL2) x86_64 | 3.13.11 | all probes completed |

The script checks every outcome this report relies on against its expected
value, including the intentional EBM leakage failure, and exits non-zero on an
engine error or any unexpected result, so the same command is the macOS CI
gate. All four runs exit 0. They agree on every recorded value except platform
names and message paths. macOS was not run locally (no macOS host). Its wheels were inspected
statically below, and CI's existing `macos-latest` matrix jobs must run the
probe once the dependency change lands.

## Packaging and platforms

| Finding | Evidence | Consequence |
|---|---|---|
| XGBoost 3.3.0 and later require Python ≥ 3.12; 3.2.0 (10 February 2026) is the newest release supporting 3.11. | PyPI `requires_python` per release. | Haute supports 3.11–3.13 and its Databricks Model Serving deployment builds Python 3.11.11. The owner chose to cap XGBoost `<3.3` and keep Python 3.11. |
| The full `xgboost` 3.2.0 wheel is 131.7 MB on Linux x86_64 and depends on `nvidia-nccl-cu12` on Linux; 3.4.1 depends on `nvidia-nccl-cu13` (305 MB). `xgboost-cpu` 3.2.0 is 5.6 MB (Linux x86_64) and 2.1 MB (Windows) with no NVIDIA dependency. | PyPI file sizes and `requires_dist`; `uv pip list` in the Linux probe env shows no `nvidia-*` packages. | Use `xgboost-cpu` on Linux and Windows. |
| `xgboost-cpu` publishes no macOS wheel; the macOS `xgboost` wheel is 2.3–2.5 MB and has no NVIDIA dependency. | PyPI file lists. | macOS takes `xgboost` through an environment marker. Both distributions install the same `xgboost` import package, so an environment must never hold both; another dependency that requires `xgboost` on Linux/Windows would conflict. |
| The macOS XGBoost 3.2.0 and LightGBM 4.7.0 wheels bundle no OpenMP runtime; both native libraries load `@rpath/libomp.dylib`. | `mac_wheels` inspection of `libxgboost.dylib` and `lib_lightgbm.dylib`. | macOS installs need Homebrew `libomp`; install docs, the macOS CI job and any macOS deploy image must provide it. |
| `interpret-core` 0.7.8 is a 15.6 MB pure-Python wheel with its native booster included, requiring numpy, pandas, scikit-learn ≥ 1.6 and joblib. | PyPI metadata. | No extra native prerequisite. |
| CatBoost 1.2.10 rejects `loss_function="Gamma"` ("Gamma loss is not supported"). | Direct fit in Haute's environment. | CatBoost's descriptor rejects the new `Gamma` Haute loss. |

## XGBoost 3.2.0 (native `Booster`, `DMatrix`, `hist`)

| Question | Result |
|---|---|
| Early stopping round convention | `best_iteration` is zero-based (17); the booster keeps the extra rounds (`num_boosted_rounds()` 23). Scoring must use `iteration_range=(0, best_iteration + 1)` or a trimmed model. |
| Trimmed persistence | `booster[: best + 1]` saved as UBJSON and reloaded has 18 rounds and bit-identical predictions. |
| Offset (`base_margin`) | With `base_margin`, XGBoost does not add its fitted `base_score`: margin(with) − margin(without) − log(exposure) is the constant −log(`base_score`). A model trained with an offset must always be scored with one. The response is exp(margin). |
| Contributions | `pred_contribs` sums to the margin including `base_margin` (the bias column carries it) within 3.4e−7 relative: float32 accumulation, consistent with the plan's 1e−5 bound. |
| Objective in the artifact | The saved config holds `count:poisson`, so the adapter can check a model's objective against its contract. |
| Category order | Scoring a frame whose categorical dtype lists the same levels in another order gives different predictions *for the probe's booster, which is sliced to its best round*. A follow-up check during MOD-F02 found that XGBoost 3.2 re-maps pandas categories by name for an unsliced booster (in memory or reloaded), but `booster[:k]` drops that stored mapping and reads codes positionally. Haute saves early-stopped fits sliced, so codes must come from the contract's stored level order. |
| Unseen category | No error; a prediction is returned. Haute's domain check must run first. |
| Unknown parameter | Only a native `WARNING … Parameters: { "not_a_param" } are not used.`; training succeeds. Haute's allowlist must reject it first. |
| No validation | 17 configured rounds give 17 rounds. |
| Objectives | `reg:squarederror`, `reg:absoluteerror`, `reg:gamma`, `reg:tweedie` (+ `tweedie_variance_power`), `binary:logistic` all train. |

## LightGBM 4.7.0 (native `Dataset`, `train`)

| Question | Result |
|---|---|
| Early stopping round convention | `best_iteration` is a one-based count (18) and equals `current_iteration()`. |
| Offset (`init_score`) | `predict` ignores `init_score`: the response equals exp(raw score) without the offset. The scoring adapter must add log(exposure) to the raw score before the inverse link. |
| Contributions | `pred_contrib` sums to the raw score excluding `init_score` (6.5e−16 relative). |
| Persistence | `save_model(num_iteration=best)` then reload has 18 trees and bit-identical raw scores. The model text records `objective=poisson` and a `pandas_categorical` block. |
| Category order | Reordered categorical dtype gives identical predictions: LightGBM re-maps pandas categories by name from the stored block. |
| Unseen category | No error; a raw score is returned. Haute's domain check must run first. |
| Native exhaustion | A constant feature with 17 configured rounds produces 1 tree (`current_iteration()` 1): the `native_exhaustion` case is real. |
| No validation | 17 configured rounds give 17 on the fixture. |
| Conflicting aliases | `num_leaves=8` with `num_leaf=4` trains silently with `num_leaves` 8 and no warning. Haute must reject aliases itself. |
| Objectives | `regression`, `regression_l1`, `gamma`, `tweedie`, `binary` all train. |

## InterpretML EBM 0.7.8

| Question | Result |
|---|---|
| API seams | `fit(X, y, sample_weight=None, bags=None, init_score=None)`, `predict(X, init_score=None)`; `monotone_constraints` exists; `to_json` exists but there is no `from_json`. `bags` is shaped `(n_samples, outer_bags)`. |
| Offset | `predict(X, init_score=log(exposure))` equals `predict(X) * exposure` exactly for `poisson_deviance`. `eval_terms` plus `intercept_` reproduces the log prediction (2.6e−16). |
| **`bags` leakage (decision 1 gate)** | With early stopping disabled and `max_rounds=60`, perturbing only validation (`-1` bag) targets leaves interaction selection and term scores identical but **changes the intercept and every prediction for all five objectives** (`rmse`, `poisson_deviance`, `gamma_deviance`, `tweedie_deviance`, `log_loss`). **The gate fails for every objective.** |
| `bags` early stopping | Runs and stops (`best_iteration_` `[[1041]]` against `max_rounds=5000`), shaped `(outer_bags, stages)`. |
| Persistence | A `joblib` dump reloads bit-identically. The payload references only `ExplainableBoostingRegressor` or `ExplainableBoostingClassifier` plus joblib/NumPy scaffolding already allowlisted (`NumpyArrayWrapper`, `dtype`, `ndarray`, `numpy._core.multiarray.scalar`). |
| Restricted loader | Haute's unchanged `safe_joblib_load` blocks the EBM class. Adding only the two exact classes to `_ALLOWED_PICKLE_CLASSES` loads regressor and classifier bit-identically, and a crafted `os.system` payload is still blocked. State restoration uses scikit-learn's `BaseEstimator.__getstate__`/`__setstate__` (no EBM-specific reduce hooks); fitted attributes are only builtins, `numpy.float64` and `ndarray`. **The persistence gate passes.** |

## Gate outcomes

1. **Versions and distributions — pass.** The engines resolve and run with
   Haute's dependencies on Python 3.11 and 3.13 on Windows and Linux. XGBoost is
   capped `<3.3` to keep Python 3.11 (owner decision, 23 September 2026). macOS runtime evidence is outstanding and must come from CI with
   `libomp`.
2. **EBM persistence — pass.** `.ebm` is a joblib file loaded by
   `safe_joblib_load` with exactly two new allowlist classes.
3. **EBM `bags` stopping — fails for every objective.** Under decision 1's
   agreed fallback, EBM always fits on training rows only with an explicit
   `max_rounds` and `outer_bags=1`; EBM early stopping is not offered in the
   first release.
4. **Native baseline, category and stopping behaviour — pass, with contract
   consequences.** The plan's adapter rules are confirmed: XGBoost offsets
   always go through `base_margin` and scoring uses a trimmed model; LightGBM
   scoring adds the baseline itself; category codes come from the contract for
   XGBoost; Haute validates domains, parameters and aliases before any engine
   sees them.
