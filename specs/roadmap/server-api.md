# Server API roadmap

## Scope

The local server's routes and live sync, as [the server API
specification](../server-api/high-level.md) describes them: what a route
answers when a node of the analyst's own fails, what the file watcher does on
the event loop, what a save answers once its files are written or when it
cannot write them, and what it leaves when the server dies in the middle.
`SRV-01` to `SRV-03` come from the 9 November 2026 codebase review:
`SRV-02` carries a measurement taken on that day and `SRV-01` is read from
the source. `SRV-03` was read from the source then and reproduced on 10
November by the review plan's sweep of failed writes, which failed every
step of 17 writing operations in turn, held their files open and killed the
server at three points. `SRV-04` and `SRV-05` come from that sweep and were
reproduced on that day; the main session added the Data Output's case to
`SRV-04`, the sweep of the frontend's logic added the too-long path, the
main session's run of 11 November added the read-only file, and `SRV-06`
comes from the main session's own runs of 10 November. `SRV-07`
comes from the plan's sweep of damaged requests and the main session's
runs behind it on 11 November.

`SRV-01` changes an accepted decision, [the optimiser's error-detail
policy](../optimiser/error-detail-policy.md), so it is a `Decision`.

## Priorities

| Package | State | Priority | Outcome |
|---|---|---:|---|
| SRV-01 | Decision | P2 | A write, a training run or a solve stopped by one of the analyst's own nodes says which node and why, as preview and trace do. |
| SRV-02 | Planned | P2 | Live sync watches only what it reads and does its parsing off the event loop. |
| SRV-03 | Planned | P2 | A save whose files are written answers as saved even when clearing a stale settings file fails, and the next save clears it. |
| SRV-04 | Planned | P2 | A save, a settings change or a Data Output's Write that cannot replace its file answers naming the file and why, as the utility editor and the Pipeline files routes do. |
| SRV-05 | Planned | P3 | A save the server died in is reported at the next load, and no staging file is left to reach version history. |
| SRV-06 | Planned | P2 | A pipeline file that is missing is reported in one line naming the file and the remedy, when the editor starts and while it runs. |
| SRV-07 | Planned | P2 | A typed file name that Windows cannot hold is refused naming the path and the rule by the optimiser's save, the model save and a Data Output's Write, and a file with a device name can be deleted from the Files panel. |

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
**Why:** Reproduced on 10 November 2026; first read from the source on 9
November. After a save's files are written transactionally and the result is
parsed back to verify it, the service removes settings files the graph no
longer uses, with no error handling: an unlink of each stale file, then a
remove of each emptied folder. An `OSError` there leaves the function after
every file was written. Run with a node removed while another program held
its settings file open, the save answered 500 "Operation failed. Check the
server logs for details." with the new pipeline file, the new sidecar and
the other node's new settings on disk, and no save on the ledger. The route
records a save as committed only after the service returns, so this one is
not broadcast, and the editor keeps its old base revision: its next save is
refused as stale and tells the analyst to reload, over work that is in fact
on disk (this last step read from the source, not run).

The removal is never tried again. A later save answered 200 and the removed
node's settings file was still on disk, still in the ledger's tree, and
still in the milestone after a Commit. Stale files are found by comparing
the graph on disk before the save with the graph being saved, and after the
failed save the graph on disk no longer names the file. The service
deliberately does not scan the settings folders for files it does not
recognise, so nothing else finds it.

**Plan:** Treat the removal as housekeeping after the commit: catch the
failure, leave the stale file, and return the committed revision with a
warning naming the file that could not be removed. Record the files a save
could not remove, in the clone's state under `.haute`, and have the next
save remove them and capture the removal. Do not bring back the folder scan.

**Acceptance:** With the unlink of a stale sidecar made to raise
`PermissionError`, the save answers success with the new revision and a
warning naming the file; the commit is recorded and broadcast; a second save
from the same editor is accepted, removes the file once it is free, and the
ledger records the removal.

**Dependencies:** None.

**Evidence:** `src/haute/routes/_save_pipeline.py::SavePipelineService._save`;
`src/haute/routes/_save_pipeline.py::_remove_stale_config_files`;
`src/haute/routes/_save_pipeline.py::_compute_disk_prev_config_files`;
`src/haute/routes/pipeline.py::save_pipeline` records the commit only after
the service returns.

