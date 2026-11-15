# MLflow model registry roadmap

## Scope

Loading a model from MLflow for a Model Scoring node and choosing one in the
editor, as [the MLflow model registry
specification](../mlflow-model-registry/high-level.md) describes it: the
local copy of a run's artifact, the retry around loading it, and the list of
runs the picker offers. The packages below come from the 9 November 2026
codebase review. `MLR-01` was reproduced against `HEAD` on that day with the
loader's collaborators replaced by stubs, and `MLR-04` by running the
commands on a shipped example; `MLR-02` and `MLR-03` are read from the
source. `MLR-05` was reproduced on 10 November 2026 against a local address
where nothing listens. `MLR-06` to `MLR-08` come from the review plan's
phase 5, a reader from a second model family (Codex) over the code changed
since the 31 October audit. A worker ran each against a local MLflow store
and the main session ran each again on 13 November.

`MLR-09` comes from the review plan's phase 5, a reader from a second
model family (Codex) over the code changed since the 31 October audit. A
worker built each run and the main session ran each again on 14 November
2026.

`MLR-10` comes from the review plan's phase 5, a reader
from a second model family (Codex) over the code changed since the 31
October audit. A worker drove each journey in a browser and the main
session ran each again on 15 November 2026.

## Priorities

| Package | State | Priority | Outcome |
|---|---|---:|---|
| MLR-01 | Planned | P2 | A model load that fails for a reason other than unreadable bytes keeps its error and leaves the local copy alone. |
| MLR-02 | Planned | P3 | Clearing old model copies never fails a load that succeeded, and never removes a copy another process is reading. |
| MLR-03 | Planned | P3 | The run picker says how many runs it could not check, and lists the runs the loader can load. |
| MLR-04 | Planned | P3 | A Model Scoring node whose MLflow run does not exist is reported by node, run and destination, not as a traceback. |
| MLR-05 | Planned | P2 | A model load from an MLflow server that cannot be reached fails within seconds and says so, instead of holding a preview for four and a half minutes. |
| MLR-06 | Planned | P1 | A Model Scoring node that names a registered model and version scores the file that version was registered from, not the first model file of its run. |
| MLR-07 | Planned | P3 | A model file logged again over the same path of a run is the one that scores. |
| MLR-08 | Planned | P3 | A logged model takes a part-year or a missing exposure whatever type its training exposure had. |
| MLR-09 | Planned | P3 | A GLM trained on the canvas logs its family, link, terms and penalty to MLflow, as a scripted run does. |
| MLR-10 | Planned | P2 | Retry after the experiment was changed logs to the new experiment, or names the one the run is in. |

## Planned improvements

