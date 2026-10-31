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
| API-R03 | Planned | P2 | Every browser type and response parser is generated from the Pydantic models. |
| API-R04 | Planned | P2 | `output_assemble_dry_run` preserves 422 domain errors across the process-mode worker pool and sets `graph.source_file`, `get_schema` keys Parquet metadata reads on `reader.format`, `NodeDataService._fail_job` formats memory-limit errors cleanly, `project_files` creates destination parent directories inside `project_write`, and `parsePreviewNodeResponse` preserves `notes` until `API-R03` converts preview responses. |

## Planned improvements

`API-R03` has the largest payoff: the hand-maintained contract files are the
most-changed files in the repository. Every route, the optimiser's included,
leaves unexpected exceptions to the application handlers, and the services
raise domain errors those handlers answer.

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
members, the data profile and target relationships; banding stats and rating
levels, with the node-data point they carry; node data (point, run, status,
clear) and the cache inventory, usage and clear; the input cache's build, job,
cancel, status and clear, an API Input's tables included; the output write, its
destination and the OUTPUT dry run; I/O
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
API Input config). The rest carry none and can go first: the shared
execution-metrics parser, which the converted status responses still apply
after their generated check (each group's validator carries its own copy of
the metrics contract, about 13 KiB gzip; one shared execution-metrics
validator module would remove both). That needs the Node stage to compile a
definition once, in its own module, and leave it an object in every group
that carries it, with the client checking the field through that module;
the hand parser stays for the preview response until the second set, and the
metrics type the eager diagnostics read carries the execution-strategy
diagnostic's normalised shape, which the move must keep.

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

### API-R04 — Process-mode 422 domain error mapping in `output_assemble_dry_run`, format-keyed Parquet metadata reads in `get_schema`, node-data memory error formatting, `project_files` destination mkdir locking, and `parsePreviewNodeResponse` `notes`
**Why:** Five route error-translation, filesystem-staging and response-guard defects
affect `output_assemble_dry_run`, `get_schema`, `NodeDataService`, `project_files`
and `/api/pipeline/preview`:
1. In `output_assemble_dry_run` (`POST /api/output-assemble/dry-run`),
   `_execute_output_assemble_dry_run_worker` runs in the interactive worker pool by
   default (`process` mode) after the upfront `validate_v2_output_mapping` check.
   While node-level `OutputMappingSchemaError` / `ConfigError` failures (such as an
   unwired port on a multi-frame input) are recorded by `walk_graph._recordable` into
   `NodeResult(status="error")` and returned as
   `OutputAssembleDryRunResponse(status="error")`, run-level contract failures at node
   boundaries raise `ContractColumnsMissingError` or `ContractMismatchError`
   (`_graph_walker.py:1893`), which propagate out of `execute_graph`. In `thread` mode,
   `except (OutputMappingSchemaError, ConfigError, ContractMismatchError) as exc:` in
   `output_assemble_dry_run` catches `ContractMismatchError` (and its subclass
   `ContractColumnsMissingError`) and returns HTTP 422; in default `process` mode, the
   worker pool wraps the exception in
   `InteractiveWorkerRemoteError(remote_module=..., remote_type=..., remote_message=...)`,
   and `_raise_interactive_remote_http_error` (`pipeline.py`) checks
   `_TRACE_CONTRACT_REMOTE_IDENTITIES` (`ContractMismatchError`,
   `ContractColumnsMissingError`, `SchemaMismatchError`) only when
   `operation == "pipeline_trace"`, turning `ContractColumnsMissingError` and
   `ContractMismatchError` in `output_assemble_dry_run` into HTTP 500 instead of 422.
   In addition, `output_assemble_dry_run` calls `_validate_runtime_input_paths(graph)`
   without calling `_ensure_source_file(graph)` first.
2. In `files.py`, `_read_schema_blocking` / `get_schema` (`GET /api/schema`) gates
   `read_parquet_metadata(target)` (which populates `row_count`) on
   `if target.suffix.lower() == ".parquet":` (`files.py:198`) rather than the resolved
   reader format (`reader.format == "parquet"`), so `GET /api/schema?format=parquet`
   for a Parquet file without a `.parquet` suffix omits `row_count` (and a non-Parquet
   file with a `.parquet` suffix read with `format=csv` still calls
   `read_parquet_metadata` and fails with `ArrowInvalid` HTTP 500).