### SRV-04 — A write that cannot replace its file says which file and why
**Why:** Reproduced on 10 November 2026 with real second handles holding
the files, and nothing injected. The commonest case first: a Data Output
writes its results to a CSV file, the analyst opens the file in Excel, and
presses Write again with Replace existing file. The answer is 500 "Operation
failed. Check the server logs for details.", for CSV and for Parquet (run
with the file held as Excel holds a workbook: others may read it, nobody may
write or replace it). A pipeline save while another program holds `main.py`
or its sidecar, a change to the pipeline settings, a change to the MLflow
settings (`haute.toml` held) and a change to the git preferences answer the
same. In every case the held file is unchanged and nothing is left beside
it, and in the sweep a save rolled its other files back, so nothing is lost.
But the analyst is told nothing they can act on. A save whose staging write
is refused for a full disk answers the same (run with the write made to
raise "No space left on device").

The words for both cases exist. The utility editor's save, run beside the
others with its file held, answered 409 "utility/helpers.py is open in Haute
or another program. Try again once it's closed.", and the Pipeline files
routes answer a full disk with 507 "There isn't room on disk for" and the
file's name (read from the source). The shared atomic writer already retries
a file held for a moment, so what reaches the analyst is a file held for
longer: a spreadsheet left open, a script reading it, a sync or backup
client, a virus scan.

A third cause has its own error and still no answer: a path too long for
the file system. A node's settings file is named from the node's name, with
every character outside ASCII written as six or seven (a Cyrillic or Greek
letter as six, a Japanese or Chinese character as seven). A name of 36
Japanese characters is accepted by the editor's rename rule, which allows
200 characters and looks only at their shape, and makes a file name of 257
characters; some 42 Cyrillic or Greek letters do the same by that
arithmetic (measured on the encoding, not saved). The save answers the same
500. The writer raised its own `PathTooLongError`, which no route
translates. On Windows without long paths the whole path counts, so fewer
characters are already too many.

A fourth cause is a file marked read-only: a rates file protected on
purpose, or a project unpacked from an archive. Run on 11 November with
nothing holding any file. A pipeline save with the pipeline file or its
layout file read-only, a change to the pipeline settings and a change to
the MLflow settings answer the same 500. The two routes that have words
use the wrong ones: saving or deleting a utility module, and replacing or
deleting a Pipeline file, answer 409 naming the file and saying that it "is
open in Haute or another program. Try again once it's closed." Nothing has
it open, so trying again never works. A Restore over a read-only pipeline
file succeeds.

**Plan:** Translate the two failures once, where the save, the settings
routes and the Data Output's write can share it: a destination another
program holds becomes the 409 naming the file relative to the project, and
a write refused for space becomes the 507 naming it, in the words the
utility and Pipeline files routes use. Translate the writer's too-long-path
error as well, naming the node whose name makes the path too long, and have
the rename rule count a name as its file will hold it. Tell a read-only
file apart from a held one before answering (its attribute says which),
on these routes and on the two that word the held file already, and say
that the file is read-only. Other `OSError`s keep the generic answer.

**Acceptance:** With its file held open by a real second handle on Windows,
each of the pipeline save, the pipeline settings, the MLflow settings, the
git preferences and a Data Output's Write answers 409 naming the file, and
the file is unchanged. With the staging write refused for space, each
answers 507 naming the file. A node name that makes its settings file's
path too long is refused when it is typed, or by the save, in a message
naming the node and the limit. With its file marked read-only and nothing
holding it, each of those writes, the utility editor's save and delete and
the Pipeline files' replace and delete answer naming the file and saying
that it is read-only.

**Dependencies:** None.

**Evidence:** `src/haute/routes/pipeline.py::write_output_node`;
`src/haute/routes/pipeline.py::save_pipeline`;
`src/haute/routes/pipeline.py::patch_pipeline_settings`;
`src/haute/routes/mlflow.py::put_mlflow_settings`;
`src/haute/routes/git.py::git_set_prefs`;
`src/haute/_file_ops.py::atomic_path`;
`src/haute/_file_ops.py::PathTooLongError`;
`src/haute/_graph_utils.py::_sanitize_func_name`;
`src/haute/routes/utility.py::_replace_or_remove`;
`src/haute/routes/project_files.py::_publish_write`.

