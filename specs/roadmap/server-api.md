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
