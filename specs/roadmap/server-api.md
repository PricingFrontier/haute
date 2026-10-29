# Server API roadmap

## Scope

The FastAPI application shell, the request/response contract every route
speaks, error translation, and the editor recovery and repair services.
Current behaviour is specified in
[the server-api specification](../server-api/high-level.md). These packages
come from the [23 September 2026 codebase review](codebase-review-2026-09-23.md).

## Priorities

| Package | State | Priority | Outcome |
|---|---|---:|---|
| API-R02 | Planned | P3 | Domain services raise domain errors; only routes speak HTTP. |
| API-R03 | Planned | P2 | Every browser type and response parser is generated from the Pydantic models. |
| API-R06 | Planned | P2 | A user-fixable error is answered as one: a graph the flattener refuses, a trace that fails in the user's node code, and the two 400s that carry the server-error text, with the trace's status chosen by type rather than by message prefix. |
| API-R08 | Planned | P2 | A save never round-trips through the file watcher, and its request is validated before any file is staged. |
| API-R09 | Planned | P3 | The repair routes, the admit-run-translate pattern, the path-resolution preludes and the two point-analysis services exist once each, and a save loads the pipeline once. |
| API-R10 | Planned | P3 | Every free string and list in a request is bounded; the dead response fields and helpers are gone; the repair message and the `file` field say what is true. |
| API-R11 | Planned | P2 | Editing an imported submodel under a nested configured pipeline reloads its importing documents. |

## Planned improvements

`API-R03` has the largest payoff: the hand-maintained contract files are the
most-changed files in the repository. Every route, the optimiser's included,
now leaves unexpected exceptions to the application handlers; `API-R02`
moves the services onto domain errors.

### API-R02 — Services raise domain errors
**Why:** Service modules raise FastAPI's `HTTPException` directly: the
optimiser service 67 times, the save service 40, the training lifecycle 35,
training preparation 15, and the job store's `require_job`. Background
threads raise it too; the optimiser's pipeline execution both records a job
failure and raises an HTTP 500. The assistant, a non-HTTP caller of the save
service, unwraps `HTTPException.detail` to recover messages.

**Plan:** Replace `HTTPException` in services with typed domain errors from
the `HauteError` hierarchy, mapped by the application exception handlers in
`routes/_error_handlers.py`. Background jobs record failures in the job store
and never raise HTTP types, which also removes the two conversions of an
`HTTPException` detail into a job failure record
(`_optimiser_service._http_error_job_update` and
`_training_preparation._http_failure_job_parts`).

**Acceptance:** No module outside `routes/` route handlers and the application
handlers imports `HTTPException`; the assistant catches domain errors, not
HTTP ones; route status-code tests pass unchanged.

**Dependencies:** None.

**Evidence:** `src/haute/routes/_optimiser_service.py::_execute_pipeline`;
`src/haute/routes/_save_pipeline.py`; `src/haute/routes/_training_lifecycle.py`;
`src/haute/routes/_training_preparation.py`; `src/haute/routes/_job_store.py`;
`src/haute/assistant/_tools.py::_error_message`.

### API-R03 — Generate the browser contract
**Why:** Three mechanisms keep the browser in step with the server. A
Pydantic → JSON Schema → TypeScript generator exists but covers two pilots
(execution-strategy diagnostics and Explore charts). Everything else is
written by hand: 233 `parse*` guards in `types/guards.ts` (4,073 lines),
`trainGuards.ts` (1,533) and `api/types.ts` (2,204), mirroring `schemas.py`
(3,973 lines, 284 classes), plus backend-written JSON fixtures that the
guards parse in contract tests. In the three months to 23 September 2026
`schemas.py`, `api/types.ts`, `guards.ts` and `api/client.ts` were the four
most-changed files in the repository.

**Plan:** The existing generator is extended (decided 24 September 2026):
`scripts/generate_api_contracts.py` emits the response models of each module
group in `RESPONSE_CONTRACT_GROUPS` as the server serializes them, and the Node
stage builds one lazily loaded validator module per group. Convert the
remaining groups the same way, replacing the hand-written types and structural
guards. Keep hand-written code only for cross-field rules that belong in the
UI. Retire the fixture parity tests once generated validators cover their
shapes.