### SRV-05 — A save the server died in is noticed and its leftovers collected
**Why:** Reproduced on 10 November 2026. A save replaces its files one after
another: the pipeline file, each node's settings file, then the layout
sidecar. Every file is rewritten on every save, and what a rollback needs
is held in memory. Measured on this machine, the replaces of one save span
24 ms for a pipeline of 3 nodes and 221 ms for one of 31, about 7 ms a file.
A server process that dies inside that span (killed here as the third file
was about to be replaced) leaves three things. The pipeline file is the new
one while a node whose settings changed in the same save keeps its old
settings. The settings file of a node the save removed stays. And the
staged copy of the file being replaced, `band_x.0aa46832.tmp`, stays beside
the settings. After a restart the pipeline loads `ready` with no
diagnostic, so nothing says it is a mix of two saves.

The next save brings the settings up to date, and leaves the other two.
The removed node's settings file is never looked for again (`SRV-03`). The
milestone's sweep takes every new file in the pipeline folder, so the next
Commit put both into version history: the milestone's tree held
`rating/config/banding/band_x.0aa46832.tmp` and
`rating/config/constant/rates.json`. A settings write killed at its rename
leaves `.haute/pipeline-settings.3b78e6a0.tmp` in the same way, though the
settings themselves read back whole. Two writers already collect their own
leftovers: the upload route removes staging files older than the server's
start, noting that "a server stopped mid-upload leaves its file, and nothing
else would ever remove it", and the assistant's session store prunes its
own. The shared atomic writer, which the save and the settings writes use,
has no collector.

A Data Output's Write leaves a second kind of leftover, run for CSV and for
Parquet with the server killed as the Write publishes its file. The output
itself stays the old, whole file. Its staged copy, a full copy of the new
output named `.result.haute-stage-` and 32 hex digits, stays in the
analyst's output folder after a restart and after a later Write that
succeeds. Nothing removes it, the Files panel hides it as a dot-name, and
each Write that is cut short adds another. The sweep's second run saw the
atomic writer's leftovers from two more operations killed at their rename,
an undelete and a branch-away (`.haute/trash.<hex>.tmp`,
`.haute/state.<hex>.tmp`).

**Plan:** Collect first: when the server starts and before a save, remove
the atomic writer's staging files older than the server's start in the
folders Haute owns (beside the pipeline file, its `config` and `modules`
folders, and `.haute`), as the upload route does, and keep the pattern out
of the milestone's sweep. Before a Data Output's Write, remove the staged
copies of the same destination that an earlier Write left. Then make a
cut-short save visible: write a small mark under `.haute` naming the save's
files before the first replace and remove it after the last; a mark found
when the pipeline is loaded becomes a diagnostic naming the files, and its
list feeds the removal `SRV-03` records.

**Acceptance:** After a save killed between two replaces, the next load
says the save was cut short and names the files; after the next start no
staging file remains; a Commit records no staging file; and the removed
node's settings file is gone after the next save. After a Write cut short,
the next Write to that destination leaves the output file alone in its
folder.

**Dependencies:** `SRV-03` for the record of files a save could not remove.

**Evidence:** `src/haute/routes/_save_pipeline.py::_stage_artifact_write_bytes`;
`src/haute/routes/_save_pipeline.py::_rollback_artifacts`;
`src/haute/_file_ops.py::atomic_path`;
`src/haute/_file_ops.py::_temp_path_for`;
`src/haute/routes/project_files.py::_remove_stale_staging`;
`src/haute/_git_transactions.py::_pipeline_folder_new_files`;
`src/haute/executor.py::new_data_output_staging_path`;
`src/haute/executor.py::commit_prepared_data_output`.

### SRV-06 — A missing pipeline file is reported in one line
**Why:** Reproduced on 10 November 2026, in two forms.

While the editor is running, the pipeline file is renamed or deleted in the
file manager (or by a branch switched in a terminal). Loading the pipeline
then answers 500 "Operation failed. Check the server logs for details.",
every time. A save from the open editor answers "stale_document_revision:
The pipeline changed on disk after this document was loaded. Reload it
before saving.", and the reload it asks for fails with the same 500. The
analyst is sent round a loop, and the one sentence that would end it stays
in the log.

At start-up, with `haute.toml` naming a pipeline file that is not there (a
mistyped path, or the file renamed while the editor was closed), the server
does not start: it prints 123 lines, 115 of them a Python traceback through
the application's start-up, then that sentence, "Pipeline file configured in
haute.toml [project].pipeline does not exist: ... Fix the path in haute.toml
or create the file.", then "Application startup failed. Exiting." A file
that is there but holds no pipeline, such as an empty one, ends the same way
with "does not look like a Haute pipeline". The server was started as the
serve command's production mode starts it, since inside this checkout the
command picks its development mode; the command makes no check of the
pipeline file before it starts the server (read).

