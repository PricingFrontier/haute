# Server API roadmap

## Scope

The local server's routes and live sync, as [the server API
specification](../server-api/high-level.md) describes them: what a route
answers when a node of the analyst's own fails, and what a save answers when it cannot write its files.
`SRV-06`
comes from the main session's own runs of 10 November. `SRV-07`
comes from the plan's sweep of damaged requests and the main session's
runs behind it on 11 November. `SRV-19` and `SRV-20` come from the 22 November 2026
sweep of `src/haute` for the cause `XPL-03` removed from Explore, a temporal
value read into Python; the [temporal values root cause](root-cause-temporal-values.md)
is taken before them. `SRV-20`
also holds the deployed app's JSON answers and requests, whose own roadmap
is retired.

## Priorities

| Package | State | Priority | Outcome |
|---|---|---:|---|
| SRV-06 | Planned | P2 | A pipeline file that is missing is shown on the editor's page in the words the load answers with, not as "HTTP <status>". |
| SRV-19 | Planned | P1 | An OUTPUT document keeps two objects whose keys differ below a microsecond, in the preview and in the deployed answer, and carries every digit of a nanosecond timestamp. |
| SRV-20 | Planned | P3 | The node preview, the file preview and the deployed app's answers show a nanosecond value's nine digits, and a request's date-time with more than six digits is read whole or refused, never cut short. |
| SRV-07 | Planned | P2 | A typed file name that Windows cannot hold is refused naming the path and the rule by the optimiser's save, the model save and a Data Output's Write, and a file with a device name can be deleted from the Files panel. |

## Planned improvements

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
path and the reason. Let the Files panel delete any file it
lists, whatever its name.

**Acceptance:** Each name above, typed into each of the three paths,
answers 4xx naming the path and the rule, and the folder holds no new file
afterwards. A file with a device name that is already in the pipeline
folder can be deleted from the Files panel, and Commit then succeeds.

**Dependencies:** None. The length rule is `_file_ops.written_file_length_problem`.

**Evidence:** `src/haute/routes/optimiser.py::save_result`;
`src/haute/routes/modelling.py::save_model`;
`src/haute/executor.py::DataOutputPublicationError`;
`src/haute/_path_resolution.py::_reject_reserved_device_components`;
`src/haute/_artifact_paths.py::ordinary_path_problem`;
`src/haute/routes/project_files.py::_delete`;
`src/haute/_git_transactions.py::_pipeline_folder_new_files`;
`frontend/src/panels/optimiser/OptimiserPublishSection.tsx`.

### SRV-19 — An OUTPUT document keeps objects that differ below a microsecond
**Why:** Reproduced on 22 November 2026 with `assemble_output_from_mapping`.
A frame holds policy `P1` with two events of amount 5, one at
09:00:00.000000100 and one at 09:00:00.000000200, in a `Datetime("ns")`
column, the unit pandas writes to Parquet. The mapping `$[:].policy`,
`$[:].events[:].at`, `$[:].events[:].amount` gives one policy with one
event, at 09:00:00, with nothing said. The assembler reads the rows with
`iter_rows` into Python datetimes, which hold microseconds, and groups an
array's objects by their own values (`_group_rows`, `_index_rows`). The
two events become one object, in the OUTPUT node's preview and in the
deployed answer alike. A preview's row limit filters the child rows by
`is_in` of the parent keys read through Python (`_limit_level_plan`), which
misses a key with digits below a microsecond. Every timestamp the document
carries is cut to six digits.

**Plan:** Key an object's identity and the preview's parent filter by the
column's own value, through `IOL-08`'s shared mechanism, not a Python
`datetime`; filter child rows by a semi-join on the parent keys, as the
many-key case already does. Write a temporal leaf with all its digits.
Update the JSON shredding specification first.

**Acceptance:** The reproduction above gives one policy with two events
whose `at` carries nine digits. A preview limited to one parent keeps both
children of a parent whose key has nanoseconds.

**Dependencies:** `IOL-08` (the [temporal values root cause](root-cause-temporal-values.md)) first.

**Evidence:** `src/haute/_output_assembler.py::_rows_from_dataframe`;
`src/haute/_output_assembler.py::_group_rows`;
`src/haute/_output_assembler.py::_index_rows`;
`src/haute/_output_assembler.py::_limit_level_plan`;
`specs/json-shredding/low-level.md`.

### SRV-20 — Previews and JSON answers carry a nanosecond value's digits
**Why:** Read on 22 November 2026, and the request reproduced. The node
preview grid reads its rows with `to_dicts` (`executor.py::_frame_rows`),
the file preview with `rows_to_json_safe(preview_df.to_dicts())`, and the
deployed app's answers and its impact and batch-scoring rows through Python
values, and each writes `isoformat()`: a pandas-written timestamp of
09:00:00.000000100 shows as 09:00:00. A request's date-time is read by
`_json_values._datetime` with `datetime.fromisoformat`, which on Python
3.11 drops digits past the sixth without refusing:
`2024-03-01T09:00:00.000000100` reads as 09:00:00.

**Plan:** Write each preview's and answer's temporal columns through
`IOL-08`'s `temporal_text` before the rows are read, and read a request's
date-time through the shared literal builder, refusing a value finer than
its column holds. Update the server API and deploy specifications first.

**Acceptance:** A node preview, a file preview and a deployed answer of a
`Datetime("ns")`, a `Time` and a `Duration("ns")` value show nine digits;
a request carrying nine digits reaches the pipeline whole, and one finer
than its column is refused naming the field.

**Dependencies:** `IOL-08` (the [temporal values root cause](root-cause-temporal-values.md)) first.

**Evidence:** `src/haute/executor.py::_frame_rows`;
`src/haute/_json_safe.py`; `src/haute/routes/files.py`;
`src/haute/deploy/_response_json.py`; `src/haute/deploy/_batch_scoring.py`;
`src/haute/deploy/_impact.py`; `src/haute/deploy/_schema.py`;
`src/haute/_json_values.py::_datetime`;
`specs/deploy/low-level.md`.