**Delivered:** utility; Databricks listings; MLflow settings, destinations,
test connection and discovery lists; modelling GPU status, training estimate,
dispersion start and status, training MLflow log and model save; every git
success response; the training responses (`TrainResponse`,
`TrainStatusResponse`); Explore pivot run, status, cancel and
members, and the data profile; banding stats and rating levels; I/O
capabilities; the session check and file listing; editor identities, Polars
step rendering and execution settings; the git 409 advisory bodies
(`GitPushRejection`, `GitMilestoneFork`), whose storage-claim reader had no
caller left and is deleted; the optimiser responses (solve, estimate, status,
apply, save, MLflow log, frontier status, auto-range start and status, frontier
select).

**Remaining:** two sets. Responses that carry node configs or the editor
document come after `PCFG-R07`: pipeline load and save, preview, trace,
submodel, recovery and repair (`PipelineRepairApplyResponse.document` is a
`PipelineEditorDocument`), and JSON-cache inference (its tables merge into the
API Input config). The rest carry none and can go first: node data, cache,
JSON-cache status and input cache (the converted Explore, banding and rating
responses keep the node-data point and profile on their hand types until
then); output write, destination and assemble dry run; and the shared
execution-metrics parser, which the converted status responses still apply
after their generated check (each group's validator carries its own copy of
the metrics contract, about 13 KiB gzip; one shared execution-metrics
validator module would remove both).

**Acceptance:** Every response the client parses is validated by generated
code; `guards.ts`, `trainGuards.ts` and `api/types.ts` contain no structural
mirror of a Pydantic model; a stale generated file fails CI; a schema change
edits one Python model plus a regeneration.

**Dependencies:** None. It covers the response models that exist today, and
later packages build on it: `PCFG-R07` (pipeline config) adds the node-config
models to the generated set.

**Evidence:** `scripts/generate_api_contracts.py`;
`frontend/src/generated/api-contracts.schema.json`;
`frontend/src/types/guards.ts`; `frontend/src/types/trainGuards.ts`;
`frontend/src/api/types.ts`; `frontend/src/api/client.ts`;
`src/haute/schemas.py`; `tests/fixtures/ui_contracts`;
`frontend/src/types/__tests__/guards.contract.test.ts`.

### API-R06 — Client errors answered as client errors
**Why:** `flatten_executable_graph` refuses a client graph with a name violation or an
undeclared handle by raising `ParseError`, which the trace, write-output,
output-destination, OUTPUT dry-run, node-data, banding and rating routes call outside any
`except` and no application handler claims (`ParseError` is a `HauteError`, not a
`ValueError`), so a user-fixable graph error is answered with the sanitized 500
"Operation failed. Check the server logs for details."; save and preview handle it
route-locally. A failure in the user's own node code during a trace (a syntax error, a
missing column) reaches the client as the same 500, while the same failure in a preview
is embedded in `NodeResult.error`; two tests pin the 500. `GET /api/schema` and submodel
creation answer a 400 with the server-error text, so the status says the request was
wrong and the text says the server failed. The trace route chooses 409, 400 or 404 by
matching the text of a `ValueError`, with the prefix table duplicated for the worker form,
so a reworded message turns a 409 into a 500. This package comes from the
[25 October 2026 codebase review](codebase-review-2026-10-25.md).

**Plan:** Register one application handler `ParseError → 400` with the exception's
message and drop the route-local clauses; map a node failure the trace walk raises to 422
carrying the node id and `str(exc)`, as preview does, and change the two tests to assert
it; let the unexpected `ValueError` in `get_schema` and `create_submodel` reach the 500
backstop or map the known client errors to 400 with their own message; replace the trace
prefix table with typed exceptions carrying an `error_code`, mapped once by type and
carried through the worker's public payload.

**Acceptance:** A graph with two nodes labelled `Rates` and `rates` is answered 400 with
the collision message by every graph route; a trace of a node with a syntax error is
answered 422 naming the node; no route answers 400 with the internal-error text; the trace
route's status tests pass with the messages reworded.

**Dependencies:** `API-R02` moves the services onto domain errors; this package covers
the route-level mapping that exists today.