The message is good and names the remedy in both forms; it is the last thing
an analyst will find. The same resolver's error reaches the deploy dry run
uncaught as well (`DPL-03`). Without a `haute.toml` the editor starts and
the first save creates the file.

**Plan:** Give the resolver's refusal a place in the answers: loading the
pipeline answers it as a refusal with its own code and the resolver's
sentence, and the editor shows it with the file's name instead of an empty
canvas. Resolve the pipeline in the serve command before the server is
started, and print the sentence as the command prints its other errors, with
exit code 1; keep the start-up check for a server started any other way,
caught there and logged as one line.

**Acceptance:** With the pipeline file renamed away under a running editor,
loading the pipeline answers a 4xx naming the file and the remedy, and the
editor shows that sentence. `haute serve` on a project whose `haute.toml`
names a missing file, or a file with no pipeline in it, prints one error
line naming the file and the remedy, with no traceback, and exits 1.

**Dependencies:** None.

**Evidence:** `src/haute/discovery.py::discover_pipelines`;
`src/haute/routes/_helpers.py::_ensure_pipeline_index`;
`src/haute/cli/_serve.py::handle_serve`.

### SRV-07 — A file name Windows cannot hold is refused by name
**Why:** Reproduced on 11 November 2026 on three paths an analyst types:
the "File path" of the optimiser's Publish pane, a Data Output's path and a
model's file path. The first two were run through their routes, the
optimiser's after a real solve. The model save's two file calls were run
directly; the route wraps them in its own generic answer (read).

A name with a time in it, `outputs/ratebook 10:30.json`, answers 500
"Filesystem error saving optimiser result. Check the server logs for
details." and leaves an empty file named `ratebook 10` in the folder:
Windows reads the colon as the start of a second stream of that file. A
question mark, an asterisk, angle brackets, double quotes or a pipe in the
name, a name of 300 characters, and a path whose folder is an existing file
all answer the same text. The model save answers its twin, "Filesystem
error saving the model. Check the server logs for details.", and leaves the
same empty file. A Data Output's Write answers "Operation failed. Check the
server logs for details." for each of them except the colon, where it gives
a wrong diagnosis: "Could not publish output without replacing an existing
destination: outputs/ratebook 10:30.csv. Choose a destination on a
filesystem that supports atomic create-only publication." It leaves an
empty `.ratebook 10`.

The names Windows keeps for devices are handled three ways. The model save
refuses them in words, "Path 'outputs/con.cbm' contains the reserved device
name 'con.cbm'.", and gives the reason. The Files panels refuse them too.
The optimiser's save and a Data Output's Write accept them and, on this
machine, create the file: "Saved optimisation result to outputs/con.json".
Git cannot open such a file. So an output named `aux.csv`, written into the
pipeline folder that Commit versions as a whole, stops every Commit with
"Operation failed. Check the server logs for details." (git: "adding files
failed"). The Files panel lists the file as new and will not delete it:
"rating/files/exports/aux.csv isn't a name a project file can have: avoid
':' and the names Windows reserves, such as CON." The analyst has to find
and remove, outside Haute, a file that Haute wrote.

**Plan:** One rule for a typed destination, applied by the three saves
before anything is written: refuse the characters Windows does not allow in
a name, a trailing dot or space, the device names and a name too long for
the disk, each time naming the path and the rule, as the model save does
for a device name. Answer a failure that still reaches the write with the
path and the reason (`SRV-04`). Let the Files panel delete any file it
lists, whatever its name.

**Acceptance:** Each name above, typed into each of the three paths,
answers 4xx naming the path and the rule, and the folder holds no new file
afterwards. A file with a device name that is already in the pipeline
folder can be deleted from the Files panel, and Commit then succeeds.

**Dependencies:** `SRV-04` for the wording of a write that fails. `VCS-02`
for a Commit that names the file it could not add.

**Evidence:** `src/haute/routes/optimiser.py::save_result`;
`src/haute/routes/modelling.py::save_model`;
`src/haute/executor.py::DataOutputPublicationError`;
`src/haute/_path_resolution.py::_reject_reserved_device_components`;
`src/haute/_artifact_paths.py::ordinary_path_problem`;
`src/haute/routes/project_files.py::_delete`;
`src/haute/_git_transactions.py::_pipeline_folder_new_files`;
`frontend/src/panels/optimiser/OptimiserPublishSection.tsx`.