### MLR-01 — Only unreadable bytes are treated as a corrupt artifact
**Why:** Reproduced. The loader wraps resolving an artifact, fetching the
contract its run logged, and loading the file in one retry. It re-raises a
short list of error types as they are; every other exception is taken for a
corrupt local copy: the copy is deleted, the artifact downloaded again, the
load retried once, and the failure reported as `RuntimeError`:
"Persistently corrupt or unloadable model artifact ...". Failures that have
nothing to do with the bytes take that route. With the three collaborators
stubbed, a `MemoryError` from the load, a tracking-server error raised while
fetching the contract (the model file already present and good) and a
`PermissionError` from a briefly locked file each ended as that
`RuntimeError`, with the good local copy deleted and one extra download
made. `MemoryError` is the one that matters most: its text is empty, so the
message ends "Last error:" with nothing after it, and as a `RuntimeError` it
no longer reaches the memory-limit handling the rest of the engine gives it
(the optimiser's handlers all test for it first). A tracking-server blip
during the contract fetch destroys the copy that the disk-cache fast path,
and an execution allowed to load only from that cache, would have used.

**Plan:** Retry, delete and download again only for a failure of the
family's file loader to read the bytes. Let a memory error, an operating
system error on the cache folder, and anything raised while resolving the
artifact or fetching the contract propagate unchanged, leaving the local
copy in place.

**Acceptance:** With the stubs above, a `MemoryError` leaves the loader as a
`MemoryError` with the local file still on disk and no second download; a
tracking-server error from the contract fetch does the same; a file whose
bytes the loader cannot read is still deleted, downloaded again and, when it
fails twice, reported as corrupt.

**Dependencies:** None.

**Evidence:** `src/haute/_mlflow_io.py::_load_with_bounded_retry`;
`src/haute/_mlflow_io.py::_run_contract_if_logged`;
`src/haute/_mlflow_io.py::_resolve_artifact_local`;
`src/haute/_memory_errors.py::memory_error_in` is the test the optimiser's
handlers apply; `src/haute/errors.py::ModelNotInDiskCacheError`.

### MLR-02 — Eviction of old model copies is housekeeping
**Why:** Read from the source. After a download lands, the loader evicts the
oldest run folders beyond fifty. The eviction catches two errors around the
rename that retires a folder; a failed `stat` while sorting, or a Windows
`PermissionError` on the rename because another process has a file of that
run open, leaves the function and fails the load whose own artifact was just
stored. The neighbouring helper's docstring states the rule this breaks:
eviction "must never raise into a caller that only asked for an artifact".
The guard that keeps a run in use from being evicted is a counter inside one
process, while the server, its workers and a `haute run` beside them share
the folder. On POSIX the rename succeeds under a reader in another process,
whose later open of the contract beside the model then fails. It needs more
than fifty stored runs to start evicting at all.

**Plan:** Catch and log every failure of the eviction. Make the in-use
guard hold across processes, with the store's existing file locks or by
leaving alone any run folder used in the last few minutes.

**Acceptance:** With the rename made to raise `PermissionError`, the load
that triggered the eviction returns its model and a warning is logged. Two
processes loading different runs while a third evicts never see a missing
file, asserted with the cross-process test helpers the snapshot store uses.

**Dependencies:** None.

**Evidence:** `src/haute/_mlflow_io.py::_evict_disk_cache`;
`src/haute/_mlflow_io.py::_resolve_artifact_local_in_use` calls it after the
move; `src/haute/_mlflow_io.py::_disk_cache_active_runs` is the process-local
guard; `src/haute/_mlflow_io.py::_delete_tombstone` states the rule.

### MLR-03 — The run picker reports what it could not check and lists what loads
**Why:** Read from the source. To list an experiment's runs, the route lists
each run's artifacts and keeps the runs holding a model file. A run whose
listing raises is skipped; the number skipped goes to a log line and nowhere
else. When the artifact store refuses or flaps while the tracking store
answers, every run is skipped and the picker shows its "no runs" text, not
an error. The filter also looks only at top-level artifact names ending in a
native model suffix, while the loader's own discovery looks one folder deep
and falls back to an MLflow pyfunc model folder. A run that holds
`model/model.cbm`, or only a pyfunc model, loads when its run id is typed by
hand and is never offered in the list.

**Plan:** Return the number of runs that could not be checked, show it in
the picker, and answer an error when every listing failed. Give the picker
and the loader one discovery rule, so a run is listed exactly when it can be
loaded.

**Acceptance:** With one run's listing made to fail, the response carries a
count of one and the picker says so; with all failing, the picker shows the
error. A run holding its model one folder deep, and a pyfunc-only run, are
listed, and a test holds the picker's rule and the loader's to the same
fixture runs.

**Dependencies:** None.

**Evidence:** `src/haute/routes/mlflow.py::_run_summaries`;
`src/haute/_mlflow_io.py::_find_artifact_by_extension`;
`src/haute/_mlflow_io.py::_find_model_artifact`;
`frontend/src/panels/editors/MlflowModelPicker.tsx`.

### MLR-04 — A missing MLflow run is reported as the node's configuration error
**Why:** Reproduced. On the shipped `model_lifecycle` example, whose Model
Scoring node names a run id that does not exist, `haute run` printed about
120 lines of Python traceback ending
"mlflow.exceptions.MlflowException: Run 'replace-with-training-run-id' not
found", and `haute deploy --dry-run` did the same after its header. Neither
names the node. A run deleted from the tracking store, a run id typed wrongly
and a node pointed at the wrong destination all arrive this way. The run
command names the failing node for failures the walker marks and keeps a
traceback only for the unexpected; this one is neither marked nor in the
expected list, although it is the analyst's configuration that is wrong.
Both commands also print MLflow's own `mlflow.agent.hint` log line, an
instruction addressed to coding agents, before their output. The editor
hides the same failure, reproduced on 10 November 2026: previewing the Model
Scoring node of that example, or the output after it, answers HTTP 500 with
"Operation failed. Check the server logs for details.", so an analyst who
opens the example before training sees neither the node nor the run id. A
version that does not exist and an artifact path the run does not hold
arrive as MLflow's own exception too, in the scorer and on the node in the
preview ("Model Version (name=freq, version=99) not found"); a missing
alias or registered model is already reported by name.

**Plan:** Convert a failed lookup of a run, a registered model, a version,
an alias or an artifact path, where the model source is resolved, into Haute's
configuration error naming the node, what it looked for and the destination
it looked in, and mark the node. Both commands then print one line. Set the
`mlflow.agent.hint` logger above INFO where Haute configures logging.

**Acceptance:** `haute run` and `haute deploy --dry-run` on the example exit
1 with one error line naming the Model Scoring node, the run id and the
destination, and no traceback. The editor's preview of that node shows the
same message on the node and answers no 500. A tracking server that cannot
be reached is reported as that, not as a missing run. Neither command prints
the agent hint.

**Dependencies:** None.

**Evidence:** `src/haute/cli/_run.py`; `src/haute/errors.py::failing_node`;
`src/haute/cli/_deploy.py::handle_deploy`;
`src/haute/_mlflow_utils.py::resolve_mlflow_source`;
`src/haute/assistant/assets/examples/model_lifecycle/pipeline.py`.

### MLR-05 — A model load from an unreachable MLflow server gives up in seconds
**Why:** Reproduced on 10 November 2026. With the project's MLflow
destination set to a server address where nothing listens, loading a Model
Scoring node's model from a run blocked for 269 seconds and then raised
MLflow's own exception: "API request to
http://127.0.0.1:9/api/2.0/mlflow/runs/get failed ... Max retries exceeded".
The address refused every connection at once; the time is MLflow's client
trying again with a growing pause between tries. The sweep then asked for
the node's preview, which began the wait again and was still waiting when
its 300 second limit ended it. The connection test on the settings page
already avoids this. It makes one attempt with a short timeout, and its
comment gives the reason: "MLflow's client defaults (120 s timeout, 7
retries, exponential backoff) would keep an abandoned worker ... busy for
minutes". A model load makes its calls with those defaults. A tracking
server that is stopped, a VPN that is down or an address typed wrongly
therefore holds each preview, each `haute run` and each deploy check for
four and a half minutes and ends in a library's message. `MLR-04` is about
what is said; this is about the wait.

