# Engineering Quality — High-Level Specification

## Purpose

Engineering quality is the repository's executable assurance system. It makes
code style, static typing, backend/frontend tests, coverage, package viability,
performance, mutation resistance, and selected dependency/order regressions
observable before or after a change reaches `main`.

The component also records the distinction between active gates and repository
evidence. Tests and maintained automation define current verification behaviour;
roadmaps, reproduction programs, review archives, generated reports, and runtime
outputs may preserve valuable context but do not themselves define the current
product contract.

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
- `docs/roadmap/`, `repro/`, `docs/review/`, `docs/fable-Review/`, and generated
  directories such as `site/`, `dist/`, coverage reports, mutation run output,
  test reports, caches, and local runtime/model outputs. Roadmaps record
  intended delivery rather than a quality gate or product contract; none of
  these paths promise current behaviour unless a live workflow, test, or
  configuration explicitly consumes a named file.

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
  checking.
- Scheduled dependency and frontend-order lanes are deliberately non-PR
  monitoring checks: on eligible failures they create or update a labelled
  GitHub issue carrying diagnostic/reproduction context. Mutation and
  performance workflows likewise operate through maintained scripts/artifacts.

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
- Mutation targets are deliberately curated and sharded across isolated CI
  runners, avoiding an unbounded or environment-racy mutation gate while
  retaining threshold enforcement for selected high-value code.
- Historical review and repro material is excluded from normal lint and public
  docs because it is point-in-time evidence. Treating it as current product code
  would create misleading failures and claims.

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
- Coverage merge/gate fails when shard data cannot satisfy the 90% global floor
  or `scripts/check_critical_coverage.py` finds a configured file below its
  statement/branch thresholds.
- E2E failures retain Playwright trace/screenshots/video according to its
  configuration. Benchmark/performance failures retain their workflow artifacts
  when the job reaches the upload step.
