# M08 — Two paths, two truths: MLflow-button vs auto-log drift, and export-script config drift

**Severity: MEDIUM (two findings) + LOW (one finding)**
**Theme: `_train_config.py` exists precisely because dual code paths drifted before (its docstring documents two historical silent-wrongness bugs). Two fresh instances of the same disease live just outside the SSOT boundary.**

---

## Finding M08-1 (MEDIUM): the "Log to MLflow" button logs 4 params; auto-logging logs everything

### Evidence
- **Auto-log path** (config has `mlflow_experiment`): `TrainingJob._log_to_mlflow`
  (`src/haute/modelling/_training_job.py:1562-1581`) logs `algorithm, task, target, weight,
  split_strategy, validation_size, holdout_size` **plus `param_{k}` for every hyperparameter**.
- **Button path**: `routes/modelling.py:373-378` (`mlflow_log`) logs only
  `{algorithm, task, target, weight}` — no split config, **no hyperparameters**, no
  loss_function/variance_power/offset/monotone constraints (neither path logs those last
  four, which compounds M02-2's "effective loss is recorded nowhere").

### DS impact
Runs logged via the button are not comparable with auto-logged runs, and a
button-logged run cannot answer "what learning rate / depth / loss produced this?" — the core
question MLflow exists to answer. Silent metadata loss, invisible until someone opens the
MLflow UI weeks later expecting reproducibility.

### Fix
Extract one `build_mlflow_params(config, split_config, params, effective_loss, ...)` builder
(natural home: `_train_config.py`, next to the kwargs SSOT) and use it from **both**
`_log_to_mlflow` and `routes/modelling.py:mlflow_log`. Include: split fields, all
hyperparameters (`param_*`), `loss_function` (effective, per M02-2), `variance_power`,
`offset`, `monotone_constraints` (compact), `row_limit` when it bound.

### TDD
- Param-set equality test: log the same completed job via both paths against a temp MLflow
  store; assert identical param dicts (fails today: 4 vs 10+ keys).
- New params (`effective loss`, offset) asserted present.

---

## Finding M08-2 (MEDIUM): the export endpoint skips the upstream categorical-levels merge that live training applies

### Evidence
- Live training merges categorical-level declarations from the node **and every upstream
  node** before building kwargs: `_declared_categorical_levels_for_training`
  (`src/haute/routes/_train_service.py:276-289`), applied at `start()`
  (`:424-430`) — conflicts raise `FeatureMismatchError`.
- The export endpoint uses the node config as-is: `routes/modelling.py:398-418` builds
  `config = dict(node.data.config)` and calls `generate_training_script` — **no merge**.
- Consequence: an exported script trains a model whose feature contract (and score-time
  categorical-domain validation) omits upstream-declared levels — the exact "same config,
  different model artifacts" drift `_train_config.py` was built to prevent. (The kwargs SSOT
  itself is clean; the drift is in the route-level config assembly that feeds it.)
- Same asymmetry for `output_dir`: live training defaults it to `<pipeline_dir>/outputs`
  (`_train_service.py:512-520`); the exported script falls back to TrainingJob's bare
  `"outputs"` relative to wherever the script runs.

### Fix
Extract the config-assembly step (categorical-levels merge + output_dir default) into a
shared helper used by both `TrainService.start()` and the export route, so
`generate_training_script` receives exactly the config live training would use.

### TDD
- Graph with an upstream node declaring `categorical_levels`: exported script string must
  contain those levels in `categorical_levels=` (fails today).
- Exported `output_dir` equals the live-training default for the same graph.

---

## Finding M08-3 (LOW): exported scripts silently train on different data than the UI run did

### Evidence
- Live training applies the RAM-derived or user `row_limit` via a seeded downsample
  (`_train_service.py:74-88, 887-889`) — deterministic, order-preserving, seed 42.
- `generate_training_script` (`src/haute/modelling/_export.py`) renders no row limit and the
  script reads the full parquet.

### Assessment
Training the exported script on **all** rows is arguably the *better* behaviour (the
downsample exists only to protect the interactive machine) — but the difference is silent.
A DS validating "the exported script reproduces my UI model" will get different metrics and
not know why.

### Fix
Comment, don't change semantics: when the UI run was row-limited, emit a header comment in
the script — `# NOTE: the in-app run trained on a 2,000,000-row seeded sample (row_limit);
this script trains on the full dataset.` Requires passing the effective row limit into the
export request (the frontend already has the estimate).

### TDD
- Export after a row-limited run contains the NOTE line with the exact row count; export
  without a limit contains no NOTE.

---

## Related, already tracked by the June 2026 audit (do not duplicate — fix under their IDs)
- MLflow logging **aborts** for models with Date/Datetime/Decimal/Time/Duration feature
  dtypes (P1-subsystem; `_signature.py:14-27,70-73` + `_polars_dtype_name` fallthrough).
- Databricks model registration uses the source filename basename instead of the logged
  `artifact_path` → registration fails for CatBoost models (P3-exhaustive;
  `_mlflow_log.py:376-381` vs `:457-476`).
- RustyStats/pyfunc models logged with `loader_module='haute._mlflow_io'` which exposes no
  `_load_pyfunc` entry point (P1-subsystem; `_mlflow_log.py:471-476`).
- `test_rows`/`test_mb` naming actually carries VALIDATION counts (P4-apidx; pinned in
  schemas + tests, rename is a coordinated API change).
