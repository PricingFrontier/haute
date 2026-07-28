# Engineering Quality — Low-Level Specification

## Module map

| File | Responsibility |
|---|---|
| `pyproject.toml` | Cross-component dependency owned by [build-and-distribution](../build-and-distribution/low-level.md); configures Ruff, pytest, coverage, critical backend coverage thresholds, mypy, pinned development tools, and excludes non-product directories from the normal Ruff target. |
| `AGENTS.md` | Records repository-local engineering and review instructions for contributors and coding agents; it is guidance, not executable build or test configuration. |
| `CLAUDE.md` | Directs Claude-compatible coding agents to the repository's authoritative `AGENTS.md` engineering instructions without duplicating policy. |
| `.gitignore` | Excludes generated builds, virtual environments, caches, local pipeline output/data, tool state, and other non-source artifacts from normal version-control discovery. |
| `specs/ownership.toml` | Machine-checked ledger for files shared by multiple Module maps or explicit cross-component prose ownership claims; records the single primary owner and all consumer components. |
| `.pre-commit-config.yaml` | Runs Ruff fix/format plus local mypy and frontend typecheck/lint hooks on relevant source changes. |
| `.github/workflows/ci.yml` | Defines PR/main CI jobs: canary, install/package smoke, dependency floors, static/type checks, coverage shards/gate, compatibility/probe, performance, optional dependencies, platform, mutation-config, frontend, and browser E2E lanes. Branch protection, outside this repository workflow file, determines which checks are required for merge. |
| `.github/workflows/dependencies.yml` | Runs the scheduled/manual fresh unlocked-resolve monitor plus the locked Python/frontend advisory gate on schedule, manual dispatch, main lock-policy changes, and relevant PRs; retains failed reports and raises/updates dependency-watch issues on eligible monitoring failures. |
| `.github/workflows/frontend-shuffle.yml` | Runs the scheduled/manual shuffled Vitest monitor and raises/updates shuffle-watch issues with its seed on eligible failures. |
| `.github/workflows/mutation.yml` | Plans changed mutation targets, runs separate CI-job shards whose mutants execute serially per runner, and uses a failure-aware non-cancelled status condition on the single merge gate so plan/shard failures become failed rather than skipped checks. |
| `.github/workflows/performance.yml` | Runs scheduled/manual Python and browser-performance lanes and uploads their artifacts. |
| `frontend/package.json` | Cross-component dependency owned by [build-and-distribution](../build-and-distribution/low-level.md); defines frontend lint/type/unit/coverage/bundle/E2E/benchmark command entry points and frontend critical-coverage entries. |
| `frontend/eslint.config.js` | Defines blocking browser TypeScript/React ESLint rules, fourteen explicit pre-existing file/rule exceptions, generated-report ignores, and underscore-prefixed intentionally-unused names. |
| `frontend/vitest.config.ts` | Configures the Vitest unit-test environment, setup, source/test selection, coverage reporting, and blocking 80/75/80/80 global thresholds. |
| `frontend/playwright.config.ts` | Configures serial browser E2E projects, retries, artifacts, and readiness-managed local E2E server. |
| `frontend/e2e/core-flows.spec.ts` | Playwright coverage for core browser flows. |
| `frontend/e2e/canvas-assurance.spec.ts` | Deterministic Chromium coverage and visual baselines for mixed Banding-to-Rating persistence and optimiser result/apply/MLflow-boundary journeys. |
| `frontend/e2e/data-io-nodes.spec.ts` | Playwright coverage for data-I/O node browser flows. |
| `frontend/e2e/edge-join.spec.ts` | Deterministic full-browser Edge Join workflow: compatible-edge feedback and insertion, configuration/preview, save/reload topology, repeated joins, named API-input source-handle preservation, and downstream trace highlighting. |
| `frontend/e2e/data-preview-scroll.benchmark.spec.ts` | `@benchmark` Playwright coverage for data-preview scrolling. |
| `frontend/e2e/git-graph.spec.ts` | Playwright coverage for the Git graph. |
| `frontend/e2e/git-sidebar-regression.spec.ts` | Playwright regression coverage for the Git sidebar. |
| `frontend/e2e/job-progress-render.benchmark.spec.ts` | `@benchmark` Playwright coverage for job-progress rendering. |
| `frontend/e2e/large-graph-drag.benchmark.spec.ts` | `@benchmark` Playwright coverage for dragging large graphs. |
| `frontend/e2e/trace-render.benchmark.spec.ts` | `@benchmark` Playwright coverage for linear and multi-frame trace rendering latency. |
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
| `scripts/check_dependency_audit.py` | Stdlib-only fail-closed advisory-policy orchestrator/parser: exports the exact locked Python graph, runs pinned `pip-audit` and full-tree `npm audit`, validates report schemas, gives npm meta-findings a topology-independent transitive identity, and subtracts only current exact accepted-risk entries. |
| `security/accepted-risks.toml` | Versioned exact advisory acceptance registry. Entries require ecosystem/package/advisory identity, owner, exposure, compensating control, approval date, and non-expired review date; stale, duplicate, malformed, mismatched, or unused entries fail the audit. |
| `scripts/core_test_files.txt` | Curated core test-subset manifest and its selection/refresh rationale for canary/dependency lanes. |
| `scripts/e2e_git_topologies.py` | Exercises Git topology scenarios used by repository-level verification. |
| `scripts/extract_polars_io.py` | Extracts/checks Polars I/O information used by related verification and maintenance workflows. |
| `scripts/init_smoke.py` | Builds/installs or consumes a wheel in a fresh environment, initialises a project, serves it headlessly, exercises an authenticated endpoint, and shuts it down. |
| `scripts/memory_smoke.py` | Runs the maintained memory-safety smoke path. |
| `scripts/package_smoke_check.py` | Validates an installed distribution's package/runtime expectations. |
| `scripts/preflight.ps1` | Windows preflight entry point for selected backend/frontend/init-smoke checks. |
| `scripts/preflight.sh` | POSIX preflight entry point for selected backend/frontend/init-smoke checks. |
| `scripts/regen_sanitize_parity_fixture.py` | Regenerates the committed sanitisation-parity fixture when deliberately requested. |
| `scripts/run_frontend_e2e_server.py` | Generates the isolated browser fixture, then starts and readiness-signals its dedicated-port backend and Vite proxy for Playwright. |
| `scripts/run_mutation_suite.py` | Implements mutation target selection, work planning, shard execution, merge, and survival-threshold reporting. |
| `scripts/run_perf_suite.py` | Runs bounded Python performance tests and writes schema-3 workload, environment, resource, wall-time, and per-test evidence artifacts. |
| `scripts/spec_corpus_inventory.py` | Builds the exact working-tree specification inventory and content fingerprint, validates complete per-file review coverage, and derives component/governance/roadmap line and coverage totals for reproducible semantic-review claims. |
| `scripts/setup-worktree.sh` | Sets up a development worktree. |
| `mutation/README.md` | Documents the maintained mutation-testing workflow and constraints. |
| `mutation/targets.json` | Declares selected mutation targets, witness suites, survival budgets, and rationales. |
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
| `tests/performance/` | `perf`-marked benchmark-style tests excluded from ordinary pytest and run by the performance harness, including the rating miss-guard evidence matrix (`test_rating_miss_guard_perf.py`). |
| `tests/test-health-summary.md` | Deterministic generated inventory of backend/frontend skip/xfail/flaky debt, browser retry budget, and mutation-survivor thresholds. Ordinary tests reject drift from the live scanners. |
| `tests/docs_accuracy_baseline.txt` | Sorted one-line TSV ratchet of current per-document accuracy violations; resolved entries are deleted and additions require explicit review. |
| `frontend/src/__tests__/` | Frontend application-level unit, contract, regression, adversarial, and bundle/coverage gate tests. |
| `frontend/src/api/__tests__/` | Frontend API-client test group. |
| `frontend/src/components/__tests__/` and `frontend/src/components/form/__tests__/` | Frontend reusable-component and form-control test groups. |
| `frontend/src/hooks/__tests__/` | Frontend hook, polling, graph-state, API, and synchronisation test group. |
| `frontend/src/nodes/__tests__/` | Frontend graph-node component test group. |
| `frontend/src/panels/__tests__/` and `frontend/src/panels/editors/__tests__/` | Frontend panel and editor test groups. |
| `frontend/src/panels/editors/banding/__tests__/` and `frontend/src/panels/editors/rating/__tests__/` | Frontend banding/rating-editor test groups. |
| `frontend/src/panels/explore/__tests__/`, `frontend/src/panels/gitgraph/__tests__/`, `frontend/src/panels/modelling/__tests__/`, `frontend/src/panels/optimiser/__tests__/`, and `frontend/src/panels/trace/__tests__/` | Specialised explore, Git graph, modelling, optimiser, and trace panel test groups. |
| `frontend/src/stores/__tests__/`, `frontend/src/test-utils/__tests__/`, `frontend/src/trace/__tests__/`, `frontend/src/types/__tests__/`, and `frontend/src/utils/__tests__/` | Frontend store, test-helper, trace, type, and utility test groups. |
| `specs/roadmap/README.md` | Entry point for the internal component improvement catalogue and its working/retirement protocol. |
| `specs/roadmap/<component>.md` | One self-contained, non-normative improvement queue per component. Each package defines its problem, plan, acceptance criteria, dependencies, and current code/test evidence. |
| `repro/` | Point-in-time benchmark/reproduction programs and metadata; not an automatically current product-behaviour contract. |
| `mlflow.db` | Checked-in SQLite MLflow tracking-store snapshot (experiments, runs, metrics, parameters, tags, and model-version metadata). It is repository data/local state, not an installed-package input or a runtime prerequisite; MLflow may instead use the configured tracking store. |

