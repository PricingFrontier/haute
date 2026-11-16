# Frontend shared roadmap

## Scope

The browser's shared code, as [the frontend shared
specification](../frontend-shared/high-level.md) describes it: which requests
the API client sends again by itself, what the editor takes a column's type
to be, and what a failed call says on the screen. `FES-01` comes from the
9 November 2026 codebase review. It is
read from the source on both sides and was not run against a dropped
connection. `FES-02` comes from the review plan's sweep of the frontend's
logic on 10 November 2026, which ran the client's rules and the server's on
the same cases, and was reproduced with a real Parquet file. `FES-03`
comes from the plan's browser sweep of failed calls on 11 November and the
main session's runs of that day in the editor and the underwriter app.

`FES-04` and `FES-05` come from a second reading on 16 November 2026. This
area's earlier, delivered roadmap was set beside this review's packages,
and the shared code the packages pass through was read. It names a cause
several packages here share, and is to be delivered before them.

## Priorities

| Package | State | Priority | Outcome |
|---|---|---:|---|
| FES-01 | Planned | P3 | A write that names the revision it replaces is never sent twice by the client. |
| FES-02 | Planned | P3 | A Float16 column is numeric to the editor's pickers, bands and steps, as it is to the server. |
| FES-03 | Planned | P3 | A call that got no answer, or an answer that is not Haute's, says that the server could not be reached, not the browser's or the JSON parser's own words, and is neither offered to the assistant as a node's error nor reported as a new version of Haute. |
| FES-04 | Planned | P2 | Every answer from the server is applied through one helper that drops it when what asked for it has changed. |
| FES-05 | Planned | P2 | Every value being edited in a panel lets go of its copy when an Undo moves the value under it. |

## Planned improvements

### FES-01 — A conditional write is not retried as if it were idempotent
**Why:** Read from the source. The client retries `GET`, `HEAD`, `PUT`,
`DELETE` and `OPTIONS` up to three times on a network error or any 5xx. A
`PUT` or `DELETE` that names the revision it replaces is not idempotent in
that sense: once the first attempt lands and only its answer is lost, the
retry names a revision that no longer exists and is refused. The workbench
form's save, the test-quote writes and a project file's delete each turn the
retry off by hand, the last with the comment "Never retried, as a write is
not". A utility module's save and its delete do not: both carry a revision
and both keep the default three retries. After a save that landed behind a
dropped answer or a proxy's 502, the panel reports that the module changed
since it was opened, over the analyst's own save. This needs a lost answer,
which is rare on a loopback server and less rare behind a hosting proxy.

**Plan:** Make the default follow the request, not the method: a request
that carries a revision or an operation key is sent once unless the caller
asks otherwise. Remove the per-call opt-outs the default then covers.

**Acceptance:** With the transport made to drop the first answer of a
utility save after the server applied it, the client sends one request and
reports the failure as a lost answer; no conflict is shown. A test lists
every `PUT` and `DELETE` wrapper and requires each to be either
unconditional or sent once.

**Dependencies:** None.

**Evidence:** `frontend/src/api/client.ts::IDEMPOTENT_METHODS` and
`frontend/src/api/client.ts::shouldRetry`;
`frontend/src/api/client.ts::updateUtilityFile` and
`frontend/src/api/client.ts::deleteUtilityFile` keep the default;
`frontend/src/api/projectFiles.ts::deleteProjectFile` and
`frontend/src/api/workbench.ts::TEST_QUOTE_CALL` turn it off;
`src/haute/routes/utility.py::_require_revision` refuses a revision that
moved.

### FES-02 — A Float16 column is a number to the editor, as it is to the server
**Why:** Reproduced on 10 November 2026. Polars 2 has a half-precision type,
`Float16`, and a Parquet file that pandas or pyarrow writes from a float16
column, as a memory "downcast" helper leaves one, is read as that type: a
Data Input's preview sends the editor `Float16` for the column. The server's
rules call the type a float and a number. The editor's two shared helpers do
not: one matches `Float32` and `Float64` only and answers that the type has
no family, the other that it is not numeric. They decide what the Banding
editor offers (categorical bands, for a numeric column), what the step
editor's cast and numeric steps accept, and which columns the pivot's and
the modelling panel's numeric pickers list, so a Float16 column is left out
of them with nothing said. A third helper, in the GLM terms editor, does
call the type continuous, so the modelling panel disagrees with itself. Of
the 38 Polars types the sweep compared on both sides, Float16 is the only
one where they part.

