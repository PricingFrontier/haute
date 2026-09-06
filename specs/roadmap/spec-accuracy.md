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