**Evidence:** `src/haute/routes/_error_handlers.py::install_exception_handlers`;
`src/haute/errors.py::ParseError`; `src/haute/routes/pipeline.py::trace_row`;
`src/haute/routes/output_assemble.py`; `src/haute/routes/node_data.py::_prepared`;
`src/haute/routes/banding.py`; `src/haute/routes/rating.py`;
`src/haute/routes/files.py::get_schema`; `src/haute/routes/submodel.py::create_submodel`;
`tests/test_trace_api.py`.

### API-R08 — A save never round-trips the watcher
**Why:** The `.haute.json` sidecar write (`save_sidecar` → `atomic_write_text`) and the
post-commit stale-config unlink register no self-write marker, so every save round-trips
through the file watcher as an external edit: the unmarked sidecar event files under the
owning pipeline as dependency-triggered, which skips the byte-hash dedupe, reloads the
document and broadcasts `pipeline.document.update` to every canvas, and a save that
retires a config file re-parses and broadcasts every pipeline in the project. The
specification says the server's own edits never round-trip. `active_source` is checked
against `sources` only by the sidecar model after the `.py`, module and config files were
staged, so a mismatch rolls everything back and surfaces as a pydantic error through the
500 backstop. The save route itself publishes no `pipeline.document.update` and returns
no document fingerprint, so a second tab of the same pipeline learns of a change only
when its own save is refused 409, and the saving tab's next WebSocket resync carries a
stale fingerprint (the browser half is `FSH-R05`). This package comes from the
[25 October 2026 codebase review](codebase-review-2026-10-25.md).

**Plan:** Mark the sidecar write (`mark_self_write(sidecar, content=bytes)` before the
write, or a `Writer`) and the stale unlink (`mark_self_write(path, deleted=True)`);
validate `active_source ∈ sources` on `SavePipelineRequest` (422) before any file is
staged; publish the saved document with its fingerprint from the save route to the other
clients and return the fingerprint to the saver. Carry the saving tab's socket
connection id with the save and exclude that connection from the route's broadcast;
retain the frontend fingerprint/snapshot guards for reconnects and in-flight saves.
Scope the exclusion to the same authorised session and pipeline. Add a watcher test that a save,
including one that removes a node, publishes no `pipeline.document.update` through the
watcher.

**Acceptance:** A save produces no watcher-originated document update; each other
connected tab for that pipeline receives one route-originated update with the saved
fingerprint. The saving tab applies no destructive self-echo in either HTTP/socket
arrival order and, with `FSH-R05`, retains jobs, undo history and newer dirty edits
across reconnect. A save retiring a config file triggers no watcher re-parse; a request
whose `active_source` is not in `sources` is answered 422 with no file touched.

**Dependencies:** Coordinate the server/client contract and two-tab tests with `FSH-R05`.

**Evidence:** `src/haute/routes/_helpers.py::save_sidecar`;
`src/haute/routes/_helpers.py::mark_self_write`;
`src/haute/routes/_save_pipeline.py::SavePipelineService._write_sidecar`;
`src/haute/routes/_save_pipeline.py::SavePipelineService._remove_stale_config_files`;
`src/haute/server.py`; `src/haute/schemas.py::SavePipelineRequest`; `tests/test_server.py`.

### API-R09 — One copy of each route mechanism
**Why:** The four repair-apply routes are near-identical (save lock, thread pool,
`PipelineRepairError` → structured JSON, `OSError` → 409), differing in a log event name
and one word; the admit → run-in-worker-or-thread → translate pattern with its thirty-line
exception ladder is copied for preview, trace, free-code columns and the OUTPUT dry run;
the submodel path-resolution call with its 400/403 pair appears three times and the
"normalise separators, refuse empty, absolute, `~` or `..`" prelude three times with three
message sets; the banding-statistics and rating-levels services duplicate their whole
skeleton (cache, consumer resolution, demand widening, missing-column handling) and
differ only in `_compute`, and both collect the whole grouped frame eagerly before taking
`.head(value_limit)`, so a high-cardinality text column materialises every distinct value
to answer a 500-row list. One save loads or parses the on-disk pipeline at least five
times, and removing a definition parses every other pipeline in the project. This package
comes from the [25 October 2026 codebase review](codebase-review-2026-10-25.md).

