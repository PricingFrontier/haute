# MOD-F05 CPU release check — 23 September 2026

Evidence that the XGBoost, LightGBM and EBM families are complete on CPU, reviewed
against every acceptance row of the model-family expansion plan's release
acceptance (the plan is delivered and retired with the modelling roadmap;
git history keeps its text).
This is a dated evidence record, not a specification: the behaviour is specified in
the owning component specifications (`specs/modelling`, `specs/sandbox-security`,
`specs/frontend-modelling-optimiser-ui`, `specs/codegen`, `specs/deploy`,
`specs/build-and-distribution`).

Delivered as MOD-F00 (probes, 9275d7f3), MOD-F01 (shared seams, b9a32c6a), MOD-F02
(XGBoost, bdd1483e), MOD-F03 (LightGBM, 60c80f92) and MOD-F04 (EBM, 62c9acc8), each
reviewed by Codex until approved.

## Acceptance rows

| Acceptance area | Evidence |
|---|---|
| Config and capabilities | `tests/test_algorithm_descriptors.py` (descriptors, aliases, reserved keys, the capability fixture equals the serialised descriptors); per-family config rejection tests in `tests/test_xgboost_family.py`, `tests/test_lightgbm_family.py` (alias table drift test against the installed release, MAE with monotone constraints) and `tests/test_ebm_family.py` (required `max_rounds`, interaction shapes, monotone interactions, MAE and feature weights refused); the frontend mirrors in `frontend/src/utils/__tests__/trainingObjective.test.ts` and `frontend/src/panels/__tests__/ModellingConfig.test.tsx`. |
| Shared preparation | Contract-order categorical encoding, reordered categories, a batch holding some levels, nulls as missing, a literal `"__missing__"` level, unseen and empty-string categories failing: in each family file. |
| Binary contract | `tests/test_model_family_acceptance.py` for CatBoost, XGBoost, LightGBM and EBM: Boolean target positive at `True`, two-string target without `positive_class` and a three-class target failing before fitting, labels equal to `prediction_proba > 0.5` on every scoring path; `tests/test_binary_classification_contract.py` holds the exactly-0.5 case and CatBoost's metadata. |
| Native fit | Every loss per family trains weighted and reloads with its native objective; weights and offsets are proven against independently fitted native models (XGBoost and LightGBM weights, LightGBM and EBM `init_score`, EBM weights); fit evidence records the thread allotment (EBM records its single thread). |
| Scoring parity | `tests/test_model_family_acceptance.py`: for every family, eager and batched `score_frame`, the shared MLflow pyfunc package, the deployed graph (`score_graph` over the model and contract remapped as the bundler writes them) and the Model Score runtime that generated pipeline code calls (`ModelScorer` over a local MLflow run) agree, for regression with an exposure offset (doubling exposure doubles the prediction) and binary classification; each family file checks the native library as an independent oracle and bit-identical save and reload. `test_the_exported_training_script_trains_the_same_model` executes each family's generated training script and matches the canvas-config fit's predictions and fit evidence exactly. Writing this found and fixed a deploy gap: a classification Model Score whose contract records Haute class labels now declares its `<output>_proba` column, so a deployed output can map the probability, while a prediction-only classifier promises none (`test_a_prediction_only_classifier_deploys_its_labels`). |
| Stopping and refit | Early-stopped XGBoost and LightGBM boosters are trimmed to the selected round and the refit reuses `best_iteration + 1`; the selected round, trimmed round count and predictions are checked against an independent native early-stopped fit for both families (`test_early_stopping_selects_what_native_xgboost_selects`, `test_fitting_with_the_offset_as_init_score_matches_an_independent_native_fit`), so an off-by-one conversion fails; disabled early stopping refits with every fitted round (both families); a constant-feature LightGBM fit records `native_exhaustion`; EBM never stops early, records `term_update_steps`, and its selection fits see only training-partition rows while the refit sees development rows (row counts spied at the estimator). A fixture engineered to select exactly three rounds was not added: the native oracles above catch the conversions it would guard. |
| Evaluation leakage | EBM row-count test above; categorical levels come from the fit's own frame (`fit_categorical_levels`), so a final-test-only level is never learned and scoring it fails loudly. |
| Explanations | Bias plus contributions reconstructs the margin and its inverse link the served value, with the offset, after reload, for every family; an EBM interaction stays one two-feature term; a wrong contribution fails the explanation check. |
| Artifacts and trust | Contract identity checks per family (another loss or algorithm fails); EBM loads only under its contract and exact `interpret-core` version, before unpickling; a crafted pickle payload in an `.ebm` is blocked; MLflow and deployed caches key an EBM by its contract; publication validates the staged evaluation and tuning artifacts for EBM runs. |
| Lifecycle and UI | `test_native_training_lifecycle_keeps_the_last_good_model` runs every family through the real training service with a real fit: train, cancel before publication, fail in the native fit, and train again; the cancelled and failed runs publish nothing and the last completed run's model still loads and scores from its restorable result. `test_a_native_fit_stops_at_the_first_progress_report_after_cancellation` shows a cancellation raised from the progress callback stops XGBoost and LightGBM mid-boosting and EBM before its fit, saving nothing. Browser scenarios in `frontend/e2e/core-flows.spec.ts`: XGBoost and LightGBM train and save their model files (`.ubj`, `.lgbm`, with the feature contract); EBM chooses an interaction, trains, and shows the interaction surface; CatBoost and GLM unchanged and passing. Registered as workflow scenario W08-S03 in `tests/workflow_coverage.toml`. |
| Resource and package | Benchmarks below; repeated loads do not grow memory; `scripts/package_smoke_check.py` imports every engine at its pinned line; CI installs `libomp` on macOS and runs the three family suites on Windows and macOS (`platform-smoke`). |

