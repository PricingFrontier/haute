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
source.

## Priorities

| Package | State | Priority | Outcome |
|---|---|---:|---|
| MLR-01 | Planned | P2 | A model load that fails for a reason other than unreadable bytes keeps its error and leaves the local copy alone. |
| MLR-02 | Planned | P3 | Clearing old model copies never fails a load that succeeded, and never removes a copy another process is reading. |
| MLR-03 | Planned | P3 | The run picker says how many runs it could not check, and lists the runs the loader can load. |
| MLR-04 | Planned | P3 | A Model Scoring node whose MLflow run does not exist is reported by node, run and destination, not as a traceback. |

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
instruction addressed to coding agents, before their output.

**Plan:** Convert a failed lookup of a run, a registered model, a version or
an alias, at the point the model source is resolved, into Haute's
configuration error naming the node, what it looked for and the destination
it looked in, and mark the node. Both commands then print one line. Set the
`mlflow.agent.hint` logger above INFO where Haute configures logging.

**Acceptance:** `haute run` and `haute deploy --dry-run` on the example exit
1 with one error line naming the Model Scoring node, the run id and the
destination, and no traceback. A tracking server that cannot be reached is
reported as that, not as a missing run. Neither command prints the agent
hint.

**Dependencies:** None.

**Evidence:** `src/haute/cli/_run.py`; `src/haute/errors.py::failing_node`;
`src/haute/cli/_deploy.py::handle_deploy`;
`src/haute/_mlflow_utils.py::resolve_mlflow_source`;
`src/haute/assistant/assets/examples/model_lifecycle/pipeline.py`.
