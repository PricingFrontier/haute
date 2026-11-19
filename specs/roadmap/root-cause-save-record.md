# Root cause: a save known only to the server's memory

## Scope

A pipeline save whose progress and whose outcome are held in the server
process alone, and a browser that treats every write alike: a save that
landed is answered or read as one that failed, and a save cut short leaves
a mix nobody is told of.

This file is one pull request. It holds one package, `SRV-18`, which names
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
| `SRV-03` | [server-api](server-api.md) | A save whose files are written answers as saved when clearing a stale settings file fails | Resolves |
| `FEG-04` | [frontend-graph-canvas](frontend-graph-canvas.md) | After a save whose answer never came, the next Save repeats it under the same operation | Resolves |
| `FES-01` | [frontend-shared](frontend-shared.md) | A write that names the revision it replaces is never sent twice by the client | Resolves |
| `SRV-05` | [server-api](server-api.md) | A save the server died in is reported at the next load, and no staging file reaches version history | In part: the staging files of the Data Output's Write and the model save are its own |

## Planned improvements

### SRV-18 — A save's progress and outcome are on disk, and the browser sends each write by its kind
**Why:** Read on 19 November 2026; the browser's call sites were read and
not run. Three things about a pipeline save are held in the server
process's memory and nowhere else.

What it is doing. A save replaces the pipeline file, each node's settings
file and the layout sidecar in turn, and the bytes a rollback needs are
in memory (`_TouchedFile.previous_bytes`). A server that dies part-way
leaves two saves' files mixed and a `.tmp` file beside them. The pipeline
loads as ready, and the next Commit takes the leftovers into version
history (`SRV-05`).

That it committed. The route records a save as committed only after the
service returns. Once its files are written the service removes the
settings files the graph no longer uses, and only then captures the save
in version history and builds its answer. A failure in the removal, after
every file is written, leaves the service before either and answers the save as failed, and the removal is never tried again
because the graph on disk no longer names the file (`SRV-03`).

What it answered. The server answers a repeat of a committed save, under
the same operation key, with that save's answer, from a list of the
newest saves kept in memory (`SaveOperations`). A restart empties it.

The browser has the matching fault. Haute's writes are of two kinds. A
keyed write carries an operation key the server keeps an answer for, and
can be repeated safely: the pipeline save. A conditional write carries
only the revision it replaces: the utility file's save and delete, the
workbench form's save. Repeating one that landed is refused, rightly,
since its revision is gone. The client does not tell the kinds apart. It
retries by method, a `PUT` or a `DELETE` three times, so a conditional
write whose first answer was lost is refused on the retry (`FES-01`);
three callers turn that off by hand and two do not. And the one keyed
write is repeated once, at once, after a network failure and then let go;
after a 500 it is not repeated at all. The next Save is a new operation
on a revision the lost save already replaced, and the editor reads its
own save as someone else's change and locks the tab (`FEG-04`). The
underwriter app keeps such a save and repeats it first.

`SRV-03` and `SRV-05` plan two records under `.haute`, and `FEG-04` and
`FES-01` two rules in the client, each for its own case.

**Plan:** Two pieces in one pull request, because each is tested against
the other.

On the server, two records under `.haute` with two lifetimes.

- A record of a save in progress, written before the first file is
  replaced: its operation key, the revision it replaces, and every file it
  will write or remove. It is marked committed once the files are written
  and parsed back, and from then the save answers as committed whatever
  the removals do. It is removed when the removals are done and captured
  in version history. A record found at a load or before the next save is
  acted on. A committed one has its removals finished and captured. An
  uncommitted one is reported as a diagnostic of the document naming its
  files, and its staging files are removed and kept out of the milestone's
  sweep. It reports the mix; it does not put the earlier document back,
  which would need the earlier bytes kept on disk and a rule for a file
  edited since, and `SRV-05` asks for the report.
- A receipt of each committed save: its operation key, the digest of its
  request and its answer. It outlives the record above and a restart, and
  the newest are kept to the number the in-memory list keeps now. A
  repeat is looked up there before the file is read, as the route does
  today.

In the browser, the client sends a write by its kind and no caller
chooses.

- A conditional write, any request that carries a revision and no
  operation key, is sent once. A lost answer is reported as a lost
  answer.
- A keyed write is sent through one call that keeps the request with its
  key when the answer does not come, or is a 500 or unreadable, and
  repeats it before anything newer is sent, until an answer below 500
  arrives. A request whose content changes takes a new key. The editor's
  save uses it, as the underwriter app's does.
- The retry by method is left to reads. The per-call opt-outs go.

Giving a conditional write a key and a receipt of its own is a separate
change for whichever write needs it. Update the server API specification
before the change.

**Acceptance:** A test fails each step of a save in turn, and kills the
server at each, and requires one of two states at the next load: the save
committed, or a diagnostic of the document naming the interrupted save's
files. No staging file is left and none reaches a Commit. A removal that
fails leaves the save answered as saved, and the next save removes the
file and captures the removal. A test kills the server after a save's
removals are done and before its answer is sent, restarts it, repeats the
save under its key, and requires the first save's answer and no conflict.
A browser test drops the answer to a save, and answers it 500, and
requires the next Save to land with the work typed since and no read-only
tab. A test drops the answer to a utility file's save and requires one
request sent. A test fails when a request carrying a revision or an
operation key is sent outside the client's two paths. The acceptance of
`SRV-03`, `FEG-04` and `FES-01` is met.

**Dependencies:** None. Deliver before the packages its table names.
`FEG-05` covers the locked tab reached by a change on disk and is not
resolved here.

**Evidence:** `src/haute/routes/_save_pipeline.py::SavePipelineService._save`;
`src/haute/routes/_save_pipeline.py::_remove_stale_config_files`;
`src/haute/routes/_save_operations.py::SaveOperations`;
`src/haute/routes/pipeline.py::save_pipeline`;
`src/haute/routes/utility.py::_require_revision`;
`src/haute/_file_ops.py::atomic_path`;
`frontend/src/api/client.ts::IDEMPOTENT_METHODS`;
`frontend/src/hooks/usePipelineAPI.ts::isLostAnswer`;
`frontend/src/app/useOpenQuoteStore.ts`;
`frontend/src/api/projectFiles.ts`.
