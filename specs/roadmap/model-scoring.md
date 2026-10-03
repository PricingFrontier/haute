# Model scoring roadmap

## Scope

Where a scored model comes from, which model families load, and how a model
Haute did not train is described well enough to score safely. This covers the
Model Scoring node (`modelScore`) and the model-loading part of the Load File
node (`externalFile`). Current behaviour is specified in
[the MLflow model registry specification](../mlflow-model-registry/high-level.md)
and its [low-level specification](../mlflow-model-registry/low-level.md), with
the editors in
[the frontend node editors specification](../frontend-node-editors/low-level.md)
and bundling in [the deploy specification](../deploy/high-level.md). Training,
run logging and the optimiser's own artifacts are out of scope.

Today Model Scoring loads every family Haute trains (CatBoost `.cbm`,
RustyStats GLM `.rsglm`, XGBoost `.ubj`, LightGBM `.lgbm`, EBM `.ebm`) and
MLflow pyfunc models, but only from MLflow: an experiment run or a registered
model. A model file in the project, whether written by Model Training's
**Save model to file** or handed over by a colleague, can only be used through
Load File. Load File reads CatBoost alone among those families, as a raw
object for hand-written `obj.predict(...)` code, with none of the feature,
categorical, offset or task checks Model Scoring applies. The deploy path
already scores every family from a local file, so the gap is in the node and
its consumers, not the loaders.

The target is one node that scores a model. Model Scoring finds the model in a
registry, a run or a project file, for every family Haute trains, through one
model source and one family registry, so that a further family is a single
registration. Load File goes back to loading objects for custom code (pickle,
joblib, JSON). Registry and run pyfunc models already work. The file-source
delivery adds native files under a declared contract, then local pyfunc
packages with an explicit executable-import trust policy, then ONNX if there
is demand. Pickled estimators stay with Load File's restricted unpickler
(`BUG-04` owns its allowlist). A pyfunc signature describes inputs; it does
not make the package's pickles or Python code safe to execute.

Every package keeps three rules. The same file, contract and checks apply in
the preview, a standalone run and a deployed bundle. A model whose inputs,
categorical encoding, missing-value policy, task, response transform or offset
cannot be established is refused, never scored on a guess. No unrecognised
file defaults to a family.

## Priorities

| Package | State | Priority | Outcome |
|---|---|---:|---|
| MSC-02 | Planned | P2 | A model family is registered in one place, and an unrecognised file is refused by name. |
| MSC-03 | Planned | P2 | Model Scoring scores a model file in the project, for every family Haute trains. |
| MSC-04 | Planned | P2 | Load File no longer loads CatBoost models; Model Scoring is the one way to score a model. |
| MSC-05 | Planned | P3 | Model Scoring scores an MLflow pyfunc model saved in the project. |
| MSC-06 | Planned | P2 | An XGBoost or LightGBM model trained outside Haute scores under a declared contract. |
| MSC-07 | Deferred | P3 | ONNX models score in Model Scoring. |

## Planned improvements

The order is `MSC-02`, `MSC-03`, then `MSC-04`; `MSC-05` and `MSC-06` each
follow `MSC-03` on their own.

### MSC-02 — A model family is registered in one place
**Why:** What Haute knows about each family is spread over sites that must
agree by hand: the flavor literal and native-wrapper suffixes, the extension
chains in `load_local_model` and `_flavor_from_artifact`, run artifact
discovery, the algorithm-to-flavor map used to verify a contract, predict-frame
preparation, the scorer's offset dispatch, the explanation dispatch, the
container's extension-to-package map, and the frontend's export extensions.
The chains already disagree at the edges: `_flavor_from_artifact` maps any
unrecognised extension to pyfunc, so an unsupported file fails as a confusing
pyfunc load rather than by name, and discovery's not-found message still lists
only `.cbm`, `.rsglm` and a pyfunc directory although it searches every native
suffix. A file source (`MSC-03`) and each later family would add one more edit
to every site.

