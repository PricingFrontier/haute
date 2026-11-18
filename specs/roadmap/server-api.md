# Server API roadmap

## Scope

The local server's routes and live sync, as [the server API
specification](../server-api/high-level.md) describes them: what a route
answers when a node of the analyst's own fails, what the file watcher does on
the event loop, what a save answers once its files are written or when it
cannot write them, and what it leaves when the server dies in the middle.
`SRV-02` and `SRV-03` come from the 9 November 2026 codebase review:
`SRV-02` carries a measurement taken on that day. `SRV-03` was read from the
source then and reproduced on 10
November by the review plan's sweep of failed writes, which failed every
step of 17 writing operations in turn, held their files open and killed the
server at three points. `SRV-04` and `SRV-05` come from that sweep and were
reproduced on that day; the main session added the Data Output's case to
`SRV-04`, the sweep of the frontend's logic added the too-long path, the
main session's run of 11 November added the read-only file, and `SRV-06`
comes from the main session's own runs of 10 November. `SRV-07`
comes from the plan's sweep of damaged requests and the main session's
runs behind it on 11 November. `SRV-08` comes from the main session's own
run, that day, of a submodel's whole life through the routes.

## Priorities

| Package | State | Priority | Outcome |
|---|---|---:|---|
| SRV-02 | Planned | P2 | Live sync watches only what it reads and does its parsing off the event loop. |
| SRV-03 | Planned | P2 | A save whose files are written answers as saved even when clearing a stale settings file fails, and the next save clears it. |
| SRV-04 | Planned | P2 | A node or submodel name that makes its file's path too long for Windows is refused when it is typed, naming the node and the limit, not by the save. |
| SRV-05 | Planned | P3 | A save the server died in is reported at the next load, and no staging file is left to reach version history. |
| SRV-06 | Planned | P2 | A pipeline file that is missing is shown on the editor's page in the words the load answers with, and stops `haute serve` at start in one line, not a traceback. |
| SRV-07 | Planned | P2 | A typed file name that Windows cannot hold is refused naming the path and the rule by the optimiser's save, the model save and a Data Output's Write, and a file with a device name can be deleted from the Files panel. |
| SRV-08 | Planned | P2 | A save deletes a dissolved submodel's file only when no pipeline beside the saved one still uses it, as the submodel specification says. |

## Planned improvements

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
on disk. That last step was read from the source here. Reached through a
lost answer instead, it was run in a browser on 11 November (`FEG-04`): the
editor turns read-only, and the reload drops the work done since the save.

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

### SRV-04 — A name that makes its file's path too long is refused when it is typed
**Why:** Reproduced on 10 and 11 November 2026. A node's settings file is
named from the node's name, with every character outside ASCII written as
six or seven (a Cyrillic or Greek letter as six, a Japanese or Chinese
character as seven). A name of 36 Japanese characters is accepted by the
editor's rename rule, which allows 200 characters and looks only at their
shape, and makes a file name of 257 characters; some 42 Cyrillic or Greek
letters do the same by that arithmetic (measured on the encoding, not
saved). The save is then refused: Windows refuses the path for its length,
and the answer names the settings file and the remedy (move the project,
enable long paths), not the node whose name made it, so the analyst is not
told what to rename. On Windows without long paths the whole path counts, so
fewer characters are already too many. A submodel's name reaches the same
refusal through its module file: a name of 200 letters makes the box, the
create route answering 200, and the Save is then refused with the box still
on the canvas (run on 11 November).

**Plan:** Have the rename rule count a name as its file will hold it, and
refuse a node name, or a submodel's, whose file's path would be too long for
the platform, naming the node and the limit, when it is typed and when the
submodel is made.

**Acceptance:** A node name that makes its settings file's path too long is
refused when it is typed, in a message naming the node and the limit, and a
submodel's name that does so to its module file is refused when the
submodel is made.

**Dependencies:** None.

**Evidence:** `src/haute/_graph_utils.py::_sanitize_func_name`;
`src/haute/_file_ops.py::PathTooLongError`;
`src/haute/routes/submodel.py::create_submodel`.

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

A model save leaves a third kind, with no crash at all. Saved over a model
file that is marked read-only, the save succeeds and leaves the old model
beside the new one, named `.motor.cbm.`, 32 hex digits and `.backup`: one
more full copy at every save (run three times: three copies). The copy
step's own note says that "a backup that cannot be deleted then is logged
and left as a hidden sibling, because the copy has already committed". For
a read-only file that is every time, and nothing removes them later.

**Plan:** Collect first: when the server starts and before a save, remove
the atomic writer's staging files older than the server's start in the
folders Haute owns (beside the pipeline file, its `config` and `modules`
folders, and `.haute`), as the upload route does, and keep the pattern out
of the milestone's sweep. Before a Data Output's Write, remove the staged
copies of the same destination that an earlier Write left. Make a backup
writable before deleting it, so that a model save leaves none. Then make a
cut-short save visible: write a small mark under `.haute` naming the save's
files before the first replace and remove it after the last; a mark found
when the pipeline is loaded becomes a diagnostic naming the files, and its
list feeds the removal `SRV-03` records.

**Acceptance:** After a save killed between two replaces, the next load
says the save was cut short and names the files; after the next start no
staging file remains; a Commit records no staging file; and the removed
node's settings file is gone after the next save. After a Write cut short,
the next Write to that destination leaves the output file alone in its
folder. A model saved three times over a read-only model file leaves the
model and its contract alone in theirs.

**Dependencies:** `SRV-03` for the record of files a save could not remove.

