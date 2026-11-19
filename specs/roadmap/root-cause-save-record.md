# Root cause: a save with no record

## Scope

A pipeline save that replaces its files one after another with nothing
written down about it, and a browser that cannot tell whether a write it
sent has landed: a save that landed is answered or read as one that
failed, and a save cut short leaves a mix nobody is told of.

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
| `MLR-10` | [mlflow-model-registry](mlflow-model-registry.md) | Retry after the experiment was changed logs to the new experiment | In part: the server's refusal of a repeated key with another request is its own |

## Planned improvements

### SRV-18 — A save is recorded on the server and has one fate in the browser
**Why:** Read on 19 November 2026; the browser's call sites were not run.
A save replaces the pipeline file, each node's settings file and the
layout sidecar in turn, holding what a rollback needs in memory. It then
removes the settings files the graph no longer uses, and only after that
does the route record the save as committed. Nothing on disk says a save
is under way. So a failure in the removal, after every file is written,
answers the save as failed and is never retried, because the graph on disk
no longer names the file (`SRV-03`). A server that dies part-way leaves
two saves' files mixed and a `.tmp` file beside them, loads as ready, and
the next Commit takes the leftovers into version history (`SRV-05`). The
two packages plan two records under `.haute`, one of files a save could
not remove and one of a save in progress.

The browser has the other half. The server answers a repeat of a committed
save, under the same operation key, with that save's answer. The editor
repeats once, at once, after a network failure, and then lets the key go;
after a 500 it does not repeat at all. The next Save is a new operation on
a revision the lost save already replaced, and the editor reads its own
save as someone else's change and locks the tab (`FEG-04`). The underwriter
app keeps such a save and repeats it first. Underneath, the client retries
by method: a `PUT` or a `DELETE` three times, though one that names the
revision it replaces is refused the second time (`FES-01`). Three callers
turn that off by hand and the utility file's save and delete do not. A
kept key is also sent with a changed request, and the stored answer comes
back for it (`MLR-10`).

**Plan:** Two pieces, one on each side, in one pull request because each
is tested against the other.

On the server, one record of a save under `.haute`: written before the
first file is replaced, naming the operation key, the revision replaced
and every file the save will write or remove; marked committed once the
files are written and captured; and removed when the removals are done.
The save answers from the record, so a save whose files are written is
committed whatever the removals do. A record found at load or before the
next save is acted on: a committed one has its removals finished, an
uncommitted one becomes a diagnostic of the document naming its files, and
its staging files are collected and kept out of the milestone's sweep.

In the browser, one call for a keyed write. A request that carries a
revision or an operation key goes through it and is never retried by
method. It is sent once; when its answer does not come, or is a 500 or
unreadable, the call keeps the request with its key and repeats it before
anything newer is sent, until an answer below 500 arrives. A request whose
content changes takes a new key. The editor's save, the utility file's
save and delete, the workbench form's save, the test-quote writes and the
MLflow log use the call, and the per-call opt-outs go. Update the server
API specification before the change.

**Acceptance:** A test fails each step of a save in turn, and kills the
server at each, and requires one of two states at the next load: the save
committed with its answer, or the document as it was with a diagnostic
naming the files. No staging file is left and none reaches a Commit. A
browser test drops the answer to a save, and answers it 500, and requires
the next Save to land with the work typed since and no read-only tab. A
test holds that no request carrying a revision or an operation key is
sent outside the one call. The acceptance of `SRV-03`, `FEG-04` and
`FES-01` is met.

**Dependencies:** None. Deliver before the packages its table names.
`FEG-05` covers the locked tab reached by a change on disk and is not
resolved here.

**Evidence:** `src/haute/routes/_save_pipeline.py::SavePipelineService._save`;
`src/haute/routes/_save_pipeline.py::_remove_stale_config_files`;
`src/haute/routes/_save_operations.py`; `src/haute/routes/pipeline.py::save_pipeline`;
`src/haute/_file_ops.py::atomic_path`;
`frontend/src/api/client.ts::IDEMPOTENT_METHODS`;
`frontend/src/hooks/usePipelineAPI.ts::isLostAnswer`;
`frontend/src/app/useOpenQuoteStore.ts`;
`frontend/src/api/projectFiles.ts`.
