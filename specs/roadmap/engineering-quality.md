# Test coverage and workflow assurance roadmap

## Scope

Expand assurance of the **current supported product** by testing complete user
operations, their boundaries, and their state transitions. Engineering-quality
owns this test programme; each product component retains ownership of its
behaviour and must specify any correction before tests or production code change.
This is an implementation plan, not a statement that the tests or fixes exist.

Planning baseline: `06c377e1fb0c5643d5c2bc781de73044daacdbb1`, 5 September 2026.
The checkout was clean. The earlier review was stored outside Git. Its
[bug findings](bug-findings-2026-09-05.md) and
[test-gap analysis](test-gap-findings-2026-09-05.md) now accompany this plan.
The original two Python probe modules, TypeScript rename probe and raw logs
remain local review artifacts, not maintained repository tests.
Those records report eight reproduced runtime defects (F1–F4, F9–F12), four
specification conflicts (F5–F8), nine failing probe cases and one passing control.
Ten neighbouring backend tests and two frontend tests passed on that same
snapshot. These are prior execution results, not a new full-suite run.
The package descriptions below preserve the actionable inputs without requiring
access to those original local artifacts. Reproduce each defect on the implementation
checkout; source inspection or a historical failure log is not current red evidence.

“All workflows and edge cases” means every documented supported action and
invariant has an explicit coverage disposition, with executable witnesses for
its applicable boundary and transition classes. It cannot mean enumerating all
possible programs, datasets, schedules, or provider behaviour. Coverage percentages
and file-name inventories do not establish semantic completeness.

### Coverage rules

- Inventory all 35 component pairs, both supplemental specifications, the current
  19 node types, and each supported operation/mode. Include file-only, browser,
  CLI, hosted, and scoring entry points; do not require a browser test for a
  capability that has no browser UI.
- For each operation record: precondition, user action, success result, rejected
  input, empty/minimum/maximum boundaries, applicable interruption/retry/restart
  transitions, durable state, and visible feedback. Mark a dimension inapplicable
  with a reason instead of constructing a meaningless Cartesian product.
- Keep one smallest decisive witness for each contract. Reuse existing tests
  that already prove it; extend existing parameterisation before adding files.
  Add a cross-component witness where isolated tests omit the actual handoff.
- In a workflow witness, keep the decisive dependency real: filesystem and save
  service for Save; parser and executor for rename; utility loading and cache
  identity for repeated jobs; Git repositories for restore. Stub external SDKs,
  clocks, and event delivery at their boundary, with explicit operation ordering.
- A result assertion must inspect values, disk bytes, branch/ledger identity,
  authoritative generation, or rendered state. Call counts, labels, successful
  status codes, valid generated signatures, and absence of private attributes
  are insufficient substitutes for those outcomes.
- Preserve per-test isolation. Put successive jobs, clients, edits, and restarts
  inside one test when their shared state is the subject. Use barriers/events
  with bounded waits and cleanup; do not rely on sleeps or favourable scheduling.
- Keep current format validation strict. Unsupported operations remain explicit
  failures. Planned EDA capabilities and optimiser process isolation do not become
  required successful workflows through this programme.

## Priorities

| Package | State | Priority | Outcome |
|---|---|---|---|
| ENG-T01 | Planned | P1 | Define the workflow inventory, evidence records, and minimal shared fixtures. |
| ENG-T02 | Reverify | P1 | Protect edit/save/reload and external-change workflows; detect F9 and F10. |
| ENG-T03 | Reverify | P1 | Keep computation and cache identity consistent across operations; detect F12. |
| ENG-T04 | Decision | P1 | Make the node execution boundary testable and truthful; detect F1. |
| ENG-T05 | Decision | P1 | Prove publication ordering at the authoritative pointer; detect F2. |
| ENG-T06 | Reverify | P2 | Preserve the selected published branch across restore; detect F4. |
| ENG-T07 | Reverify | P1 | Conserve or reject every authored connection; detect F3. |
| ENG-T08 | Reverify | P2 | Preserve executable meaning through rename and graph editing; detect F11. |
| ENG-T09 | Planned | P2 | Reconcile F5–F8 against executable boundary contracts. |
| ENG-T10 | Planned | P2 | Cover every supported workflow family across product components. |
| ENG-T11 | Planned | P2 | Add bounded state-machine, differential, and boundary generation. |
| ENG-T12 | Planned | P2 | Enforce collection, regression sensitivity, and sustainable CI cost. |

## Planned improvements

### ENG-T01 — Establish the executable coverage inventory

**Why:** The existing suite is substantial, but a test near an implementation
does not prove the composed workflow. The review probes are outside pytest
discovery, and existing tests sometimes replace precisely the boundary at risk.

**Plan:**

1. Start a versioned `tests/workflow_coverage.toml` and a small validator in an
   existing repository-health test module, or a focused new module if that is
   clearer. Define the format in engineering-quality specs first. Use stable
   workflow/invariant IDs, component/spec anchors, applicable entry points,
   scenario IDs, exact pytest node IDs or Playwright/Vitest titles, test tier,
   real/stubbed boundaries, CI lane, and evidence state. Execution evidence names
   the tested commit, exact command, result and negative control where applicable;
   a green result from another snapshot is not silently carried forward. Store no duplicate prose
   defect registry and no time-based expiry rule.
2. Evidence states are `covered`, `gap`, `decision`, and `not-applicable`.
   `covered` requires a reviewed outcome assertion and collected executable
   test reference; `gap`/`decision` require an owning active package;
   `not-applicable` requires the reason and contract reference. Collection can
   establish existence and scheduling, not assertion quality. Validate unknown
   components, missing IDs, duplicate scenario IDs, stale links and references,
   and silently skipped/focused tests. Keep the semantic judgment in review.
