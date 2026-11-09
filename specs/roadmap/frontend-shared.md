# Frontend shared roadmap

## Scope

The browser's API client, as [the frontend shared
specification](../frontend-shared/high-level.md) describes it: which requests
it sends again by itself. The package below comes from the 9 November 2026
codebase review. It is read from the source on both sides and was not run
against a dropped connection.

## Priorities

| Package | State | Priority | Outcome |
|---|---|---:|---|
| FES-01 | Planned | P3 | A write that names the revision it replaces is never sent twice by the client. |

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
