# Haute Engineering Standard Plan

Date: 2026-04-08

Related documents:
- [`CODEBASE_REVIEW.md`](/home/ralph/suite/haute/CODEBASE_REVIEW.md)
- [`README.md`](/home/ralph/suite/haute/README.md)
- [`docs/ARCHITECTURE.md`](/home/ralph/suite/haute/docs/ARCHITECTURE.md)

Reviewed against current checked-out tree:
- branch: `main`
- head observed during revalidation: `0b8e170`

## Purpose

This document turns the current code review into a remediation and audit program aimed at the highest practical engineering standard for the `haute/` codebase.

It is not a claim that the codebase is currently at that bar.

It is a plan for how to get there.

## What “Highest Engineering Standard” Means Here

For Haute, that standard should mean all of the following at the same time:

- correct behavior under normal and edge-case usage
- explicit protection around unsafe code, files, model artifacts, and user input
- deterministic code-generation and round-trip fidelity
- strong regression protection through tests, static analysis, and CI gates
- clear operational behavior in local, CI, and deployment environments
- maintainable frontend and backend architecture with predictable ownership
- reproducible builds and acceptable performance characteristics
- docs and developer workflows that match the actual product

## Current Position

The current review in [`CODEBASE_REVIEW.md`](/home/ralph/suite/haute/CODEBASE_REVIEW.md) is broad and validated, but it is not exhaustive enough to certify the repo at that bar.

After revalidating the current tree, the plan needs one important adjustment:

- some of the earlier security/path items are now partially or fully fixed in code
- the plan should therefore distinguish between:
  - issues that still need implementation work
  - areas that now need regression protection and explicit policy rather than first-time fixes

## Revalidation Summary

### Already improved in the current tree

- `safe_joblib_load()` now matches the two-part allowlist semantics used by `_RestrictedUnpickler` and also protects the monkey-patch with a lock in [`src/haute/_sandbox.py`](/home/ralph/suite/haute/src/haute/_sandbox.py).
- `load_node_config()` now validates resolved paths against the project root in [`src/haute/_config_io.py`](/home/ralph/suite/haute/src/haute/_config_io.py).
- parser config resolution now falls back to `_load_error` metadata instead of loading external JSON when a `config=` path escapes the project root in [`src/haute/_parser_helpers.py`](/home/ralph/suite/haute/src/haute/_parser_helpers.py).
- sandbox coverage is substantially stronger now: `haute/tests/test_sandbox.py` currently passes with `216 passed, 1 xfailed`.

### Still open and still belongs in the plan

- duplicate sanitized node names are still not rejected before codegen/save
- generated file-backed nodes are still sensitive to process `cwd`
- submodel create/dissolve flows still drop pipeline descriptions
- frontend still has at least one real invalid-DOM/hydration warning in tests (`<button>` nested inside `<button>` in `RatingStepEditor`)
- frontend production build is still a single large JS chunk
- Python lint baseline is still far from green
- frontend subproject documentation is still generic/stale

### Reframed item

- parser-side external config handling is no longer a direct data-exfiltration bug in the current tree, but it still needs an explicit product decision:
  - is `_load_error` preservation the desired UX for invalid external paths
  - or should parse/save fail hard and visibly

The plan should treat that as a policy and regression-coverage task, not as an unimplemented security fix.

## Program Structure

This should be run as five phases.

### Phase 1: Immediate Risk Reduction

Goal:
- remove known correctness and security risks that are already validated

Required work:
- preserve and regression-test the current `safe_joblib_load()` protections, including an end-to-end malicious `builtins.eval` payload test
- preserve and regression-test project-root enforcement for config sidecars
- decide and document the expected behavior for out-of-root `config=` references: `_load_error` preservation vs hard parse failure
- reject duplicate sanitized node/function/config names at save time
- make generated file-backed paths resolve relative to pipeline file or explicit project root
- preserve pipeline descriptions through submodel create/dissolve flows

Required output:
- code fixes
- one regression test per issue
- short changelog entry in the PR or remediation notes

Exit criteria:
- all still-open validated findings from [`CODEBASE_REVIEW.md`](/home/ralph/suite/haute/CODEBASE_REVIEW.md) are fixed or explicitly deferred with rationale
- already-fixed security/path protections are covered by regression tests and explicit policy

### Phase 2: Contract Audit

Goal:
- prove the core product contract is stable across parser, graph, codegen, execution, and UI APIs

Audit areas:
- graph identity and invariants
- parser fidelity
- codegen determinism
- parser/codegen round-trip stability
- sidecar/config file behavior
- source file path semantics
- submodel creation, drill-in, dissolve, and round-trip semantics

Required checks:
- labels, IDs, handles, and config names remain unique and stable
- preserved blocks, preamble, descriptions, and scenarios survive round trips
- generated code is deterministic for equivalent graphs
- parse errors are actionable and do not partially corrupt saved state
- path resolution behavior is explicit and consistent