3. Bootstrap all components and workflow families below, then implement the P1
   witnesses immediately. Completing the whole semantic inventory must not block
   a small regression. Read each component's high/low behaviour and invariants
   when populating its records; an extracted Testing section alone is insufficient.
4. Reuse `tests/conftest.py`, the storage test transport and temporary Git fixtures,
   and `frontend/e2e/projectIsolation.ts`. Extract a shared helper only after a
   second genuine consumer needs it. Add helpers for snapshots of owned file
   bytes, controlled event interleavings, and fresh-process restore only where
   existing utilities cannot express the test clearly.
5. Remove absolute local paths and sibling-test-module imports when promoting
   review probes. F11 must execute the current frontend update path during the
   test; a frozen `rename-result.json` from the review cannot prove the current UI.

**Acceptance:** Every component and node type has a coverage disposition; no
workflow is called covered solely because its test file exists. The eight defect
IDs map to the packages below. The ledger is runnable from a fresh checkout and
new unmapped supported actions require a reviewed coverage record. The validator
has small malformed/missing/duplicate-reference tests. This is an index and gate,
not a new generic workflow execution framework.

**Dependencies:** None. Add the owning test-system specification contract before
implementing the ledger. Product decisions are owned by the corresponding package.

**Evidence:** `specs/README.md`; `specs/corpus.toml`; `tests/test_docs_accuracy.py`;
`tests/test_test_debt.py`; `tests/conftest.py`; `frontend/e2e/projectIsolation.ts`;
`scripts/spec_corpus_inventory.py`.

### ENG-T02 — Edit, save, observe external changes, and recover

**Why:** F9 serialises Save requests without checking their base revision, so a
valid external edit can be overwritten. F10 records self-writes by path/time,
so a later external write to the same path can be suppressed. Existing response
ordering, lock-spy, and different-path watcher tests miss the persisted result.

**Plan:** Extend the existing stale-revision precondition to Save rather than
designing a new one. `pipeline_document_revision` in
`src/haute/_pipeline_revision.py` already digests the canonical graph, the parent
source and its `.haute.json` sidecar, and every submodel child source and
sidecar; it is returned by load and save responses, required as `base_revision`
by submodel create and dissolve, and required as `source_revision` by recovery
preview, each rejecting a mismatch with 409 before any mutation. Amend
server-api, pipeline-config and frontend-shared contracts so `SavePipelineRequest`,
the inline request type in `frontend/src/api/client.ts` and the payload built in
`frontend/src/hooks/usePipelineAPI.ts` carry that token, and the route checks it
inside the save transaction before mutation, returning
the structured 409 conflict that recovery preview already uses (code, message,
expected and provided revision). Specify initial creation separately, UI
recovery, and whether recovery artifacts, which feed the separate recovery
revision, join the Save precondition. Specify how external writes during
a multi-file commit are detected and handled; an application lock alone cannot
promise filesystem-wide atomicity. Self-write suppression must identify committed
content, with an explicit deletion representation, failed-write and rollback
lifecycle.

Extend real route/persistence tests and watcher tests with these scenario groups:

| Sequence | Decisive assertions |
|---|---|
| Load A; external valid B; save A-based edit | Conflict; every B-owned artifact byte preserved; no new save-ledger commit or publish acknowledgement. |
| Two clients load A; first saves B; delayed second saves C | Exactly one fresh save; stale client gets conflict; B remains on disk. Reverse the arrival order as a separate case. |
| Fresh base; ordinary save; save response arrives after newer UI edit | Correct disk generation and returned revision; newer unsaved UI edit stays dirty. |
| Only child source, config, sidecar or recovery artifact changes | The document revision changes where the specified ownership requires it; a stale write cannot overwrite that artifact. |
| Delete/recreate target; malformed external edit; failed commit partway through | Explicit conflict/error; no mixed generation; recovery retains user work and coherent revision. Test identical recreation according to content-revision semantics. |
| Server write then external replace/modify/delete on same path before coalesced event | Update describes final disk state; unchanged server-only control is suppressed. |
| Failed server write or rollback followed by external edit | Marker does not hide external work; duplicate events do not erase dirty browser state. |

Keep the actual Save service and disk writes in the witness. Drive the watcher
with an injected event iterator and real classification, then assert the parsed
broadcast revision/content. Add one two-page browser Save conflict workflow with
real routes and a temporary project: conflict is visible, the local unsaved edit
survives, and explicit reload/reapply permits a subsequent fresh save. Do not add
an implicit overwrite retry. Separately cover disconnect/reconnect retrieving
the current document when an event was missed.

Replace the misleading `TestConcurrentSaveAndPreview` assertions in
`tests/test_partial_failure.py`: two raw `Path.write_text` calls are not a Save
test; absence of a `_lock` attribute is not a concurrency invariant; checking a
self-write marker is not proof of preview cache invalidation. Replace with a
real overlapping Save/preview operation and output/revision assertions, or remove
redundancy when the owning modules already prove the contract.

**Acceptance:** The stale-save and same-path cases fail on the baseline for lost
bytes/missing notification, then pass with the correction. Fresh-save, true
self-write, and conflict-recovery controls pass. HTTP contracts, generated
frontend types/validators, request builder, persistence and UI agree. No broad
exception assertion can pass merely because a fixture or import is broken.

**Dependencies:** ENG-T01 seed; root defines the revision/creation/transaction
contract before code. Generated API contract changes are atomic across stacks.

