# Engineering quality audit — August 2026

This is the point-in-time record of a full semantic complexity audit of the
repository, completed on 2026-08-31 against commit `6abf5679`. It preserves the
audit's scope, evidence, findings, and dispositions exactly as observed at that
commit. Statistics below (file counts, line counts, complexity values) describe
that commit and are not maintained; do not treat them as current.

The audit's delivery input was the set of work packages `ENG-CX01` through
`ENG-CX12`. In accordance with the roadmap lifecycle, the active roadmap file
was removed after every outcome became maintained component specifications,
regression tests, and performance evidence. Package identifiers below are
historical audit references; this report remains their supporting evidence and
rationale.

## Scope and method

The audit performed a full semantic read of the specification tree as it
existed at the start of the audit. The literal tree contained 78 files: 77
Markdown files (29,568 lines) and `specs/ownership.toml`. The review covered
every file to EOF, then followed the implementation and test references needed
to distinguish five concerns: overengineering, duplication, complicated control
flow, brittleness, and performance bottlenecks.

The repository's inventory command reported only 77 of those files. It omitted
`specs/optimiser/error-detail-policy.md`, because it discovers component
`high-level.md`/`low-level.md` pairs, root governance files, and direct roadmap
Markdown rather than every file below `specs/`. That omission is itself
executive finding 1 below. The machine-readable coverage ledger
`docs/ENGINEERING_QUALITY_AUDIT_2026_08_COVERAGE.toml` covers every file the
audit-time inventory could represent; the omitted policy file is explicitly
included in this report's literal-tree coverage and component disposition. The
ledger itself originally sat undeclared inside `specs/roadmap/` — exactly the
class of inventory-invisible file finding 1 describes — and was relocated next
to this report; `ENG-CX01` owns making such omissions impossible.

The audit used three confidence classes:

- **Verified** — current specs plus source/tests directly exhibit the issue.
- **Structural risk** — the shape is verified, but user impact or future drift
  has not been reproduced.
- **Performance hypothesis** — the costly control path is verified, but workload
  measurements are required before changing it.

Static complexity numbers are locators, not proof that a function is wrong.
Similarly, duplicated boundary validation is not automatically waste: the
audit recommends consolidation only where one semantic contract is being
maintained independently in several places.

## Executive findings

### 1. The specification process currently permits contradictory and invisible documents

**Confidence: Verified.** The spec-first workflow cannot be authoritative while
its semantic and mechanical inventories disagree.

- `scripts/spec_corpus_inventory.py` omits
  `specs/optimiser/error-detail-policy.md`; the documentation-accuracy checks
  also do not apply the component heading contract to it.
- `specs/hosted-databricks-app/high-level.md` says the component is a draft and
  that nothing exists in `src/haute`, while
  `specs/hosted-databricks-app/low-level.md`, `src/haute/hosted.py`, and
  `tests/test_hosted.py` describe and exercise the implementation.
- `specs/engineering-quality/high-level.md` specifies performance-report schema
  3; its low-level spec, `scripts/run_perf_suite.py`, and tests use schema 4.
- The Databricks IO specs say SQL acquisition reads only a PAT, while
  `src/haute/_databricks_io.py` supports PAT-or-OAuth service-principal
  credentials. The hosted component separately states the OAuth contract.
- There are 23 `Approved change contract` headings. Several describe symbols
  and tests that are already present, including the hosted-storage section that
  literally begins "Delivered". The retirement guard only detects a subset of
  shipped contracts when particular labels and symbol-qualified references are
  used.
- `specs/roadmap/explore-eda.md` has 20 package headings but only 12 priority
  rows, seven `In progress` rows, and repeated delivered- and review-outcome
  narratives. `specs/roadmap/optimiser.md` retains a delivered-outcomes
  section. Both conflict with the roadmap rule that delivered work is removed.
- `specs/execution-engine/high-level.md` repeats the same eight-line "Assistant
  schema inspection is plan-only" requirement in adjacent bullets.

This is more than editorial untidiness: stale future/past tense can cause a
second implementation of shipped work, and an omitted file can evade every
claimed full-corpus review.

### 2. One API boundary is maintained in several independent representations

**Confidence: Verified duplication; structural drift risk.** The central API
surface currently spans 226 Pydantic `BaseModel` subclasses in
`src/haute/schemas.py`, 191 exported TypeScript type/interface declarations in
`frontend/src/api/types.ts`, and 214 parser/validator-style functions found by
the audit's static pattern in `frontend/src/types/guards.ts` and
`frontend/src/types/trainGuards.ts`. The four files total 10,215 lines.