**Plan:** Define one adapter per family in a leaf module: its flavor name, file
suffixes, which scoring semantics the particular artifact records and which
need a feature contract, its loader, its predict-frame preparation, supported
response transforms, how it reads its offset, its explanation method (or none),
and the distributions its runtime needs. Contract requirements depend on the
artifact's metadata, not just its family: a CatBoost suffix alone does not
establish whether a training baseline must be supplied. A registry keyed by
flavor and by suffix replaces the chains and maps above. An unrecognised
suffix raises a `ConfigError` naming the supported ones, and only a directory holding an
`MLmodel` file is pyfunc. A suffix several families share (`MSC-06`'s external
XGBoost and LightGBM files) resolves only through a family the node names, and
the family's loader confirms the file is its own. Discovery and its message
read the registry. The frontend reads the suffix list from a generated JSON
file that a parity test holds equal to the registry, as it does for the
algorithm capabilities.

**Acceptance:** A test registers a stub family, and it loads from a local file
and from a run artifact, appears in the suffix list and maps to its
distributions with no other edit. An unrecognised suffix fails naming the
supported suffixes. The frontend parity test pins the generated list.

**Dependencies:** None. It lands before `MSC-03`, so the file source reads the
registry.

**Evidence:** `src/haute/_model_flavors.py`;
`src/haute/_mlflow_io.py::load_local_model`;
`src/haute/_mlflow_io.py::_flavor_from_artifact`;
`src/haute/_mlflow_io.py::_find_model_artifact`;
`src/haute/_mlflow_io.py::verify_contract_identity`;
`src/haute/_mlflow_io.py::_prepare_predict_frame`;
`src/haute/_model_explainability.py::explain_model_score_from_config`;
`src/haute/deploy/_container.py` (`_ARTIFACT_EXT_TO_DEPS`);
`frontend/src/panels/modelling/modelExport.ts`.

### MSC-03 — Model Scoring scores a model file in the project
**Why:** **Save model to file** writes a native model and its feature contract
into the project, and a deployed bundle already scores every family from such
a file through `load_local_model`. The Model Scoring node reads only MLflow,
so scoring a saved file in a pipeline means Load File. Only CatBoost loads
there, as a raw object without feature order, categorical domain, offset or
task checks, and an XGBoost, LightGBM, EBM or GLM file cannot be scored in a
pipeline at all without an MLflow run.