**Evidence:** `src/haute/schemas.py::SavePipelineRequest`;
`src/haute/routes/pipeline.py::save_pipeline`; `src/haute/_pipeline_revision.py`;
`src/haute/routes/submodel.py`; `src/haute/routes/_save_pipeline.py`;
`frontend/src/api/client.ts`; `frontend/src/hooks/usePipelineAPI.ts`;
`src/haute/routes/_helpers.py`;
`src/haute/server.py`; `tests/test_save_pipeline_integrity.py`;
`tests/test_pipeline_revision.py`; `tests/test_save_lock_contract.py`;
`tests/test_partial_failure.py`; `tests/test_server.py`;
`frontend/src/hooks/__tests__/usePipelineAPI.test.ts`;
`frontend/src/hooks/__tests__/usePipelineAPI.gaps.test.ts`;
`frontend/e2e/core-flows.spec.ts`.

### ENG-T03 — Pin dependencies once per operation and refresh the next operation

**Why:** F12 uses a process-lifetime no-refresh namespace for independent
optimiser jobs. A helper edit changes the dataframe-cache fingerprint while
computation still uses old values. The preview refresh test uses another caller;
the optimiser cache test mocks the compiler and builders.

**Plan:** Specify operation-scoped dependency snapshots in execution-engine,
caching and optimiser before implementation. At operation admission resolve the
namespace and dependency identity together; reuse that snapshot throughout its
chunks. A later operation resolves a fresh snapshot. Define mid-operation edits
as either completing the pinned snapshot or explicitly superseding it, never
publishing values under a different dependency identity.

- In one test, create `utility.py` with value 10; materialise real optimiser
  inputs; change the helper to 200; preview; start another optimiser setup.
  Assert preview and second input equal 200, the first equals 10, and retained
  cache provenance matches the executed snapshot. No solver is needed for this
  minimal regression; do not patch the compiler, builders or cache-key logic.
- Cover each no-refresh call site at the planning baseline: `_execute_pipeline`
  and `_run_streaming_frontier_auto_range_job` in
  `src/haute/routes/_optimiser_service.py`, and `prepare_data_output` in
  `src/haute/executor.py`. Every other `_compile_preamble` caller uses the
  default refresh. Give each of the three an operation-scoped disposition and
  witness, covering separate estimate and solve admissions, streaming
  auto-range and repeated data-output preparation; do not assume one caller
  fixes them all.
- Cover unchanged helper cache reuse, transitive helper edits, helper deletion
  and syntax errors, two project roots with the same module name, and changed
  preamble text. Preserve the documented source-fingerprint policy when choosing
  same-size/timestamp cases; do not assert a stronger freshness guarantee silently.
- Hold a multi-chunk operation at a checkpoint while editing its helper; assert
  no mixed old/new rows and no incorrectly labelled cache entry. Cancellation,
  failure and the next successful operation must release state and use the
  correct snapshot. Clear module/cache state in teardown, not between the two
  operations that form the regression.

**Acceptance:** The real repeated-setup test fails with 10 instead of 200 on the
baseline and passes after correction. Reuse controls remain effective. Every
no-refresh call site has evidence for the lifetime it assumes. One small real
solver integration subsequently checks that the changed input reaches the result.

**Dependencies:** ENG-T01 seed; root-owned snapshot semantics. No dependency on
the optimiser extraction or process-isolation roadmaps.

**Evidence:** `src/haute/executor.py::_compile_preamble`;
`src/haute/routes/_optimiser_service.py`; `tests/test_executor.py`;
`tests/test_optimiser_routes.py`; `tests/test_optimiser_routes_real_library.py`;
`tests/test_data_io_nodes.py`; `tests/test_streaming_chunk_size_threading.py`.

### ENG-T04 — Verify the actual node execution boundary

**Why:** F1 shows that restrictions on builtins and explicit path helpers do not
constrain all capabilities reachable through the actual injected Polars objects.
The existing permissive executor fixture cannot prove project containment.

**Plan:** First make a root-owned design decision on the node-text trust
boundary for each supported local/hosted execution mode. Two outcomes are
legitimate. Either keep the untrusted-node contract and enforce it outside the
Python object graph, with a separately privileged process and explicit
filesystem, credential, process and network confinement, or declare node text
trusted first-party code in that mode, state it in sandbox-security and the
execution UX, and keep the AST/builtins layer as an accident guard only.
In-process Python holding the full Polars module cannot be contained, so a
same-privilege child process or an additional attribute denylist is not evidence
of containment. Specify allowed data operations, project reads/writes,
environment exposure, and the enforcement boundary for the chosen outcome. If a
supported platform cannot enforce a containment contract, report that limitation
and gate the dependent implementation; never change the contract implicitly to
make tests green.

Promote the two local review witnesses into the owning tests using a temporary
project, an outside-project sentinel and a synthetic environment marker. Exercise
the real production node entry point, not just `validate_project_path`. Verify
that the forbidden operation fails and the sentinel/marker remains protected,
while ordinary permitted Polars transformations still work. Keep the test data
synthetic and all writes inside the test's temporary parent directory. Do not
read real credentials or contact external services.

Add applicable path variants (relative, absolute, traversal and supported
symlinks), permitted data IO controls, and cleanup after cancellation/failure.
Test each materially distinct execution host; distinguish privileged preambles
from restricted node text explicitly. Run these witnesses without the fixture
that widens the execution root to the whole filesystem. Test-runner write guards
must not intercept the call in place of the production enforcement being tested.