## Benchmarks

`mod-f05-release-benchmarks.py` fits each family through its Haute adapter;
[`mod-f05-release-benchmarks.json`](mod-f05-release-benchmarks.json) holds the raw run
(Windows, Python 3.11.13, 22 threads). Peak memory is resident memory above the
pre-fit baseline.

| Shape | Family | Fit (s) | Score (s) | Peak memory (MB) | Model file (MB) |
|---|---|---:|---:|---:|---:|
| Wide: 50,000 × 200 numeric, 200 rounds (EBM 300) | XGBoost | 8.4 | 0.16 | 366 | 0.9 |
| | LightGBM | 3.8 | 0.16 | 226 | 0.6 |
| | EBM | 22.9 | 0.11 | 168 | 8.0 |
| Categorical: 100,000 × 30 (20 categorical, one with 1,000 levels), Poisson with offset | XGBoost | 3.1 | 0.81 | 64 | 5.3 |
| | LightGBM | 4.6 | 0.86 | 77 | 1.1 |
| | EBM | 2.5 | 0.35 | 22 | 0.5 |
| EBM interactions: 50,000 × 25, 300 rounds | 0 pairs | 1.3 | 0.15 | 14 | 0.5 |
| | 10 pairs | 10.2 | 0.14 | 8 | 3.3 |
| | 40 pairs | 30.8 | 0.21 | 31 | 14.1 |

Thirty repeated loads grew resident memory by at most 0.4 MB for any family.

## EBM format limits

- An `.ebm` is a joblib dump of the native estimator; it holds no Haute metadata and
  loads only beside (or with) its feature contract.
- It loads only under the exact `interpret-core` version the contract records, so a
  minor or patch upgrade of `interpret-core` requires retraining.
- File size grows with bins and interactions: about 0.04 MB per wide continuous main
  effect at the default 1,024 bins, and about 0.33 MB per pairwise interaction at the
  default 64 interaction bins. The results payload carries every term's scores, so an
  EBM with dozens of interactions sends a correspondingly larger training result.
- Fit time grows with the interaction count (40 pairs took about 24 times the
  main-effects-only fit above).

## Platforms and packaging

- Core dependencies: `xgboost-cpu>=3.2,<3.3` (`xgboost` on macOS), `lightgbm>=4.7,<5`,
  `interpret-core>=0.7.8,<0.8`; Python 3.11 is kept by the XGBoost cap.
- macOS needs Homebrew `libomp` for XGBoost and LightGBM: documented in the install
  guide and the Model Training guide, and installed in the macOS CI jobs.
- Local runs covered Windows (this report) and Linux (the MOD-F00 probes); macOS
  runtime evidence comes from the CI `platform-smoke` and `init-smoke` jobs once this
  branch runs in CI.

## Not in this release

GPU training for XGBoost and LightGBM (MOD-F06, deferred), EBM early stopping or outer
bagging, multiclass classification, and model continuation. None is selectable.
