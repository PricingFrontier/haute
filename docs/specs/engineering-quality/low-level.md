# Engineering Quality — Low-Level Specification

## Module map

| File | Responsibility |
|---|---|
| `pyproject.toml` | Configures Ruff, pytest, coverage, critical backend coverage thresholds, mypy, pinned development tools, and excludes non-product directories from the normal Ruff target. |
| `AGENTS.md` | Records repository-local engineering and review instructions for contributors and coding agents; it is guidance, not executable build or test configuration. |
| `.gitignore` | Excludes generated builds, virtual environments, caches, local pipeline output/data, tool state, and other non-source artifacts from normal version-control discovery. |
| `docs/specs/ownership.toml` | Machine-checked ledger for files deliberately named by multiple component Module maps; records the single primary owner and all consumer components. |
| `.pre-commit-config.yaml` | Runs Ruff fix/format plus local mypy and frontend typecheck/lint hooks on relevant source changes. |
| `.github/workflows/ci.yml` | Defines PR/main CI jobs: canary, install/package smoke, dependency floors, static/type checks, coverage shards/gate, compatibility/probe, performance, optional dependencies, platform, mutation-config, frontend, and browser E2E lanes. Branch protection, outside this repository workflow file, determines which checks are required for merge. |
| `.github/workflows/dependencies.yml` | Runs the scheduled/manual fresh unlocked-resolve monitor and raises/updates dependency-watch issues when it fails. |
| `.github/workflows/frontend-shuffle.yml` | Runs the scheduled/manual shuffled Vitest monitor and raises/updates shuffle-watch issues with its seed on eligible failures. |
| `.github/workflows/mutation.yml` | Plans changed mutation targets, runs separate CI-job shards whose mutants execute serially per runner, merges results, enforces target thresholds, and uploads mutation artifacts. |
| `.github/workflows/performance.yml` | Runs scheduled/manual Python and browser-performance lanes and uploads their artifacts. |
| `frontend/package.json` | Defines frontend lint/type/unit/coverage/bundle/E2E/benchmark command entry points and frontend critical-coverage entries. |
| `frontend/eslint.config.js` | Defines browser TypeScript/React ESLint rules, ignores generated reports, and preserves underscore-prefixed intentionally-unused names. |
| `frontend/vitest.config.ts` | Configures the Vitest unit-test environment, setup, coverage reporting, and source/test selection. |
| `frontend/playwright.config.ts` | Configures serial browser E2E projects, retries, artifacts, and readiness-managed local E2E server. |
| `frontend/e2e/core-flows.spec.ts` | Playwright coverage for core browser flows. |
| `frontend/e2e/data-io-nodes.spec.ts` | Playwright coverage for data-I/O node browser flows. |
| `frontend/e2e/data-preview-scroll.benchmark.spec.ts` | `@benchmark` Playwright coverage for data-preview scrolling. |
| `frontend/e2e/git-graph.spec.ts` | Playwright coverage for the Git graph. |
| `frontend/e2e/git-sidebar-regression.spec.ts` | Playwright regression coverage for the Git sidebar. |
| `frontend/e2e/job-progress-render.benchmark.spec.ts` | `@benchmark` Playwright coverage for job-progress rendering. |
| `frontend/e2e/large-graph-drag.benchmark.spec.ts` | `@benchmark` Playwright coverage for dragging large graphs. |
| `frontend/e2e/migration/v1-to-v2-node-continuity.spec.ts` | Playwright coverage for node-continuity migration behaviour. |
| `frontend/e2e/persistence/api-input-frame-alignment.spec.ts` | Playwright geometry coverage for API-input frame-row/handle alignment and downstream frame naming. |
| `frontend/e2e/persistence/api-input-render-gate.spec.ts` | Playwright persistence/render-gate coverage for API input. |
| `frontend/e2e/persistence/api-input-v2-native.spec.ts` | Playwright persistence coverage for native v2 API input. |
| `frontend/e2e/smoke.spec.ts` | Tagged smoke Playwright coverage, including the Firefox smoke project. |
| `frontend/e2e/__tests__/projectIsolation.test.ts` | Vitest unit coverage for E2E project-isolation helpers, explicitly ignored by Playwright. |
| `frontend/e2e/projectIsolation.ts` | Shared browser-E2E project-isolation helper. |
| `frontend/scripts/analyze-bundle-sourcemaps.mjs` | Analyses source-mapped production bundle composition for the frontend performance lane. |
| `frontend/scripts/check-bundle-size.mjs` | Enforces frontend bundle-size expectations. |
| `frontend/scripts/check-critical-coverage.mjs` | Reads Vitest coverage summary and enforces `frontend/package.json` critical entries. |
| `frontend/scripts/check-ui-dependencies.mjs` | Audits UI dependency constraints used by the frontend bundle check. |
| `scripts/check_critical_coverage.py` | Enforces configured backend per-file statement/branch coverage floors from coverage JSON. |
| `scripts/core_test_files.txt` | Curated core test-subset manifest and its selection/refresh rationale for canary/dependency lanes. |
| `scripts/e2e_git_topologies.py` | Exercises Git topology scenarios used by repository-level verification. |
| `scripts/extract_polars_io.py` | Extracts/checks Polars I/O information used by related verification and maintenance workflows. |
| `scripts/init_smoke.py` | Builds/installs or consumes a wheel in a fresh environment, initialises a project, serves it headlessly, exercises an authenticated endpoint, and shuts it down. |
| `scripts/memory_smoke.py` | Runs the maintained memory-safety smoke path. |
| `scripts/package_smoke_check.py` | Validates an installed distribution's package/runtime expectations. |
| `scripts/preflight.ps1` | Windows preflight entry point for selected backend/frontend/init-smoke checks. |
| `scripts/preflight.sh` | POSIX preflight entry point for selected backend/frontend/init-smoke checks. |
| `scripts/regen_sanitize_parity_fixture.py` | Regenerates the committed sanitisation-parity fixture when deliberately requested. |
| `scripts/run_frontend_e2e_server.py` | Starts and readiness-signals the backend/frontend process used by Playwright. |
| `scripts/run_mutation_suite.py` | Implements mutation target selection, work planning, shard execution, merge, and survival-threshold reporting. |
| `scripts/run_perf_suite.py` | Runs bounded Python performance tests and writes performance artifacts. |
| `scripts/setup-worktree.sh` | Sets up a development worktree. |
| `mutation/README.md` | Documents the maintained mutation-testing workflow and constraints. |
| `mutation/targets.json` | Declares selected mutation targets, witness suites, and survival budgets. |
| `mutation/cosmic-ray.executor.toml` | Cosmic Ray configuration for executor mutation coverage. |
| `mutation/cosmic-ray.job-store.toml` | Cosmic Ray configuration for job-store mutation coverage. |
| `mutation/cosmic-ray.json-cache.toml` | Cosmic Ray configuration for JSON-cache mutation coverage. |
| `mutation/cosmic-ray.jsonpath.toml` | Cosmic Ray configuration for JSONPath mutation coverage. |
| `mutation/cosmic-ray.json-shred.toml` | Cosmic Ray configuration for JSON-shredding mutation coverage. |
| `mutation/cosmic-ray.output-assembler.toml` | Cosmic Ray configuration for output-assembler mutation coverage. |
| `mutation/cosmic-ray.path-resolution.toml` | Cosmic Ray configuration for path-resolution mutation coverage. |
| `mutation/cosmic-ray.registry.toml` | Cosmic Ray configuration for registry mutation coverage. |
| `tests/` | Active Python unit, integration, property, regression, contract, E2E-support, and repository-hygiene test corpus. |
| `tests/fixtures/` | Checked-in input, golden, expected-contract, UI-contract, and data fixtures consumed by active tests. |
| `tests/performance/` | `perf`-marked benchmark-style tests excluded from ordinary pytest and run by the performance harness. |
| `frontend/src/__tests__/` | Frontend application-level unit, contract, regression, adversarial, and bundle/coverage gate tests. |
| `frontend/src/api/__tests__/` | Frontend API-client test group. |
| `frontend/src/components/__tests__/` and `frontend/src/components/form/__tests__/` | Frontend reusable-component and form-control test groups. |
| `frontend/src/hooks/__tests__/` | Frontend hook, polling, graph-state, API, and synchronisation test group. |
| `frontend/src/nodes/__tests__/` | Frontend graph-node component test group. |
| `frontend/src/panels/__tests__/` and `frontend/src/panels/editors/__tests__/` | Frontend panel and editor test groups. |
| `frontend/src/panels/editors/banding/__tests__/` and `frontend/src/panels/editors/rating/__tests__/` | Frontend banding/rating-editor test groups. |
| `frontend/src/panels/explore/__tests__/`, `frontend/src/panels/gitgraph/__tests__/`, `frontend/src/panels/modelling/__tests__/`, `frontend/src/panels/optimiser/__tests__/`, and `frontend/src/panels/trace/__tests__/` | Specialised explore, Git graph, modelling, optimiser, and trace panel test groups. |
| `frontend/src/stores/__tests__/`, `frontend/src/test-utils/__tests__/`, `frontend/src/trace/__tests__/`, `frontend/src/types/__tests__/`, and `frontend/src/utils/__tests__/` | Frontend store, test-helper, trace, type, and utility test groups. |
| `docs/roadmap/` | Current non-normative engineering delivery roadmaps; not an active quality gate or product-behaviour specification. |
| `repro/` | Point-in-time benchmark/reproduction programs and metadata; not an automatically current product-behaviour contract. |
| `docs/review/` | Historical engineering findings and runnable evidence excluded from normal Ruff and public MkDocs delivery. |
| `docs/fable-Review/` | Historical review/audit material excluded from normal Ruff and public MkDocs delivery. |
| `mlflow.db` | Checked-in SQLite MLflow tracking-store snapshot (experiments, runs, metrics, parameters, tags, and model-version metadata). It is repository data/local state, not an installed-package input or a runtime prerequisite; MLflow may instead use the configured tracking store. |