Required tests:
- property-style round-trip tests
- collision tests
- path-resolution tests
- mutation-style regression tests around save/load

Exit criteria:
- explicit written invariants for core graph/codegen/parser behavior
- full regression coverage for every invariant judged business-critical

### Phase 3: Runtime, Security, and Operational Hardening

Goal:
- raise confidence that Haute behaves safely and predictably under real workloads

Audit areas:
- sandboxing
- file system boundaries
- model artifact loading
- job orchestration and cancellation
- background jobs and temp-file cleanup
- deploy/config loading
- environment handling
- MLflow and Databricks integration boundaries

Required checks:
- every user-controlled path is validated against the intended root
- untrusted artifacts cannot escape class/module restrictions
- subprocess/network/deploy code degrades safely when dependencies are missing
- temp artifacts are cleaned up on success and failure
- long-running jobs are observable and cancelable
- server restart and watcher behavior do not corrupt or duplicate work

Required tests:
- negative-path security tests
- temp-file cleanup tests
- cancellation/retry tests
- deploy config fallback tests
- integration tests for CLI commands under multiple `cwd` layouts

Exit criteria:
- no known path-traversal or unsafe-load gaps
- operational workflows documented and test-covered

### Phase 4: Code Quality, Performance, and Frontend Architecture

Goal:
- remove quality debt that blocks long-term maintainability

Audit areas:
- Python lint/type baseline
- frontend architecture and state ownership
- build output and chunking
- expensive renders and duplicated fetches
- API client consistency
- test isolation and warning-free frontend tests

Required checks:
- Python lint baseline is green or intentionally narrowed
- type-checking expectations are explicit and enforced where feasible
- frontend large panels are split or lazy-loaded where justified
- React warnings in tests are eliminated rather than tolerated
- invalid HTML structure warnings are treated as real bugs, not harmless test noise
- major stores have clear ownership and invalidation semantics

Required metrics:
- `ruff` green under committed rules, or revised rules committed intentionally
- current baseline: `472` Ruff findings in the checked-out tree
- frontend build warning about giant chunks eliminated or justified with thresholds
- current baseline: one `2,776.43 kB` minified JS entry chunk (`833.83 kB` gzip)
- frontend tests currently pass at `132` files / `2550` tests, but the run is not warning-free
- test suite free of avoidable React warnings

Exit criteria:
- CI quality signals are trustworthy and low-noise
- frontend architecture is documented enough for safe extension

### Phase 5: Release Standardization

Goal:
- make “good engineering practice” repeatable rather than dependent on memory

Required work:
- define CI gates
- define PR review checklist
- define release checklist
- align docs with actual project layout and workflows
- document local dev, test, and deployment expectations

Required gates:
- backend tests
- frontend tests
- frontend build
- Python lint
- any chosen type-check gate
- targeted security/path regression tests

Exit criteria:
- a failed quality signal blocks merge
- docs match the real engineering workflow

## Systematic Audit Checklist

This is the checklist I would use for a true end-to-end certification pass.

### 1. Core Graph and Shared Types

Files:
- [`src/haute/_types.py`](/home/ralph/suite/haute/src/haute/_types.py)
- [`src/haute/schemas.py`](/home/ralph/suite/haute/src/haute/schemas.py)
- [`src/haute/graph_utils.py`](/home/ralph/suite/haute/src/haute/graph_utils.py)
- [`src/haute/pipeline.py`](/home/ralph/suite/haute/src/haute/pipeline.py)

Checklist:
- node/edge identity rules are explicit
- helper maps do not silently collapse invalid states
- serialized API shape is stable and documented
- backend and frontend naming conventions stay aligned
- graph validation exists for every invalid state that would corrupt persistence

### 2. Parser and Codegen

Files:
- [`src/haute/parser.py`](/home/ralph/suite/haute/src/haute/parser.py)
- [`src/haute/_parser_helpers.py`](/home/ralph/suite/haute/src/haute/_parser_helpers.py)
- [`src/haute/_parser_submodels.py`](/home/ralph/suite/haute/src/haute/_parser_submodels.py)
- [`src/haute/_parser_regex.py`](/home/ralph/suite/haute/src/haute/_parser_regex.py)
- [`src/haute/codegen.py`](/home/ralph/suite/haute/src/haute/codegen.py)
- [`src/haute/_config_io.py`](/home/ralph/suite/haute/src/haute/_config_io.py)

Checklist:
- round-trip keeps semantic equivalence
- metadata survives save/load/create/dissolve operations
- generated code is deterministic
- path behavior is explicit
- collisions are rejected before code is emitted
- malformed source fails clearly and safely

### 3. Execution Runtime

Files:
- [`src/haute/executor.py`](/home/ralph/suite/haute/src/haute/executor.py)
- adjacent lazy/builder helpers under [`src/haute`](/home/ralph/suite/haute/src/haute)

Checklist:
- node execution order is deterministic
- cache semantics are explicit
- temp outputs are cleaned up
- user code boundaries are documented and enforced
- error messages identify failing node and root cause
- runtime path assumptions do not depend on accidental `cwd`