Strict browser-side parsing is valuable. The duplication is that field names,
unions, enum mappings, caps, and nested rules are handwritten in each layer.
Execution diagnostics are a concrete example: producer dataclasses, Pydantic
DTOs, TypeScript declarations, runtime guards, and renderer projections each
encode the version and field rules. Explore chart and pivot contracts repeat a
similar validator set in Python and TypeScript.

The correct simplification is not to trust network data. It is to generate
static declarations and validator metadata from one checked schema while
retaining a runtime trust boundary, redaction checks, and frontend-specific
presentation validation.

### 3. Browser code derives Python and storage identities

**Baseline finding: Verified; remediated by ENG-CX03.** The browser previously
carried a manual copy of `src/haute/_graph_utils.py::_sanitize_func_name`,
including Python's keyword list and an explicit CPython `str.isspace()`
codepoint set. Executable identities now come from the server-owned editor
identity contract; browser-only persistence keys use the deliberately distinct
`portableKey` contract.

This is already a failure-prone seam. `frontend/src/panels/NodePanel.tsx`
documents a prior path mismatch in which a raw node id addressed the wrong JSON
cache and silently reported `cached=false`. Config references and executable
edge/input identities are now supplied by the server and consumed as transient
editor metadata.

The server already owns Python identifiers, pipeline configuration, and path
containment. Editor documents or capability payloads should carry canonical
function names, input identities, and config references explicitly; the browser
should display them, not reimplement Python naming semantics.

### 4. Background jobs expose mutable storage while lifecycle guarantees live outside it

**Confidence: Verified coupling; structural race/bypass risk.** Job records are
plain `dict[str, Any]`. `JobStore.get_job()` and `JobStore.jobs` expose live
records/backing state, while `JobLifecycle` enters `store._write_lock` and calls
private merge/cleanup methods. Training artifact publication also reaches into
that private lock for a paired compare-and-swap.

The specs explicitly state that raw store writes do not enforce the terminal
state policy and that correctness assumes callers treat exposed records as
read-only. This makes every new job type responsible for preserving locking,
copy-on-write, terminal states, heavy-object expiry, artifact cleanup, and
activity bookkeeping. A typed record plus public atomic transition/publication
operations would make invalid states harder to express without removing the
existing concurrency guarantees.

### 5. Complexity is concentrated in orchestration boundaries, not evenly spread

**Confidence: Verified static structure; refactor value still requires scoped
tests.** The backend had 233 production Python files and 146,545 lines. Running
Ruff's C901 rule as an audit-only locator found 282 functions over its default
threshold, 76 at complexity 20 or above, 24 at 30 or above, and seven at 50 or
above. C901 is not a normal repository gate.

The largest decision functions were:

| Function | Complexity | Concern |
|---|---:|---|
| `_execute_lazy` | 86 | Graph preparation, cache/checkpoint policy, projection, execution, and publication in one path. |
| `_execute_eager_core` | 78 | A parallel orchestration path with overlapping node and contract concerns. |
| `_expression_parser._call` | 77 | A custom semantic interpreter over many Polars call forms. |
| `_trace_enrichment.enrich_steps` | 66 | Parsing, recursive lineage, node-specific enrichment, and error isolation. |
| `_column_lineage._parse_call_sequence` | 55 | Syntax classification and lineage extraction. |
| `_trace_correlation._typed_value_match_expr` | 52 | Cross-dtype row matching policy. |
| `server._file_watcher` | 50 | Filesystem observation, coalescing, parsing, and publication. |

The largest backend files reinforce the same boundaries:
`src/haute/routes/_optimiser_service.py` (5,212 lines),
`src/haute/_git.py` (4,272), `src/haute/schemas.py` (3,711),
`src/haute/routes/_train_service.py` (3,527), and
`src/haute/projection.py` (3,402). Size alone is not the recommendation; each
file owns several independently changing policies or state machines.

A targeted ESLint sample of ten high-churn frontend files, with complexity
temporarily reported above 10, produced 47 complexity warnings plus ten
pre-existing React ref/effect warnings. The largest functions were
`FlowEditor` (110), `NodePanel` (75), `OptimiserConfig` (75), `GitPanel` (47),
and `ExploreChartsConfig` (48). The React warnings include render-time ref
mutation and synchronous state updates inside effects, both of which increase
the chance of cascading work and stale state. This was a targeted sample, not a
whole-frontend count.