**Acceptance:** Current witnesses fail at the missing runtime restriction before
the change. Under a containment outcome, corrected execution rejects the
operation for the specified reason, leaves the sentinel unchanged, and still
computes the permitted control result. Under a trusted-code outcome, the
sandbox-security and execution-engine specifications and the execution UX state
the trust boundary explicitly, the witnesses are re-scoped as accident-guard
regressions, and hosted mode records its own enforcement decision and lane.
Any platform qualification required by the design has an explicit CI lane;
unsupported enforcement cannot count as a skipped passing capability.

**Dependencies:** ENG-T01 seed; a recorded execution-boundary design in
sandbox-security and execution-engine. The initial reproductions can run before
the decision; implementation and completion depend on it.

**Evidence:** `src/haute/_user_exec.py`; `src/haute/_sandbox.py`;
`tests/test_sandbox.py`; `tests/test_user_exec_imports.py`;
`tests/test_worker_isolation.py`; `tests/conftest.py`;
`specs/sandbox-security/high-level.md`.

### ENG-T05 — Publish one authoritative generation under competing writers

**Why:** F2 injects a competing UC pointer after the final read and before the
unconditional upload. Publication reports success while replacing the competitor.
The existing race test injects before the final read and therefore passes.

**Plan:** Preserve the deterministic in-memory Files API transport; add explicit
hooks around bundle upload, final pointer read, and pointer commit. Keep the real
publisher. Enumerate competitor arrival before final read, after final read,
initial empty-pointer publication, and predecessor resumption after claim takeover.
Record a history of attempts, outcomes and authoritative generations; assert
that every acknowledged commit has a valid ordering and a stale writer cannot
overwrite the winning generation. Test rejection preserving local saved work,
failure before/after commit, and retry after an ambiguous transport response.

Before selecting a fix, verify the actual storage API's available atomic
conditional-update or enforced-writer primitive. Check create-only upload first:
if a per-generation object written with overwrite disabled is honoured
atomically by the volume, the authoritative head becomes the highest committed
generation object rather than a mutable pointer, and the post-read window
disappears. Record the chosen capability and supported failure semantics in
hosted-project-storage. A fake must model the real API, not invent
compare-and-swap support. Another read or a longer advisory lease cannot close
the interval. If the provider cannot supply the required primitive, choose and
specify a supported publication authority before declaring the guarantee
satisfied. A provider contract qualification must use an isolated test resource
when implementation reaches that step.

**Acceptance:** The post-read interleaving fails on the baseline by detecting the
overwritten pointer. All critical orderings preserve the accepted winner or
surface an explicit conflict under the chosen contract. Local saves and retry
state remain usable; no success message is inferred only from bundle existence.
Unit evidence and provider capability evidence are reported separately.

**Dependencies:** ENG-T01 seed; root-owned storage capability/design decision.
The deterministic regression does not wait for remote credentials; provider
qualification is required to claim the production atomicity guarantee.

**Evidence:** `src/haute/_uc_transport.py`; `src/haute/_project_storage.py`;
`tests/test_project_storage.py`; `specs/hosted-project-storage/low-level.md`.

### ENG-T06 — Save, change branch, publish, and restore a fresh session

**Why:** F4 restores the branch captured at binding instead of the branch most
recently published. An original-branch-only restart test cannot distinguish them.

**Plan:** Specify durable branch selection and its relationship to a successfully
published generation, including populated binds, unpublished local changes, branch
removal and failed publication. Extend temporary Git/UC lifecycle fixtures:
bind on A; select B; edit/save distinct rows on B; publish; discard clone-local
state; restore in a fresh clone/session. Check the active working branch,
corresponding save ledger, source bytes, parsed graph and computed result.

Run the sequence for UC and a local bare Git remote. Add populated bind followed
by selection, a failed publish that must not advertise an unavailable restart
target, missing/renamed branch handling, restore twice, and missing Git identity
followed by explicit identity setup and save retry. Follow the specified branch
recovery behaviour rather than accepting any branch with matching file contents.

**Acceptance:** Baseline UC test restores A/old data and fails; corrected UC and
Git cases restore B and the correct save posture. No process-global binding cache
or leftover `.haute` state makes a supposed restart pass. A browser test verifies
the branch/recovery indication while backend tests own process replacement.

**Dependencies:** ENG-T01 seed; ENG-T05 for concurrent publication guarantees.
The ordinary branch-restart regression can be implemented independently.

**Evidence:** `src/haute/_project_storage.py`; `tests/test_project_storage.py`;
`tests/test_hosted.py`; `frontend/e2e/git-graph.spec.ts`;
`frontend/e2e/git-sidebar-regression.spec.ts`.

### ENG-T07 — Parse, flatten and save without losing authored graph intent

**Why:** F3 lets parent connections name private child nodes, then drops those
connections without a diagnostic. Valid generated round trips cannot generate
that illegal raw-source case. Pipeline-config prose also conflicts with the
public-interface parser/codegen contract.

**Plan:** Reconcile pipeline-config, expression-parsing, codegen and submodels:
parent endpoints are root nodes or registered occurrence aliases with valid
public ports. Every authored edge is represented, with identity preserved, or
rejected explicitly before a save can erase it.

Add raw-source cases to parser conservation/submodel tests: private child as
source, as target, child-to-child, nested child, and a shared definition used by
two aliases. Include missing alias/port, duplicate identity, and legal root/public
port controls. Assert contextual `ParseError` and no file mutation, not merely
that the smaller returned graph remains a DAG. For valid inputs assert the
ordered edge/handle identities through parse, flatten, generate and reparse,
plus equal computed rows for a tiny multi-file graph.

