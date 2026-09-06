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
| ENG-T04 | Decision | P1 | Make the node execution boundary testable and truthful; detect F1. |
| ENG-T12 | Planned | P2 | Enforce collection, regression sensitivity, and sustainable CI cost. |

## Planned improvements

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

**Dependencies:** The coverage ledger; a recorded execution-boundary design in
sandbox-security and execution-engine. The initial reproductions can run before
the decision; implementation and completion depend on it.

**Evidence:** `src/haute/_user_exec.py`; `src/haute/_sandbox.py`;
`tests/test_sandbox.py`; `tests/test_user_exec_imports.py`;
`tests/test_worker_isolation.py`; `tests/conftest.py`;
`specs/sandbox-security/high-level.md`.

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
ENG-T04–11. Mutation expansion follows measured value, not a blanket target count.

**Evidence:** `.github/workflows/ci.yml`; `.github/workflows/mutation.yml`;
`frontend/package.json`; `frontend/playwright.config.ts`; `pyproject.toml`;
`mutation/targets.json`; `scripts/run_mutation_suite.py`;
`tests/test_test_debt.py`; `tests/test-health-summary.md`.

## Delivery order and verification

Reproduce ENG-T04 and
resolve its enforcement decision promptly.
Integrate ENG-T12 continuously.

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