## Key types and data structures

- **Backend critical-coverage entry** in `pyproject.toml` has a source `path`,
  minimum statement/branch percentages, and rationale. The coverage JSON path
  is `.cache/coverage/backend.json`.
- **Frontend critical-coverage entry** in `frontend/package.json` has a source
  glob-like `pattern` and thresholds for statements, branches, functions, and
  lines; the summary artifact is `coverage/coverage-summary.json`. This sits
  after Vitest's global floors of 80% statements, 75% branches, 80% functions,
  and 80% lines.
- **Documentation violation** is the ordered
  `document<TAB>rule<TAB>detail` record emitted by
  `tests/test_docs_accuracy.py`. The committed
  `tests/docs_accuracy_baseline.txt` contains every accepted current record
  exactly once in sorted order.
- **Mutation target** in `mutation/targets.json` selects a Cosmic Ray config,
  witness test command, survivor budget, and rationale.
  `scripts/run_mutation_suite.py` rejects malformed target metadata and carries
  the rationale into plan and aggregate result artifacts.
- **Test-health summary** — the backend AST scanner includes
  `pytest.mark.flaky` in its exact fingerprint budget (zero at present). The
  generated Markdown groups live site counts by signal and lists each mutation
  target separately, so a reviewer can act without reading the scanner's
  implementation. The Playwright CI retry allowance is pinned to exactly 2 by a
  direct assertion against `frontend/playwright.config.ts`.