## Key types and data structures

- **Backend critical-coverage entry** in `pyproject.toml` has a source `path`,
  minimum statement/branch percentages, and rationale. The coverage JSON path
  is `.cache/coverage/backend.json`.
- **Frontend critical-coverage entry** in `frontend/package.json` has a source
  glob-like `pattern` and thresholds for statements, branches, functions, and
  lines; the summary artifact is `coverage/coverage-summary.json`.
- **Mutation target** in `mutation/targets.json` selects a Cosmic Ray config,
  witness test command, and survivor budget. `scripts/run_mutation_suite.py`
  turns targets into a plan/shard/merge data flow.
- **Playwright configuration** uses a single worker and `fullyParallel: false`;
  Chromium is the normal project and Firefox is restricted to `@smoke` tests.
  CI retries twice, recording traces on first retry and screenshots/video on
  failure.
- **Pytest configuration** constrains collection to `tests/`, has strict
  markers/configuration/xfails, excludes `perf` by default, and recognises
  `slow`, `perf`, and `sandbox_strict` markers.

## Control flow

1. A normal contributor pre-commit path applies Ruff fix/format and runs the
   local type/lint hooks selected by changed paths. CI remains the authority for
   the full matrix and clean environments.
2. `.github/workflows/ci.yml` synchronises the locked dev environment. Its
   canary runs Ruff then the manifest in `scripts/core_test_files.txt`; static
   CI runs Ruff, mypy, and `HAUTE_BUILD_FRONTEND=1 uv build`.