**Plan:** Add Float16 to the two helpers. Hold the client's list of types
against the server's with one shared case file (the text of every Polars
numeric type, and what each rule answers for it), read by both test suites,
so that the next type Polars adds fails a test instead of a picker.

**Acceptance:** `castFamily("Float16")` answers "float" and
`isNumericDtype("Float16")` answers true; a Float16 column is listed by the
pivot's and the modelling panel's numeric pickers and is offered breakpoint
bands; the shared cases fail when either side lacks a type the other knows.

**Dependencies:** None.

**Evidence:** `frontend/src/utils/polarsDtypes.ts::castFamily`;
`frontend/src/utils/polarsDtypes.ts::isNumericDtype`;
`frontend/src/panels/modelling/glmTerms.ts::glmDtypeClass`;
`src/haute/_polars_steps.py::cast_family`.

### FES-03 — A call that got no answer of Haute's says so in words
**Why:** Reproduced in a browser on 11 November 2026, in the editor and in
the underwriter app. When a call gets no answer, or an answer that is not
Haute's, the screen shows whatever text the failure happened to carry:

- No answer, the connection lost: the preview pane reads "Failed to fetch"
  and nothing else, a save "Failed to save pipeline: Failed to fetch", and
  the underwriter app's save "Failed to fetch". The words are the
  browser's.
- A sign-in page answered with 200, as a proxy may send once a session has
  run out: the preview pane reads
  `Unexpected token '<', "<!DOCTYPE "... is not valid JSON`, and a save
  the same after "Failed to save pipeline:". The words are the JSON
  parser's.
- A gateway's 502 or 504 page: "Bad Gateway" and "Gateway Timeout", the
  status line alone.
- A preview that got no answer is offered to the assistant as the node's
  fault: under "Failed to fetch" the pane shows "Ask the assistant to fix",
  and pressing it opens the assistant with "Fix the error this node raises
  when it runs." and the chip "Run error: enriched", for a node that raised
  nothing.
- A part of the page that is fetched on first use, with the connection away
  for two seconds as a node of a kind not yet opened is clicked: the node's
  panel reads "Haute has been updated" and "This page was opened before the
  update and cannot load this part of it. Reload to use the new version.",
  and still does ten seconds after the connection is back. Nothing was
  updated. The specification gives that fallback to a part the rebuilt
  server no longer has. Its test is the browser's message for a part that
  could not be fetched, which is the same when the server did not answer
  (seen on the development server).

None of them tells an analyst, or an underwriter, what happened: that the
server could not be reached, or that something between them and Haute
answered in its place, and that what they typed is still on the screen.
One helper makes every such message: it returns the server's detail when
there is one, and otherwise the thrown error's own text. A page load is
the exception and shows the pattern to follow: after its retries it says
"Haute could not open this pipeline" and explains (run: 12 seconds of
"Loading pipeline...", then the message).

**Plan:** In the one helper, name the three cases before falling back to
the error's text: a request that got no answer (the fetch's own failure
or a timeout), an answer that is not JSON where JSON was due, and a status
of 502, 503 or 504 without a detail of Haute's. Say that the server could
not be reached, or that the answer did not come from Haute, with the
status when there is one, and keep the error's own text for the console.
Offer "Ask the assistant to fix" only under an error the node raised. For a
part of the page that could not be fetched, tell a file the server no longer
has from a server that did not answer, and for the second say so and offer
to try again.

**Acceptance:** With the connection cut, a preview and a save in the editor
and a save in the underwriter app each say that the server could not be
reached, and none shows "Failed to fetch". With a sign-in page answered
with 200, and with a gateway's 502 page, each says that the answer did
not come from Haute. A detail the server sends is shown as it is today.
A preview that got no answer shows no "Ask the assistant to fix". With the
connection cut as a panel is first opened, the panel says that the server
could not be reached and loads once it is back, without a page reload; with
the part's file gone it says "Haute has been updated" as today.