**Plan:** Give the calls a model load makes, to read the run, the version
or the alias and to download the file, a bounded number of tries and a
total time fit for someone waiting at the editor, as the connection test
has. Report a destination that cannot be reached as that, with its address
and what to check, through the error `MLR-04` introduces. Let a batch run
and a deployment's start-up ask for a longer wait.

**Acceptance:** With the destination pointed at a closed local port, the
preview of a Model Scoring node fails in under fifteen seconds with a
message naming the node and the address, and a second preview waits no
longer than the first. A server that answers slowly but within the allowed
time still loads. A test with a refused connection holds the bound.

**Dependencies:** None. `MLR-04` words the message. `MLR-01` removes the
second wait where its retry stops treating every failure as a damaged file.

**Evidence:** `src/haute/_mlflow_io.py::_resolve_artifact_local_in_use`;
`src/haute/_mlflow_utils.py::resolve_mlflow_source`;
`src/haute/routes/mlflow.py::_search_experiments_probe` is the bounded call
to follow.

### MLR-06 — A registered model version scores the file it was registered from
**Why:** Reproduced on 13 November 2026 with a local MLflow store. An
analyst's script logs two CatBoost models into one run, `frequency.cbm` and
`severity.cbm`, and registers the second as version 1 of `severity`. The
version's source ends `artifacts/severity.cbm`. A Model Scoring node names
that model and version, with no artifact path. Its scores are the frequency
model's: 0.465, 0.478 and 0.645, where the severity model's own `predict`
gives 2287.21, 2356.015 and 3571.833. The preview, `haute run` and the
deploy scorer all give the frequency scores, and the deploy bundle carries
`frequency.cbm`. Nothing says another file was chosen.

Resolving a registered version keeps the version's run and drops its
source. The loader then searches the run's artifacts and takes the first
model file it finds. The specification locates a registered model by its
name and its version; a search of the run belongs to a model given by run
with no path. Haute's own Log to MLflow makes one run for each model, so
the fault needs a run that a script of the analyst's own made.

The same pair logged with `mlflow.catboost.log_model` and registered from
the logged model, as MLflow 3 registers, is not scored at all. `haute run`
and the deploy say "No model artifact found in run", and the preview
answers 500, "Operation failed. Check the server logs for details." A
logged model's files are not among its run's artifacts.

**Plan:** Load the file a version's source names. When a version cannot be
resolved to one model file, refuse by name: the model, the version and the
files found. Decide whether a version registered from a logged model is
supported; read it from the logged model's own location if so, and say in
every path that it is not if not.

**Acceptance:** The node above scores 2287.21, 2356.015 and 3571.833 in
the preview, in `haute run` and in the deploy scorer, and the bundle
carries `severity.cbm`. A version that cannot be told from another file of
its run is refused by name. A version registered from a logged model
either scores that model or is refused in the same words by the preview,
the run and the deploy.

**Dependencies:** None.

