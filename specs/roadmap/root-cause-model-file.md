# Root cause: which file a model reference means

## Scope

A Model Scoring node's reference to an MLflow model that is resolved to a
run, after which each caller finds a model file in that run by its own
search: the loader, the picker, the bundle and the container's
dependencies can each mean a different file, or none.

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
| `DPL-10` | [deploy](deploy.md) | A container for a model chosen by run carries MLflow and the model's engine | In part: the image's dependency list still has to read the engine from the resolved file |

## Planned improvements

### MLR-11 — A model reference resolves to one file, for every caller
**Why:** Read on 19 November 2026. `resolve_mlflow_source` answers a
reference with a run id and, for a registered model, a version number. It
does not answer with a file. Five callers take the run from it: the
loader, the deploy bundle (twice), the execution plan and the optimiser's
reader. The loader and the bundle then each call `_find_model_artifact`,
which searches the run's artifacts and takes the first model file it
finds. The picker decides which runs to list by a third rule,
`_find_artifact_by_extension` over top-level names only.

So a registered version, whose source names its file, scores the first
file of its run instead (`MLR-06`, P1): the frequency model's scores where
the severity model was registered, in the preview, `haute run`, the deploy
scorer and the bundle alike, with nothing said. The picker leaves out runs
the loader could load and lists by a rule the loader does not use
(`MLR-03`). The local copy is kept under the store, the run and the path,
which do not change when a file is logged again over that path, so the old
bytes go on scoring (`MLR-07`). The container reads its engines from the
suffixes of bundled files, and a node resolved at run time bundles none,
so the image has neither MLflow nor the engine (`DPL-10`). Each is planned
as its own fix at its own caller. The next caller that needs the model's
file will search the run again.

**Plan:** Give the resolution one answer that is a file: the store, the
artifact's full location, the model family, and what identifies its bytes
(the artifact's size and modification time or digest, as the store gives
them). A registered version resolves through its source; a run with a path
resolves to that path; a run with no path is searched once, here, by the
one discovery rule. A reference that cannot be resolved to one file is
refused by name with the files found. The loader, the local copy's key,
the picker's filter, the bundle and the image's dependency list take that
answer and do no search of their own. Update the MLflow model registry
specification before the change.

**Acceptance:** A test builds a local MLflow store holding a run with two
model files, a version registered from the second, a run with one file a
folder deep, and a file logged again over its own path. For each reference
it requires the same file from the loader, the picker's listing, the
bundle and the image's dependency list, and the scores of that file's own
`predict`. The re-logged file scores as its new bytes. No caller outside
the resolution calls `_find_model_artifact` or searches a run's artifacts;
a test holds that. The acceptance of `MLR-07` is met, and of `MLR-06` but
for the version registered from a logged model.

**Dependencies:** None. Deliver before the packages its table names.
`MLR-06` keeps its decision on logged-model versions.

**Evidence:** `src/haute/_mlflow_utils.py::resolve_mlflow_source`;
`src/haute/_mlflow_io.py::_find_model_artifact`;
`src/haute/_mlflow_io.py::_find_artifact_by_extension`;
`src/haute/_mlflow_io.py::_disk_cached_run_model`;
`src/haute/deploy/_bundler.py`; `src/haute/deploy/_container.py`;
`src/haute/execution.py`; `src/haute/_optimiser_io.py`;
`src/haute/routes/mlflow.py::_run_summaries`.