### 6. The Python-file source-of-truth decision creates a multi-parser architecture

**Confidence: Verified mechanism; architecture decision required.** A saved
node can pass through structured builders, source templates, string/token
rewrites, final AST parsing, strict pipeline parsing, regex/text recovery, and a
separate expression semantic interpreter. Runtime and codegen builders are
paired implementations. Window metadata and trace row-reordering decisions
still use narrow regex or raw substring classifiers even though AST structure is
available elsewhere. Pipeline discovery deliberately treats a comment containing
`haute.Pipeline` as a pipeline.

These mechanisms have extensive tests and should not be replaced piecemeal.
The simplification opportunity is a deliberate CST/IR boundary: preserve
hand-authored Python, comments, and recovery needs while reducing the number of
independent interpretations of the same call/decorator/input identity.

### 7. Several small duplicates are directly removable

**Confidence: Verified.** These do not require an architecture programme:

- `_is_simple_literal` is duplicated in `src/haute/_explore_charts.py` and
  `src/haute/_explore_pivots.py`.
- Trace `instanceOf` effective-code resolution is repeated in three paths in
  `src/haute/_trace_enrichment.py`.
- `row_to_json_safe` and trace `_jsonify_row` perform the same wrapper
  conversion around the shared JSON-safe scalar function.
- Databricks route and SQL IO paths independently resolve the same PAT/OAuth
  precedence.
- CLI deploy validation scores test quotes, then the CLI scores them again to
  render output.
- `frontend/src/__tests__/utils/graphFingerprint.test.ts` tests a function
  copied into the test and described as the App implementation. Production now
  uses `computeStructuralFingerprint` in
  `frontend/src/stores/useGraphStore.ts`, so the copied test can stay green
  while production changes.

These are suitable first refactors because their acceptance tests can call the
real shared function or compare an unchanged public result.

### 8. The credible performance findings need two different responses

**Confidence: mixed; see the performance register.** The audit confirmed
control paths that scale with candidate frames, remote runs, or repository
history, but did not manufacture latency claims from code size.

- Trace correlation can scan a full parent frame for row matching and repeats
  that match for every candidate frame in a multi-frame parent. Across a reverse
  graph walk this can scale with nodes × candidate frames × rows × shared
  columns. The request contract permits `row_limit=10,000`.
- MLflow search calls `list_artifacts` once per returned run, an N+1-shaped
  remote API path.
- Unity Catalog project storage publishes full history-bearing bundles; cost
  grows with bundle/history size.
- Thread compatibility mode cannot kill a started blocking call. Response
  timeout detaches it, while caller cancellation waits for completion and can
  retain task/limiter ownership.
- A first trace after a target-only preview cannot reuse that narrower cached
  frame set and may execute full lineage cold. This is deliberate correctness,
  but observable latency should be measured.

By contrast, serial chunk execution, bounded caches, warm-worker limits, and a
single blocking optimiser solve are explicit safety/resource policies. They are
throughput constraints, but should change only under the existing optimiser
roadmap or after representative measurements.

## Deliberate complexity not recommended for blanket simplification

| Area | Audit disposition |
|---|---|
| Eager preview versus lazy batch execution | Preserve the semantic split. Share preparation and node-boundary plumbing only. |
| Process, warm-pool, and explicit thread worker modes | Each has a documented operational role. First unify result/state vocabulary and measure mode usage. |
| Strict frontend runtime guards | Keep validation at the network boundary; generate the repetitive schema content. |
| Dataframe, source, JSON, preview, and trace caches | They retain different artifact shapes and lifecycles. Consolidate identity/telemetry only where evidence shows duplicate ownership. |
| Sandbox AST and restricted-globals layers | Defence in depth is intentional. Do not remove a layer without an explicit threat model and adversarial acceptance suite. |
| Serial bounded chunk runner | It is a memory guarantee, not automatically a performance defect. |
| Optimiser global admission/frontier coordination | Existing `OPT-P06`, `OPT-P12`, and related packages own measurement and safe concurrency changes. |
| Reference pipeline | It is explicitly a non-runnable snapshot. Do not convert it into a smoke application unless that product/documentation decision changes. |

## Performance bottleneck register

