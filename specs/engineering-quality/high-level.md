# Engineering Quality — High-Level Specification

## Purpose

Engineering quality is the repository's executable assurance system. It makes
code style, static typing, backend/frontend tests, coverage, package viability,
performance, mutation resistance, and selected dependency/order regressions
observable before or after a change reaches `main`.

The component also records the distinction between active gates and repository
evidence. Tests and maintained automation define current verification
behaviour; the component roadmaps, reproduction programs, generated reports,
and runtime outputs may preserve valuable context but do not themselves define
the current product contract.

## Scope

In scope:

- GitHub Actions workflows, pre-commit hooks, and project configuration for
  Ruff, mypy, pytest, coverage, and critical-coverage thresholds.
- Maintained operational scripts for preflight, smoke, mutation, performance,
  frontend E2E support, coverage checking, fixture regeneration, and worktree
  setup.
- Frontend lint/type/test/coverage/E2E configuration and the corresponding npm
  commands.
- The active backend test corpus, frontend unit corpus, E2E corpus, fixtures,
  mutation configuration, and performance test groups.

Out of scope:

- Package composition, static-asset build policy, and documentation publication,
  owned by [build-and-distribution](../build-and-distribution/high-level.md).
- Product behaviour described by individual component specs.
- `specs/roadmap/`, `repro/`, and generated directories such as `site/`,
  `dist/`, coverage reports, mutation run output, test reports, caches, and
  local runtime/model outputs. Roadmaps record intended delivery rather than a
  quality gate or product contract; none of these paths promise current
  behaviour unless a live workflow, test, or configuration explicitly consumes
  a named file.

## Behaviour

- Pull requests and pushes to `main` run early lint/format and core-test canary
  checks, backend static/type/package checks, sharded coverage with a global
  90% floor plus configured critical-file floors, supported Python compatibility
  tests, package/install smoke tests, optional-dependency checks, frontend
  checks, browser E2E, and the named auxiliary lanes in the CI workflow.
- Python 3.14 is a non-blocking forward probe. A failure in that lane reports
  compatibility information but does not become a required green result.
- Pre-commit runs Ruff fix/format and repository-local mypy/frontend
  typecheck/lint hooks for matching source files. The hooks do not replace CI.
- The default pytest configuration excludes only `perf`-marked tests, treats
  xfails/config/markers strictly, and turns most runtime warnings into errors.
  Performance tests run through dedicated scripts/workflows with explicit time
  budgets instead.
- Frontend commands use TypeScript build-mode type checking, ESLint, Vitest,
  critical coverage validation, and Playwright. Browser E2E serialises workers,
  retries only in CI, and starts the repository's E2E server with readiness
  checking. Vitest's blocking global coverage floors are 80% statements, 75%
  branches, 80% functions, and 80% lines, followed by the configured
  source-specific critical-coverage ratchet. ESLint treats useless assignments,
  discarded caught-error context, unsafe hook refs, and state updates in effects
  as errors; fourteen pre-existing file/rule pairs remain visible as narrow config
  exceptions until their owning changes land.
- The project-isolated Chromium canvas-assurance module owns the high-risk
  Banding-to-Rating and optimiser/MLflow-boundary journeys, keyboard operation,
  and reviewed screenshots at 1440×900 and 1024×768. Firefox remains a smoke
  compatibility project rather than a visual oracle. User-reported canvas
  defects receive the smallest regression at the owning tier before a fix.
- Scheduled dependency and frontend-order lanes are deliberately non-PR
  monitoring checks: on eligible failures they create or update a labelled
  GitHub issue carrying diagnostic/reproduction context. Mutation and
  performance workflows likewise operate through maintained scripts/artifacts.
- Python performance reports use schema 3, recording deterministic workload and
  profile descriptors, environment versions, independent resource counters,
  explicit unavailable values, and a checked wall-time partition. The CI-small
  Polars join-plus-training scenario exercises every supported execution profile;
  1m/10m variants remain opt-in and no hardware-specific throughput threshold is
  a normal CI gate.
- Internal improvement work is self-contained in one flat component catalogue:
  `specs/roadmap/README.md` plus one `specs/roadmap/<component>.md` file per
  component. Each package records its problem, implementation direction,
  acceptance criteria, dependencies, and current code/test evidence. The
  catalogue remains non-normative: an item must be re-verified against `HEAD`,
  specified, and regression-tested before implementation. The index points to
  a non-deferred package only; it shows no starting package when every
  remaining package is explicitly deferred.