**Acceptance:** The original two private-child edges produce a targeted rejection
on corrected code and are silently absent on baseline, making the regression
red there. Legal public interfaces and repeated definition occurrences continue
to execute. Recovery UI retains the invalid source and exposes the diagnostic.

**Dependencies:** ENG-T01 seed; root resolves the contradictory endpoint prose
before tests. No private-endpoint compatibility or repair fallback is added.

**Evidence:** `src/haute/_parser_conservation.py`;
`src/haute/_parser_submodels.py`; `tests/test_parser_conservation.py`;
`tests/test_parser_submodels.py`; `tests/test_codegen_roundtrip_property.py`;
`tests/test_codegen_execution_equivalence.py`; `tests/test_pipeline_recovery.py`.

### ENG-T08 — Preserve execution through rename and graph editing

**Why:** F11 changes edge input names and structured mappings but leaves
downstream Python referring to the old binding. UI tests check metadata and
the related persistence browser test returns a synthetic successful preview.

**Plan:** Define rename as a transaction over identities and all consumers.
The root must choose a canonical stable-binding design or a Python-aware
reference refactor and document ambiguous-reference rejection before code changes.
Do not use textual replacement, temporary old-name aliases, or migration paths.

Use the real frontend rename action and real backend execution in a minimal
connected graph: preview known rows; rename source; preview again; save; reload;
preview again. Compare rows, schema and input identity at every step. Extend
ordinary-node and API-frame rename coverage with multiple consumers, instances,
public submodel ports, sanitised-name collisions, and nested scopes. Names in
strings/comments/attributes and shadowed local variables must not be incorrectly
rewritten. An ambiguous edit must fail before graph/code/history mutation with
an actionable error. Undo/redo must restore executable behaviour as one edit.

Keep focused mocked identity tests, but remove preview stubbing from the new
execution witness. Reuse core-flow and frame-persistence browser fixtures; backend
semantic cases should carry the larger scope/collision matrix at lower cost.

**Acceptance:** The simple rename sequence fails with an unbound old input on
the baseline and succeeds with equal rows after the correction. Invalid/colliding
renames preserve the old graph and undo history. All supported rename entry points
map to a collected test, including frame labels and submodel interfaces.

**Dependencies:** ENG-T01 seed; root-owned rename contract. ENG-T07 supplies the
submodel interface invariant; ENG-T02 supplies conflict-safe persistence.

**Evidence:** `frontend/src/utils/nodeUpdatePlan.ts`;
`frontend/src/hooks/useGraphCommitController.ts`;
`frontend/src/__tests__/App.integration.test.tsx`;
`frontend/e2e/core-flows.spec.ts`;
`frontend/e2e/persistence/api-input-frame-alignment.spec.ts`;
`tests/test_codegen_input_identity.py`; `src/haute/_user_exec.py`.

### ENG-T09 — Reconcile shared boundary contracts and their witnesses

**Why:** F5–F8 are prose contradictions, not four more reproduced runtime bugs.
Mechanical documentation checks were green despite them. Tests should protect
the existing correct behaviour while specifications adopt one owner per contract.

**Plan:** Review the current assertions below and extend only missing outcomes:

| Finding | Spec correction and executable obligation |
|---|---|
| F5 | Execution-engine must reference assistant-owned post-save verification tiers. Executable edits require schema evidence; non-executable edits may be structural. Schema failure cannot be reported as successful schema verification; schema-only work must not collect rows. |
| F6 | JSON-shredding/caching must distinguish per-process reentrancy from the native cross-process build lock and the HTTP child/parent lifecycle. `tests/test_json_cache_cross_process.py::test_cache_build_lock_serializes_independent_processes` already proves two spawned processes exclude each other on the build lock; verify it covers publication of the same cache, then add the missing witness that cancellation/timeout cannot release admission or the publication lock before child exit and staging cleanup. Preserve the in-process library path's separate contract. |
| F7 | Explore must expose explicitly safe typed errors and keep unexpected exception text diagnostic-only. Inject a synthetic diagnostic marker and verify it stays out of status/result payloads and rendered messages while terminal state and logging remain useful. |
| F8 | Modelling must distinguish `HauteValidationError` provenance from a dependency's plain `ValueError`, including training and dispersion. Assert error classification, public message, diagnostics and cleanup, not only an exception class. |

Do not add tests that freeze contradictory paragraphs or require a prose-only
defect to make runtime tests fail. Link consumers to the owner and run docs
accuracy after reconciliation. Keep package acceptance tests in owning Testing
sections so a future refactor can find the behavioural obligation.

**Acceptance:** Four prose conflicts are removed without weakening the currently
implemented contracts. Existing correct tests remain green; any added test closes
a demonstrated assertion gap. JSON lock evidence uses separate processes, and
error tests use synthetic data with exact public/private outcome assertions.

**Dependencies:** ENG-T01 seed. Root semantic review of the actual current
behaviour; no dependency on changing assistant verification or worker architecture.

**Evidence:** `tests/test_assistant_application.py`;
`tests/test_json_cache_cross_process.py`; `tests/test_json_cache_routes.py`;
`tests/test_explore_routes.py`; `tests/test_training_worker_protocol.py`;
`src/haute/assistant/_application.py`; `src/haute/routes/_training_worker.py`;
`specs/execution-engine/low-level.md`; `specs/json-shredding/low-level.md`;
`specs/caching/low-level.md`; `specs/explore-eda/high-level.md`;
`specs/modelling/low-level.md`.

### ENG-T10 — Complete the supported workflow families