3. In `NodeDataService._fail_job` (`_node_data_service.py`), when
   `ExecutionAdmissionError` or `ExecutionMemoryLimitExceededError` is raised in the
   parent thread of a node-data build or profile job, `_fail_job` assigns
   `payload = exc.to_payload()` (a `dict`) and passes `message=str(payload)` and
   `"error": str(payload)` to `self._lifecycle.transition(...)`, exposing a raw Python
   dict repr (`"{'error_code': 'memory_limit', ...}"`) in `NodeDataStatusResponse.message`
   and `.error` instead of `memory_limit_user_message(exc, operation_noun=label)`.
4. In `project_files.py`, while upload staging already uses `_staging_path(project_root)`
   under `.haute/uploads/`, `_plan_write` calls
   `target.path.parent.mkdir(parents=True, exist_ok=True)` on the destination directory
   before the upload body is streamed and outside `project_write` (`save_lock`),
   leaving empty destination directories behind on rejected uploads
   (`400`/`409`/`412`/`413`) and failing in `_publish_write` if a concurrent operation
   removes the empty destination directory before `_publish_write` enters
   `project_write`.
5. `parsePreviewNodeResponse` in `frontend/src/types/guards.ts` constructs a new
   `NodeResult` return object without copying `notes: optionalStringArray("parsePreviewNodeResponse", obj, "notes")`,
   silently stripping `notes` from every `/api/pipeline/preview` response before
   `usePipelineAPI` reads `result.notes` (an interim fix until `API-R03` converts
   preview responses to generated validators).

**Plan:** Call `_ensure_source_file(graph)` before `_validate_runtime_input_paths(graph)`
in `output_assemble_dry_run` and preserve the typed `InteractiveWorkerRemoteError`
boundary in `_raise_interactive_remote_http_error` by mapping a dedicated operation
allowlist of the four exact `(exc.remote_module, exc.remote_type)` identities
(`ContractMismatchError`, `ContractColumnsMissingError`, `OutputMappingSchemaError` and
`ConfigError`) to HTTP 422 (`detail=exc.remote_message`) when
`operation == "output_assemble_dry_run"`; in `_read_schema_blocking` / `get_schema`,
gate `read_parquet_metadata(target)` on `reader.format == "parquet"` rather than
`target.suffix`; format `ExecutionAdmissionError` and `ExecutionMemoryLimitExceededError`
in `NodeDataService._fail_job` with `memory_limit_user_message(exc, operation_noun=label)`;
defer `target.path.parent.mkdir` in `project_files.py` into `_publish_write` under
`project_write` while keeping upload staging via `_staging_path(project_root)` under
`.haute/uploads/`; and include `notes` in `parsePreviewNodeResponse` until `API-R03`
converts preview responses.

**Acceptance:** In `process` mode, `POST /api/output-assemble/dry-run` on a graph where
the OUTPUT mapping or an upstream node triggers `ContractColumnsMissingError` or
`ContractMismatchError` inside `_execute_output_assemble_dry_run_worker` returns HTTP
422 with `exc.remote_message` through the typed `InteractiveWorkerRemoteError`
`(remote_module, remote_type)` boundary (matching `thread` mode, while a node-level
unwired-port failure on a multi-frame input continues to return
`OutputAssembleDryRunResponse(status="error")`), and resolves pipeline-relative paths
when `graph.source_file` is omitted; `GET /api/schema` populates `row_count` via
`read_parquet_metadata` when `format=parquet` is passed for a file without a `.parquet`
extension and skips `read_parquet_metadata` when `format=csv` is passed for a
`.parquet`-named CSV file; a parent-thread memory admission error in `NodeDataService`
records a human-readable message rather than a dict repr; a rejected upload in
`project_files` creates no destination directory on disk; and
`parsePreviewNodeResponse` preserves `notes`.

**Dependencies:** Coordinates with `API-R03` for `parsePreviewNodeResponse`.

**Evidence:** `src/haute/routes/output_assemble.py::output_assemble_dry_run`;
`src/haute/routes/output_assemble.py::_execute_output_assemble_dry_run_worker`;
`src/haute/routes/pipeline.py::_raise_interactive_remote_http_error`;
`src/haute/routes/files.py::_read_schema_blocking`;
`src/haute/routes/files.py::get_schema`;
`src/haute/routes/_node_data_service.py::NodeDataService._fail_job`;
`src/haute/routes/project_files.py::_plan_write`;
`src/haute/routes/project_files.py::_publish_write`;
`frontend/src/types/guards.ts::parsePreviewNodeResponse`.
