# Model scoring roadmap

## Scope

Where a scored model comes from, which model families load, and how a model
Haute did not train is described well enough to score safely. This covers the
Model Scoring node (`modelScore`). Current behaviour is specified in
[the MLflow model registry specification](../mlflow-model-registry/high-level.md)
and its [low-level specification](../mlflow-model-registry/low-level.md), with
the editors in
[the frontend node editors specification](../frontend-node-editors/low-level.md)
and bundling in [the deploy specification](../deploy/high-level.md). Training,
run logging and the optimiser's own artifacts are out of scope.

Model Scoring loads every family Haute trains (CatBoost `.cbm`, RustyStats
GLM `.rsglm`, XGBoost `.ubj`, LightGBM `.lgbm`, EBM `.ebm`) from an experiment
run, a registered model or a model file in the project, and MLflow pyfunc
models from a run or the registry. Every loaded model is bound to its feature
contract, which declares the offset a CatBoost file cannot. Model Scoring is
the one way to score a model; Load File loads objects for custom code
(pickle, joblib, JSON).

The target is one node that scores a model. Model Scoring finds the model in a
registry, a run or a project file, for every family Haute trains, through one
model source and one family registry, so that a further family is a single
registration. Registry and run pyfunc models already work. The file-source
delivery adds native files under a declared contract, then local pyfunc
packages with an explicit executable-import trust policy, then ONNX if there
is demand. Pickled estimators stay with Load File's restricted unpickler, whose
allowlist names no XGBoost or LightGBM class. A pyfunc signature describes inputs; it does
not make the package's pickles or Python code safe to execute.

Every package keeps three rules. The same file, contract and checks apply in
the preview, a standalone run and a deployed bundle. A model whose inputs,
categorical encoding, missing-value policy, task, response transform or offset
cannot be established is refused, never scored on a guess. No unrecognised
file defaults to a family.

## Priorities

| Package | State | Priority | Outcome |
|---|---|---:|---|
| MSC-05 | Planned | P3 | Model Scoring scores an MLflow pyfunc model saved in the project. |
| MSC-06 | Planned | P3 | XGBoost and LightGBM files saved under their libraries' generic suffixes score, and an XGBoost model's missing-value sentinel can be declared. |
| MSC-07 | Deferred | P3 | ONNX models score in Model Scoring. |
| MSC-09 | Planned | P3 | Loader errors keep their meaning: a Haute refusal is not retried as corruption and a CatBoost metadata failure is not swallowed. |

## Planned improvements

`MSC-05` and `MSC-06` each build on the file source on their own. `MSC-09`
comes from the
[25 October 2026 codebase review](codebase-review-2026-10-25.md).
Each family they add is one registration in the model family registry
(`src/haute/_model_flavors.py`).