**Evidence:** `src/haute/routes/_save_pipeline.py::_stage_artifact_write_bytes`;
`src/haute/routes/_save_pipeline.py::_rollback_artifacts`;
`src/haute/_file_ops.py::atomic_path`;
`src/haute/_file_ops.py::_temp_path_for`;
`src/haute/routes/project_files.py::_remove_stale_staging`;
`src/haute/_git_transactions.py::_pipeline_folder_new_files`;
`src/haute/executor.py::new_data_output_staging_path`;
`src/haute/executor.py::commit_prepared_data_output`;
`src/haute/_file_ops.py::atomic_copy_files`.

### SRV-06 — A missing pipeline file is shown on the page and stops `haute serve` in one line
**Why:** Reproduced on 10 and 11 November 2026, in two forms. The load route
now answers a pipeline file renamed or deleted while the editor runs, a
`haute.toml` left half-edited and a pipeline file emptied to nothing in one
line naming the file and the remedy (404 `pipeline_file_missing`, or the
configuration's own words). The page does not show it. The client gives
every failed response the message `HTTP <status>`, with the server's words
kept beside it, and the page's own load is the one caller that shows the
message instead of the words: after twelve to fourteen seconds of "Loading
pipeline..." the screen reads "Haute could not open this pipeline", an
explanation, and "HTTP 500", under the toast "Failed to load pipeline: HTTP
500" (seen in a browser on 11 November by the review plan's sweep of failed
calls, with the failure injected, and by the main session with a
`haute.toml` left half-edited and with the pipeline file emptied).

At start-up, with `haute.toml` naming a pipeline file that is not there (a
mistyped path, or the file renamed while the editor was closed), the server
does not start: it prints 123 lines, 115 of them a Python traceback through
the application's start-up, then that sentence, then "Application startup
failed. Exiting." A file that is there but holds no pipeline, such as an
empty one, ends the same way with "does not look like a Haute pipeline".
The server was started as the serve command's production mode starts it,
since inside this checkout the command picks its development mode; the
command makes no check of the pipeline file before it starts the server
(read). Without a `haute.toml` the editor starts and the first save creates
the file.

**Plan:** Show the load's own words on the page with the file's name instead
of an empty canvas and "HTTP <status>". Check the pipeline file in
`haute serve` before the server starts, so a missing or empty one ends the
command in one line.

**Acceptance:** With the pipeline file deleted while the editor runs, the
page says the file's name and the remedy; `haute serve` with `haute.toml`
naming a missing or empty pipeline file prints one line and exits 1, with no
traceback.

**Dependencies:** `DPL-03` covers the deploy dry run's same resolver error.

**Evidence:** `frontend/src/api/client.ts`; the page's pipeline load in
`frontend/src`; `src/haute/cli/_serve.py`; `src/haute/server.py::_lifespan`.

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

### SRV-08 — A save keeps a submodel's file that a pipeline beside it uses
**Why:** Reproduced on 11 November 2026 through the editor's routes and
`haute run`, twice each way. A project made by `haute init` keeps its
pipeline in `rating/main.py`. Two of its nodes are grouped as the submodel
`loading` and saved, which writes `rating/modules/loading.py`. A second
pipeline beside the first, `rating/renewal.py`, uses the same definition by
the import the submodel page shows, `from modules import loading`; a copy of
`main.py` kept under another name does the same without a line being
written. `haute run rating/renewal.py` prices with it. In the first pipeline
the analyst then dissolves the box, or deletes it, and presses Save. The save
answers "saved" and nothing more, deletes `rating/modules/loading.py` with
its layout file, and records that as "Updated main, loading". The second
pipeline no longer loads: "Error parsing pipeline: Referenced submodel
file(s) do not exist. (missing_paths=['modules/loading.py'])". After a
delete, as opposed to a dissolve, no file in the project holds the
definition any more; it has to be found in version history.

The submodel specification rules this out. A definition's file is deleted
only when "no other parseable pipeline references the same resolved file",
and "a child referenced by another healthy pipeline" is kept: "Uncertainty
never authorises deletion." The submodel page offers the reuse under "Reuse
across pipelines" and warns only that changes are shared.

The check exists, and works in one layout. With both pipelines in the
project folder itself, the same steps keep `modules/loading.py` and the
second pipeline still prices (run the same day). The save asks
`discover_pipelines` which other pipelines there are, and that looks at the
configured pipeline and at the `.py` files of the project folder only. A
definition is imported from the `modules` folder beside its pipeline. So in
the layout `haute init` makes, the pipelines that can share a definition are
exactly the ones the check never sees. Three more checks read the same list
(read from the source, not run): the check that a utility module's edit
breaks no other pipeline's names, the index of which pipelines import a
module, and the notice a save sends to other open documents.

**Plan:** Give the reference check the pipeline files that can import the
definition: every pipeline file in the folder of the pipeline being saved,
besides what discovery returns. One of them that cannot be parsed keeps
every definition, as an unreadable pipeline does now. Decide in the same
change whether the three other checks take that list too.

**Acceptance:** With `rating/main.py` and `rating/renewal.py` both using
`modules/loading.py`, dissolving the box in `main.py`, or deleting it, and
saving keeps `rating/modules/loading.py`, and `haute run rating/renewal.py`
prices as before. With no other pipeline using the definition, the same save
deletes the file as it does today. A regression test covers both layouts:
the pipelines in a folder of the project, and in the project folder.

**Dependencies:** None.

**Evidence:**
`src/haute/routes/_save_pipeline.py::SavePipelineService._definition_files_declared_elsewhere`;
`src/haute/discovery.py::discover_pipelines`;
`src/haute/routes/utility.py`; `specs/submodels/high-level.md`;
`docs/building-models/nodes/submodel.md`.