| Path | Current evidence | Confidence | Required next evidence |
|---|---|---|---|
| Trace row correlation | Full-row vector matching per candidate frame during a reverse node walk; target limit up to 10,000. | Performance hypothesis | Record candidate frames, scanned rows/columns, cache origin, and p50/p95 latency on representative fan-out graphs. |
| Trace after target-only preview | Narrow preview cache cannot satisfy full lineage, so first trace may execute cold. | Verified behavior; impact unmeasured | Compare cold, target-cache, full-preview-cache, and trace-cache paths. |
| MLflow run search | One remote artifact listing per returned run. | N+1 shape verified | Measure API-call count/quota and latency by run count; test provider-side filtering or bounded metadata cache. |
| UC project publishing | Full bundle plus manifest/fence publication on save. | Growth path verified | Record compressed bytes, history objects, serialization time, upload time, and retry rate. |
| Non-killable thread cancellation | Started thread continues; cancellation waits for its completion. | Verified lifecycle | Inventory production call sites and duration bounds; measure retained limiter/task time after cancellation. |
| Frontend effect orchestration | Targeted sample exposes synchronous effect state updates and very high branch complexity. | Structural risk | Profile render/commit counts for graph edits, Git refresh, node switching, and optimiser progress before/after decomposition. |
| CLI deploy quotes | Validation and rendering score the same quotes twice. | Verified duplicate work | Reuse a structured validation result and assert one scorer invocation. |
| Optimiser solve/frontier | Process-wide admission and lock scope constrain throughput. | Verified constraint; already owned | Use `OPT-P06`/`OPT-P12` benchmarks and per-parent lock acceptance rather than a duplicate package here. |

## Component-by-component disposition

Every row below covers both the component's high- and low-level document in
full unless an additional file is named. "No new package" means the audit found
no evidence strong enough to justify work beyond current tests/roadmaps; it does
not mean the implementation is small.