**Why:** Fixing eight examples alone leaves similar gaps in other user journeys.
The seed below covers every component; ENG-T01 expands each row into the actions
and modes explicitly supported by its current specifications and maps existing
tests before creating more.

**Plan:** Implement the following as separate bounded slices, each with its own
spec review, exact test selectors and acceptance evidence. Retain existing real
training, trace, IO, graph and explore browser journeys where they already prove
the result. Use small synthetic fixtures with independently calculated outputs.

| Workflow | Components | Required sequence and outcomes |
|---|---|---|
| W01: install and start | build-and-distribution, cli, hosted-databricks-app | Fresh supported install/init/open/run; missing or invalid config and optional dependency; hosted session bootstrap/failure/restart; useful error and no partial project. Reuse package/platform/optional-dependency lanes. |
| W02: author and recover | pipeline-config, server-api, frontend-shared | Load/create/edit/save/reload; external edits, two clients, invalid source, recovery, disconnect/reconnect and unsaved work. ENG-T02 owns the critical data-loss witnesses. |
| W03: build a graph | frontend-graph-canvas, frontend-node-editors, submodels, codegen | Create/configure/connect/disconnect/copy/instance/group/enter/exit/dissolve/delete/undo/redo/save/reopen. Conserve graph and computed meaning; singleton API input/output rules apply across nested definitions. ENG-T07/08 own parse/rename invariants. |
| W04: obtain and persist data | io-layer, databricks-io | File/inline/database/lakehouse/Databricks operations that the registry supports; source switch, schema discovery, cache refresh and explicit write. Preview cannot perform writes. Missing source, wrong options, empty input, cancellation and failed output preserve appropriate prior artifacts. External credentials never enter browser/code fixtures. |
| W05: structured request to response | json-shredding | JSON/JSONL/XML input, frame/edge join, output mapping, dry-run and batch assembly; missing/null/empty/nested arrays, duplicate or missing keys, row ordering, strict schema errors and exact expected response per request. Persist frame edits and reopen. |
| W06: execute and inspect | execution-engine, caching, tracing, frontend-trace-ui | Preview/refresh/trace/export with active source, cold/warm caches, config/data/helper edits, filtered/reordered/joined rows and multiple frames. Compare values and trace identity; stale result cannot replace a newer selection. ENG-T03 supplies operation freshness. |
| W07: rate and explain | rating, expression-parsing | Band/rating configuration, preview and trace against hand-calculated values; exact thresholds, ties, nulls, missing factors, mixed supported key types, rounding and invalid expressions. Supported expression parity is explicit; unsupported AST forms retain their documented outcome. |
| W08: train, retain and score | modelling, mlflow-model-registry, frontend-modelling-optimiser-ui | Configure/train/cancel/retry/persist/load/score and switch panels; minimal real supported model plus worker/lifecycle tests. Empty input, target/weight validation, partition boundaries, non-finite metrics, stale model/cache identity and artifact mismatch have explicit results. |
| W09: optimise, choose and apply | optimiser | Estimate/solve/frontier/select/save/apply/trace; online and ratebook modes, infeasible/empty/non-finite cases, factor dtypes, tie ordering, constraints at limits and stale/cancelled jobs. Selected result, saved artifact, applied prices and trace agree. Keep current thread-backed isolation truthful. |
| W10: explore and present | explore-eda, frontend-preview-explore | Run/cancel/retry report; pivot/filter/chart/edit/save/reopen/export; exact aggregates, empty/all-null/constant/large-cardinality and cap boundaries. Dependent charts become stale/refresh together; failed refresh retains or clears prior evidence exactly as specified. |
| W11: version and restore | git-integration, hosted-project-storage, frontend-git-ui | Choose working branch/save/compare/revert/submit/push/pull/conflict/restore; cancellation leaves usable state and unsaved edits are protected. ENG-T05/06 supply pointer and restart contracts. Use temporary bare remotes and deterministic UC transport. |
| W12: assistant edit lifecycle | assistant, frontend-assistant-ui | Request/stream/tool proposal/validation/apply/save/verify/undo/cancel/reconnect with real application service and deterministic provider boundary. Stale proposals cannot overwrite newer edits; failed verification stays visible. Live provider qualification remains a separate lane. |
| W13: deploy and score | deploy | Validate/prune/bundle/load/score for one and many requests; compare editor dry-run with packaged scoring using the same synthetic input. Missing artifacts, invalid output, non-JSON values and unsupported target fail at the specified stage; stub upload/service SDKs in ordinary CI. |
| W14: admission and lifecycle | background-jobs | Running/completed/error/cancelled/timeout/superseded transitions for each applicable job family; late progress/completion cannot revive terminal jobs; reservations, processes, cache leases and temporary artifacts release once. A successful retry proves subsequent usability. |
| W15: containment and assurance | sandbox-security, engineering-quality, reference-pipeline | ENG-T04 boundary witnesses and ENG-T12 gates. The checked-in rating project remains a documented non-runnable snapshot: validate that limitation, never invent missing data or advertise it as the runnable end-to-end fixture. |

Apply a common boundary vocabulary only where meaningful: zero/one/many; missing
versus null versus empty; valid limit minus one/at limit/plus one; duplicate and
reordered identities; supported dtype classes including non-finite numbers;
relative/case-sensitive/nested paths; warm/cold/invalidated cache; success/failure
before and after commit; cancellation before start/during work/at publication;
same and different projects/nodes/clients. Audit success, failure and recovery
outcomes separately. Cover valid boundary values as well as rejection.