## Design rationale

- The assurance system layers cheap specific feedback before expensive broad
  evidence. Canary/static checks find common failures early; sharded coverage,
  fresh-install smoke, browser E2E, mutation, and performance check different
  failure classes without pretending that one test style is sufficient.
- Critical coverage floors are per-file ratchets alongside the global floor,
  prioritising safety-sensitive or high-impact code instead of rewarding only
  aggregate coverage.
- Locked dependencies prove the committed environment; the scheduled unlocked
  resolve lane separately exposes breakage from a later dependency release
  within published version caps. These answer different questions.
- npm meta-findings use the affected package plus a stable transitive-advisory
  identity. A lockfile topology change must not invalidate a reviewed acceptance;
  a different package or concrete GHSA identity still does.
- Mutation targets are deliberately curated and sharded across isolated CI
  runners, avoiding an unbounded or environment-racy mutation gate while
  retaining threshold enforcement for selected high-value code.
- Test debt is visible through one generated, committed health summary rather
  than scattered comments alone. Backend skip/xfail/flaky sites and frontend
  skipped/focused/expected-failure sites remain exact fingerprint ratchets.
  The same summary includes the pinned Playwright retry budget and every
  mutation target's survivor threshold. A stale generated summary fails
  ordinary tests; review is event-driven through the ratchet, with no
  calendar-expiry gate (ruled 2026-07-27).
- Point-in-time reproduction material is non-normative evidence. Treating it as
  a current product contract would create misleading claims.
- Component roadmaps preserve only the actionable conclusion and current
  code/test evidence needed to reverify it. Each change therefore has one
  visible owner, execution order, dependency boundary, and retirement decision
  without depending on a second review or remediation Markdown tree.

## Assistant provider qualification

Supported assistant provider/model configurations are gated by a separate
credentialed evaluation lane. Version-controlled held-out semantic and
adversarial scenarios run repeatedly in isolated temporary projects and are
scored against a closed support matrix. Deterministic CI validates the harness,
fixture separation, aggregation, attribution, and zero-tolerance safety rules;
it does not substitute a scripted provider result for live qualification.

## Interactions

- Every product component supplies source and tests to this component's gates;
  [build-and-distribution](../build-and-distribution/high-level.md) consumes the
  resulting package/frontend/docs verification in its delivery paths.
- [sandbox-security](../sandbox-security/high-level.md) is exercised by strict
  write-sandbox tests, but owns runtime write policy.
- [frontend-shared](../frontend-shared/high-level.md) and other frontend specs
  are tested through the frontend unit and browser-E2E harnesses.
- `mutation/targets.json` and `mutation/*.toml` configure selected mutation
  targets; they do not make mutation testing a runtime dependency of Haute.

## Failure model

- A failing CI command stops its job and makes the corresponding check red;
  package, test, lint, type, coverage, mutation-config, frontend, and E2E
  failures are surfaced rather than replaced with a passing fallback. GitHub
  branch protection, rather than these workflow files, decides which checks are
  required before a merge.
- The Python 3.14 probe uses `continue-on-error`, so it is intentionally
  informative rather than blocking. Scheduled dependency and frontend-shuffle
  failures are also monitoring signals, with issue-creation logic rather than a
  claim that every historical run blocks a pull request.
- Strict pytest configuration fails unknown markers/configuration and unexpected
  xpasses; configured warning filters turn most runtime warnings into errors.
- The test-health gate fails on an unreviewed backend/frontend debt site, a
  missing static reason, a non-strict unbudgeted xfail, a stale aggregate
  summary, or malformed mutation-target metadata. Zero current backend flaky
  markers is an enforced budget, not an undocumented observation.
- Coverage merge/gate fails when shard data cannot satisfy the 90% global floor
  or `scripts/check_critical_coverage.py` finds a configured file below its
  statement/branch thresholds.
- E2E failures retain Playwright trace/screenshots/video according to its
  configuration. Benchmark/performance failures retain their workflow artifacts
  when the job reaches the upload step.
- The single mutation gate runs after its dependencies even when planning or a
  required shard fails. It fails explicitly when no valid plan/result set exists,
  so a failed planner cannot turn the required gate into a skipped check.
- Documentation-contract retirement requires positive delivery evidence from a
  resolvable target symbol or executable acceptance-test symbol. The existence
  of a file named as an intended edit is not delivery evidence.
