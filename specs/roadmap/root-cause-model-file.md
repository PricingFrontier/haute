# Root cause: which file a model reference means

## Scope

A Model Scoring node's reference to an MLflow model that is resolved to a
run, after which the loader and the bundle search that run for a model,
the picker lists by a rule of its own, and the local copy and a stored
result are kept under a name that does not change with the model's bytes.

This file is one pull request. It holds one package, `MLR-11`, which names
a cause that packages in other files share, and it is taken before them.
The pull request makes the shared mechanism the only path and adds the
test that keeps it so. It then checks each package listed below against
that package's own acceptance, removes the ones it has met, and removes
this file.

## Packages this resolves

"Resolves" means the package's acceptance should be met by this work
alone. "In part" means this work removes the shared cause and the package
keeps something of its own to do.

| Package | Roadmap | What it records | Expected |
|---|---|---|---|
| `MLR-07` | [mlflow-model-registry](mlflow-model-registry.md) | A model file logged again over the same path of a run is the one that scores | Resolves |
| `MLR-06` | [mlflow-model-registry](mlflow-model-registry.md) | A registered version scores the file it was registered from | In part: whether a version registered from a logged model is supported is still to decide |
| `MLR-03` | [mlflow-model-registry](mlflow-model-registry.md) | The run picker lists the runs the loader can load | In part: the count of runs it could not check is its own |
| `DPL-10` | [deploy](deploy.md) | A container for a model chosen by run carries MLflow and the model's engine | In part: the image's dependency list still has to read the engine from the resolved model |

## Planned improvements

### MLR-11 — A model reference resolves to one model, for every caller
**Why:** Read on 19 November 2026. `resolve_mlflow_source` answers a
reference with a run id, a version number for a registered model, and a
client. That is the right answer for its callers that want only those: the
execution plan takes the version, one of the bundle's calls takes the
client, and the optimiser's reader goes on to its own
`optimiser_result.json`. It is not an answer to which model scores.

For that, the loader and the bundle each call `_find_model_artifact` on
the run. It takes the first native model file it finds, at the top level
or one folder down, and otherwise a pyfunc model folder. The picker
decides which runs to list by another rule, its own `_match` over the
run's top-level names by suffix.

So a registered version, whose source names its file, scores the first
file of its run instead (`MLR-06`): the frequency model's scores where the
severity model was registered, in the preview, `haute run`, the deploy
scorer and the bundle alike, with nothing said. The picker leaves out a
run whose model is a folder down or a pyfunc folder, which the loader can
load (`MLR-03`). The local copy is kept under the store, the run and the
path, none of which changes when a file is logged again over that path, so
the old bytes go on scoring (`MLR-07`); and a node's stored result is kept
under the model's settings, its backend and its registered version, so a
warm preview answers from the old model without loading anything. The
container reads its engines from the suffixes of bundled files, and a node
resolved at run time bundles none (`DPL-10`). Each is planned as its own
fix at its own caller.

**Plan:** Put one model resolution above the run and client resolution,
which stays as it is for the callers that want only a run, a version or a
client. It answers a reference with one resolved model: the store; the
artifact's location, a file or a pyfunc folder; the model family; the
feature contract logged beside a native model, when there is one; and what
identifies the bytes of each as the store gives it. A registered version
resolves through its source. A run with a path resolves to that path. A
run with no path is searched here, once, by one discovery rule. A
reference that cannot be resolved to one model is refused by name with the
candidates found.

Everything that needs the scoring model takes that answer and does no
search of its own: the loader, the key of the local copy, the picker's
listing, the bundle, the image's dependency list, and the identity a
node's stored result is kept under. The deploy specification's limit
stays: a bundle does not carry a pyfunc folder, and such a node is
refused for a bundle by name. Update the MLflow model registry
specification before the change.

**Acceptance:** A test builds a local MLflow store holding a run with two
model files and a version registered from the second, a run with one file
a folder down, a run holding only a pyfunc folder, a native model with a
feature contract beside it, and a file logged again over its own path. For
each reference it requires the same model from the loader, the picker's
listing, the bundle and the image's dependency list, and the scores of
that model's own `predict`. After the file is logged again, the same
preview and the same trace, asked a second time with nothing else changed,
give the new model's scores. A test fails when code outside the model
resolution searches a run's artifacts for a scoring model; the optimiser
result's own lookup is not one. The acceptance of `MLR-07` is met, and of
`MLR-06` but for the version registered from a logged model.

**Dependencies:** None. Deliver before the packages its table names.
`MLR-06` keeps its decision on logged-model versions.

**Evidence:** `src/haute/_mlflow_utils.py::resolve_mlflow_source`;
`src/haute/_mlflow_io.py::_find_model_artifact`;
`src/haute/_mlflow_io.py::_find_artifact_by_extension`;
`src/haute/_mlflow_io.py::_disk_cached_run_model`;
`src/haute/_mlflow_io.py::load_mlflow_model`;
`src/haute/routes/mlflow.py::_run_summaries`;
`src/haute/deploy/_bundler.py`; `src/haute/deploy/_container.py`;
`src/haute/execution.py::_runtime_input_fingerprint_entry`;
`src/haute/executor.py`; `specs/deploy/high-level.md`.