Browser coverage concentrates on observable transitions: keyboard access and
focus, loading/disabled/error/empty states, pending edit completion, narrow
viewport where already supported, navigation between panels, and recovery after
reconnect. Assert accessible visible state and final data; screenshots support
layout checks but do not establish computational correctness. Keep most input
combinations in fast backend or component tests, with one real cross-stack
witness for each materially different UI-to-runtime handoff.

Explicitly disposition these existing specification limitations while mapping
the families; they are not all missing product features:

- W01: the CLI Testing section flags uninvoked registered signal callbacks and
  mocked Vite/uvicorn startup. Add deterministic callback cleanup tests and one
  bounded real start/readiness/stop witness if existing browser/package harnesses
  do not already exercise the CLI lifecycle. Check help defaults/types with
  focused assertions, not nine broad snapshots merely to increase test volume.
- W03: hierarchical generated modules are supported through static parse/flatten;
  direct hierarchical `Pipeline.run()` registration is explicitly not the current
  equivalence path. Test the supported path; do not introduce a failing success
  requirement for the unsupported one.
- W06: HTTP preview currently retains target-only materialisation. A first trace
  must produce correct lineage by the supported route even when it recomputes;
  successful full-lineage cache reuse requires a separate cache-scope change and
  is not an acceptance requirement here. Keep value/identity parity and current
  performance budgets distinct.
- W13: platform-container service updates are unimplemented and must remain loud.
  A real built-image smoke for the implemented generic container scoring contract
  can close a packaging gap using local synthetic data, without requiring a live
  registry push or implementing a cloud adapter. Record provider-only tests as
  external qualification, not as covered by an SDK mock.
- Indirectly covered frontend helpers are not automatic gaps. Inspect the
  `flowHandles` and toolbar formatting assertions named by their specs; add a
  focused case only if a meaningful boundary outcome is absent.

**Acceptance:** All W01–W15 actions and all current node types/modes have a
reviewed covered or justified inapplicable record. Existing evidence is reused;
new tests have an identified missing outcome. Required decisions/gaps remain
visible and prevent a claim of complete coverage. No new EDA, hosted, deployment
adapter or solver feature is implemented merely to satisfy an invented test.

**Dependencies:** ENG-T01 inventory; corresponding ENG-T02–09 contracts where
workflows overlap. Unrelated W slices can proceed independently after root scoping.

**Evidence:** `frontend/e2e/core-flows.spec.ts`; `frontend/e2e/data-io-nodes.spec.ts`;
`frontend/e2e/edge-join.spec.ts`; `frontend/e2e/explore.spec.ts`;
`frontend/e2e/smoke.spec.ts`; `tests/test_data_io_roundtrips.py`;
`tests/test_output_nested_roundtrip.py`; `tests/test_trace_matches_preview.py`;
`tests/test_rating_key_agreement.py`; `tests/test_training_worker_protocol.py`;
`tests/test_optimiser_ratebook_apply_agreement.py`; `tests/test_job_lifecycle.py`;
`tests/test_deploy_batch_scoring.py`; `tests/test_cli_smoke.py`;
`specs/reference-pipeline/high-level.md`.

### ENG-T11 — Exercise generated boundaries and state transitions

**Why:** Curated valid examples miss illegal namespace combinations and operation
orderings. The specs also explicitly identify gaps in malformed-source generation,
expression/window parity and optimiser tie-label generation.

**Plan:** Extend existing property/differential suites, with small bounded domains
and independent oracles. Do not derive expected values by calling the same helper
as the production result. Add each generator only after its invariant is precise:

- Graph/source: generate bounded canonical DAGs with public submodel ports;
  parse/generate/flatten preserves edge identities and computed rows. Mutate one
  interface or authored connection at a time and require conservation or a
  contextual rejection. Test malformed-source recovery independently from the
  canonical parser, which does not support that recovery path.
- Editing/version state: bounded load/edit/save/external-write/rename/undo/reload
  sequences. A small model tracks base revision, unsaved edit and durable files;
  stale writes never replace a newer accepted generation. Use ENG-T02/08 semantics.
- Jobs/cache: enumerate admitted/running/cancel/timeout/late-completion/retry
  sequences with controlled scheduler hooks. Results correspond to one snapshot,
  terminal state never revives, and resource ownership returns to baseline.
- Numeric/data contracts: test documented expression operations against real
  Polars over supported inputs, including multi-row partitions where supported;
  simple independent band/rating oracles; structured output record/frame alignment;
  optimiser level-label ties and supported dtype equivalence. Explicitly classify
  documented unsupported forms rather than broadening semantics by accident.
- Metamorphic relations: cold and warm results agree; save/reload preserves values;
  request permutation preserves per-request answers for row-independent scoring;
  equivalent public-interface graphs compute the same result. Do not apply row
  permutation or chunk equivalence to order-sensitive/global operations without
  their specified ordering/collection rules.

Use the existing Hypothesis/toolchain support. Set small reproducible PR budgets,
retain seeds and shrunk failing examples, and place any larger exploration in
an explicit CI lane. Preserve every discovered defect as a fixed regression.
No dependency upgrade or new property framework is needed solely for this plan.

**Acceptance:** Each generated family demonstrates a distinct invariant and a
known negative control that it detects. Failures reproduce from retained examples
without relying on test order or a local Hypothesis cache. Generated tests do not
replace the eight fixed defect witnesses or the real user workflow checks.

**Dependencies:** ENG-T02–10 contracts for the relevant family. Do not generate
against an undecided save, rename, publication or execution-boundary oracle.

