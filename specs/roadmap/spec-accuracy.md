# Specification accuracy roadmap

## Scope

Resolve every standing finding of the
[specification corpus review of 6 September 2026](fable5.1-review/report.md):
the 220 first-pass findings that survived the second pass and the 19 second-pass
additions recorded in `fable5.1-review/findings.json`, minus the three roadmap
findings that the retirement of the test coverage roadmap on the same day made
moot (F005, F124 and the third second-pass addition). Every finding is a
semantic discrepancy between a specification and the code, a violation of the
writing rules in `specs/TEMPLATE.md` (resolved history, planned behaviour,
legacy framing, partial paths, ownership claims without a ledger record, extra
top-level headings, unresolvable test references), or an enumeration that omits
a live surface. The mechanical gate (`tests/test_docs_accuracy.py`) was green on
the review snapshot, so none of this is caught by it today.

The review's method was two independent passes over all 83 corpus files; its
line references describe the snapshot at commit `9f2e9576e0`. Each package
re-verifies its findings against `HEAD` before editing: the submodels, codegen,
pipeline-config, expression-parsing, server-api and frontend specifications
changed later that day (public port names, occurrence names and identities), so
a finding may already be resolved or its line may have moved.

Non-goals:

- The 101 "unverified" claims in the ledger are not findings and are not
  addressed here.
- Behaviour changes are limited to the four the findings expose as live defects
  or as compatibility paths the canonical-only policy forbids (SPEC-A09). Every
  other package changes documents only; where a finding offers a choice between
  fixing the code and fixing the description, the description is fixed and the
  decision recorded in the package.

## Priorities

| Package | State | Priority | Outcome |
|---|---|---|---|
| SPEC-A01 | Planned | P1 | The seven P1 route, security and submodel contracts read as the code behaves. |
| SPEC-A02 | Planned | P2 | Server API, git, hosted app, hosted storage and sandbox specifications corrected. |
| SPEC-A03 | Planned | P2 | Background jobs, build, caching, CLI, codegen, Databricks I/O, deploy, engineering-quality and execution-engine specifications corrected. |
| SPEC-A04 | Planned | P2 | Expression parsing, explore, I/O layer, JSON shredding, MLflow, optimiser, rating, reference pipeline and tracing specifications corrected. |
| SPEC-A05 | Planned | P2 | Assistant, modelling, pipeline-config and submodels specifications corrected. |
| SPEC-A06 | Planned | P2 | Frontend graph canvas and node editor specifications corrected. |
| SPEC-A07 | Planned | P2 | Frontend shared, git, assistant, trace, modelling and preview UI specifications corrected. |
| SPEC-A08 | Planned | P2 | Ownership ledger, document structure and governance statements match the corpus. |
| SPEC-A09 | Decision | P2 | The four findings that expose code, not prose, are fixed with tests. |

## Planned improvements

Delivery order is SPEC-A01 first (it shares files with SPEC-A02 and SPEC-A05),
then the component packages SPEC-A02 to SPEC-A07 in pairs with disjoint files,
then SPEC-A08 (it edits `specs/ownership.toml` and folds headings in files the
component packages also touch) and SPEC-A09. Each package is one bounded
editing pass: for every finding it re-reads the cited specification line and
the cited code at `HEAD`, applies the recorded fix or a better one that obeys
the writing rules, or records the finding as already resolved with the proof,
and never edits a file outside its list.

### SPEC-A01 — P1 contracts

**Why:** Seven findings describe contracts an integrator would code against
wrongly: the git readiness state set (F002), the storage bind status code and
its claimed 409 (F003, F004), the security boundary that denies the hosted
reverse-proxy mode exists (F006), a `PipelineSummary.error` field that does not
exist (F007), submodel routes described as writing files (F008), and a null
inbound submodel handle described as silently dropped when it raises (F009, and
its low-level twin ADD14).

**Plan:** Fix the descriptions. F003 keeps the route at 200 (the body's
`outcome` already carries the pending state; a status change would alter a
contract for no consumer). F009/ADD14 state the `ParseError` that
`flatten_graph` raises through `resolve_submodel_instances`, because a draft
handle that reaches execution must fail loudly rather than vanish. F006 states
the boundary as it is: loopback-only is the posture of `haute serve`, and the
hosted entry point in `src/haute/hosted.py` delegates authentication to the
platform proxy, cross-linked to the hosted-databricks-app specification.

