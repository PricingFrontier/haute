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
| ENG-T12 | Planned | P2 | Enforce collection, regression sensitivity, and sustainable CI cost; the W13-S02 container lane remains. |

## Planned improvements

### ENG-T12 — Make the new evidence permanent and affordable

**Why:** Review-only probes provide no ongoing CI protection. Coverage and
mutation gates are useful but do not discover absent workflows. A refactor can
also move behaviour outside a filename-specific mutation target.

**Delivered (2026-09-06):** the ledger validator refuses a witness the ordinary
lane would not collect (a module under `tests/performance/` or carrying a
module-level perf mark) and keeps `scripts/property_test_files.txt` equal to the
Hypothesis modules; the weekly `property-exploration` workflow runs that manifest
under a wide, re-randomised budget and opens a `property-watch` issue on failure;
the regression-sensitivity replay ran every fixed finding's witnesses against the
pre-fix source in a detached worktree (all seven red; recorded in each ledger
record's evidence and in `specs/engineering-quality/low-level.md`); the
`parser-conservation` mutation target (F3's structural acceptance gate) was added
after measuring its witness command (3.4 seconds per run, 170 mutants, 26.5%
raw survival) with reviewed annotation pragmas and direct gate-branch tests, and
a fresh run of the committed gate and tests left 3 effective survivors of
170 (1.76%, the three equivalent mutants named in its rationale) under the
3.0% ceiling; added PR cost is recorded per family in the owning Testing sections
(about 70 seconds of serial backend time in total, no browser journeys added).

**Plan:** the remaining steps. ENG-T04 was decided on 6 September 2026
(project code is trusted first-party code; the exec guard is an accident guard)
and its witnesses landed in `tests/test_node_code_trust_boundary.py` with
W15-S01 covered; the `_user_exec` mutation ownership review found the guard
already exercised through the real entry point by those witnesses and the
sandbox suite, so no new target was added.

1. Add the weekly/dispatch Docker lane for W13-S02 that builds the generated
   scoring image and answers `/health` and `/quote` with local synthetic data;
   its first scheduled run is the verification, since Docker is not available on
   the development machine.
2. Claim programme completion only when no required `gap`/`decision` remains.

**Acceptance:** All eight runtime findings have ordinary collected tests, observed
red-to-green evidence and outcome assertions at the real boundary. F5–F8 have
reconciled specs and appropriate passing contract witnesses. All workflow records
have an explicit final disposition; no required `gap`/`decision` remains when
claiming the programme complete. Relevant CI checks are green without weakening
existing gates, and runtime/platform/provider limitations are stated explicitly.

**Dependencies:** the W13-S02 lane.

**Evidence:** `.github/workflows/ci.yml`; `.github/workflows/mutation.yml`;
`.github/workflows/property-exploration.yml`; `frontend/package.json`;
`frontend/playwright.config.ts`; `pyproject.toml`; `mutation/targets.json`;
`scripts/run_mutation_suite.py`; `scripts/property_test_files.txt`;
`tests/test_test_debt.py`; `tests/test-health-summary.md`;
`tests/test_workflow_coverage.py`.

## Delivery order and verification

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