### MSC-05 — Model Scoring scores an MLflow pyfunc model saved in the project
**Why:** MLflow's pyfunc format is how most Python models trained outside Haute
travel (scikit-learn, statsmodels, a team's own wrapper class), with a declared
input signature and their own requirements. Model Scoring already scores a
pyfunc model from a run or a registry, but `load_local_model` refuses a local
pyfunc directory, so a pyfunc model exported into the project cannot be
scored without an MLflow server.

**Plan:** A local pyfunc package is an explicitly trusted executable import.
Its `MLmodel` file selects the directory, but browsing and inspection only
parse metadata and hash files: they never import the package, deserialize its
Python model or call `load_context`. The editor offers an explicit trust action
which records the inspected package's SHA-256 content digest as
`trusted_model_digest` in the canonical Model Scoring config. The same field
is usable without the GUI. A missing or mismatched trust digest refuses
loading, scoring and bundling before any package code executes, including on a warm cache. Trust
is for those bytes, not a path or a global allow-pickle switch. Replacing any
package content requires a new explicit trust decision; inspection never
grants it automatically. Compute and verify the digest of a private immutable
snapshot, then load or bundle those same bytes; a stat-only cache hit is not
proof of trust. A concurrent replacement cannot execute different bytes after
verification. This policy covers local file sources and their deployed
bundles; it does not broaden Load File's restricted unpickler or silently
change existing MLflow-source trust behaviour.

The input signature must describe supported named tabular columns; an absent
or unsupported signature is refused. Offsets come only from a feature
contract, as for a registry pyfunc. The package digest
covers sorted relative file names and their bytes, including `MLmodel`, code,
pickles, data and requirement files; added and removed files change it too.
All package members and local model, artifact and code paths declared by
`MLmodel` must remain within the package after symlink resolution. An escaping
reference is refused before loading. Once trusted, the package's executed
Python has the same process privileges as other trusted project code.
Model-object and execution-cache identity include that package identity and
the resolved feature contract, extending the file source's preview, trace and
snapshot freshness rules. The trust check precedes reuse of cached outputs.

Deploy bundles the verified directory and records its approved digest,
checking it again before the bundle loads the package. It checks the model's
recorded requirements against the image, including version constraints, so a
requirement the image does not satisfy fails the deploy by name rather than
at serving time. The trace states that no explanation is available for a
pyfunc model. Update the sandbox-security high- and low-level specifications
with this executable-import exception and its enforcement points, alongside
the MLflow model registry, node editor, caching and deploy specifications,
before implementation.

**Acceptance:** Explicitly trusted pyfunc regressor and classifier packages
score in the preview, in a standalone run and in a deployed bundle, with
predictions equal to the model's own `predict`. A harmless fixture records
whether its deserialization hook or `load_context` ran: browsing and
inspection leave both untouched, and absent or mismatched trust refuses
scoring and bundling without running either. Verify this with warm model and
output caches too. After an approved package changes, the old digest fails;
after explicit trust of the new digest, predictions use the new content.
Cover an edit below a nested directory and file addition/removal, not only an
`MLmodel` edit. A contract-only change invalidates cached results as in
for a native model file. A package replaced during verification cannot execute unverified
bytes, and a modified deployed package fails before loading. An escaping file
reference or symlink, an absent or unsupported signature, and a requirement
missing from or incompatible with the image are each refused with the reason.

**Dependencies:** None. It extends the file source
(`src/haute/_model_source.py::FileModelSource`).

**Evidence:** `src/haute/_mlflow_io.py::load_local_model`;
`src/haute/_mlflow_io.py::_wrap_pyfunc`;
`src/haute/_mlflow_io.py::_extract_pyfunc_features`;
`specs/sandbox-security/high-level.md` (trusted code and untrusted artifacts);
`src/haute/deploy/_container.py` (`_ARTIFACT_EXT_TO_DEPS`).

### MSC-06 — Generic suffixes and a declared missing-value sentinel
**Why:** XGBoost, LightGBM and t-boost files trained outside Haute already score
from what their libraries record, with no contract and no offset (26 October
2026). Two gaps remain. The libraries save to generic extensions (XGBoost to
`.json` or `.ubj`, LightGBM usually to `.txt`), and Haute recognises a model by
its suffix, so an analyst must re-save an XGBoost JSON model as `.ubj` (XGBoost
picks its parser by extension) or rename LightGBM text to `.lgbm`. And XGBoost
records no missing-value sentinel: a model trained with `missing=-999` is scored
with NaN as its only missing value, so a `-999` in the input is read as a number.

**Plan:** Let the node record the family the analyst chose, so a `.json` or
`.txt` file loads as that library's model once loading checks that it really
is one; a `.ubj` file carrying Haute's record still loads as a Haute model. An
optional contract field declares the XGBoost missing-value policy used to
construct its training DMatrix (NaN only, or NaN plus a finite numeric
sentinel), applied when building prediction and contribution matrices.

**Acceptance:** An XGBoost model saved as `.json` and a LightGBM model saved as
`.txt` score from a project file and an MLflow run, matching each library's own
predictions; a file that is not the chosen library's model is refused by name.
An XGBoost model trained with `missing=-999` and a declared sentinel scores
sentinel, null and ordinary rows as its native reference does.

**Dependencies:** None.

**Evidence:** `src/haute/modelling/_xgboost.py::XGBoostModel`;
`src/haute/modelling/_lightgbm.py::LightGBMModel`;
`src/haute/_model_flavors.py::family_for_artifact`.

### MSC-07 — ONNX models score in Model Scoring
**Why:** ONNX is the common export format across frameworks, and the
container's dependency map already names `.onnx`, yet nothing loads one. The package is deferred because `onnxruntime` is not a Haute
dependency, no user has asked for it, and pyfunc (`MSC-05`) covers Python
models first.

**Plan:** If reopened, register an ONNX family in the model family registry: input names
from the graph, a required feature contract for task and offset, `onnxruntime` as an optional extra, and probability outputs mapped to
the binary positive-class rule.

**Acceptance:** An ONNX regressor and classifier converted from another
framework score with predictions equal to the source model within float
tolerance. Without the extra installed, choosing an ONNX file fails naming the
extra.

**Dependencies:** Demand for ONNX.

**Evidence:** `src/haute/deploy/_container.py` (`_ARTIFACT_EXT_TO_DEPS`);
`frontend/src/panels/editors/ModelScoreEditor.tsx`.

### MSC-09 — Loader errors keep their meaning
**Why:** `_load_with_bounded_retry` re-raises only `AttributeError`,
`TypeError`, `KeyError`, `ConfigError` and `ArtifactVersionMismatchError`, so a
readable model the loader refuses with `HauteValidationError` or
`FeatureMismatchError` (a `.ubj`, `.lgbm` or `.tboost` without Haute's record,
a contract whose hash fails) is treated as a corrupt download: deleted,
re-downloaded and reported as persistently corrupt with the real reason only in
the last error. `catboost_class_labels` swallows a failed metadata read and
falls back to `classes_`, against the module's rule and in a branch
`_wrap_catboost` already makes dead.

**Plan:** Re-raise every `HauteError` and `HauteValidationError` and retry only
on non-Haute exceptions; remove the metadata `try/except`.

**Acceptance:** A `.ubj` without Haute's record fails once with its own message
and no re-download; the dead branch is gone.

**Dependencies:** None.

**Evidence:** `src/haute/_mlflow_io.py::_load_with_bounded_retry`;
`src/haute/_mlflow_io.py::catboost_class_labels`.