**Acceptance:** Each of the eight findings is either corrected in the cited
document (and in every sibling passage the finding names) or recorded as
resolved at `HEAD` with the proof; `tests/test_docs_accuracy.py` stays green.

**Dependencies:** None.

**Evidence:** `fable5.1-review/report.md`; `fable5.1-review/findings.json`;
`specs/git-integration/low-level.md`; `specs/hosted-project-storage/low-level.md`;
`specs/sandbox-security/high-level.md`; `specs/server-api/low-level.md`;
`specs/submodels/high-level.md`; `specs/submodels/low-level.md`.

### SPEC-A02 — Server API, git, hosted app, hosted storage, sandbox

**Why:** Twenty-two P2 and P3 findings against these five components: omitted
routes, fields and startup steps, a stale 422 mapping, a header token transport
that does not exist, a blank forwarded-email identity described as rejected,
partial paths, and resolved-history phrasing (ADD02, F093, F094, F096, F097,
F099, F126, F127, F128, F129, F130, F131, F132, F134, F135, F196, F198, F199,
F200, F229, F230, F231).

**Plan:** One editing pass over `specs/server-api/low-level.md`,
`specs/git-integration/low-level.md`, `specs/hosted-project-storage/low-level.md`,
`specs/hosted-databricks-app/high-level.md`, `specs/hosted-databricks-app/low-level.md`,
`specs/sandbox-security/high-level.md` and `specs/sandbox-security/low-level.md`.
The module-map rows F097 asks for (`databricks_app/app.yaml`,
`databricks_app/requirements.txt`, the two scripts and `LEARNINGS.md`) are
added with one-line responsibilities.

**Acceptance:** Every listed finding corrected or recorded as resolved at
`HEAD`; the docs check stays green, including the module-map path and
test-reference rules the new rows and paths must satisfy.

**Dependencies:** SPEC-A01 (shared files).

**Evidence:** `fable5.1-review/findings.json`; `specs/server-api/low-level.md`;
`specs/git-integration/low-level.md`; `specs/hosted-project-storage/low-level.md`;
`specs/hosted-databricks-app/low-level.md`; `specs/sandbox-security/high-level.md`.

### SPEC-A03 — Background jobs, build, caching, CLI, codegen, Databricks I/O, deploy, engineering quality, execution engine

**Why:** Forty-nine findings against nine backend components: symbols attributed
to the wrong module, retry and ordering claims that contradict the code, missing
manifest keys and error rows, a garbled numbered list, a missing pytest marker,
and resolved-history or planned-behaviour phrasing (ADD01, F018, F019, F020,
F021, F022, F024, F025, F026, F027, F028, F029, F030, F031, F033, F034, F035,
F036, F037, F038, F039, F040, F041, F042, F043, F044, F045, F046, F047, F048,
F152, F153, F154, F155, F156, F157, F158, F159, F162, F163, F164, F165, F166,
F167, F169, F170, F171, F173, F174).

**Plan:** One editing pass over the background-jobs, build-and-distribution,
caching, cli, codegen, databricks-io, deploy, engineering-quality and
execution-engine documents. F048 (a "future tier" sentence) is deleted rather
than turned into a package: no such tier is planned. F021 drops the unrelated
test file from the background-jobs Testing section.

**Acceptance:** Every listed finding corrected or recorded as resolved at
`HEAD`; the docs check stays green.

**Dependencies:** None (its files are disjoint from SPEC-A02).

**Evidence:** `fable5.1-review/findings.json`; `specs/background-jobs/low-level.md`;
`specs/caching/low-level.md`; `specs/cli/low-level.md`; `specs/codegen/low-level.md`;
`specs/deploy/low-level.md`; `specs/execution-engine/low-level.md`.

### SPEC-A04 — Expression parsing, explore, I/O layer, JSON shredding, MLflow, optimiser, rating, reference pipeline, tracing