| Component | Disposition |
|---|---|
| assistant | Plan construction and request validation repeat across layers; regex recipe routing is used as a hard tool gate. `ENG-CX07`. |
| background-jobs | Mutable dictionaries, private locks, and split check/acquire lifecycle authority are high-value simplification targets. `ENG-CX04`. |
| build-and-distribution | Build bypass makes regenerated frontend assets context-dependent; retain as a release-readiness check under `ENG-CX11`, not a build-system rewrite. |
| caching | Strongly specified bounds, pins, and artifact lifecycles justify most layering. Same-stat reuse is an explicit advisory trade-off; no new package without hit/staleness data. |
| cli | Duplicate deploy quote scoring and help/effective-default drift are verified. `ENG-CX11`. |
| codegen | Token/text rewrite after structured generation and paired runtime/codegen builders create representation coupling. `ENG-CX08`. |
| databricks-io | PAT-only prose contradicts the OAuth implementation; credential resolution is duplicated with routes. `ENG-CX01`, `ENG-CX11`. |
| deploy | Broad scoring/bundle paths and filename conventions are brittle; prefer a resolved immutable config/manifest. Include local duplicate work in `ENG-CX11`; require a separate product case before larger restructuring. |
| engineering-quality | Corpus discovery misses a file, performance schema versions conflict, and complexity debt is visible but not ratcheted. `ENG-CX01`; use scoped post-refactor ratchets rather than a blanket C901 gate. |
| execution-engine | The two highest-complexity backend functions share orchestration concerns, while worker isolation and serial chunking are deliberate. `ENG-CX06`. |
| explore-eda | Backend/frontend validators and a backend helper are duplicated; the 1,001-line roadmap mixes active, delivered, and superseded plans. `ENG-CX01`, `ENG-CX02`, `ENG-CX11`. |
| expression-parsing | A custom Polars semantic interpreter mixes AST handling with narrow lexical metadata rules. `ENG-CX08`. |
| frontend-assistant-ui | Module-level controllers plus several generation counters form a hidden navigation/stream state machine. Fold into `ENG-CX05` after assistant authority is settled. |
| frontend-git-ui | `GitPanel` is a 1,335-line, complexity-47 component with render-ref and effect-state exceptions. `ENG-CX05`. |
| frontend-graph-canvas | `FlowEditor` complexity 110 and repeated fingerprint/dirty branches concentrate graph orchestration. `ENG-CX05`; replace the copied fingerprint test via `ENG-CX11`. |
| frontend-modelling-optimiser-ui | `OptimiserConfig` complexity 75 and result-store lifecycle breadth warrant staged decomposition, not a visual rewrite. `ENG-CX05`. |
| frontend-node-editors | `NodePanel` complexity 75, Python-name mirroring, local path derivation, and shipped-looking temporary contracts. `ENG-CX01`, `ENG-CX03`, `ENG-CX05`. |
| frontend-preview-explore | Explore chart configuration complexity and multiple pivot-start authorities overlap with the Explore backend contract. `ENG-CX02`, `ENG-CX05`. |
| frontend-shared | The API client/types/guards boundary and Python sanitizer mirror are the main duplication seams. `ENG-CX02`, `ENG-CX03`. |
| frontend-trace-ui | Small, bounded presentation layer; no standalone finding. Backend trace work is owned by `ENG-CX10`. |
| git-integration | `src/haute/_git.py` has 4,272 lines and 134 top-level definitions spanning subprocesses, reads, caches, branch-pair transactions, archive, and remote operations. `ENG-CX09`. |
| hosted-databricks-app | High-level draft/unimplemented claims conflict with current code and low-level spec; auth contract is duplicated. `ENG-CX01`, `ENG-CX11`. |
| hosted-project-storage | Delivered history and unresolved follow-ups remain in ordinary specs; full UC bundles are a scale hypothesis. `ENG-CX01`, `ENG-CX12`. |
| io-layer | Registry/schema literal operation names require coordinated edits, but source-cache leases/publication are deliberate. Treat registry generation as a future `ENG-CX02` pilot only if drift is observed. |
| json-shredding | Multiple cache/storage layers protect distinct durable/runtime states and have extensive integrity tests. No refactor package without telemetry; cross-stack API shapes remain covered by `ENG-CX02`. |
| mlflow-model-registry | Per-run artifact listing is an N+1 remote path. `ENG-CX12`; disk-cache concurrency itself is deliberate. |
| modelling | Training route/service and job modules total 6,128 lines, repeat target/task work, and couple to job publication internals. `ENG-CX04`, `ENG-CX09`. |
| optimiser | The service monolith, global lock, input planning, artifact lifecycle, and frontier concurrency are already owned by `OPT-P11`, `OPT-P13`, `OPT-P12`, `OPT-P14`, and `OPT-P06`. The extra error-detail policy is invisible to corpus tooling: `ENG-CX01`. |
| pipeline-config | Literal source discovery and the code-as-authority round trip are brittle syntax seams. `ENG-CX08`. |
| rating | Mirrored configuration/runtime key validation is real but bounded and strongly cross-tested. No standalone package; consider it only as a generated-contract pilot. |
| reference-pipeline | Explicitly non-runnable and honestly documents missing artifacts. No new package. |
| sandbox-security | Denylist-adjacent AST checks plus restricted globals are intentional defence in depth; import-enabled paths are first-party trust boundaries. No simplification without a new threat-model decision. |
| server-api | The schema/API boundary, recovery contracts, file watcher, and temporary contracts concentrate drift. `ENG-CX01`, `ENG-CX02`; service decomposition only after those contracts settle. |
| submodels | Complex flatten/dissolve identity rules are extensively specified and tested; no independent duplication or bottleneck justified a new package. |
| tracing | Repeated effective-code resolution, substring reordering classification, and multi-frame full scans are verified. `ENG-CX08`, `ENG-CX10`, `ENG-CX11`. |

Governance files (`specs/README.md`, `specs/TEMPLATE.md`, and
`specs/ownership.toml`) were also read in full. README and TEMPLATE duplicate
temporary-contract policy; ownership has no separate defect. All four
pre-existing roadmap Markdown files were read in full. The background-jobs
roadmap contains one coherent deferred package; Explore and optimiser retain
delivered/history material as described above. The additional
`specs/optimiser/error-detail-policy.md` was read in full: its error decision is
coherent, but its undeclared location bypasses the claimed component corpus.

## Existing roadmap ownership confirmed

This audit does not create second owners for already-planned optimiser work:

| Existing package | Audit confirmation |
|---|---|
| `OPT-P11` | Artifact lifecycle extraction remains the correct owner for handle validation, persistence, cleanup, and expiry. |
| `OPT-P13` | Input-planning extraction remains the correct owner for projection/cache/input preparation. |
| `OPT-P06` | Bounded frontier parallelism owns throughput benchmarking and worker-count policy. |
| `OPT-P12` | Frontier domain service and narrower per-parent locking own global-lock reduction. |
| `OPT-P14` | Solver/result publication owns terminal result assembly and publication boundaries. |
| `ROAD-WORKER-04` | Remains correctly deferred until solver-specific durable persistence exists. |

Explore packages must be reconciled against shipped code before new work is
selected; `ENG-CX01` owns that roadmap cleanup, not the product behavior those
packages may ultimately retain.
