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
| SPEC-A09 | Decision | P2 | The three findings that expose code, not prose, are fixed with tests; the fourth was a prose fix. |

## Planned improvements

Delivery order is SPEC-A01 first (it shares files with SPEC-A02 and SPEC-A05),
then the component packages SPEC-A02 to SPEC-A07 in pairs with disjoint files,
then SPEC-A08 (it edits `specs/ownership.toml` and folds headings in files the
component packages also touch) and SPEC-A09. Each package is one bounded
editing pass: for every finding it re-reads the cited specification line and
the cited code at `HEAD`, applies the recorded fix or a better one that obeys
the writing rules, or records the finding as already resolved with the proof,
and never edits a file outside its list.

### SPEC-A09 — Findings that expose code

**Why:** Four findings are about the code, not the prose: a blank
`X-Forwarded-Email` header is recorded as a user identity (F095), the pivot
result cache evicts pinned keys once unpinned ones run out although the store
invariant says pinned entries are exempt (ADD07), the hatch build hook's atomic
proof-publication failure branch has no test although the Testing section
claims one (F023), and the json-shredding specifications describe the
persisted-proof rebind as a legacy-manifest upgrade although the code rebinds
current-format manifests written on another volume or before a revision was
observable (F203).

**Plan:** Decisions taken: F095 treats a blank or whitespace-only header as
absent (the guard the specification already describes) with a test; ADD07
makes the pivot trimmer exempt pinned keys like the other four caches, with a
Vitest witness; F023 adds the OSError-during-publication test; F203 rewrites the
two json-shredding passages to describe the rebind rule as it is (a
content-matching manifest whose recorded proof differs is rebound after a full
hash) and leaves the code alone, because that path is the fresh-host and
first-observation rebind rather than a format shim. Each code change updates
its own specification passage in the same commit.

**Acceptance:** Three behaviour changes each with a test; the four
specification passages describe the behaviour as shipped; backend and frontend
suites green.

**Dependencies:** SPEC-A02 and SPEC-A07 (they leave these passages alone).

**Evidence:** `src/haute/hosted.py`; `frontend/src/stores/useNodeResultsStore.ts`;
`tests/test_hatch_build.py`; `src/haute/_json_shred/_cache.py`;
`specs/hosted-databricks-app/high-level.md`; `specs/frontend-shared/low-level.md`;
`specs/build-and-distribution/low-level.md`; `specs/json-shredding/high-level.md`.