**Why:** Forty-nine findings against nine components: non-existent symbols
(`_UNHASHABLE_DTYPES`, `_is_unhashable_dtype`, `_CATEGORICAL_COUNT_FIELD`,
`_record_relaxed_candidate_ambiguity`, `try_get`, the wrong trace-cache module
and method), reversed step orders, stale counts and sizes, missing exception
types and callers, and resolved-history phrasing (ADD04, ADD05, ADD10, ADD11,
ADD12, F010, F049, F050, F051, F052, F053, F054, F100, F101, F102, F103, F104,
F105, F106, F115, F121, F122, F141, F142, F143, F144, F145, F146, F176, F177,
F178, F179, F201, F202, F204, F206, F207, F208, F209, F214, F219, F220, F221,
F223, F224, F225, F226, F227, F234).

**Plan:** One editing pass over the expression-parsing, explore-eda, io-layer,
json-shredding, mlflow-model-registry, optimiser, rating, reference-pipeline and
tracing documents. F010 describes the shipped one-candidate-per-edge behaviour
of `_resolve_multi_frame_parent` (the resolution rules already disambiguate).
F206's "becomes justified only if" gate is deleted rather than filed: no work
is planned behind it. Stale counts (F214, F221) are dropped rather than
refreshed so they cannot drift again.

**Acceptance:** Every listed finding corrected or recorded as resolved at
`HEAD`; the docs check stays green.

**Dependencies:** None (its files are disjoint from SPEC-A05).

**Evidence:** `fable5.1-review/findings.json`; `specs/explore-eda/low-level.md`;
`specs/json-shredding/low-level.md`; `specs/mlflow-model-registry/low-level.md`;
`specs/rating/low-level.md`; `specs/tracing/low-level.md`.

### SPEC-A05 — Assistant, modelling, pipeline-config, submodels

**Why:** Thirty-four findings against four components: dead roadmap package
references (ASSIST-A04/A05/A07), prompt guidance described as backend behaviour,
wrong artifact names, wrong scaffold directory names, an obsolete `to_graph()`
fallback description, a `Submodel` constructor contract and `RegisteredSubmodel`
type missing from Key types, a planned test list inside a submodels invariant,
a recovery-revision claim, and resolved-history phrasing (ADD13, ADD15, ADD19,
F011, F012, F013, F014, F016, F017, F107, F108, F109, F110, F111, F112, F113,
F114, F116, F117, F118, F119, F120, F136, F137, F139, F140, F150, F151, F210,
F213, F216, F217, F218, F232).

**Plan:** One editing pass over the assistant, modelling, pipeline-config and
submodels documents. F117 and F216 are re-verified first: the registration is
now `pipeline.submodel(path, name[, instance_of=...])` and `RegisteredSubmodel`
carries `{file, name, instance_of}`, so the corrections describe that form.
F139's to-build test list becomes present-tense coverage statements; anything it
lists that does not exist is dropped, not filed.

**Acceptance:** Every listed finding corrected or recorded as resolved at
`HEAD`; the docs check stays green.

**Dependencies:** SPEC-A01 (shares the submodels documents).

**Evidence:** `fable5.1-review/findings.json`; `specs/assistant/low-level.md`;
`specs/modelling/low-level.md`; `specs/pipeline-config/low-level.md`;
`specs/submodels/low-level.md`.

### SPEC-A06 — Frontend graph canvas and node editors

**Why:** Twenty-one findings: `parsePipelineResponse` named where
`parsePipelineEditorDocument` runs, a plural `_SourceHandles` symbol, paths
relative to `frontend/src/`, a revision guard overstated, an incomplete node
renderer exception list, and eleven passages of resolved-history, rejected-
proposal or process-mandate language (ADD09, F062, F063, F064, F065, F068,
F069, F070, F071, F072, F073, F074, F075, F077, F078, F079, F080, F081, F186,
F187, F188).

**Plan:** One editing pass over the four frontend-graph-canvas and
frontend-node-editors documents, excluding the two heading folds SPEC-A08
owns (F067, ADD08) and the ownership claims SPEC-A08 records (F066, F082).
F079's older-format fallback is described as the single current behaviour
(an absent `sort_by` resolves to the sole active Value sort, else null).

**Acceptance:** Every listed finding corrected or recorded as resolved at
`HEAD`; the docs check stays green, including the frontend path rule for
`## Testing` references.

**Dependencies:** None.

**Evidence:** `fable5.1-review/findings.json`;
`specs/frontend-graph-canvas/high-level.md`; `specs/frontend-graph-canvas/low-level.md`;
`specs/frontend-node-editors/low-level.md`.