- **Performance report schema 3** contains top-level `environment`, `workload`,
  `resources`, and `wall_time` records plus per-test bounded evidence.
  Unavailable numeric counters are JSON `null`; reported pytest phase time plus
  runner overhead must equal total time within the recorded tolerance. The
  deterministic CI-small Polars scenario records every `ExecutionProfile`,
  while 1m/10m inputs remain opt-in.
- **Playwright configuration** uses a single worker and `fullyParallel: false`;
  Chromium is the normal project and Firefox is restricted to `@smoke` tests.
  CI retries twice, recording traces on first retry and screenshots/video on
  failure.
- **Edge Join E2E fixture** is a project-isolated, generated pipeline with
  deterministic small frames and one API-input frame whose raw label is the
  persisted source handle. The workflow targets nodes, handles, and rendered
  edge ids through stable locators and derives drag coordinates from live
  handle bounds and rendered SVG path geometry; it does not use production
  data, fixed sleeps, or hard-coded canvas coordinates.
- **Pytest configuration** constrains collection to `tests/`, has strict
  markers/configuration/xfails, excludes `perf` by default, and recognises
  `slow`, `perf`, and `sandbox_strict` markers.
- **Component improvement package** is a `### <package-id>` section in one
  flat `specs/roadmap/<component>.md` file. It has a stable ID, priority/order,
  problem, plan, acceptance criteria, dependencies, and current evidence. The
  component file owns whether the package is queued, blocked, or retired.

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
   waits for its readiness URL. The harness preserves the same-origin Vite
   proxy and real HttpOnly-cookie bootstrap used by the product; it never
   exports a browser-readable session-token variable or adds a browser bearer
   header. Its generated fixture explicitly owns the deterministic
   `raw_rows` → `enriched` → `priced` core graph and sidecars after invoking
   the blank `haute init` scaffold; browser coverage must not depend on product
   scaffolding to supply test nodes. Its out-of-browser readiness probe uses
   the supported non-browser token header. `HAUTE_E2E_BACKEND_PORT`,
   `HAUTE_E2E_FRONTEND_PORT`, and
   `HAUTE_E2E_READINESS_PORT` may select alternate loopback ports when a
   developer already has Haute running; the harness and Playwright config
   validate and share those values.
   `frontend/scripts/check-bundle-size.mjs` counts the production entry and
   modulepreload chunks against a default 247 KiB initial-JavaScript gzip
   ceiling. That is the smallest whole-KiB ceiling above the measured merged
   246.2 KiB bundle, retaining less than 1 KiB of headroom while still catching
   accidental eager imports; CI may override it only through the documented
   `HAUTE_BUNDLE_MAX_INITIAL_GZIP_KIB` environment variable.
   User-triggered surfaces such as the Ctrl+K `NodeSearch` palette remain
   dynamically imported so their implementation is excluded from that initial
   chunk. Canvas-assurance screenshots retain the shared 2% pixel-difference
   ceiling. The narrow mixed-Banding and rebuilt-Rating captures and both
   selected-optimiser captures select reviewed Linux-specific baselines in
   Linux CI; the two desktop Banding/Rating captures deliberately keep the
   default developer baseline. Platform-specific baselines are added only when
   repeated captures prove stable system-font or native-control differences.
