# Server API roadmap

## Scope

The local server's routes and live sync, as [the server API
specification](../server-api/high-level.md) describes them: what a route
answers when a node of the analyst's own fails, what the file watcher does on
the event loop, what a save answers when it cannot write its files, and what
a Data Output's Write and a model save leave when they are cut short.
`SRV-02` comes from the 9 November 2026 codebase review and carries a
measurement taken on that day. `SRV-04` and `SRV-05` come from the review
plan's sweep of failed writes on 10 November, which failed every step of 17
writing operations in turn, held their files open and killed the server at
three points, and were reproduced on that day; the main session added the Data Output's case to
`SRV-04`, the sweep of the frontend's logic added the too-long path, the
main session's run of 11 November added the read-only file, and `SRV-06`
comes from the main session's own runs of 10 November. `SRV-07`
comes from the plan's sweep of damaged requests and the main session's
runs behind it on 11 November.

## Priorities

| Package | State | Priority | Outcome |
|---|---|---:|---|
| SRV-02 | Planned | P2 | Live sync watches only what it reads and does its parsing off the event loop. |
| SRV-04 | Planned | P2 | A node or submodel name that makes its file's path too long for Windows is refused when it is typed, naming the node and the limit, not by the save. |
| SRV-05 | Planned | P3 | A Data Output's Write and a model save leave no staged copy beside their file, and no other writer's staging file outlives a restart. |
| SRV-06 | Planned | P2 | A pipeline file that is missing is shown on the editor's page in the words the load answers with, not as "HTTP <status>". |
| SRV-07 | Planned | P2 | A typed file name that Windows cannot hold is refused naming the path and the rule by the optimiser's save, the model save and a Data Output's Write, and a file with a device name can be deleted from the Files panel. |

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

### SRV-05 — A cut-short write and a model save leave no staged copy behind
**Why:** Reproduced on 10 November 2026. The pipeline save's own leftovers
are settled by its record (`src/haute/_save_records.py`): a save the server
died in is reported on its document, its staging files are removed when the
server starts and before the next save, and its removals are made by the
next save. Three other writers still leave theirs.

A Data Output's Write, run for CSV and for Parquet with the server killed as
the Write publishes its file, leaves its staged copy. The output itself stays
the old, whole file. Its staged copy, a full copy of the new output named
`.result.haute-stage-` and 32 hex digits, stays in the analyst's output
folder after a restart and after a later Write that succeeds. Nothing removes
it, the Files panel hides it as a dot-name, and each Write that is cut short
adds another.

A model save leaves a second kind, with no crash at all. Saved over a model
file that is marked read-only, the save succeeds and leaves the old model
beside the new one, named `.motor.cbm.`, 32 hex digits and `.backup`: one
more full copy at every save (run three times: three copies). The copy
step's own note says that "a backup that cannot be deleted then is logged
and left as a hidden sibling, because the copy has already committed". For
a read-only file that is every time, and nothing removes them later.

The shared atomic writer leaves a third kind when the server is killed at
its rename in any writer but the save: the sweep saw
`.haute/pipeline-settings.3b78e6a0.tmp` from a settings write, and
`.haute/trash.<hex>.tmp` and `.haute/state.<hex>.tmp` from an undelete and a
branch-away. Two writers already collect their own leftovers: the upload
route removes staging files older than the server's start, and the
assistant's session store prunes its own.

**Plan:** Before a Data Output's Write, remove the staged copies of the same
destination that an earlier Write left. Make a backup writable before
deleting it, so that a model save leaves none. When the server starts,
remove the atomic writer's staging files under `.haute` older than the
server's start, as the upload route does (`staging_files_of` in
`src/haute/_file_ops.py` names them for a target).

**Acceptance:** After a Write cut short, the next Write to that destination
leaves the output file alone in its folder. A model saved three times over a
read-only model file leaves the model and its contract alone in theirs. A
settings write killed at its rename leaves no staging file under `.haute`
after the next start.

**Dependencies:** None.

**Evidence:** `src/haute/_file_ops.py::atomic_path`;
`src/haute/_file_ops.py::staging_files_of`;
`src/haute/routes/project_files.py::_remove_stale_staging`;
`src/haute/executor.py::new_data_output_staging_path`;
`src/haute/executor.py::commit_prepared_data_output`;
`src/haute/_file_ops.py::atomic_copy_files`.

### SRV-06 — A missing pipeline file is shown on the page in the load's words
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

**Plan:** Show the load's own words on the page with the file's name instead
of an empty canvas and "HTTP <status>".

**Acceptance:** With the pipeline file deleted while the editor runs, the
page says the file's name and the remedy.

**Dependencies:** None. The deploy dry run's same resolver error is one
line and a hint since 20 November 2026.

**Evidence:** `frontend/src/api/client.ts`; the page's pipeline load in
`frontend/src`.

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

**Dependencies:** `SRV-04` for the wording of a write that fails.

**Evidence:** `src/haute/routes/optimiser.py::save_result`;
`src/haute/routes/modelling.py::save_model`;
`src/haute/executor.py::DataOutputPublicationError`;
`src/haute/_path_resolution.py::_reject_reserved_device_components`;
`src/haute/_artifact_paths.py::ordinary_path_problem`;
`src/haute/routes/project_files.py::_delete`;
`src/haute/_git_transactions.py::_pipeline_folder_new_files`;
`frontend/src/panels/optimiser/OptimiserPublishSection.tsx`.