**Plan:** Add a third model source, `sourceType: "file"`, with `model_path`
(project-relative, through the sandbox's path check) and the existing
`feature_contract_path`. The contract defaults to the one saved beside the
file, and unresolved required scoring semantics fail without a contract.
For CatBoost, missing Haute offset metadata never proves that training used
no baseline. A file with no recorded offset needs a contract that explicitly
declares either an offset column and its transform or no offset; conflicting
file metadata and contract declarations fail. Haute's saved sidecar supplies that
declaration for its own models. External CatBoost files need the same
declaration, supplied as a contract in this package. Apply this check through
the source seam to run and registered sources too, documenting the tightening
for previously accepted files whose offset was unknown. A missing required
offset input fails before prediction.

Loading goes through the source seam to `load_local_model`, under the same
in-memory model cache keyed on the model and contract bytes, which the deploy
scorer's stat-gated cache folds into. The resolved model and contract also
become runtime inputs to execution-cache identity, including a sibling
contract discovered without `feature_contract_path` in the config. Reuse the
existing file-freshness and content-signature machinery. Model replacement,
contract-only replacement, a changed sibling-contract selection, and deletion
must invalidate preview, trace and downstream node-output snapshot freshness
before cached results can be reused. A missing required file raises even when
a model object or scored frame is cached; an unchanged path is not freshness
evidence. Existing explicitly pinned snapshot generations keep their fixed
data semantics; they must not be reported as current model execution.
The other consumers follow from the parsed model source
(`src/haute/_model_source.py`): column planning, the trace explanation,
`score_from_config`, cache
field classification, config recovery (where `sourceType` gains `file`), and
the deploy bundler, which bundles the file and its contract under the node.
In the editor, **MODEL SOURCE** gains **Model file**, with a file browser
limited to the registry's suffixes. An inspection endpoint reports the chosen
file's family, task, features, offset and contract, so **TASK** is shown
read-only when recorded, as it is for a run. Update the MLflow model registry
specification's sources and failure model, the feature-contract, caching,
execution-engine, node editor and deploy specifications, and the Model
Scoring and Load File pages first.

**Acceptance:** For each family Haute trains, a model saved with **Save model
to file** scores through a file-sourced node. Its predictions equal those of
the same model scored from its MLflow run, in the preview, in a standalone
`pipeline.run()` and in a deployed bundle. An external CatBoost model saved
after training with a nonzero, nonconstant baseline matches native prediction
with that baseline supplied through a CatBoost Pool; the trace reconstructs
the same prediction. An explicit no-offset contract also scores. Missing
offset declarations, disagreement with embedded metadata, and a missing
declared offset input each fail for file, run and registered sources.

Warm the model, preview and trace caches and a downstream snapshot, then
replace the model at the same path: subsequent unpinned execution returns the
new predictions and the old snapshot is not current. Repeat with only the
automatically discovered sibling contract changed, using a valid change that
alters scoring or rejects an input; stale predictions must not be returned.
Adding a higher-priority sibling contract changes the selected contract and
its identity. Removing the model or its required contract produces a named
error even with warm caches. Retain a case for explicit contract paths too.
A missing file, an unsupported suffix, a path outside the project and an EBM
without its contract each fail with an error naming the file. The trace
explains a file-sourced CatBoost, GLM and XGBoost prediction. A frontend test
chooses a file and commits `sourceType` and `model_path`.

**Dependencies:** `MSC-02`.

**Evidence:** `src/haute/_mlflow_io.py::load_local_model`;
`src/haute/_mlflow_io.py::model_contract_candidates`;
`src/haute/_mlflow_io.py::_catboost_offset_column`;
`src/haute/deploy/_scorer.py::_load_local_model_cached`;
`src/haute/execution.py::_runtime_file_signature_paths`;
`src/haute/trace.py` (cached scored frames);
`tests/test_runtime_input_cache_invalidation.py`;
`src/haute/_types.py::ModelScoreConfig`;
`src/haute/_node_config_recovery.py`;
`frontend/src/panels/editors/ModelScoreEditor.tsx`;
`docs/building-models/nodes/model-score.md`.

### MSC-04 — Load File no longer loads CatBoost models
**Why:** Once Model Scoring scores a file, Load File's **CATBOOST** file type
is a second, weaker way to score the same file: no feature checks, a **MODEL
TYPE** that repeats the task, and scoring code the analyst writes by hand. It
also keeps model concerns in a generic loader: the `modelClass` field, its
cache classification and recovery check, the deploy scorer's handling of it,
and a CatBoost line in the Databricks serving requirements keyed on
`fileType`, which is redundant because haute itself depends on CatBoost.
Decided on 3 October 2026: CatBoost leaves Load File, and Model Scoring is the
one way to score a model. No pipeline uses a CatBoost Load File node, so the
removal carries no migration or dedicated recovery path.

**Plan:** Remove the `catboost` file type and `modelClass` from the Load File
config, loader, editor, cache classification, recovery check, deploy scorer
and Databricks requirements. A config that still names `catboost` fails as an
unsupported file type through the existing Load File validation, like any
other unknown type. The Load File picker's extensions match its remaining
loaders (pickle, joblib, JSON), which closes `BUG-07` in the same change.
Update the Load File and Model Scoring pages, the node cards and the assistant
catalogue with the specifications.

**Acceptance:** `catboost` and `modelClass` no longer appear in the Load File
config, loader, editor, cache classification or deploy code; a Load File
config with `fileType: "catboost"` is refused as an unsupported file type; a
frontend test pins the picker's extensions; the Load File page no longer
mentions CatBoost and points model files to Model Scoring.

**Dependencies:** `MSC-03`.

**Evidence:** `src/haute/_io.py::_load_external_object_uncached`;
`src/haute/_types.py::ExternalFileConfig`;
`src/haute/_node_config_recovery.py`;
`src/haute/deploy/_mlflow.py::_pip_requirements`;
`frontend/src/panels/editors/ExternalFileEditor.tsx`;
`src/haute/assistant/assets/node_cards/externalFile.json`;
`docs/building-models/nodes/external-file.md`.

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
or unsupported signature is refused. Offsets and categorical domains come
only from a feature contract, as for a registry pyfunc. The package digest
covers sorted relative file names and their bytes, including `MLmodel`, code,
pickles, data and requirement files; added and removed files change it too.
All package members and local model, artifact and code paths declared by
`MLmodel` must remain within the package after symlink resolution. An escaping
reference is refused before loading. Once trusted, the package's executed
Python has the same process privileges as other trusted project code.
Model-object and execution-cache identity include that package identity and
the resolved feature contract, extending `MSC-03`'s preview, trace and
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
`MSC-03`. A package replaced during verification cannot execute unverified
bytes, and a modified deployed package fails before loading. An escaping file
reference or symlink, an absent or unsupported signature, and a requirement
missing from or incompatible with the image are each refused with the reason.

**Dependencies:** `MSC-03`.

**Evidence:** `src/haute/_mlflow_io.py::load_local_model`;
`src/haute/_mlflow_io.py::_wrap_pyfunc`;
`src/haute/_mlflow_io.py::_extract_pyfunc_features`;
`specs/sandbox-security/high-level.md` (trusted code and untrusted artifacts);
`src/haute/deploy/_container.py` (`_ARTIFACT_EXT_TO_DEPS`).

### MSC-06 — A native model trained outside Haute scores under a declared contract
**Why:** A CatBoost `.cbm` trained outside Haute currently loads, but its
features and categorical columns do not establish its baseline semantics;
`MSC-03` requires an explicit offset/no-offset contract when those semantics
are absent. A GLM file describes itself. An XGBoost or LightGBM file trained
outside Haute is refused, because Haute's wrappers read the features,
categorical encoding, task, link and offset from a record Haute writes into
the file. The refusal is right: the
code each categorical level maps to, the link and the offset cannot be guessed
without risking plausible but wrong prices. Teams with existing XGBoost and
LightGBM models therefore have no route in short of retraining in Haute or
wrapping the model as pyfunc (`MSC-05`). Decided on 3 October 2026: Model
Scoring supports these files.

**Plan:** Register an external XGBoost and an external LightGBM family through
`MSC-02`, each needing a feature contract. The libraries save to generic
extensions (XGBoost to `.json` or `.ubj`, LightGBM usually to `.txt`), so the
suffix alone does not decide the family: the node records the family the
analyst chose, loading checks that the file really is that library's model,
and a `.ubj` file carrying Haute's record still loads as a Haute model. A
model trained through either library's scikit-learn interface comes in through
its saved booster. The contract is drafted in the editor from what the file
records, completed by the analyst and saved beside the model, with its model
identity marked as external. Whatever the file records must agree with the
contract: XGBoost's feature names, types and saved categories; LightGBM's
feature names, categorical levels and missing-value flags; and the objective,
its response-transform parameters, task and link. A categorical feature whose
level-to-code mapping the file does not record is refused.

An external XGBoost contract must explicitly declare the missing-value policy
used to construct its training DMatrix: NaN only, or NaN plus a finite numeric
sentinel. Null inputs are missing; ordinary values, including a sentinel-looking
value under the NaN-only policy, retain their numeric meaning. The same
declared policy is applied when building prediction and contribution matrices.
A saved booster does not recover this policy, so the draft marks it unresolved until
the analyst supplies it. Missing or invalid declarations fail; external files
never inherit Haute's training defaults. Honour LightGBM's saved missing-value
flags and refuse a combination the adapter cannot reproduce.

Initial objective support is limited to the built-in regression and binary
objective/link combinations Haute already implements. Where the file cannot
establish whether training used a built-in objective, the contract must
explicitly declare built-in, custom or unknown objective provenance. External
XGBoost always needs this declaration: a custom callback is not saved, and
the saved objective name can still be an allowed built-in name. The editor
leaves provenance unresolved until the analyst supplies it; custom, unknown
or omitted provenance is refused. This is a declaration of training semantics,
not a claim that the native artifact can verify an analyst's assertion.
For declared built-in objectives, inspect the full saved objective and its
prediction-affecting parameters and require agreement with the contract. In this
package LightGBM requires unit sigmoid and `reg_sqrt=false`; a non-default
sigmoid (including `sigmoid=2`), square-root response transform, custom or
unknown objective, multiclass or multi-output model is refused with the
unsupported setting named. The component specification enumerates the exact
accepted combinations and parameters before code changes. No unsupported
transform is reduced to identity, log or unit logit.

Binary labels in negative/positive order come from the contract. Every
external contract explicitly declares an offset column and its transform, or
no offset. The offset transform is separate from the response link: an already
transformed margin uses an identity offset transform even for a log-response
model. The objective cannot reveal whether training used `base_margin` or
`init_score`. Missing offset declarations and missing declared offset inputs
fail. The trace explains accepted models through each library's native
contributions with the same missing policy, response transform and offset as
prediction. Update the feature contract and MLflow model registry high- and
low-level specifications first, including serialization, hashing and
validation of these declarations.

**Acceptance:** Models trained through each library's own APIs cover numeric
and categorical input, regression and binary classification, and offset and
explicit no-offset declarations. Score the saved models from a project file
and an MLflow run in the preview, a standalone run and a deployed bundle.
Independent reference calculations use the native library, never Haute's
wrapper, encoder or offset helper:

- For XGBoost, build a DMatrix with the training category mapping and declared
  missing policy. Supply the independently transformed row offset as
  `base_margin` when declared; it replaces the native base score, so do not
  add that score again. Compare native response predictions with Haute's
  regression output or positive-class probability as appropriate.
- For LightGBM, obtain native raw predictions, add the independently
  transformed row offset once when declared, then apply the supported
  response transform. For a log-link exposure model this is
  `exp(native_raw + log(exposure))`, not ordinary `Booster.predict`.
- Use nonconstant, nonunit offsets. Assert the original binary labels and
  positive-class probabilities separately: the positive label applies when
  `p > 0.5`, with the negative label at a tie. Reconstruct the same served
  prediction from trace contributions, bias, offset and response transform.

Round-trip an XGBoost model trained with `missing=-999`, with sentinel, null
and ordinary-valued rows, and a NaN-only-policy model where `-999` is ordinary
data. Each matches its own native reference. Omitted or invalid missing-value
policies fail. LightGBM `sigmoid=2` and `reg_sqrt=true` models fail naming the
unsupported transform instead of producing default-transform predictions.
Save an XGBoost model trained with a custom objective callback while retaining
an allowed saved name such as `reg:squarederror`. Inspection must leave its
objective provenance unresolved; absent, custom or unknown declarations each
refuse scoring. A model known to have used the built-in objective scores only
after that provenance is declared and the remaining contract checks pass.
A contract that disagrees with the file, a file that is not the chosen
library's model, an unsupported objective or output shape, a categorical
feature with no recorded mapping, and absent offset declarations or inputs
are each refused by name. A frontend test drafts a contract, keeps missing-value
semantics and objective provenance unresolved until supplied, and saves the
completed declarations.

**Dependencies:** `MSC-02`, `MSC-03`.

**Evidence:** `src/haute/modelling/_xgboost.py::XGBoostModel`;
`src/haute/modelling/_lightgbm.py::LightGBMModel`;
`src/haute/modelling/_feature_contract.py::FeatureContract`;
`src/haute/_mlflow_io.py::verify_contract_identity`;
`tests/test_lightgbm_family.py` (native offset-inclusive reference);
`tests/test_xgboost_family.py` (native prediction references);
[XGBoost model IO: custom objective and metric](https://xgboost.readthedocs.io/en/release_3.1.0/tutorials/saving_model.html#custom-objective-and-metric).

### MSC-07 — ONNX models score in Model Scoring
**Why:** ONNX is the common export format across frameworks, and the Load File
picker and the container's dependency map already name `.onnx`, yet nothing
loads one. The package is deferred because `onnxruntime` is not a Haute
dependency, no user has asked for it, and pyfunc (`MSC-05`) covers Python
models first.

**Plan:** If reopened, register an ONNX family through `MSC-02`: input names
from the graph, a required feature contract for categorical domains, task and
offset, `onnxruntime` as an optional extra, and probability outputs mapped to
the binary positive-class rule.

**Acceptance:** An ONNX regressor and classifier converted from another
framework score with predictions equal to the source model within float
tolerance. Without the extra installed, choosing an ONNX file fails naming the
extra.

**Dependencies:** `MSC-02`, `MSC-03`, and demand for ONNX.

**Evidence:** `src/haute/deploy/_container.py` (`_ARTIFACT_EXT_TO_DEPS`);
`frontend/src/panels/editors/ExternalFileEditor.tsx`.