6. Mutation CI calls `scripts/run_mutation_suite.py --phase plan`, executes
   each isolated target/shard, downloads all artifacts, and calls `--phase merge`
   to enforce total survivor budgets. The merge job's `!cancelled()` status
   condition ensures dependency failures do not skip it, and it fails explicitly
   when planning or a required shard was unsuccessful. Plan and merge artifacts
   retain each target's rationale beside the threshold and observed
   survival rate.
   Scheduled performance calls
   `scripts/run_perf_suite.py`; scheduled dependency/shuffle workflows use their
   own commands and issue-alarm paths.
7. The locked advisory job exports all locked Python groups/extras without the
   project itself, audits them with pinned `pip-audit`, audits the full frontend
   tree (including dev/build dependencies) with `npm audit --ignore-scripts`,
   parses both JSON reports, and evaluates `security/accepted-risks.toml`.
   Every Python finding and every npm high/critical finding blocks unless its
   exact current identity is accepted. Concrete npm advisories use GHSA/source
   identity; meta-findings use the affected package plus `npm:transitive`, not
   the dependency path recorded by the current lockfile. Scanner/export/report-
   schema errors fail closed; failed reports are uploaded for triage.

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
- A component roadmap contains active/deferred/decision/reverify work only.
  Delivered packages are removed once present-tense specs and ordinary tests
  own their outcome; the index's `Start with` cell names an existing
  non-deferred package, or `—` when no such package exists.
- Mutation thresholds measure observable behaviour. Syntax-only mutants in
  postponed annotations and keyword-only call markers are explicitly excluded
  with `# pragma: no mutate`; executable expressions and branch decisions must
  remain in scope and be killed by focused witnesses rather than hidden behind
  a pragma or a relaxed survivor budget.
- A retained skip, xfail, expected failure, focused test, flaky marker, or
  browser retry is debt even when it is justified. Exact-site fingerprints
  prevent silent growth: a new site fails the ratchet until it is explicitly
  reviewed and budgeted, and a removed site fails until its stale entry is
  deleted (ruled 2026-07-27: no calendar expiry — review is event-driven,
  triggered by the ratchet, not by dates).
- Frontend shuffled tests are a nightly monitor for within-file state leaks,
  not an ordinary PR requirement. A captured seed makes a failed ordering
  reproducible.
- `tests/`, `frontend/src/__tests__/`, and the colocated frontend test
  directories are active corpora. `specs/roadmap/`, `repro/`, and generated
  output/reports must not be read as exhaustive or current behaviour merely
  because they remain tracked or present locally.
- Every component roadmap has `Scope`, `Priorities`, and `Planned improvements`
  sections. Every package supplies `Why`, `Plan`, `Acceptance`, `Dependencies`,
  and `Evidence`; a package appears in exactly one owning component.
- The normal Ruff configuration excludes `rating/`, `modules/`, `outputs/`, and
  generated pytest basetemp families (`.codex-pytest-*`, `.ops-pytest-temp`,
  `.providers-pytest-temp`, `.pytest-tmp*`). The former are lint-target
  boundaries; the latter may contain intentionally unreadable test fixtures and
  are never source inputs.

## Error handling

