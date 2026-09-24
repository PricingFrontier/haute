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
| API-R01 | Planned | P2 | The optimiser and JSON-cache routes join the application exception handlers. |
| API-R02 | Planned | P3 | Domain services raise domain errors; only routes speak HTTP. |
| API-R03 | Planned | P2 | Every browser type and response parser is generated from the Pydantic models. |

## Planned improvements

`API-R03` has the largest payoff: the hand-maintained contract files are the
most-changed files in the repository. The application exception handlers
from `API-R01` are in place; what remains of it sits in files owned by the
optimiser and caching work and can land with `API-R02`.

### API-R01 — Translate errors once at the application edge
**Why:** The application exception handlers exist (`routes/_error_handlers.py`:
public contract errors, memory refusals and overruns, the `GitError`
family; `_RequestIdMiddleware` answers anything unclaimed with
`_INTERNAL_ERROR_DETAIL`), and the git, pipeline, files, Databricks,
modelling and OUTPUT dry-run routes no longer catch `Exception` to log it
and answer 500. Files owned by the optimiser and caching work were left
alone: `routes/optimiser.py` still has five catch-log-500 blocks (one also
cleans up an orphaned apply artifact first), `routes/json_cache.py` two, and
`_optimiser_service._memory_limit_http_exception` is a second copy of the
memory-limit mapping. Turning an `HTTPException` detail into a job failure
record is still implemented twice, in `_optimiser_service._http_error_job_update`
and `_training_preparation._http_failure_job_parts`.

**Plan:** Delete the remaining catch-log-500 blocks, keeping the frontier
apply cleanup and re-raising after it. Replace the optimiser's memory-limit
helper with `memory_limit_http_exception(exc, operation_noun="Auto-range")`.
Fold the job-failure-record duplication into `API-R02`, where background
jobs stop carrying HTTP types at all.

**Acceptance:** No route contains a generic `except Exception` that only logs
and returns the internal-error detail; `memory_limit_http_exception` is the
only memory-limit mapping; the optimiser and JSON-cache status-code and
sanitised-error tests pass unchanged.

**Dependencies:** None; best taken with `API-R02` in the optimiser work.

**Evidence:** `src/haute/routes/optimiser.py` (`apply_lambdas`,
`run_frontier`, `select_frontier_point`, `save_result`,
`_materialise_frontier_point_apply`); `src/haute/routes/json_cache.py`
(`build_json_cache`, `infer_json_cache_schema`);
`src/haute/routes/_optimiser_service.py::_memory_limit_http_exception`;
`src/haute/routes/_optimiser_service.py::_http_error_job_update`;
`src/haute/routes/_training_preparation.py::_http_failure_job_parts`.

### API-R02 — Services raise domain errors
**Why:** Service modules raise FastAPI's `HTTPException` directly: the
optimiser service 67 times, the save service 40, the training lifecycle 35,
training preparation 15, and the job store's `require_job`. Background
threads raise it too; the optimiser's pipeline execution both records a job
failure and raises an HTTP 500. The assistant, a non-HTTP caller of the save
service, unwraps `HTTPException.detail` to recover messages.

**Plan:** Replace `HTTPException` in services with typed domain errors from
the `HauteError` hierarchy, mapped by the `API-R01` handlers. Background jobs
record failures in the job store and never raise HTTP types.

**Acceptance:** No module outside `routes/` route handlers and the application
handlers imports `HTTPException`; the assistant catches domain errors, not
HTTP ones; route status-code tests pass unchanged.

**Dependencies:** `API-R01`.

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
success response.

**Remaining:** the training responses (`TrainResponse`, `TrainStatusResponse`,
best taken with `MOD-T10`, whose browser semantic re-checks would otherwise be
rewritten only to be deleted); optimiser, including its MLflow log (still on
the hand-written `parseMlflowLogResponse`); pipeline load and save, preview,
trace and submodel responses (they carry node configs, so after `PCFG-R07`);
recovery and repair; node data, cache, JSON cache and input cache; Explore
pivot and profile; output write, destination and assemble dry run; I/O
capabilities; banding stats and rating levels (they embed the node-data point);
session bootstrap and file listing; editor identities; Polars step rendering;
execution settings; and the git 409 advisory bodies (`GitPushRejection`,
`GitMilestoneFork`) with the storage-claim reader.

**Acceptance:** Every response the client parses is validated by generated
code; `guards.ts`, `trainGuards.ts` and `api/types.ts` contain no structural
mirror of a Pydantic model; a stale generated file fails CI; a schema change
edits one Python model plus a regeneration.

**Dependencies:** None. It covers the response models that exist today, and
later packages build on it: `PCFG-R07` (pipeline config) adds the node-config
models to the generated set, and `MOD-T10` (modelling) then reduces the
tuning and evaluation response models to structure.

**Evidence:** `scripts/generate_api_contracts.py`;
`frontend/src/generated/api-contracts.schema.json`;
`frontend/src/types/guards.ts`; `frontend/src/types/trainGuards.ts`;
`frontend/src/api/types.ts`; `frontend/src/api/client.ts`;
`src/haute/schemas.py`; `tests/fixtures/ui_contracts`;
`frontend/src/types/__tests__/guards.contract.test.ts`.
