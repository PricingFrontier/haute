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

## Priorities

| Package | State | Priority | Outcome |
|---|---|---:|---|
| FES-01 | Planned | P3 | A write that names the revision it replaces is never sent twice by the client. |
| FES-02 | Planned | P3 | A Float16 column is numeric to the editor's pickers, bands and steps, as it is to the server. |
| FES-03 | Planned | P3 | A call that got no answer, or an answer that is not Haute's, says that the server could not be reached, not the browser's or the JSON parser's own words. |

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

**Acceptance:** With the connection cut, a preview and a save in the editor
and a save in the underwriter app each say that the server could not be
reached, and none shows "Failed to fetch". With a sign-in page answered
with 200, and with a gateway's 502 page, each says that the answer did
not come from Haute. A detail the server sends is shown as it is today.

**Dependencies:** None.

**Evidence:** `frontend/src/api/errors.ts::apiErrorMessage`;
`frontend/src/hooks/usePipelineAPI.ts::usePipelineAPI`;
`frontend/src/app/useOpenQuoteStore.ts`.
