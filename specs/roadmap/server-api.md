# Server API roadmap

## Scope

The local server's routes and live sync, as [the server API
specification](../server-api/high-level.md) describes them: what a route
answers when a node of the analyst's own fails, what the file watcher does on
the event loop, and what a save answers once its files are written. The
packages below come from the 9 November 2026 codebase review. `SRV-02`
carries a measurement taken on that day; the others are read from the source
and were not run end to end.

`SRV-01` changes an accepted decision, [the optimiser's error-detail
policy](../optimiser/error-detail-policy.md), so it is a `Decision`.

## Priorities

| Package | State | Priority | Outcome |
|---|---|---:|---|
| SRV-01 | Decision | P2 | A write, a training run or a solve stopped by one of the analyst's own nodes says which node and why, as preview and trace do. |
| SRV-02 | Planned | P2 | Live sync watches only what it reads and does its parsing off the event loop. |
| SRV-03 | Planned | P2 | A save whose files are written answers as saved even when clearing a stale settings file fails. |

## Planned improvements

### SRV-01 — A node's own failure is named by Write, Train and the optimiser
**Why:** Read from the source. When a node's code fails, the walker marks the
failure with its node. Preview shows it on the node, trace answers "Node 'x'
failed: ..." and `haute run` names the node. Three other routes discard it.
A Data Output write builds "Writing Data Output 'x' failed while computing
its rows: ..." and marks the node, but the failure leaves the worker as a
remote error and the route answers it with no public detail: "Operation
failed. Check the server logs for details." Training's data preparation
answers any failure it does not classify, an upstream node's own error
included, with "Pipeline execution failed. Check the server logs for
details." Optimiser setup records the real message on the job and raises the
same generic text as its answer. The generic text is deliberate for unknown
failures: the accepted policy keeps library text and file paths out of
answers. But a failure in the analyst's own node, whose message preview
already shows in full, is not unknown, and an analyst on a hosted editor has
no server log to check.

**Plan:** Decide that a failure the walker marked with a node is the
analyst's own and public. Then convert it once, with the trace's conversion,
at the worker boundary of the write, the training preparation and the
optimiser setup: "Node 'x' failed: <message>" with the node id on the
payload, so the canvas can mark the node. Failures with no node stay
generic. Amend the policy document in the same change.

**Acceptance:** With a Transform that raises upstream of a Data Output, a
Model Training node and an Optimiser, each of Write, Train and Solve answers
with the node's name and its message and carries the node id; the same
pipeline's preview shows the same message. A failure raised outside any node
still answers the generic text, and no answer carries a file path.

**Dependencies:** The decision above, which amends the optimiser's
error-detail policy.

**Evidence:** `src/haute/errors.py::mark_failing_node`;
`src/haute/routes/pipeline.py::_trace_node_failures` is the conversion to
reuse; `src/haute/routes/pipeline.py::_prepare_data_output_worker` and
`src/haute/routes/pipeline.py::write_output_node`;
`src/haute/routes/_worker_failure_http.py::_remote_http_exception`;
`src/haute/routes/_training_preparation.py::preparation_failure_outcome`;
`src/haute/routes/_training_preparation.py::prepare_training_data_worker`;
`src/haute/routes/_optimiser_service.py`; `src/haute/cli/_run.py`.

### SRV-02 — Live sync watches what it reads and parses off the event loop
**Why:** Measured cost, reach read from the source. The file watcher watches
the whole project root recursively with the watcher library's default filter,
which lets through Haute's own `.haute_cache` folder (source snapshots, node
snapshots, JSON spill), the outputs folder and data folders. Its flush is an
`async` function that awaits nothing: for every changed path it resolves the
path and runs the routing checks on the event loop, then loads, dumps and
fingerprints the full editor document of each affected pipeline there, all
pipelines when a settings file changed. The websocket resync does the same
load in a worker thread, with a comment saying why. The per-path work alone
took 16.1 seconds for 20,000 changed paths that no longer existed (0.8 ms
each, on the review machine). A chunked cache write of a few thousand part
files, each a temporary file then a rename, therefore stalls every request
and every websocket for seconds when the debounce fires, and the debounce
restarts on every batch, so a long write is flushed in one piece at its end.
The preview route computes its supersession key on the event loop too, where
trace computes its key in a worker thread.

**Plan:** Give the watcher a filter that names what live sync reads: pipeline
and module `.py` files, settings and sidecar JSON, the form folder. Run a
batch's routing and document loads in a worker thread, as the resync does,
and publish from the loop. Move the preview's supersession key to a worker
thread.

**Acceptance:** Writing 5,000 files under `.haute_cache` produces no flush
at all, asserted on the watcher's filter and on a counter in the flush. A
batch of 20,000 changed paths keeps a concurrent request to a cheap route
under 100 ms, asserted with an injected batch. An edit to a pipeline file is
still broadcast.

**Dependencies:** None.

**Evidence:** `src/haute/server.py::_file_watcher` and its nested
`_flush_once`; `src/haute/server.py::_publish_pipeline_document`;
`src/haute/server.py::_prepare_ws_document_resync` uses
`run_in_threadpool`; `src/haute/routes/pipeline.py::_preview_canonical_graph`
and `src/haute/routes/pipeline.py::_preview_supersession_key`.

### SRV-03 — A save that landed answers as saved
**Why:** Read from the source, not run. After a save's files are written
transactionally and the result is parsed back to verify it, the service
removes settings files the graph no longer uses, with no error handling: an
unlink of each stale file, then a remove of each emptied folder. An `OSError`
there (on Windows, a sidecar another program holds open; anywhere, a file
appearing between the emptiness check and the remove) leaves the function
after every file was written. The route answers an error, the save is never
recorded as committed, so it is not broadcast and the ledger never captures
it, and the editor keeps its old base revision: its next save is refused as
stale and tells the analyst to reload, over work that is in fact on disk.

**Plan:** Treat the removal as housekeeping after the commit: catch the
failure, leave the stale file, and return the committed revision with a
warning naming the file that could not be removed. The next save tries the
removal again.

**Acceptance:** With the unlink of a stale sidecar made to raise
`PermissionError`, the save answers success with the new revision and a
warning naming the file; the commit is recorded and broadcast; a second save
from the same editor is accepted.

**Dependencies:** None.

**Evidence:** `src/haute/routes/_save_pipeline.py::SavePipelineService._save`;
`src/haute/routes/_save_pipeline.py::_remove_stale_config_files`;
`src/haute/routes/pipeline.py::save_pipeline` records the commit only after
the service returns.