**Dependencies:** None.

**Evidence:** `frontend/src/api/errors.ts::apiErrorMessage`;
`frontend/src/hooks/usePipelineAPI.ts::usePipelineAPI`;
`frontend/src/app/useOpenQuoteStore.ts`;
`frontend/src/panels/DataPreview.tsx`;
`frontend/src/components/AskAssistantButton.tsx`;
`frontend/src/utils/chunkLoadError.ts::isChunkLoadError`;
`frontend/src/components/ErrorBoundary.tsx`.

### FES-04 — An answer is applied only to what asked for it
**Why:** Read on 16 November 2026. The editor has a fence for answers that
arrive late: a caller captures the document's state before a request and
checks it after. It was an earlier roadmap's work, and where it is used no
finding of this review falls. It is used by choice. Of 105 source files
that wait for an answer, 12 use the fence, 8 use the optimiser's
latest-request hook, and 22 keep a counter or a cancelled flag of their
own. The files behind this review's late-answer findings use none of them:
`App.tsx`, the utility panel, the repair dialog, the catalog selector, the
MLflow export section and the test-quotes store (`FEG-10`, `FEP-04`,
`FEN-01`, `MLR-10`, `WB-61`). The fence was also built for execution
answers; an answer that writes to the document or to a panel, such as a
repair, a catch up, a delete or imported values (`VCS-10`, `FEN-10`), has
no shared equivalent. A new panel is correct only if its author remembers.

**Plan:** One request helper that every call goes through when its answer
will be written to state. It records what the answer belongs to: the
document, the node, the panel and the edit it was asked under. A late
answer is dropped, or handed back marked stale for the caller to settle.
Replace the 22 hand-kept guards with it. Add a test that lists the files
which write state after waiting without the helper, and only lets the list
shrink, to none.

**Acceptance:** One test drives every surface that calls the server: it
holds the answer, changes what it belongs to (another document, the node
deleted, an Undo, text typed), lets the answer go, and requires the page to
show the current state. The list of files outside the helper is empty. The
journeys of `FEG-10`, `VCS-10`, `FEP-04`, `FEN-01`, `FEN-10`, `WB-61` and
`MLR-10` pass.

**Dependencies:** None. Deliver before the packages its acceptance names.

**Evidence:** `frontend/src/stores/useDocumentStatusStore.ts`;
`frontend/src/panels/optimiser/useLatestRequest.ts`; `frontend/src/App.tsx`;
`frontend/src/panels/UtilityPanel.tsx`.

### FES-05 — A panel's unsaved value follows the document through Undo
**Why:** Read on 16 November 2026. Panels and node components keep their
own state in 399 `useState` calls across 98 files, and no shared hook ties
a value being edited to the store's history. An earlier roadmap put each
editor's draft in step with the document one editor at a time. Undo
restores the store and a panel goes on showing, and sending, its own copy:
the search space box (`FEM-03`), the optimiser's estimate (`FEM-04`), the
Output editor's preview (`FEN-08`), a Banding factor's remembered rules
(`FEN-09`) and "Save node" (`FEG-08`). Each was found by pressing Undo in
one panel; the panels nobody pressed it in are built the same way.

**Plan:** One hook for a value being edited. It is keyed by the node and by
the store's place in its history, lets go of its copy when an Undo, a Redo
or a change from outside moves the value under it, and commits as one undo
step. A request a panel derives, such as an estimate or a preview, is built
from the store's value, never from the copy. Move the editors to the hook,
and add a test that lists the panels holding a copy without it and only
lets the list shrink.

**Acceptance:** One test drives every editor field that holds a copy: it
types, undoes a change upstream, and requires the field to show the store's
value and the next request to carry it. The journeys of `FEM-03`, `FEM-04`,
`FEN-08`, `FEN-09` and `FEG-08` pass.

**Dependencies:** None. Deliver before the packages its acceptance names.

**Evidence:** `frontend/src/panels/ModellingConfig.tsx`;
`frontend/src/panels/OptimiserConfig.tsx`;
`frontend/src/panels/editors/OutputEditor.tsx`;
`frontend/src/components/form/CommittedTextField.tsx`.