3. Backend coverage runs the full test corpus in two pytest-split shards. The
   gate combines the coverage files, enforces the global 90% floor, writes JSON,
   then invokes `scripts/check_critical_coverage.py` for per-file floors.
4. Compatibility, optional-dependency, platform, package, init, and mutation
   configuration smoke lanes run their named commands. The 3.14 probe is
   explicitly allowed to fail without blocking the workflow result.
5. The frontend CI job runs `npm ci` then the frontend-only preflight. Browser
   E2E additionally synchronises Python, installs Chromium/Firefox, and runs
   `npm run test:e2e`; Playwright calls `scripts/run_frontend_e2e_server.py` and
   waits for its readiness URL.
6. Mutation CI calls `scripts/run_mutation_suite.py --phase plan`, executes
   each isolated target/shard, downloads all artifacts, and calls `--phase merge`
   to enforce total survivor budgets. Scheduled performance calls
   `scripts/run_perf_suite.py`; scheduled dependency/shuffle workflows use their
   own commands and issue-alarm paths.

## Edge cases and invariants

- Coverage data uses relative paths so artifacts from separate runner checkout
  paths can be combined correctly. Shards disable the immediate fail-under
  check; only the combine gate is authoritative.
- The dependency-floor job deliberately re-resolves at `lowest-direct` and uses
  `--frozen` thereafter; it must not silently re-lock at the normal highest
  resolution. The scheduled dependency job instead tests a fresh
  latest-within-caps wheel installation.