- Ruff, mypy, pytest, coverage, npm, Playwright, Cosmic Ray, and maintained
  scripts use non-zero exits to fail their calling hook/job; CI does not turn
  a failed required command into a passing substitute.
- The mutation gate is scheduled after failed dependencies and emits a direct
  failure before checkout/setup when planning failed or selected shards did
  not complete, preserving one authoritative pass/fail check.
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
- `check_dependency_audit.py` returns 1 for unaccepted findings and 2 for
  policy/scanner/report failures. An acceptance is not a wildcard: an expired,
  duplicate, wrong-package, or no-longer-observed entry makes the policy fail
  so the registry cannot accumulate silent debt.

## Testing

- `tests/test_bug_regressions.py` — named deep-review bug regressions (streaming restoration, source/pipeline path handling, preview source files, and Polars/parser safety).
- `tests/test_bugfixes.py` — regression contracts for streaming chunk restoration, source/pipeline path resolution, preview source files, and parser/config safety.
- `tests/test_coverage_gaps.py` — targeted edge coverage for config/fingerprint/builders/artifacts/model helpers, schemas, and node discovery.
- `tests/test_decoupling_contracts.py` — separation contracts for tracing, EventBus/file-watcher integration, and logging conventions.
- `tests/test_dry_fixes.py` — DRY response/model inheritance and optimiser finalize contracts across online/ratebook/frontier paths.
- `tests/test_dry_refactors.py` — shared exception hierarchy, dispatch-table parity, transport resolution, dead-code removal, typed-node lookup, and shared code compilation.
- `tests/test_frontend_e2e_server.py` — browser-harness contracts for augmenting the blank scaffold with a complete, executable fixture graph.
- `tests/test_performance_docs.py` — documentation contracts for Python/Polars/frontend/memory performance workflows and links.
- `tests/test_property.py` — Hypothesis properties for sanitisation, topology, path resolution, banding/rating, codegen/parser round-trips, fingerprints, config, validation, and cache invariants.
- `tests/test_repository_hygiene.py` — repository artifact/path, dependency-import, subprocess, encoding, sanitizer, and persistence-path hygiene.
- `tests/test_small_module_contracts.py` — JSON-safe serialization, shared contracts, and package-init module contracts.
- `tests/test_test_debt.py` — AST debt scanner budgets and explicit-reason
  contracts for backend/frontend skip/xfail/fixme markers, plus the zero
  backend-flaky budget, the Playwright retry budget, and exact regeneration of
  `tests/test-health-summary.md`.

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
- `frontend/e2e/edge-join.spec.ts` asserts user-observable outcomes across the
  real canvas and backend: pre-release candidate feedback, insertion and role
  handles, same-name-key configuration, joined preview columns/rows, persisted
  split topology/config after reload, two joins on one branch after a second
  reload, exact named API-input `sourceHandle`, and an Edge Join retained and
  highlighted in a downstream trace. Private React state is not an oracle.
- `tests/test_check_critical_coverage.py`, `tests/test_mutation_suite_runner.py`,
  `tests/test_mutation_sharding.py`, `tests/test_run_perf_suite.py`,
  `tests/test_perf_suite_script.py`,
  `tests/test_memory_smoke_script.py`, `tests/test_frontend_bundle_budget_ci.py`,
  `tests/test_infrastructure_contracts.py`, `tests/test_docs_accuracy.py`, and
  `tests/test_spec_corpus_inventory.py`
  cover important assurance tooling and repository-policy contracts. The
  documentation-accuracy checks validate complete-document repository paths
  from a fail-loud versionable working-tree inventory (tracked plus untracked,
  with ignored files excluded) whose exact, suffix, and parent-path indexes are
  built once per ratchet evaluation; they also validate
  `path::symbol` and Module-map responsibility symbols, exact headings, Testing
  references and backend-test indexing, link anchors, roadmap evidence,
  present-tense ownership claims, positive-evidence temporary-contract
  retirement, and the one-line ratchet.
- `tests/test_dependency_audit.py` covers clean/blocking reports, npm
  high/critical and transitive identities, valid/expired/malformed/duplicate/
  unused acceptances, the invariant that an accepted parent meta-finding cannot
  waive a child's concrete GHSA, malformed fail-closed reports, and live-command
  return-code orchestration without contacting advisory services.
- `mutation/` is tested as configuration/orchestration through its active
  script/tests and CI workflow. `specs/roadmap/`, `repro/`, and generated
  artifacts are intentionally not claimed as a current test suite.