**Evidence:** `tests/test_codegen_roundtrip_property.py`;
`tests/test_expression_parser_polars_parity.py`; `tests/test_job_lifecycle.py`;
`tests/test_output_nested_roundtrip.py`; `tests/test_optimiser_ratebook_apply_agreement.py`;
`specs/expression-parsing/low-level.md`; `specs/optimiser/low-level.md`.

### ENG-T12 — Make the new evidence permanent and affordable

**Why:** Review-only probes provide no ongoing CI protection. Coverage and
mutation gates are useful but do not discover absent workflows. A refactor can
also move behaviour outside a filename-specific mutation target.

**Plan:**

1. Confirm exact test collection and execution lanes for each ledger record.
   Ordinary backend witnesses belong under `tests/`, without a `perf` marker;
   frontend tests use existing Vitest discovery; browser journeys use existing
   Playwright projects and fixture isolation. Add targeted cross-platform cases
   to the platform lane where filesystem/spawn behaviour requires it.
2. Keep the current backend coverage/compatibility, frontend, browser, package,
   performance and mutation gates. Expand only relevant selectors/targets;
   do not lower floors, add skip/xfail debt, or increase retries to absorb failures.
   Exact expected-failure evidence is collected before the fix locally; permanent
   tests enter the ordinary green gate together with the correction.
3. Check regression sensitivity with the original faulty implementation or a
   narrowly scoped test-only fault in an isolated checkout: omitted revision
   comparison, path-only suppression, stale namespace, discarded private edge,
   incomplete rename, stale restart branch, ineffective publication fence and
   missing execution enforcement must each trigger their intended assertion.
   Replaying a safe recorded failure is acceptable evidence only for the exact
   same code snapshot; report it as such. Do not modify the user's checkout to
   run destructive or concurrent mutation experiments.
4. Review mutation ownership for `_user_exec`, parser conservation, persistence,
   watcher and cache callers after fixes. Add only bounded high-value targets and
   decisive test commands after measuring their runtime. Backend mutation cannot
   certify frontend rename behaviour; the real execution witness remains mandatory.
   Equivalent/time-out mutants are not silently labelled killed.
5. Measure added duration in existing CI artifacts and use the current job
   timeout/budget contracts. Keep one smoke journey per critical UI handoff and
   shift combinations to lower tiers. Run deterministic race regressions without
   retry dependence; retain traces, seeds and exact selectors on failure.
6. At each package completion update owning Testing sections and ledger records,
   fold temporary spec contracts into current behaviour, and remove its roadmap
   row/section. Keep active work in this catalogue, with no second remediation tree.

**Acceptance:** All eight runtime findings have ordinary collected tests, observed
red-to-green evidence and outcome assertions at the real boundary. F5–F8 have
reconciled specs and appropriate passing contract witnesses. All workflow records
have an explicit final disposition; no required `gap`/`decision` remains when
claiming the programme complete. Relevant CI checks are green without weakening
existing gates, and runtime/platform/provider limitations are stated explicitly.

**Dependencies:** Incremental after each package; final completion requires
ENG-T01–11. Mutation expansion follows measured value, not a blanket target count.

**Evidence:** `.github/workflows/ci.yml`; `.github/workflows/mutation.yml`;
`frontend/package.json`; `frontend/playwright.config.ts`; `pyproject.toml`;
`mutation/targets.json`; `scripts/run_mutation_suite.py`;
`tests/test_test_debt.py`; `tests/test-health-summary.md`.

## Delivery order and verification

Start with the small ENG-T01 schema/fixture seed, then ENG-T02 and ENG-T03 to
cover ordinary data-loss and stale-result workflows. Reproduce ENG-T04/05 and
resolve their enforcement/provider decisions promptly; those decisions must not
block ENG-T07 parser conservation. Follow with ENG-T06 and ENG-T08 lifecycle
work, and reconcile ENG-T09 prose alongside the relevant boundary review.
Expand ENG-T10 one workflow slice at a time; add ENG-T11 properties only after
the corresponding oracle is settled. Integrate ENG-T12 continuously.

Each implementation slice is one coherent spec/test/fix change. First run the
smallest new regression and record its intended failure, implement the smallest
coherent correction, rerun that selector, then the affected module and touched
static checks. A failing import, a timeout, or an unrelated 500 is not acceptable
red evidence. Keep known-good controls next to negative cases.

| Change surface | Lowest sufficient verification |
|---|---|
| Plan/spec registration | `uv run pytest tests/test_docs_accuracy.py -q`; `uv run python scripts/spec_corpus_inventory.py --format json` |
| Python behaviour | `uv run pytest tests/<owning_module>.py::<test_or_class> -q`, then that module; Ruff check and format check on touched files; affected `uv run mypy src/haute/` |
| Frontend behaviour | `npm --prefix frontend test -- src/<owning_test>.test.tsx` (or `.ts`), then affected neighbours; `npm --prefix frontend run typecheck` and `npm --prefix frontend run lint` |
| API contract | Regenerate via `npm --prefix frontend run generate:contracts`; run Python contract tests and `npm --prefix frontend run check:contracts`; inspect generated diff |
| Browser handoff | `npm --prefix frontend run test:e2e -- e2e/<owning>.spec.ts --project=chromium --retries=0`, using only the relevant spec/title during iteration |
| Full compatibility, coverage, package, performance and mutation | Existing GitHub CI lanes; inspect failing logs and rerun the affected workflow after a fix |

The initial plan does not require running or recreating the entire CI/browser
suite locally. At implementation time capture exact node IDs/titles rather than
leaving the placeholders above in a verification report. Hosted SDK tests in
ordinary CI use synthetic transports; a claim about provider atomicity or a
live assistant model needs its separate qualification evidence.