**Plan:** One `_apply_repair(fn, body, *, io_event, io_message)` helper; one
`run_interactive(...)` coroutine returning the result or raising one typed route error a
single ladder maps; one child-reference resolver and one relative-path prelude; a small
base for the two point-analysis services that pushes the sort and head into the lazy plan
and takes `distinct_count` from `n_unique()`; load the editor document and the strict
graph once at the top of `save()` and hand both to the steps.

**Acceptance:** jscpd reports none of the pairs; the route status tests pass unchanged;
a save parses the pipeline file once (a counting test).

**Dependencies:** None.

**Evidence:** `src/haute/routes/pipeline.py`; `src/haute/routes/_save_pipeline.py`;
`src/haute/routes/submodel.py`; `src/haute/routes/_banding_stats.py`;
`src/haute/routes/_rating_levels.py`; `src/haute/routes/output_assemble.py`.

### API-R10 — Bounded requests, honest fields
**Why:** Apart from `EditorIdentitiesRequest`, no request bounds its strings or lists:
no length on names, paths, sources, module names or contents, no size on `row_values` or
`factor`; a utility name of a few thousand characters passes the identifier check and
fails in the write as a 500. `UtilityWriteResponse` carries `error` and `error_line`
fields no route sets, yet the generated contract requires them and the client parses
them. `raise_validation_error` has no production caller, and the path-less cooldown mode
of the self-write tracker is written by the save and read by nothing but tests while the
specification still describes it. The repair routes' `OSError` answer states that the
original artifacts were restored, but rollback is best-effort and a failed restore is only
logged. `SavePipelineResponse.file` and `PipelineSummary.file` use OS-native separators
while every other project-relative path on the wire is POSIX. This package comes from the
[25 October 2026 codebase review](codebase-review-2026-10-25.md).

**Plan:** `max_length` on every free string that names a file, module, source or node and
a size cap on contents and value lists; remove the two dead fields and regenerate the
contract; delete `raise_validation_error`, the bare cooldown mode, its constant and the
specification sentence; word the repair detail as an attempt or thread the failed paths
into it; `.as_posix()` for the two `file` fields.

**Acceptance:** An over-long utility name is answered 422; the contract has no
`error_line`; the dead helpers are gone; a failed rollback is named in the repair
response; `file` is POSIX on Windows.

**Dependencies:** `API-R03` regenerates the contract.

**Evidence:** `src/haute/schemas.py::UtilityWriteResponse`;
`src/haute/schemas.py::EditorIdentitiesRequest`;
`src/haute/routes/_helpers.py::raise_validation_error`;
`src/haute/routes/_save_pipeline.py::_rollback_artifacts`;
`src/haute/routes/pipeline.py`; `src/haute/routes/utility.py`.

### API-R11 — Module watching follows the configured pipeline directory

**Why:** The watcher anchors modules at cwd/modules, while the configured pipeline and its
modules can be under rating/. With haute.toml naming rating/main.py, real discovery finds the
importer, but a modified rating/modules/factors.py event is classified as non-pipeline Python
and skipped. The measured module edit publishes zero documents; a parent-file event publishes
one. Existing tests chdir into the pipeline directory and miss this layout.

**Plan:** Resolve watched module locations through the same configured pipeline/module context
used by parsing and dependency discovery. Route added, modified and deleted imported modules
through dependency invalidation and publish only their importing documents, including nested
pipeline layouts.

**Acceptance:** From the project root, load a ready rating/main.py importing
rating/modules/factors.py, change its premium, deliver a watcher event and assert the refreshed
importer contains the change. Cover added/deleted modules and a root-level pipeline; unrelated
Python files still do not broadcast. Preserve self-write suppression and the save-echo
contract.

**Dependencies:** `API-R08` owns self-save echoes; share event classification without restoring those echoes.

**Evidence:** `src/haute/server.py::_file_watcher`; `src/haute/server.py::_file_watcher._flush_once`; `src/haute/server.py::_file_watcher._flush_once`; watcher tests in `tests/test_server.py`.