### SPEC-A07 — Frontend shared, git, assistant, trace, modelling and preview UI

**Why:** Thirty-two findings: a fifth bounded cache category missing from every
enumeration, wrong exported type names, non-existent refs and guards
(`peekingRef`, `setExpanded`, `refreshGeneration`, `applied`), a modal-mode
union listed at three of six members, a stale bundle budget, an SSE variant
count off by one, a guard order that contradicts the implementation, missing
session-list surfaces, and resolved-history phrasing (ADD06, ADD16, ADD17,
F055, F056, F057, F058, F059, F060, F061, F083, F084, F085, F086, F087, F088,
F089, F090, F091, F092, F180, F181, F182, F183, F184, F185, F189, F190, F192,
F193, F194, F195).

**Plan:** One editing pass over the frontend-shared, frontend-git-ui,
frontend-assistant-ui, frontend-trace-ui, frontend-modelling-optimiser-ui and
frontend-preview-explore documents. The pivot-cache pin invariant (ADD07) is
not described until SPEC-A09 has made the code honour it.

**Acceptance:** Every listed finding corrected or recorded as resolved at
`HEAD`; the docs check stays green.

**Dependencies:** None (its files are disjoint from SPEC-A06).

**Evidence:** `fable5.1-review/findings.json`; `specs/frontend-shared/low-level.md`;
`specs/frontend-git-ui/low-level.md`; `specs/frontend-assistant-ui/low-level.md`;
`specs/frontend-trace-ui/low-level.md`.

### SPEC-A08 — Ownership ledger, document structure and governance statements

**Why:** Seventeen findings that cut across components: prose ownership claims
without a `specs/ownership.toml` record (F032, F066, F082, F098, F138, F197,
F233, ADD18), a stale ledger rationale (F215), documents carrying a seventh
top-level heading (F067, F175, F211, ADD08), governance statements in
`specs/README.md` that overstate the inventory gate, the coverage contract and
the roadmap index (F147, F148, F149), and roadmap Evidence fields that name no
source (F228).

**Plan:** One editing pass, run after the component packages because it edits
the shared ledger and folds headings in documents they also touch. Ledger
entries name the primary that already documents the file's behaviour; heading
folds move content under the template's six headings without changing it;
F148 classifies the packaged assistant example bundles in the coverage contract.

**Acceptance:** Every listed finding corrected; `specs/ownership.toml` records
every file two Module maps name or prose claims across components; every
component document carries exactly the template headings; the docs check stays
green.

**Dependencies:** SPEC-A02 to SPEC-A07.

**Evidence:** `fable5.1-review/findings.json`; `specs/ownership.toml`;
`specs/README.md`; `specs/roadmap/explore-eda.md`; `tests/test_docs_accuracy.py`.

### SPEC-A09 — Findings that expose code

**Why:** Four findings are about the code, not the prose: a blank
`X-Forwarded-Email` header is recorded as a user identity (F095), the pivot
result cache evicts pinned keys once unpinned ones run out although the store
invariant says pinned entries are exempt (ADD07), the hatch build hook's atomic
proof-publication failure branch has no test although the Testing section
claims one (F023), and the JSON cache keeps an old-manifest upgrade path that
the canonical-only policy forbids in a prerelease codebase (F203).

**Plan:** Decisions taken: F095 treats a blank or whitespace-only header as
absent (the guard the specification already describes) with a test; ADD07
makes the pivot trimmer exempt pinned keys like the other four caches, with a
Vitest witness; F023 adds the OSError-during-publication test; F203 removes the
manifest-upgrade path so a manifest without the proof field fails validity and
is rebuilt, with the shred tests adjusted. Each change updates its own
specification passage in the same commit.

**Acceptance:** Four behaviour changes each with a test; the four
specification passages describe the new behaviour; backend and frontend suites
green.

**Dependencies:** SPEC-A02 and SPEC-A07 (they leave these passages alone).

**Evidence:** `src/haute/hosted.py`; `frontend/src/stores/useNodeResultsStore.ts`;
`tests/test_hatch_build.py`; `src/haute/_json_shred/_cache.py`;
`specs/hosted-databricks-app/high-level.md`; `specs/frontend-shared/low-level.md`;
`specs/build-and-distribution/low-level.md`; `specs/json-shredding/high-level.md`.