- Mutation shards partition a shared initial work order and run mutants one at
  a time per runner. Separate CI runners provide the shard parallelism while
  serial execution avoids concurrent in-place mutation races; merge expects
  every selected mutant to contribute exactly once.
- Frontend shuffled tests are a nightly monitor for within-file state leaks,
  not an ordinary PR requirement. A captured seed makes a failed ordering
  reproducible.
- `tests/`, `frontend/src/__tests__/`, and the colocated frontend test
  directories are active corpora. `docs/roadmap/`, `repro/`, review archives,
  and generated output/reports must not be read as exhaustive or current
  behaviour merely because they remain tracked or present locally.
- The normal Ruff configuration excludes `rating/`, `modules/`, `outputs/`,
  `docs/review/`, and `docs/fable-Review/`; that is a lint-target boundary, not
  evidence that these paths are shipped runtime code.

## Error handling

- Ruff, mypy, pytest, coverage, npm, Playwright, Cosmic Ray, and maintained
  scripts use non-zero exits to fail their calling hook/job; CI does not turn
  a failed required command into a passing substitute.
- Pytest's strict settings fail unknown markers/configuration and unexpected
  xpasses. `pytest-timeout` arguments in CI stop overlong tests rather than
  leaving a job indefinitely running.
- `scripts/check_critical_coverage.py` reports missing/under-threshold entries;
  frontend `check-critical-coverage.mjs` does the corresponding validation for
  the coverage summary. Both are explicit gate commands after test execution.
- Playwright retains configured failure diagnostics, while CI uploads browser,
  mutation, coverage, and performance artifacts only from their named workflow
  steps (usually with `if: always()`).
- Monitoring workflows deliberately create/update issues on their eligible
  failures. That notification is not a fallback for fixing a failed required
  check.

## Testing

- Active backend test groups live in `tests/`: unit, property-based,
  regression, API/contract, end-to-end, security/sandbox, and repository
  hygiene tests. `tests/fixtures/` provides the corresponding stable data and
  golden contracts; `tests/performance/` supplies the separately scheduled
  performance cases.
- Active frontend unit coverage lives in `frontend/src/__tests__/`,
  `frontend/src/api/__tests__/`, `frontend/src/components/__tests__/`,
  `frontend/src/hooks/__tests__/`, `frontend/src/panels/__tests__/`, and the
  other exact frontend test-group directories listed in the module map, run by
  `npm run test` or `npm run test:coverage`. Browser coverage lives in `frontend/e2e/`, run by
  `npm run test:e2e`, `npm run test:e2e:smoke`, or the benchmark command.
- `tests/test_check_critical_coverage.py`, `tests/test_mutation_suite_runner.py`,
  `tests/test_run_perf_suite.py`, `tests/test_perf_suite_script.py`,
  `tests/test_memory_smoke_script.py`, `tests/test_frontend_bundle_budget_ci.py`,
  and `tests/test_docs_accuracy.py` cover important assurance tooling and
  repository-policy contracts.
- `mutation/` is tested as configuration/orchestration through its active
  script/tests and CI workflow. `docs/roadmap/`, `repro/`, review archives, and
  generated artifacts are intentionally not claimed as a current test suite.