**Evidence:** `src/haute/_mlflow_utils.py::resolve_mlflow_source`;
`src/haute/_mlflow_io.py::_find_model_artifact`;
`src/haute/_mlflow_io.py::load_mlflow_model`;
`src/haute/deploy/_bundler.py`;
`specs/mlflow-model-registry/high-level.md`.

### MLR-07 — A run's model file logged again is the one that scores
**Why:** Reproduced on 13 November 2026 with a local MLflow store. A Model
Scoring node names a run and the artifact path `model.cbm`. The analyst's
script resumes that run and logs a retrained `model.cbm` over the same
path, and the store then holds the retrained file. Haute goes on scoring
with the first one: 290.45 and 599.24, where the retrained model gives
1029.05 and 1059.92. It does so in the same editor process with another
row limit, in a new process, and from `haute run`.

The first download is kept in the project's `.cache/models` folder under
the store, the run and the path, and a copy that exists is used without
asking MLflow. The plan's earlier sweep found that a model retrained over a
file path, and an alias moved to another version, are both picked up at
once; a run's artifact is the one source that is not.

**Plan:** Before a kept copy is used, compare it with what the run holds
now, at the cost of one request, or key the copy on what identifies the
stored file.

**Acceptance:** After the second log the node above scores 1029.05 and
1059.92, in the same process and in a new one. A load whose run is
unchanged makes no download.

**Dependencies:** `MLR-01` and `MLR-02` change the same copy's handling.

**Evidence:** `src/haute/_mlflow_io.py::_disk_cached_run_model`;
`src/haute/_mlflow_io.py::_resolve_artifact_local_in_use`.

### MLR-08 — A logged model takes a part-year or a missing exposure whatever its training data held
**Why:** Reproduced on 13 November 2026. A Poisson model is trained through
Haute on data whose exposure column holds whole numbers, typed Int64, and
is logged to MLflow. The logged model's input schema then declares the
exposure as `long`. Scored through the logged model, as a serving endpoint
scores it, an exposure of 0.5 is refused by MLflow: "Can not safely convert
float64 to int64". So are a missing exposure, and 1.0 given as a decimal.
The Model Scoring node scores the same rows natively: 0.28197 for 0.5, and
0.56395 for a missing exposure, which the specification prices "as an
exposure of one" "for every model family".

**Plan:** Declare an exposure column as a double in the logged model's
schema, whatever type the training data gave it.

**Acceptance:** The logged model above scores an exposure of 0.5, a missing
exposure and 1.0 with the values the Model Scoring node gives.

**Dependencies:** None.

**Evidence:** `src/haute/modelling/_signature.py::build_signature`;
`src/haute/modelling/_candidate_run.py`;
`specs/mlflow-model-registry/high-level.md`.

### MLR-09 — A GLM trained on the canvas logs its parameters to MLflow
**Why:** Reproduced on 14 November 2026. A Poisson GLM with a log link,
two terms, an exposure offset and a ridge penalty of 0.1 is trained on the
canvas and logged to a local MLflow. The run holds no `param_` keys. The
same model trained by a scripted job logs five: `param_alpha`,
`param_family`, `param_link`, `param_regularization` and `param_terms`.
The specification has the final parameters logged on both paths. The
canvas log reads the node's `params` setting, which a GLM does not use:
its family, link and terms are fields of the node.

**Plan:** Log the parameters the training was built with, from the same
function that built them.

**Acceptance:** The canvas run above holds the five keys the scripted run
holds, with the same values.

**Dependencies:** None.

**Evidence:** `src/haute/routes/modelling.py`;
`src/haute/modelling/_train_config.py`;
`specs/mlflow-model-registry/low-level.md`.

### MLR-10 — Retry after a changed experiment does not show the old run as the new one
**Why:** Reproduced on 15 November 2026 in the browser with a local
MLflow folder. A trained model was logged to experiment `motor-2026`; the
server logged the run, but the answer was dropped, so the panel showed a
failure and Retry. The experiment was changed to `motor-2027` and Retry
pressed. The request carries `motor-2027` with the first attempt's
operation id; the server answers with the stored receipt for
`motor-2026`. The panel shows success and the run id beside a box reading
`motor-2027`. On disk there is one run in `motor-2026` and none in
`motor-2027`.

**Plan:** Start a new operation id when a setting that the log request
carries changes. On the server, refuse a repeated operation id whose
request differs from the stored one.

**Acceptance:** The journey above ends with a run in `motor-2027`, or
with the panel naming `motor-2026` as where the run was logged.

**Dependencies:** None.

**Evidence:** `frontend/src/panels/modelling/MlflowExportSection.tsx`;
`src/haute/routes/modelling.py`.
