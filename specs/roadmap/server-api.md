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
`TrainStatusResponse`), with `MOD-T10`; Explore pivot run, status, cancel and
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
