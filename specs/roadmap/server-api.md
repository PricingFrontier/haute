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
| API-R01 | Planned | P2 | Exceptions are translated to HTTP responses once, by application handlers. |
| API-R02 | Planned | P3 | Domain services raise domain errors; only routes speak HTTP. |
| API-R03 | Planned | P2 | Every browser type and response parser is generated from the Pydantic models. |
| API-R04 | Decision | P3 | The editor recovery and repair subsystem is scoped to what hand-editing actually needs. |

## Planned improvements

`API-R03` has the largest payoff: the hand-maintained contract files are the
most-changed files in the repository. `API-R01` is mechanical and can go
first; `API-R02` follows it. `API-R04` is a product decision.

### API-R01 — Translate errors once at the application edge
**Why:** No FastAPI exception handler is registered. Instead, 53 route-level
blocks repeat `except Exception: log; raise HTTPException(500,
_INTERNAL_ERROR_DETAIL)`, 29 of them nearly identical in the git routes, and
the internal-error text is referenced 88 times. `_memory_limit_http_exception`
is defined three times, and turning an `HTTPException` detail into a job
failure record is implemented twice.

**Plan:** Register application exception handlers for unexpected exceptions,
the `HauteError` families that carry safe messages, the public contract
errors, and admission and memory-limit errors. Delete the per-route
catch-log-500 blocks and the duplicated helpers, keeping route-specific
handling only where a route genuinely maps an error differently.

**Acceptance:** The application registers the handlers; routes contain no
generic `except Exception` that only logs and returns the internal-error
detail; the sanitised-error, status-code and request-id tests pass
unchanged; one memory-limit mapping remains.

**Dependencies:** None.

**Evidence:** `src/haute/server.py`; `src/haute/routes/git.py`;
`src/haute/errors.py`; `src/haute/routes/pipeline.py::_memory_limit_http_exception`;
`src/haute/routes/_training_preparation.py::_memory_limit_http_exception`;
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

**Plan:** Extend the existing generator, or adopt an OpenAPI-based one, to
emit types and runtime validators for every response model, and replace the
hand-written types and structural guards module by module. Keep hand-written
code only for cross-field rules that belong in the UI. Retire the fixture
parity tests once generated validators cover their shapes.

**Acceptance:** Every response the client parses is validated by generated
code; `guards.ts`, `trainGuards.ts` and `api/types.ts` contain no structural
mirror of a Pydantic model; a stale generated file fails CI; a schema change
edits one Python model plus a regeneration.

**Dependencies:** `PCFG-R07` (pipeline config) supplies node-config models to
generate from; `MOD-T10` (modelling) moves semantic checks out of the
response models.

**Evidence:** `scripts/generate_api_contracts.py`;
`frontend/src/generated/api-contracts.schema.json`;
`frontend/src/types/guards.ts`; `frontend/src/types/trainGuards.ts`;
`frontend/src/api/types.ts`; `frontend/src/api/client.ts`;
`src/haute/schemas.py`; `tests/fixtures/ui_contracts`;
`frontend/src/types/__tests__/guards.contract.test.ts`.

### API-R04 — Scope the editor recovery subsystem
**Why:** Because the `.py` file is the source of truth and may be hand-edited
anywhere, the editor keeps working on broken files through about 6,100
backend lines: `_pipeline_recovery.py` (2,316 lines; `_build_recovery_graph`
is 368 lines with complexity 77), `_pipeline_repair.py` (1,003),
`_pipeline_repair_actions.py` (858), `_node_config_recovery.py` (727), a
second, regex-based parser for syntax-invalid files (894), and recovery
sources and submodel recovery. Around it sit degraded and source-only
document states, recovery previews, and plan-hash dry-run/apply repairs.

**Plan:** Decide what hand-editing the product supports. One option: users
hand-edit node bodies and the preamble; anything else that breaks the file
shows the parse error with an "open in editor" action and no recovered
canvas. Specify the chosen scope, then remove the recovery states, repair
actions and regex parser that fall outside it.

**Acceptance:** The server-api specification states the supported
hand-editing scope and what the editor shows for a file outside it; code
outside that scope is removed with its tests; loading, saving and live sync
of well-formed files are unchanged.

**Dependencies:** `PCFG-R01` (pipeline config) should not be built further
if this decision removes the repair it improves.

**Evidence:** `src/haute/_pipeline_recovery.py::load_pipeline_editor_document`;
`src/haute/_pipeline_repair.py::build_remove_unavailable_node_plan`;
`src/haute/_pipeline_repair_actions.py::build_recovery_action_plan`;
`src/haute/_node_config_recovery.py::reconcile_config`;
`src/haute/_parser_regex.py`; `tests/test_pipeline_recovery.py`.