### 4. Security and Sandboxing

Files:
- [`src/haute/_sandbox.py`](/home/ralph/suite/haute/src/haute/_sandbox.py)
- filesystem-sensitive route/helpers under [`src/haute/routes`](/home/ralph/suite/haute/src/haute/routes)

Checklist:
- all user paths are validated
- all unsafe deserialization paths are explicitly allowlisted
- tests cover negative cases, not only happy paths
- safe defaults are used when optional integrations are missing

### 5. Server and Routes

Files:
- [`src/haute/server.py`](/home/ralph/suite/haute/src/haute/server.py)
- [`src/haute/routes`](/home/ralph/suite/haute/src/haute/routes)

Checklist:
- route behavior is consistent across parse/save/preview/train/optimise flows
- route error responses are structured and actionable
- side effects are atomic enough for editor workflows
- websocket/watcher behavior is not race-prone
- source file and sidecar ownership rules are clear

### 6. CLI and Deploy

Files:
- [`src/haute/cli`](/home/ralph/suite/haute/src/haute/cli)
- [`src/haute/deploy`](/home/ralph/suite/haute/src/haute/deploy)

Checklist:
- commands behave correctly with and without `haute.toml`
- commands behave correctly from supported working directories
- configuration precedence is documented
- failure modes are explicit and script-friendly
- packaging/bundling only includes what should ship

### 7. Modelling and Optimisation

Files:
- [`src/haute/modelling`](/home/ralph/suite/haute/src/haute/modelling)
- modelling/optimiser routes under [`src/haute/routes`](/home/ralph/suite/haute/src/haute/routes)

Checklist:
- metrics and diagnostics are reproducible
- config validation is strong enough for expensive runs
- intermediate artifacts are traceable
- long-running jobs resume/fail/report correctly

### 8. Frontend

Files:
- [`frontend/src/App.tsx`](/home/ralph/suite/haute/frontend/src/App.tsx)
- hooks, stores, panels, editors, and client code under [`frontend/src`](/home/ralph/suite/haute/frontend/src)

Checklist:
- state ownership is explicit
- expensive panels are isolated and lazy where sensible
- network calls are canceled and deduplicated correctly
- test suite runs without React warnings
- backend/frontend contract drift is easy to detect
- bundle size is monitored and bounded

### 9. Tests, Docs, and Metadata

Files:
- [`tests`](/home/ralph/suite/haute/tests)
- [`docs`](/home/ralph/suite/haute/docs)
- [`pyproject.toml`](/home/ralph/suite/haute/pyproject.toml)
- [`frontend/README.md`](/home/ralph/suite/haute/frontend/README.md)

Checklist:
- tests reflect real supported workflows
- docs match the codebase, not aspirations from older versions
- package metadata and build behavior are aligned
- frontend and backend developer documentation are both real and current

## Concrete Quality Gates

These are the gates I would aim to enforce before calling the codebase “high standard”.

### Must Be Green

- backend test suite
- frontend test suite
- frontend build
- Python lint
- frontend lint

### Must Be Added or Tightened

- regression tests for every validated finding in [`CODEBASE_REVIEW.md`](/home/ralph/suite/haute/CODEBASE_REVIEW.md)
- parser/codegen round-trip regression tests for metadata and path semantics
- negative security tests for path traversal and unsafe artifact loading
- integration tests for CLI behavior under different working directories

### Must Be Measured

- frontend bundle size
- backend test runtime
- flake/noise level in CI
- number of known deferred defects

## Deliverables

To finish this program properly, the repo should end up with:

- fixes for validated defects
- regression tests for each fix and each already-landed security/path hardening change
- a tightened CI pipeline
- updated docs for backend and frontend workflows
- a short engineering checklist for future PRs
- an explicit list of invariants for parser/codegen/runtime safety

## Suggested Execution Order

1. Fix the validated critical/high issues from [`CODEBASE_REVIEW.md`](/home/ralph/suite/haute/CODEBASE_REVIEW.md).
2. Add regression tests immediately with each fix, and backfill tests for the security/path protections already present in the current tree.
3. Run the contract audit for parser/graph/codegen/submodels.
4. Run the runtime/security/deploy audit.
5. Clean the quality baseline: lint, warnings, bundle size, docs.
6. Add CI/release gates so the standard holds.

## Final Standard for Sign-Off

I would only describe Haute as meeting a highest engineering standard once all of these are true:

- no known critical or high-severity unresolved defects in core paths
- core parser/codegen/runtime invariants are documented and enforced by tests
- safety boundaries around files and artifacts are explicit and regression-tested
- repo quality signals are green and low-noise
- docs accurately describe how the system really works
- engineering quality is enforced automatically in CI, not informally

Until then, the right framing is:

Haute has a solid architectural foundation and meaningful strengths, but it still needs a deliberate remediation and hardening program to reach the highest standard.
